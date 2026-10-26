"""Offline IP-28 logic probes over real temporary SQLite rows.

This is a test-only adapter for the projection/event/audit ports. It is not
PostgreSQL, a live venue feed or qualification evidence. Production composition
never imports this module. Every fixture drives the production query, stream,
health and redaction logic; no method returns a hard-coded success.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3
from threading import RLock
from uuid import uuid4

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload

FIXTURE_SECRET = 'fixture-secret-value'
FIXTURE_KEY = 'fixture-key-value'
from trading_system.control.event_stream import EventRecord, EventStream, event_stream_id
from trading_system.control.health import (
    DATABASE,
    EXECUTION_OWNER,
    HEALTH_COMPONENTS,
    MARKET_DATA,
    MODEL,
    PRIVATE_STREAM,
    ComponentHealth,
    HealthProjection,
)
from trading_system.control.queries import (
    AuditEntry,
    AuditProjection,
    ProjectionQuery,
    ProjectionWatermark,
)
from trading_system.storage.models import decode, encode, scope_id


STAMP = '2026-10-20T06:40:00Z'
OBSERVED_AT = '2026-10-20T06:40:01Z'
NOW = datetime(2026, 10, 20, 6, 40, 1, tzinfo=timezone.utc)


def make_scope(environment=t.Environment.PAPER, account='paper-account-a',
               instrument='BTC-USDT-SPOT'):
    return t.ScopeKey(environment=environment, venue_account_id=account,
        product=t.Product.SPOT, instrument_uid=instrument, venue_leg='BOTH')


def make_actor(scope, identity='human-a'):
    return t.ActorContext(identity_id=identity, environment=scope.environment,
        deployment_id='deployment-a', authorization_id='authorization-a',
        policy_revision=1, ownership_epoch=1, scopes=(scope,))


SCOPE_A = make_scope()
SCOPE_B = make_scope(t.Environment.TESTNET, 'testnet-account-b')


def _connect(path):
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


class SQLiteProjectionStore:
    def __init__(self, path):
        self.path = path
        self.lock = RLock()
        self.read_calls = 0
        with _connect(path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS projection_watermarks '
                '(scope_id TEXT PRIMARY KEY, as_of TEXT, sequence INTEGER, '
                'retained_from INTEGER, missing TEXT)')
            for table in ('projection_positions', 'projection_orders',
                          'projection_risk_locks', 'projection_controls',
                          'projection_intents'):
                connection.execute(f'CREATE TABLE IF NOT EXISTS {table} '
                    '(scope_id TEXT, record_id TEXT, payload TEXT, '
                    'PRIMARY KEY(scope_id,record_id))')

    def set_watermark(self, scope, *, as_of=STAMP, sequence=1,
                      retained_from=1, missing=()):
        with self.lock, _connect(self.path) as connection:
            connection.execute('INSERT INTO projection_watermarks VALUES (?,?,?,?,?) '
                'ON CONFLICT(scope_id) DO UPDATE SET as_of=excluded.as_of, '
                'sequence=excluded.sequence, retained_from=excluded.retained_from, '
                'missing=excluded.missing',
                (scope_id(scope), as_of, sequence, retained_from,
                 json.dumps(list(missing))))

    def watermark(self, scope):
        with self.lock, _connect(self.path) as connection:
            row = connection.execute('SELECT as_of,sequence,retained_from,missing '
                'FROM projection_watermarks WHERE scope_id=?',
                (scope_id(scope),)).fetchone()
        if row is None:
            return None
        return ProjectionWatermark(as_of=row[0], sequence=row[1],
            retained_from=row[2], missing=tuple(json.loads(row[3])))

    def _put(self, table, scope, record_id, record):
        with self.lock, _connect(self.path) as connection:
            connection.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?,?)',
                (scope_id(scope), record_id, encode(record)))

    def _get(self, table, scope, cls):
        with self.lock, _connect(self.path) as connection:
            rows = connection.execute(f'SELECT payload FROM {table} '
                'WHERE scope_id=? ORDER BY record_id', (scope_id(scope),)).fetchall()
        return tuple(decode(cls, row[0]) for row in rows)

    def put_position(self, scope, projection):
        self._put('projection_positions', scope, projection.position.position_id,
                  projection)

    def put_order(self, scope, projection):
        self._put('projection_orders', scope,
                  projection.order.identity.logical_client_order_id, projection)

    def put_risk_lock(self, scope, lock):
        self._put('projection_risk_locks', scope, lock.lock_id, lock)

    def positions(self, scope, owner_strategy_id):
        self.read_calls += 1
        return tuple(item for item in self._get('projection_positions', scope,
            t.PositionProjection)
            if owner_strategy_id is None
            or item.position.owner_strategy_id == owner_strategy_id)

    def orders(self, scope, owner_strategy_id):
        self.read_calls += 1
        return tuple(item for item in self._get('projection_orders', scope,
            t.OrderProjection)
            if owner_strategy_id is None
            or item.order.owner_strategy_id == owner_strategy_id)

    def risk_locks(self, scope, owner_strategy_id):
        self.read_calls += 1
        return self._get('projection_risk_locks', scope, t.RiskLock)

    def controls(self, scope, owner_strategy_id):
        self.read_calls += 1
        return self._get('projection_controls', scope,
                         t.ControlCommandProjection)

    def intents(self, scope, owner_strategy_id):
        self.read_calls += 1
        return self._get('projection_intents', scope, t.TradeIntent)


class SQLiteEventFeed:
    def __init__(self, path):
        self.path = path
        self.lock = RLock()
        self.read_calls = 0
        with _connect(path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS event_feed '
                '(scope_id TEXT, position INTEGER, payload TEXT, '
                'PRIMARY KEY(scope_id,position))')

    def append(self, scope, event):
        with self.lock, _connect(self.path) as connection:
            row = connection.execute('SELECT COALESCE(MAX(position),0) '
                'FROM event_feed WHERE scope_id=?', (scope_id(scope),)).fetchone()
            position = row[0] + 1
            connection.execute('INSERT INTO event_feed VALUES (?,?,?)',
                (scope_id(scope), position, encode(event)))
        return position

    def watermark(self, scope):
        with self.lock, _connect(self.path) as connection:
            return connection.execute('SELECT COALESCE(MAX(position),0) '
                'FROM event_feed WHERE scope_id=?', (scope_id(scope),)).fetchone()[0]

    def retained_from(self, scope):
        with self.lock, _connect(self.path) as connection:
            row = connection.execute('SELECT MIN(position) FROM event_feed '
                'WHERE scope_id=?', (scope_id(scope),)).fetchone()
        return self.watermark(scope) + 1 if row[0] is None else row[0]

    def read_after(self, scope, after, limit):
        self.read_calls += 1
        with self.lock, _connect(self.path) as connection:
            rows = connection.execute('SELECT position,payload FROM event_feed '
                'WHERE scope_id=? AND position>? ORDER BY position LIMIT ?',
                (scope_id(scope), after, limit)).fetchall()
        return tuple(EventRecord(position=row[0], event=decode(t.DomainEvent, row[1]))
                     for row in rows)

    def retain_from(self, scope, position):
        with self.lock, _connect(self.path) as connection:
            connection.execute('DELETE FROM event_feed WHERE scope_id=? AND position<?',
                (scope_id(scope), position))


class SQLiteAuditReader:
    def __init__(self, path):
        self.path = path
        self.lock = RLock()
        with _connect(path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS audit_feed '
                '(scope_id TEXT, entry_id TEXT, payload TEXT, '
                'PRIMARY KEY(scope_id,entry_id))')

    def append(self, entry):
        with self.lock, _connect(self.path) as connection:
            connection.execute('INSERT OR REPLACE INTO audit_feed VALUES (?,?,?)',
                (scope_id(entry.scope), entry.entry_id, encode(entry)))

    def read(self, scope, limit):
        with self.lock, _connect(self.path) as connection:
            rows = connection.execute('SELECT payload FROM audit_feed '
                'WHERE scope_id=? ORDER BY entry_id LIMIT ?',
                (scope_id(scope), limit)).fetchall()
        return tuple(decode(AuditEntry, row[0]) for row in rows)


def _coverage(scope):
    return t.Coverage(scope=scope, query_succeeded=True,
        pagination_complete=True, supported=True, missing=())


def make_position(scope, position_id, owner):
    return t.ManagedPosition(position_id=position_id, scope=scope,
        actual_quantity=t.Quantity(value=Decimal('1.5'), unit='BTC'),
        owner_strategy_id=owner, owner_book_id='book-a', ownership_epoch=1,
        cost_evidence=_coverage(scope), realized_pnl=None,
        unrealized_valuation=None, active_exit_plan_id=None, risk_locks=())


def make_protection(scope, *, mode='NATIVE', state='GAP'):
    return t.ProtectionPlan(plan_id='protection-1', mode=mode, scope=scope,
        coverage_quantity=t.Quantity(value=Decimal('1.0'), unit='BTC'),
        stop_price=t.Price(value=Decimal('90'), currency='USDT'),
        stop_trigger='LAST', native_order_ids=(), monitor_id='monitor-1',
        state=state, last_verified_at=STAMP,
        coverage_gap_since=STAMP if state == 'GAP' else None,
        replacement_revision=1, on_gap_policy='REDUCE_ONLY')


def make_position_page(position, protection=None):
    return t.PositionProjection(position=position, protection=protection,
        freshness=t.FreshnessView(as_of=STAMP, age_ms=1000, stale=False,
                                  missing=()),
        sequence=1)


def make_order(scope, owner, logical='order-a', leaves='0.5'):
    identity = t.OrderIdentity(scope=scope, logical_client_order_id=logical,
        native_client_order_id='native-' + logical,
        venue_client_order_id='venue-' + logical, venue_order_id=None)
    return t.OrderProjection(order=t.OwnedOrderRef(identity=identity,
        owner_strategy_id=owner, ownership_epoch=1),
        lifecycle_state=t.OrderLifecycle.PARTIALLY_FILLED,
        pending_operation='SUBMIT', command_certainty='UNKNOWN',
        outcome_provenance='VENUE_EVENT',
        filled_quantity=t.Quantity(value=Decimal('1.0'), unit='BTC'),
        leaves_quantity=t.Quantity(value=Decimal(leaves), unit='BTC'),
        lifecycle_revision=2, sequence=2)


def make_lock(scope, lock_id='lock-1'):
    return t.RiskLock(lock_id=lock_id, scope=scope, cause='DAILY_LOSS_LOCK',
        state='ACTIVE', permission=t.RiskPermission.NO_NEW_RISK,
        required_evidence=('RISK_DAY_RECONCILED',), raised_at=STAMP)


class ProjectionCase:
    def __init__(self, path):
        self.scope = SCOPE_A
        self.other_scope = SCOPE_B
        self.actor = make_actor(self.scope)
        self.store = SQLiteProjectionStore(path)
        self.query = ProjectionQuery(self.store, clock=lambda: NOW)
        self.store.set_watermark(self.scope, as_of=STAMP, sequence=7,
            retained_from=1, missing=('MODEL_UNAVAILABLE',
                                      'COST_BUDGET_EXCEEDED'))
        self.store.put_position(self.scope, make_position_page(
            make_position(self.scope, 'position-a', 'owner-a'),
            make_protection(self.scope, mode='NATIVE', state='GAP')))
        self.store.put_position(self.scope, make_position_page(
            make_position(self.scope, 'position-b', 'owner-b'),
            make_protection(self.scope, mode='HOSTED', state='PENDING')))
        self.store.put_order(self.scope, make_order(self.scope, 'owner-a'))
        self.store.put_risk_lock(self.scope, make_lock(self.scope))

    def request(self, resource='positions', *, scope=None, cursor=None,
                limit=100, maximum_age_ms=None, owner_strategy_id='owner-a'):
        return t.ProjectionQueryRequest(scope=scope or self.scope,
            resource=resource, owner_strategy_id=owner_strategy_id,
            cursor=cursor, limit=limit, maximum_age_ms=maximum_age_ms)

    def read(self, resource='positions', **changes):
        return self.query.read(self.request(resource, **changes), self.actor)

    def cursor(self, resource='positions', sequence=7, scope=None, stream_id=None):
        from trading_system.control.queries import projection_stream_id
        actual_scope = scope or self.scope
        return t.StreamCursor(scope=actual_scope, sequence=sequence,
            stream_id=stream_id or projection_stream_id(actual_scope, resource))

    def read_cross_environment(self):
        # Actor A attempts to read the other environment's exact scope.
        return self.query.read(self.request(scope=self.other_scope), self.actor)


class StreamCase:
    def __init__(self, path):
        self.scope = SCOPE_A
        self.other_scope = SCOPE_B
        self.feed = SQLiteEventFeed(path)
        self.stream = EventStream(self.feed)
        for revision in range(1, 5):
            self.append('order:one', revision)
        for revision in range(1, 3):
            self.append('order:one', revision, scope=self.other_scope)
        self.feed.read_calls = 0

    def append(self, aggregate_id, revision, *, scope=None, event_id=None):
        scope = scope or self.scope
        event = t.DomainEvent(event_id=event_id or uuid4().hex, scope=scope,
            aggregate_id=aggregate_id, revision=revision, occurred_at=STAMP,
            evidence=(), payload=make_lock(scope, 'lock-' + uuid4().hex))
        return EventRecord(position=self.feed.append(scope, event), event=event)

    def cursor(self, sequence, *, scope=None, stream_id=None):
        actual_scope = scope or self.scope
        return t.StreamCursor(scope=actual_scope, sequence=sequence,
            stream_id=stream_id or event_stream_id(actual_scope))

    def read_expired_cursor(self):
        self.feed.retain_from(self.scope, 3)
        return self.stream.read(self.cursor(0), self.scope)

    def read_cross_environment_cursor(self):
        return self.stream.read(self.cursor(1, scope=self.other_scope), self.scope)

    def read_cross_account_cursor(self):
        changed = make_scope(account='paper-account-other')
        return self.stream.read(self.cursor(1, scope=changed), self.scope)

    def read_tampered_stream(self):
        return self.stream.read(self.cursor(1, stream_id='events:v1:forged'),
                                 self.scope)

    def read_next(self, cursor=None):
        return self.stream.read(cursor, self.scope)

    def read_regressed_revision(self):
        self.append('order:regress', 2)
        self.append('order:regress', 1)
        return self.stream.read(None, self.scope)


def evidence_ref(name='health'):
    return t.EvidenceRef(id='evidence-' + name, hash=sha256(name.encode()).hexdigest(),
        environment=t.Environment.PAPER, source='synthetic-health',
        visible_at=STAMP)


class StaticHealthSource:
    def __init__(self, reports):
        self._reports = tuple(reports)

    def reports(self):
        return self._reports


class HealthCase:
    def report(self, component, state='READY', *, faults=(), with_evidence=True):
        return ComponentHealth(component=component, state=t.ProcessState(state),
            observed_at=STAMP, faults=tuple(faults),
            evidence=(evidence_ref(component),) if with_evidence else ())

    def snapshot(self, *, states=None, faults=None, no_evidence=(),
                 omit=(), extra=()):
        states = dict(states or {})
        faults = dict(faults or {})
        reports = list(extra)
        for component in HEALTH_COMPONENTS:
            if component in omit:
                continue
            reports.append(self.report(component, states.get(component, 'READY'),
                faults=faults.get(component, ()),
                with_evidence=component not in no_evidence))
        return HealthProjection(StaticHealthSource(reports)).snapshot()


class AuditCase:
    def __init__(self, path):
        self.scope = SCOPE_A
        self.actor = make_actor(self.scope)
        self.reader = SQLiteAuditReader(path)
        self.entry = AuditEntry(entry_id='audit-1', scope=self.scope,
            occurred_at=STAMP, actor_id='full-identity-001',
            action='MODEL_CALL', outcome='REJECTED', details={
                'symbol': 'BTCUSDT',
                'prompt': 'use the private stop order now',
                'api_secret': FIXTURE_SECRET,
                'credentials': {'api_key': FIXTURE_KEY,
                                'signature': 'fixture-signature'},
                'email': 'alice@example.com',
                'identity_id': 'full-identity-001',
                'nested': [{'token': 'token-value-456'}],
            })
        self.reader.append(self.entry)
        self.projection = AuditProjection(self.reader)

    def read(self, **changes):
        return self.projection.read(self.scope, self.actor, **changes)

    def read_cross_scope(self):
        # Actor A attempts to read actor B's environment.
        return self.projection.read(SCOPE_B, self.actor)


@pytest.fixture
def projection_case(tmp_path):
    return ProjectionCase(tmp_path / 'projection.sqlite')


@pytest.fixture
def stream_case(tmp_path):
    return StreamCase(tmp_path / 'stream.sqlite')


@pytest.fixture
def health_case():
    return HealthCase()


@pytest.fixture
def audit_case(tmp_path):
    return AuditCase(tmp_path / 'audit.sqlite')
