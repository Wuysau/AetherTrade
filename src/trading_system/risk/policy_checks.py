"""Shared deterministic policy predicates; calculations are never authority.

These primitives run in the legacy evaluator at its original semantic stages.
A future program adapter must first prove RiskTerms/complete RiskMetrics, not
interpret an empty tuple of local violations as financial admission.
"""
from contextlib import contextmanager
from decimal import Decimal,localcontext,Inexact,Rounded,ROUND_CEILING
from trading_system.contracts.canonical import decimal_string
from .valuation import nonnegative

D=Decimal


@contextmanager
def exact_risk_arithmetic():
    with localcontext() as ctx:
        ctx.prec=256
        ctx.traps[Inexact]=ctx.traps[Rounded]=True
        yield


def legacy_margin_upper_bound(notional,leverage):
    """Bound a rational margin upward, never silently round a reserve down.

Keep the legacy80-significant-digit monetary representation where exact. A
nonterminating quotient needs an explicit upper bound, not an exactness claim.
Only this directed rounding disables inexact traps; quantity sizing is separate.
"""
    with localcontext() as ctx:
        ctx.prec=80
        ctx.rounding=ROUND_CEILING
        ctx.traps[Inexact]=ctx.traps[Rounded]=False
        result=notional/leverage
    with exact_risk_arithmetic():
        if result*leverage<notional:raise ValueError('MARGIN_UPPER_BOUND_INVALID')
    return result


def account_policy_codes(*,product,venue_leg,target,leverage,multiplier,reducing,policy,
                         authorized_margin_mode,observed_margin_mode,authorized_position_mode,observed_position_mode):
    codes=[]
    if product!='USD_M_PERP' and (target<0 or leverage!=1 or multiplier!=1):codes.append('CASH_ONLY')
    if type(leverage) is not int or leverage<1:codes.append('INVALID_LEVERAGE')
    elif not reducing and leverage>policy.maximum_leverage:codes.append('REPLAN:LEVERAGE_LIMIT')
    if not reducing and product=='USD_M_PERP' and (authorized_margin_mode not in ('ISOLATED','CROSS')
        or authorized_position_mode not in ('ONE_WAY','HEDGE') or authorized_margin_mode!=observed_margin_mode
        or authorized_position_mode!=observed_position_mode):codes.append('ACCOUNT_MODE_MISMATCH')
    if not reducing and product=='USD_M_PERP' and ((observed_position_mode=='ONE_WAY' and venue_leg!='BOTH')
        or (observed_position_mode=='HEDGE' and venue_leg=='BOTH')):codes.append('POSITION_MODE_LEG_MISMATCH')
    return tuple(codes)


def protection_policy_codes(*,product,protection_policy,policy):
    codes=[]
    if protection_policy!=policy.protection_policy:codes.append('PROTECTION_POLICY_MISMATCH')
    if product=='USD_M_PERP' and protection_policy!='NATIVE_REQUIRED':codes.append('NATIVE_PROTECTION_REQUIRED')
    return tuple(codes)


def portfolio_limit_codes(*,notional,loss,requested_loss,exposure,existing_loss,group_limits,
                          position_count,active_order_count,policy):
    with exact_risk_arithmetic():
        codes=[]
        limits=((notional,policy.maximum_order_notional,'ORDER_NOTIONAL'),
            (loss,policy.maximum_planned_loss_per_intent,'INTENT_LOSS'),(loss,requested_loss,'REQUESTED_LOSS'),
            (exposure.gross.amount,policy.maximum_gross_exposure,'GROSS'),
            (abs(exposure.net.amount),policy.maximum_net_exposure,'NET'),
            (loss+existing_loss,policy.maximum_total_planned_loss,'TOTAL_LOSS'))
        for value,limit,name in limits:
            if value>nonnegative(decimal_string(limit)):codes.append('REPLAN:'+name)
        groups={group.group_id:group.maximum_gross for group in group_limits}
        if len(groups)!=len(group_limits):codes.append('DUPLICATE_GROUP_LIMIT')
        for group in exposure.groups:
            bound=groups.get(group.group_id)
            if bound is None or bound.currency!=policy.valuation_currency:codes.append('GROUP_LIMIT_MISSING')
            elif group.gross.amount>nonnegative(bound.amount):codes.append('REPLAN:RELATED_GROUP')
        if position_count>policy.maximum_open_positions or (active_order_count is not None and active_order_count>policy.maximum_active_orders):
            codes.append('REPLAN:COUNT_LIMIT')
        return tuple(codes)


def spendable_codes(*,required_assets,spendable,book):
    with exact_risk_arithmetic():
        codes=[]
        available={m.currency:nonnegative(m.amount) for m in spendable}
        balances={m.currency:nonnegative(m.amount) for m in book.balances}
        if len(available)!=len(spendable) or len(balances)!=len(book.balances):codes.append('AMBIGUOUS_BALANCES')
        for projection in book.reservations:
            if projection.reservation.state=='RELEASED':continue
            asset=projection.reservation.asset
            held,reflected,pending=(nonnegative(v) for v in (projection.held,projection.reflected,projection.release_pending))
            if reflected>held:codes.append('INVALID_RESERVATION')
            available[asset]=available.get(asset,D(0))-max(held-reflected,pending)
        for asset,amount in required_assets.items():
            if amount>min(available.get(asset,D(0)),balances.get(asset,D(0))):codes.append('INSUFFICIENT_SPENDABLE:'+asset)
        return tuple(codes)


def capital_limit_exceeded(*,capital,fees,funding,capital_in_use,policy):
    with exact_risk_arithmetic():
        return capital+fees+funding+nonnegative(capital_in_use)+nonnegative(decimal_string(policy.cash_buffer))>nonnegative(decimal_string(policy.maximum_capital))
