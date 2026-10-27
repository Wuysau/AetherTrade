"""Mandatory real-PostgreSQL synthetic venue tests; never exchange qualification."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import secrets
from threading import Barrier, Event
import time
from uuid import uuid4

import psycopg
import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec
from trading_system.engine.nautilus.bridge import BridgeAuthority, BridgeDenied
from trading_system.engine.paper import PaperEngine, SyntheticOrderEvent, paper_client_order_id
from trading_system.runtime.execution_owner import ExecutionOwner, OwnerDenied
from scripts.provision_paper_engine_roles import provision_paper_engine_roles, require_isolated_paper_database

ROOT = Path(__file__).resolve().parents[2]
NOW, EXPIRY = '2026-10-27T00:00:00Z', '2026-10-27T00:01:00Z'


@pytest.fixture(scope='module')
def paper_dsn():
    dsn = os.environ.get('PAPER_ENGINE_DATABASE_DSN')
    if not dsn or os.environ.get('PAPER_ENGINE_ISOLATED_DATABASE') != 'YES':
        pytest.fail('NOT_RUN: explicit isolated PAPER PostgreSQL is required')
    admin_dsn = os.environ.get('PAPER_ENGINE_ADMIN_DATABASE_DSN', dsn)
    suffix = uuid4().hex[:12]
    roles = provision_paper_engine_roles(admin_dsn, isolated_paper=True,
        runtime_role='paper_engine_runtime_' + suffix, event_role='paper_engine_events_' + suffix,
        runtime_password=secrets.token_urlsafe(32), event_password=secrets.token_urlsafe(32))
    with psycopg.connect(admin_dsn) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS paper_authority_sentinel (secret TEXT NOT NULL)')
    original = {key: os.environ.get(key) for key in
        ('PAPER_ENGINE_DATABASE_DSN', 'PAPER_ENGINE_ADMIN_DATABASE_DSN', 'PAPER_ENGINE_EVENT_DATABASE_DSN')}
    os.environ['PAPER_ENGINE_DATABASE_DSN'] = roles.runtime_dsn
    os.environ['PAPER_ENGINE_ADMIN_DATABASE_DSN'] = admin_dsn
    os.environ['PAPER_ENGINE_EVENT_DATABASE_DSN'] = roles.event_dsn
    yield roles.runtime_dsn
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class Case:
    def __init__(self, dsn):
        self.scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='synthetic-' + uuid4().hex,
            product=t.Product.SPOT, instrument_uid='synthetic-BTCUSDT', venue_leg='BOTH')
        self.lease = t.OwnerLease(shard=t.ExecutionShard(environment=t.Environment.PAPER,
            venue_account_id=self.scope.venue_account_id, product=self.scope.product,
            node_id='synthetic-node', execution_epoch=7), instance_id='synthetic-owner',
            expires_at=EXPIRY, deadline_monotonic_ns=10000)
        self.authorities = {}
        self.now = t.ClockReading(utc_now=NOW, monotonic_ns=100, clock_health='HEALTHY')
        self.dsn = dsn
        self.event_dsn = os.environ['PAPER_ENGINE_EVENT_DATABASE_DSN']
        self.event_revoked = False
        self.engine = self.restart()

    @contextmanager
    def guard(self, command_id):
        if command_id not in self.authorities:
            raise BridgeDenied('AUTHORITY_UNAVAILABLE')
        yield self.authorities[command_id]

    def restart(self, **kwargs):
        return PaperEngine(self.scope, dsn=self.dsn, authority_guard=self.guard,
            owner_strategy_id='synthetic-strategy', quantity_unit='BTC', price_currency='USDT',
            event_guard=self.event_guard, event_dsn=self.event_dsn, clock=lambda: self.now, **kwargs)

    @contextmanager
    def event_guard(self, event):
        if self.event_revoked or self.now.monotonic_ns >= 1000:
            raise BridgeDenied('EVENT_CONTROLLER_REVOKED_OR_EXPIRED')
        yield self.scope

    def command(self, operation='SUBMIT', target=None, quantity='1'):
        raw = json.loads((ROOT / 'tests/fixtures/contracts/examples/execution-command.paper.json').read_text())
        logical = uuid4().hex
        raw.update(command_id='cmd-' + logical, attempt_id='attempt-' + logical,
            venue_account_id=self.scope.venue_account_id, instrument_uid=self.scope.instrument_uid,
            logical_client_order_id=logical, venue_client_order_id=paper_client_order_id(self.scope, logical, operation),
            operation=operation, target_order_id=target, execution_epoch=7,
            state='MAY_HAVE_SENT', durable_record_ref='synthetic-committed-' + logical, created_at=NOW)
        raw['canonical_payload'] = {'side': 'BUY', 'order_type': 'LIMIT', 'quantity': quantity, 'price': '100'}
        raw['payload_hash'] = canonical_hash(raw['canonical_payload'])
        cmd = ContractCodec().decode('execution-command', raw)
        permit = t.DurablePermit(command_id=cmd.command_id, durable_record_ref=cmd.durable_record_ref,
            payload_hash=cmd.payload_hash, scope=self.scope, ownership_epoch=1, execution_epoch=7,
            policy_revision=1, expires_at=EXPIRY, deadline_monotonic_ns=1000)
        self.authorities[cmd.command_id] = BridgeAuthority(command=cmd, permit=permit,
            lease=self.lease, ownership_epoch=1, policy_revision=1)
        return cmd, permit

    def query(self, *commands, scope=None):
        return self.engine.query(t.ReconciliationCase(case_id='synthetic-query', scope=scope or self.scope,
            command_ids=tuple(c.command_id for c in commands), position_ids=(), missing=(),
            query_budget=t.QueryBudget(maximum_requests=1, window_seconds=60, minimum_interval_ms=1000,
                retry_after=None), attempts=0, next_query_at=NOW, state='OPEN'))

    def event(self, cmd, status, cumulative='0', price=None, event_id=None, **kwargs):
        return SyntheticOrderEvent(event_id=event_id or uuid4().hex, scope=self.scope,
            venue_client_order_id=cmd.venue_client_order_id, status=status,
            cumulative_quantity=Decimal(cumulative), price=Decimal(price) if price else None,
            occurred_at=NOW, **kwargs)


@pytest.fixture
def case(paper_dsn):
    return Case(paper_dsn)


def test_submit_is_acknowledgement_only(case):
    cmd, permit = case.command()
    receipt = case.engine.submit(cmd, permit)
    assert receipt.status == t.CommandState.VENUE_CONFIRMED
    report = case.query(cmd)
    order, = report.orders
    assert order.lifecycle_state == t.OrderLifecycle.SUBMITTED
    assert order.filled_quantity.value == 0 and order.leaves_quantity.value == 1
    assert report.fills == () and report.positions == ()
    assert 'PAPER_BALANCES_POSITIONS_PROTECTION_NOT_COVERED' in report.coverage.missing


def test_lost_ack_restart_query_original_and_never_resend(case):
    cmd, permit = case.command()
    case.engine = case.restart(lose_ack_for=frozenset({cmd.command_id}))
    with pytest.raises(TimeoutError):
        case.engine.submit(cmd, permit)
    case.engine = case.restart()
    before = case.query(cmd)
    assert before.orders[0].order.identity.venue_client_order_id == cmd.venue_client_order_id
    with pytest.raises(BridgeDenied, match='QUERY_SAME_ID'):
        case.engine.submit(cmd, permit)
    assert case.query(cmd).orders == before.orders


def test_partial_fill_cancel_unknown_duplicate_and_restart(case):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    partial = case.event(cmd, 'PARTIALLY_FILLED', '0.4', '99.1234567890123456789')
    assert case.engine.inject(partial) is True
    assert case.engine.inject(partial) is False
    order_id = case.query(cmd).orders[0].order.identity.venue_order_id
    cancel, cp = case.command('CANCEL', order_id)
    assert case.engine.cancel(cancel, cp).status == t.CommandState.UNKNOWN
    case.engine.inject(case.event(cmd, 'CANCEL_UNKNOWN', '0.4'))
    case.engine = case.restart()
    report = case.query(cancel)
    order, = report.orders
    assert order.lifecycle_state == t.OrderLifecycle.PARTIALLY_FILLED
    assert order.pending_operation == 'CANCEL' and order.command_certainty == 'UNKNOWN'
    assert order.filled_quantity.value == Decimal('0.4') and order.leaves_quantity.value == Decimal('0.6')
    fill, = report.fills
    assert fill.quantity.value == Decimal('0.4') and fill.price.value == Decimal('99.1234567890123456789')
    with pytest.raises(BridgeDenied, match='EVENT_ID_CONFLICT'):
        case.engine.inject(replace(partial, price=Decimal('1')))


@pytest.mark.parametrize('mutation', ['permit', 'scope', 'epoch', 'expired', 'authority', 'client', 'method'])
def test_invalid_authority_has_no_side_effect(case, mutation):
    cmd, permit = case.command()
    if mutation == 'permit':
        permit = replace(permit, payload_hash='f' * 64)
    elif mutation == 'scope':
        cmd = replace(cmd, instrument_uid='another-instrument')
    elif mutation == 'epoch':
        permit = replace(permit, execution_epoch=8)
    elif mutation == 'expired':
        case.now = replace(case.now, monotonic_ns=1001)
    elif mutation == 'authority':
        case.authorities.clear()
    elif mutation == 'client':
        cmd = replace(cmd, venue_client_order_id='unbound-client')
        case.authorities[cmd.command_id] = replace(case.authorities[cmd.command_id], command=cmd)
    with pytest.raises(BridgeDenied):
        (case.engine.cancel if mutation == 'method' else case.engine.submit)(cmd, permit)
    assert case.query(cmd).orders == ()


def test_terminal_transition_and_cumulative_quantity_fail_closed(case):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    for event in (case.event(cmd, 'FILLED', '0.4', '100'),
                  case.event(cmd, 'PARTIALLY_FILLED', '1.1', '100'),
                  case.event(cmd, 'SUBMITTED', '0.1')):
        with pytest.raises(BridgeDenied):
            case.engine.inject(event)
    case.engine.inject(case.event(cmd, 'FILLED', '1', '100'))
    with pytest.raises(BridgeDenied):
        case.engine.inject(case.event(cmd, 'EXPIRED', '1'))
    assert len(case.query(cmd).fills) == 1


def test_scoped_query_never_leaks_or_claims_absence_for_missing(case):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    with pytest.raises(BridgeDenied, match='QUERY_SCOPE_MISMATCH'):
        case.query(cmd, scope=replace(case.scope, venue_leg='LONG'))
    missing, _ = case.command()
    report = case.query(missing)
    assert report.orders == () and 'COMMAND_NOT_OBSERVED' in report.coverage.missing


@pytest.mark.parametrize('environment', [t.Environment.RESEARCH, t.Environment.TESTNET, t.Environment.LIVE])
def test_only_paper_environment(case, environment):
    with pytest.raises(BridgeDenied, match='PAPER_ONLY'):
        PaperEngine(replace(case.scope, environment=environment), dsn=case.dsn,
            authority_guard=case.guard, owner_strategy_id='synthetic', quantity_unit='BTC', price_currency='USDT')


def test_concurrent_duplicate_submit_has_one_durable_side_effect(case):
    cmd, permit = case.command()
    barrier = Barrier(2)

    def send():
        engine = case.restart()
        barrier.wait(timeout=5)
        try:
            engine.submit(cmd, permit)
            return 'ACK'
        except BridgeDenied:
            return 'DUPLICATE'

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: send(), range(2)))
    assert sorted(results) == ['ACK', 'DUPLICATE']
    assert len(case.query(cmd).orders) == 1
    with psycopg.connect(case.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM paper_engine_commands WHERE command_id=%s',
            (cmd.command_id,)).fetchone()[0] == 1


def test_separate_process_query_after_forced_post_commit_exit(case):
    from trading_system.contracts.canonical import domain_payload
    cmd, permit = case.command()
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PAPER_CHILD_CASE=json.dumps({
        'command': ContractCodec().encode(cmd), 'permit': domain_payload(permit),
        'scope': domain_payload(case.scope), 'lease': domain_payload(case.lease)}))
    script = '''
import json, os
from contextlib import contextmanager
from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec
from trading_system.engine.nautilus.bridge import BridgeAuthority
from trading_system.engine.paper import PaperEngine
v=json.loads(os.environ['PAPER_CHILD_CASE'])
scope=t.ScopeKey(**v['scope'])
lease=v['lease']; lease['shard']=t.ExecutionShard(**lease['shard']); lease=t.OwnerLease(**lease)
permit=v['permit']; permit['scope']=scope; permit=t.DurablePermit(**permit)
command=ContractCodec().decode('execution-command',v['command'])
@contextmanager
def guard(command_id):
    yield BridgeAuthority(command=command,permit=permit,lease=lease,ownership_epoch=1,policy_revision=1)
engine=PaperEngine(scope,dsn=os.environ['PAPER_ENGINE_DATABASE_DSN'],authority_guard=guard,
    owner_strategy_id='synthetic-strategy',quantity_unit='BTC',price_currency='USDT',
    clock=lambda:t.ClockReading(utc_now='2026-10-27T00:00:00Z',monotonic_ns=100,clock_health='HEALTHY'),
    lose_ack_for={command.command_id})
try: engine.submit(command,permit)
except TimeoutError: os._exit(23)
os._exit(24)
'''
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 23, result.stderr
    assert case.query(cmd).orders[0].order.identity.venue_client_order_id == cmd.venue_client_order_id
    with pytest.raises(BridgeDenied, match='QUERY_SAME_ID'):
        case.engine.submit(cmd, permit)


def test_amend_ack_does_not_change_order_until_exact_event(case):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    order_id = case.query(cmd).orders[0].order.identity.venue_order_id
    amend, ap = case.command('AMEND', order_id, quantity='0.8')
    assert case.engine.amend(amend, ap).status == t.CommandState.UNKNOWN
    assert case.query(cmd).orders[0].leaves_quantity.value == 1
    case.engine.inject(case.event(cmd, 'AMENDED', command_id=amend.command_id))
    assert case.query(amend).orders[0].leaves_quantity.value == Decimal('0.8')
    later, lp = case.command('AMEND', order_id, quantity='0.6')
    case.engine.amend(later, lp)
    with pytest.raises(BridgeDenied, match='PENDING_AMEND_COMMAND_REQUIRED'):
        case.engine.inject(case.event(cmd, 'AMENDED', command_id=amend.command_id))
    case.engine.inject(case.event(cmd, 'AMENDED', command_id=later.command_id))
    assert case.query(cmd).orders[0].leaves_quantity.value == Decimal('0.6')


@pytest.mark.parametrize('status', ['EXPIRED', 'REJECTED'])
def test_explicit_unfilled_terminal_events(case, status):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    case.engine.inject(case.event(cmd, status))
    assert case.query(cmd).orders[0].lifecycle_state == status
    assert not case.query(cmd).fills


def test_submitted_event_cannot_clear_unknown_cancel(case):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    order_id = case.query(cmd).orders[0].order.identity.venue_order_id
    cancel, cp = case.command('CANCEL', order_id)
    case.engine.cancel(cancel, cp)
    case.engine.inject(case.event(cmd, 'SUBMITTED'))
    assert case.query(cmd).orders[0].pending_operation == 'CANCEL'
    assert case.query(cmd).orders[0].command_certainty == 'UNKNOWN'


def test_event_injection_requires_explicit_trusted_controller(case):
    cmd, _ = case.command()
    engine = PaperEngine(case.scope, dsn=case.dsn, authority_guard=case.guard,
        owner_strategy_id='synthetic', quantity_unit='BTC', price_currency='USDT')
    with pytest.raises(BridgeDenied, match='SYNTHETIC_EVENT_CONTROLLER_REQUIRED'):
        engine.inject(case.event(cmd, 'FILLED', '1', '100'))


def test_partial_lifecycle_preserves_late_fills_during_unknown_cancel(case):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    case.engine.inject(case.event(cmd, 'PARTIALLY_FILLED', '0.4', '99'))
    order_id = case.query(cmd).orders[0].order.identity.venue_order_id
    cancel, cp = case.command('CANCEL', order_id)
    case.engine.cancel(cancel, cp)
    case.engine.inject(case.event(cmd, 'CANCEL_UNKNOWN', '0.4'))
    case.engine.inject(case.event(cmd, 'PARTIALLY_FILLED', '0.5', '101'))
    assert case.query(cmd).orders[0].command_certainty == 'UNKNOWN'
    case.engine.inject(case.event(cmd, 'CANCELED', '0.5'))
    report = case.query(cancel)
    assert report.orders[0].lifecycle_state == 'CANCELED'
    assert report.orders[0].filled_quantity.value == Decimal('0.5')
    assert report.orders[0].command_certainty == 'CONFIRMED'
    assert sum(f.quantity.value for f in report.fills) == Decimal('0.5')
    assert len(report.fills) == 2


def test_partial_then_fill_after_engine_restart(case):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    case.engine.inject(case.event(cmd, 'PARTIALLY_FILLED', '0.4', '99'))
    case.engine = case.restart()
    case.engine.inject(case.event(cmd, 'FILLED', '1', '100'))
    order, = case.query(cmd).orders
    assert order.lifecycle_state == 'FILLED' and order.leaves_quantity.value == 0
    assert sorted(f.quantity.value for f in case.query(cmd).fills) == [Decimal('0.4'), Decimal('0.6')]


def test_wrong_event_scope_and_backwards_fill_do_not_change_state(case):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    case.engine.inject(case.event(cmd, 'PARTIALLY_FILLED', '0.4', '99'))
    for event in (replace(case.event(cmd, 'FILLED', '1', '100'),
                          scope=replace(case.scope, venue_leg='LONG')),
                  case.event(cmd, 'PARTIALLY_FILLED', '0.3', '100')):
        with pytest.raises(BridgeDenied):
            case.engine.inject(event)
    assert case.query(cmd).orders[0].filled_quantity.value == Decimal('0.4')
    assert len(case.query(cmd).fills) == 1


def test_database_query_failure_produces_no_facts(case, monkeypatch):
    def fail():
        raise psycopg.OperationalError('injected synthetic database outage')
    monkeypatch.setattr(case.engine, '_connect', fail)
    report = case.query()
    assert not report.coverage.query_succeeded
    assert report.orders == report.fills == report.evidence == ()
    assert 'PAPER_DATABASE_QUERY_FAILED' in report.coverage.missing


def test_exact_paper_owner_composition_and_native_gate(case, tmp_path):
    @contextmanager
    def claim(shard):
        yield
    owner = ExecutionOwner(case.lease, journal_path=tmp_path / 'synthetic.jsonl',
        engine=case.engine, claim=claim, clock=lambda: case.now)
    assert owner.live_enabled is False and case.engine.live_enabled is False
    with pytest.raises(BridgeDenied, match='NATIVE_PATH_NOT_QUALIFIED'):
        case.engine.enable_native_path('anything')
    class FakePaperEngine(PaperEngine):
        pass
    arbitrary = object.__new__(FakePaperEngine)
    for engine, lease in ((arbitrary, case.lease), (case.engine, replace(case.lease,
            shard=replace(case.lease.shard, environment=t.Environment.LIVE))),
            (case.engine, replace(case.lease, shard=replace(case.lease.shard, venue_account_id='other')))):
        with pytest.raises(OwnerDenied, match='OFFLINE_OWNER_COMPOSITION_REQUIRED'):
            ExecutionOwner(lease, journal_path=tmp_path / 'denied.jsonl', engine=engine, claim=claim)


def _while_engine_is_blocked(case, monkeypatch, blocking_sql, operation, invalidate, *, controller=False):
    """Observe PostgreSQL's actual lock wait before changing current authority."""
    admin_dsn = os.environ.get('PAPER_ENGINE_ADMIN_DATABASE_DSN', case.dsn)
    connected, backend = Event(), []
    method = '_event_connect' if controller else '_connect'
    original_connect = getattr(case.engine, method)

    def instrumented_connect():
        conn = original_connect()
        backend.append(conn.execute('SELECT pg_backend_pid()').fetchone()['pg_backend_pid'])
        connected.set()
        return conn

    monkeypatch.setattr(case.engine, method, instrumented_connect)
    with psycopg.connect(admin_dsn) as holder:
        holder.execute(blocking_sql)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(operation)
            try:
                assert connected.wait(5), 'engine did not connect'
                with psycopg.connect(admin_dsn, autocommit=True) as observer:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        waiting = observer.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s',
                            (backend[0],)).fetchone()
                        if waiting and waiting[0] == 'Lock':
                            break
                        time.sleep(0.01)
                    else:
                        pytest.fail('engine never entered the real PostgreSQL lock wait')
                invalidate()
            finally:
                holder.rollback()
            with pytest.raises(BridgeDenied):
                future.result(timeout=5)
    monkeypatch.setattr(case.engine, method, original_connect)


