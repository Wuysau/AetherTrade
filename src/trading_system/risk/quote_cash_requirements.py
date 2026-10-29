"""Per-asset cash and receipt envelopes; this calculation never admits a trade.

The interval's execution bounds must be independently authenticated by the
financial host. These arithmetic records cannot establish that a venue enforces
those bounds, that a wallet owns the assets, or that fees are actually known.
"""
from dataclasses import dataclass
from decimal import Decimal,DecimalException
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_program import identifier
from trading_system.contracts.program_financial import require
from trading_system.intents.quote_fill_interval import QuoteFillIntervalV1,compile_quote_fill_interval
from .program_exposure import FeeBound
from .policy_checks import exact_risk_arithmetic


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteCashRequirementsV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_CASH_REQUIREMENTS_V1']
    side:Literal['BUY','SELL']
    base_asset:str
    interval:QuoteFillIntervalV1
    fees:tuple[FeeBound,...]
    required_assets:tuple[t.Money,...]
    receipt_fee_bounds:tuple[t.Money,...]
    fee_liabilities:tuple[t.Money,...]
    net_received_minimum:t.Money
    net_received_maximum:t.Money
    position_delta_minimum:t.Quantity
    position_delta_maximum:t.Quantity

    @property
    def execution_authorized(self)->Literal[False]:return False


def derive_quote_cash_requirements(interval,*,side,base_asset,fees):
    require(type(interval) is QuoteFillIntervalV1,'QUOTE_FILL_INTERVAL_REQUIRED')
    require(side in ('BUY','SELL') and interval.maximum.unit in ('BASE','SHARE')
        and interval.notional_multiplier==1,'CASH_QUOTE_UNITS_REQUIRED')
    identifier(base_asset)
    require(base_asset!=interval.gross_quote.currency,'CASH_QUOTE_ASSET_COLLISION')
    require(type(fees) is tuple,'QUOTE_FEE_FACTS_REQUIRED')
    expected=compile_quote_fill_interval(maximum_gross_quote=interval.gross_quote,
        minimum_price=interval.minimum_execution_price,maximum_price=interval.maximum_execution_price,
        quantity_step=interval.quantity_step,notional_multiplier=interval.notional_multiplier)
    require(canonical_hash(domain_payload(expected))==canonical_hash(domain_payload(interval)),
        'QUOTE_FILL_INTERVAL_CHANGED')
    try:
        with exact_risk_arithmetic():
            return _derive(interval,side,base_asset,fees)
    except DecimalException as error:
        raise ValueError('QUOTE_CASH_ARITHMETIC_UNPROVEN') from error


def _derive(interval,side,base_asset,fees):
    quote=interval.gross_quote;quantity=interval.maximum.value
    received_asset=base_asset if side=='BUY' else quote.currency
    received_upper=quantity if side=='BUY' else quote.amount
    external={};receipt={};liability={}
    for fee in fees:
        require(type(fee) is FeeBound and type(fee.amount) is t.Money
            and type(fee.amount.amount) is Decimal and fee.amount.amount.is_finite()
            and fee.amount.amount>=0 and fee.debit in ('FROM_RECEIPT','EXTERNAL_BALANCE'),
            'QUOTE_FEE_BOUND_INVALID')
        asset=fee.amount.currency;identifier(asset)
        liability[asset]=liability.get(asset,Decimal(0))+fee.amount.amount
        if fee.debit=='FROM_RECEIPT':
            require(asset==received_asset,'QUOTE_RECEIPT_FEE_ASSET_MISMATCH')
            receipt[asset]=receipt.get(asset,Decimal(0))+fee.amount.amount
        else:external[asset]=external.get(asset,Decimal(0))+fee.amount.amount
    require(receipt.get(received_asset,Decimal(0))<=received_upper,'QUOTE_RECEIPT_FEE_EXCEEDS_RECEIPT')
    required=dict(external)
    debit_asset=quote.currency if side=='BUY' else base_asset
    required[debit_asset]=required.get(debit_asset,Decimal(0))+(quote.amount if side=='BUY' else quantity)
    base_fee=external.get(base_asset,Decimal(0))
    minimum=-base_fee if side=='BUY' else -quantity-base_fee
    maximum=quantity if side=='BUY' else Decimal(0)
    money=lambda values:tuple(t.Money(currency=k,amount=v) for k,v in sorted(values.items()) if v)
    return QuoteCashRequirementsV1(contract_version='2026-10-30',record_kind='QUOTE_CASH_REQUIREMENTS_V1',
        side=side,base_asset=base_asset,interval=interval,fees=fees,required_assets=money(required),
        receipt_fee_bounds=money(receipt),fee_liabilities=money(liability),
        net_received_minimum=t.Money(currency=received_asset,amount=Decimal(0)),
        net_received_maximum=t.Money(currency=received_asset,amount=received_upper),
        position_delta_minimum=t.Quantity(unit=interval.maximum.unit,value=minimum),
        position_delta_maximum=t.Quantity(unit=interval.maximum.unit,value=maximum))
