"""Synthetic offline readers, never venue, protection or ownership qualification.

Database probes opt in to a fresh isolated Linux PostgreSQL database; nothing
is installed or started and no home credential discovery is performed.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import os
import sys

import pytest

from integration.support_ip_03 import ROOT
from integration.support_ip_10 import DispatchCase
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.recovery.cases import unknown_case
from trading_system.recovery.gate import REQUIREMENTS, RecoveryEvidence, RecoveryGate
from trading_system.recovery.reconciler import (
    CommandBinding, CommandResolution, Reconciler, ReportAttestation,
)


STAMP = '2026-10-23T00:00:00Z'


def reading(seconds=0, *, wall_seconds=None, health='HEALTHY'):
    stamp = utc_timestamp(STAMP) + timedelta(seconds=seconds if wall_seconds is None else wall_seconds)
    return t.ClockReading(utc_now=stamp.isoformat(), monotonic_ns=int(seconds * 10**9), clock_health=health)


def budget(maximum=1, window=60, interval=1000, retry_after=None):
    return t.QueryBudget(maximum_requests=maximum, window_seconds=window,
                         minimum_interval_ms=interval, retry_after=retry_after)


class RecoveryCase:
    def __init__(self):
        self.scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='synthetic-ip13',
            product=t.Product.USD_M_PERP, instrument_uid='synthetic:BTCUSDT', venue_leg='BOTH')
        self.bindings = {}
        self.proofs = {}
        self.records = {}
        self.locks = ()
        self.reconciler = Reconciler(read_attestation=self.read_attestation,
            read_command=lambda scope, key: self.bindings.get((scope, key)), clock=lambda: STAMP)
        self.gate = RecoveryGate(read_evidence=lambda scope, kind, ref: self.records.get((scope, kind, ref)),
            read_locks=lambda scope: self.locks, clock=lambda: STAMP)
        refs = {}
        for kind, requirements in REQUIREMENTS.items():
            ref = self.ref(kind)
            refs[kind] = ref
            self.records[(self.scope, kind, ref)] = RecoveryEvidence(ref=ref, scope=self.scope,
                kind=kind, verified=tuple(sorted(requirements)), valid_until=reading(60).utc_now,
                economic_history_complete=True)
        self.checklist = t.RecoveryChecklist(scope=self.scope, **refs, unresolved_cases=())

    def ref(self, name='report'):
        return t.EvidenceRef(id='synthetic:' + name, hash=canonical_hash({'fixture': name}),
            environment=self.scope.environment, source='synthetic-IP13-only', visible_at=STAMP)

    def read_attestation(self, case, report):
        return self.proofs.get((case.case_id, canonical_hash(domain_payload(report))))

    def case(self, operation='SUBMIT', command_id=None, attempts=0):
        command_id = command_id or operation.lower()
        case = unknown_case(scope=self.scope, command_id=command_id, operation=operation,
                            now=reading(), budget=budget(), attempts=attempts)
        order = t.OrderIdentity(scope=self.scope, logical_client_order_id='original-logical',
            native_client_order_id='original-native', venue_client_order_id='original-client',
            venue_order_id='original-venue')
        self.bindings[(self.scope, command_id)] = CommandBinding(scope=self.scope,
            command_id=command_id, operation=operation, order=order,
            request_hash=canonical_hash({'command': command_id, 'operation': operation}))
        return case

    def positions_case(self):
        return t.ReconciliationCase(case_id='positions', scope=self.scope, command_ids=(),
            position_ids=('current-position',), missing=('POSITIONS',), query_budget=budget(),
            attempts=0, next_query_at=STAMP, state='OPEN')

    def report(self, **changes):
        result = t.VenueReport(coverage=t.Coverage(scope=self.scope, query_succeeded=True,
            pagination_complete=True, supported=True, missing=()), orders=(), positions=(),
            balances=(), fills=(), evidence=(self.ref(),), next_cursor=None, as_of=STAMP)
        return replace(result, **changes)

    def attest(self, case, report, *, commands=(), **changes):
        resolutions = tuple(CommandResolution(binding=self.bindings[(self.scope, key)],
            provenance='VENUE_QUERY', evidence=report.evidence) for key in commands)
        proof = ReportAttestation(case_id=case.case_id, scope=self.scope,
            case_hash=canonical_hash(domain_payload(case)),
            report_hash=canonical_hash(domain_payload(report)), commands=resolutions,
            satisfied=case.missing, current_positions_verified=True,
            current_orders_verified=True,
            balances_and_allocations_verified=True, economic_history_complete=True)
        proof = replace(proof, **changes)
        self.proofs[(case.case_id, canonical_hash(domain_payload(report)))] = proof
        return proof

    def position(self, quantity='1'):
        return t.ManagedPosition(position_id='current-position', scope=self.scope,
            actual_quantity=t.Quantity(value=Decimal(quantity), unit='BTC'),
            owner_strategy_id='synthetic-owner', owner_book_id='synthetic-book', ownership_epoch=1,
            cost_evidence=t.Coverage(scope=self.scope, query_succeeded=False,
                pagination_complete=False, supported=True, missing=('ECONOMIC_HISTORY',)),
            realized_pnl=None, unrealized_valuation=None, active_exit_plan_id=None, risk_locks=())

    def order(self, **changes):
        self.case()
        identity = self.bindings[(self.scope, 'submit')].order
        result = t.OrderProjection(order=t.OwnedOrderRef(identity=identity,
            owner_strategy_id='synthetic-owner', ownership_epoch=1),
            lifecycle_state=t.OrderLifecycle.ACCEPTED,
            pending_operation='NONE', command_certainty='CONFIRMED',
            outcome_provenance='VENUE_QUERY',
            filled_quantity=t.Quantity(value=Decimal('0'), unit='BTC'),
            leaves_quantity=t.Quantity(value=Decimal('1'), unit='BTC'),
            lifecycle_revision=1, sequence=1)
        return replace(result, **changes)

    def evaluate_empty_positions(self, coverage_complete):
        case = self.positions_case()
        report = self.report()
        report = replace(report, coverage=replace(report.coverage, pagination_complete=coverage_complete))
        self.attest(case, report)
        return self.reconciler.assess(case, report)

    def change_record(self, selector, **changes):
        ref = getattr(self.checklist, selector)
        key = (self.scope, selector, ref)
        self.records[key] = replace(self.records[key], **changes)

    def lock(self, cause='ORDER_OUTCOME_UNKNOWN', permission=t.RiskPermission.RECOVERY_REQUIRED):
        return t.RiskLock(lock_id='lock:' + cause, scope=self.scope, cause=cause,
            state='ACTIVE', permission=permission, required_evidence=('CAUSE_SPECIFIC_REVIEW',), raised_at=STAMP)


@pytest.fixture
def recovery_case():
    return RecoveryCase()


@pytest.fixture(scope='module')
def ip13_database():
    if os.environ.get('IP13_ISOLATED_DATABASE') != 'YES' or not os.environ.get('IP13_DATABASE_DSN'):
        pytest.skip('NOT_RUN: explicitly isolated IP13 PostgreSQL not supplied')
    if sys.platform != 'linux':
        pytest.skip('NOT_RUN: database probes require isolated Linux')
    import psycopg
    dsn = os.environ['IP13_DATABASE_DSN']
    with psycopg.connect(dsn) as conn:
        if not conn.execute('SELECT current_database()').fetchone()[0].startswith('ip13_test_'):
            pytest.fail('fresh dedicated ip13_test_* database required')
        if conn.execute("SELECT to_regnamespace('ledger')").fetchone()[0] is not None:
            pytest.fail('refusing existing ledger; provision a fresh database')
        conn.execute((ROOT / 'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'deploy/db/roles.sql').read_text(encoding='utf-8'))
    return dsn


@pytest.fixture
def durable_case(ip13_database):
    case = DispatchCase(ip13_database)
    case.engine.response_lost = True
    case.dispatch()
    return case


@pytest.fixture
def accepted_case(ip13_database):
    case = DispatchCase(ip13_database)
    assert case.dispatch().status == t.CommandState.VENUE_CONFIRMED
    return case
