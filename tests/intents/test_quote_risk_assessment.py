"""Real interval risk and replay; synthetic facts never establish authority."""
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.risk.evaluator import RiskEvaluator
from test_quote_target_allocation import allocation_case
from test_program_risk_assessment import risk_case


def digest(value):return canonical_hash(domain_payload(value))


def quote_risk_case(*,actual='0',side='BUY',quote='4000',fees=()):
    from trading_system.risk.quote_assessment import QuoteRiskTermsV1,QuoteRiskFactsV1
    from trading_system.risk.terms import RiskSource
    from trading_system.intents.quote_target_allocation import compile_quote_target
    from trading_system.intents.quote_program_compilation import compile_quote_program
    c=risk_case(actual=actual,quantity=str(D(1)-D(actual)));plan,book,target=allocation_case()
    node=plan.original_program.nodes[0]
    node=replace(node,order=replace(node.order,side=side,quantity=replace(node.order.quantity,value=quote)))
    proposal=replace(plan.original_proposal,program=replace(plan.original_program,nodes=(node,)))
    plan=compile_quote_program(proposal,target.scope_facts.rules,target.capabilities,profile=target.profile,
        scope=plan.scope,now=target.scope_facts.inputs.clock.utc_now,
        expected_build_digest=target.expected_build_digest,expected_rule_revision=target.scope_facts.rules.revision,
        expected_capabilities_hash=plan.capabilities_hash,expected_instrument_ref=target.expected_instrument_ref,
        allow_round_down=target.scope_facts.inputs.allow_round_down)
    book=replace(book,balances=c.book.balances,positions=c.book.positions)
    context=replace(target.scope_facts.context,snapshot_ref=c.facts.market.ref)
    scope_facts=replace(target.scope_facts,book_hash=digest(book),context=context,compilation_hash=digest(plan),
        unit_binding=replace(target.scope_facts.unit_binding,compilation_hash=digest(plan)),
        inputs=replace(target.scope_facts.inputs,context=context))
    target=replace(target,scope_facts=scope_facts,bounds=replace(target.bounds,compilation_hash=digest(plan),fees=fees,
        maximum_gross_quote=t.Money(currency='USDT',amount=D(quote))))
    allocation=compile_quote_target(plan,book,target)
    p=plan.original_proposal;s=scope_facts
    terms=QuoteRiskTermsV1(contract_version='2026-10-30',record_kind='QUOTE_RISK_TERMS_V1',
        source=RiskSource(kind='POSITION_VALUE_BAND_PROPOSAL',version=p.contract_version,hash=plan.proposal_hash),
        decision_id=c.terms.decision_id,scope=plan.scope,authorization_id=context.authorization_id,
        owner_strategy_id=s.owner.owner_strategy_id,ownership_epoch=s.owner.ownership_epoch,
        policy_revision=s.owner.policy_revision,collateral_group_id=s.collateral_group_id,
        basis_fingerprint=book.basis_fingerprint,snapshot_id=c.facts.market.ref.id,action=p.action,
        goal=p.goal,quantity_unit=s.unit_binding.ledger_unit,requested_leverage=p.requested_leverage,
        maximum_planned_loss=c.terms.maximum_planned_loss,protection_policy=p.protection_policy,
        stop_price=D(p.exit_plan.stop_price),stop_trigger=p.exit_plan.stop_trigger,
        maximum_holding_seconds=p.exit_plan.maximum_holding_seconds,review_due_at=p.exit_plan.review_due_at,
        as_of=c.terms.as_of,created_at=c.terms.created_at,valid_until=allocation.valid_until,
        submission_deadline=p.program.submit_valid_until)
    current=replace(c.facts.inputs,instrument=s.inputs.instrument,session=s.inputs.session,
        order_sides=s.inputs.order_sides,clock=s.inputs.clock,allow_round_down=s.inputs.allow_round_down,
        fee_reserve=tuple(f.amount for f in fees))
    proofs=tuple(replace(proof,program_hash=plan.program_hash,allocation_hash=digest(allocation),
        book_hash=digest(book),maximum_quantity=t.Quantity(unit='BASE',value=max(D(actual),allocation.position.projected_maximum.value)),
        stop_price=t.Price(currency='USDT',value=D('40000'))) for proof in c.facts.protection)
    facts=QuoteRiskFactsV1(contract_version='2026-10-30',record_kind='QUOTE_RISK_FACTS_V1',
        inputs=current,market=c.facts.market,authorization=c.facts.authorization,target_facts=target,
        working_bounds=(),pending_bounds=(),additional_exit_fees=(),protection=proofs,
        allowed_stop_triggers=c.facts.allowed_stop_triggers,maximum_live_orders=2,
        holding_horizon_seconds=c.facts.holding_horizon_seconds,entry_slippage_bps=D('2000'),
        evidence=book.evidence,as_of=c.facts.as_of,valid_until=c.facts.valid_until)
    return SimpleNamespace(plan=plan,book=book,allocation=allocation,terms=terms,facts=facts,policy=c.policy)


