"""Classify software-OCO outcomes. Non-atomic dual execution is not hidden.

A software OCO uses two independently authorized child commands. If both legs
execute before cancellation settles, the result is a real double execution and
must be reconciled. This module never releases funds, cancels a sibling or
upgrades a software list to native semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from trading_system.contracts.order_program import amount


@dataclass(frozen=True, slots=True, kw_only=True)
class OcoLegFill:
    child_id: str
    quantity: str
    price: str
    fill_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalOcoRaceDecision:
    state: Literal['SOFTWARE_OCO_NONE_EXECUTED', 'SOFTWARE_OCO_SINGLE_EXECUTED',
                   'SOFTWARE_OCO_DOUBLE_EXECUTED', 'SOFTWARE_OCO_UNSETTLED']
    executed_children: tuple[str, ...]
    duties: tuple[str, ...] = ()

    @property
    def native_equivalent(self) -> Literal[False]:
        return False

    @property
    def funds_release_authorized(self) -> Literal[False]:
        return False


def classify_local_oco(fills_by_child: Mapping[str, tuple[OcoLegFill, ...]]) -> LocalOcoRaceDecision:
    if not isinstance(fills_by_child, Mapping) or len(fills_by_child) > 2:
        raise ValueError('LOCAL_OCO_TWO_LEGS_REQUIRED')
    executed = tuple(sorted(child for child, fills in fills_by_child.items() if fills))
    for child, fills in fills_by_child.items():
        if not isinstance(child, str) or not child:
            raise ValueError('LOCAL_OCO_CHILD_ID_REQUIRED')
        if any(type(fill) is not OcoLegFill or fill.child_id != child
               or not fill.fill_id or amount(fill.quantity) <= 0 for fill in fills):
            raise ValueError('LOCAL_OCO_FILL_IDENTITY_INVALID')
    if len(executed) > 1:
        return LocalOcoRaceDecision(
            state='SOFTWARE_OCO_DOUBLE_EXECUTED', executed_children=executed,
            duties=('QUERY_BOTH_ORIGINAL_IDS', 'RETAIN_BOTH_OBLIGATIONS',
                    'RECONCILE_SOFTWARE_OCO_DOUBLE_EXECUTION', 'NO_FUNDS_RELEASE'))
    if len(executed) == 1:
        return LocalOcoRaceDecision(
            state='SOFTWARE_OCO_SINGLE_EXECUTED', executed_children=executed,
            duties=('CANCEL_SIBLING_BY_ORIGINAL_ID_AND_VERIFY',
                    'RETAIN_CANCELLATION_DUTY', 'NO_FUNDS_RELEASE'))
    return LocalOcoRaceDecision(state='SOFTWARE_OCO_NONE_EXECUTED',
                                executed_children=(),
                                duties=('RETAIN_BOTH_LEGS',))
