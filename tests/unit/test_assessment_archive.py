"""New static type/replay; untrusted claims cannot substitute for assessment."""
from dataclasses import replace,fields
from decimal import Decimal

import pytest

from unit.support_program_financial import financial_case
from trading_system.contracts.canonical import domain_payload,canonical_hash
from trading_system.contracts import types as t
from trading_system.storage.models import decode
from trading_system.risk.evaluator import RiskEvaluator


def actual_archive(*,cover_actual=False,**kwargs):
    # Reuse the real generic assessment fixture and actual policy/evaluator.
    financial_case()  # initializes only fixed sibling fixture import paths
    from test_program_risk_assessment import risk_case
    c=risk_case(**kwargs)
    if cover_actual:
        # The test supplies a stronger whole-current-position duty, so a
        # missing-coverage denial cannot conceal retained-risk arithmetic.
        quantity=sum((p.actual_quantity.value.copy_abs() for p in c.book.positions),Decimal(0))
        c.facts=replace(c.facts,protection=tuple(replace(p,maximum_quantity=replace(p.maximum_quantity,value=quantity))
            for p in c.facts.protection))
    return archive_from_case(c)


def archive_from_case(c):
    from test_program_risk_assessment import assess
    from trading_system.contracts.program_financial import FinancialSource,FinancialBounds
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
    decision=assess(c);terms=c.terms
    source=FinancialSource(source_kind='ORDER_PROPOSAL',source_version=terms.source.version,
        source_hash=terms.source.hash,intake_record_id='unverified-fixture-intake',intake_hash='b'*64,
        intake_transaction_id='fixture-intake-tx',context=c.facts.target_facts.context,
        context_hash=canonical_hash(domain_payload(c.facts.target_facts.context)))
    bounds=FinancialBounds(maximum_planned_loss=terms.maximum_planned_loss,
        maximum_holding_seconds=terms.maximum_holding_seconds,protection_policy=terms.protection_policy,
        stop_trigger=terms.stop_trigger,stop_price=terms.stop_price,review_due_at=terms.review_due_at)
    archive=ProgramFinancialAssessmentArchive(contract_version='2026-10-29',
        record_kind='PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE',archive_revision=1,intent_id=terms.decision_id,
        source=source,bounds=bounds,compilation=c.plan,rules=c.facts.rules,capabilities=c.facts.capabilities,
        profile=c.facts.profile,book=c.book,target_facts=c.facts.target_facts,allocation=c.allocation,
        budget_facts=c.facts.budget_facts,exposure=c.exposure,risk_terms=terms,risk_metrics=decision.metrics,
        risk_policy=c.policy,risk_facts=c.facts,assessment=decision)
    return archive


def bounded_archive(*,missing_stop=True):
    financial_case()
    from test_program_reduction_risk import bounded_case
    return archive_from_case(bounded_case(missing_stop=missing_stop))


