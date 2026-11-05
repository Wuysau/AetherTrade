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

_held_guards = ContextVar('demo_sender_guards', default={})

metadata = MetaData()
from trading_system.storage.demo_authority import define_authority
authority_proofs = define_authority(metadata)
from trading_system.storage.demo_physical import define_physical_owners
physical_owners = define_physical_owners(metadata)

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
    Column('original_reserve', Text, nullable=False), Column('protection_plan', JSON), Column('risk_receipt', JSON),
    Column('purpose', String(24), nullable=False, server_default='ENTRY'), Column('order_id', String(64)),
    Column('order_status', String(32)), Column('cumulative_qty', Text, nullable=False), Column('cumulative_quote', Text, nullable=False, server_default='0'), Column('reason', Text),
    UniqueConstraint('account_key', 'namespace', 'client_id', name='demo_client_identity'))
observations = Table('demo_observations', metadata,
    Column('observation_id', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('scope_key', String(64), nullable=False), Column('scope', JSON), Column('source', String(32), nullable=False),
    Column('credential_revision', Integer, nullable=False), Column('stream_generation', Integer), Column('payload_hash', String(64), nullable=False),
    Column('payload', JSON, nullable=False), Column('received_at', Integer, nullable=False))
inbox = Table('demo_inbox', metadata,
    Column('observation_id', String(64), primary_key=True), Column('state', String(16), nullable=False),
    Column('attempts', Integer, nullable=False), Column('reason', Text))
fills = Table('demo_fills', metadata,
    Column('fill_key', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('scope_key', String(64), nullable=False), Column('symbol', String(20), nullable=False),
    Column('trade_id', String(64), nullable=False), Column('order_id', String(64), nullable=False),
    Column('payload_hash', String(64), nullable=False), Column('payload', JSON, nullable=False),
    Column('occurred_at', Integer, nullable=False, server_default='0'),
    UniqueConstraint('account_key', 'symbol', 'trade_id', name='demo_fill_identity'))
postings = Table('demo_postings', metadata,
    Column('fill_key', String(64), primary_key=True), Column('asset', String(20), primary_key=True),
    Column('account_key', String(64), nullable=False), Column('scope_key', String(64), nullable=False),
    Column('delta', Text, nullable=False))
protection = Table('demo_protection', metadata,
    Column('fill_key', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('scope_key', String(64), nullable=False), Column('state', String(24), nullable=False),
    Column('plan', JSON), Column('remaining_quantity', Text), Column('entry_command_id', String(100)),
    Column('coverage_until', Integer, nullable=False, server_default='0'),
    Column('opened_at', Integer, nullable=False, server_default='0'), Column('mitigation_command_id', String(100)))
outbox = Table('demo_outbox', metadata,
    Column('event_id', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('kind', String(40), nullable=False), Column('payload', JSON, nullable=False),
    Column('delivered', Boolean, nullable=False), Column('created_at', Integer, nullable=False, server_default='0'))
reconciliations = Table('demo_reconciliations', metadata,
    Column('account_key', String(64), primary_key=True), Column('symbol', String(20), primary_key=True),
    Column('cursor', JSON, nullable=False), Column('complete', Boolean, nullable=False),
    Column('revision', Integer, nullable=False))


strategies = Table('demo_strategies', metadata,
    Column('scope_key', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('scope', JSON, nullable=False), Column('revision', Integer, nullable=False), Column('config', JSON, nullable=False))
decisions = Table('demo_decisions', metadata,
    Column('scope_key', String(64), primary_key=True), Column('run_id', String(100), primary_key=True),
    Column('account_key', String(64), nullable=False), Column('state', String(32), nullable=False),
    Column('credential_revision', Integer), Column('stream_generation', Integer),
    Column('source', String(32), nullable=False), Column('binding_hash', String(64), nullable=False),
    Column('snapshot', JSON, nullable=False), Column('strategy', JSON, nullable=False), Column('result', JSON),
    Column('command_id', String(100)), Column('error', String(80)), Column('memory_context', JSON))

account_snapshots = Table('demo_account_snapshots', metadata,
    Column('account_key', String(64), primary_key=True), Column('facts_hash', String(64), nullable=False),
    Column('facts', JSON, nullable=False), Column('observed_at', String(40), nullable=False))

memory_projection = Table('demo_memory_projection', metadata,
    Column('scope_key', String(64), primary_key=True), Column('event_id', String(64), primary_key=True),
    Column('account_key', String(64), nullable=False), Column('kind', String(40), nullable=False),
    Column('payload', JSON), Column('created_at', Integer, nullable=False))

plan_heads = Table('demo_plan_heads', metadata,
    Column('scope_key', String(64), primary_key=True), Column('account_key', String(64), nullable=False),
    Column('revision', Integer, nullable=False), Column('state', String(16), nullable=False), Column('proposal', JSON, nullable=False),
    Column('source_command_id', String(100), nullable=False), Column('predecessor_hash', String(64)))
plan_updates = Table('demo_plan_updates', metadata,
    Column('management_id', String(100), primary_key=True), Column('scope_key', String(64), nullable=False),
    Column('account_key', String(64), nullable=False), Column('run_id', String(100), nullable=False),
    Column('request_hash', String(64), nullable=False), Column('receipt', JSON, nullable=False),
    UniqueConstraint('scope_key', 'run_id', name='demo_exit_update_identity'))


def unresolved_trade_history(conn, account_key, *, limit=100000):
    """Fail closed on excessive or malformed history; resolved requires an audit event."""
    rows = conn.execute(select(outbox).where(outbox.c.account_key == account_key,
        outbox.c.kind.in_(['UNRESOLVED_TRADE_HISTORY', 'RESOLVED_TRADE_HISTORY'])).limit(limit + 1)).mappings().all()
    if len(rows) > limit: raise DemoError('DEMO_HISTORY_REVIEW_LIMIT_EXCEEDED')
    pending, resolved = {}, set()
    for row in rows:
        payload = row['payload'] if isinstance(row['payload'], dict) else {}
        key = payload.get('trade_hash')
        if not isinstance(key, str) or len(key) != 64: raise DemoError('DEMO_HISTORY_REVIEW_CORRUPT')
        if row['kind'] == 'UNRESOLVED_TRADE_HISTORY': pending[key] = dict(row)
        else: resolved.add(key)
    return [row for key, row in pending.items() if key not in resolved]


def balance_invalidated_evidence(evidence):
    """Preserve only an already-qualified, separately bound cancel capability."""
    import re
    if not isinstance(evidence, dict): return None
    if evidence.get('state') == 'INVALIDATED_BALANCE': return evidence
    if evidence.get('state') != 'DEMO_VERIFIED': return None
    capabilities = evidence.get('capabilities')
    if not isinstance(capabilities, dict): capabilities = {}
    proof = capabilities.get('CANCEL') or (evidence if evidence.get('order_type') == 'CANCEL' else {})
    if (not isinstance(proof, dict) or proof.get('state') != 'DEMO_VERIFIED' or
            not re.fullmatch(r'[0-9a-f]{64}', proof.get('evidence_sha256') or '') or
            not isinstance(proof.get('official_contract_revision'), str) or not proof['official_contract_revision']):
        return None
    return {**{key:evidence.get(key) for key in ('code_sha','account_key','credential_revision','product',
                                               'proof_nonce','evidence_sha256','baseline_hash','policy')},
            'state':'INVALIDATED_BALANCE', 'capabilities':{'CANCEL':{key:proof[key] for key in ('state','evidence_sha256','official_contract_revision')}}}


def final_activity_digest(conn, scope, *, limit=100000):
    # Caller holds the account row lock. Include every durable account fact
    # that can change trading/recovery, not account revision alone.
    if type(limit) is not int or not 1 <= limit <= 100000:
        raise DemoError('DEMO_ACTIVITY_LIMIT_INVALID')
    account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
    account_fields = ('identity','credential_ref','fingerprint','credential_revision','budget','balances','frozen','qualification')
    snapshot = {'account': {key: account[key] for key in account_fields}}
    tables = (commands, observations, fills, postings, protection, reconciliations, strategies, decisions, account_snapshots, memory_projection, plan_heads, plan_updates)
    remaining = limit
    for table in tables:
        rows = conn.execute(select(table).where(table.c.account_key == scope.account_key).order_by(*table.primary_key.columns).limit(remaining + 1)).mappings().all()
        if len(rows) > remaining: raise DemoError('DEMO_ACTIVITY_LIMIT_EXCEEDED')
        snapshot[table.name] = [dict(row) for row in rows]
        remaining -= len(rows)
    rows = conn.execute(select(inbox).join(observations, inbox.c.observation_id == observations.c.observation_id)
        .where(observations.c.account_key == scope.account_key).order_by(inbox.c.observation_id).limit(remaining + 1)).mappings().all()
    if len(rows) > remaining: raise DemoError('DEMO_ACTIVITY_LIMIT_EXCEEDED')
    snapshot[inbox.name] = [dict(row) for row in rows]
    safety = conn.execute(select(outbox).where(outbox.c.account_key == scope.account_key,
        outbox.c.kind.in_(['EXECUTION_RISK_BREACH','ACCOUNT_PAUSED','UNRESOLVED_TRADE_HISTORY','RESOLVED_TRADE_HISTORY','BALANCE_BASELINE_INVALIDATED']))
        .order_by(outbox.c.event_id).limit(remaining - len(rows) + 1)).mappings().all()
    if len(safety) > remaining - len(rows): raise DemoError('DEMO_ACTIVITY_LIMIT_EXCEEDED')
    # Only the two mandatory local teardown pauses are expected after the
    # receipt. Every other pause is a unique, durable safety activity.
    snapshot['safety_events'] = [dict(row) for row in safety if row['kind'] != 'ACCOUNT_PAUSED' or
        (row['payload'] or {}).get('reason') not in {'DEMO_STREAM_DISCONNECTED','DEMO_RUNTIME_WINDOW_ENDED'}]
    return digest(snapshot)


def require_physical_generation(conn, scope, generation):
    if type(generation) is not int:
        raise DemoError('DEMO_PHYSICAL_GENERATION_REJECTED')
    owner = conn.execute(select(physical_owners).where(physical_owners.c.physical_key == scope.physical_account_key).with_for_update()).mappings().first()
    if (owner is None or owner['state'] != 'DIRTY' or owner['account_key'] != scope.account_key or
            owner['generation'] != generation):
        raise DemoError('DEMO_PHYSICAL_GENERATION_REJECTED')


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
        # Keep the transaction open so transaction pooling cannot return the
        # transaction lock's backend while the sender is using it.
        from trading_system.storage.demo_physical import PinnedSenderLock
        if self.engine.dialect.name != 'postgresql':
            raise DemoError('DEMO_POSTGRESQL_OWNER_REQUIRED')
        identity = (id(self.engine), scope.account_key)
        current = _held_guards.get()
        if identity in current:
            current[identity].validate()
            yield current[identity]
            return
        guard = PinnedSenderLock.acquire(self.engine, scope.account_key)
        token = _held_guards.set({**current, identity: guard})
        try:
            yield guard
        finally:
            _held_guards.reset(token)
            guard.close()

    def safety_guard(self, scope):
        return self.owner_guard(scope) if self.engine.dialect.name == 'postgresql' else self._lock

    def register_account(self, scope, *, credential_ref, fingerprint, budget, physical_owner=None):
        from dataclasses import asdict
        amount = decimal(budget, positive=True)
        if amount > 10000:
            raise DemoError('DEMO_PROJECT_BUDGET_EXCEEDED')
        # Registration does not certify that this identity belongs to a key.
        if self.engine.dialect.name == 'postgresql':
            if physical_owner is None: raise DemoError('DEMO_PHYSICAL_OWNER_REQUIRED')
            physical_owner.validate(scope)
        with self.transaction(scope.account_key) as conn:
            if self.engine.dialect.name == 'postgresql':
                require_physical_generation(conn, scope, physical_owner.generation)
                physical_owner.validate(scope)
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
            conn.execute(insert(outbox).values(event_id=event_id, account_key=account_key, kind=kind, payload=payload, delivered=False, created_at=int(__import__('time').time())))

    def stage(self, scope, *, command_id, request, reserve_asset, reserve_amount, available, authorization_ref, expires_at, now, method='POST', purpose='ENTRY', protection_plan=None, risk_receipt=None, expected_account_revision=None, expected_strategy_revision=None, reject_active_orders=False):
        if scope.product != 'SPOT' or method not in {'POST', 'DELETE'}:
            raise DemoError('DEMO_CAPABILITY_NOT_SUPPORTED')
        if ((method == 'DELETE' and purpose != 'CANCEL') or
                (method == 'POST' and (purpose not in {'ENTRY', 'REDUCTION', 'PROTECTION'} or
                 (purpose == 'ENTRY' and request.get('side') != 'BUY') or
                 (purpose in {'REDUCTION', 'PROTECTION'} and request.get('side') != 'SELL')))):
            raise DemoError('DEMO_ORDER_PURPOSE_INVALID')
        amount, available = decimal(reserve_amount), decimal(available)
        if not authorization_ref or type(expires_at) is not int or not now < expires_at <= now + 300:
            raise DemoError('DEMO_AUTHORIZATION_EXPIRED')
        if method == 'POST':
            required = {'symbol', 'side', 'type', 'newClientOrderId'}
            allowed = required | {'quantity', 'quoteOrderQty', 'price', 'timeInForce'}
            if not required <= request.keys() or request.keys() - allowed or request['type'] not in {'LIMIT', 'MARKET'} or request['side'] not in {'BUY', 'SELL'} or ('quantity' in request) == ('quoteOrderQty' in request):
                raise DemoError('DEMO_ORDER_INVALID')
            client_id = request['newClientOrderId']
            if request['symbol'] != 'BTCUSDT':
                raise DemoError('DEMO_RESERVATION_SYMBOL_NOT_QUALIFIED')
            expected_asset = 'USDT' if request['side'] == 'BUY' else 'BTC'
            if reserve_asset != expected_asset:
                raise DemoError('DEMO_RESERVATION_ASSET_INVALID')
            if request['type'] == 'LIMIT':
                if 'quoteOrderQty' in request or request.get('timeInForce') != 'GTC':
                    raise DemoError('DEMO_ORDER_INVALID')
                required_amount = decimal(request['quantity'], positive=True)
                if request['side'] == 'BUY': required_amount *= decimal(request.get('price'), positive=True)
            else:
                risk = (risk_receipt or {}).get('market_risk') or {}
                sizing = 'QUOTE_NOTIONAL' if request['side'] == 'BUY' else 'BASE_QUANTITY'
                field = 'quoteOrderQty' if request['side'] == 'BUY' else 'quantity'
                if 'price' in request or 'timeInForce' in request or field not in request or risk.get('sizing') != sizing or risk.get('price_policy') != 'UNBOUNDED_EXECUTION_PRICE':
                    raise DemoError('DEMO_MARKET_RISK_NOT_QUALIFIED')
                if request['side'] == 'SELL' and purpose not in {'REDUCTION', 'PROTECTION'}:
                    raise DemoError('DEMO_UNOWNED_EXPOSURE')
                fee_assets = risk.get('fee_assets')
                if not isinstance(fee_assets, list) or not fee_assets or set(fee_assets) - {'BTC', 'USDT'}:
                    raise DemoError('DEMO_MARKET_FEE_NOT_QUALIFIED')
                principal = decimal(request[field], positive=True)
                if decimal(risk.get('principal_cap')) != principal:
                    raise DemoError('DEMO_MARKET_PRINCIPAL_INVALID')
                fee_rate = decimal(risk.get('fee_rate_cap'))
                if fee_rate > 1:
                    raise DemoError('DEMO_MARKET_FEE_NOT_QUALIFIED')
                required_amount = principal * (1 + fee_rate)
            if amount < required_amount:
                raise DemoError('DEMO_RESERVATION_TOO_SMALL')
        else:
            if set(request) != {'symbol', 'origClientOrderId'}:
                raise DemoError('DEMO_CANCEL_INVALID')
            # Cancellation has an independent command identity; target stays original.
            client_id = digest(command_id)[:36]
        intent_hash = digest([scope.key, request, method, reserve_asset, str(amount), authorization_ref, expires_at, purpose, protection_plan, risk_receipt])
        with self.transaction(scope.account_key) as conn:
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if account['frozen']:
                raise DemoError('DEMO_EPOCH_FROZEN')
            if method == 'POST' and purpose == 'ENTRY' and conn.execute(select(outbox.c.event_id).where(outbox.c.account_key == scope.account_key, outbox.c.kind == 'EXECUTION_RISK_BREACH')).first():
                raise DemoError('DEMO_EXECUTION_RISK_BREACH')
            if method == 'POST' and purpose == 'ENTRY' and unresolved_trade_history(conn, scope.account_key):
                raise DemoError('DEMO_UNRESOLVED_TRADE_HISTORY_REVIEW_REQUIRED')
            if expected_account_revision is not None and account['revision'] != expected_account_revision:
                raise DemoError('DEMO_DECISION_BINDING_CHANGED')
            if expected_strategy_revision is not None:
                config = conn.execute(select(strategies.c.config).where(strategies.c.scope_key == scope.key)).scalar_one()
                if config['revision'] != expected_strategy_revision or config.get('authorized') is not True or config['authorization_ref'] != authorization_ref or config['authorized_until'] <= now:
                    raise DemoError('DEMO_DECISION_BINDING_CHANGED')
            old = conn.execute(select(commands).where(commands.c.command_id == command_id)).mappings().first()
            if old:
                if old['request_hash'] != intent_hash:
                    raise DemoError('DEMO_IDEMPOTENCY_CONFLICT')
                return dict(old)
            if conn.execute(select(commands.c.command_id).where(commands.c.account_key == scope.account_key, commands.c.namespace == ('SPOT' if method == 'POST' else 'SPOT_CANCEL'), commands.c.client_id == client_id)).first():
                raise DemoError('DEMO_CLIENT_ID_CONFLICT')
            if reject_active_orders and conn.execute(select(commands.c.command_id).where(commands.c.scope_key == scope.key, commands.c.method == 'POST', commands.c.state != 'ABANDONED',
                    (commands.c.order_status.is_(None) | commands.c.order_status.not_in(['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH']) | (commands.c.reserve_amount != '0')))).first():
                raise DemoError('DEMO_ACTIVE_ORDER_REQUIRES_RECONCILIATION')
            if method == 'DELETE':
                cancels = conn.execute(select(commands).where(commands.c.scope_key == scope.key, commands.c.method == 'DELETE', commands.c.state.in_(['DURABLE_READY', 'MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED']))).mappings().all()
                for cancel in cancels:
                    if cancel['request'] == request:
                        return dict(cancel)
            held = sum((decimal(v) for v in conn.execute(select(commands.c.reserve_amount).where(commands.c.account_key == scope.account_key, commands.c.reserve_asset == reserve_asset)).scalars()), Decimal(0))
            ceiling = min(available, decimal(account['budget'])) if reserve_asset == 'USDT' else available
            if method == 'POST' and held + amount > ceiling:
                raise DemoError('DEMO_BUDGET_EXCEEDED')
            conn.execute(insert(commands).values(command_id=command_id, account_key=scope.account_key, scope_key=scope.key,
                scope=asdict(scope), namespace='SPOT' if method == 'POST' else 'SPOT_CANCEL', client_id=client_id, request=dict(request), request_hash=intent_hash,
                authorization_ref=authorization_ref, credential_revision=account['credential_revision'], admission_revision=account['revision'], state='DURABLE_READY', method=method,
                expires_at=expires_at, purpose=purpose, protection_plan=protection_plan, risk_receipt=risk_receipt, reserve_asset=reserve_asset, reserve_amount=str(amount), original_reserve=str(amount), cumulative_qty='0', cumulative_quote='0'))
            self._event(conn, scope.account_key, 'COMMAND_STAGED', command_id, {'command_id': command_id, 'request_hash': intent_hash})
        return self.command(scope, command_id)

    def begin_send(self, scope, command_id, *, expected_revision, now, clock=None, validator=None):
        with self.transaction(scope.account_key) as conn:
            now = int(clock()) if clock is not None else now
            if validator is not None: validator(now)
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            row = conn.execute(select(commands).where(commands.c.command_id == command_id, commands.c.scope_key == scope.key)).mappings().one()
            if row['state'] != 'DURABLE_READY':
                raise DemoError('DEMO_SEND_ALREADY_ATTEMPTED')
            if account['frozen'] or account['revision'] != expected_revision or row['admission_revision'] != expected_revision or row['credential_revision'] != account['credential_revision'] or row['expires_at'] <= now:
                raise DemoError('DEMO_SEND_AUTHORIZATION_STALE')
            if row['method'] == 'POST':
                if row['purpose'] == 'ENTRY' and conn.execute(select(outbox.c.event_id).where(outbox.c.account_key == scope.account_key, outbox.c.kind == 'EXECUTION_RISK_BREACH')).first():
                    raise DemoError('DEMO_EXECUTION_RISK_BREACH')
                if row['purpose'] == 'ENTRY' and unresolved_trade_history(conn, scope.account_key):
                    raise DemoError('DEMO_UNRESOLVED_TRADE_HISTORY_REVIEW_REQUIRED')
                pending = conn.execute(select(inbox.c.observation_id).where(inbox.c.state != 'PROCESSED', inbox.c.observation_id.in_(select(observations.c.observation_id).where(observations.c.account_key == scope.account_key)))).first()
                if pending:
                    raise DemoError('DEMO_INBOX_RECONCILIATION_REQUIRED')
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

    def observe(self, scope, *, source, payload, received_at, credential_ref, credential_revision=None, stream_generation=None):
        # Internal transport/stream boundary only. There is no HTTP ingest route.
        if source not in {'WS_EXECUTION_REPORT', 'WS_ACCOUNT_POSITION', 'WS_UNSUPPORTED_EVENT', 'REST_ORDER', 'REST_TRADES'}:
            raise DemoError('DEMO_OBSERVATION_SOURCE_REJECTED')
        encoded = str(payload).lower()
        if any(key in encoded for key in ('apikey', 'secret', 'signature', 'authorization')):
            raise DemoError('DEMO_OBSERVATION_SECRET_REJECTED')
        payload_hash = digest(payload)
        if source in {'WS_ACCOUNT_POSITION', 'WS_UNSUPPORTED_EVENT'}:
            # These event types have no documented unique event ID. Identical
            # frames may be separate activity and must both survive a checkpoint.
            from uuid import uuid4
            observation_id = digest([scope.key, source, payload_hash, uuid4().hex])
        else:
            observation_id = digest([scope.key, source, payload_hash])
        with self.transaction(scope.account_key) as conn:
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if (account['credential_ref'] != credential_ref or account['frozen'] or
                    (credential_revision is None and self.engine.dialect.name == 'postgresql') or
                    (credential_revision is not None and (type(credential_revision) is not int or account['credential_revision'] != credential_revision))):
                raise DemoError('DEMO_OBSERVATION_BINDING_REJECTED')
            if source not in {'WS_ACCOUNT_POSITION','WS_UNSUPPORTED_EVENT'} and stream_generation is not None:
                observation_id = digest(['demo-observation-generation-v1',scope.key,source,payload_hash,
                                         account['credential_revision'],stream_generation])
            if self.engine.dialect.name == 'postgresql':
                require_physical_generation(conn, scope, stream_generation)
            old = conn.execute(select(observations.c.observation_id).where(observations.c.observation_id == observation_id)).first()
            if old is None:
                conn.execute(insert(observations).values(observation_id=observation_id, account_key=scope.account_key, scope_key=scope.key, scope=asdict(scope),
                    source=source, credential_revision=account['credential_revision'], stream_generation=stream_generation, payload_hash=payload_hash, payload=payload, received_at=received_at))
                conn.execute(insert(inbox).values(observation_id=observation_id,
                    state='CONFLICT' if source == 'WS_UNSUPPORTED_EVENT' else 'PENDING', attempts=0,
                    reason='DEMO_UNSUPPORTED_STREAM_EVENT' if source == 'WS_UNSUPPORTED_EVENT' else None))
                if source == 'WS_ACCOUNT_POSITION':
                    # Persist first; no account event is itself a ledger entry.
                    conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(
                        state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
                    conn.execute(update(protection).where(protection.c.account_key == scope.account_key,
                        protection.c.state == 'HOSTED_ACTIVE').values(state='REQUIRED', coverage_until=0))
                elif source == 'WS_UNSUPPORTED_EVENT' and payload.get('e') in {'balanceUpdate', 'externalLockUpdate'}:
                    # Deposits, withdrawals, transfers and external locks can
                    # invalidate the old balance proof; never post their delta.
                    conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(
                        qualification=balance_invalidated_evidence(account['qualification']), state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
                    conn.execute(update(protection).where(protection.c.account_key == scope.account_key,
                        protection.c.state == 'HOSTED_ACTIVE').values(state='REQUIRED', coverage_until=0))
                    self._event(conn, scope.account_key, 'BALANCE_BASELINE_INVALIDATED', observation_id,
                        {'reason':'DEMO_EXTERNAL_BALANCE_ACTIVITY', 'previous_qualification_hash':digest(account['qualification'] or {})})
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

    def health(self, scope, *, now=None):
        import time
        now = int(time.time()) if now is None else now
        with self.engine.connect() as conn:
            def count(table, predicate):
                return conn.execute(select(func.count()).select_from(table).where(predicate)).scalar_one()
            return {'fill_count': count(fills, fills.c.account_key == scope.account_key),
                    'risk_breaches': count(outbox, (outbox.c.account_key == scope.account_key) & (outbox.c.kind == 'EXECUTION_RISK_BREACH')),
                    'unknown': count(commands, (commands.c.account_key == scope.account_key) & commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN'])),
                    'unprotected': count(protection, (protection.c.account_key == scope.account_key) & (protection.c.state != 'SATISFIED') & ((protection.c.state != 'HOSTED_ACTIVE') | (protection.c.coverage_until <= now))),
                    'pending_inbox': count(inbox, (inbox.c.state != 'PROCESSED') & inbox.c.observation_id.in_(select(observations.c.observation_id).where(observations.c.account_key == scope.account_key)))}

    def save_cursor(self, scope, symbol, cursor, *, complete, expected_revision, credential_revision=None, stream_generation=None):
        with self.transaction(scope.account_key) as conn:
            account_revision = conn.execute(select(accounts.c.credential_revision).where(accounts.c.account_key == scope.account_key)).scalar_one()
            if ((credential_revision is None and self.engine.dialect.name == 'postgresql') or
                    (credential_revision is not None and (type(credential_revision) is not int or account_revision != credential_revision))):
                raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            if self.engine.dialect.name == 'postgresql':
                require_physical_generation(conn, scope, stream_generation)
            row = conn.execute(select(reconciliations).where(reconciliations.c.account_key == scope.account_key, reconciliations.c.symbol == symbol)).mappings().first()
            if row is None:
                if expected_revision != 0:
                    raise DemoError('DEMO_CURSOR_CONFLICT')
                conn.execute(insert(reconciliations).values(account_key=scope.account_key, symbol=symbol, cursor=cursor, complete=complete, revision=1))
            elif row['revision'] != expected_revision:
                raise DemoError('DEMO_CURSOR_CONFLICT')
            else:
                conn.execute(update(reconciliations).where(reconciliations.c.account_key == scope.account_key, reconciliations.c.symbol == symbol).values(cursor=cursor, complete=complete, revision=expected_revision + 1))

    def rotate_credential(self, scope, *, credential_ref, fingerprint, expected_revision, physical_owner=None):
        postgres = self.engine.dialect.name == 'postgresql'
        if postgres:
            if physical_owner is None or type(physical_owner.generation) is not int or physical_owner.generation < 2:
                raise DemoError('DEMO_CREDENTIAL_REBIND_FENCING_UNQUALIFIED')
            physical_owner.validate(scope)
        # PostgreSQL takes physical ownership first, then the epoch sender lock.
        guard = self.owner_guard(scope) if postgres else self._lock
        with guard as sender, self.transaction(scope.account_key) as conn:
            if postgres:
                sender.validate(); physical_owner.validate(scope)
                require_physical_generation(conn, scope, physical_owner.generation)
            row = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if row['revision'] != expected_revision:
                raise DemoError('DEMO_CREDENTIAL_REVISION_CONFLICT')
            pending = conn.execute(select(inbox.c.observation_id).where(inbox.c.state != 'PROCESSED', inbox.c.observation_id.in_(
                select(observations.c.observation_id).where(observations.c.account_key == scope.account_key)))).first()
            if pending:
                raise DemoError('DEMO_INBOX_RECONCILIATION_REQUIRED')
            if postgres:
                unresolved = conn.execute(select(commands.c.command_id).where(commands.c.account_key == scope.account_key,
                    commands.c.state.in_(['MAY_HAVE_SENT','UNKNOWN']))).first()
                if unresolved or row['frozen']: raise DemoError('DEMO_RECOVERY_REQUIRED')
                sender.validate(); physical_owner.validate(scope)
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(credential_ref=credential_ref,
                fingerprint=fingerprint, revision=expected_revision + 1, credential_revision=row['credential_revision'] + 1,
                state='RECOVERY_REQUIRED' if postgres else 'READ_ONLY', qualification=None))

    def acknowledge(self, scope, command_id, response, *, validator=None):
        row = self.command(scope, command_id)
        request = row['request']
        target = request.get('newClientOrderId', request.get('origClientOrderId'))
        if response.get('symbol') != request['symbol'] or response.get('origClientOrderId' if row['method'] == 'DELETE' else 'clientOrderId') != target or type(response.get('orderId')) is not int:
            raise DemoError('DEMO_ACK_BINDING_INVALID')
        with self.transaction(scope.account_key) as conn:
            if validator is not None: validator()
            conn.execute(update(commands).where(commands.c.command_id == command_id, commands.c.scope_key == scope.key,
                commands.c.state == 'MAY_HAVE_SENT').values(state='ACKNOWLEDGED', order_id=str(response['orderId'])))
            self._event(conn, scope.account_key, 'COMMAND_ACKNOWLEDGED', command_id, {'command_id': command_id})

    def pause(self, scope, reason='DEMO_RECOVERY_REQUIRED'):
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
            # Repeated safety pauses must not collapse into one event: a new
            # stream gap after a final receipt must change its activity hash.
            from uuid import uuid4
            self._event(conn, scope.account_key, 'ACCOUNT_PAUSED', uuid4().hex, {'reason': reason})

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

    def invalidate_balance_baseline(self, scope, *, expected_revision, stream=None):
        """Revoke qualification without overwriting historical balance facts."""
        from uuid import uuid4
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            row = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if row['revision'] != expected_revision: raise DemoError('DEMO_ACCOUNT_READ_RACED')
            if stream is not None:
                if hasattr(stream, 'validate_owner'): stream.validate_owner()
                if (not stream.healthy or row['credential_ref'] != stream.credential_ref or
                        row['credential_revision'] != stream.credential_revision):
                    raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            if self.engine.dialect.name == 'postgresql':
                if stream is None: raise DemoError('DEMO_PHYSICAL_GENERATION_REJECTED')
                require_physical_generation(conn, scope, getattr(stream, 'physical_generation', None))
            unresolved = conn.execute(select(inbox.c.observation_id).join(observations,
                inbox.c.observation_id == observations.c.observation_id).where(observations.c.account_key == scope.account_key,
                observations.c.source != 'WS_ACCOUNT_POSITION', inbox.c.state != 'PROCESSED')).first()
            active = conn.execute(select(commands.c.command_id).where(commands.c.account_key == scope.account_key,
                commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED', 'OBSERVED']),
                (commands.c.order_status.is_(None) | commands.c.order_status.not_in(['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH']) |
                 (commands.c.reserve_amount != '0')))).first()
            if unresolved or active: raise DemoError('DEMO_BALANCE_RECONCILIATION_INCOMPLETE')
            terminals = conn.execute(select(commands).where(commands.c.account_key == scope.account_key,
                commands.c.method == 'POST', commands.c.order_status.in_(['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH'])).limit(100001)).mappings().all()
            if len(terminals) > 100000: raise DemoError('DEMO_BALANCE_RECONCILIATION_INCOMPLETE')
            all_fills = conn.execute(select(fills.c.symbol, fills.c.order_id, fills.c.payload).where(
                fills.c.account_key == scope.account_key).limit(100001)).all()
            if len(all_fills) > 100000: raise DemoError('DEMO_BALANCE_RECONCILIATION_INCOMPLETE')
            totals = {}
            for symbol, order_id, item in all_fills:
                quantity, quote = totals.get((symbol, order_id), (Decimal(0), Decimal(0)))
                totals[(symbol, order_id)] = (quantity + decimal(item['quantity']), quote + decimal(item['quote_quantity']))
            for command in terminals:
                expected = decimal(command['cumulative_qty'])
                if expected == 0: continue
                if command['order_id'] is None: raise DemoError('DEMO_BALANCE_RECONCILIATION_INCOMPLETE')
                quantity, quote = totals.get((command['request']['symbol'], command['order_id']), (Decimal(0), Decimal(0)))
                if quantity != expected:
                    raise DemoError('DEMO_BALANCE_RECONCILIATION_INCOMPLETE')
                if command['request']['type'] == 'MARKET' and quote != decimal(command['cumulative_quote']):
                    raise DemoError('DEMO_BALANCE_RECONCILIATION_INCOMPLETE')
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(
                qualification=balance_invalidated_evidence(row['qualification']), state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
            self._event(conn, scope.account_key, 'BALANCE_BASELINE_INVALIDATED', uuid4().hex,
                {'reason':'DEMO_BALANCE_MISMATCH', 'previous_qualification_hash':digest(row['qualification'] or {})})

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

    def record_unresolved_trade(self, scope, *, symbol, trade, credential_ref, credential_revision, stream_generation):
        """Record an unresolved fact without attributing it to a strategy.

        It may be an external trade or one of our own unresolved sends. A hash
        is an audit marker, not proof of ownership or history completeness.
        """
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if (account['credential_ref'] != credential_ref or type(credential_revision) is not int or
                    account['credential_revision'] != credential_revision):
                raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            if self.engine.dialect.name == 'postgresql':
                require_physical_generation(conn, scope, stream_generation)
            order_id = trade.get('orderId')
            if type(order_id) is int and order_id >= 0:
                matches = [row for row in conn.execute(select(commands).where(commands.c.account_key == scope.account_key,
                    commands.c.namespace == 'SPOT', commands.c.order_id == str(order_id))).mappings()
                    if row['request'].get('symbol') == symbol]
                if len(matches) == 1:
                    return DemoScope(**matches[0]['scope'])
            fact_hash = digest(trade)
            # Retain documented economic fields, not arbitrary venue payload
            # fields or credentials. Unknown/oversized fields remain blocked.
            fields = {'symbol','id','orderId','orderListId','price','qty','quoteQty',
                      'commission','commissionAsset','time','isBuyer','isMaker','isBestMatch'}
            import re
            def safe_value(key, value):
                if key in {'symbol', 'commissionAsset'}:
                    return type(value) is str and re.fullmatch(r'[A-Z0-9]{1,20}', value) is not None
                if key in {'price', 'qty', 'quoteQty', 'commission'}:
                    try:
                        return type(value) is str and decimal(value) >= 0
                    except DemoError:
                        return False
                if key in {'id', 'orderId', 'orderListId', 'time'}:
                    return type(value) is int and (-1 if key == 'orderListId' else 0) <= value < 2**63
                return key in {'isBuyer', 'isMaker', 'isBestMatch'} and type(value) is bool
            fact = {key:value for key, value in trade.items() if key in fields and safe_value(key, value)}
            complete = set(fact) == fields and set(trade) == fields
            # Expose only bounded numeric venue identifiers for later review.
            def venue_id(value):
                return str(value) if type(value) is int and 0 <= value < 2**63 else None
            self._event(conn, scope.account_key, 'UNRESOLVED_TRADE_HISTORY', fact_hash,
                        {'symbol':symbol, 'trade_hash':fact_hash, 'credential_revision':credential_revision,
                         'stream_generation':stream_generation, 'trade_id':venue_id(trade.get('id')),
                         'order_id':venue_id(trade.get('orderId')), 'trade_fact':fact,
                         'trade_fact_complete':complete})
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(
                state='RECOVERY_REQUIRED', revision=accounts.c.revision + 1))
            return None

    def save_strategy(self, scope, config, *, expected_revision):
        if type(config.get('revision')) is not int or config['revision'] != expected_revision + 1 or type(config.get('authorized')) is not bool:
            raise DemoError('DEMO_STRATEGY_REVISION_INVALID')
        if decimal(config['budget'], positive=True) > 10000:
            raise DemoError('DEMO_PROJECT_BUDGET_EXCEEDED')
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            row = conn.execute(select(strategies).where(strategies.c.scope_key == scope.key)).mappings().first()
            if (row['revision'] if row else 0) != expected_revision:
                raise DemoError('DEMO_STRATEGY_REVISION_CONFLICT')
            values = {'scope_key': scope.key, 'account_key': scope.account_key, 'scope': asdict(scope), 'revision': config['revision'], 'config': config}
            if row:
                conn.execute(update(strategies).where(strategies.c.scope_key == scope.key).values(**values))
            else:
                conn.execute(insert(strategies).values(**values))
            # Grant changes invalidate pending native decisions without altering
            # historical PAPER or Demo records.
            if row:
                conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(revision=accounts.c.revision + 1, state='PAUSED'))

    def strategy(self, scope):
        with self.engine.connect() as conn:
            row = conn.execute(select(strategies.c.config).where(strategies.c.scope_key == scope.key)).scalar_one_or_none()
        if row is None:
            raise DemoError('DEMO_STRATEGY_UNREGISTERED')
        return row

    def decision(self, scope, run_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(decisions).where(decisions.c.scope_key == scope.key, decisions.c.run_id == run_id)).mappings().first()
            return dict(row) if row else None

    def begin_decision(self, scope, run_id, snapshot, strategy, source, memory_context=None, *, credential_revision=None, stream_generation=None):
        binding_hash = digest([snapshot['snapshot_hash'], strategy, memory_context])
        with self.transaction(scope.account_key) as conn:
            if self.engine.dialect.name == 'postgresql':
                account_revision = conn.execute(select(accounts.c.credential_revision).where(accounts.c.account_key == scope.account_key)).scalar_one()
                if type(credential_revision) is not int or account_revision != credential_revision: raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
                require_physical_generation(conn, scope, stream_generation)
            row = conn.execute(select(decisions).where(decisions.c.scope_key == scope.key, decisions.c.run_id == run_id)).mappings().first()
            if row:
                if row['binding_hash'] != binding_hash:
                    raise DemoError('DEMO_DECISION_IDEMPOTENCY_CONFLICT')
                raise DemoError('DEMO_MODEL_RUN_NOT_RETRYABLE')
            conn.execute(insert(decisions).values(scope_key=scope.key, run_id=run_id, account_key=scope.account_key,
                state='IN_FLIGHT', credential_revision=credential_revision, stream_generation=stream_generation,
                source=source, binding_hash=binding_hash, snapshot=snapshot, strategy=strategy, memory_context=memory_context))
        return self.decision(scope, run_id)

    def finish_decision(self, scope, run_id, *, result=None, error=None, credential_revision=None, stream_generation=None):
        with self.transaction(scope.account_key) as conn:
            if self.engine.dialect.name == 'postgresql':
                account_revision = conn.execute(select(accounts.c.credential_revision).where(accounts.c.account_key == scope.account_key)).scalar_one()
                if type(credential_revision) is not int or account_revision != credential_revision: raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
                require_physical_generation(conn, scope, stream_generation)
                current = conn.execute(select(decisions.c.credential_revision, decisions.c.stream_generation).where(
                    decisions.c.scope_key == scope.key, decisions.c.run_id == run_id)).first()
                if current is None or current[0] != credential_revision or current[1] != stream_generation:
                    raise DemoError('DEMO_DECISION_BINDING_CHANGED')
            conn.execute(update(decisions).where(decisions.c.scope_key == scope.key, decisions.c.run_id == run_id,
                decisions.c.state == 'IN_FLIGHT').values(state='FAILED' if error else 'ASSESSED', result=result, error=error))
            self._event(conn, scope.account_key, 'DECISION_RECORDED', digest([scope.key, run_id]), {'scope_key': scope.key, 'run_id': run_id})
        return self.decision(scope, run_id)

    def bind_decision_command(self, scope, run_id, command_id):
        with self.transaction(scope.account_key) as conn:
            row = conn.execute(select(decisions).where(decisions.c.scope_key == scope.key, decisions.c.run_id == run_id)).mappings().one()
            if row['command_id'] and row['command_id'] != command_id:
                raise DemoError('DEMO_DECISION_COMMAND_CONFLICT')
            conn.execute(update(decisions).where(decisions.c.scope_key == scope.key, decisions.c.run_id == run_id).values(state='STAGED', command_id=command_id))

    def pending_observations(self, scope, limit=100, *, exclude_account_positions=False):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise DemoError('DEMO_INBOX_LIMIT_INVALID')
        with self.engine.connect() as conn:
            query = select(observations).join(inbox, observations.c.observation_id == inbox.c.observation_id).where(
                observations.c.account_key == scope.account_key, inbox.c.state == 'PENDING')
            if exclude_account_positions: query = query.where(observations.c.source != 'WS_ACCOUNT_POSITION')
            return [dict(row) for row in conn.execute(query.order_by(observations.c.received_at, observations.c.observation_id).limit(limit)).mappings()]

    def record_account_snapshot(self, scope, facts, *, observed_at, expected_revision):
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            if account['revision'] != expected_revision or account['frozen']:
                raise DemoError('DEMO_ACCOUNT_READ_RACED')
            previous = conn.execute(select(account_snapshots).where(account_snapshots.c.account_key == scope.account_key)).mappings().first()
            hashed = digest(facts)
            values = {'account_key': scope.account_key, 'facts_hash': hashed, 'facts': facts, 'observed_at': observed_at}
            if previous:
                conn.execute(update(account_snapshots).where(account_snapshots.c.account_key == scope.account_key).values(**values))
                if previous['facts_hash'] != hashed:
                    conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(revision=accounts.c.revision + 1))
            else:
                conn.execute(insert(account_snapshots).values(**values))
        return self.account(scope)

    def active_orders(self, scope):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(commands).where(commands.c.scope_key == scope.key, commands.c.method == 'POST',
                commands.c.state != 'ABANDONED', (commands.c.order_status.is_(None) | commands.c.order_status.not_in(['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH']) | (commands.c.reserve_amount != '0')))).mappings()]

    def mitigation_lineage(self, scope, fill_keys):
        keys = set(fill_keys)
        with self.engine.connect() as conn:
            rows = conn.execute(select(commands.c.command_id, commands.c.risk_receipt).where(
                commands.c.scope_key == scope.key, commands.c.purpose == 'PROTECTION')).all()
        return sorted(row.command_id for row in rows if keys.intersection((row.risk_receipt or {}).get('protection_fill_keys', [])))

    def protection_duties(self, scope):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(protection).where(protection.c.scope_key == scope.key, protection.c.state != 'SATISFIED').order_by(protection.c.opened_at, protection.c.fill_key)).mappings()]

    def operator_view(self, scope, *, limit=50):
        """Project bounded, scope-bound durable facts, not an assertion of completeness."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise DemoError('DEMO_OBSERVATION_LIMIT_REJECTED')
        def scoped(table):
            return (table.c.account_key == scope.account_key, table.c.scope_key == scope.key)
        with self.engine.connect() as conn:
            ds = conn.execute(select(decisions).where(*scoped(decisions)).order_by(decisions.c.run_id.desc()).limit(limit)).mappings().all()
            cs = conn.execute(select(commands).where(*scoped(commands)).order_by(commands.c.command_id.desc()).limit(limit)).mappings().all()
            os = conn.execute(select(observations, inbox.c.state.label('inbox_state')).outerjoin(inbox, inbox.c.observation_id == observations.c.observation_id).where(*scoped(observations)).order_by(observations.c.received_at.desc(), observations.c.observation_id.desc()).limit(limit)).mappings().all()
            fs = conn.execute(select(fills).where(*scoped(fills)).order_by(fills.c.occurred_at.desc(), fills.c.fill_key.desc()).limit(limit)).mappings().all()
            unresolved = sorted(unresolved_trade_history(conn, scope.account_key),
                key=lambda row: (row['created_at'], row['event_id']), reverse=True)[:limit]
            position_count = conn.execute(select(func.count()).select_from(observations.join(inbox,
                observations.c.observation_id == inbox.c.observation_id)).where(observations.c.account_key == scope.account_key,
                observations.c.source == 'WS_ACCOUNT_POSITION', inbox.c.state == 'PENDING')).scalar_one()
        def decision(row):
            result = row['result'] if isinstance(row['result'], dict) else {}
            body = result.get('decision') if isinstance(result.get('decision'), dict) else {}
            return {'run_id':row['run_id'], 'state':row['state'], 'source':row['source'], 'command_id':row['command_id'],
                    'error':row['error'], 'summary':body.get('summary'), 'assessment_status':body.get('assessment_status'), 'action':body.get('action')}
        confirmation = {'DURABLE_READY':'NOT_SENT', 'MAY_HAVE_SENT':'OUTCOME_UNKNOWN', 'UNKNOWN':'OUTCOME_UNKNOWN',
                        'ACKNOWLEDGED':'VENUE_ACKNOWLEDGED', 'OBSERVED':'VENUE_OBSERVED', 'ABANDONED':'ABANDONED_UNSENT'}
        def command(row):
            request = row['request'] if isinstance(row['request'], dict) else {}
            return {'command_id':row['command_id'], 'client_id':row['client_id'], 'state':row['state'],
                    'confirmation':confirmation.get(row['state'], 'UNVERIFIED'), 'method':row['method'], 'purpose':row['purpose'],
                    **{key:request.get(key) for key in ('symbol','side','type','quantity','price')},
                    'order_id':row['order_id'], 'order_status':row['order_status'], 'cumulative_qty':row['cumulative_qty'],
                    'cumulative_quote':row['cumulative_quote'], 'reason':row['reason']}
        def observation(row):
            payload = row['payload'] if isinstance(row['payload'], dict) else {}
            fields = ('symbol','orderId','clientOrderId','status','executedQty','cummulativeQuoteQty','s','i','c','X','z','Z')
            return {'observation_id':row['observation_id'], 'source':row['source'], 'received_at':row['received_at'],
                    'inbox_state':row['inbox_state'], 'fields':{key:payload[key] for key in fields if key in payload}}
        def fill(row):
            payload = row['payload'] if isinstance(row['payload'], dict) else {}
            fields = ('side','quantity','price','quote_quantity','commission','commission_asset')
            return {'symbol':row['symbol'], 'order_id':row['order_id'], 'trade_id':row['trade_id'], 'occurred_at':row['occurred_at'],
                    'confirmation':'VENUE_OBSERVED', **{key:payload.get(key) for key in fields}}
        return {'decisions':[decision(row) for row in ds], 'orders':[command(row) for row in cs],
                'reports':[observation(row) for row in os], 'fills':[fill(row) for row in fs],
                'unresolved_trade_history':[{**{key:row['payload'].get(key) for key in ('symbol','trade_hash','trade_id','order_id','trade_fact_complete')},
                    'recorded_at':row['created_at']} for row in unresolved],
                'account_position_review':{'state':'PENDING' if position_count else 'NONE', 'count':position_count,
                    'execution_authorized':False, 'blocker':'DEMO_ACCOUNT_STREAM_SNAPSHOT_ORDERING_UNQUALIFIED' if position_count else None},
                'pnl':{'state':'UNAVAILABLE', 'realized_usdt':None, 'unrealized_usdt':None, 'reason':'DEMO_PNL_EVIDENCE_INCOMPLETE'},
                'limit':limit, 'complete_history':False}

    def risk_summary(self, scope, *, mark, now):
        """Weighted-cost inventory with UTC-day realized loss plus open drawdown.

        Unknown third-asset fee valuations block increased risk, never assume zero.
        """
        mark = decimal(mark, positive=True)
        quantity = cost = realized_loss = Decimal(0)
        complete = True
        with self.engine.connect() as conn:
            history = conn.execute(select(fills.c.payload, fills.c.occurred_at).where(fills.c.scope_key == scope.key).order_by(fills.c.occurred_at, fills.c.trade_id)).all()
        history.sort(key=lambda row: (row[1], int(row[0]['trade_id'])))
        for item, occurred_at in history:
            qty, quote, fee = decimal(item['quantity']), decimal(item['quote_quantity']), decimal(item['commission'])
            if fee and item['commission_asset'] not in {'BTC', 'USDT'}: complete = False
            if occurred_at <= 0: complete = False
            base_fee = fee if item['commission_asset'] == 'BTC' else Decimal(0)
            quote_fee = fee if item['commission_asset'] == 'USDT' else Decimal(0)
            if item['side'] == 'BUY':
                quantity += qty - base_fee; cost += quote + quote_fee
            else:
                consumed = qty + base_fee
                if quantity <= 0 or consumed > quantity:
                    raise DemoError('DEMO_UNOWNED_EXPOSURE')
                basis = cost * consumed / quantity
                if occurred_at // 86400 == now // 86400:
                    realized_loss += max(Decimal(0), basis - (quote - quote_fee))
                quantity -= consumed; cost -= basis
        return {'loss': realized_loss + max(Decimal(0), cost - quantity * mark), 'complete': complete, 'quantity': quantity}

    def renew_hosted_protection(self, scope, *, fill_key, now, until, authorization_ref):
        if not now < until <= now + 5:
            raise DemoError('DEMO_PROTECTION_LEASE_INVALID')
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            row = conn.execute(select(protection).where(protection.c.fill_key == fill_key, protection.c.scope_key == scope.key)).mappings().one()
            if not row['plan'] or row['plan'].get('mode') != 'HOSTED' or row['plan']['authorization_ref'] != authorization_ref or row['state'] not in {'REQUIRED', 'HOSTED_ACTIVE'}:
                raise DemoError('DEMO_PROTECTION_AUTHORITY_INVALID')
            pending = conn.execute(select(inbox.c.observation_id).join(observations,
                inbox.c.observation_id == observations.c.observation_id).where(observations.c.account_key == scope.account_key,
                inbox.c.state != 'PROCESSED')).first()
            if pending: raise DemoError('DEMO_PROTECTION_RECONCILIATION_REQUIRED')
            conn.execute(update(protection).where(protection.c.fill_key == fill_key).values(state='HOSTED_ACTIVE', coverage_until=until))

    def bind_mitigation(self, scope, fill_keys, command_id):
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            for key in fill_keys:
                row = conn.execute(select(protection).where(protection.c.fill_key == key, protection.c.scope_key == scope.key)).mappings().one()
                if row['mitigation_command_id'] and row['mitigation_command_id'] != command_id:
                    raise DemoError('DEMO_PROTECTION_COMMAND_CONFLICT')
                conn.execute(update(protection).where(protection.c.fill_key == key).values(state='MITIGATION_PENDING', mitigation_command_id=command_id, coverage_until=0))

    def abandon_unneeded_protection(self, scope):
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            if conn.execute(select(protection.c.fill_key).where(protection.c.scope_key == scope.key, protection.c.state != 'SATISFIED')).first():
                return 0
            rows = conn.execute(select(commands.c.command_id).where(commands.c.scope_key == scope.key,
                commands.c.purpose == 'PROTECTION', commands.c.state == 'DURABLE_READY')).scalars().all()
            for command_id in rows:
                conn.execute(update(commands).where(commands.c.command_id == command_id).values(state='ABANDONED', reserve_amount='0', reason='DEMO_NO_REMAINING_PROTECTION_DUTY'))
                self._event(conn, scope.account_key, 'COMMAND_ABANDONED_UNSENT', command_id, {'command_id': command_id})
            return len(rows)

    def abandon_stale_unsent(self, scope, *, now):
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            rows = conn.execute(select(commands).where(commands.c.scope_key == scope.key, commands.c.state == 'DURABLE_READY',
                ((commands.c.expires_at <= now) | (commands.c.admission_revision != account['revision']) | (commands.c.credential_revision != account['credential_revision'])))).mappings().all()
            for row in rows:
                conn.execute(update(commands).where(commands.c.command_id == row['command_id']).values(state='ABANDONED', reserve_amount='0', reason='DEMO_UNSENT_AUTHORITY_EXPIRED'))
                conn.execute(update(protection).where(protection.c.scope_key == scope.key, protection.c.mitigation_command_id == row['command_id'], protection.c.state == 'MITIGATION_PENDING').values(state='REQUIRED', mitigation_command_id=None, coverage_until=0))
                self._event(conn, scope.account_key, 'COMMAND_ABANDONED_UNSENT', row['command_id'], {'command_id': row['command_id']})
            return len(rows)

    def cancel_lineage(self, scope, original_client_id):
        with self.engine.connect() as conn:
            rows = conn.execute(select(commands.c.command_id, commands.c.request).where(
                commands.c.scope_key == scope.key, commands.c.method == 'DELETE')).all()
        return sorted(row.command_id for row in rows if row.request.get('origClientOrderId') == original_client_id)

    def pending_cancel(self, scope, original_client_id):
        with self.engine.connect() as conn:
            candidates = conn.execute(select(commands).where(commands.c.scope_key == scope.key, commands.c.method == 'DELETE',
                commands.c.state.in_(['DURABLE_READY', 'MAY_HAVE_SENT', 'UNKNOWN', 'ACKNOWLEDGED']))).mappings().all()
        matches = [dict(row) for row in candidates if row['request']['origClientOrderId'] == original_client_id]
        if len(matches) > 1:
            raise DemoError('DEMO_CANCEL_IDENTITY_CONFLICT')
        return matches[0] if matches else None

    def plan_head(self, scope):
        with self.engine.connect() as conn:
            row = conn.execute(select(plan_heads).where(plan_heads.c.scope_key == scope.key, plan_heads.c.state == 'ACTIVE')).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def duties_hash(rows):
        return digest(sorted([{'fill_key': row['fill_key'], 'plan': row['plan'], 'remaining_quantity': row['remaining_quantity'],
            'entry_command_id': row['entry_command_id'], 'opened_at': row['opened_at']} for row in rows], key=lambda row: row['fill_key']))

    def exit_update(self, scope, run_id):
        with self.engine.connect() as conn:
            return conn.execute(select(plan_updates.c.receipt).where(plan_updates.c.scope_key == scope.key, plan_updates.c.run_id == run_id)).scalar_one_or_none()

    def apply_exit_update(self, scope, run_id, *, candidate, expected_account_revision, expected_plan_revision, expected_duties_hash, strategy, now, bid, clock=None):
        from trading_system.contracts.wire import ContractCodec, validate_transition
        identifier = 'exit:' + digest([scope.key, run_id])
        request_hash = digest([scope.key, run_id, candidate, expected_plan_revision, expected_duties_hash])
        with self.safety_guard(scope), self.transaction(scope.account_key) as conn:
            now = int(clock()) if clock is not None else now
            prior = conn.execute(select(plan_updates).where(plan_updates.c.management_id == identifier)).mappings().first()
            if prior:
                if prior['request_hash'] != request_hash: raise DemoError('DEMO_EXIT_UPDATE_IDEMPOTENCY_CONFLICT')
                return prior['receipt']
            account = conn.execute(select(accounts).where(accounts.c.account_key == scope.account_key)).mappings().one()
            current_strategy = conn.execute(select(strategies.c.config).where(strategies.c.scope_key == scope.key)).scalar_one()
            head = conn.execute(select(plan_heads).where(plan_heads.c.scope_key == scope.key, plan_heads.c.state == 'ACTIVE')).mappings().first()
            decision = conn.execute(select(decisions).where(decisions.c.scope_key == scope.key, decisions.c.run_id == run_id)).mappings().one()
            if account['frozen'] or account['revision'] != expected_account_revision or current_strategy != strategy or not strategy.get('authorized') or now >= strategy['authorized_until'] or not head or head['revision'] != expected_plan_revision:
                raise DemoError('DEMO_EXIT_UPDATE_BINDING_CHANGED')
            from datetime import datetime
            if any(datetime.fromisoformat(value).timestamp() <= now for value in (candidate['valid_until'], candidate['entry']['entry_deadline'], candidate['exit_plan']['review_due_at'])):
                raise DemoError('DEMO_EXIT_UPDATE_EXPIRED')
            if decision['state'] != 'ASSESSED' or (decision['result'] or {}).get('decision', {}).get('proposal') != candidate:
                raise DemoError('DEMO_EXIT_UPDATE_RECEIPT_INVALID')
            active = conn.execute(select(commands.c.command_id).where(commands.c.scope_key == scope.key, commands.c.method == 'POST', commands.c.state != 'ABANDONED',
                (commands.c.order_status.is_(None) | commands.c.order_status.not_in(['FILLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH']) | (commands.c.reserve_amount != '0')))).first()
            pending = conn.execute(select(inbox.c.observation_id).where(inbox.c.state != 'PROCESSED', inbox.c.observation_id.in_(select(observations.c.observation_id).where(observations.c.account_key == scope.account_key)))).first()
            unknown = conn.execute(select(commands.c.command_id).where(commands.c.account_key == scope.account_key, commands.c.state.in_(['MAY_HAVE_SENT', 'UNKNOWN']))).first()
            duties = [dict(row) for row in conn.execute(select(protection).where(protection.c.scope_key == scope.key, protection.c.state != 'SATISFIED')).mappings()]
            if active or pending or unknown or not duties or any(row['state'] == 'MITIGATION_PENDING' for row in duties):
                raise DemoError('DEMO_EXIT_UPDATE_RECOVERY_REQUIRED')
            if self.duties_hash(duties) != expected_duties_hash:
                raise DemoError('DEMO_EXIT_UPDATE_BINDING_CHANGED')
            codec = ContractCodec()
            try: validate_transition(codec.decode('decision-proposal', head['proposal']), codec.decode('decision-proposal', candidate))
            except Exception: raise DemoError('DEMO_EXIT_UPDATE_TRANSITION_INVALID') from None
            if candidate['action'] != 'UPDATE_EXIT' or candidate.get('requested_leverage', 1) != head['proposal'].get('requested_leverage', 1) or any(candidate['entry'][key] != head['proposal']['entry'][key] for key in ('order_preference', 'limit_price', 'maximum_entry_slippage_bps', 'allow_partial')):
                raise DemoError('DEMO_EXIT_UPDATE_ENTRY_CHANGED')
            exit_plan = candidate['exit_plan']; stop = decimal(exit_plan['stop_price'], positive=True)
            if exit_plan['stop_trigger'] != 'LAST' or stop >= decimal(bid, positive=True):
                raise DemoError('DEMO_EXIT_UPDATE_STOP_INVALID')
            for duty in duties:
                plan = duty['plan'] or {}
                if plan.get('mode') != 'HOSTED' or plan.get('stop_trigger', 'LAST') != 'LAST' or plan.get('authorization_ref') != strategy['authorization_ref']:
                    raise DemoError('DEMO_EXIT_UPDATE_PROTECTION_UNSUPPORTED')
                if stop < decimal(plan['stop_price']) or exit_plan['maximum_holding_seconds'] > plan['maximum_holding_seconds']:
                    raise DemoError('DEMO_EXIT_UPDATE_WOULD_LOOSEN')
                if now >= duty['opened_at'] + exit_plan['maximum_holding_seconds']:
                    raise DemoError('DEMO_EXIT_UPDATE_REDUCTION_DUE')
            for duty in duties:
                plan = {**duty['plan'], 'stop_price': str(stop), 'maximum_holding_seconds': exit_plan['maximum_holding_seconds'], 'proposal_id': candidate['proposal_id']}
                conn.execute(update(protection).where(protection.c.fill_key == duty['fill_key']).values(plan=plan))
            conn.execute(update(plan_heads).where(plan_heads.c.scope_key == scope.key).values(revision=head['revision'] + 1, proposal=candidate, predecessor_hash=digest(dict(head))))
            conn.execute(update(accounts).where(accounts.c.account_key == scope.account_key).values(revision=accounts.c.revision + 1))
            receipt = {'management_id': identifier, 'state': 'APPLIED', 'purpose': 'UPDATE_EXIT', 'execution_authorized': False,
                'scope_key': scope.key, 'run_id': run_id, 'previous_revision': head['revision'], 'plan_revision': head['revision'] + 1,
                'proposal_hash': digest(candidate), 'duty_ids': [row['fill_key'] for row in duties]}
            conn.execute(insert(plan_updates).values(management_id=identifier, scope_key=scope.key, account_key=scope.account_key, run_id=run_id, request_hash=request_hash, receipt=receipt))
            conn.execute(update(decisions).where(decisions.c.scope_key == scope.key, decisions.c.run_id == run_id).values(state='MANAGEMENT_APPLIED'))
            self._event(conn, scope.account_key, 'EXIT_PLAN_UPDATED', identifier, receipt)
            return receipt
