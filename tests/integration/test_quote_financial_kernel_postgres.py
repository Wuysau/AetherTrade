"""Actual bounded intake and SAME kernel transaction, without send qualification."""
from dataclasses import replace
from decimal import Decimal as D
from uuid import uuid4

import pytest

from integration.test_program_financial_admission import database,isolated_postgres,real_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.intents.generic_program_intake import GenericProgramIngress,ProgramIntakeInputs,intake_key,read_program_source
from trading_system.intents.quote_program_compilation import compile_quote_program
from trading_system.intents.quote_target_allocation import compile_quote_target
from trading_system.risk.program_financial_admission import ProgramFinancialQualification,BUNDLE_PREFIX
from trading_system.risk.reservation_repository import ReservationRepository
from trading_system.storage.models import scope_id


def digest(value):return canonical_hash(domain_payload(value))


def quote_case(database, *, real_options=None, quote_options=None):
    c=real_case(database, **(real_options or {}))
    # real_case initializes the existing synthetic helper import paths only.
    from test_quote_risk_assessment import quote_risk_case
    q=quote_risk_case(**(quote_options or {}));original=c.ingress.load_inputs(None,None,None,None)
    context=c.record.context;scope=c.scope
    caps=replace(q.facts.target_facts.capabilities,scope=scope,qualification='PAPER_VERIFIED',evidence=original.capabilities.evidence)
    profile=replace(q.facts.target_facts.profile,scope=scope)
    rules=replace(q.facts.target_facts.scope_facts.rules,scope=scope)
    p=replace(q.plan.original_proposal,proposal_id='quote-'+uuid4().hex,
        program=replace(q.plan.original_program,program_id='quote-program-'+uuid4().hex))
    q.plan=compile_quote_program(p,rules,caps,profile=profile,scope=scope,now=c.c.facts.inputs.clock.utc_now,
        expected_build_digest=q.plan.adapter_build_digest,expected_rule_revision=rules.revision,
        expected_capabilities_hash=digest(caps),expected_instrument_ref=p.instrument_ref,allow_round_down=True)
    q.book=c.c.book
    s=replace(q.facts.target_facts.scope_facts,compilation_hash=digest(q.plan),rules=rules,context=context,
        owner=c.c.facts.target_facts.owner,collateral_group_id=c.c.terms.collateral_group_id,
        book_sequence=q.book.sequence,book_hash=digest(q.book),
        inputs=replace(c.c.facts.target_facts.inputs,context=context,allow_round_down=True),
        unit_binding=replace(q.facts.target_facts.scope_facts.unit_binding,scope=scope,compilation_hash=digest(q.plan)))
    bounds=replace(q.facts.target_facts.bounds,scope=scope,compilation_hash=digest(q.plan),
        rules_hash=q.plan.rules_hash,profile_hash=q.plan.profile_hash,evidence=q.book.evidence[0])
    target=replace(q.facts.target_facts,scope_facts=s,profile=profile,capabilities=caps,bounds=bounds)
    facts=ProgramIntakeInputs(compilation=q.plan,rules=rules,capabilities=caps,profile=profile,
        authorization=original.authorization,clock=c.c.facts.inputs.clock,expected_build_digest=q.plan.adapter_build_digest,
        expected_instrument_ref=p.instrument_ref,allow_round_down=True)
    ingress=GenericProgramIngress(c.intake,scope,lambda *args:facts,clock=lambda:c.c.facts.inputs.clock.utc_now)
    source,record=ingress.accept(p,context)
    q.terms=replace(q.terms,source=replace(q.terms.source,hash=q.plan.proposal_hash),
        decision_id=record.intent_id,scope=scope,authorization_id=context.authorization_id,
        owner_strategy_id=s.owner.owner_strategy_id,ownership_epoch=s.owner.ownership_epoch,policy_revision=s.owner.policy_revision,
        collateral_group_id=s.collateral_group_id,basis_fingerprint=q.book.basis_fingerprint,snapshot_id=context.snapshot_ref.id)
    q.policy=c.c.policy
    def load(tx,actual_source,intake,book,clock):
        from trading_system.intents.quote_assessment_archive import QuoteAssessmentArchiveV1
        from trading_system.risk.quote_financial_admission import QuoteFinancialInputsV1
        from trading_system.risk.evaluator import RiskEvaluator
        from trading_system.accounts.authorization import AuthorizationService
        current=replace(c.c.facts.inputs,intent_id=intake.intent_id,clock=clock,allow_round_down=True)
        sf=replace(s,transaction_id=tx.transaction_id,book_hash=digest(book),inputs=replace(s.inputs,clock=clock))
        tf=replace(target,scope_facts=sf)
        q.allocation=compile_quote_target(q.plan,book,tf);q.book=book
        q.facts=replace(q.facts,inputs=current,market=c.c.facts.market,target_facts=tf,evidence=book.evidence,
            authorization=AuthorizationService(lambda _:original.authorization,clock=lambda:utc_timestamp(clock.utc_now)).validate(context),
            protection=tuple(replace(p,scope=scope,program_hash=q.plan.program_hash,
                allocation_hash=digest(q.allocation),book_hash=digest(book)) for p in q.facts.protection))
        decision=RiskEvaluator(current).evaluate_quote_program(q.terms,q.plan,q.allocation,book,q.policy,q.facts)
        archive=QuoteAssessmentArchiveV1(contract_version='2026-10-30',record_kind='QUOTE_ASSESSMENT_ARCHIVE_V1',
            intent_id=intake.intent_id,source=actual_source,compilation=q.plan,book=book,allocation=q.allocation,
            risk_terms=q.terms,risk_facts=q.facts,risk_policy=q.policy,assessment=decision)
        assets=(t.Money(currency='USDT',amount=D('1000000')),)
        return QuoteFinancialInputsV1(archive=archive,authorization=original.authorization,
            group_capacity=assets,strategy_capacity=assets,buffer=(t.Money(currency='USDT',amount=D(0)),),
            reconciled_holds=tuple(p for p in ReservationRepository(tx).group()
                if p.reservation.intent_id!=intake.intent_id))
    def qualify(tx,a,clock):
        return ProgramFinancialQualification(scope=scope,compilation_hash=digest(a.compilation),
            capabilities_hash=a.compilation.capabilities_hash,rules_hash=a.compilation.rules_hash,
            profile_hash=a.compilation.profile_hash,adapter_build_digest=a.compilation.adapter_build_digest,
            execution_mode=a.compilation.original_program.execution_mode,evidence=caps.evidence,valid_until=q.terms.valid_until)
    c.q=q;c.qsource=source;c.qrecord=record;c.qkey=intake_key(p,context);c.qingress=ingress
    c.qload=load;c.qqualify=qualify;c.bounds=lambda *args:q.facts.target_facts.bounds
    return c


