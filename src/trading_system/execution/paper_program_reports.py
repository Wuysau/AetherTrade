"""Exact PAPER source economics; no authorization or financial posting.

The complete report verifier is coupled to the independently reviewed matcher
interface. This first unit bridge preserves native facts for existing FillLedger
and carries the separate position-unit bridge for generic ledger accounting.
"""
from dataclasses import dataclass
from decimal import localcontext,Inexact,Rounded
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.program_financial import FinancialUnit,require


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeFillEconomics:
    native_quantity:t.Quantity
    venue_price:t.Price
    ledger_quantity:t.Quantity
    unit:FinancialUnit
    reported_gross:t.Money
    expected_gross:t.Money
    fees:tuple[t.Money,...]
    discrepancies:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False

    @property
    def source_verified(self)->Literal[False]:return False


def normalize_native_economics(*,quantity,price,fees,reported_gross,unit):
    require(type(quantity) is t.Quantity and type(price) is t.Price
        and type(reported_gross) is t.Money and type(unit) is FinancialUnit,
        'PAPER_TYPED_NATIVE_ECONOMICS_REQUIRED')
    require(quantity.unit==unit.native_unit and quantity.value>0 and price.value>0,
        'PAPER_NATIVE_FILL_UNIT_OR_VALUE_INVALID')
    require(type(fees) is tuple and all(type(f) is t.Money for f in fees),
        'PAPER_EXPLICIT_FEE_FACTS_REQUIRED')
    # Reuse the existing immutable unit contract, including its exact multiplier
    # identity. Native quantity/price/fees are never overwritten or FX converted.
    unit.__post_init__()
    with localcontext() as arithmetic:
        arithmetic.prec=256
        arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        ledger_value=quantity.value*unit.ledger_units_per_native_unit
        native_gross=quantity.value*price.value*unit.native_multiplier
        ledger_gross=ledger_value*price.value*unit.ledger_multiplier
    require(native_gross==ledger_gross,'PAPER_UNIT_GROSS_BRIDGE_MISMATCH')
    discrepancies=()
    if reported_gross.currency!=price.currency:
        discrepancies=('GROSS_CURRENCY_MISMATCH',)
    elif reported_gross.amount!=native_gross:
        discrepancies=('GROSS_NOTIONAL_MISMATCH',)
    return NativeFillEconomics(native_quantity=quantity,venue_price=price,
        ledger_quantity=t.Quantity(value=ledger_value,unit=unit.ledger_unit),unit=unit,
        reported_gross=reported_gross,expected_gross=t.Money(amount=native_gross,currency=price.currency),
        fees=fees,discrepancies=discrepancies)
