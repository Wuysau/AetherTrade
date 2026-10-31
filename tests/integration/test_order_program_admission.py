"""Mandatory isolated PostgreSQL OP-C probes; synthetic PAPER facts, no fills.

This suite uses fixture qualification evidence only. It cannot qualify any
deployed matcher/adapter or enable Testnet/LIVE. The runner must provision a
fresh IP07 database, as for the existing reservation suites.
"""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from integration.support_ip_07 import ReservationCase, ip07_database, NOW
from intents.test_order_program_ingress import admission_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.wire import ContractCodec
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.intents.ingress import IngressInputs
from trading_system.intents.order_program_compiler import compile_atomic_target
from trading_system.intents.order_program_ingress import (
    AdmissionInputs, OrderProgramIngress, PaperQualification,
    OrderProgramCommandBinding, COMMAND_PREFIX, validate_program_command,
    program_intake,
)
from trading_system.execution.commands import CommandRepository
from trading_system.risk.reservations import ReservationService
from trading_system.storage.models import scope_id, decode, encode
from unit.support_ip_06 import END, BEFORE, money


@pytest.fixture(scope='module')
def program_database(ip07_database):
    import psycopg
    from integration.support_ip_03 import ROOT
    with psycopg.connect(ip07_database) as conn:
        conn.execute((ROOT / 'migrations/0008_order_program_records.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'migrations/0010_order_program_intake.sql').read_text(encoding='utf-8'))
    return ip07_database


def program_case(dsn):
    c = ReservationCase(dsn, available='10000')
    template, p, _, f, _ = admission_case()
    scope = c.scope
    spec = replace(template.binding.instrument, instrument_uid=scope.instrument_uid)
    c.case.quote('81490', '81490')
    c.case.inputs = replace(c.case.inputs, instrument=spec, allow_round_down=True)
    c.group_capacity = c.strategy_capacity = (money('10000'), money('10', 'ASSET'))
    c.buffer = (money('0'), money('0', 'ASSET'))
    context = c.target_context(c.case.intent)
    grant = replace(template.binding.authorization.authorization, scopes=(scope,),
        authorization_id=context.authorization_id, deployment_id=context.deployment_id,
        owner_strategy_id=c.owner, valid_from=BEFORE, expires_at=END)
    auth = replace(template.binding.authorization, authorization=grant, identity_id=context.identity_id,
        strategy_revision=context.strategy_revision, ownership_epoch=context.ownership_epoch,
        evidence=c.case.book.evidence)
    binding = replace(template.binding, authorization=auth, instrument_ref=scope.instrument_uid, instrument=spec,
        collateral_group_id=c.group, snapshot=c.case.market, clock=c.case.inputs.clock,
        maximum_valid_until=END, max_planned_loss=money('100'),
        evidence=(c.case.market.ref, *c.case.book.evidence))
    target = replace(template.target, instrument=spec, expected_instrument=spec, context=context,
        clock=c.case.inputs.clock, session=c.case.inputs.session, deadline_monotonic_ns=10**20)
    binding = replace(binding, deadline_monotonic_ns=10**20)
    rules = replace(f.rules, scope=scope, as_of=BEFORE, valid_until=END, evidence=c.case.book.evidence)
    caps = replace(f.capabilities, scope=scope, as_of=BEFORE, valid_until=END, evidence=c.case.book.evidence)
    p = replace(p, proposal_id='proposal-' + uuid4().hex, instrument_ref=scope.instrument_uid,
        valid_until=END, evidence_refs=(c.case.market.ref.id,), exit_plan=replace(p.exit_plan, review_due_at=END),
        program=replace(p.program, program_id='program-' + uuid4().hex,
            instrument_ref=scope.instrument_uid, submit_valid_until=END))
    f = replace(f, ingress=IngressInputs(binding=binding, target=target), rules=rules, capabilities=caps,
        expected_capabilities_hash=canonical_hash(domain_payload(caps)))
    compiled = compile_atomic_target(p, rules, caps, scope=scope, now=NOW,
        expected_build_digest=f.expected_build_digest, expected_rule_revision=1,
        expected_capabilities_hash=f.expected_capabilities_hash, expected_instrument_ref=scope.instrument_uid,
        allow_round_down=True)
    proof = PaperQualification(scope=scope, capabilities_hash=compiled.capabilities_hash,
        rules_hash=compiled.rules_hash, adapter_build_digest=f.expected_build_digest,
        evidence=caps.evidence, valid_until=END)

    def reserve_inputs(tx, intent, book):
        values = c.inputs(tx, intent, book)
        original = program_intake(tx, intent)
        assert original is not None
        payload = replace(c.command.canonical_payload, side='BUY', order_type='LIMIT', price='81490.00',
            quantity=original.compilation.normalized_target_quantity, time_in_force=original.compilation.time_in_force)
        command = replace(c.command, intent_id=intent.intent_id, canonical_payload=payload,
            command_id='command-' + intent.intent_id, logical_client_order_id='logical-' + intent.intent_id,
            venue_client_order_id='client-' + intent.intent_id)
        command = replace(command, payload_hash=canonical_hash(ContractCodec().encode(command)['canonical_payload']))
        return replace(values, command=command,
            risk=replace(values.risk, intent_id=intent.intent_id))

    service = ReservationService(c.ledger.uow, scope, reserve_inputs, clock=lambda: NOW)
    from trading_system.storage.uow import UnitOfWork
    def connect_intake():
        conn=c.ledger.connect_admin()
        conn.execute('SET ROLE ledger_intake')
        conn.commit()
        return conn
    ingress = OrderProgramIngress(c.ledger.uow, scope, lambda *args: f, service,
        intake_uow=UnitOfWork(connect_intake),execution_policy=f.execution_policy,
        load_qualification=lambda *args: proof)
    return c, p, compiled, context, ingress, f, proof


def test_program_admission_persists_exact_fields_and_replays_original(program_database):
    c, p, compiled, context, ingress, facts, proof = program_case(program_database)
    result = ingress.admit(p, context, compiled)
    assert result.reservation is not None and result.reservation.accepted, result
    assert c.count('commands') == c.count('intents') == 1
    with c.ledger.uow.transaction(c.scope) as tx:
        from trading_system.risk.reservation_repository import ReservationRepository
        command = ReservationRepository(tx).first_commands(result.receipt.intent_id)[0]
        intent = tx._latest('intents', result.receipt.intent_id, type(c.case.intent))
        sidecar = tx._latest('audit_events', COMMAND_PREFIX + command.command_id, OrderProgramCommandBinding)
        assert sidecar.compilation.original_proposal == p
        assert sidecar.compilation.original_program.nodes[0].order.tif.kind == 'GTC'
        assert command.canonical_payload.price == '81490.00'
        assert command.canonical_payload.time_in_force == 'GTC'
        assert intent.exit_plan.stop_price == '81330.00'
        assert decode(OrderProgramCommandBinding, encode(sidecar)) == sidecar
        validate_program_command(tx, command, intent, lambda *args: facts, lambda *args: proof)
    assert ingress.reservations.reserve(intent, 1).rejection_codes == ('PROGRAM_ADMISSION_REQUIRED',)
    assert ingress.reservations.reserve_proposal(intent.intent_id).rejection_codes == ('PROGRAM_ADMISSION_REQUIRED',)
    replay = ingress.admit(p, context, compiled)
    assert replay.reservation == result.reservation
    assert c.count('commands') == c.count('intents') == 1


@pytest.mark.parametrize('uncertain', [False, True])
def test_distinct_target_cannot_duplicate_working_or_unknown(program_database, uncertain):
    c, p, compiled, context, ingress, _, _ = program_case(program_database)
    first = ingress.admit(p, context, compiled)
    assert first.reservation.accepted
    if uncertain:
        with c.ledger.uow.transaction(c.scope) as tx:
            from trading_system.risk.reservation_repository import ReservationRepository
            command = ReservationRepository(tx).first_commands(first.receipt.intent_id)[0]
            tx.commands.mark_may_have_sent(tx, command.command_id, 'DURABLE_READY')
    p2 = replace(p, proposal_id=p.proposal_id + '-second')
    compiled2 = replace(compiled, original_proposal=p2,
        proposal_hash=VersionedOrderCodec().hash('order-proposal', p2))
    result = ingress.admit(p2, context, compiled2)
    assert not result.reservation.accepted
    # MAY_HAVE_SENT appends a new version of the original command, not a new
    # external write identity. Count both versions and distinct IDs explicitly.
    assert c.count('commands') == (2 if uncertain else 1)
    assert c.count('intents') == 1
    with c.ledger.connect_admin() as conn:
        rows=conn.execute('SELECT DISTINCT record_id FROM ledger.commands WHERE scope_id=%s',
                          (scope_id(c.scope),)).fetchall()
        assert len(rows)==1
        if uncertain:
            assert rows[0][0]==command.command_id


def test_budget_denial_never_persists_command_or_funding(program_database):
    c, p, compiled, context, ingress, _, _ = program_case(program_database)
    c.strategy_capacity = (money('1'), money('10', 'ASSET'))
    result = ingress.admit(p, context, compiled)
    assert result.reservation is not None and not result.reservation.accepted
    assert c.count('commands') == c.count('intents') == c.count('reservations') == 0


def test_tif_command_mismatch_preserves_intake_but_rolls_back_financial_bundle(program_database):
    c, p, compiled, context, ingress, _, _ = program_case(program_database)
    original = ingress.reservations.load_inputs
    def wrong(*args):
        values = original(*args)
        command = replace(values.command, canonical_payload=replace(values.command.canonical_payload, time_in_force='FOK'))
        return replace(values, command=replace(command,
            payload_hash=canonical_hash(ContractCodec().encode(command)['canonical_payload'])))
    ingress.reservations.load_inputs = wrong
    result = ingress.admit(p, context, compiled)
    assert result.receipt.reason == 'PROGRAM_TIF_MISMATCH'
    assert c.count('commands') == c.count('intents') == c.count('reservations') == 0
    with c.ledger.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.proposals WHERE scope_id=%s', (scope_id(c.scope),)).fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM ledger.order_program_intakes WHERE scope_id=%s', (scope_id(c.scope),)).fetchone()[0] == 1


def test_execution_owner_cannot_author_source_and_intake_cannot_author_command(program_database):
    import psycopg
    c,*_=program_case(program_database)
    for role,table in [('ledger_owner','proposals'),('ledger_owner','order_program_intakes'),
                       ('ledger_intake','commands'),('ledger_intake','intents'),('ledger_intake','reservations')]:
        with c.ledger.connect_admin() as conn:
            conn.execute('SET ROLE '+role)
            assert conn.execute('SELECT current_user').fetchone()[0]==role
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute('INSERT INTO ledger.'+table+' SELECT * FROM ledger.'+table+' WHERE false')
            conn.rollback()


def test_owner_phase_revalidates_rules_after_intake_commit(program_database):
    c,p,compiled,context,ingress,facts,_=program_case(program_database)
    calls=[]
    def current(tx,*args):
        role=tx.execute('SELECT current_user').fetchone()[0]
        calls.append(role)
        return replace(facts,expected_rule_revision=2) if role=='ledger_owner' else facts
    ingress.load_inputs=current
    result=ingress.admit(p,context,compiled)
    assert result.receipt.status=='REJECTED' and result.reservation is None
    assert calls==['ledger_intake','ledger_owner']
    assert c.count('commands')==c.count('intents')==c.count('reservations')==0
    with c.ledger.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.order_program_intakes WHERE scope_id=%s',
                            (scope_id(c.scope),)).fetchone()[0]==1


