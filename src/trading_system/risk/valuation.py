"""Offline valuation. Converted value is never spendable currency."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, localcontext

from trading_system.contracts.types import Money, Quantity

D = Decimal


def nonnegative(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError('NONNEGATIVE_DECIMAL_REQUIRED')
    return value


@dataclass(frozen=True, kw_only=True)
class FxRate:
    source: str
    target: str
    asset_rate: Decimal
    liability_rate: Decimal
    haircut: Decimal
    evidence_id: str

    def __post_init__(self):
        for value in (self.asset_rate, self.liability_rate, self.haircut):
            nonnegative(value)
        if (not self.evidence_id or not self.source or not self.target
                or self.asset_rate <= 0 or self.liability_rate < self.asset_rate
                or self.haircut >= 1):
            raise ValueError('INVALID_FX_EVIDENCE')


class Valuation:
    def __init__(self, currency: str, rates: tuple[FxRate, ...] = ()):
        self.currency = currency
        self.rates = rates
        if len({(r.source, r.target) for r in rates}) != len(rates):
            raise ValueError('DUPLICATE_FX')

    def convert(self, money: Money, *, liability: bool = False) -> Money:
        with localcontext() as ctx:
            ctx.prec = 80
            if money.currency == self.currency:
                return money
            rate = next((r for r in self.rates
                         if r.source == money.currency and r.target == self.currency), None)
            if rate is None:
                raise ValueError('FX_MISSING:' + money.currency)
            factor = (rate.liability_rate if liability or money.amount < 0
                      else rate.asset_rate * (1 - rate.haircut))
            return Money(amount=money.amount * factor, currency=self.currency)


@dataclass(frozen=True, kw_only=True)
class QuantityAdjustment:
    original: Quantity
    permitted: Quantity


def floor_quantity(quantity: Quantity, step: Quantity) -> QuantityAdjustment:
    nonnegative(quantity.value)
    if quantity.unit != step.unit or nonnegative(step.value) == 0:
        raise ValueError('INVALID_QUANTITY_STEP')
    with localcontext() as ctx:
        ctx.prec = 80
        value = (quantity.value / step.value).to_integral_value(rounding=ROUND_DOWN) * step.value
    return QuantityAdjustment(original=quantity, permitted=Quantity(value=value, unit=quantity.unit))
