"""Exact incremental funding for management actions.

No second ledger: this computes only the additional worst-case requirement that
must pass the existing capacity/hold checks. Cancellation and keep-priority
reductions never release or re-use existing holds here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.order_program import amount
from trading_system.risk.policy_checks import exact_risk_arithmetic


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagementFundingRequirement:
    asset: str
    amount: t.Money

    @property
    def execution_authorized(self) -> bool:
        return False


def incremental_management_requirements(*, kind: str, product: str, quote_asset: str,
                                        old_quantity: str | None, old_price: str | None,
                                        new_quantity: str | None, new_price: str | None,
                                        remaining_quantity: str | None,
                                        native_multiplier: str = '1',
                                        new_fees: tuple[t.Money, ...] = ()) -> tuple[t.Money, ...]:
    if kind not in ('CANCEL', 'KEEP_PRIORITY', 'MODIFY_LIMIT', 'CANCEL_REPLACE'):
        raise ValueError('MANAGEMENT_KIND_INVALID')
    if kind == 'CANCEL':
        if new_quantity is not None or new_price is not None:
            raise ValueError('MANAGEMENT_NO_NEW_TERMS_EXPECTED')
        return ()
    if kind == 'KEEP_PRIORITY':
        if new_price is not None:
            raise ValueError('MANAGEMENT_KEEP_PRIORITY_PRICE_CHANGE_UNSUPPORTED')
        return ()
    if kind == 'MODIFY_LIMIT':
        if product != 'USD_M_PERP':
            raise ValueError('MANAGEMENT_MODIFY_USDM_REQUIRED')
        if old_quantity is None or old_price is None or new_quantity is None or new_price is None \
                or remaining_quantity is None:
            raise ValueError('MANAGEMENT_MODIFY_TERMS_REQUIRED')
        with exact_risk_arithmetic():
            multiplier = Decimal(amount(native_multiplier))
            old_remaining = Decimal(amount(remaining_quantity)) * Decimal(amount(old_price)) * multiplier
            new_worst = Decimal(amount(new_quantity)) * Decimal(amount(new_price)) * multiplier
            incremental = max(Decimal(0), new_worst - old_remaining)
            requirements = {quote_asset: incremental}
            for fee in new_fees:
                if type(fee) is not t.Money or fee.amount <= 0:
                    raise ValueError('MANAGEMENT_FEE_INVALID')
                requirements[fee.currency] = requirements.get(fee.currency, Decimal(0)) + fee.amount
        return tuple(t.Money(amount=value, currency=currency)
                     for currency, value in sorted(requirements.items()) if value > 0)
    # Replacement is a new independent order: the old hold is never netted away.
    if new_quantity is None or new_price is None:
        raise ValueError('MANAGEMENT_REPLACEMENT_TERMS_REQUIRED')
    with exact_risk_arithmetic():
        multiplier = Decimal(amount(native_multiplier))
        requirements = {quote_asset: Decimal(amount(new_quantity))
                        * Decimal(amount(new_price)) * multiplier}
        for fee in new_fees:
            if type(fee) is not t.Money or fee.amount <= 0:
                raise ValueError('MANAGEMENT_FEE_INVALID')
            requirements[fee.currency] = requirements.get(fee.currency, Decimal(0)) + fee.amount
    return tuple(t.Money(amount=value, currency=currency)
                 for currency, value in sorted(requirements.items()) if value > 0)