def claimed_archive(template='ATOMIC'):
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
    from trading_system.risk.program_assessment import ProgramRiskFacts,ProgramRiskDecision
    from unit.support_ip_06 import make_case
    c=financial_case(template);a=c.archive;terms=a.risk_terms
    old=make_case(a.compilation.scope.product)
    market=replace(old.market,ref=a.source.context.snapshot_ref,scope=a.book.scope,coverage=a.book.coverage)
    inputs=replace(old.inputs,intent_id=a.intent_id,authorization_id=terms.authorization_id,
        owner_strategy_id=terms.owner_strategy_id,ownership_epoch=terms.ownership_epoch,
        policy_revision=terms.policy_revision,scope=terms.scope,collateral_group_id=terms.collateral_group_id,
        basis_fingerprint=a.book.basis_fingerprint,book_sequence=a.book.sequence,snapshot_ref=market.ref,
        clock=a.target_facts.inputs.clock,expires_at=terms.valid_until,instrument=a.target_facts.inputs.instrument,
        session=a.target_facts.inputs.session,multiplier=a.target_facts.unit_binding.ledger_multiplier,
        base_asset=a.rules.base_asset,fee_reserve=(),funding_reserve=(),spendable=a.book.balances)
    facts=ProgramRiskFacts(inputs=inputs,market=market,authorization=t.AuthorizationDecision(
            allowed=True,authorization_id=terms.authorization_id,rejection_codes=(),evidence=a.book.evidence),
        target_facts=a.target_facts,budget_facts=a.budget_facts,rules=a.rules,capabilities=a.capabilities,profile=a.profile,
        expected_build_digest=a.compilation.adapter_build_digest,expected_instrument_ref=a.compilation.original_proposal.instrument_ref,
        node_bounds=None,working_bounds=(),pending_bounds=(),additional_exit_fees=None,protection=None,
        allowed_stop_triggers=None,maximum_live_orders=None,holding_horizon_seconds=None,entry_slippage_bps=None,
        evidence=a.book.evidence,as_of=terms.created_at,valid_until=terms.valid_until)
    # Deliberately untrusted input for a negative test, not an assessment stub.
    # Missing evidence stays None rather than being converted to zero/True.
    digest=lambda value:canonical_hash(domain_payload(value))
    decision=ProgramRiskDecision(risk_decision_id='untrusted-claim',source=terms.source,terms_hash=digest(terms),
        compilation_hash=digest(a.compilation),allocation_hash=digest(a.allocation),exposure_hash=digest(a.exposure),
        facts_hash=digest(facts),policy_hash=digest(a.risk_policy),evaluated_at=terms.created_at,
        expires_at=terms.valid_until,verdict='ALLOW',metrics=a.risk_metrics,rejection_codes=(),obligations=())
    values={f.name:getattr(a,f.name) for f in fields(a)}
    values.update(record_kind='PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE',archive_revision=1,
        risk_facts=facts,assessment=decision)
    return ProgramFinancialAssessmentArchive(**values)


@pytest.mark.parametrize('template',['ATOMIC','VP'])
def test_static_new_kind_restores_whole_v2_v3_and_full_fact_type(template):
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
    a=claimed_archive(template)
    restored=decode(ProgramFinancialAssessmentArchive,domain_payload(a))
    assert restored==a
    assert type(restored.compilation.original_proposal) is type(a.compilation.original_proposal)
    assert restored.risk_facts.node_bounds is None and restored.risk_facts.working_bounds==()
    assert not restored.execution_authorized


def test_old_reader_cannot_silently_downgrade_new_archive():
    from trading_system.intents.program_financial_records import ProgramFinancialArchiveCodec
    a=claimed_archive()
    with pytest.raises(ValueError):ProgramFinancialArchiveCodec().decode(domain_payload(a))
    with pytest.raises(ValueError):ProgramFinancialArchiveCodec().encode(a)


def test_missing_actual_assessor_blocks_caller_allow_and_all_converters(monkeypatch):
    from trading_system.intents.program_assessment_archive import (
        ProgramFinancialAssessmentArchiveCodec,financial_intent_from_assessment_archive)
    monkeypatch.delattr(RiskEvaluator,'evaluate_program',raising=False)
    a=claimed_archive()
    with pytest.raises(ValueError,match='PROGRAM_RISK_ASSESSMENT_NOT_AVAILABLE'):
        ProgramFinancialAssessmentArchiveCodec().encode(a)
    with pytest.raises(ValueError,match='PROGRAM_RISK_ASSESSMENT_NOT_AVAILABLE'):
        financial_intent_from_assessment_archive(a)


@pytest.mark.parametrize('fault',['compilation','allocation','exposure','facts'])
def test_replayed_source_content_is_required_before_assessment(fault,monkeypatch):
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec
    monkeypatch.delattr(RiskEvaluator,'evaluate_program',raising=False)
    a=claimed_archive()
    if fault=='compilation':a=replace(a,compilation=replace(a.compilation,requested_features=()))
    if fault=='allocation':a=replace(a,allocation=replace(a.allocation,requests=()))
    if fault=='exposure':a=replace(a,exposure=replace(a.exposure,obligations=()))
    if fault=='facts':a=replace(a,risk_facts=replace(a.risk_facts,target_facts=replace(a.target_facts,transaction_id='other')))
    with pytest.raises(ValueError,match='ASSESSMENT_ARCHIVE_'):
        ProgramFinancialAssessmentArchiveCodec().encode(a)


