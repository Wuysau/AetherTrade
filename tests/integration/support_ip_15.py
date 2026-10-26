"""Synthetic application fixtures, never native or trading qualification.

The memory journal/dispatcher model failure injection only. The separately
opted-in PostgreSQL probes use the actual repository and IP-10 dispatcher.
No fixture creates a venue client or discovers credentials.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.wire import ContractCodec
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.execution.commands import command_hash
from trading_system.protection.exit import ExitFacts, ExitService, FlatProof
from trading_system.protection.manager import (
    CommandCompiler, EntryRisk, ProtectionInputs, ProtectionManager,
    ProtectionRecord, ProtectionRepository,
)
from trading_system.protection.native import (
    NativeCapability, NativeOrderProof, NativeProof, NativeVerifier, closing_side, digest,
)
from trading_system.storage.models import SequenceConflict, decode, encode


ROOT = Path(__file__).resolve().parents[2]
STAMP = '2026-10-23T00:00:00Z'


def quantity(value):
    return t.Quantity(value=Decimal(value), unit='BTC')


def price(value):
    return t.Price(value=Decimal(value), currency='USDT')


class MemoryJournal:
    """Fault-injectable typed journal, explicitly not PostgreSQL durability."""
    def __init__(self):
        self.records = {}
        self.command_records = {}
        self.history = []
        self.fail_load = False
        self.fail_before_save = False
        self.fail_after_save = False

    def load(self, scope, position_id):
        if self.fail_load:
            raise OSError('synthetic read failure')
        return self.records.get((scope, position_id))

    def load_plan(self, scope, plan_id):
        if self.fail_load:
            raise OSError('synthetic read failure')
        matches = [r for (s, _), r in self.records.items()
                   if s == scope and r.plan.plan_id == plan_id]
        if len(matches) > 1:
            raise ValueError('ambiguous synthetic plan')
        return matches[0] if matches else None

    def commands(self, scope, ids):
        return tuple(self.command_records[(scope, key)] for key in ids)

    def save(self, record, previous, commands=()):
        if self.fail_before_save:
            raise OSError('synthetic commit failure')
        key = (record.position.scope, record.position.position_id)
        if self.records.get(key) != previous or record.revision != (previous.revision + 1 if previous else 1):
            raise SequenceConflict('synthetic compare-and-swap conflict')
        for command in commands:
            old = self.command_records.get((record.position.scope, command.command_id))
            if old and command_hash(old) != command_hash(command):
                raise SequenceConflict('synthetic command identity conflict')
        # Exercise the real nested-domain serialization; no fixed PASS values.
        restored = decode(ProtectionRecord, encode(record))
        self.records[key] = restored
        for command in commands:
            self.command_records[(record.position.scope, command.command_id)] = command
        self.history.append(restored)
        if self.fail_after_save:
            raise OSError('synthetic lost commit acknowledgement')


class SimulatedProcessLoss(BaseException):
    """Local interruption probe only; this is NOT SIGKILL evidence for T37."""


class RecordingDispatcher:
    def __init__(self, repository, scope):
        self.repository, self.scope = repository, scope
        self.calls = []
        self.status = t.CommandState.VENUE_CONFIRMED
        self.error = None
        self.crash = False

    def dispatch(self, command_id, lease):
        command = self.repository.command_records[(self.scope, command_id)]
        assert any(command_id in r.command_ids and r.locks and r.case.state == 'OPEN'
                   for r in self.repository.history)
        self.calls.append(command_id)
        if self.error:
            raise self.error
        self.repository.command_records[(self.scope, command_id)] = replace(
            command, state='MAY_HAVE_SENT' if self.crash else str(self.status))
        if self.crash:
            raise SimulatedProcessLoss('synthetic loss after lifecycle persisted')
        return t.CommandReceipt(receipt_id='synthetic:' + command_id, command_id=command_id,
            status=self.status, reason=None, existing_result_ref='synthetic-receipt-only')


class ProtectionCase:
    def __init__(self, *, product=t.Product.USD_M_PERP, leg='BOTH', amount='0.4',
                 scope=None, now=STAMP, template=None, repository=None, dispatcher=None,
                 lease=None, owner_strategy_id='synthetic-owner'):
        self.now = now
        self.scope = scope or t.ScopeKey(environment=t.Environment.PAPER,
            venue_account_id='synthetic-ip15', product=product,
            instrument_uid='synthetic-btc', venue_leg=leg)
        self.position = t.ManagedPosition(position_id='position-ip15', scope=self.scope,
            actual_quantity=quantity(amount), owner_strategy_id=owner_strategy_id,
            owner_book_id='synthetic-book', ownership_epoch=1,
            cost_evidence=self.coverage(), realized_pnl=None, unrealized_valuation=None,
            active_exit_plan_id='plan-ip15', risk_locks=())
        self.plan = t.ProtectionPlan(plan_id='plan-ip15', mode='NATIVE', scope=self.scope,
            coverage_quantity=quantity(amount), stop_price=price('90'), stop_trigger='LAST',
            native_order_ids=(), monitor_id=None, state=t.ProtectionState.PENDING,
            last_verified_at=None, coverage_gap_since=None, replacement_revision=0,
            on_gap_policy='REPAIR_THEN_AUTHORIZED_EXIT')
        self.capability = NativeCapability(scope=self.scope, status=t.CapabilityStatus.VERIFIED,
            minimum_quantity=quantity('0.01'), quantity_step=quantity('0.01'),
            order_types=('STOP_MARKET', 'LIMIT', 'MARKET'), triggers=('LAST',),
            atomic_amend=False, coexist=False, oco=False,
            valid_until=self.later(300), evidence=self.ref('capability'))
        if template is None:
            body = json.loads((ROOT / 'tests/fixtures/contracts/examples/'
                               'execution-command.paper.json').read_text(encoding='utf-8'))
            body.update(environment=str(self.scope.environment),
                venue_account_id=self.scope.venue_account_id, product=str(self.scope.product),
                instrument_uid=self.scope.instrument_uid, created_at=now)
            template = ContractCodec().decode('execution-command', body)
        self.template = template
        self.codec = VenueIdCodec(self.scope)
        self.compiler = CommandCompiler(template, self.codec, clock=lambda: self.now)
        self.repository = repository or MemoryJournal()
        self.dispatcher = dispatcher or RecordingDispatcher(self.repository, self.scope)
        self.lease = lease or t.OwnerLease(shard=t.ExecutionShard(environment=self.scope.environment,
            venue_account_id=self.scope.venue_account_id, product=self.scope.product,
            node_id='synthetic-node', execution_epoch=1), instance_id='synthetic-instance',
            expires_at=self.later(300), deadline_monotonic_ns=300_000_000_000)
        self.entries = ()
        self.take_profit = False
        self.native_proofs = {}
        self.flat_proofs = {}
        self.exit_facts = None
        self.fill = t.Fill(fill_id='fill-ip15', venue='SYNTHETIC', scope=self.scope,
            native_trade_id='trade-ip15', order=self.ref_order('entry').identity,
            quantity=quantity(amount), price=price('100'), fees=(), occurred_at=self.now,
            correction_of=None, evidence=self.ref('fill'))
        self.restart()

    def close(self):
        self.codec.close()

    def later(self, seconds):
        return (utc_timestamp(self.now) + timedelta(seconds=seconds)).isoformat()

    def ref(self, name='report'):
        return t.EvidenceRef(id='synthetic:' + name, hash=digest(self.scope),
            environment=self.scope.environment, source='synthetic-IP15-only', visible_at=self.now)

    def coverage(self):
        return t.Coverage(scope=self.scope, query_succeeded=True,
            pagination_complete=True, supported=True, missing=())

    def ref_order(self, key):
        return t.OwnedOrderRef(identity=t.OrderIdentity(scope=self.scope,
            logical_client_order_id=key, native_client_order_id='native:' + key,
            venue_client_order_id='client:' + key, venue_order_id='venue:' + key),
            owner_strategy_id=self.position.owner_strategy_id, ownership_epoch=self.position.ownership_epoch)

    def projection(self, ref, *, state=t.OrderLifecycle.ACCEPTED, leaves='0.4',
                   filled='0', certainty='CONFIRMED', pending='NONE'):
        if ref.identity.venue_order_id is None:
            ref = replace(ref, identity=replace(ref.identity,
                venue_order_id='venue:' + ref.identity.logical_client_order_id))
        return t.OrderProjection(order=ref, lifecycle_state=state,
            pending_operation=pending, command_certainty=certainty,
            outcome_provenance='VENUE_QUERY', filled_quantity=quantity(filled),
            leaves_quantity=quantity(leaves), lifecycle_revision=1, sequence=1)

    def inputs(self, fill, position):
        return ProtectionInputs(fill_hash=digest(fill), position=self.position,
            plan=self.plan, capability=self.capability, entries=self.entries,
            evidence=self.ref('ledger-projection'), take_profit_requested=self.take_profit)

    def restart(self):
        self.verifier = NativeVerifier(clock=lambda: self.now,
            read_proof=lambda plan, report: self.native_proofs.get((digest(plan), digest(report))))
        self.manager = ProtectionManager(self.repository, self.dispatcher, self.lease,
            read_inputs=self.inputs, compile_command=self.compiler, verifier=self.verifier,
            clock=lambda: self.now)
        self.exits = ExitService(self.manager, read_facts=lambda p, c: self.exit_facts,
            read_flat_proof=lambda record, report: self.flat_proofs.get(digest(report)))

    def record(self):
        return self.repository.load(self.scope, self.position.position_id)

    def protect(self):
        return self.manager.on_fill(self.fill, self.position)

    def partial_unknown(self):
        self.entries = (EntryRisk(order=self.projection(self.ref_order('entry'),
            state=t.OrderLifecycle.PARTIALLY_FILLED, leaves='0.6', filled='0.4',
            certainty='UNKNOWN', pending='CANCEL'), command_id='entry-cancel-original'),)

    def report(self, *, orders=(), positions=None, **changes):
        return replace(t.VenueReport(coverage=self.coverage(), orders=orders,
            positions=(self.position,) if positions is None else positions,
            balances=(t.Money(amount=self.position.actual_quantity.value, currency='BTC'),),
            fills=(), evidence=(self.ref(),), next_cursor=None, as_of=self.now), **changes)

    def attest_native(self, plan, report, **changes):
        orders = tuple(NativeOrderProof(order=o.order, side=closing_side(self.position),
            order_type='STOP_MARKET', stop_price=plan.stop_price, trigger=plan.stop_trigger,
            reduce_only=self.scope.product == t.Product.USD_M_PERP,
            full_position_exit=False, evidence=report.evidence[0])
            for o in report.orders
            if o.order.identity.logical_client_order_id in
            {r.identity.logical_client_order_id for r in plan.native_order_ids})
        proof = replace(NativeProof(plan_hash=digest(plan), report_hash=digest(report),
            position=self.position, positions_verified=True,
            balances_and_allocations_verified=True, orders=orders), **changes)
        self.native_proofs[(digest(plan), digest(report))] = proof
        return proof

    def native_report(self, plan=None, **order_changes):
        plan = plan or self.record().plan
        report = self.report(orders=(self.projection(plan.native_order_ids[0],
            leaves=str(plan.coverage_quantity.value), **order_changes),))
        self.attest_native(plan, report)
        return report

    def next_plan(self, **changes):
        current = self.record().plan
        return replace(current, native_order_ids=(), stop_price=price('91'),
            replacement_revision=current.replacement_revision + 1,
            state=t.ProtectionState.PENDING, last_verified_at=None, **changes)

    def prepare_exit(self, *, orders=(), **facts_changes):
        record = self.record()
        context = t.EmergencyContext(context=t.ControlContext(identity_id='synthetic-user',
            environment=self.scope.environment, deployment_id='synthetic-deployment',
            authorization_id='synthetic-authorization', policy_revision=1,
            ownership_epoch=self.position.ownership_epoch, scopes=(self.scope,),
            expected_sequence=record.revision if record else 0, idempotency_key='exit-ip15'),
            reducible_quantity=quantity(str(abs(self.position.actual_quantity.value))),
            quantity_evidence=self.ref(), allowed_order_types=('LIMIT',),
            maximum_slippage_bps=Decimal('100'), expires_at=self.later(60))
        self.exit_facts = replace(ExitFacts(position=self.position, context_hash=digest(context),
            plan=record.plan if record else self.plan, capability=self.capability,
            report=self.report(orders=orders), positions_verified=True, orders_verified=True,
            balances_and_allocations_verified=True, authorization_verified=True,
            owner_strategy_id=self.position.owner_strategy_id, market_open=True,
            order_type='LIMIT', limit_price=price('100'), reference_price=price('100')), **facts_changes)
        return context

    def attest_flat(self, report, **changes):
        record = self.record()
        proof = replace(FlatProof(position_id=self.position.position_id,
            owner_strategy_id=self.position.owner_strategy_id, owner_book_id=self.position.owner_book_id,
            ownership_epoch=self.position.ownership_epoch, report_hash=digest(report),
            exit_context_hash=record.exit_context_hash, positions_verified=True,
            orders_verified=True, balances_and_allocations_verified=True,
            commands_resolved=record.command_ids, economic_missing_recorded=True,
            economic_missing=('FEES_PENDING',)), **changes)
        self.flat_proofs[digest(report)] = proof
        return proof


@pytest.fixture
def protection_case():
    case = ProtectionCase()
    try:
        yield case
    finally:
        case.close()


@pytest.fixture(scope='module')
def ip15_database():
    if os.environ.get('IP15_ISOLATED_DATABASE') != 'YES' or not os.environ.get('IP15_DATABASE_DSN'):
        pytest.skip('NOT_RUN: explicitly isolated IP15 PostgreSQL not supplied')
    if sys.platform != 'linux':
        pytest.skip('NOT_RUN: PostgreSQL probes require isolated Linux')
    import psycopg
    dsn = os.environ['IP15_DATABASE_DSN']
    with psycopg.connect(dsn) as conn:
        if not conn.execute('SELECT current_database()').fetchone()[0].startswith('ip15_test_'):
            pytest.fail('fresh dedicated ip15_test_* database required')
        if conn.execute("SELECT to_regnamespace('ledger')").fetchone()[0] is not None:
            pytest.fail('refusing an existing ledger; provision a fresh database')
        conn.execute((ROOT / 'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'deploy/db/roles.sql').read_text(encoding='utf-8'))
    return dsn


@pytest.fixture
def database_protection_case(ip15_database):
    from integration.support_ip_10 import DispatchCase
    dispatch = DispatchCase(ip15_database)
    case = ProtectionCase(scope=dispatch.scope, now=dispatch.now.isoformat(),
        template=dispatch.command, repository=ProtectionRepository(dispatch.uow),
        dispatcher=dispatch.dispatcher, lease=dispatch.lease,
        owner_strategy_id=dispatch.intent.owner_strategy_id)
    try:
        yield case, dispatch
    finally:
        case.close()