def assess(c):
    return RiskEvaluator(c.facts.inputs).evaluate_quote_program(c.terms,c.plan,c.allocation,c.book,c.policy,c.facts)


def test_quote_risk_uses_same_policy_core_and_keeps_nofill_exposure_without_scalar_goal():
    c=quote_risk_case();decision=assess(c)
    assert decision.verdict=='ALLOW',decision.rejection_codes
    assert not hasattr(c.terms,'target') and c.terms.goal==c.plan.original_proposal.goal
    assert decision.metrics.maximum_order_notional.amount==4000
    assert decision.metrics.exposure.gross.amount>=D('.08')*D('81490')
    assert decision.metrics.planned_loss.amount>0
    assert decision.metrics.required_assets==(t.Money(currency='USDT',amount=D('4000')),)
    assert decision.source.kind=='POSITION_VALUE_BAND_PROPOSAL' and not decision.execution_authorized
    assert 'UNMET_GOAL_MANAGEMENT_REQUIRED' in decision.obligations


@pytest.mark.parametrize('fault,code',[
    ('order_limit','REPLAN:ORDER_NOTIONAL'),('gross_limit','REPLAN:GROSS'),
    ('capital_limit','REPLAN:CAPITAL'),('spendable','INSUFFICIENT_SPENDABLE:USDT'),
    ('missing_protection','QUOTE_PARTIAL_OR_RETAINED_PROTECTION_MISSING'),
    ('future_clock','EXPIRED_OR_FUTURE_INTENT'),('bad_market','QUOTE_MARKET_HASH_MISMATCH'),
    ('new_risk_disabled','NEW_RISK_DISABLED'),('changed_allocation','QUOTE_ALLOCATION_CHANGED'),
    ('wrong_authority','AUTHORITY_MISMATCH'),('missing_working','QUOTE_WORKING_COVERAGE_INCOMPLETE'),
    ('fee_reserve','QUOTE_FEE_RESERVE_MISMATCH')])
def test_quote_risk_rejects_each_current_policy_or_full_source_gap(fault,code):
    c=quote_risk_case()
    if fault=='order_limit':c.policy=replace(c.policy,maximum_order_notional='3999')
    if fault=='gross_limit':c.policy=replace(c.policy,maximum_gross_exposure='5000')
    if fault=='capital_limit':c.policy=replace(c.policy,maximum_capital='3999')
    if fault=='spendable':c.facts=replace(c.facts,inputs=replace(c.facts.inputs,spendable=()))
    if fault=='missing_protection':c.facts=replace(c.facts,protection=())
    if fault=='future_clock':c.terms=replace(c.terms,created_at='2099-01-01T00:00:00Z')
    if fault=='bad_market':c.facts=replace(c.facts,market=replace(c.facts.market,
        quote=replace(c.facts.market.quote,last=t.Price(currency='USDT',value=D(1)))))
    if fault=='new_risk_disabled':c.facts=replace(c.facts,inputs=replace(c.facts.inputs,new_risk_allowed=False))
    if fault=='changed_allocation':c.allocation=replace(c.allocation,native_quote_amount=t.Money(currency='USDT',amount=D(1)))
    if fault=='wrong_authority':c.facts=replace(c.facts,inputs=replace(c.facts.inputs,ownership_epoch=2))
    if fault=='missing_working':c.facts=replace(c.facts,working_bounds=None)
    if fault=='fee_reserve':c.facts=replace(c.facts,inputs=replace(c.facts.inputs,
        fee_reserve=(t.Money(currency='USDT',amount=D(1)),)))
    decision=assess(c)
    assert decision.verdict!='ALLOW' and decision.metrics is None
    assert code in decision.rejection_codes,decision.rejection_codes