@pytest.mark.parametrize('operation', ['CANCEL', 'AMEND'])
@pytest.mark.parametrize('invalidation', ['permit_expired', 'lease_expired', 'revoked', 'ownership_epoch'])
def test_locked_target_rechecks_expiry_and_current_authority(case, monkeypatch, operation, invalidation):
    from psycopg import sql
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    before = case.query(cmd).orders
    maintenance, mp = case.command(operation, before[0].order.identity.venue_order_id, quantity='0.8')

    def invalidate():
        if invalidation == 'permit_expired':
            case.now = replace(case.now, monotonic_ns=1001)
        elif invalidation == 'lease_expired':
            case.now = replace(case.now, monotonic_ns=10001)
        elif invalidation == 'revoked':
            case.authorities.pop(maintenance.command_id)
        else:
            case.authorities[maintenance.command_id] = replace(
                case.authorities[maintenance.command_id], ownership_epoch=2)

    blocking_sql = sql.SQL('SELECT 1 FROM paper_engine_orders WHERE order_id={} FOR UPDATE').format(
        sql.Literal(before[0].order.identity.venue_order_id))
    _while_engine_is_blocked(case, monkeypatch, blocking_sql,
        lambda: getattr(case.engine, operation.lower())(maintenance, mp), invalidate)
    assert case.query(cmd).orders == before
    with psycopg.connect(case.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM paper_engine_commands WHERE command_id=%s',
            (maintenance.command_id,)).fetchone()[0] == 0


