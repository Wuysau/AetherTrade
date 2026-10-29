"""Real isolated PostgreSQL safety readers; no venue sends, fills or permits."""
from dataclasses import replace
from uuid import uuid4

import pytest

from integration.support_ip_03 import isolated_postgres, LedgerScenario, ROOT
from unit.test_application_write_reader import write_case
from unit.support_ip_09 import make_case, NOW
from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.execution.commands import CommandRepository, CommandOutcome
from trading_system.intents.semantic_validation import ProposalRejected
from trading_system.risk.target_guard import target_obligations
from trading_system.storage.application_writes import application_write_obligations
from trading_system.storage.models import MissingEvidence, encode, scope_id
from trading_system.storage.order_programs import OrderProgramRepository
from trading_system.storage.uow import LedgerTx


@pytest.fixture(scope='module')
def program_database(isolated_postgres):
    import psycopg
    with psycopg.connect(isolated_postgres) as c:
        c.execute((ROOT/'migrations/0008_order_program_records.sql').read_text(encoding='utf-8'))
    return isolated_postgres


@pytest.fixture
def scenario(program_database):
    s=LedgerScenario(program_database)
    with s.uow.transaction(s.scope) as tx:s.write_bundle(tx)
    return s


def add_write(s, *, scope=None, uncertain=False, command_id=None):
    scope=scope or s.scope
    write,program=write_case(scope,command_id or 'program-'+uuid4().hex)
    write=replace(write,owner_strategy_id=s.intent.owner_strategy_id)
    with s.uow.transaction(scope) as tx:
        repo=OrderProgramRepository(tx)
        repo.save_program(program);repo.create_write(write)
        if uncertain:
            # Explicit committed-boundary fixture, as in OP-B migration tests.
            # No engine is constructed and no permit or fill is fabricated.
            tx._append('order_writes_v2',write.command_id,
                replace(write,state='MAY_HAVE_SENT',durable_record_ref='original-boundary'),1)
    return write


def test_ready_program_blocks_target_without_inferred_allocation(scenario):
    s=scenario;write=add_write(s)
    book=make_case().book
    book=replace(book,scope=s.scope,coverage=replace(book.coverage,scope=s.scope))
    with pytest.raises(ProposalRejected,match='PROGRAM_TARGET_ALLOCATION_REQUIRED'):
        with s.uow.transaction(s.scope) as tx:
            target_obligations(tx,s.intent,book,(),utc_timestamp(NOW))
    assert s.counts().command_count==s.counts().reservation_count==1
    with s.uow.transaction(s.scope) as tx:
        obligation,=application_write_obligations(tx)
        assert obligation.original==obligation.current==write


def test_legacy_exclusion_cannot_hide_same_text_program_id(scenario):
    s=scenario;write=add_write(s,uncertain=True,command_id=s.command.command_id)
    with s.uow.transaction(s.scope) as tx:
        unresolved=CommandRepository(tx).unresolved_in_group(s.command.command_id)
        assert len(unresolved)==1 and unresolved[0].endswith(':'+write.command_id)
        assert ':EXECUTION_ALGO:' in unresolved[0]
    assert s.counts().command_count==1


@pytest.mark.parametrize('same_group',[True,False])
@pytest.mark.parametrize('product',[t.Product.SPOT,t.Product.USD_M_PERP])
def test_current_collateral_binding_counts_other_account_product(scenario,same_group,product):
    s=scenario
    other=replace(s.scope,venue_account_id='other-'+uuid4().hex,product=product)
    group=s.group if same_group else 'separate-'+uuid4().hex
    with s.connect_admin() as c:
        if not same_group:c.execute('INSERT INTO ledger.collateral_mutex VALUES(%s,%s)',('PAPER',group))
        c.execute('INSERT INTO ledger.account_bindings VALUES(%s,%s,%s,%s,NULL)',
            ('PAPER',other.venue_account_id,product.value,group))
        c.execute('INSERT INTO ledger.active_owners VALUES(%s,%s,%s,%s,%s,%s,1,1)',
            ('PAPER',other.venue_account_id,product.value,other.instrument_uid,other.venue_leg,s.intent.owner_strategy_id))
    write=add_write(s,scope=other,uncertain=True)
    with s.uow.transaction(s.scope) as tx:
        unresolved=CommandRepository(tx).unresolved_in_group(s.command.command_id)
        assert bool(unresolved)==same_group
        if same_group:assert unresolved[0].endswith(':'+write.command_id)


