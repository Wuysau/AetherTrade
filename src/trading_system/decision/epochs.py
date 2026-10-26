"""Process-local research epochs and monotonic deadlines; no execution authority."""
from dataclasses import dataclass

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp


def healthy_clock(now: t.ClockReading) -> None:
    if (now.clock_health != 'HEALTHY' or type(now.monotonic_ns) is not int
            or now.monotonic_ns < 0):
        raise ValueError('CLOCK_UNHEALTHY')
    utc_timestamp(now.utc_now)


@dataclass(frozen=True)
class Deadline:
    expires_at: str
    started_ns: int
    end_ns: int

    @classmethod
    def bind(cls, expires_at: str, now: t.ClockReading, maximum_runtime_ns: int):
        healthy_clock(now)
        delta = utc_timestamp(expires_at) - utc_timestamp(now.utc_now)
        duration = ((delta.days * 86400 + delta.seconds) * 1_000_000
                    + delta.microseconds) * 1000
        if duration <= 0:
            raise ValueError('EXPIRED')
        return cls(expires_at, now.monotonic_ns,
                   now.monotonic_ns + min(duration, maximum_runtime_ns))

    def valid(self, now: t.ClockReading) -> bool:
        healthy_clock(now)
        return (self.started_ns <= now.monotonic_ns < self.end_ns
                and utc_timestamp(now.utc_now) < utc_timestamp(self.expires_at))


class Epochs:
    """Owned under the scheduler lock; never persisted as a trading epoch."""

    def __init__(self):
        self._latest: dict[tuple[str, str], int] = {}

    @staticmethod
    def key(slot: t.DecisionSlot) -> tuple[str, str]:
        if (not slot.strategy_id or not slot.instrument_or_portfolio_id
                or type(slot.decision_epoch) is not int or slot.decision_epoch < 0):
            raise ValueError('INVALID_SLOT')
        return slot.strategy_id, slot.instrument_or_portfolio_id

    def latest(self, slot: t.DecisionSlot) -> int:
        return self._latest.get(self.key(slot), -1)

    def advance(self, slot: t.DecisionSlot) -> None:
        if slot.decision_epoch < self.latest(slot):
            raise ValueError('STALE_EPOCH')
        self._latest[self.key(slot)] = slot.decision_epoch