@pytest.mark.parametrize('operation', ['SUBMIT', 'CANCEL', 'AMEND'])
@pytest.mark.parametrize('invalidation', ['expired', 'revoked'])
def test_late_denial_after_staged_mutation_rolls_back_entire_transaction(case, monkeypatch, operation, invalidation):
    original, permit = case.command()
    case.engine.submit(original, permit)
    before = case.query(original).orders
    command, cp = case.command(operation,
        None if operation == 'SUBMIT' else before[0].order.identity.venue_order_id, quantity='0.8')
    def invalidate():
        if invalidation == 'expired':
            case.now = replace(case.now, monotonic_ns=1001)
        else:
            case.authorities.pop(command.command_id)

    _while_engine_is_blocked(case, monkeypatch, 'LOCK TABLE paper_engine_commands IN SHARE MODE',
        lambda: getattr(case.engine, 'submit' if operation == 'SUBMIT' else operation.lower())(command, cp),
        invalidate)
    assert case.query(original).orders == before
    assert case.query(command).orders == ()
    assert len(case.query().orders) == 1


@pytest.mark.parametrize('invalidation', ['expired', 'revoked'])
def test_event_controller_rechecked_after_real_order_lock(case, monkeypatch, invalidation):
    from psycopg import sql
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    before = case.query(cmd).orders
    event = case.event(cmd, 'PARTIALLY_FILLED', '0.4', '100')

    def invalidate():
        if invalidation == 'expired':
            case.now = replace(case.now, monotonic_ns=1001)
        else:
            case.event_revoked = True

    blocking_sql = sql.SQL('SELECT 1 FROM paper_engine_orders WHERE order_id={} FOR UPDATE').format(
        sql.Literal(before[0].order.identity.venue_order_id))
    _while_engine_is_blocked(case, monkeypatch, blocking_sql,
        lambda: case.engine.inject(event), invalidate, controller=True)
    assert case.query(cmd).orders == before
    assert not case.query(cmd).fills


