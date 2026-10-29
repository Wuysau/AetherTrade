"""Unmet goals retain old risk; they never authorize new offsetting exposure."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from unit.support_ip_09 import make_case,position,order
from trading_system.contracts import types as t
from trading_system.contracts.quote_position_v1 import AbsolutePositionValueBandV1,PositionValueBasisV1
from trading_system.intents.target_compiler import TargetCompiler,TargetScopeTerms
from trading_system.risk.exposure import WorkingOrder


def assess(*,actual='.1',side='SELL',lower='-.02',upper='0',product=t.Product.SPOT,goal_side='LONG',
           minimum='3500',maximum='4500',working='0',working_side='BUY'):
    from trading_system.intents.position_value_band import assess_position_value_band
    c=make_case(product);scope=c.book.scope;old=c.bind();unit=c.target.instrument.quantity_step.unit
    goal=AbsolutePositionValueBandV1(kind='ABSOLUTE_POSITION_VALUE_BAND',side=goal_side,
        minimum_quote=t.Money(currency='USDT',amount=D(minimum)),maximum_quote=t.Money(currency='USDT',amount=D(maximum)),
        valuation_basis=PositionValueBasisV1(kind='DECLARED_REFERENCE_PRICE',
            reference_price=t.Price(currency='USDT',value=D('50000')),notional_multiplier=D(1)))
    book=replace(c.book,positions=(position(scope,actual,unit),) if D(actual) else (),
        active_orders=(order(scope,working,unit=unit,certainty='UNKNOWN'),) if D(working) else ())
    inputs=replace(c.target,order_sides=(WorkingOrder(logical_client_order_id='order',side=working_side),) if D(working) else ())
    terms=TargetScopeTerms(scope=scope,venue=old.venue,owner_strategy_id=old.owner_strategy_id,
        ownership_epoch=old.ownership_epoch,collateral_group_id=old.collateral_group_id,
        basis_fingerprint=old.basis_fingerprint,target_side=goal_side,unit=unit,
        valid_until=old.valid_until,submission_deadline=old.execution.entry_deadline)
    state=TargetCompiler(inputs).read_scope_terms(terms,book)
    return assess_position_value_band(goal,state,scope=scope,request_side=side,
        delta_minimum=t.Quantity(unit=unit,value=D(lower)),delta_maximum=t.Quantity(unit=unit,value=D(upper)))


@pytest.mark.parametrize('product,actual,side,lower,upper,goal_side',[
    (t.Product.SPOT,'.1','SELL','-.02','0','LONG'),
    (t.Product.USD_M_PERP,'-.1','BUY','0','.02','SHORT')])
def test_reduction_retains_no_fill_old_exposure_above_desired_band(product,actual,side,lower,upper,goal_side):
    result=assess(product=product,actual=actual,side=side,lower=lower,upper=upper,goal_side=goal_side)
    assert not result.blockers and result.classification=='REDUCE'
    assert result.unmet_goal_possible and result.current_value.amount==D('5000')
    assert not result.execution_authorized


def test_unknown_buy_upper_cannot_authorize_another_buy_outside_the_goal_band():
    result=assess(actual='0',side='BUY',lower='0',upper='.001',working='.1')
    assert 'REPLAN:POSITION_BAND_UPPER' in result.blockers


def test_prior_sell_cannot_be_netted_to_authorize_a_buy_before_it_fills():
    result=assess(actual='.1',side='BUY',lower='0',upper='.02',working='.08',working_side='SELL',maximum='5000')
    assert 'REPLAN:POSITION_BAND_UPPER' in result.blockers


@pytest.mark.parametrize('values',[
    dict(lower='-.11'),dict(lower='-.1',working='.001',working_side='SELL'),
    dict(actual='.0000005',lower='-.000001',goal_side='FLAT',minimum='0',maximum='0')])
def test_reduction_cannot_oversell_reverse_or_erase_exact_owned_dust(values):
    assert 'REPLAN:POSITION_REVERSAL_OR_OVERSELL' in assess(**values).blockers


def test_buy_external_base_fee_cannot_borrow_a_future_receipt_to_cover_negative_endpoint():
    result=assess(actual='0',side='BUY',lower='-.001',upper='.08')
    assert 'REPLAN:POSITION_REVERSAL_OR_OVERSELL' in result.blockers


def test_unconditional_buy_interval_can_fit_upper_band_while_goal_is_still_unmet():
    result=assess(actual='0',side='BUY',lower='0',upper='.08')
    assert not result.blockers and result.classification=='INCREASE' and result.unmet_goal_possible


def test_sell_cannot_claim_a_positive_position_delta():
    with pytest.raises(ValueError):assess(side='SELL',lower='-.02',upper='.001')
