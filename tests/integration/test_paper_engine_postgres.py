"""Mandatory real-PostgreSQL synthetic venue tests; never exchange qualification."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec
from trading_system.engine.nautilus.bridge import BridgeAuthority, BridgeDenied
from trading_system.engine.paper import PaperEngine, SyntheticOrderEvent, paper_client_order_id
from trading_system.runtime.execution_owner import ExecutionOwner, OwnerDenied

ROOT = Path(__file__).resolve().parents[2]
NOW, EXPIRY = '2026-10-27T00:00:00Z', '2026-10-27T00:01:00Z'


@pytest.fixture(scope='module')
def paper_dsn():
    dsn = os.environ.get('PAPER_ENGINE_DATABASE_DSN')
    if not dsn or os.environ.get('PAPER_ENGINE_ISOLATED_DATABASE') != 'YES':
        pytest.fail('NOT_RUN: explicit isolated PAPER PostgreSQL is required')
    with psycopg.connect(dsn) as conn:
        conn.execute((ROOT / 'migrations/0003_paper_engine.sql').read_text())
    return dsn


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
        self.engine = self.restart()

    @contextmanager
    def guard(self, command_id):
        if command_id not in self.authorities:
            raise BridgeDenied('AUTHORITY_UNAVAILABLE')
        yield self.authorities[command_id]

    def restart(self, **kwargs):
        return PaperEngine(self.scope, dsn=self.dsn, authority_guard=self.guard,
            owner_strategy_id='synthetic-strategy', quantity_unit='BTC', price_currency='USDT',
            event_guard=self.event_guard, clock=lambda: self.now, **kwargs)

    @contextmanager
    def event_guard(self, event):
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