@pytest.mark.parametrize('invalidation', ['expired', 'revoked'])
def test_controller_late_denial_rolls_back_staged_amendment(case, monkeypatch, invalidation):
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    amend, ap = case.command('AMEND', case.query(cmd).orders[0].order.identity.venue_order_id, quantity='0.8')
    case.engine.amend(amend, ap)
    before = case.query(cmd).orders
    event = case.event(cmd, 'AMENDED', command_id=amend.command_id)

    def invalidate():
        if invalidation == 'expired':
            case.now = replace(case.now, monotonic_ns=1001)
        else:
            case.event_revoked = True

    _while_engine_is_blocked(case, monkeypatch, 'LOCK TABLE paper_engine_events IN SHARE MODE',
        lambda: case.engine.inject(event), invalidate, controller=True)
    assert case.query(cmd).orders == before
    with psycopg.connect(case.dsn) as conn:
        assert conn.execute('SELECT count(*) FROM paper_engine_events WHERE event_id=%s',
            (event.event_id,)).fetchone()[0] == 0


def test_runtime_and_controller_are_restricted_distinct_database_roles(case):
    for dsn in (case.dsn, case.event_dsn):
        with psycopg.connect(dsn) as conn:
            flags = conn.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls '
                'FROM pg_roles WHERE rolname=current_user').fetchone()
            assert flags == (False,) * 6
        for statement in ('CREATE TABLE public.paper_forbidden (x INT)',
                          'CREATE TEMP TABLE paper_forbidden (x INT)',
                          'ALTER TABLE paper_engine_orders ADD COLUMN forbidden INT',
                          'DROP TABLE paper_engine_commands',
                          'TRUNCATE paper_engine_events',
                          'DELETE FROM paper_engine_orders',
                          'SELECT * FROM paper_authority_sentinel',
                          'CREATE ROLE paper_engine_forbidden'):
            with psycopg.connect(dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    for statement in ('UPDATE paper_engine_orders SET filled=1',
                      "UPDATE paper_engine_orders SET lifecycle='FILLED'",
                      "INSERT INTO paper_engine_events (environment) VALUES ('PAPER')",
                      "INSERT INTO paper_engine_orders (filled) VALUES (1)"):
        with psycopg.connect(case.dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)
    with psycopg.connect(case.event_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("INSERT INTO paper_engine_commands (environment) VALUES ('PAPER')")


def test_engine_rejects_admin_and_swapped_database_credentials(case):
    for dsn in (os.environ['PAPER_ENGINE_ADMIN_DATABASE_DSN'], case.event_dsn):
        engine = PaperEngine(case.scope, dsn=dsn, authority_guard=case.guard,
            owner_strategy_id='synthetic', quantity_unit='BTC', price_currency='USDT')
        with pytest.raises(BridgeDenied, match='RESTRICTED_SEPARATE_PAPER_DATABASE_ROLES_REQUIRED'):
            engine._connect()
    engine = case.restart()
    engine._event_dsn = case.dsn
    with pytest.raises(BridgeDenied, match='RESTRICTED_SEPARATE_PAPER_DATABASE_ROLES_REQUIRED'):
        engine._event_connect()


def test_provisioning_guard_refuses_existing_application_schema_before_migration(paper_dsn):
    with psycopg.connect(os.environ['PAPER_ENGINE_ADMIN_DATABASE_DSN']) as conn:
        assert conn.execute('SELECT current_database()').fetchone()[0].startswith('paper_engine_')
        conn.execute('CREATE SCHEMA paper_sim')
        with pytest.raises(ValueError, match='existing paper_sim schema refused'):
            require_isolated_paper_database(conn)
        conn.rollback()  # The negative test creates no persistent application schema.


@pytest.fixture
def empty_paper_database(paper_dsn):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    admin = conninfo_to_dict(os.environ['PAPER_ENGINE_ADMIN_DATABASE_DSN'])
    database = 'paper_engine_guard_' + uuid4().hex[:12]
    with psycopg.connect(make_conninfo(**dict(admin, dbname='postgres')), autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    return ProvisioningDatabase(make_conninfo(**dict(admin, dbname=database)))


@dataclass(frozen=True)
class ProvisioningDatabase:
    dsn: str = field(repr=False)


def _public_schema_snapshot(conn):
    return (
        conn.execute("SELECT c.oid,c.relname,c.relkind,c.relowner,c.relacl FROM pg_class c JOIN pg_namespace n "
            "ON c.relnamespace=n.oid WHERE n.nspname='public' ORDER BY c.oid").fetchall(),
        conn.execute("SELECT a.attrelid,a.attnum,a.attname,a.atttypid,a.attnotnull FROM pg_attribute a "
            "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND a.attnum>0 ORDER BY a.attrelid,a.attnum").fetchall(),
        conn.execute("SELECT nspowner,nspacl FROM pg_namespace WHERE nspname='public'").fetchall(),
        conn.execute('SELECT datacl FROM pg_database WHERE datname=current_database()').fetchall())


@pytest.mark.parametrize('relation', ['paper_engine_orders', 'paper_engine_commands', 'paper_engine_events'])
@pytest.mark.parametrize('kind', ['TABLE', 'VIEW', 'SEQUENCE'])
def test_provisioning_refuses_any_existing_engine_relation_without_changes(empty_paper_database, relation, kind):
    from psycopg import sql
    admin_dsn = empty_paper_database.dsn
    marker = 'preexisting-' + uuid4().hex
    target = sql.Identifier('public', relation)
    with psycopg.connect(admin_dsn) as conn:
        if kind == 'TABLE':
            conn.execute(sql.SQL('CREATE TABLE {} (marker TEXT NOT NULL)').format(target))
            conn.execute(sql.SQL('INSERT INTO {} VALUES (%s)').format(target), (marker,))
        elif kind == 'VIEW':
            conn.execute(sql.SQL('CREATE VIEW {} AS SELECT {}::text AS marker').format(target, sql.Literal(marker)))
        else:
            conn.execute(sql.SQL('CREATE SEQUENCE {} START 73').format(target))
        before = _public_schema_snapshot(conn)
        data_before = conn.execute(sql.SQL('SELECT * FROM {}').format(target)).fetchall()
    suffix = uuid4().hex[:12]
    proposed_roles = ['paper_engine_runtime_' + suffix, 'paper_engine_events_' + suffix]
    with pytest.raises(ValueError, match='existing public PAPER engine relation refused before migration'):
        provision_paper_engine_roles(admin_dsn, isolated_paper=True,
            runtime_role=proposed_roles[0], event_role=proposed_roles[1],
            runtime_password=secrets.token_urlsafe(32), event_password=secrets.token_urlsafe(32))
    with psycopg.connect(admin_dsn) as conn:
        assert _public_schema_snapshot(conn) == before
        assert conn.execute(sql.SQL('SELECT * FROM {}').format(target)).fetchall() == data_before
        assert conn.execute('SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)', (proposed_roles,)).fetchone()[0] == 0


def test_runtime_schema_and_privilege_lookups_ignore_untrusted_search_path(case):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    shadow = 'paper_untrusted_' + uuid4().hex[:12]
    with psycopg.connect(case.dsn) as conn:
        runtime_role = conn.execute('SELECT current_user').fetchone()[0]
    with psycopg.connect(os.environ['PAPER_ENGINE_ADMIN_DATABASE_DSN']) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(shadow)))
        conn.execute(sql.SQL('CREATE TABLE {} (marker TEXT)').format(sql.Identifier(shadow, 'paper_engine_commands')))
        conn.execute(sql.SQL('INSERT INTO {} VALUES (%s)').format(sql.Identifier(shadow, 'paper_engine_commands')),
            ('untrusted-original-marker',))
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(
            sql.Identifier(shadow), sql.Identifier(runtime_role)))
        conn.execute(sql.SQL('GRANT SELECT,INSERT ON {} TO {}').format(
            sql.Identifier(shadow, 'paper_engine_commands'), sql.Identifier(runtime_role)))
    case.engine._dsn = make_conninfo(**dict(conninfo_to_dict(case.dsn),
        options='-c search_path=' + shadow + ',public,pg_catalog'))
    with case.engine._connect() as conn:
        assert conn.execute('SHOW search_path').fetchone()['search_path'] == 'pg_catalog'
    cmd, permit = case.command()
    case.engine.submit(cmd, permit)
    assert case.query(cmd).orders[0].order.identity.venue_client_order_id == cmd.venue_client_order_id
    with psycopg.connect(os.environ['PAPER_ENGINE_ADMIN_DATABASE_DSN']) as conn:
        assert conn.execute(sql.SQL('SELECT * FROM {}').format(
            sql.Identifier(shadow, 'paper_engine_commands'))).fetchall() == [('untrusted-original-marker',)]


