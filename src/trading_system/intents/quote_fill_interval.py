"""Exact conservative quote-fill enclosure, never a price guarantee or approval.

The caller must independently establish the gross quote and execution-price
ceilings. This arithmetic helper grants no authority to trust those inputs and
does not alter scalar target allocation, requests, financial records or risk.
"""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.program_financial import require


def _number(value,*,positive):
    require(type(value) is Decimal and value.is_finite()
        and (value>0 if positive else value>=0),'QUOTE_INTERVAL_NUMBER_INVALID')
    # Bound work before converting a decimal exponent into an exact integer.
    # This is a pure arithmetic envelope, not an exchange quantity restriction.
    require(len(value.as_tuple().digits)<=2048 and abs(value.as_tuple().exponent)<=2048,
        'QUOTE_INTERVAL_ARITHMETIC_BUDGET_EXCEEDED')


def _grid_decimal(lots,step):
    """Integer times Decimal with no ambient context rounding."""
    parts=step.as_tuple()
    coefficient=int(''.join(map(str,parts.digits)))
    digits=tuple(map(int,str(lots*coefficient)))
    return Decimal((0,digits,parts.exponent))


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteFillIntervalV1:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['QUOTE_FILL_INTERVAL_V1']
    minimum:t.Quantity
    maximum:t.Quantity
    gross_quote:t.Money
    minimum_execution_price:t.Price
    maximum_execution_price:t.Price
    quantity_step:t.Quantity
    notional_multiplier:Decimal

    @property
    def execution_authorized(self)->Literal[False]:return False

    @property
    def fully_spent_minimum(self)->None:
        # No full-spend, minimum-liquidity or lot-residual assumption is made.
        return None


def compile_quote_fill_interval(*,maximum_gross_quote,minimum_price,maximum_price,
                                quantity_step,notional_multiplier):
    require(type(maximum_gross_quote) is t.Money and type(minimum_price) is t.Price
        and type(maximum_price) is t.Price and type(quantity_step) is t.Quantity,
        'QUOTE_INTERVAL_TYPED_UNITS_REQUIRED')
    _number(maximum_gross_quote.amount,positive=False)
    for value in (minimum_price.value,maximum_price.value,quantity_step.value,notional_multiplier):
        _number(value,positive=True)
    require(type(maximum_gross_quote.currency) is str and bool(maximum_gross_quote.currency)
        and maximum_gross_quote.currency==minimum_price.currency==maximum_price.currency,
        'QUOTE_INTERVAL_CURRENCY_MISMATCH')
    require(minimum_price.value<=maximum_price.value,'QUOTE_INTERVAL_PRICE_RANGE_INVALID')
    require(quantity_step.unit in ('BASE','CONTRACT','SHARE'),'QUOTE_INTERVAL_LEDGER_UNIT_REQUIRED')
    exact_lots=(Fraction(maximum_gross_quote.amount)/Fraction(minimum_price.value)
        /Fraction(notional_multiplier)/Fraction(quantity_step.value))
    lots=-(-exact_lots.numerator//exact_lots.denominator)
    upper=_grid_decimal(lots,quantity_step.value)
    return QuoteFillIntervalV1(contract_version='2026-10-29',record_kind='QUOTE_FILL_INTERVAL_V1',
        minimum=t.Quantity(value=Decimal(0),unit=quantity_step.unit),
        maximum=t.Quantity(value=upper,unit=quantity_step.unit),gross_quote=maximum_gross_quote,
        minimum_execution_price=minimum_price,maximum_execution_price=maximum_price,
        quantity_step=quantity_step,notional_multiplier=notional_multiplier)