def test_owner_commit_failure_returns_no_receipt_and_retains_only_durable_intake(program_database,monkeypatch):
    from trading_system.storage.uow import LedgerTx
    c,p,compiled,context,ingress,_,_=program_case(program_database)
    original=LedgerTx._commit
    def fail_owner(tx):
        if tx.execute('SELECT current_user').fetchone()[0]=='ledger_owner':
            raise OSError('injected owner commit failure')
        return original(tx)
    monkeypatch.setattr(LedgerTx,'_commit',fail_owner)
    with pytest.raises(OSError,match='injected owner commit failure'):
        ingress.admit(p,context,compiled)
    assert c.count('commands')==c.count('intents')==c.count('reservations')==0
    with c.ledger.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.order_program_intakes WHERE scope_id=%s',
                            (scope_id(c.scope),)).fetchone()[0]==1


def test_owner_authored_sidecar_cannot_change_bounded_intake_intent(program_database):
    from trading_system.intents.semantic_validation import ProposalRejected
    from trading_system.risk.reservation_repository import ReservationRepository
    c,p,compiled,context,ingress,facts,proof=program_case(program_database)
    result=ingress.admit(p,context,compiled)
    assert result.reservation.accepted
    with c.ledger.uow.transaction(c.scope) as tx:
        command=ReservationRepository(tx).first_commands(result.receipt.intent_id)[0]
        intent=tx._latest('intents',result.receipt.intent_id,type(c.case.intent))
        changed=replace(intent,target_quantity='4.900')
        sidecar=tx._latest('audit_events',COMMAND_PREFIX+command.command_id,OrderProgramCommandBinding)
        tx._append('audit_events',COMMAND_PREFIX+command.command_id,
                   replace(sidecar,intent_hash=ContractCodec().hash(changed)),1)
        with pytest.raises(ProposalRejected,match='PROGRAM_SOURCE_BINDING_MISMATCH'):
            validate_program_command(tx,command,changed,lambda *args:facts,lambda *args:proof)
    assert c.count('commands')==1


