"""Evidence-backed UTC session intervals; no local timezone or 24/7 fallback."""
from dataclasses import dataclass
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.recovery.reconciler import valid_ref


@dataclass(frozen=True, kw_only=True)
class SessionEvidence:
    scope: t.ScopeKey
    instrument_revision: int
    calendar_revision: str
    session_id: str
    starts_at: str
    ends_at: str
    state: str
    observed_at: str
    next_open_at: str | None
    evidence: t.EvidenceRef


class MarketSessionService:
    def __init__(self, *, scope: t.ScopeKey, read_session: Callable | None = None):
        self.scope = scope
        self.read_session = read_session

    def state(self, instrument: t.InstrumentSpec, now: t.ClockReading) -> t.SessionState:
        unknown = t.SessionState(instrument_uid=instrument.instrument_uid,
            session_id='UNKNOWN', state='UNKNOWN', observed_at=now.utc_now, next_open_at=None,
            evidence=t.EvidenceRef(id='missing-session', hash='0' * 64,
                environment=self.scope.environment, source='UNVERIFIED', visible_at=now.utc_now))
        try:
            row = self.read_session(instrument, now) if self.read_session else None
            if (now.clock_health != 'HEALTHY' or self.scope.environment not in
                    (t.Environment.PAPER, t.Environment.RESEARCH)
                    or instrument.product != t.Product.US_EQUITY
                    or self.scope.venue_leg != 'BOTH'
                    or type(instrument.rule_revision) is not int or instrument.rule_revision < 1
                    or instrument.product != self.scope.product
                    or instrument.instrument_uid != self.scope.instrument_uid
                    or not isinstance(row, SessionEvidence) or row.scope != self.scope
                    or row.instrument_revision != instrument.rule_revision
                    or not row.calendar_revision or not row.session_id
                    or row.state not in ('OPEN', 'CLOSED', 'HALTED')
                    or not utc_timestamp(row.starts_at) <= utc_timestamp(now.utc_now) < utc_timestamp(row.ends_at)
                    or not 0 <= (utc_timestamp(now.utc_now) - utc_timestamp(row.observed_at)).total_seconds() <= 60
                    or not valid_ref(row.evidence, self.scope, now.utc_now, 60)
                    or (row.next_open_at is not None and utc_timestamp(row.next_open_at) <= utc_timestamp(now.utc_now))):
                return unknown
            return t.SessionState(instrument_uid=instrument.instrument_uid, session_id=row.session_id,
                state=row.state, observed_at=row.observed_at, next_open_at=row.next_open_at,
                evidence=row.evidence)
        except Exception:
            # A failed calendar/halts reader is an unknown market, never OPEN.
            return unknown
