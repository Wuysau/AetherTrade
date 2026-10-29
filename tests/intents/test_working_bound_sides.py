"""Per-order sides cannot be swapped behind an unchanged aggregate working net."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from test_quote_risk_assessment import quote_risk_case
from test_program_risk_assessment import risk_case
from test_program_target_allocation import order
from trading_system.contracts import types as t
from trading_system.risk.exposure import WorkingOrder
from trading_system.risk.program_assessment import WorkingRiskBound
from trading_system.risk.valuation import Valuation


@pytest.mark.parametrize('adapter',['quote','program'])
@pytest.mark.parametrize('foreign',[False,True])
def test_individual_bound_side_must_match_current_order_side_even_when_pair_net_is_unchanged(adapter,foreign):
    c=quote_risk_case() if adapter=='quote' else risk_case()
    scope=c.book.scope;other=replace(scope,instrument_uid='other-scope') if foreign else scope
    buy=order(scope,'1',unit='BASE');sell=order(other,'1',unit='BASE')
    buy=replace(buy,order=replace(buy.order,identity=replace(buy.order.identity,logical_client_order_id='true-buy')))
    sell=replace(sell,order=replace(sell.order,identity=replace(sell.order.identity,logical_client_order_id='true-sell')))
    book=replace(c.book,active_orders=(buy,sell))
    refs=(book.evidence[0].id,)
    bounds=tuple(WorkingRiskBound(logical_client_order_id=identifier,scope=s,side=side,
        quantity=t.Quantity(unit='BASE',value=D(1)),minimum_price=t.Price(currency='USDT',value=D(50000)),
        maximum_price=t.Price(currency='USDT',value=D(price)),evidence_refs=refs)
        for identifier,s,side,price in [('true-buy',scope,'SELL',90000),('true-sell',other,'BUY',80000)])
    inputs=replace(c.facts.inputs,order_sides=(WorkingOrder(logical_client_order_id='true-buy',side='BUY'),
        WorkingOrder(logical_client_order_id='true-sell',side='SELL')))
    if foreign:inputs=replace(inputs,marks=inputs.marks+(replace(inputs.marks[0],scope=other),))
    facts=replace(c.facts,inputs=inputs,working_bounds=bounds)
    if adapter=='quote':
        from trading_system.risk.quote_assessment import _portfolio
        allocation=replace(c.allocation,scope_state=replace(c.allocation.scope_state,
            working=t.Quantity(unit='BASE',value=D(1 if foreign else 0))))
        def run():return _portfolio(inputs,c.terms,allocation,book,facts,Valuation('USDT',inputs.fx_rates))
    else:
        from trading_system.risk.program_assessment import _portfolio
        delta=replace(c.allocation.delta,working=t.Quantity(unit='BASE',value=D(1 if foreign else 0)),
            target=t.Quantity(unit='BASE',value=D(2 if foreign else 1)))
        allocation=replace(c.allocation,delta=delta)
        indexed={b.node_id:b for b in facts.node_bounds}
        def run():return _portfolio(inputs,c.terms,c.plan,allocation,c.exposure,book,facts,indexed,Valuation('USDT',inputs.fx_rates))
    with pytest.raises(ValueError,match='WORKING_BOUND_MISMATCH'):run()