def test_full_fact_dimensions_and_scoped_original_refs_restore_without_coercion():
    from decimal import Decimal as D
    from trading_system.risk.program_assessment import NodeRiskBound,WorkingRiskBound,PendingRiskBound,ProtectionCoverage
    from trading_system.risk.program_exposure import FeeBound
    from trading_system.risk.valuation import FxRate
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
    a=claimed_archive('VP');f=a.risk_facts;scope=a.book.scope
    ref=a.book.evidence[0]
    lower=t.Price(value=D('81000.01'),currency='USDT');upper=t.Price(value=D('90000.09'),currency='USDT')
    node=NodeRiskBound(node_id=a.compilation.nodes[0].original.node_id,minimum_price=lower,maximum_price=upper,
        execution_fees=(FeeBound(amount=t.Money(amount=D('.00001'),currency='BNB'),debit='EXTERNAL_BALANCE'),),
        funding_reserve=(t.Money(amount=D('1.125'),currency='USDT'),),
        margin_reserve=(t.Money(amount=D('10.25'),currency='USDT'),),holding_horizon_seconds=3600,
        maximum_live_children=3,evidence_refs=(ref.id,))
    working=WorkingRiskBound(logical_client_order_id='same-text-id',scope=scope,side='SELL',
        quantity=t.Quantity(value=D('.25'),unit='BASE'),minimum_price=lower,maximum_price=upper,evidence_refs=(ref.id,))
    pending=PendingRiskBound(obligation_id='pending',scope=scope,source_family='APPLICATION_WRITE',
        service_namespace='EXECUTION_ALGO',canonical_command_refs=('PROGRAM_WRITE:EXECUTION_ALGO:scoped:same-text-id',),
        original_command_refs=('same-text-id',),minimum_price=lower,maximum_price=upper,evidence_refs=(ref.id,))
    duty=ProtectionCoverage(scope=scope,owner_strategy_id=a.risk_terms.owner_strategy_id,
        ownership_epoch=a.risk_terms.ownership_epoch,program_hash=a.compilation.program_hash,
        allocation_hash=canonical_hash(domain_payload(a.allocation)),book_hash=canonical_hash(domain_payload(a.book)),
        origin='NATIVE_PER_FILL_DUTY',phases=('PARTIAL_FILL','FULL_FILL'),node_ids=(node.node_id,),
        maximum_quantity=t.Quantity(value=D(1),unit='BASE'),stop_price=t.Price(value=D('81330'),currency='USDT'),
        stop_trigger='LAST',duty_refs=(ref,),valid_until=a.risk_terms.valid_until)
    rates=(FxRate(source='BNB',target='USDT',asset_rate=D('500.01'),liability_rate=D('501.02'),haircut=D('.01'),evidence_id=ref.id),)
    f=replace(f,inputs=replace(f.inputs,fx_rates=rates),node_bounds=(node,),working_bounds=(working,),
        pending_bounds=(pending,),additional_exit_fees=node.execution_fees,protection=(duty,),
        allowed_stop_triggers=('LAST',),maximum_live_orders=4,holding_horizon_seconds=3600,entry_slippage_bps=D('12.5'))
    a=replace(a,risk_facts=f)
    # Static restoration is deliberately separate from qualified replay. These
    # untrusted claims cannot become a decision or permit through the codec.
    restored=decode(ProgramFinancialAssessmentArchive,domain_payload(a))
    assert restored==a
    assert restored.risk_facts.pending_bounds[0].original_command_refs==('same-text-id',)
    assert restored.risk_facts.inputs.fx_rates==rates and restored.risk_facts.node_bounds[0].funding_reserve==node.funding_reserve
    assert restored.risk_facts.protection[0].phases==('PARTIAL_FILL','FULL_FILL')


def test_deny_retains_missing_metrics_instead_of_fabricating_zeros():
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
    a=claimed_archive();a=replace(a,risk_metrics=None,
        assessment=replace(a.assessment,verdict='DENY',metrics=None,rejection_codes=('UNTRUSTED_INPUT_CLAIM',)))
    restored=decode(ProgramFinancialAssessmentArchive,domain_payload(a))
    assert restored.risk_metrics is None and restored.assessment.metrics is None