def test_provisioning_migration_ignores_untrusted_admin_search_path(empty_paper_database):
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    admin_dsn = empty_paper_database.dsn
    with psycopg.connect(admin_dsn) as conn:
        conn.execute('CREATE SCHEMA untrusted_admin_path')
    suffix = uuid4().hex[:12]
    roles = provision_paper_engine_roles(make_conninfo(**dict(conninfo_to_dict(admin_dsn),
        options='-c search_path=untrusted_admin_path,public,pg_catalog')), isolated_paper=True,
        runtime_role='paper_engine_runtime_' + suffix, event_role='paper_engine_events_' + suffix,
        runtime_password=secrets.token_urlsafe(32), event_password=secrets.token_urlsafe(32))
    with psycopg.connect(admin_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='untrusted_admin_path'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=ANY(%s) AND c.relkind='r'",
            (['paper_engine_orders', 'paper_engine_commands', 'paper_engine_events'],)).fetchone()[0] == 3
    engine = PaperEngine(t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='synthetic-isolation',
        product=t.Product.SPOT, instrument_uid='synthetic-BTCUSDT', venue_leg='BOTH'),
        dsn=roles.runtime_dsn, authority_guard=lambda _: None,
        owner_strategy_id='synthetic', quantity_unit='BTC', price_currency='USDT')
    with engine._connect() as conn:
        assert conn.execute('SELECT count(*) FROM public.paper_engine_orders').fetchone()['count'] == 0