def test_full_quote_assessment_archive_replays_math_and_rejects_forged_allow():
    from trading_system.intents.quote_assessment_archive import QuoteAssessmentArchiveV1,QuoteAssessmentArchiveCodec
    from trading_system.contracts.quote_financial import QuoteFinancialSourceV1
    c=quote_risk_case();decision=assess(c)
    context=c.facts.target_facts.scope_facts.context
    source=QuoteFinancialSourceV1(source_kind='POSITION_VALUE_BAND_PROPOSAL',source_version='2026-10-30',
        source_hash=c.plan.proposal_hash,intake_record_id='synthetic-bounded-intake',intake_hash='a'*64,
        intake_transaction_id='synthetic-intake-transaction',context=context,context_hash=digest(context))
    archive=QuoteAssessmentArchiveV1(contract_version='2026-10-30',record_kind='QUOTE_ASSESSMENT_ARCHIVE_V1',
        intent_id=c.terms.decision_id,source=source,compilation=c.plan,book=c.book,allocation=c.allocation,
        risk_terms=c.terms,risk_facts=c.facts,risk_policy=c.policy,assessment=decision)
    codec=QuoteAssessmentArchiveCodec();payload=codec.encode(archive)
    assert digest(codec.decode(payload))==digest(archive)
    payload['assessment']['metrics']['required_assets'][0]['amount']='1'
    with pytest.raises(ValueError,match='QUOTE_ARCHIVE_DECISION_REPLAY_MISMATCH'):codec.decode(payload)


def test_actual_above_band_partial_reduction_retains_nofill_loss_and_exact_base_fee_obligation():
    from trading_system.risk.program_exposure import FeeBound
    c=quote_risk_case(actual='.1',side='SELL',quote='1000',fees=(
        FeeBound(amount=t.Money(currency='BTC',amount=D('.001')),debit='EXTERNAL_BALANCE'),))
    decision=assess(c)
    assert decision.verdict=='ALLOW',decision.rejection_codes
    assert c.allocation.position.unmet_goal_possible and c.allocation.position.classification=='REDUCE'
    assert decision.metrics.required_assets==(t.Money(currency='BTC',amount=D('.021')),)
    assert decision.metrics.exposure.gross.amount>=D('.1')*D('81490')
    assert decision.metrics.planned_loss.amount>D('4000')
    assert c.allocation.scope_state.actual.value==D('.1')


def test_unknown_original_command_ids_never_get_zeroed_by_interval_risk():
    from trading_system.intents.quote_target_allocation import compile_quote_target
    c=quote_risk_case();target=c.facts.target_facts
    target=replace(target,scope_facts=replace(target.scope_facts,unknown_command_ids=('original-unknown',)))
    c.facts=replace(c.facts,target_facts=target);c.allocation=compile_quote_target(c.plan,c.book,target)
    decision=assess(c)
    assert decision.verdict=='DENY' and 'TARGET_COMMAND_RECONCILIATION_REQUIRED' in decision.rejection_codes
    assert c.facts.target_facts.scope_facts.unknown_command_ids==('original-unknown',)


def test_immutable_native_request_is_never_resized_to_make_risk_allow():
    c=quote_risk_case(quote='5000');original=digest(c.plan)
    decision=assess(c)
    assert decision.verdict=='REPLAN_REQUIRED' and 'REPLAN:POSITION_BAND_UPPER' in decision.rejection_codes
    assert digest(c.plan)==original and c.plan.nodes[0].quantity.value=='5000'


def test_interval_scope_context_cannot_borrow_a_different_authorization():
    from trading_system.intents.quote_target_allocation import compile_quote_target
    c=quote_risk_case();tf=c.facts.target_facts;s=tf.scope_facts
    context=replace(s.context,authorization_id='different-authorization')
    tf=replace(tf,scope_facts=replace(s,context=context,inputs=replace(s.inputs,context=context)))
    c.facts=replace(c.facts,target_facts=tf);c.allocation=compile_quote_target(c.plan,c.book,tf)
    c.facts=replace(c.facts,protection=tuple(replace(p,allocation_hash=digest(c.allocation)) for p in c.facts.protection))
    decision=assess(c)
    assert decision.verdict=='DENY' and 'QUOTE_SCOPE_AUTHORITY_MISMATCH' in decision.rejection_codes
