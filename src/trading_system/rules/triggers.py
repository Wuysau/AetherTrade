"""Explicit trigger timing: window, closed-bar eligibility, cooldown, edge/level.

Decision time is the snapshot's own `revision_visible_at`; this module never
reads a wall clock, so replaying the same snapshot reproduces the same instant.
All conversions are exact (integer epoch seconds plus `Decimal` fractions) and
never use float. The module produces timing verdicts only; it issues no order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class TriggerSemanticsError(ValueError):
    """Timing inputs are internally inconsistent and cannot be evaluated."""


def exact_epoch_seconds(moment: datetime) -> Decimal:
    """Exact seconds since the UTC epoch without float conversion."""
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise TriggerSemanticsError('timestamps must be timezone aware')
    delta = moment.astimezone(timezone.utc) - _EPOCH
    return (Decimal(delta.days) * 86400 + Decimal(delta.seconds)
            + Decimal(delta.microseconds) / Decimal(1_000_000))


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservationBar:
    """Deterministic window bucket for one observation."""

    window_seconds: int
    bar_start_seconds: Decimal
    bar_end_seconds: Decimal
    bar_id: str
    closed: bool


def observation_bar(*, window_seconds: int, occurred_at: datetime,
                    decision_at: datetime) -> ObservationBar:
    """Bucket `occurred_at` into a fixed window and decide bar closure.

    The bar is closed once the decision instant reaches the window end. Windows
    are anchored to the UTC epoch, so two runs with the same timestamps always
    produce the same identifier.
    """
    if type(window_seconds) is not int or window_seconds <= 0:
        raise TriggerSemanticsError('a closed-bar observation needs a positive window')
    if occurred_at > decision_at:
        raise TriggerSemanticsError('a decision cannot precede its observation')

    start = exact_epoch_seconds(occurred_at)
    if start < 0:
        raise TriggerSemanticsError('timestamps before the UTC epoch are not supported')
    width = Decimal(window_seconds)
    bar_index = int(start) // window_seconds
    bar_start = Decimal(bar_index * window_seconds)
    bar_end = bar_start + width
    decision = exact_epoch_seconds(decision_at)
    return ObservationBar(window_seconds=window_seconds,
                          bar_start_seconds=bar_start,
                          bar_end_seconds=bar_end,
                          bar_id=f'{window_seconds}s@{bar_index * window_seconds}',
                          closed=decision >= bar_end)


def cooldown_active(*, cooldown_seconds: int, last_triggered_at: datetime | None,
                    decision_at: datetime) -> bool:
    if type(cooldown_seconds) is not int or cooldown_seconds < 0:
        raise TriggerSemanticsError('cooldown seconds must be a non-negative integer')
    if last_triggered_at is None or cooldown_seconds == 0:
        return False
    elapsed = exact_epoch_seconds(decision_at) - exact_epoch_seconds(last_triggered_at)
    if elapsed < 0:
        raise TriggerSemanticsError('the last trigger cannot be in the future')
    return elapsed < Decimal(cooldown_seconds)


@dataclass(frozen=True, slots=True, kw_only=True)
class TriggerVerdict:
    fires: bool
    reason: str


def evaluate_trigger(*, trigger: str, previous_condition: bool | None,
                     condition: bool, cooling_down: bool) -> TriggerVerdict:
    """EDGE fires on a false->true transition, CONTINUOUS while the level holds.

    A cooldown suppresses the proposal but still records the observation as the
    caller's state update, so the same edge is not replayed after the cooldown.
    """
    if trigger == 'EDGE':
        if not condition:
            return TriggerVerdict(fires=False, reason='CONDITION_FALSE')
        if previous_condition is None:
            return TriggerVerdict(fires=False, reason='EDGE_NO_PRIOR_OBSERVATION')
        if previous_condition:
            return TriggerVerdict(fires=False, reason='CONDITION_NOT_NEW')
        if cooling_down:
            return TriggerVerdict(fires=False, reason='COOLDOWN_ACTIVE')
        return TriggerVerdict(fires=True, reason='EDGE_RISING')
    if trigger == 'CONTINUOUS':
        if not condition:
            return TriggerVerdict(fires=False, reason='CONDITION_FALSE')
        if cooling_down:
            return TriggerVerdict(fires=False, reason='COOLDOWN_ACTIVE')
        return TriggerVerdict(fires=True, reason='LEVEL_HOLDING')
    raise TriggerSemanticsError(f'unknown trigger {trigger!r}')