@pytest.mark.parametrize('revision',[True,2])
def test_unknown_or_boolean_archive_revision_is_rejected(revision,monkeypatch):
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec
    monkeypatch.delattr(RiskEvaluator,'evaluate_program',raising=False)
    a=replace(claimed_archive(),archive_revision=revision)
    with pytest.raises(ValueError):ProgramFinancialAssessmentArchiveCodec().encode(a)


@pytest.mark.parametrize('template',['ATOMIC','VP','OTOCO'])
def test_real_complete_assessment_is_replayed_before_non_authorizing_metadata(template):
    from trading_system.intents.program_assessment_archive import (ProgramFinancialAssessmentArchiveCodec,
        financial_intent_from_assessment_archive,financial_risk_basis_from_assessment_archive)
    from trading_system.contracts.ledger_intents import LedgerIntentCodec
    a=actual_archive(template=template)
    assert a.assessment.verdict=='ALLOW',a.assessment.rejection_codes
    codec=ProgramFinancialAssessmentArchiveCodec();restored=codec.decode(codec.encode(a))
    assert restored==a and type(restored.compilation.original_proposal) is type(a.compilation.original_proposal)
    intent=financial_intent_from_assessment_archive(a)
    basis=financial_risk_basis_from_assessment_archive(intent,a)
    assert intent.hashes.risk_facts==canonical_hash(domain_payload(a.risk_facts))==a.risk_metrics.facts_hash
    assert intent.hashes.risk_facts!=canonical_hash(domain_payload(a.budget_facts))
    assert intent.hashes.archive==codec.hash(a) and basis.intent_hash==LedgerIntentCodec().hash(intent)
    assert not a.execution_authorized and not intent.execution_authorized and not basis.execution_authorized
    assert not hasattr(intent,'execution')


def test_real_derived_deny_retains_none_metrics_and_cannot_be_converted():
    from trading_system.intents.program_assessment_archive import (
        ProgramFinancialAssessmentArchiveCodec,financial_intent_from_assessment_archive)
    a=actual_archive();facts=replace(a.risk_facts,node_bounds=None)
    decision=RiskEvaluator(facts.inputs).evaluate_program(a.risk_terms,a.compilation,a.allocation,
        a.exposure,a.book,a.risk_policy,facts)
    assert decision.verdict=='DENY' and decision.metrics is None
    a=replace(a,risk_facts=facts,assessment=decision,risk_metrics=None)
    assert ProgramFinancialAssessmentArchiveCodec().decode(ProgramFinancialAssessmentArchiveCodec().encode(a))==a
    with pytest.raises(ValueError,match='PROGRAM_RISK_ASSESSMENT_NOT_ALLOW'):
        financial_intent_from_assessment_archive(a)


def test_self_consistent_forged_allow_cannot_override_actual_unknown_fact_denial():
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec
    a=actual_archive();facts=replace(a.risk_facts,node_bounds=None)
    denial=RiskEvaluator(facts.inputs).evaluate_program(a.risk_terms,a.compilation,a.allocation,
        a.exposure,a.book,a.risk_policy,facts)
    assert denial.verdict=='DENY' and denial.metrics is None
    claimed_metrics=replace(a.risk_metrics,facts_hash=canonical_hash(domain_payload(facts)))
    claim=replace(denial,verdict='ALLOW',metrics=claimed_metrics,rejection_codes=())
    body=domain_payload(claim);del body['risk_decision_id']
    claim=replace(claim,risk_decision_id=canonical_hash(body))
    forged=replace(a,risk_facts=facts,assessment=claim,risk_metrics=claimed_metrics)
    with pytest.raises(ValueError,match='ASSESSMENT_ARCHIVE_DECISION_REPLAY_MISMATCH'):
        ProgramFinancialAssessmentArchiveCodec().encode(forged)


