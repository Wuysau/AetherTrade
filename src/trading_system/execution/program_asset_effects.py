"""Exact Spot fill arithmetic. A calculation never grants spendable credit.

The ledger writer must independently authenticate committed source, complete
economics and current allocation before applying these deltas to a book. Other
products need their own typed settlement/PnL evidence and cannot use this path.
"""
from dataclasses import dataclass
from decimal import Decimal,localcontext,Inexact,Rounded
from typing import Literal

from trading_system.contracts import types as t
from .paper_program_reports import NormalizedPaperFill,normalize_native_economics


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class SpotAssetEffects:
    asset_deltas:tuple[t.Money,...]
    position_delta:t.Quantity
    gross_notional:t.Money
    fees:tuple[t.Money,...]

    @property
    def balance_credit_authorized(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False


def derive_spot_asset_effects(normalized,*,base_asset,quote_asset):
    require(type(normalized) is NormalizedPaperFill and normalized.fill.scope.product==t.Product.SPOT,
        'SPOT_ASSET_EFFECTS_REQUIRED')
    require(normalized.fees_known is True,'FEE_RECONCILIATION_REQUIRED')
    economics=normalized.economics;unit=economics.unit;fill=normalized.fill
    require(base_asset!=quote_asset and unit.native_unit==base_asset and unit.ledger_unit=='BASE'
        and unit.ledger_units_per_native_unit==unit.native_multiplier==unit.ledger_multiplier==1
        and fill.quantity==economics.native_quantity and fill.price==economics.venue_price
        and fill.price.currency==quote_asset,'CASH_NATIVE_UNIT_BINDING_MISMATCH')
    rederived=normalize_native_economics(quantity=fill.quantity,price=fill.price,fees=economics.fees,
        reported_gross=economics.reported_gross,unit=unit)
    require(rederived==economics and not economics.discrepancies,'ECONOMICS_RECONCILIATION_REQUIRED')
    require(normalized.side in ('BUY','SELL') and all(type(f.amount) is Decimal and f.amount.is_finite()
        and type(f.currency) is str and bool(f.currency) for f in economics.fees),'EXACT_FEE_ASSET_FACTS_REQUIRED')
    with localcontext() as arithmetic:
        arithmetic.prec=256
        arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        sign=Decimal(1) if normalized.side=='BUY' else Decimal(-1)
        deltas={base_asset:sign*economics.ledger_quantity.value,quote_asset:-sign*economics.expected_gross.amount}
        for fee in economics.fees:deltas[fee.currency]=deltas.get(fee.currency,Decimal(0))-fee.amount
    return SpotAssetEffects(asset_deltas=tuple(t.Money(amount=value,currency=asset) for asset,value in sorted(deltas.items())),
        position_delta=t.Quantity(value=deltas[base_asset],unit=unit.ledger_unit),
        gross_notional=economics.expected_gross,fees=economics.fees)
