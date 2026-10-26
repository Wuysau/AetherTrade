"""Terminal control client for the frozen IP-27/IP-28 control wire.

The terminal is a *client*: it submits the approved control actions and reads
server projections. It never holds an exchange API key, signs a venue request,
computes balances or positions, or turns a receipt into a fill/exit claim.

Design boundaries
-----------------
* Only a revocable application session token is accepted. It is sent solely as
  an ``Authorization: Bearer`` header; no venue credentials or signatures exist
  in this module.
* A client instance is bound to exactly one ``ScopeKey``. Cross-scope and LIVE
  requests are rejected before any transport call (the server still re-checks).
* ``command`` reuses the exact Web idempotency contract: the same
  ``Idempotency-Key`` plus body resolves to the same server command. A repeat
  submission first reads the known command; a timed-out write is retried with
  the identical key/body so the server returns the original receipt instead of
  creating a second command. An unresolved outcome is reported as unknown, not
  as success.
* Projection amount fields stay ``Decimal`` values decoded from wire strings.
* ``follow`` is a bounded catch-up iterator. A gap is surfaced through
  ``stream_state`` and stops iteration; it never skips ahead to "latest".
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import socket
import ssl
from typing import Iterator, Protocol
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import (
    canonical_hash,
    canonical_json,
    domain_payload,
)
from trading_system.contracts.wire import ContractCodec
from trading_system.storage.models import decode


MAX_TOKEN_LENGTH = 4096
MAX_IDEMPOTENCY_KEY = 256
DEFAULT_MAX_EVENT_PAGES = 1000
RESPONSE_LIMIT = 4 * 1024 * 1024

SCOPE_QUERY_FIELDS = ('environment', 'venue_account_id', 'product',
                      'instrument_uid', 'venue_leg')
QUERY_PATHS = {
    'positions': '/api/positions',
    'orders': '/api/orders',
    'risk-locks': '/api/risk-locks',
}
EVENT_PATH = '/api/events'
CONTROL_PATH = '/api/control-commands'

_CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f]')
_SAFE_ID = re.compile(r'[^/\\?#\s]{1,256}\Z')


# --- Errors -----------------------------------------------------------------


class TerminalError(Exception):
    """Structured control error from the local client or the server."""

    def __init__(self, status: int, code: str, *, detail: str | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail


class TerminalInvalid(TerminalError):
    """Malformed request; nothing was submitted."""


class TerminalDenied(TerminalError):
    """Missing, revoked, cross-scope or LIVE authority."""


class TerminalConflict(TerminalError):
    """Idempotency conflict, stale revision/epoch or cursor conflict."""


class TerminalNotFound(TerminalError):
    """The requested control command is not visible in this scope."""


class TerminalUnavailable(TerminalError):
    """Transport/dependency unavailable; no outcome may be claimed."""


class TerminalOutcomeUnknown(TerminalUnavailable):
    """A write may have reached the server but no receipt was observed."""

    def __init__(self, *, key: str, payload_hash: str):
        super().__init__(503, 'COMMAND_OUTCOME_UNKNOWN',
                         detail='query the original command before retrying')
        self.key = key
        self.payload_hash = payload_hash


class TerminalProtocolError(TerminalError):
    """The server response did not match the frozen control contract."""


class TransportError(Exception):
    """Base class for injected/built-in transport failures."""


class TransportTimeout(TransportError):
    """The request may have reached the server but no response was observed."""


class TransportUnavailable(TransportError):
    """The request could not be sent or no usable response was received."""


# --- Session token ----------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlToken:
    """Opaque, revocable application session token.

    This is deliberately not a venue credential object: it has no key/secret
    pair, no signing method and no account identifier. The server resolves the
    identity, environment and scope from its own session store.
    """

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TerminalInvalid(401, 'SESSION_REQUIRED')
        if (not self.value or len(self.value) > MAX_TOKEN_LENGTH
                or self.value.strip() != self.value
                or _CONTROL_CHARS.search(self.value)
                or any(ch.isspace() for ch in self.value)):
            raise TerminalInvalid(401, 'SESSION_REQUIRED')

    @property
    def authorization(self) -> str:
        return 'Bearer ' + self.value

    def redacted(self) -> str:
        digest = sha256(self.value.encode('utf-8')).hexdigest()[:12]
        return 'ControlToken(sha256:' + digest + ')'

    def __repr__(self) -> str:
        return self.redacted()

    __str__ = __repr__


# --- Transport --------------------------------------------------------------


class ControlTransport(Protocol):
    """Synchronous transport seam.

    Returns ``(status, decoded_json_object)``. Implementations must not log or
    persist request headers, because the session token is a credential.
    """

    def send(self, method: str, path: str, *, headers: dict[str, str],
             body: dict | None = None,
             query: dict[str, str] | None = None) -> tuple[int, dict]: ...


class _NoRedirect(urlrequest.HTTPRedirectHandler):
    """Never forward a bearer token to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_object(payload: bytes) -> dict:
    def unique(pairs):
        values: dict = {}
        for key, value in pairs:
            if key in values:
                raise ValueError('duplicate JSON field')
            values[key] = value
        return values

    value = json.loads(payload.decode('utf-8'), object_pairs_hook=unique)
    if type(value) is not dict:
        raise ValueError('expected JSON object')
    return value


