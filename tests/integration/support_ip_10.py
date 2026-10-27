"""Opt-in PostgreSQL probes plus IP-00 RecordingTransport; no venue writes.

All facts/authorizations/owner guards below are synthetic fixtures. They prove
only application/SQL behavior if run, never native bridge/fence qualification.
Provision a fresh ip10_test_* database on isolated Linux externally. This module
does not install/start anything, discover credentials, or change ledger guards.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import os
import signal
import sys
from threading import RLock
from time import monotonic_ns
from uuid import uuid4

import pytest

from integration.support_ip_03 import LedgerScenario, ROOT
from support.recording_transport import RecordingTransport
from trading_system.accounts.authorization import AuthorizationBinding
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec, TradeIntent
from trading_system.execution.commands import CommandRepository, command_hash
from trading_system.execution.dispatcher import CommandDispatcher, DispatchFacts, REQUIRED_CHECKS
from trading_system.execution.outbox import OutboxConsumer
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork


@pytest.fixture(scope='module')
def ip10_database():
    if os.environ.get('IP10_ISOLATED_DATABASE') != 'YES' or not os.environ.get('IP10_DATABASE_DSN'):
        pytest.skip('NOT_RUN: explicitly isolated IP10 PostgreSQL not supplied')
    if sys.platform != 'linux':
        pytest.skip('NOT_RUN: database/process probes require isolated Linux')
    import psycopg
    dsn = os.environ['IP10_DATABASE_DSN']
    with psycopg.connect(dsn) as conn:
        if not conn.execute('SELECT current_database()').fetchone()[0].startswith('ip10_test_'):
            pytest.fail('fresh dedicated ip10_test_* database required')
        if conn.execute("SELECT to_regnamespace('ledger')").fetchone()[0] is not None:
            pytest.fail('refusing an existing ledger; provision a fresh database')
        conn.execute((ROOT / 'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'deploy/db/roles.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'migrations/0004_command_abort_guard.sql').read_text(encoding='utf-8'))
    return dsn


@dataclass(frozen=True)
class DeliveryResult:
    wire_submit_count: int  # fictional RecordingTransport.send count, no venue I/O
    command_state: str


class RecordingEngine:
    def __init__(self, case):
        self.case = case
        self.transport = RecordingTransport()
        self.response_lost = False
        self.status = t.CommandState.VENUE_CONFIRMED
        self.result_ref = 'fictional-venue-event'
        self.wrong_command = False
        self.observations = []
        self.methods = []
        self.after_send = None

    def _send(self, method, command, permit):
        # A fresh independent SQL connection must observe the committed marker
        # and identities BEFORE the first recorder side effect. No mocked SQL.
        with self.case.ledger.connect_admin() as conn:
            raw = conn.execute('SELECT payload FROM ledger.commands WHERE scope_id=%s AND record_id=%s '
                'ORDER BY sequence DESC LIMIT 1', (scope_id(self.case.scope), command.command_id)).fetchone()[0]
            marker = conn.execute('SELECT count(*) FROM ledger.transaction_observations WHERE transaction_id=%s',
                (permit.durable_record_ref.removeprefix('postgres:'),)).fetchone()[0]
            identities = conn.execute('SELECT command_id,venue_client_order_id FROM ledger.command_identities '
                'WHERE command_id=%s', (command.command_id,)).fetchone()
        assert raw['state'] == 'MAY_HAVE_SENT'
        assert marker == 1
        assert identities == (command.command_id, command.venue_client_order_id)
        assert command.durable_record_ref == permit.durable_record_ref
        self.observations.append((raw['state'], marker, identities))
        self.methods.append(method)
        self.transport.send(ContractCodec().encode(command))
        if self.after_send:
            self.after_send()
        if self.response_lost:
            raise TimeoutError('synthetic response loss after recorder side effect')
        return t.CommandReceipt(receipt_id='fictional-receipt:' + command.command_id,
            command_id='unrelated-command' if self.wrong_command else command.command_id,
            status=self.status, reason=None, existing_result_ref=self.result_ref)

    def submit(self, command, permit):
        return self._send('submit', command, permit)

    def cancel(self, command, permit):
        return self._send('cancel', command, permit)

    def amend(self, command, permit):
        return self._send('amend', command, permit)

    def query(self, query):
        raise AssertionError('query scheduling belongs to the reconciler, not repeated outbox delivery')


class CommitFaultConnection:
    """Real PostgreSQL connection, injected failure before the selected commit."""
    def __init__(self, connection, case):
        self.connection, self.case = connection, case

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        self.case.commits += 1
        if self.case.fail_commit == self.case.commits:
            raise OSError('injected commit failure')
        result = self.connection.commit()
        if self.case.fail_after_commit == self.case.commits:
            raise OSError('injected lost commit acknowledgement')
        return result


class DispatchCase:
    def __init__(self, dsn, *, suffix=None, seed=True, operation='SUBMIT'):
        self.ledger = LedgerScenario(dsn, suffix=suffix, seed=seed)
        self.scope = self.ledger.scope
        now = datetime.now(timezone.utc)
        self.now = now
        if seed:
            self.intent = replace(self.ledger.intent, valid_until=(now + timedelta(hours=1)).isoformat())
            self.command = replace(self.ledger.command, operation=operation,
                logical_client_order_id='logical-' + self.ledger.suffix,
                target_order_id='owned-original-order' if operation in ('CANCEL', 'AMEND') else None,
                created_at=(now - timedelta(seconds=1)).isoformat())
            with self.ledger.uow.transaction(self.scope) as tx:
                # The dispatcher must inspect current position locks even when
                # account cause locks are empty. Establish the fixture's fresh
                # synthetic empty-position book rather than infer it from no row.
                tx.projections.save_book(t.BookSnapshot(scope=self.scope, positions=(), active_orders=(),
                    reservations=(), balances=(t.Money(amount=self.ledger.reservation.amount, currency='USDT'),),
                    sequence=1, basis_fingerprint=self.intent.basis_fingerprint,
                    coverage=t.Coverage(scope=self.scope, query_succeeded=True,
                        pagination_complete=True, supported=True, missing=()),
                    evidence=(t.EvidenceRef(id='synthetic-dispatch-book', hash='b' * 64,
                        environment=self.scope.environment, source='synthetic-IP10-fixture',
                        visible_at=(now - timedelta(seconds=1)).isoformat()),)), 0)
                tx.intents.append(self.intent)
                tx.reservations.append(replace(self.ledger.reservation,
                    command_refs=(self.command.command_id,)), 0)
                CommandRepository(tx).append(self.command)
        else:
            with self.ledger.uow.transaction(self.scope) as tx:
                self.command = tx.commands.get(self.ledger.command.command_id)
                self.intent = tx._latest('intents', self.command.intent_id, TradeIntent)
        self.lease = t.OwnerLease(shard=t.ExecutionShard(environment=self.scope.environment,
            venue_account_id=self.scope.venue_account_id, product=self.scope.product,
            node_id='fictional-node', execution_epoch=self.command.execution_epoch),
            instance_id='fictional-owner', expires_at=(now + timedelta(minutes=10)).isoformat(),
            deadline_monotonic_ns=monotonic_ns() + 600_000_000_000)
        self.binding = AuthorizationBinding(authorization=t.Authorization(
            authorization_id=self.intent.authorization_id, owner_strategy_id=self.intent.owner_strategy_id,
            environment=self.scope.environment, deployment_id='fictional-deployment', scopes=(self.scope,),
            maximum_capital=t.Money(amount=self.ledger.reservation.amount, currency='USDT'),
            policy_revision=self.command.policy_revision, protection_options=('NATIVE_REQUIRED',),
            model_cost_budget=t.Money(amount=self.ledger.reservation.amount, currency='USDT'),
            valid_from=(now - timedelta(minutes=1)).isoformat(), expires_at=(now + timedelta(minutes=10)).isoformat(),
            revoked_at=None), identity_id='fictional-user', strategy_revision=self.intent.strategy_revision,
            ownership_epoch=self.command.ownership_epoch, queue_environment=self.scope.environment,
            capabilities=('fixture-only',), evidence=(t.EvidenceRef(id='synthetic-grant', hash='a' * 64,
                environment=self.scope.environment, source='synthetic-IP10-fixture',
                visible_at=(now - timedelta(seconds=1)).isoformat()),))
        self.lock = RLock()
        self.facts_calls = 0
        self.on_facts = None
        self.facts_transform = lambda facts: facts
        self.commits = 0
        self.fail_commit = None
        self.fail_after_commit = None
        self.storage_down = False
        self.engine = RecordingEngine(self)
        self.uow = UnitOfWork(self.connect)
        self.dispatcher = self.new_dispatcher()
        self.outbox = OutboxConsumer(self.dispatcher, 'ip10-consumer')

    def connect(self):
        if self.storage_down:
            raise OSError('injected storage unavailable')
        return CommitFaultConnection(self.ledger.connect_owner(), self)

    @contextmanager
    def guard(self, lease):
        # Process-local fixture mutex only; NOT an IP-17 external fencing proof.
        with self.lock:
            yield

    def facts(self, tx, command, intent):
        self.facts_calls += 1
        if self.on_facts:
            self.on_facts(self.facts_calls)
        return self.facts_transform(DispatchFacts(binding=self.binding, lease=self.lease,
            request_hash=command_hash(command), valid_until=self.lease.expires_at,
            deadline_monotonic_ns=self.lease.deadline_monotonic_ns,
            checks=REQUIRED_CHECKS | {'OWNED_TARGET'}))

    def new_dispatcher(self):
        return CommandDispatcher(self.uow, self.scope, self.engine,
            load_facts=self.facts, owner_guard=self.guard)

    def dispatch(self):
        return self.dispatcher.dispatch(self.command.command_id, self.lease)

    def get(self):
        with self.ledger.uow.transaction(self.scope) as tx:
            return CommandRepository(tx).get(self.command.command_id)

    def message_id(self):
        with self.ledger.connect_admin() as conn:
            return conn.execute("SELECT message_id FROM ledger.outbox WHERE scope_id=%s AND kind='COMMAND_READY' "
                'AND aggregate_id=%s', (scope_id(self.scope), self.command.command_id)).fetchone()[0]

    def deliver_same_command_twice(self, response_lost=True):
        self.engine.response_lost = response_lost
        message_id = self.message_id()
        self.outbox.consume(message_id, self.lease)
        # New consumer object represents restart; durable state supplies the gate.
        OutboxConsumer(self.new_dispatcher(), 'restarted-consumer').consume(message_id, self.lease)
        return DeliveryResult(len(self.engine.transport.writes), self.get().state)

    def variant(self, **changes):
        command = replace(self.command, **changes)
        return replace(command, payload_hash=canonical_hash(ContractCodec().encode(command)['canonical_payload']))

    def append(self, command):
        with self.ledger.uow.transaction(self.scope) as tx:
            CommandRepository(tx).append(command)

    def make_another_command(self):
        """A distinct semantic action, used to check unresolved group blocking."""
        suffix = uuid4().hex
        command = self.variant(command_id='command-' + suffix, attempt_id='attempt-' + suffix,
            logical_client_order_id='logical-' + suffix, venue_client_order_id='client-' + suffix)
        self.append(command)
        return command

    def recovery_records(self):
        with self.ledger.uow.transaction(self.scope) as tx:
            locks = tx.risk_locks.active(self.scope)
            case = tx._latest('reconciliation_cases', 'ip10:reconcile:' + self.command.command_id,
                             t.ReconciliationCase)
            reservations = tx.reservations.for_intent(self.intent.intent_id)
        return locks, case, reservations


@pytest.fixture
def dispatch_case(ip10_database):
    return DispatchCase(ip10_database)


def crash_before_wire_worker(dsn, suffix, pipe):
    """Only invoked by an opted-in isolated Linux test, never on import."""
    if sys.platform != 'linux' or os.environ.get('IP10_ISOLATED_DATABASE') != 'YES':
        raise RuntimeError('isolated Linux required')
    case = DispatchCase(dsn, suffix=suffix, seed=False)

    def kill_after_commit(call):
        if call == 2:
            assert not case.engine.transport.writes
            pipe.send('MAY_HAVE_SENT_COMMITTED_BEFORE_WIRE')
            os.kill(os.getpid(), signal.SIGKILL)

    case.on_facts = kill_after_commit
    case.dispatch()
    raise AssertionError('expected SIGKILL before any recorder call')
