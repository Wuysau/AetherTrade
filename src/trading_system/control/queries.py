"""Scoped read-only projection and audit queries. No risk unlock or order write.

The store/feed ports are injected. This module binds every read to the server
actor's environment and exact ScopeKey before asking for data, checks a cursor's
scope/stream/resource binding, and never treats a missing projection as zero
exposure. Implementations must remain read-only; this module neither clears risk
locks nor emits execution commands.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Callable, Protocol

from trading_system.contracts import types as t
from trading_system.contracts.canonical import (
    canonical_hash,
    canonical_json,
    domain_payload,
    utc_timestamp,
)
from .auth import ControlError


RESOURCES = ('positions', 'orders', 'risk-locks', 'intents', 'control-commands')
MAX_PAGE_LIMIT = 1000


@dataclass(frozen=True, kw_only=True)
class ProjectionWatermark:
    """Durable read watermark supplied by the projection store.

    retained_from is the oldest still available sequence for this scope. A
    cursor older than retained_from - 1 cannot be resumed without a snapshot.
    """

    as_of: str
    sequence: int
    retained_from: int
    missing: tuple[str, ...] = ()


class ProjectionStore(Protocol):
    def watermark(self, scope: t.ScopeKey) -> ProjectionWatermark | None: ...

    def positions(self, scope: t.ScopeKey, owner_strategy_id: str | None
                  ) -> tuple[t.PositionProjection, ...]: ...

    def orders(self, scope: t.ScopeKey, owner_strategy_id: str | None
               ) -> tuple[t.OrderProjection, ...]: ...

    def risk_locks(self, scope: t.ScopeKey, owner_strategy_id: str | None
                   ) -> tuple[t.RiskLock, ...]: ...

    def controls(self, scope: t.ScopeKey, owner_strategy_id: str | None
                 ) -> tuple[t.ControlCommandProjection, ...]: ...

    def intents(self, scope: t.ScopeKey, owner_strategy_id: str | None
                ) -> tuple[t.TradeIntent, ...]: ...


def projection_stream_id(scope: t.ScopeKey, resource: str) -> str:
    """Cursor stream identity binds resource + exact environment/account scope."""
    return 'projection:v1:' + resource + ':' + canonical_hash(domain_payload(scope))


def _now(clock: Callable[[], datetime] | None) -> datetime:
    value = datetime.now(timezone.utc) if clock is None else clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('projection clock must return an aware datetime')
    return value.astimezone(timezone.utc)


def _authorize(scope: t.ScopeKey, actor: t.ActorContext) -> None:
    if not isinstance(scope, t.ScopeKey):
        raise ControlError(400, 'INVALID_SCOPE')
    if (actor.environment != scope.environment
            or scope not in actor.scopes):
        raise ControlError(403, 'AUTHORIZATION_SCOPE_MISMATCH')
    if scope.environment == t.Environment.LIVE:
        raise ControlError(403, 'LIVE_NOT_QUALIFIED')


def _scope_violation() -> ControlError:
    # Never return a record from another scope, even if an adapter is defective.
    return ControlError(503, 'PROJECTION_SCOPE_VIOLATION')


def _check_watermark(watermark: ProjectionWatermark | None) -> None:
    if watermark is None:
        return
    if not isinstance(watermark, ProjectionWatermark):
        raise ControlError(503, 'PROJECTION_WATERMARK_INVALID')
    try:
        utc_timestamp(watermark.as_of)
    except ValueError as exc:
        raise ControlError(503, 'PROJECTION_WATERMARK_INVALID') from exc
    if (type(watermark.sequence) is not int or watermark.sequence < 0
            or type(watermark.retained_from) is not int
            or watermark.retained_from < 0
            or watermark.retained_from > watermark.sequence + 1):
        raise ControlError(503, 'PROJECTION_WATERMARK_INVALID')
    if not isinstance(watermark.missing, tuple) or any(
            type(item) is not str or not item for item in watermark.missing):
        raise ControlError(503, 'PROJECTION_WATERMARK_INVALID')


def _check_cursor(query: t.ProjectionQueryRequest, stream_id: str,
                  watermark: ProjectionWatermark | None) -> None:
    cursor = query.cursor
    if cursor is None:
        return
    if not isinstance(cursor, t.StreamCursor):
        raise ControlError(400, 'INVALID_CURSOR')
    if (scalar_bool(cursor.sequence) or type(cursor.sequence) is not int
            or cursor.sequence < 0):
        raise ControlError(400, 'INVALID_CURSOR')
    if cursor.scope != query.scope:
        # Scope check happens before any store read; no other account/environment
        # projection is looked up or disclosed.
        raise ControlError(403, 'CURSOR_SCOPE_MISMATCH')
    if cursor.stream_id != stream_id:
        raise ControlError(409, 'CURSOR_STREAM_MISMATCH')
    if watermark is None:
        raise ControlError(409, 'CURSOR_EXPIRED')
    if cursor.sequence > watermark.sequence:
        raise ControlError(409, 'CURSOR_AHEAD')
    if cursor.sequence < watermark.retained_from - 1:
        raise ControlError(409, 'CURSOR_EXPIRED')


def scalar_bool(value: object) -> bool:
    """bool is an int subclass; reject it explicitly for numeric wire fields."""
    return isinstance(value, bool)


def _freshness(watermark: ProjectionWatermark | None, maximum_age_ms: int | None,
               observed: datetime, extra_missing: tuple[str, ...]) -> t.FreshnessView:
    missing = list(() if watermark is None else watermark.missing)
    if watermark is None:
        missing.append('PROJECTION_UNAVAILABLE')
        as_of = observed.isoformat()
        age_ms = 0
        stale = True
    else:
        as_of = watermark.as_of
        measured = int((observed - utc_timestamp(as_of)).total_seconds() * 1000)
        if measured < 0:
            missing.append('CLOCK_SKEW')
            age_ms = 0
        else:
            age_ms = measured
        stale = (maximum_age_ms is not None and age_ms > maximum_age_ms)
        if 'DATABASE_UNAVAILABLE' in missing:
            stale = True
    for reason in extra_missing:
        if reason not in missing:
            missing.append(reason)
    return t.FreshnessView(as_of=as_of, age_ms=age_ms, stale=stale,
                           missing=tuple(missing))


def _slice(records, limit: int, key):
    ordered = sorted(records, key=key)
    if len(ordered) > limit:
        return tuple(ordered[:limit]), True
    return tuple(ordered), False


class ProjectionQuery:
    """Implementation of the frozen ProjectionQuery port.

    The returned collections are current read projections, not an execution
    book. `freshness.missing` carries explicit unavailable/cost/capability
    reasons so a UI can explain why no new trade is suggested or permitted.
    """

    def __init__(self, store: ProjectionStore, *,
                 clock: Callable[[], datetime] | None = None):
        self.store = store
        self.clock = clock

    def read(self, query: t.ProjectionQueryRequest,
             actor: t.ActorContext) -> t.ProjectionPage:
        if not isinstance(query, t.ProjectionQueryRequest):
            raise ControlError(400, 'INVALID_PROJECTION_QUERY')
        if query.resource not in RESOURCES:
            raise ControlError(400, 'INVALID_PROJECTION_QUERY')
        if (scalar_bool(query.limit) or type(query.limit) is not int
                or not 1 <= query.limit <= MAX_PAGE_LIMIT):
            raise ControlError(400, 'INVALID_PAGE_LIMIT')
        if (query.maximum_age_ms is not None
                and (scalar_bool(query.maximum_age_ms)
                     or type(query.maximum_age_ms) is not int
                     or query.maximum_age_ms < 0)):
            raise ControlError(400, 'INVALID_MAXIMUM_AGE')
        owner = query.owner_strategy_id
        if owner is not None and (type(owner) is not str or not owner.strip()):
            raise ControlError(400, 'INVALID_OWNER_FILTER')
        _authorize(query.scope, actor)
        stream_id = projection_stream_id(query.scope, query.resource)
        watermark = self.store.watermark(query.scope)
        _check_watermark(watermark)
        _check_cursor(query, stream_id, watermark)
        observed = _now(self.clock)

        positions: tuple[t.PositionProjection, ...] = ()
        orders: tuple[t.OrderProjection, ...] = ()
        risk_locks: tuple[t.RiskLock, ...] = ()
        controls: tuple[t.ControlCommandProjection, ...] = ()
        intents: tuple[t.TradeIntent, ...] = ()
        truncated = False
        if query.resource == 'positions':
            records = tuple(self.store.positions(query.scope, owner))
            for item in records:
                if (not isinstance(item, t.PositionProjection)
                        or item.position.scope != query.scope
                        or (item.protection is not None
                            and item.protection.scope != query.scope)):
                    raise _scope_violation()
                if owner is not None and item.position.owner_strategy_id != owner:
                    raise _scope_violation()
            positions, truncated = _slice(records, query.limit,
                                          lambda item: item.position.position_id)
        elif query.resource == 'orders':
            records = tuple(self.store.orders(query.scope, owner))
            for item in records:
                if (not isinstance(item, t.OrderProjection)
                        or item.order.identity.scope != query.scope):
                    raise _scope_violation()
                if owner is not None and item.order.owner_strategy_id != owner:
                    raise _scope_violation()
            orders, truncated = _slice(records, query.limit,
                                       lambda item: item.order.identity.logical_client_order_id)
        elif query.resource == 'risk-locks':
            records = tuple(self.store.risk_locks(query.scope, owner))
            for item in records:
                if not isinstance(item, t.RiskLock) or item.scope != query.scope:
                    raise _scope_violation()
            # A risk lock has no strategy owner; all account scoped locks remain
            # visible even when filtering another collection by owner.
            risk_locks, truncated = _slice(records, query.limit,
                                           lambda item: item.lock_id)
        elif query.resource == 'control-commands':
            records = tuple(self.store.controls(query.scope, owner))
            for item in records:
                if (not isinstance(item, t.ControlCommandProjection)
                        or item.request.scope != query.scope):
                    raise _scope_violation()
            controls, truncated = _slice(records, query.limit,
                                         lambda item: item.receipt.command_id)
        else:
            records = tuple(self.store.intents(query.scope, owner))
            for item in records:
                if (not isinstance(item, t.TradeIntent)
                        or item.environment != str(query.scope.environment)
                        or item.venue_account_id != query.scope.venue_account_id
                        or item.product != str(query.scope.product)
                        or item.instrument_uid != query.scope.instrument_uid
                        or item.venue_leg != str(query.scope.venue_leg)
                        or (owner is not None and item.owner_strategy_id != owner)):
                    raise _scope_violation()
            intents, truncated = _slice(records, query.limit,
                                        lambda item: item.intent_id)

        extra = ('PAGE_TRUNCATED',) if truncated else ()
        freshness = _freshness(watermark, query.maximum_age_ms, observed, extra)
        next_cursor = None
        if watermark is not None:
            next_cursor = t.StreamCursor(scope=query.scope,
                                         sequence=watermark.sequence,
                                         stream_id=stream_id)
        return t.ProjectionPage(scope=query.scope, positions=positions,
            orders=orders, risk_locks=risk_locks, controls=controls,
            intents=intents, next_cursor=next_cursor, freshness=freshness)


# --- Audit redaction -------------------------------------------------------

_SECRET_PARTS = (
    'secret', 'signature', 'password', 'passwd', 'token', 'credential',
    'apikey', 'privatekey', 'mnemonic', 'passphrase', 'cookie',
    'authorization', 'authheader', 'session',
)
_IDENTITY_KEYS = frozenset((
    'identityid', 'actorid', 'userid', 'email', 'phone', 'phonenumber',
    'fullname', 'name', 'username', 'displayname', 'owneridentity',
))
_PROMPT_KEYS = frozenset((
    'prompt', 'prompts', 'prompttext', 'modelinput', 'messages',
))


def _normalize_key(key: str) -> str:
    return key.lower().replace('-', '').replace('_', '')


def _digest(value: object) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode('utf-8')
    else:
        try:
            raw = canonical_json(value).encode('utf-8') if isinstance(value, dict) else repr(value).encode('utf-8')
        except (TypeError, ValueError):
            raw = repr(value).encode('utf-8')
    return sha256(raw).hexdigest()


class AuditRedactor:
    """Remove credential material, prompts, and full identities from reads.

    Redaction is applied recursively to every returned audit detail. A prompt is
    represented only by a content digest; identity values are replaced by a
    salted pseudonym. Unknown nested objects are rejected rather than passed
    through with an unverifiable contract.
    """

    def __init__(self, *, identity_salt: str = 'ip-28-audit-v1'):
        self.identity_salt = identity_salt

    def pseudonym(self, value: object, *, kind: str = 'identity') -> str:
        raw = (self.identity_salt + ':' + kind + ':'
               + (value if isinstance(value, str) else repr(value)))
        return 'pseudonym:' + sha256(raw.encode('utf-8')).hexdigest()[:16]

    def redact(self, value: object) -> object:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError('audit keys must be strings')
                normalized = _normalize_key(key)
                if any(part in normalized for part in _SECRET_PARTS):
                    result[key] = '[REDACTED]'
                elif normalized in _PROMPT_KEYS or 'prompt' in normalized:
                    result[key] = {'redacted': 'PROMPT_HASH',
                                   'sha256': _digest(item)}
                elif normalized in _IDENTITY_KEYS:
                    result[key] = self.pseudonym(item)
                else:
                    result[key] = self.redact(item)
            return result
        if isinstance(value, (list, tuple)):
            return [self.redact(item) for item in value]
        if value is None or type(value) in (str, bool, int):
            return value
        if isinstance(value, Decimal):
            return format(value, 'f')
        raise ValueError('unsupported value in audit payload')


@dataclass(frozen=True, kw_only=True)
class AuditEntry:
    entry_id: str
    scope: t.ScopeKey
    occurred_at: str
    actor_id: str
    action: str
    outcome: str
    details: dict


class AuditReader(Protocol):
    def read(self, scope: t.ScopeKey, limit: int) -> tuple[AuditEntry, ...]: ...


class AuditProjection:
    """Read-only redacted audit view; never returns model prompts or secrets."""

    def __init__(self, reader: AuditReader, *, redactor: AuditRedactor | None = None):
        self.reader = reader
        self.redactor = redactor or AuditRedactor()

    def read(self, scope: t.ScopeKey, actor: t.ActorContext,
             limit: int = 100) -> tuple[dict, ...]:
        _authorize(scope, actor)
        if scalar_bool(limit) or type(limit) is not int or not 1 <= limit <= MAX_PAGE_LIMIT:
            raise ControlError(400, 'INVALID_PAGE_LIMIT')
        entries = tuple(self.reader.read(scope, limit))
        rows = []
        for entry in entries:
            if not isinstance(entry, AuditEntry) or entry.scope != scope:
                raise _scope_violation()
            try:
                utc_timestamp(entry.occurred_at)
            except ValueError as exc:
                raise ControlError(503, 'AUDIT_PROJECTION_INVALID') from exc
            if entry.action not in ('MODEL_CALL', 'CONTROL', 'RISK', 'PROTECTION',
                                    'EXECUTION', 'RECONCILIATION'):
                raise ControlError(503, 'AUDIT_PROJECTION_INVALID')
            rows.append({
                'entry_id': entry.entry_id,
                'occurred_at': entry.occurred_at,
                'action': entry.action,
                'outcome': entry.outcome,
                'actor': self.redactor.pseudonym(entry.actor_id, kind='actor'),
                'details': self.redactor.redact(entry.details),
            })
        rows.sort(key=lambda row: (row['occurred_at'], row['entry_id']))
        return tuple(rows[:limit])
