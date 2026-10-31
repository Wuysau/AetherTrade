"""Exact Spot weighted-average economic cost, version SPOT_WAC_RATIONAL_V1.

Inputs are net asset deltas, already fee-adjusted by the authentic source reader.
No ledger cashflow is posted here. Rational amounts retain every division residue.
"""
from dataclasses import dataclass
from fractions import Fraction
from decimal import Decimal
from trading_system.storage.models import MissingEvidence

POLICY='SPOT_WAC_RATIONAL_V1'

@dataclass(frozen=True)
class SpotCost:
    quantity:Fraction=Fraction(0)
    cost:Fraction=Fraction(0)
    realized:Fraction=Fraction(0)


def apply_spot_cost(state,*,side,base_delta,quote_delta,external_fee=False):
    if external_fee:raise MissingEvidence('COST_FEE_FX_REQUIRED')
    base,quote=Fraction(base_delta),Fraction(quote_delta)
    if side=='BUY':
        if base<=0 or quote>0:raise MissingEvidence('COST_BUY_ECONOMICS_REQUIRED')
        return SpotCost(state.quantity+base,state.cost-quote,state.realized)
    if side!='SELL' or base>=0 or quote<0 or -base>state.quantity:
        raise MissingEvidence('COST_SELL_ECONOMICS_REQUIRED')
    released=state.cost*(-base)/state.quantity
    return SpotCost(state.quantity+base,state.cost-released,state.realized+quote-released)


def exact_decimal(value):
    """Return an exact finite Decimal or None; never round a recurring ratio."""
    denominator=value.denominator;twos=fives=0
    while denominator%2==0:twos+=1;denominator//=2
    while denominator%5==0:fives+=1;denominator//=5
    if denominator!=1:return None
    scale=max(twos,fives)
    integer=value.numerator*2**(scale-twos)*5**(scale-fives)
    return Decimal((int(integer<0),tuple(map(int,str(abs(integer)))),-scale))
