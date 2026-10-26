"""Offline logic probes with real temporary SQLite transactions.

This is a test-only repository adapter, NOT PostgreSQL, native process, exchange
or qualification evidence. It drives the production inbox/auth/API logic. No
fixture is imported by production composition.
"""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.control.app import ControlApp
from trading_system.control.auth import ControlGrant, Session, SessionAuth
from trading_system.control.commands import DurableInbox
from trading_system.storage.models import decode, encode, scope_id


class SQLiteTx:
    def __init__(self, connection, scope, fail_append_at=None):
        self.connection = connection
        self.scope = scope
        self.group_id = 'paper-group-demo'
        self.fail_append_at = fail_append_at
        self.append_count = 0

    def execute(self, sql, params=()):
        if sql == 'SELECT pg_advisory_xact_lock(%s)':
            # The fixture UoW mutex serializes the entire transaction.
            return self.connection.execute('SELECT 1')
        return self.connection.execute(sql.replace('ledger.', '').replace('%s', '?'), params)

    def _append(self, table, record_id, record, expected_sequence):
        assert table == 'control_commands'
        self.append_count += 1
        if self.append_count == self.fail_append_at:
            raise OSError('injected append failure')
        row = self.connection.execute('SELECT MAX(sequence) FROM control_commands '
            'WHERE scope_id=? AND record_id=?', (scope_id(self.scope), record_id)).fetchone()
        if (row[0] or 0) != expected_sequence:
            raise ValueError('sequence conflict')
        self.connection.execute('INSERT INTO control_commands VALUES (?,?,?,?)',
            (scope_id(self.scope), record_id, expected_sequence + 1, encode(record)))

    def _latest(self, table, record_id, cls):
        assert table == 'control_commands'
        row = self.connection.execute('SELECT payload FROM control_commands '
            'WHERE scope_id=? AND record_id=? ORDER BY sequence DESC LIMIT 1',
            (scope_id(self.scope), record_id)).fetchone()
        return None if row is None else decode(cls, row[0])


class SQLiteUow:
    def __init__(self, path):
        self.path = path
        self.lock = RLock()
        self.fail_commit = False
        self.fail_append_at = None
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS control_commands '
                '(scope_id TEXT, record_id TEXT, sequence INTEGER, payload TEXT, '
                'PRIMARY KEY(scope_id,record_id,sequence))')

    @contextmanager
    def transaction(self, scope):
        with self.lock:
            connection = sqlite3.connect(self.path)
            try:
                connection.execute('BEGIN IMMEDIATE')
                yield SQLiteTx(connection, scope, self.fail_append_at)
                if self.fail_commit:
                    raise OSError('injected commit failure')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def count(self):
        with sqlite3.connect(self.path) as connection:
            return connection.execute('SELECT COUNT(*) FROM control_commands').fetchone()[0]


class ApiCase:
    def __init__(self, path):
        self.scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='paper-account',
            product=t.Product.SPOT, instrument_uid='BTC-USDT-SPOT', venue_leg='BOTH')
        self.actor = t.ActorContext(identity_id='human', environment=t.Environment.PAPER,
            deployment_id='deployment', authorization_id='authorization', policy_revision=1,
            ownership_epoch=1, scopes=(self.scope,))
        self.grant = ControlGrant(actor=self.actor, role='USER', operations=(
            'enqueueControl', 'readControl', 'reviseRiskPolicy', 'createDeployment'))
        self.token = 'offline-application-session-not-trading-credential'
        self.sessions = {sha256(self.token.encode()).hexdigest(): Session(actor=self.actor, expires_at=200)}
        self.uow = SQLiteUow(path)
        self.inbox = DurableInbox(self.uow, lambda tx, actor, scope: self.grant)
        self.app = ControlApp(SessionAuth(self.sessions.get, clock=lambda: 100), self.inbox)

    def request(self, action='exit_owned'):
        return domain_payload(t.ControlCommandRequest(action=action, scope=self.scope,
            expected_revision=1, ownership_epoch=1, transfer=None))

    def post(self, body=None, *, path='/api/control-commands', key='key', revision='1',
             scheme='https', token=None):
        return self.app.dispatch('POST', path, scheme=scheme, headers={
            'authorization': 'Bearer ' + (self.token if token is None else token),
            'idempotency-key': key, 'if-match': revision}, body=self.request() if body is None else body)

    def get(self, command_id, *, query=None):
        return self.app.dispatch('GET', '/api/control-commands/' + command_id,
            scheme='https', headers={'authorization': 'Bearer ' + self.token},
            query=domain_payload(self.scope) if query is None else query)

    def draft(self):
        root = Path(__file__).resolve().parents[2]
        policy = json.loads((root / 'tests/fixtures/contracts/examples/risk-policy.paper.json')
                            .read_text(encoding='utf-8'))
        policy['revision'] = 2
        return {'policy': policy, 'author_id': 'human', 'expected_revision': 1}

    def review(self, operation, body):
        digest = canonical_hash({'operation': operation, 'body': body})
        self.grant = replace(self.grant, reviewed_hashes=(digest,))

    def change_actor(self, **changes):
        self.actor = replace(self.actor, **changes)
        self.sessions[sha256(self.token.encode()).hexdigest()] = Session(actor=self.actor, expires_at=200)
        self.grant = replace(self.grant, actor=self.actor)