class HttpsControlTransport:
    """Minimal stdlib HTTPS transport with conservative defaults.

    * HTTPS only; no HTTP fallback and no credentials embedded in the URL.
    * Environment proxies are ignored so a token is not silently sent through
      an untrusted proxy.
    * Redirects are refused.
    * Responses are size limited.
    * Network errors become typed transport failures; this class never retries
      a write by itself.
    """

    def __init__(self, base_url: str, *, timeout: float = 15.0):
        parsed = urlparse.urlsplit(base_url)
        if (parsed.scheme != 'https' or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError('control transport requires a plain https base URL')
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._opener = urlrequest.build_opener(
            urlrequest.ProxyHandler({}),
            _NoRedirect(),
            urlrequest.HTTPSHandler(context=ssl.create_default_context()),
        )

    def send(self, method: str, path: str, *, headers: dict[str, str],
             body: dict | None = None,
             query: dict[str, str] | None = None) -> tuple[int, dict]:
        url = self.base_url + path
        if query:
            url += '?' + urlparse.urlencode(sorted(query.items()))
        data = None
        if body is not None:
            data = canonical_json(body).encode('utf-8')
        request = urlrequest.Request(url, data=data, method=method.upper(),
                                     headers=dict(headers))
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(RESPONSE_LIMIT + 1)
                if len(raw) > RESPONSE_LIMIT:
                    raise TerminalProtocolError(502, 'RESPONSE_TOO_LARGE')
                return response.status, _json_object(raw)
        except urlerror.HTTPError as exc:
            try:
                raw = exc.read(RESPONSE_LIMIT + 1)
                if len(raw) > RESPONSE_LIMIT:
                    raise TerminalProtocolError(502, 'RESPONSE_TOO_LARGE') from exc
                payload = _json_object(raw) if raw else {}
            except (ValueError, UnicodeError):
                payload = {}
            return exc.code, payload
        except (socket.timeout, TimeoutError) as exc:
            raise TransportTimeout('control request timed out') from exc
        except urlerror.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise TransportTimeout('control request timed out') from exc
            raise TransportUnavailable('control API unavailable') from exc
        except (ValueError, UnicodeError) as exc:
            raise TerminalProtocolError(502, 'INVALID_RESPONSE') from exc


# --- Journals, cursor and state --------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class JournalEntry:
    command_id: str
    payload_hash: str


class CommandJournal(Protocol):
    """Local memory of submitted idempotency keys, never an authority source."""

    def get(self, identity: tuple[str, str, str]) -> JournalEntry | None: ...

    def put(self, identity: tuple[str, str, str], entry: JournalEntry) -> None: ...

    def drop(self, identity: tuple[str, str, str]) -> None: ...


class MemoryCommandJournal:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], JournalEntry] = {}

    def get(self, identity: tuple[str, str, str]) -> JournalEntry | None:
        return self._entries.get(identity)

    def put(self, identity: tuple[str, str, str], entry: JournalEntry) -> None:
        self._entries[identity] = entry

    def drop(self, identity: tuple[str, str, str]) -> None:
        self._entries.pop(identity, None)


