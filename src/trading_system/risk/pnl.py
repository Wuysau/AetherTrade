"""Cashflow-adjusted equity arithmetic; no inference of missing cost history."""
from decimal import Decimal, localcontext

from trading_system.contracts.canonical import utc_timestamp


def exact_sum(values):
    values = tuple(values)
    if not values:
        return Decimal(0)
    with localcontext() as ctx:
        ctx.prec = max(v.adjusted() for v in values) - min(v.as_tuple().exponent for v in values) + len(str(len(values))) + 3
        return sum(values, Decimal(0))


def adjusted_equities(series, cashflows):
    """Return adjusted equity at every point and completeness issues.

    Inputs are one portfolio, one currency, one environment, over (start,end].
    Equity already includes fees/funding/dividends: these are not subtracted twice.
    Every INTERNAL pair must contain both equal/opposite legs in this window.
    """
    if len(series) < 2:
        raise ValueError('at least two equity points required')
    times = [utc_timestamp(p.at) for p in series]
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError('equity points must be strictly chronological')
    currency = series[0].equity.currency
    environment = series[0].evidence.environment
    if any(p.equity.currency != currency or p.evidence.environment != environment for p in series):
        raise ValueError('mixed currency or environment')
    seen = set()
    internal = {}
    external = []
    missing = []
    for flow in cashflows:
        at = utc_timestamp(flow.at)
        if not times[0] < at <= times[-1]:
            raise ValueError('cashflow outside (start,end]')
        if flow.amount.currency != currency or flow.evidence.environment != environment:
            raise ValueError('cashflow currency or environment mismatch')
        identity = flow.evidence.id
        if not identity or identity in seen:
            raise ValueError('cashflow requires unique evidence identity per leg')
        seen.add(identity)
        if flow.classification == 'EXTERNAL':
            external.append((at, flow.amount.amount))
        elif flow.classification == 'INTERNAL':
            if not flow.internal_pair_id:
                missing.append('INTERNAL_PAIR_ID_MISSING')
            else:
                internal.setdefault(flow.internal_pair_id, []).append((at, flow.amount.amount))
        elif flow.classification not in {'FEE', 'FUNDING', 'DIVIDEND'}:
            raise ValueError('unsupported cashflow classification')
    for pair, legs in internal.items():
        if len(legs) != 2 or exact_sum(amount for _, amount in legs) != 0:
            missing.append('INTERNAL_PAIR_INCOMPLETE:' + pair)
        elif any(exact_sum(amount for at, amount in legs if at <= time) != 0 for time in times):
            missing.append('INTERNAL_TRANSFER_IN_TRANSIT:' + pair)
    adjusted = tuple(exact_sum([point.equity.amount] + [amount.copy_negate() for at, amount in external if at <= time])
                     for point, time in zip(series, times))
    return adjusted, tuple(sorted(set(missing)))