@pytest.mark.parametrize('state',['UNKNOWN','VENUE_CONFIRMED','VENUE_REJECTED','ABORTED_UNSENT'])
def test_raw_terminal_labels_and_legacy_outcome_audit_cannot_clear_program(scenario,state):
    s=scenario;write=add_write(s,uncertain=True)
    with s.uow.transaction(s.scope) as tx:
        tx._append('audit_events','ip10:outcome:'+write.command_id,CommandOutcome(
            command_id=write.command_id,request_hash='0'*64,durable_record_ref='original-boundary',
            receipt=t.CommandReceipt(receipt_id='forged-terminal',command_id=write.command_id,
                status=t.CommandState.VENUE_CONFIRMED,reason='raw audit claim',existing_result_ref='claim'),
            provenance='VENUE_EVENT'),0)
    with s.connect_admin() as c:
        # Fault injection is uncommitted and rolled back. Only migration/test
        # admin can bypass the currently stricter write-state INSERT trigger.
        c.execute('ALTER TABLE ledger.order_writes_v2 DISABLE TRIGGER order_write_guard')
        c.execute('INSERT INTO ledger.order_writes_v2(scope_id,record_id,sequence,payload,transaction_id) '
            'VALUES(%s,%s,3,%s::jsonb,%s)',(scope_id(s.scope),write.command_id,
            encode(replace(write,state=state,durable_record_ref='original-boundary')),'injected-state'))
        c.execute('SET ROLE ledger_owner')
        tx=LedgerTx(c,s.scope,s.group)
        assert CommandRepository(tx).unresolved_in_group(s.command.command_id)
        c.rollback()


@pytest.mark.parametrize('fault',['malformed-latest','malformed-first','physical-scope','missing-binding'])
def test_corrupt_or_unbound_unknown_is_not_discarded_by_group_join(scenario,fault):
    s=scenario;write=add_write(s,uncertain=True)
    with s.connect_admin() as c:
        c.execute('ALTER TABLE ledger.order_writes_v2 DISABLE TRIGGER order_write_guard')
        if fault=='malformed-latest':
            physical,identifier,sequence,payload=scope_id(s.scope),write.command_id,3,'{"state":"UNKNOWN"}'
        elif fault=='malformed-first':
            physical,identifier,sequence,payload=scope_id(s.scope),'corrupt-first-'+uuid4().hex,1,'{"state":"UNKNOWN"}'
        else:
            candidate=replace(write,command_id='injected-'+uuid4().hex,
                scope=replace(write.scope,venue_account_id='unbound-'+uuid4().hex))
            physical='wrong-scope' if fault=='physical-scope' else scope_id(candidate.scope)
            identifier,sequence,payload=candidate.command_id,1,encode(candidate)
        c.execute('INSERT INTO ledger.order_writes_v2(scope_id,record_id,sequence,payload,transaction_id) '
            'VALUES(%s,%s,%s,%s::jsonb,%s)',(physical,identifier,sequence,payload,'injected-corruption'))
        c.execute('SET ROLE ledger_owner')
        tx=LedgerTx(c,s.scope,s.group)
        with pytest.raises(MissingEvidence,match='PROGRAM_WRITE_'):
            CommandRepository(tx).unresolved_in_group(s.command.command_id)
        c.rollback()