@pytest.mark.parametrize('fault',['decision','metrics','policy','price','funding','protection','facts_alias'])
def test_supplied_allow_or_consistent_claim_hashes_cannot_replace_real_assessment(fault):
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec
    from decimal import Decimal as D
    a=actual_archive()
    if fault=='decision':a=replace(a,assessment=replace(a.assessment,risk_decision_id='claimed'))
    if fault=='metrics':a=replace(a,risk_metrics=replace(a.risk_metrics,capital=t.Money(amount=D(0),currency='USDT')))
    if fault=='policy':a=replace(a,risk_policy=replace(a.risk_policy,maximum_order_notional='1'))
    if fault=='price':
        bound=a.risk_facts.node_bounds[0]
        a=replace(a,risk_facts=replace(a.risk_facts,node_bounds=(replace(bound,maximum_price=t.Price(value=D('90000'),currency='USDT')),)))
    if fault=='funding':
        bound=a.risk_facts.node_bounds[0]
        a=replace(a,risk_facts=replace(a.risk_facts,node_bounds=(replace(bound,funding_reserve=None),)))
    if fault=='protection':a=replace(a,risk_facts=replace(a.risk_facts,protection=()))
    if fault=='facts_alias':
        alias=canonical_hash(domain_payload(a.budget_facts))
        m=replace(a.risk_metrics,facts_hash=alias)
        a=replace(a,assessment=replace(a.assessment,facts_hash=alias,metrics=m),risk_metrics=m)
    with pytest.raises(ValueError,match='ASSESSMENT_ARCHIVE_'):
        ProgramFinancialAssessmentArchiveCodec().encode(a)


def test_old_known_content_conversion_stays_blocked_even_when_real_assessor_exists():
    from trading_system.intents.program_financial_records import financial_intent_from_archive
    assert callable(getattr(RiskEvaluator,'evaluate_program',None))
    with pytest.raises(ValueError,match='PROGRAM_RISK_FACTS_ARCHIVE_REQUIRED'):
        financial_intent_from_archive(financial_case().archive)


def test_partial_reduction_archive_preserves_derived_retained_position_loss():
    from trading_system.intents.program_assessment_archive import financial_intent_from_assessment_archive
    a=actual_archive(actual='1',target='.6',quantity='.4',side='SELL',cover_actual=True)
    assert a.assessment.verdict=='ALLOW',a.assessment.rejection_codes
    assert a.risk_metrics.planned_loss.amount>0
    assert financial_intent_from_assessment_archive(a).target.remaining.value<0


def test_partial_reduction_bad_retained_stop_cannot_become_financial_metadata():
    from trading_system.intents.program_assessment_archive import financial_intent_from_assessment_archive
    a=actual_archive(actual='1',target='.6',quantity='.4',side='SELL',stop='90000',cover_actual=True)
    assert a.assessment.verdict=='DENY'
    with pytest.raises(ValueError,match='PROGRAM_RISK_ASSESSMENT_NOT_ALLOW'):
        financial_intent_from_assessment_archive(a)


def test_genuine_bounded_exit_preserves_false_protection_unknown_loss_and_duties():
    from trading_system.risk.reduction import ReductionRiskMetrics
    from trading_system.intents.program_assessment_archive import (ProgramFinancialAssessmentArchiveCodec,
        financial_intent_from_assessment_archive,financial_risk_basis_from_assessment_archive)
    a=bounded_archive();assert a.assessment.verdict=='ALLOW',a.assessment.rejection_codes
    restored=ProgramFinancialAssessmentArchiveCodec().decode(ProgramFinancialAssessmentArchiveCodec().encode(a))
    assert type(restored.risk_metrics) is ReductionRiskMetrics and type(restored.assessment.metrics) is ReductionRiskMetrics
    assert restored.risk_metrics.protection_complete is False and restored.risk_metrics.planned_loss is None
    assert restored.risk_metrics.residual.hard_loss_status=='UNKNOWN' and restored.risk_metrics.residual.hard_loss_bound is None
    assert restored.risk_metrics.residual.risk_permission==t.RiskPermission.EXIT_ONLY
    assert {'PERSIST_RESIDUAL_RISK_LOCK','RETAIN_MANAGE_UNTIL_FLAT','CURRENT_LOCKED_ACTION_VERIFIER_REQUIRED'}<=set(restored.assessment.obligations)
    intent=financial_intent_from_assessment_archive(restored)
    basis=financial_risk_basis_from_assessment_archive(intent,restored)
    assert intent.hashes.risk_metrics==canonical_hash(domain_payload(a.risk_metrics))
    assert basis.archive_hash==intent.hashes.archive and not intent.execution_authorized


