"""Continuation facts do not overwrite old post-full-fill planned metrics."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from unit.test_assessment_archive import actual_archive
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload


def derive(archive):
    from trading_system.risk.program_continuation import derive_program_continuation
    return derive_program_continuation(archive)


def test_real_partial_request_retains_exact_nofill_range_separate_from_planned_residual_loss():
    a=actual_archive(actual='2',target='1',quantity='1',side='SELL')
    original=canonical_hash(domain_payload(a));r=derive(a)
    assert r.actual_quantity.value==2 and r.minimum_possible_quantity.value==1 and r.maximum_possible_quantity.value==2
    assert a.assessment.metrics.planned_loss.amount==D('16458')
    assert r.no_fill_stop_loss_estimate.amount==D('32916')
    assert r.hard_loss_bound is None and r.hard_loss_status=='UNKNOWN'
    assert canonical_hash(domain_payload(a))==original and not r.execution_authorized


def test_flat_intent_is_not_an_observed_flat_position_or_fee_only_outstanding_risk():
    a=actual_archive(actual='1',target='0',quantity='1',side='SELL')
    r=derive(a)
    assert a.assessment.metrics.planned_loss.amount==0
    assert r.actual_quantity.value==1 and r.minimum_possible_quantity.value==0 and r.maximum_possible_quantity.value==1
    assert r.no_fill_stop_loss_estimate.amount==D('16458')
    assert 'MANAGE_UNTIL_OBSERVED_FLAT_OR_AUTHORIZED_TRANSFER' in r.required_duties


def test_short_exit_has_symmetric_possible_quantity_and_positive_stop_estimate():
    a=actual_archive(product=t.Product.USD_M_PERP,actual='-2',target='1',quantity='1',side='BUY',
        target_side='SHORT',stop='92000',reduce_only=True)
    r=derive(a)
    assert r.minimum_possible_quantity.value==-2 and r.maximum_possible_quantity.value==-1
    assert r.no_fill_stop_loss_estimate.amount==D('53616')


def test_missing_current_protection_is_unknown_not_zero_or_authority():
    a=actual_archive(actual='1',target='0',quantity='1',side='SELL',missing_stop=True)
    r=derive(a)
    assert r.protection_state=='UNKNOWN' and r.no_fill_stop_loss_estimate is None
    assert 'CURRENT_PROTECTION_OR_BOUNDED_EXIT_REVIEW_REQUIRED' in r.required_duties


def test_quote_continuation_reuses_actual_interval_without_a_fake_scalar_target():
    from unit.support_program_financial import financial_case
    financial_case()
    from test_quote_financial_records import archive_case
    r=derive(archive_case())
    assert r.actual_quantity.value==0 and r.minimum_possible_quantity.value==0 and r.maximum_possible_quantity.value==D('.08')
    assert r.goal_kind=='ABSOLUTE_POSITION_VALUE_BAND' and 'UNMET_GOAL_REVIEW_REQUIRED' in r.required_duties


def test_sub_lot_owned_inventory_is_preserved_exactly_even_when_no_order_can_trade_it():
    from trading_system.risk.program_continuation import derive_position_lot_residual
    qty=t.Quantity(unit='BASE',value=D('.0000001'));step=t.Quantity(unit='BASE',value=D('.000001'))
    executable,residual=derive_position_lot_residual(qty,step)
    assert executable.value==0 and residual==qty and qty.value!=0


def test_changed_archived_position_cannot_be_hidden_behind_an_allocation_actual():
    a=actual_archive(actual='2',target='1',quantity='1',side='SELL')
    p=a.book.positions[0];a=replace(a,book=replace(a.book,positions=(replace(p,actual_quantity=replace(p.actual_quantity,value=D('3'))),)))
    with pytest.raises(ValueError,match='CONTINUATION_CURRENT_BOOK_CHANGED'):derive(a)


@pytest.mark.parametrize('fault',['off_tick','hosted_not_allowed','wrong_trigger'])
def test_a_flat_goal_does_not_make_invalid_retained_protection_genuine(fault):
    a=actual_archive(actual='1',target='0',quantity='1',side='SELL')
    proof=a.risk_facts.protection[0]
    if fault=='off_tick':proof=replace(proof,stop_price=replace(proof.stop_price,value=D('81330.005')))
    if fault=='hosted_not_allowed':proof=replace(proof,origin='HOSTED_CASH_DUTY')
    if fault=='wrong_trigger':proof=replace(proof,stop_trigger='MARK')
    r=derive(replace(a,risk_facts=replace(a.risk_facts,protection=(proof,))))
    assert r.protection_state=='UNKNOWN' and r.no_fill_stop_loss_estimate is None
