"""Fixed legacy cases for full decision/hash parity across a core extraction."""
from dataclasses import replace
from trading_system.contracts import types as t
from unit.support_ip_06 import make_case,money

CASES=('perp','spot','equity','reduce','reduce_without_emergency','order_limit','loss_limit',
       'gross_limit','net_limit','capital_limit','count_limit','leverage_limit','mode_mismatch',
       'wrong_scope','stale_basis','expired','missing_stop','negative_fee','unknown_clock','compound')

def legacy_case(name):
    product={'spot':t.Product.SPOT,'equity':t.Product.US_EQUITY}.get(name,t.Product.USD_M_PERP)
    c=make_case(product)
    if name in ('reduce','reduce_without_emergency'):c.exiting(emergency=name=='reduce')
    policies={'order_limit':('maximum_order_notional','199'),'loss_limit':('maximum_planned_loss_per_intent','23'),
        'gross_limit':('maximum_gross_exposure','199'),'net_limit':('maximum_net_exposure','199'),
        'capital_limit':('maximum_capital','202'),'count_limit':('maximum_active_orders',0)}
    if name in policies:
        field,value=policies[name];c.policy=replace(c.policy,**{field:value})
    if name=='leverage_limit':c.intent=replace(c.intent,requested_leverage=6)
    if name=='mode_mismatch':c.inputs=replace(c.inputs,observed_margin_mode='CROSS')
    if name=='wrong_scope':c.intent=replace(c.intent,venue_account_id='other')
    if name=='stale_basis':c.intent=replace(c.intent,basis_fingerprint='changed')
    if name=='expired':c.intent=replace(c.intent,valid_until='2026-10-22T11:00:00Z')
    if name=='missing_stop':c.intent=replace(c.intent,exit_plan=replace(c.intent.exit_plan,stop_price=None))
    if name=='negative_fee':c.inputs=replace(c.inputs,fee_reserve=(money('-1'),))
    if name=='unknown_clock':c.inputs=replace(c.inputs,clock=replace(c.inputs.clock,clock_health='UNKNOWN'))
    if name=='compound':
        c.policy=replace(c.policy,maximum_order_notional='1',maximum_planned_loss_per_intent='1',
            maximum_total_planned_loss='1',maximum_gross_exposure='1',maximum_net_exposure='1',
            maximum_capital='1',maximum_open_positions=0,maximum_active_orders=0,maximum_leverage=1)
        c.inputs=replace(c.inputs,new_risk_allowed=False,observed_margin_mode='CROSS')
    return c
