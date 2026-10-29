"""Purpose-specific bounded EXIT facts; no ledger lock/permission is issued."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_program_risk_assessment import risk_case,assess,rebind,digest,NOW,END
from unit.support_ip_06 import price
from trading_system.contracts import types as t
from trading_system.risk.evaluator import EmergencyReduction
from trading_system.risk.reduction import BoundedReductionProof,ReductionRiskMetrics
from trading_system.risk.terms import RiskMetrics,metrics_completeness


def bounded_case(**kwargs):
    c=risk_case(actual='2',target='1',quantity='1',side='SELL',**kwargs)
    lock=t.RiskLock(lock_id='real-captured-gap',scope=c.book.scope,cause='PROTECTION_GAP',state='ACTIVE',
        permission=t.RiskPermission.EXIT_ONLY,required_evidence=('PROTECTION_OR_EXIT_CONFIRMED',),raised_at=NOW)
    position=replace(c.book.positions[0],risk_locks=(lock,))
    rebind(c,book=replace(c.book,positions=(position,)))
    emergency=EmergencyReduction(maximum_quantity=t.Quantity(value=D(1),unit=c.terms.target.unit),allowed_order_types=('LIMIT',),
        reference_price=price('81490'),maximum_slippage_bps=D(500),maximum_quote_age_ms=10000)
    current=replace(c.facts.inputs,emergency=emergency,new_risk_allowed=False)
    report=t.VenueReport(coverage=c.book.coverage,orders=(),positions=c.book.positions,balances=c.book.balances,
        fills=(),evidence=c.book.evidence,next_cursor=None,as_of=NOW)
    proof=BoundedReductionProof(contract_version='2026-10-29',record_kind='BOUNDED_REDUCTION_PROOF',scope=c.book.scope,
        owner_strategy_id=c.terms.owner_strategy_id,ownership_epoch=c.terms.ownership_epoch,
        authorization_id=c.terms.authorization_id,policy_revision=c.terms.policy_revision,source_hash=c.terms.source.hash,
        terms_hash=digest(c.terms),allocation_hash=digest(c.allocation),book_hash=digest(c.book),
        context_hash=digest(c.facts.target_facts.context),snapshot_ref=c.facts.market.ref,
        collateral_group_id=c.terms.collateral_group_id,group_facts_hash=digest(c.facts.budget_facts),
        position=position,emergency=emergency,locks=(lock,),unresolved_group_commands=(),report=report,
        archive_ref=replace(c.book.evidence[0],id='captured-reduction-report',hash=digest(report)),
        management_duty_refs=c.book.evidence,valid_until=END)
    c.facts=replace(c.facts,inputs=current,entry_slippage_bps=D(500),protection=(),reduction_proof=proof,
        evidence=(*c.facts.evidence,proof.archive_ref))
    return c


def test_pre_authorized_partial_exit_retains_truthful_unprotected_residual_and_lock_duty():
    c=bounded_case();result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert type(result.metrics) is ReductionRiskMetrics and result.metrics.protection_complete is False
    residual=result.metrics.residual
    assert residual.remaining_quantity.value==1 and residual.starting_quantity.value==2
    assert residual.protection_state=='UNPROTECTED' and residual.hard_loss_status=='UNKNOWN' and residual.hard_loss_bound is None
    assert residual.risk_permission==t.RiskPermission.EXIT_ONLY
    assert result.metrics.planned_loss.amount>0
    assert {'PERSIST_RESIDUAL_RISK_LOCK','RETAIN_MANAGE_UNTIL_FLAT'}<=set(result.obligations)
    assert not result.execution_authorized


def test_unprotected_partial_without_typed_emergency_proof_is_not_ordinary_complete_metrics():
    c=bounded_case();c.facts=replace(c.facts,reduction_proof=None)
    result=assess(c)
    assert result.verdict=='DENY' and result.metrics is None


@pytest.mark.parametrize('fault',['book','owner','context','source','quantity','types','expired','unknown_lock','unresolved_group','new_risk'])
def test_bounded_exit_exception_cannot_change_owner_data_permission_or_direction(fault):
    c=bounded_case();proof=c.facts.reduction_proof
    if fault=='book':proof=replace(proof,book_hash='0'*64)
    if fault=='owner':proof=replace(proof,ownership_epoch=2)
    if fault=='context':proof=replace(proof,context_hash='0'*64)
    if fault=='source':proof=replace(proof,source_hash='0'*64)
    if fault=='quantity':proof=replace(proof,emergency=replace(proof.emergency,maximum_quantity=t.Quantity(value=D('.5'),unit='BASE')))
    if fault=='types':proof=replace(proof,emergency=replace(proof.emergency,allowed_order_types=('MARKET',)))
    if fault=='expired':proof=replace(proof,valid_until=NOW)
    if fault=='unknown_lock':proof=replace(proof,locks=(replace(proof.locks[0],cause='ORDER_OUTCOME_UNKNOWN',permission=t.RiskPermission.RECOVERY_REQUIRED),))
    if fault=='unresolved_group':proof=replace(proof,unresolved_group_commands=('unknown-original-id',))
    if fault=='new_risk':c.terms=replace(c.terms,target=t.Quantity(value=D(3),unit='BASE'))
    c.facts=replace(c.facts,reduction_proof=proof)
    result=assess(c)
    assert result.verdict=='DENY' and result.metrics is None


def test_unprotected_residual_without_a_stop_is_explicit_unknown_not_zero_loss():
    c=bounded_case(missing_stop=True);result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert type(result.metrics) is ReductionRiskMetrics
    assert result.metrics.planned_loss is None and result.metrics.residual.intended_stop_loss is None
    assert result.metrics.residual.hard_loss_status=='UNKNOWN' and result.metrics.protection_complete is False
    assert not metrics_completeness(result.metrics)


def test_unknown_or_false_protection_does_not_become_ordinary_complete_risk_metrics():
    from dataclasses import fields
    c=bounded_case();result=assess(c)
    base=RiskMetrics(**{f.name:getattr(result.metrics,f.name) for f in fields(RiskMetrics)})
    assert 'RISK_METRIC_MISSING:PROTECTION' in metrics_completeness(base)
    assert not hasattr(base,'metric_kind')


def test_reduction_subtype_roundtrip_keeps_truthful_residual_and_unknowns():
    from trading_system.storage.models import encode,decode
    c=bounded_case(missing_stop=True);m=assess(c).metrics
    restored=decode(ReductionRiskMetrics,encode(m))
    assert type(restored) is ReductionRiskMetrics and restored==m
    assert restored.protection_complete is False and restored.residual.hard_loss_bound is None


def test_partial_unknown_native_outcome_cannot_be_relabeled_as_protection_failure():
    c=bounded_case()
    lock=replace(c.facts.reduction_proof.locks[0],cause='ORDER_OUTCOME_UNKNOWN',permission=t.RiskPermission.RECOVERY_REQUIRED)
    position=replace(c.book.positions[0],risk_locks=(lock,))
    c.book=replace(c.book,positions=(position,));rebind(c)
    # Exact updated data hashes still cannot turn RECOVERY_REQUIRED into EXIT.
    proof=replace(c.facts.reduction_proof,position=position,locks=(lock,),book_hash=digest(c.book),
        allocation_hash=digest(c.allocation),report=replace(c.facts.reduction_proof.report,positions=(position,)))
    proof=replace(proof,archive_ref=replace(proof.archive_ref,hash=digest(proof.report)))
    c.facts=replace(c.facts,reduction_proof=proof,evidence=(*c.book.evidence,proof.archive_ref))
    result=assess(c)
    assert result.verdict=='DENY' and 'BOUNDED_REDUCTION_RECOVERY_OR_UNKNOWN_LOCK' in result.rejection_codes


def test_invalid_tick_cannot_be_hidden_by_an_emergency_reduction_proof():
    result=assess(bounded_case(stop='81330.005'))
    assert result.verdict=='DENY' and 'INVALID_STOP' in result.rejection_codes


def test_futures_emergency_exit_must_carry_the_existing_reduce_only_wire_guard():
    result=assess(bounded_case(product=t.Product.USD_M_PERP))
    assert result.verdict=='DENY' and 'BOUNDED_REDUCTION_REDUCE_ONLY_REQUIRED' in result.rejection_codes
    result=assess(bounded_case(product=t.Product.USD_M_PERP,reduce_only=True))
    assert result.verdict=='ALLOW',result.rejection_codes
    assert type(result.metrics) is ReductionRiskMetrics and result.metrics.protection_complete is False


def test_conditional_order_is_not_an_existing_immediate_emergency_exit_permission():
    c=bounded_case(conditional=True)
    emergency=replace(c.facts.inputs.emergency,allowed_order_types=('STOP_LOSS',))
    proof=replace(c.facts.reduction_proof,emergency=emergency)
    c.facts=replace(c.facts,inputs=replace(c.facts.inputs,emergency=emergency),reduction_proof=proof)
    result=assess(c)
    assert result.verdict=='DENY' and 'BOUNDED_REDUCTION_ORDER_NOT_AUTHORIZED' in result.rejection_codes


def test_cause_label_cannot_erase_its_required_recovery_evidence():
    c=bounded_case();proof=c.facts.reduction_proof
    lock=replace(proof.locks[0],required_evidence=());position=replace(c.book.positions[0],risk_locks=(lock,))
    rebind(c,book=replace(c.book,positions=(position,)))
    report=replace(proof.report,positions=(position,));ref=replace(proof.archive_ref,hash=digest(report))
    proof=replace(proof,locks=(lock,),position=position,report=report,archive_ref=ref,
        book_hash=digest(c.book),allocation_hash=digest(c.allocation))
    c.facts=replace(c.facts,reduction_proof=proof,evidence=(*c.book.evidence,ref))
    result=assess(c)
    assert result.verdict=='DENY' and 'BOUNDED_REDUCTION_RECOVERY_OR_UNKNOWN_LOCK' in result.rejection_codes
