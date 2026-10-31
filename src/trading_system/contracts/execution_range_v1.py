"""Published execution price-range source, distinct from the risk envelope.

The venue execution range governs whether an order may execute at a price. It
is not the risk maximum price and not a licence to fill beyond the limit. A
missing or expired range blocks; a violated range yields the official
EXECUTION_RULE_PRICE_RANGE_EXCEEDED expiry reason. No MARKET conversion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.order_program import amount


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionRangeEventV1:
    contract_version: Literal['2026-10-31']
    scope: object
    venue_symbol: str
    revision: int
    reference_price: str
    lower_price: str
    upper_price: str
    valid_from: str
    valid_until: str
    source_hash: str

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionRangeDecision:
    status: Literal['ALLOW', 'BLOCK', 'EXPIRED']
    reason: str = ''
    lower_price: str | None = None
    upper_price: str | None = None

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


def resolve_execution_range(event: ExecutionRangeEventV1 | None, *, price: str,
                            now: str) -> ExecutionRangeDecision:
    if event is None:
        return ExecutionRangeDecision(status='BLOCK', reason='EXECUTION_RANGE_SOURCE_REQUIRED')
    if type(event) is not ExecutionRangeEventV1 or event.contract_version != '2026-10-31' \
            or type(event.revision) is not int or event.revision < 1 \
            or not isinstance(event.source_hash, str) or not event.source_hash:
        raise ValueError('EXECUTION_RANGE_EVENT_INVALID')
    lower, upper, reference = (amount(event.lower_price), amount(event.upper_price),
                               amount(event.reference_price))
    value = amount(price)
    if not (lower <= reference <= upper) or lower > upper:
        raise ValueError('EXECUTION_RANGE_BAND_INVALID')
    if utc_timestamp(now) < utc_timestamp(event.valid_from):
        return ExecutionRangeDecision(status='BLOCK', reason='EXECUTION_RANGE_NOT_YET_VALID',
                                      lower_price=event.lower_price,
                                      upper_price=event.upper_price)
    if utc_timestamp(now) >= utc_timestamp(event.valid_until):
        return ExecutionRangeDecision(status='EXPIRED',
                                      reason='EXECUTION_RULE_PRICE_RANGE_EXCEEDED',
                                      lower_price=event.lower_price,
                                      upper_price=event.upper_price)
    if not lower <= value <= upper:
        return ExecutionRangeDecision(status='EXPIRED',
                                      reason='EXECUTION_RULE_PRICE_RANGE_EXCEEDED',
                                      lower_price=event.lower_price,
                                      upper_price=event.upper_price)
    return ExecutionRangeDecision(status='ALLOW', lower_price=event.lower_price,
                                  upper_price=event.upper_price)
