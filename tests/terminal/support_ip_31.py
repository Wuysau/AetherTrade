"""Offline IP-31 terminal probes over the real IP-27/IP-28 services.

This is a **test-only** composition layer. It routes terminal HTTP-shaped
requests to the production control inbox/auth API and to the production
projection/event services backed by temporary SQLite adapters. It is not a
PostgreSQL server, an HTTPS listener, a venue connection or native/process
evidence.

The transport records every request so tests can assert that the terminal sent
only a revocable session token (never exchange credentials) and that closing the
UI sent no control command. No fixture returns a fixed PASS value.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from api.support_ip_27 import ApiCase
from api.support_ip_28 import (
    HealthCase,
    ProjectionCase,
    SCOPE_A,
    StreamCase,
    make_position,
    make_position_page,
    make_protection,
)
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.control.auth import ControlError
from trading_system.terminal.client import (
    ControlToken,
    QUERY_PATHS,
    TerminalClient,
    TransportTimeout,
    TransportUnavailable,
    decode_cursor,
)
from trading_system.terminal.tui import ControlBinding, TerminalSession


class OfflineTerminalTransport:
    """HTTP-shaped adapter over the frozen IP-27/IP-28 production services."""

    def __init__(self, *, api: ApiCase, projection: ProjectionCase,
                 streams: StreamCase, actor: t.ActorContext,
                 scope: t.ScopeKey):
        self.api = api
        self.projection = projection
        self.streams = streams
        self.actor = actor
        self.scope = scope
        self.requests: list[dict] = []
        self.command_posts: list[dict] = []
        self.always_timeout = False
        self.timeout_after_commit = 0
        self.always_fail_reads = False

    # -- helpers ------------------------------------------------------------

    def _record(self, method, path, headers, body, query):
        record = {
            'method': method.upper(),
            'path': path,
            'headers': dict(headers),
            'body': body,
            'query': dict(query or {}),
        }
        self.requests.append(record)
        return record

    def _scope(self, query) -> t.ScopeKey:
        try:
            return t.ScopeKey(
                environment=t.Environment(query['environment']),
                venue_account_id=query['venue_account_id'],
                product=t.Product(query['product']),
                instrument_uid=query['instrument_uid'],
                venue_leg=query['venue_leg'])
        except (KeyError, ValueError) as exc:
            raise AssertionError('terminal sent an invalid scope query') from exc

    def _authorize(self, scope) -> tuple[int, dict] | None:
        # Mirror the server-side binding so a cross-account read cannot use the
        # in-process projection service as an oracle.
        if scope not in self.actor.scopes or scope.environment != self.actor.environment:
            return self._error(403, 'AUTHORIZATION_SCOPE_MISMATCH')
        return None

    @staticmethod
    def _error(status: int, code: str) -> tuple[int, dict]:
        # Same error envelope as the production control adapter.
        return status, {'reason_code': code, 'message': code,
                        'correctable_fields': [], 'allowed_actions': []}

    def _service_error(self, exc: ControlError) -> tuple[int, dict]:
        return self._error(exc.status, exc.code)

    # -- transport ----------------------------------------------------------

    def send(self, method, path, *, headers, body=None, query=None):
        record = self._record(method, path, headers, body, query)
        method = method.upper()

        if method == 'POST' and path == '/api/control-commands':
            self.command_posts.append(record)
            if self.always_timeout:
                raise TransportTimeout('injected before server dispatch')
            status, payload = self.api.app.dispatch(
                'POST', path, scheme='https', headers=headers, body=body)
            if self.timeout_after_commit:
                self.timeout_after_commit -= 1
                raise TransportTimeout('injected after server commit')
            return status, payload

        if method == 'GET' and path.startswith('/api/control-commands/'):
            if self.always_fail_reads:
                raise TransportUnavailable('injected read outage')
            return self.api.app.dispatch(
                'GET', path, scheme='https', headers=headers, query=query)

        if method == 'GET' and self.always_fail_reads:
            raise TransportUnavailable('injected read outage')

        if method == 'GET' and path in QUERY_PATHS.values():
            scope = self._scope(query)
            denied = self._authorize(scope)
            if denied is not None:
                return denied
            resource = {value: key for key, value in QUERY_PATHS.items()}[path]
            cursor = (decode_cursor(query['cursor'])
                      if query.get('cursor') else None)
            request = t.ProjectionQueryRequest(
                scope=scope, resource=resource,
                owner_strategy_id=query.get('owner_strategy_id'),
                cursor=cursor,
                limit=int(query.get('limit', 100)),
                maximum_age_ms=(int(query['maximum_age_ms'])
                                if query.get('maximum_age_ms') else None))
            try:
                page = self.projection.query.read(request, self.actor)
            except ControlError as exc:
                return self._service_error(exc)
            return 200, domain_payload(page)

        if method == 'GET' and path == '/api/events':
            scope = self._scope(query)
            denied = self._authorize(scope)
            if denied is not None:
                return denied
            cursor = (decode_cursor(query['cursor'])
                      if query.get('cursor') else None)
            try:
                page = self.streams.stream.read(cursor, scope)
            except ControlError as exc:
                return self._service_error(exc)
            return 200, domain_payload(page)

        return 404, {'reason_code': 'RESOURCE_NOT_FOUND',
                     'message': 'RESOURCE_NOT_FOUND',
                     'correctable_fields': [], 'allowed_actions': []}


class GuardedEnviron(dict):
    """Environment view that records reads and fails on credential access."""

    def __init__(self, values):
        super().__init__(values)
        self.reads: list[str] = []

    @staticmethod
    def _guard(key: str) -> None:
        normalized = key.upper()
        if any(part in normalized for part in
               ('BINANCE', 'SECRET', 'PRIVATE_KEY', 'API_KEY', 'APIKEY')):
            raise AssertionError('terminal read a venue credential variable: ' + key)

    def get(self, key, default=None):
        self.reads.append(key)
        self._guard(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.reads.append(key)
        self._guard(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self.reads.append(key)
        self._guard(key)
        return super().__contains__(key)


class TerminalCase:
    def __init__(self, root: Path):
        self.api = ApiCase(root / 'control.sqlite')
        self.projection = ProjectionCase(root / 'projection.sqlite')
        self.streams = StreamCase(root / 'stream.sqlite')

        # Bind the control actor and every projection/stream to the exact same
        # account scope. IP-28's default fixture account differs by one suffix.
        self.scope = SCOPE_A
        self.api.scope = SCOPE_A
        self.api.change_actor(scopes=(SCOPE_A,))

        # An unmanaged/EXTERNAL position is visible but must never be presented
        # as an owned exit target.
        self.projection.store.put_position(self.scope, make_position_page(
            make_position(self.scope, 'position-external', None),
            make_protection(self.scope, mode='HOSTED', state='PENDING')))

        self.health = HealthCase()
        self.transport = OfflineTerminalTransport(
            api=self.api, projection=self.projection, streams=self.streams,
            actor=self.api.actor, scope=self.scope)
        self.binding = ControlBinding(expected_revision=1, ownership_epoch=1)
        self.client = self.client_with_token(self.api.token)
        self.session = TerminalSession(
            self.client, binding=self.binding,
            health_reader=self.health.snapshot)

    # -- construction -------------------------------------------------------

    def client_with_token(self, token: str) -> TerminalClient:
        return TerminalClient(transport=self.transport,
                              token=ControlToken(value=token),
                              scope=self.scope)

    def new_session(self, client: TerminalClient | None = None) -> TerminalSession:
        return TerminalSession(client or self.client, binding=self.binding,
                               health_reader=self.health.snapshot)

    # -- request helpers ----------------------------------------------------

    def request(self, action: str = 'pause_new', **changes
                ) -> t.ControlCommandRequest:
        fields = dict(scope=self.scope, expected_revision=1,
                      ownership_epoch=1, transfer=None)
        fields.update(changes)
        return t.ControlCommandRequest(action=action, **fields)

    def command(self, action: str = 'pause_new', key: str = 'terminal-key'):
        return self.client.command(self.request(action), key)

    def web_post(self, *, action: str = 'pause_new', key: str = 'terminal-key'):
        body = domain_payload(self.request(action))
        return self.api.post(body, key=key)

    def query(self, resource: str = 'positions', **changes):
        fields = dict(scope=self.scope, resource=resource,
                      owner_strategy_id=None, cursor=None, limit=100,
                      maximum_age_ms=None)
        fields.update(changes)
        return self.client.query(t.ProjectionQueryRequest(**fields))

    def event_cursor(self, sequence: int):
        return self.streams.cursor(sequence)

    def expire_event_history(self, before: int = 3):
        self.streams.feed.retain_from(self.scope, before)

    # -- fault injection ----------------------------------------------------

    def timeout_next_command_after_commit(self, count: int = 1):
        self.transport.timeout_after_commit = count

    def always_timeout_commands(self, enabled: bool = True):
        self.transport.always_timeout = enabled

    def fail_reads(self, enabled: bool = True):
        self.transport.always_fail_reads = enabled

    def revoke_session(self):
        token_hash = sha256(self.api.token.encode()).hexdigest()
        self.api.sessions[token_hash] = replace(
            self.api.sessions[token_hash], revoked=True)

    # -- UI lifecycle -------------------------------------------------------

    def close_ui(self):
        return self.session.close()

    def command_count(self) -> int:
        return self.api.uow.count()

    def transport_requests_after(self, index: int) -> list[dict]:
        return self.transport.requests[index:]


@pytest.fixture
def terminal_case(tmp_path):
    return TerminalCase(tmp_path)