def test_quote_source_uses_the_same_restricted_intake_and_original_replay(database):
    import psycopg
    c=quote_case(database)
    assert c.qrecord.record_kind=='QUOTE_PROGRAM_INTAKE_V1'
    assert c.qingress.accept(c.qrecord.proposal,c.qrecord.context)==(c.qsource,c.qrecord)
    with c.owner.transaction(c.scope) as tx:assert read_program_source(tx,c.qkey)==(c.qsource,c.qrecord)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.owner.transaction(c.scope) as tx:tx._append('order_program_intakes','forged-quote',c.qrecord,0)


def reserve(c,tx,**kwargs):
    return c.service._reserve_program(tx,c.qkey,('immutable-native-quote-id',),load_inputs=c.qload,
        load_qualification=c.qqualify,load_clock=lambda _:c.c.facts.inputs.clock,
        load_execution_bounds=c.bounds,**kwargs)


def test_quote_bundle_and_real_hold_share_kernel_sql_transaction_and_attachment_rollback(database):
    c=quote_case(database)
    class MissingAttachment(RuntimeError):pass
    def operation(tx):
        bundle=reserve(c,tx)
        assert bundle.intent.record_kind=='QUOTE_FINANCIAL_INTENT_V1' and not bundle.execution_authorized
        assert bundle.reservations[0].reservation.amount==4000
        assert tx.intents.get(c.qrecord.intent_id)==bundle.intent
        rows=tx.execute('SELECT xmin::text FROM ledger.intents WHERE scope_id=%s AND record_id=%s UNION ALL '
            'SELECT xmin::text FROM ledger.reservations WHERE scope_id=%s AND payload->>\'intent_id\'=%s UNION ALL '
            'SELECT xmin::text FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
            (scope_id(c.scope),c.qrecord.intent_id,scope_id(c.scope),c.qrecord.intent_id,
                scope_id(c.scope),BUNDLE_PREFIX+c.qrecord.intent_id)).fetchall()
        assert len(rows)==3 and len({r[0] for r in rows})==1
        raise MissingAttachment('actual request attachment not available yet')
    with pytest.raises(MissingAttachment):c.service._transaction(operation)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.qrecord.intent_id) is None and tx.reservations.for_intent(c.qrecord.intent_id)==()
        assert read_program_source(tx,c.qkey)==(c.qsource,c.qrecord)


@pytest.mark.parametrize('fault',['missing_bounds','changed_bounds','capacity','foreign_transaction'])
def test_quote_current_enforcement_and_financial_inputs_fail_closed_before_any_commit(database,fault):
    c=quote_case(database);original=c.qload
    if fault=='missing_bounds':c.bounds=None
    if fault=='changed_bounds':c.bounds=lambda *args:replace(c.q.facts.target_facts.bounds,model_hash='f'*64)
    def load(*args):
        value=original(*args)
        if fault=='capacity':value=replace(value,group_capacity=(t.Money(currency='USDT',amount=D(1)),))
        if fault=='foreign_transaction':
            tf=value.archive.risk_facts.target_facts
            f=replace(value.archive.risk_facts,target_facts=replace(tf,scope_facts=replace(tf.scope_facts,transaction_id='foreign')))
            value=replace(value,archive=replace(value.archive,risk_facts=f))
        return value
    c.qload=load
    with pytest.raises(ValueError):c.service._transaction(lambda tx:reserve(c,tx))
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.qrecord.intent_id) is None and tx.reservations.for_intent(c.qrecord.intent_id)==()


@pytest.mark.parametrize('fault',['final_enforcement_expired','final_enforcement_changed_book'])
def test_last_enforcement_read_cannot_outlive_clock_or_current_book_barrier(database,fault):
    c=quote_case(database);original=c.bounds;calls=[];wall=[c.c.facts.inputs.clock.utc_now]
    c.service.clock=lambda:wall[0]
    def bounds(tx,*args):
        calls.append(1)
        if len(calls)==2:
            if fault=='final_enforcement_expired':wall[0]=c.q.terms.valid_until
            else:
                book=tx.projections.book(c.scope)
                tx.projections.save_book(replace(book,sequence=book.sequence+1),book.sequence)
        return original(tx,*args)
    c.bounds=bounds
    with pytest.raises(ValueError):c.service._transaction(lambda tx:reserve(c,tx))
    assert len(calls)==2
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.qrecord.intent_id) is None and tx.reservations.for_intent(c.qrecord.intent_id)==()

