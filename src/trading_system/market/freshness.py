"""Per-market freshness and completeness verdicts for new risk.

`DataVerdict.usable` means "this snapshot may serve an entry/new-risk decision",
never "the market is safe" and never an execution permit. The reference 2-second
gate is configured for liquid crypto only; equity sessions require their own
explicit limit and a closed/halted/unknown session cannot open new risk even
with a recent last price. Authorized emergency reduction is a separate caller
policy and must not be blocked mechanically by this gate (approved SPEC
SAFETY_STATE_MACHINES, market-session section).
"""
from __future__ import annotations

from collections.abc import Mapping

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp

from .provenance import is_digest, valid_evidence_ref
from .snapshots import snapshot_content_hash

DEFAULT_AGE_LIMITS_MS: dict[t.Product, int] = {
    t.Product.SPOT: 2_000,
    t.Product.USD_M_PERP: 2_000,
}

_SESSION_CODES = {
    'CLOSED': 'SESSION_CLOSED',
    'HALTED': 'SESSION_HALTED',
    'UNKNOWN': 'SESSION_UNKNOWN',
}


class FreshnessPolicy:
    """Evaluate quote age, provenance and session coverage for new risk."""

    def __init__(self, age_limits_ms: Mapping[t.Product, int] | None = None, *,
                 closed_state_max_age_ms: int | None = None):
        limits = dict(DEFAULT_AGE_LIMITS_MS if age_limits_ms is None else age_limits_ms)
        checked: dict[t.Product, int] = {}
        for product, limit in limits.items():
            if not isinstance(product, t.Product):
                raise ValueError('age limits are keyed by Product')
            if type(limit) is not int or limit <= 0:
                raise ValueError('age limit must be a positive integer millisecond value')
            checked[product] = limit
        if closed_state_max_age_ms is not None and (
                type(closed_state_max_age_ms) is not int or closed_state_max_age_ms <= 0):
            raise ValueError('closed-session age limit must be a positive integer')
        self._limits = checked
        self._closed_limit = closed_state_max_age_ms

    def evaluate(self, snapshot: t.SnapshotPacket, now: t.ClockReading,
                 session: t.SessionState) -> t.DataVerdict:
        codes: list[str] = []
        missing: list[str] = []

        def reject(code: str) -> None:
            if code not in codes:
                codes.append(code)

        def omit(name: str) -> None:
            if name not in missing:
                missing.append(name)

        now_dt = None
        if not isinstance(now, t.ClockReading):
            reject('CLOCK_INVALID')
        else:
            if type(now.monotonic_ns) is not int or now.monotonic_ns <= 0:
                reject('CLOCK_INVALID')
            if now.clock_health not in ('HEALTHY', 'DEGRADED', 'UNKNOWN'):
                reject('CLOCK_INVALID')
            elif now.clock_health != 'HEALTHY':
                # A degraded clock cannot distinguish a jump from a stale quote.
                reject('CLOCK_UNHEALTHY')
            try:
                now_dt = utc_timestamp(now.utc_now)
            except (TypeError, ValueError):
                reject('CLOCK_INVALID')

        if not isinstance(snapshot, t.SnapshotPacket):
            reject('INVALID_SNAPSHOT')
            return t.DataVerdict(usable=False, age_ms=0, missing=(), rejection_codes=tuple(codes))

        scope = snapshot.scope
        scope_ok = (isinstance(scope, t.ScopeKey)
                    and isinstance(scope.environment, t.Environment)
                    and isinstance(scope.product, t.Product)
                    and isinstance(scope.venue_account_id, str) and bool(scope.venue_account_id)
                    and isinstance(scope.instrument_uid, str) and bool(scope.instrument_uid)
                    and scope.venue_leg in ('BOTH', 'LONG', 'SHORT'))
        if not scope_ok:
            reject('INVALID_SCOPE')

        ref = snapshot.ref
        if (not isinstance(ref, t.SnapshotRef) or not is_digest(ref.hash)
                or not isinstance(ref.id, str) or not ref.id
                or not isinstance(ref.source, str) or not ref.source
                or not isinstance(ref.environment, t.Environment)):
            reject('INVALID_SNAPSHOT_REF')
        else:
            if scope_ok and ref.environment != scope.environment:
                reject('SNAPSHOT_REFERENCE_SCOPE_MISMATCH')
            try:
                visible = utc_timestamp(ref.visible_at)
            except (TypeError, ValueError):
                reject('INVALID_SNAPSHOT_REF')
            else:
                if now_dt is not None and visible > now_dt:
                    reject('FUTURE_DATA')
            try:
                if snapshot_content_hash(snapshot) != ref.hash:
                    reject('SNAPSHOT_HASH_MISMATCH')
            except (TypeError, ValueError, AttributeError):
                reject('SNAPSHOT_HASH_MISMATCH')

        age_ms = 0
        age_known = False
        if not isinstance(snapshot.quote, t.Quote):
            reject('INVALID_QUOTE')
        else:
            quote = snapshot.quote
            present = [price for price in (quote.bid, quote.ask, quote.last)
                       if price is not None]
            if any(not isinstance(price, t.Price) for price in present):
                reject('INVALID_QUOTE')
            if quote.bid is None:
                omit('bid')
                reject('MISSING_BID')
            if quote.ask is None:
                omit('ask')
                reject('MISSING_ASK')
            if len({price.currency for price in present if isinstance(price, t.Price)}) > 1:
                reject('MIXED_QUOTE_CURRENCY')
            try:
                occurred = utc_timestamp(quote.occurred_at)
                received = utc_timestamp(quote.received_at)
            except (TypeError, ValueError):
                reject('INVALID_TIMESTAMP')
            else:
                if occurred > received:
                    reject('INVALID_TIME_ORDER')
                elif now_dt is None:
                    reject('CLOCK_INVALID')
                else:
                    age_ms = int((now_dt - occurred).total_seconds() * 1000)
                    age_known = True
                    if age_ms < 0 or received > now_dt:
                        reject('FUTURE_DATA')

        try:
            published = utc_timestamp(snapshot.published_at)
            revision_visible = utc_timestamp(snapshot.revision_visible_at)
        except (TypeError, ValueError):
            reject('INVALID_TIMESTAMP')
        else:
            if published > revision_visible:
                reject('INVALID_TIME_ORDER')
            if now_dt is not None and any(moment > now_dt
                                          for moment in (published, revision_visible)):
                reject('FUTURE_DATA')

        coverage = snapshot.coverage
        if not isinstance(coverage, t.Coverage):
            reject('INVALID_COVERAGE')
        else:
            if scope_ok and coverage.scope != scope:
                reject('COVERAGE_SCOPE_MISMATCH')
            if coverage.query_succeeded is not True:
                reject('COVERAGE_QUERY_FAILED')
            if coverage.pagination_complete is not True:
                reject('COVERAGE_INCOMPLETE')
            if coverage.supported is not True:
                reject('COVERAGE_UNSUPPORTED')
            if not isinstance(coverage.missing, tuple):
                reject('INVALID_COVERAGE')
            else:
                items = [item for item in coverage.missing
                         if not isinstance(item, str) or not item]
                if items:
                    reject('INVALID_COVERAGE')
                if coverage.missing:
                    reject('MISSING_COVERAGE')
                    for item in coverage.missing:
                        if isinstance(item, str) and item:
                            omit(item)

        for refs in (snapshot.private_refs, snapshot.economic_refs):
            if not isinstance(refs, tuple) or any(
                    not isinstance(ref, t.EvidenceRef)
                    or (now_dt is not None
                        and scope_ok
                        and not valid_evidence_ref(ref, scope.environment, now_dt))
                    for ref in refs):
                reject('INVALID_EVIDENCE_REF')

        state = None
        if not isinstance(session, t.SessionState):
            reject('SESSION_INVALID')
        else:
            if session.state not in ('OPEN', 'CLOSED', 'HALTED', 'UNKNOWN'):
                reject('SESSION_INVALID')
            else:
                state = session.state
            if scope_ok and session.instrument_uid != scope.instrument_uid:
                reject('SESSION_MISMATCH')
            try:
                observed = utc_timestamp(session.observed_at)
            except (TypeError, ValueError):
                reject('SESSION_INVALID')
            else:
                if now_dt is not None and observed > now_dt:
                    reject('FUTURE_DATA')
            if now_dt is not None and scope_ok:
                if not valid_evidence_ref(session.evidence, scope.environment, now_dt):
                    reject('SESSION_EVIDENCE_INVALID')

        if age_known and now_dt is not None:
            if state == 'OPEN':
                limit = self._limits.get(scope.product) if scope_ok else None
                if limit is None:
                    # Never silently apply the crypto reference gate to another market.
                    reject('FRESHNESS_POLICY_MISSING')
                elif age_ms > limit:
                    reject('STALE_QUOTE')
            elif state in _SESSION_CODES:
                reject(_SESSION_CODES[state])
                if self._closed_limit is not None and age_ms > self._closed_limit:
                    reject('STALE_QUOTE')

        return t.DataVerdict(usable=not codes, age_ms=age_ms,
                             missing=tuple(missing), rejection_codes=tuple(codes))