def test_source_only_intake_cannot_select_legacy_dispatch(program_database,monkeypatch):
    from trading_system.storage.uow import LedgerTx
    from trading_system.intents.order_program_ingress import accepted_program_source
    from trading_system.intents.ingress import accepted_intent
    from trading_system.execution.dispatcher import CommandDispatcher,DispatchDenied
    c,p,compiled,context,ingress,_,_=program_case(program_database)
    original=LedgerTx._commit
    def fail_owner(tx):
        if tx.execute('SELECT current_user').fetchone()[0]=='ledger_owner':
            raise OSError('owner phase stopped')
        return original(tx)
    with monkeypatch.context() as patch:
        patch.setattr(LedgerTx,'_commit',fail_owner)
        with pytest.raises(OSError,match='owner phase stopped'):
            ingress.admit(p,context,compiled)
    with c.ledger.uow.transaction(c.scope) as tx:
        rows=tx.execute('SELECT record_id FROM ledger.order_program_intakes WHERE scope_id=%s',
                        (scope_id(c.scope),)).fetchall()
        assert len(rows)==1
        intent=accepted_intent(accepted_program_source(tx,rows[0][0]))
        assert tx._latest('audit_events','order-program-admission:'+intent.intent_id,object) is None
        monkeypatch.setattr(CommandRepository,'validate_binding',lambda *args:intent)
        def legacy(*args):
            raise AssertionError('legacy facts loader reached for source-backed program')
        dispatcher=CommandDispatcher(None,c.scope,load_facts=legacy)
        with pytest.raises(DispatchDenied,match='PROGRAM_COMMAND_BINDING_REQUIRED'):
            dispatcher._check(tx,c.command,None)
    assert c.count('commands')==c.count('intents')==c.count('reservations')==0