@pytest.mark.parametrize('fault',['proof','erase_obligations','erase_subtype','false_to_true','kind'])
def test_bounded_exit_cannot_forge_or_erase_residual_proof_and_obligations(fault):
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec
    a=bounded_archive()
    if fault=='proof':a=replace(a,risk_facts=replace(a.risk_facts,
        reduction_proof=replace(a.risk_facts.reduction_proof,book_hash='f'*64)))
    if fault=='erase_obligations':
        claim=replace(a.assessment,obligations=())
        body=domain_payload(claim);del body['risk_decision_id']
        a=replace(a,assessment=replace(claim,risk_decision_id=canonical_hash(body)))
    payload=domain_payload(a)
    if fault=='erase_subtype':
        for metric in (payload['risk_metrics'],payload['assessment']['metrics']):
            for key in ('contract_version','metric_kind','reduction_proof_hash','residual'):metric.pop(key)
    if fault=='false_to_true':payload['risk_metrics']['protection_complete']=True
    if fault=='kind':payload['risk_metrics']['metric_kind']='UNKNOWN_REDUCTION'
    with pytest.raises(ValueError):ProgramFinancialAssessmentArchiveCodec().decode(payload)


def test_known_recovery_lock_is_not_relabelled_as_a_bounded_exit():
    from test_program_reduction_risk import bounded_case
    from test_program_risk_assessment import rebind,digest
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec,financial_intent_from_assessment_archive
    c=bounded_case(missing_stop=True);old=c.facts.reduction_proof
    lock=replace(old.locks[0],cause='ORDER_OUTCOME_UNKNOWN',permission=t.RiskPermission.RECOVERY_REQUIRED)
    position=replace(c.book.positions[0],risk_locks=(lock,))
    rebind(c,book=replace(c.book,positions=(position,)))
    report=replace(old.report,positions=(position,));ref=replace(old.archive_ref,hash=digest(report))
    proof=replace(old,position=position,locks=(lock,),book_hash=digest(c.book),allocation_hash=digest(c.allocation),
        report=report,archive_ref=ref)
    c.facts=replace(c.facts,reduction_proof=proof,evidence=(*c.book.evidence,ref))
    a=archive_from_case(c)
    assert a.assessment.verdict=='DENY' and a.assessment.metrics is None
    ProgramFinancialAssessmentArchiveCodec().encode(a)
    with pytest.raises(ValueError,match='PROGRAM_RISK_ASSESSMENT_NOT_ALLOW'):financial_intent_from_assessment_archive(a)


def test_semantically_equal_decimal_cannot_change_recomputed_assessment_bytes():
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec
    a=actual_archive()
    metrics=replace(a.risk_metrics,fees=t.Money(amount=Decimal('-0'),currency=a.risk_metrics.fees.currency))
    a=replace(a,risk_metrics=metrics,assessment=replace(a.assessment,metrics=metrics))
    with pytest.raises(ValueError,match='ASSESSMENT_ARCHIVE_'):
        ProgramFinancialAssessmentArchiveCodec().encode(a)


def test_false_and_zero_cannot_change_compiled_parameter_wire_bytes():
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec
    a=actual_archive(product=t.Product.USD_M_PERP,conditional=True)
    assert a.assessment.verdict=='ALLOW',a.assessment.rejection_codes
    def modified(parameters):
        return tuple(replace(p,value=0) if p.wire_name=='priceProtect' else p for p in parameters)
    assert any(p.wire_name=='priceProtect' and p.value is False for p in a.compilation.requests[0].parameters)
    compiled=replace(a.compilation,nodes=tuple(replace(n,parameters=modified(n.parameters)) for n in a.compilation.nodes),
        requests=tuple(replace(r,parameters=modified(r.parameters)) for r in a.compilation.requests))
    assert compiled==a.compilation and canonical_hash(domain_payload(compiled))!=canonical_hash(domain_payload(a.compilation))
    with pytest.raises(ValueError,match='ASSESSMENT_ARCHIVE_COMPILATION_REPLAY_MISMATCH'):
        ProgramFinancialAssessmentArchiveCodec().encode(replace(a,compilation=compiled))
