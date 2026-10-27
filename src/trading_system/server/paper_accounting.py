"""Derived cash-PAPER realized PnL; original fills remain the source of truth.

Input is a chronological, complete long-only scope's synthetic fills. The fixed
0.1% synthetic fee matches the console's cash ledger. Rational arithmetic keeps
allocation exact across partial exits and independent of ambient Decimal context.
This does not price external fees, corporate actions, shorts or derivatives.
"""
from decimal import Decimal, localcontext
from fractions import Fraction


def realized_by_order(fills, *, fee_rate=Decimal('0.001')) -> dict[str, Decimal]:
    if not isinstance(fee_rate, Decimal) or not fee_rate.is_finite() or fee_rate < 0:
        raise ValueError('finite nonnegative synthetic fee required')
    held, cost, entry_fees = Fraction(0), Fraction(0), Fraction(0)
    fee = Fraction(fee_rate)
    realized = {}
    for fill in fills:
        side, quantity, price, key = (fill['side'], fill['quantity'], fill['price'], fill['command_id'])
        if (side not in ('BUY', 'SELL') or not key or any(not isinstance(value, Decimal)
                or not value.is_finite() or value <= 0 for value in (quantity, price))):
            raise ValueError('positive, identified synthetic cash fills required')
        q, p = Fraction(quantity), Fraction(price)
        notional = q * p
        if side == 'BUY':
            held += q
            cost += notional
            entry_fees += notional * fee
        else:
            if q > held:
                raise ValueError('uncovered sell cannot establish realized cash PnL')
            allocated_cost, allocated_fee = cost * q / held, entry_fees * q / held
            realized[key] = realized.get(key, Fraction(0)) + notional - allocated_cost - allocated_fee - notional * fee
            held -= q
            cost -= allocated_cost
            entry_fees -= allocated_fee
    result = {}
    for key, value in realized.items():
        with localcontext() as context:
            context.prec = max(80, len(str(abs(value.numerator))) + len(str(value.denominator)) + 8)
            result[key] = Decimal(value.numerator) / Decimal(value.denominator)
    return result
