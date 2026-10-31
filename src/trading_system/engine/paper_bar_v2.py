"""OHLCV bar events are never an execution path.

A bar cannot prove the intrabar ordering of a stop and a take-profit. The only
allowed outcomes are explicit ambiguity/unknown dispositions; no fill, cancel or
funds release is produced. This closes ORD-50 without inventing a price path.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.order_program import amount


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperBarEventV2:
    contract_version: Literal['2026-10-31']
    scope: object
    venue_symbol: str
    interval_seconds: int
    open_time: str
    close_time: str
    open: str
    high: str
    low: str
    close: str
    sequence: int
    closed: bool
    source_hash: str

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class BarPathDecision:
    state: Literal['NO_LEVEL_TOUCHED', 'PATH_UNKNOWN', 'AMBIGUOUS_BOTH_LEVELS_TOUCHED']
    policy: Literal['NO_BAR_PATH_INFERENCE'] = 'NO_BAR_PATH_INFERENCE'
    reason: str = ''
    fills: tuple[object, ...] = ()
    duties: tuple[str, ...] = ()

    @property
    def execution_authorized(self) -> Literal[False]:
        return False

    @property
    def funds_release_authorized(self) -> Literal[False]:
        return False


def decide_bar_path(event: PaperBarEventV2, *, side: str, take_profit: str,
                    stop_loss: str) -> BarPathDecision:
    if type(event) is not PaperBarEventV2:
        raise ValueError('PAPER_BAR_EVENT_REQUIRED')
    if event.contract_version != '2026-10-31' or event.interval_seconds <= 0 \
            or type(event.sequence) is not int or event.sequence < 1 \
            or not isinstance(event.source_hash, str) or not event.source_hash:
        raise ValueError('PAPER_BAR_EVENT_INVALID')
    utc_timestamp(event.open_time)
    utc_timestamp(event.close_time)
    high, low = amount(event.high), amount(event.low)
    tp, sl = amount(take_profit), amount(stop_loss)
    if high < low or not (low <= tp <= high or low <= sl <= high):
        # Levels outside the bar are still not evidence of an executed path.
        return BarPathDecision(state='NO_LEVEL_TOUCHED')
    if side == 'BUY':
        if not (tp > sl):
            raise ValueError('PAPER_BAR_LEVEL_ORDER_INVALID')
    elif side == 'SELL':
        if not (sl > tp):
            raise ValueError('PAPER_BAR_LEVEL_ORDER_INVALID')
    else:
        raise ValueError('PAPER_BAR_SIDE_INVALID')
    if not event.closed:
        return BarPathDecision(state='PATH_UNKNOWN', reason='BAR_NOT_CLOSED',
                               duties=('KEEP_EXIT_DUTY',))
    touched_tp, touched_sl = low <= tp <= high, low <= sl <= high
    if touched_tp and touched_sl:
        return BarPathDecision(state='AMBIGUOUS_BOTH_LEVELS_TOUCHED',
                               reason='INTRABAR_ORDER_UNKNOWN',
                               duties=('KEEP_EXIT_DUTY', 'RECONCILE_AMBIGUOUS_EXIT'))
    if touched_tp or touched_sl:
        return BarPathDecision(state='PATH_UNKNOWN', reason='SINGLE_LEVEL_TOUCH_ONLY',
                               duties=('KEEP_EXIT_DUTY',))
    return BarPathDecision(state='NO_LEVEL_TOUCHED')


def bar_source_hash(event: PaperBarEventV2) -> str:
    return canonical_hash(domain_payload(event))
