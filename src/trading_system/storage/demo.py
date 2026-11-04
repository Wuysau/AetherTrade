"""Isolated Demo durable records. No startup migration and no PAPER table reuse.

Portable SQL is unit-tested with SQLite; execution ownership requires PostgreSQL.
All monetary values use canonical decimal strings, never binary floating point.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from decimal import Decimal
from threading import RLock

from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, Boolean, JSON,
                        UniqueConstraint, select, insert, update, func)

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, decimal, digest

_held_guards = ContextVar('demo_sender_guards', default=frozenset())

metadata = MetaData()
accounts = Table('demo_accounts', metadata,
    Column('account_key', String(64), primary_key=True), Column('identity', JSON, nullable=False),
    Column('credential_ref', String(128), nullable=False), Column('fingerprint', String(64), nullable=False),
    Column('revision', Integer, nullable=False), Column('credential_revision', Integer, nullable=False), Column('state', String(32), nullable=False),
    Column('budget', Text, nullable=False), Column('balances', JSON), Column('frozen', Boolean, nullable=False),
    Column('reset_evidence', Text), Column('qualification', JSON))
commands = Table('demo_commands', metadata,
    Column('command_id', String(100), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('scope_key', String(64), nullable=False), Column('scope', JSON, nullable=False), Column('namespace', String(16), nullable=False),
    Column('client_id', String(36), nullable=False), Column('request', JSON, nullable=False),
    Column('request_hash', String(64), nullable=False), Column('authorization_ref', Text, nullable=False),
    Column('credential_revision', Integer, nullable=False), Column('admission_revision', Integer, nullable=False), Column('state', String(32), nullable=False),
    Column('method', String(8), nullable=False), Column('expires_at', Integer, nullable=False),
    Column('reserve_asset', String(20), nullable=False), Column('reserve_amount', Text, nullable=False),
    Column('original_reserve', Text, nullable=False), Column('order_id', String(64)),
    Column('order_status', String(32)), Column('cumulative_qty', Text, nullable=False), Column('reason', Text),
    UniqueConstraint('account_key', 'namespace', 'client_id', name='demo_client_identity'))
observations = Table('demo_observations', metadata,
    Column('observation_id', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('scope_key', String(64), nullable=False), Column('source', String(32), nullable=False),
    Column('credential_revision', Integer, nullable=False), Column('payload_hash', String(64), nullable=False),
    Column('payload', JSON, nullable=False), Column('received_at', Integer, nullable=False))
inbox = Table('demo_inbox', metadata,
    Column('observation_id', String(64), primary_key=True), Column('state', String(16), nullable=False),
    Column('attempts', Integer, nullable=False), Column('reason', Text))
fills = Table('demo_fills', metadata,
    Column('fill_key', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('scope_key', String(64), nullable=False), Column('symbol', String(20), nullable=False),
    Column('trade_id', String(64), nullable=False), Column('order_id', String(64), nullable=False),
    Column('payload_hash', String(64), nullable=False), Column('payload', JSON, nullable=False),
    UniqueConstraint('account_key', 'symbol', 'trade_id', name='demo_fill_identity'))
postings = Table('demo_postings', metadata,
    Column('fill_key', String(64), primary_key=True), Column('asset', String(20), primary_key=True),
    Column('account_key', String(64), nullable=False), Column('scope_key', String(64), nullable=False),
    Column('delta', Text, nullable=False))
protection = Table('demo_protection', metadata,
    Column('fill_key', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('scope_key', String(64), nullable=False), Column('state', String(24), nullable=False))
outbox = Table('demo_outbox', metadata,
    Column('event_id', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('kind', String(40), nullable=False), Column('payload', JSON, nullable=False),
    Column('delivered', Boolean, nullable=False))
reconciliations = Table('demo_reconciliations', metadata,
    Column('account_key', String(64), primary_key=True), Column('symbol', String(20), primary_key=True),
    Column('cursor', JSON, nullable=False), Column('complete', Boolean, nullable=False),
    Column('revision', Integer, nullable=False))


class DemoRepository:
    def __init__(self, engine):
        self.engine = engine
        self._lock = RLock()

    @contextmanager
    def transaction(self, account_key):
        with self._lock, self.engine.connect() as conn:
            # PostgreSQL account row lock serializes every strategy in the epoch.
            if self.engine.dialect.name == 'sqlite':
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                conn.begin()
                conn.exec_driver_sql('SET LOCAL synchronous_commit = on')
            try:
                conn.execute(select(accounts.c.account_key).where(accounts.c.account_key == account_key).with_for_update()).first()
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def owner_guard(self, scope):
        # A session advisory lock survives the MAY transaction commit and covers
        # the network request. Lease expiry never steals it from a live sender.
        if self.engine.dialect.name != 'postgresql':
            raise DemoError('DEMO_POSTGRESQL_OWNER_REQUIRED')
        identity = (id(self.engine), scope.account_key)
        if identity in _held_guards.get():
            yield
            return
        key = int(scope.account_key[:15], 16)
        with self.engine.connect() as conn:
            locked = conn.exec_driver_sql('SELECT pg_try_advisory_lock(%s)', (key,)).scalar()
            conn.commit()
            if not locked:
                raise DemoError('DEMO_OWNER_BUSY')
            token = _held_guards.set(_held_guards.get() | {identity})
            try:
                yield
            finally:
                _held_guards.reset(token)
                conn.exec_driver_sql('SELECT pg_advisory_unlock(%s)', (key,))
                conn.commit()

    def safety_guard(self, scope):
        return self.owner_guard(scope) if self.engine.dialect.name == 'postgresql' else self._lock

    def register_account(self, scope, *, credential_ref, fingerprint, budget):
        from dataclasses import asdict
        amount = decimal(budget, positive=True)
        if amount > 10000:
            raise DemoError('DEMO_PROJECT_BUDGET_EXCEEDED')
        # Registration does not certify that this identity belongs to a key.
        with self.transaction(scope.account_key) as conn:
            existing = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().first()
            if existing:
                if existing['credential_ref'] != credential_ref or existing['fingerprint'] != fingerprint:
                    raise DemoError('DEMO_CREDENTIAL_ROTATION_REQUIRES_PAUSE')
                return dict(existing)
            conn.execute(insert(accounts).values(account_key=scope.account_key, identity={k: v for k, v in asdict(scope).items() if k != 'strategy_id'},
                credential_ref=credential_ref, fingerprint=fingerprint, revision=0, credential_revision=0, state='READ_ONLY', budget=str(amount), balances=None, frozen=False))

    def account(self, scope):
        with self.engine.connect() as conn:
            row = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().first()
        if row is None:
            raise DemoError('DEMO_ACCOUNT_UNREGISTERED')
        return dict(row)

    def command(self, scope, command_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(commands).where(commands.c.command_id == command_id, commands.c.scope_key == scope.key)).mappings().first()
        if row is None:
            raise DemoError('DEMO_COMMAND_NOT_FOUND')
        return dict(row)

    def _event(self, conn, account_key, kind, identity, payload):
        event_id = digest([account_key, kind, identity])
        if conn.execute(select(outbox.c.event_id).where(outbox.c.event_id == event_id)).first() is None:
            conn.execute(insert(outbox).values(event_id=event_id, account_key=account_key, kind=kind, payload=payload, delivered=False))

    def stage(self, scope, *, command_id, request, reserve_asset, reserve_amount, available, authorization_ref, expires_at, now, method='POST'):
        if scope.product != 'SPOT' or method not in {'POST', 'DELETE'}:
            raise DemoError('DEMO_CAPABILITY_NOT_SUPPORTED')
        amount, available = decimal(reserve_amount), decimal(available)
        if not authorization_ref or type(expires_at) is not int or not now < expires_at <= now + 300:
            raise DemoError('DEMO_AUTHORIZATION_EXPIRED')
        if method == 'POST':
            required = {'symbol', 'side', 'type', 'quantity', 'newClientOrderId'}
            allowed = required | {'price', 'timeInForce'}
            if not required <= request.keys() or request.keys() - allowed or request['type'] not in {'LIMIT', 'MARKET'} or request['side'] not in {'BUY', 'SELL'}:
                raise DemoError('DEMO_ORDER_INVALID')
            decimal(request['quantity'], positive=True)
            client_id = request['newClientOrderId']
            if request['symbol'] != 'BTCUSDT':
                raise DemoError('DEMO_RESERVATION_SYMBOL_NOT_QUALIFIED')
            expected_asset = 'USDT' if request['side'] == 'BUY' else 'BTC'
            if reserve_asset != expected_asset:
                raise DemoError('DEMO_RESERVATION_ASSET_INVALID')
            if request['type'] != 'LIMIT':
                raise DemoError('DEMO_MARKET_RISK_NOT_QUALIFIED')
            required_amount = decimal(request['quantity'], positive=True)
            if request['side'] == 'BUY':
                required_amount *= decimal(request.get('price'), positive=True)
            if amount < required_amount:
                raise DemoError('DEMO_RESERVATION_TOO_SMALL')
        else:
            if set(request) != {'symbol', 'origClientOrderId'}:
                raise DemoError('DEMO_CANCEL_INVALID')
            # Cancellation has an independent command identity; target stays original.
            client_id = digest(command_id)[:36]
        intent_hash = digest([scope.key, request, method, reserve_asset, str(amount), authorization_ref, expires_at])
        with self.transaction(scope.account_key) as conn:
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if account['frozen']:
                raise DemoError('DEMO_EPOCH_FROZEN')
            old = conn.execute(select(commands).where(commands.c.command_id == command_id)).mappings().first()
            if old:
                if old['request_hash'] != intent_hash:
                    raise DemoError('DEMO_IDEMPOTENCY_CONFLICT')
                return dict(old)
            if conn.execute(select(commands.c.command_id).where(commands.c.account_key == scope.account_key, commands.c.namespace == ('SPOT' if method == 'POST' else 'SPOT_CANCEL'), commands.c.client_id == client_id)).first():
                raise DemoError('DEMO_CLIENT_ID_CONFLICT')
            held = sum((decimal(v) for v in conn.execute(select(commands.c.reserve_amount).where(commands.c.account_key == scope.account_key, commands.c.reserve_asset == reserve_asset)).scalars()), Decimal(0))
            ceiling = min(available, decimal(account['budget'])) if reserve_asset == 'USDT' else available
            if held + amount > ceiling:
                raise DemoError('DEMO_BUDGET_EXCEEDED')
            conn.execute(insert(commands).values(command_id=command_id, account_key=scope.account_key, scope_key=scope.key,
                scope=asdict(scope), namespace='SPOT' if method == 'POST' else 'SPOT_CANCEL', client_id=client_id, request=dict(request), request_hash=intent_hash,
                authorization_ref=authorization_ref, credential_revision=account['credential_revision'], admission_revision=account['revision'], state='DURABLE_READY', method=method,
                expires_at=expires_at, reserve_asset=reserve_asset, reserve_amount=str(amount), original_reserve=str(amount), cumulative_qty='0'))
            self._event(conn, scope.account_key, 'COMMAND_STAGED', command_id, {'command_id': command_id, 'request_hash': intent_hash})
        return self.command(scope, command_id)

    def begin_send(self, scope, command_id, *, expected_revision, now):
        with self.transaction(scope.account_key) as conn:
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            row = conn.execute(select(commands).where(commands.c.command_id == command_id, commands.c.scope_key == scope.key)).mappings().one()
            if row['state'] != 'DURABLE_READY':
                raise DemoError('DEMO_SEND_ALREADY_ATTEMPTED')
            if account['frozen'] or account['revision'] != expected_revision or row['admission_revision'] != expected_revision or row['credential_revision'] != account['credential_revision'] or row['expires_at'] <= now:
                raise DemoError('DEMO_SEND_AUTHORIZATION_STALE')
            conn.execute(update(commands).where(commands.c.command_id == command_id).values(state='MAY_HAVE_SENT'))
            self._event(conn, scope.account_key, 'MAY_HAVE_SENT', command_id, {'command_id': command_id})
        # Return only after commit succeeded; an uncertain commit never returns a permit.
        return self.command(scope, command_id)

    def mark_unknown(self, scope, command_id, reason):
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            conn.execute(update(commands).where(commands.c.command_id == command_id, commands.c.scope_key == scope.key, commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN'])).values(state='UNKNOWN', reason=reason))
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
            self._event(conn, scope.account_key, 'COMMAND_UNKNOWN', command_id, {'command_id': command_id})

    def reconcile_not_found(self, scope, command_id):
        self.mark_unknown(scope, command_id, 'DEMO_NOT_FOUND_IS_NOT_NOT_SENT')

    def freeze_epoch(self, scope, *, evidence_ref):
        if not evidence_ref:
            raise DemoError('DEMO_RESET_EVIDENCE_REQUIRED')
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(frozen=True, state='RECOVERY_REQUIRED', reset_evidence=evidence_ref, revision=accounts.c.revision + 1))

    def observe(self, scope, *, source, payload, received_at, credential_ref):
        # Internal transport/stream boundary only. There is no HTTP ingest route.
        if source not in {'WS_EXECUTION_REPORT', 'REST_ORDER', 'REST_TRADES'}:
            raise DemoError('DEMO_OBSERVATION_SOURCE_REJECTED')
        encoded = str(payload).lower()
        if any(key in encoded for key in ('apikey', 'secret', 'signature', 'authorization')):
            raise DemoError('DEMO_OBSERVATION_SECRET_REJECTED')
        payload_hash = digest(payload)
        observation_id = digest([scope.key, source, payload_hash])
        with self.transaction(scope.account_key) as conn:
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if account['credential_ref'] != credential_ref or account['frozen']:
                raise DemoError('DEMO_OBSERVATION_BINDING_REJECTED')
            old = conn.execute(select(observations.c.observation_id).where(observations.c.observation_id == observation_id)).first()
            if old is None:
                conn.execute(insert(observations).values(observation_id=observation_id, account_key=scope.account_key, scope_key=scope.key,
                    source=source, credential_revision=account['credential_revision'], payload_hash=payload_hash, payload=payload, received_at=received_at))
                conn.execute(insert(inbox).values(observation_id=observation_id, state='PENDING', attempts=0))
        return observation_id

    def freeze_on_conflict(self, scope, observation_id, reason):
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
            conn.execute(update(inbox).where(inbox.c.observation_id == observation_id).values(state='CONFLICT', reason=reason, attempts=inbox.c.attempts + 1))

    def asset_deltas(self, scope):
        result = {}
        with self.engine.connect() as conn:
            for asset, delta in conn.execute(select(postings.c.asset, postings.c.delta).where(postings.c.scope_key == scope.key)):
                result[asset] = result.get(asset, Decimal(0)) + Decimal(delta)
        return result

    def health(self, scope):
        with self.engine.connect() as conn:
            def count(table, predicate):
                return conn.execute(select(func.count()).select_from(table).where(predicate)).scalar_one()
            return {'fill_count': count(fills, fills.c.account_key == scope.account_key),
                    'unknown': count(commands, (commands.c.account_key == scope.account_key) & commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN'])),
                    'unprotected': count(protection, (protection.c.account_key == scope.account_key) & (protection.c.state == 'REQUIRED')),
                    'pending_inbox': count(inbox, (inbox.c.state != 'PROCESSED') & inbox.c.observation_id.in_(select(observations.c.observation_id).where(observations.c.account_key == scope.account_key)))}

    def save_cursor(self, scope, symbol, cursor, *, complete, expected_revision):
        with self.transaction(scope.account_key) as conn:
            row = conn.execute(select(reconciliations).where(reconciliations.c.account_key == scope.account_key, reconciliations.c.symbol == symbol)).mappings().first()
            if row is None:
                if expected_revision != 0:
                    raise DemoError('DEMO_CURSOR_CONFLICT')
                conn.execute(insert(reconciliations).values(account_key=scope.account_key, symbol=symbol, cursor=cursor, complete=complete, revision=1))
            elif row['revision'] != expected_revision:
                raise DemoError('DEMO_CURSOR_CONFLICT')
            else:
                conn.execute(update(reconciliations).where(reconciliations.c.account_key == scope.account_key, reconciliations.c.symbol == symbol).values(cursor=cursor, complete=complete, revision=expected_revision + 1))

    def rotate_credential(self, scope, *, credential_ref, fingerprint, expected_revision):
        # Call under the owner_guard in a deployed PostgreSQL runtime. SQLite is
        # offline-only and cannot have an executing owner.
        guard = self.owner_guard(scope) if self.engine.dialect.name == 'postgresql' else self._lock
        with guard, self.transaction(scope.account_key) as conn:
            row = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if row['revision'] != expected_revision:
                raise DemoError('DEMO_CREDENTIAL_REVISION_CONFLICT')
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(credential_ref=credential_ref,
                fingerprint=fingerprint, revision=expected_revision + 1, credential_revision=row['credential_revision'] + 1, state='READ_ONLY', qualification=None))

    def acknowledge(self, scope, command_id, response):
        row = self.command(scope, command_id)
        request = row['request']
        target = request.get('newClientOrderId', request.get('origClientOrderId'))
        if response.get('symbol') != request['symbol'] or response.get('clientOrderId') != target or type(response.get('orderId')) is not int:
            raise DemoError('DEMO_ACK_BINDING_INVALID')
        with self.transaction(scope.account_key) as conn:
            conn.execute(update(commands).where(commands.c.command_id == command_id, commands.c.scope_key == scope.key,
                commands.c.state == 'MAY_HAVE_SENT').values(state='ACKNOWLEDGED', order_id=str(response['orderId'])))
            self._event(conn, scope.account_key, 'COMMAND_ACKNOWLEDGED', command_id, {'command_id': command_id})

    def pause(self, scope, reason='DEMO_RECOVERY_REQUIRED'):
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
            self._event(conn, scope.account_key, 'ACCOUNT_PAUSED', reason, {'reason': reason})

    def pending_commands(self, scope, *, limit=50):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(commands).where(commands.c.account_key == scope.account_key,
                commands.c.method == 'POST', commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED', 'OBSERVED']),
                (commands.c.order_status.is_(None) | commands.c.order_status.not_in(['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH']) | (commands.c.reserve_amount != '0')))
                .order_by(commands.c.command_id).limit(limit)).mappings()]

    def cursor(self, scope, symbol):
        with self.engine.connect() as conn:
            row = conn.execute(select(reconciliations).where(reconciliations.c.account_key == scope.account_key, reconciliations.c.symbol == symbol)).mappings().first()
            return dict(row) if row else {'cursor': {'from_id': None}, 'complete': False, 'revision': 0}

    def balance_baseline(self, scope, balances):
        with self.transaction(scope.account_key) as conn:
            row = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if row['balances'] is None:
                # A baseline cannot be introduced after economic facts exist.
                if conn.execute(select(fills.c.fill_key).where(fills.c.account_key == scope.account_key)).first():
                    raise DemoError('DEMO_BASELINE_TOO_LATE')
                conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(balances=balances))
                return balances
            return row['balances']

    def account_deltas(self, scope):
        result = {}
        with self.engine.connect() as conn:
            for asset, delta in conn.execute(select(postings.c.asset, postings.c.delta).where(postings.c.account_key == scope.account_key)):
                result[asset] = result.get(asset, Decimal(0)) + Decimal(delta)
        return result

    def resolve_order_scope(self, account_scope, *, client_id=None, order_id=None, symbol):
        if not symbol or (client_id is None and order_id is None):
            raise DemoError('DEMO_ORDER_BINDING_REJECTED')
        query = select(commands).where(commands.c.account_key == account_scope.account_key, commands.c.namespace == 'SPOT')
        query = query.where(commands.c.client_id == client_id) if client_id is not None else query.where(commands.c.order_id == str(order_id))
        with self.engine.connect() as conn:
            matches = [row for row in conn.execute(query).mappings() if row['request']['symbol'] == symbol]
        if len(matches) != 1:
            raise DemoError('DEMO_ORDER_BINDING_REJECTED')
        return DemoScope(**matches[0]['scope'])