def encode_cursor(cursor: t.StreamCursor) -> str:
    """Lossless one-string cursor representation for the query parameter.

    The frozen OpenAPI declares the ``cursor`` parameter as a string while
    page/event bodies carry a ``StreamCursor`` object. Host adapters must use
    this same codec; a mismatch is an integration blocker and must not be
    papered over by silently dropping scope or stream identity.
    """
    if not isinstance(cursor, t.StreamCursor):
        raise TerminalInvalid(400, 'INVALID_CURSOR')
    return canonical_json(domain_payload(cursor))


def decode_cursor(value: str) -> t.StreamCursor:
    if type(value) is not str or not value:
        raise TerminalInvalid(400, 'INVALID_CURSOR')
    try:
        payload = json.loads(value)
    except (ValueError, UnicodeError) as exc:
        raise TerminalInvalid(400, 'INVALID_CURSOR') from exc
    try:
        cursor = decode(t.StreamCursor, payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise TerminalInvalid(400, 'INVALID_CURSOR') from exc
    if cursor.sequence < 0 or not cursor.stream_id:
        raise TerminalInvalid(400, 'INVALID_CURSOR')
    return cursor


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamState:
    cursor: t.StreamCursor | None
    gap: bool
    requires_snapshot: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingCommand:
    """A write whose server outcome is not yet known."""

    key: str
    action: str
    payload_hash: str
    reason: str


# --- Response decoding ------------------------------------------------------


def _require_object(payload: object, fields: set[str], code: str) -> dict:
    if type(payload) is not dict or set(payload) != fields:
        raise TerminalProtocolError(502, code)
    return payload


def _decode_receipt(payload: dict) -> t.ControlCommandReceipt:
    _require_object(payload, {'command_id', 'status', 'reason',
                              'existing_result_ref'}, 'INVALID_RECEIPT')
    try:
        receipt = decode(t.ControlCommandReceipt, payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise TerminalProtocolError(502, 'INVALID_RECEIPT') from exc
    if (not receipt.command_id or receipt.status not in
            ('QUEUED', 'APPLIED', 'REJECTED', 'IN_PROGRESS')):
        raise TerminalProtocolError(502, 'INVALID_RECEIPT')
    return receipt


def _decode_projection(payload: dict) -> t.ControlCommandProjection:
    _require_object(payload, {'receipt', 'request', 'payload_hash',
                              'idempotency_key', 'sequence',
                              'incomplete_reasons'}, 'INVALID_CONTROL_PROJECTION')
    try:
        projection = decode(t.ControlCommandProjection, payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise TerminalProtocolError(502, 'INVALID_CONTROL_PROJECTION') from exc
    if type(projection.sequence) is not int or projection.sequence < 0:
        raise TerminalProtocolError(502, 'INVALID_CONTROL_PROJECTION')
    return projection


def _decode_page(payload: dict) -> t.ProjectionPage:
    _require_object(payload, {'scope', 'positions', 'orders', 'risk_locks',
                              'controls', 'intents', 'next_cursor',
                              'freshness'}, 'INVALID_PROJECTION_PAGE')
    try:
        cursor = (None if payload['next_cursor'] is None
                  else decode(t.StreamCursor, payload['next_cursor']))
        page = t.ProjectionPage(
            scope=decode(t.ScopeKey, payload['scope']),
            positions=tuple(decode(t.PositionProjection, item)
                            for item in payload['positions']),
            orders=tuple(decode(t.OrderProjection, item)
                         for item in payload['orders']),
            risk_locks=tuple(decode(t.RiskLock, item)
                             for item in payload['risk_locks']),
            controls=tuple(decode(t.ControlCommandProjection, item)
                           for item in payload['controls']),
            intents=tuple(ContractCodec().decode('trade-intent', item)
                          for item in payload['intents']),
            next_cursor=cursor,
            freshness=decode(t.FreshnessView, payload['freshness']),
        )
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        raise TerminalProtocolError(502, 'INVALID_PROJECTION_PAGE') from exc
    if type(page.freshness.age_ms) is not int or page.freshness.age_ms < 0:
        raise TerminalProtocolError(502, 'INVALID_PROJECTION_PAGE')
    return page


def _decode_event_page(payload: dict) -> t.EventPage:
    _require_object(payload, {'events', 'next_cursor', 'gap',
                              'requires_snapshot'}, 'INVALID_EVENT_PAGE')
    try:
        page = decode(t.EventPage, payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise TerminalProtocolError(502, 'INVALID_EVENT_PAGE') from exc
    if page.gap and not page.requires_snapshot:
        raise TerminalProtocolError(502, 'INVALID_EVENT_PAGE')
    return page


# --- Client -----------------------------------------------------------------


class TerminalClient:
    """Implementation of the frozen ``TerminalClient`` port."""

    def __init__(self, *, transport: ControlTransport, token: ControlToken,
                 scope: t.ScopeKey, journal: CommandJournal | None = None,
                 max_timeout_retries: int = 1):
        if transport is None or not callable(getattr(transport, 'send', None)):
            raise TerminalInvalid(400, 'INVALID_TRANSPORT')
        if not isinstance(token, ControlToken):
            raise TerminalInvalid(401, 'SESSION_REQUIRED')
        self._validate_scope(scope)
        if (type(max_timeout_retries) is not int
                or not 0 <= max_timeout_retries <= 5):
            raise TerminalInvalid(400, 'INVALID_RETRY_POLICY')
        self.transport = transport
        self.token = token
        self.scope = scope
        self.journal = journal or MemoryCommandJournal()
        self.max_timeout_retries = max_timeout_retries
        self._pending: list[PendingCommand] = []
        self._stream_state = StreamState(cursor=None, gap=False,
                                         requires_snapshot=False)

    # -- read-only state ----------------------------------------------------

    @property
    def pending(self) -> tuple[PendingCommand, ...]:
        return tuple(self._pending)

    @property
    def stream_state(self) -> StreamState:
        return self._stream_state

    # -- validation ---------------------------------------------------------

    def _validate_scope(self, scope: object) -> None:
        if not isinstance(scope, t.ScopeKey):
            raise TerminalInvalid(400, 'INVALID_SCOPE')
        if scope.environment == t.Environment.LIVE:
            raise TerminalDenied(403, 'LIVE_NOT_QUALIFIED')

    def _bind_scope(self, scope: t.ScopeKey) -> None:
        self._validate_scope(scope)
        if scope != self.scope:
            raise TerminalDenied(403, 'AUTHORIZATION_SCOPE_MISMATCH')

    @staticmethod
    def _scope_hash(scope: t.ScopeKey) -> str:
        return canonical_hash(domain_payload(scope))

    def _scope_query(self) -> dict[str, str]:
        return {
            'environment': str(self.scope.environment),
            'venue_account_id': self.scope.venue_account_id,
            'product': str(self.scope.product),
            'instrument_uid': self.scope.instrument_uid,
            'venue_leg': str(self.scope.venue_leg),
        }

    def _base_headers(self) -> dict[str, str]:
        return {'authorization': self.token.authorization,
                'accept': 'application/json'}

    def _validate_command(self, request: object, key: object) -> None:
        if not isinstance(request, t.ControlCommandRequest):
            raise TerminalInvalid(400, 'INVALID_CONTROL_REQUEST')
        if type(key) is not str or not key.strip() or len(key) > MAX_IDEMPOTENCY_KEY:
            raise TerminalInvalid(400, 'INVALID_IDEMPOTENCY_KEY')
        self._bind_scope(request.scope)
        if request.action not in ('pause_new', 'cancel_entries', 'exit_owned',
                                  'transfer', 'reconcile'):
            raise TerminalInvalid(422, 'INVALID_CONTROL_REQUEST')
        if (type(request.expected_revision) is not int
                or request.expected_revision < 0
                or type(request.ownership_epoch) is not int
                or request.ownership_epoch < 1):
            raise TerminalInvalid(422, 'INVALID_CONTROL_REQUEST')
        if request.action == 'transfer':
            transfer = request.transfer
            if (transfer is None or transfer.scope != request.scope
                    or transfer.ownership_epoch != request.ownership_epoch):
                raise TerminalInvalid(422, 'INVALID_TRANSFER')
        elif request.transfer is not None:
            raise TerminalInvalid(422, 'UNEXPECTED_TRANSFER')

    def _validate_cursor(self, cursor: object) -> None:
        if not isinstance(cursor, t.StreamCursor):
            raise TerminalInvalid(400, 'INVALID_CURSOR')
        if (type(cursor.sequence) is not int or cursor.sequence < 0
                or not cursor.stream_id):
            raise TerminalInvalid(400, 'INVALID_CURSOR')
        if cursor.scope != self.scope:
            raise TerminalDenied(403, 'CURSOR_SCOPE_MISMATCH')

    # -- error mapping ------------------------------------------------------

    @staticmethod
    def _reason(payload: dict) -> str:
        code = payload.get('reason_code') if type(payload) is dict else None
        return code if type(code) is str and code else 'CONTROL_REQUEST_FAILED'

    def _raise_status(self, status: int, payload: dict) -> None:
        code = self._reason(payload)
        if status == 401:
            raise TerminalDenied(401, code)
        if status == 403:
            raise TerminalDenied(403, code)
        if status == 404:
            raise TerminalNotFound(404, code)
        if status == 409:
            raise TerminalConflict(409, code)
        if status in (400, 422):
            raise TerminalInvalid(status, code)
        if status == 503:
            raise TerminalUnavailable(503, code)
        raise TerminalProtocolError(502, 'UNEXPECTED_STATUS:' + str(status))

    def _record_pending(self, key: str, action: str, digest: str,
                        reason: str) -> None:
        for item in self._pending:
            if (item.key == key and item.action == action
                    and item.payload_hash == digest):
                return
        self._pending.append(PendingCommand(key=key, action=action,
                                            payload_hash=digest,
                                            reason=reason))

    def _clear_pending(self, key: str, action: str, digest: str) -> None:
        self._pending = [item for item in self._pending
                         if not (item.key == key and item.action == action
                                 and item.payload_hash == digest)]

    # -- control write ------------------------------------------------------

    def command(self, request: t.ControlCommandRequest,
                key: str) -> t.ControlCommandReceipt:
        self._validate_command(request, key)
        body = domain_payload(request)
        digest = canonical_hash({'operation': 'enqueueControl', 'body': body})
        identity = (request.action, self._scope_hash(self.scope), key)

        # A repeat click/restart looks up the original command first.
        entry = self.journal.get(identity)
        if entry is not None and entry.payload_hash == digest:
            projection = self._lookup(entry.command_id, key=key, digest=digest)
            if projection is not None:
                if (projection.payload_hash != digest
                        or projection.idempotency_key != key):
                    raise TerminalProtocolError(502, 'IDEMPOTENCY_PROJECTION_MISMATCH')
                self._clear_pending(key, request.action, digest)
                return projection.receipt

        headers = self._base_headers()
        headers.update({'content-type': 'application/json',
                        'idempotency-key': key,
                        'if-match': str(request.expected_revision)})
        attempts = self.max_timeout_retries + 1
        for _ in range(attempts):
            try:
                status, payload = self.transport.send(
                    'POST', CONTROL_PATH, headers=headers, body=body)
            except TransportTimeout:
                # The write may or may not have committed. Never invent a new
                # command: resolve a known command, otherwise replay the exact
                # same key/body so the server returns the original receipt.
                known = self.journal.get(identity)
                if known is not None:
                    projection = self._lookup(known.command_id, key=key,
                                              digest=digest)
                    if projection is not None:
                        return projection.receipt
                continue
            except TransportUnavailable as exc:
                raise TerminalUnavailable(503, 'CONTROL_API_UNAVAILABLE') from exc

            if status == 202:
                receipt = _decode_receipt(payload)
                self.journal.put(identity, JournalEntry(
                    command_id=receipt.command_id, payload_hash=digest))
                self._clear_pending(key, request.action, digest)
                return receipt
            self._raise_status(status, payload)

        self._record_pending(key, request.action, digest,
                             'COMMAND_OUTCOME_UNKNOWN')
        raise TerminalOutcomeUnknown(key=key, payload_hash=digest)

    def command_status(self, command_id: str) -> t.ControlCommandProjection:
        if type(command_id) is not str or not _SAFE_ID.fullmatch(command_id):
            raise TerminalInvalid(400, 'INVALID_COMMAND_ID')
        projection = self._lookup(command_id, key=None, digest=None)
        if projection is None:
            raise TerminalNotFound(404, 'CONTROL_NOT_FOUND')
        return projection

    def _lookup(self, command_id: str, *, key: str | None,
                digest: str | None) -> t.ControlCommandProjection | None:
        try:
            status, payload = self.transport.send(
                'GET', CONTROL_PATH + '/' + command_id,
                headers=self._base_headers(), query=self._scope_query())
        except TransportTimeout as exc:
            if key is not None and digest is not None:
                raise TerminalUnavailable(
                    503, 'COMMAND_STATUS_TIMEOUT',
                    detail='query the original command before retrying') from exc
            raise TerminalUnavailable(503, 'COMMAND_STATUS_TIMEOUT') from exc
        except TransportUnavailable as exc:
            raise TerminalUnavailable(503, 'CONTROL_API_UNAVAILABLE') from exc
        if status == 404:
            return None
        if status != 200:
            self._raise_status(status, payload)
        projection = _decode_projection(payload)
        if projection.request.scope != self.scope:
            raise TerminalProtocolError(502, 'CONTROL_SCOPE_VIOLATION')
        return projection

    # -- projections --------------------------------------------------------

    def query(self, request: t.ProjectionQueryRequest) -> t.ProjectionPage:
        if not isinstance(request, t.ProjectionQueryRequest):
            raise TerminalInvalid(400, 'INVALID_PROJECTION_QUERY')
        self._bind_scope(request.scope)
        path = QUERY_PATHS.get(request.resource)
        if path is None:
            # There is no frozen list endpoint for intents/control-commands.
            raise TerminalInvalid(400, 'UNSUPPORTED_PROJECTION_RESOURCE')
        if (type(request.limit) is not int or not 1 <= request.limit <= 1000):
            raise TerminalInvalid(400, 'INVALID_PAGE_LIMIT')
        if request.maximum_age_ms is not None and (
                type(request.maximum_age_ms) is not int
                or request.maximum_age_ms < 0):
            raise TerminalInvalid(400, 'INVALID_MAXIMUM_AGE')
        if request.owner_strategy_id is not None and (
                type(request.owner_strategy_id) is not str
                or not request.owner_strategy_id.strip()):
            raise TerminalInvalid(400, 'INVALID_OWNER_FILTER')
        if request.cursor is not None:
            self._validate_cursor(request.cursor)

        params = self._scope_query()
        if request.owner_strategy_id is not None:
            params['owner_strategy_id'] = request.owner_strategy_id
        if request.cursor is not None:
            params['cursor'] = encode_cursor(request.cursor)
        params['limit'] = str(request.limit)
        if request.maximum_age_ms is not None:
            params['maximum_age_ms'] = str(request.maximum_age_ms)

        try:
            status, payload = self.transport.send(
                'GET', path, headers=self._base_headers(), query=params)
        except TransportTimeout as exc:
            raise TerminalUnavailable(503, 'PROJECTION_TIMEOUT') from exc
        except TransportUnavailable as exc:
            raise TerminalUnavailable(503, 'CONTROL_API_UNAVAILABLE') from exc
        if status != 200:
            self._raise_status(status, payload)
        page = _decode_page(payload)
        if page.scope != request.scope:
            raise TerminalProtocolError(502, 'PROJECTION_SCOPE_VIOLATION')
        if page.next_cursor is not None and page.next_cursor.scope != request.scope:
            raise TerminalProtocolError(502, 'PROJECTION_SCOPE_VIOLATION')
        return page

    # -- events -------------------------------------------------------------

    def follow(self, cursor: t.StreamCursor | None
               ) -> Iterator[t.ProjectionEvent]:
        if cursor is not None:
            self._validate_cursor(cursor)
        return self._follow(cursor)

    def _follow(self, cursor: t.StreamCursor | None
                ) -> Iterator[t.ProjectionEvent]:
        current = cursor
        for _ in range(DEFAULT_MAX_EVENT_PAGES):
            params = self._scope_query()
            if current is not None:
                params['cursor'] = encode_cursor(current)
            try:
                status, payload = self.transport.send(
                    'GET', EVENT_PATH, headers=self._base_headers(),
                    query=params)
            except TransportTimeout as exc:
                raise TerminalUnavailable(503, 'EVENT_STREAM_TIMEOUT') from exc
            except TransportUnavailable as exc:
                raise TerminalUnavailable(503, 'CONTROL_API_UNAVAILABLE') from exc
            if status != 200:
                self._raise_status(status, payload)
            page = _decode_event_page(payload)
            if page.next_cursor.scope != self.scope:
                raise TerminalProtocolError(502, 'EVENT_SCOPE_VIOLATION')
            self._stream_state = StreamState(cursor=page.next_cursor,
                                             gap=page.gap,
                                             requires_snapshot=page.requires_snapshot)
            if page.gap:
                # The client must obtain a new snapshot. Never fabricate a
                # continuous stream or linger on the previous cursor.
                return
            if not page.events:
                return
            previous = None if current is None else current.sequence
            for item in page.events:
                if item.cursor.scope != self.scope:
                    raise TerminalProtocolError(502, 'EVENT_SCOPE_VIOLATION')
                if item.cursor.sequence <= (previous or 0):
                    raise TerminalProtocolError(502, 'EVENT_ORDER_VIOLATION')
                previous = item.cursor.sequence
                yield item
            if page.requires_snapshot:
                return
            current = page.next_cursor


# --- Helpers for callers ----------------------------------------------------


def scope_query(scope: t.ScopeKey) -> dict[str, str]:
    """Public scope query builder, kept identical to the client's binding."""
    if not isinstance(scope, t.ScopeKey):
        raise TerminalInvalid(400, 'INVALID_SCOPE')
    return {
        'environment': str(scope.environment),
        'venue_account_id': scope.venue_account_id,
        'product': str(scope.product),
        'instrument_uid': scope.instrument_uid,
        'venue_leg': str(scope.venue_leg),
    }
