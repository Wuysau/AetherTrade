"""Synthetic offline authority readers driving real owner/journal/bridge/gate.

In-memory claims and attestations are test inputs, not OS, database, venue or
native qualification. File fsync and SQLite ID registration use temporary files.
"""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from threading import Lock

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.wire import ContractCodec
from trading_system.engine.nautilus.bridge import ClientBinding, NautilusEngine, OfflineBridge
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.recovery.gate import REQUIREMENTS, RecoveryEvidence, RecoveryGate
from trading_system.runtime.backup import BackupState
from trading_system.runtime.emergency_journal import GENESIS
from trading_system.runtime.execution_owner import (
    EMERGENCY_CHECKS, EmergencyFacts, ExecutionOwner, JournalMergeEvidence, OwnerDenied, PriorityInbox,
)
from trading_system.runtime.supervisor import FenceObservation, Supervisor


ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-10-23T00:00:00Z'
EXPIRY = '2026-10-23T00:01:00Z'


class ProbeClaim:
    """No claim of cross-process exclusion; optional Linux probes use flock."""
    def __init__(self):
        self.lock = Lock()

    @contextmanager
    def __call__(self, shard):
        if not self.lock.acquire(blocking=False):
            raise OwnerDenied('PROBE_ALREADY_CLAIMED')
        try:
            yield
        finally:
            self.lock.release()


class RuntimeCase:
    def __init__(self, directory: Path):
        self.directory = directory
        self.scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='offline-ip17',
            product=t.Product.SPOT, instrument_uid='offline:BTCUSDT', venue_leg='BOTH')
        self.shard = t.ExecutionShard(environment=self.scope.environment, venue_account_id=self.scope.venue_account_id,
            product=self.scope.product, node_id='offline-old-host', execution_epoch=7)
        self.lease = t.OwnerLease(shard=self.shard, instance_id='offline-old-instance',
                                  expires_at=EXPIRY, deadline_monotonic_ns=60_000_000_000)
        self.now = t.ClockReading(utc_now=NOW, monotonic_ns=100, clock_health='HEALTHY')
        self.db = False
        self.facts = {}
        self.merge = None
        self.checks = {}
        self.locks = ()
        self.claim = ProbeClaim()
        self.codec = VenueIdCodec(self.scope, database=directory / 'ids.sqlite')
        self.engine = NautilusEngine(t.CompatibilityMap(build_digest='a' * 64, entries=(),
            send_path_inventory=(), uncovered_paths=('ALL_NATIVE_PATHS',)),
            offline=OfflineBridge(scope=self.scope, codec=self.codec,
                bindings=(ClientBinding(scope=self.scope, client_id='offline-original-client', native_instrument_id='BTCUSDT.OFFLINE'),),
                authority_guard=lambda key: self.owner.bridge_authority(key), clock=lambda: self.now))
        refs = {}
        for kind, obligations in REQUIREMENTS.items():
            ref = self.ref(kind)
            refs[kind] = ref
            self.checks[kind] = RecoveryEvidence(ref=ref, scope=self.scope, kind=kind,
                verified=tuple(sorted(obligations)), valid_until=EXPIRY, economic_history_complete=True)
        self.checklist = t.RecoveryChecklist(scope=self.scope, **refs, unresolved_cases=())
        self.gate = RecoveryGate(read_evidence=lambda scope, kind, ref: self.checks.get(kind),
            read_locks=lambda scope: self.locks, clock=lambda: self.now.utc_now)
        self.inbox = PriorityInbox()
        self.handled = []
        self.owner = self.make_owner()

    def ref(self, name):
        return t.EvidenceRef(id='offline:' + name, hash=canonical_hash({'fixture': name}),
            environment=self.scope.environment, source='synthetic-IP17-NOT-platform', visible_at=NOW)

    def make_owner(self):
        return ExecutionOwner(self.lease, journal_path=self.directory / 'emergency.jsonl', engine=self.engine,
            claim=self.claim, read_emergency=lambda record: self.facts.get(record.command.command_id),
            database_available=lambda: self.db, recovery_gate=self.gate,
            read_checklist=lambda scope: self.checklist, read_merge=lambda scope: self.merge,
            clock=lambda: self.now, events=self.inbox, handle_event=lambda event, owner: self.handled.append(event))

    def record(self, suffix='one', *, operation='CLOSE', **payload_changes):
        body = json.loads((ROOT / 'tests/fixtures/contracts/examples/execution-command.paper.json').read_text('utf-8'))
        body.update(command_id='ip17-command-' + suffix, attempt_id='ip17-attempt-' + suffix,
            logical_client_order_id='ip17-logical-' + suffix,
            venue_client_order_id=self.codec.encode('ip17-logical-' + suffix, self.scope.product, 'ORDER'),
            venue_account_id=self.scope.venue_account_id, instrument_uid=self.scope.instrument_uid,
            operation=operation, execution_epoch=7, created_at=NOW, durable_record_ref='offline-original:' + suffix)
        body['canonical_payload'].update(side='SELL', quantity='0.001', price='50000')
        body['canonical_payload'].update(payload_changes)
        if operation in {'CANCEL', 'AMEND'}:
            client = self.codec.encode('owned-target', self.scope.product, 'ORDER')
            self.codec.bind_venue_order(client, 'owned-venue-order')
            body['target_order_id'] = 'owned-venue-order'
        body['payload_hash'] = canonical_hash(body['canonical_payload'])
        command = ContractCodec().decode('execution-command', body)
        context = t.EmergencyContext(context=t.ControlContext(identity_id='offline-owner',
            environment=self.scope.environment, deployment_id='offline-deployment', authorization_id='offline-pregrant',
            policy_revision=1, ownership_epoch=1, scopes=(self.scope,), expected_sequence=1, idempotency_key=suffix),
            reducible_quantity=t.Quantity(value=Decimal('0.001'), unit='BTC'), quantity_evidence=self.ref('quantity'),
            allowed_order_types=('LIMIT',), maximum_slippage_bps=Decimal('10'), expires_at=EXPIRY)
        record = t.EmergencyRecord(record_id='ip17-record-' + suffix, command=command, context=context,
                                   prior_record_hash=self.owner.journal.head)
        self.grant(record)
        return record

    def grant(self, record, **changes):
        facts = EmergencyFacts(scope=self.scope, lease=self.lease, request_hash=canonical_hash(domain_payload(record)),
            context=record.context, closing_side='SELL', checks=EMERGENCY_CHECKS,
            expires_at=EXPIRY, deadline_monotonic_ns=30_000_000_000)
        self.facts[record.command.command_id] = replace(facts, **changes)

    def merge_journal(self):
        entries = self.owner.journal.snapshot()
        self.merge = JournalMergeEvidence(scope=self.scope, execution_epoch=self.shard.execution_epoch,
            journal_head=entries[-1].record_hash if entries else GENESIS,
            command_ids=tuple(entry.command.command_id for entry in entries), evidence=self.ref('merge'), platform_reconciled=True)

    def takeover_case(self, *, isolated=False, expired=True):
        old = replace(self.lease, expires_at='2026-10-22T00:00:00Z', deadline_monotonic_ns=1) if expired else self.lease
        target = replace(self.shard, node_id='offline-new-host', execution_epoch=8)
        proof = t.ExternalFenceProof(shard=self.shard, old_instance_id=old.instance_id,
                                    method='NETWORK_BLOCKED', evidence=self.ref('fence'))
        observation = FenceObservation(proof=proof, old_lease=old, target=target,
            external_write_isolated=isolated, expires_at=EXPIRY, deadline_monotonic_ns=30_000_000_000)
        supervisor = Supervisor(old_lease=old, scope=self.scope, read_fence=lambda p, s: observation,
            read_recovery=lambda shard: self.gate.evaluate(self.checklist), clock=lambda: self.now)
        return supervisor, target, proof

    def backup_state(self):
        position = t.ManagedPosition(position_id='offline-position', scope=self.scope,
            actual_quantity=t.Quantity(value=Decimal('0.001'), unit='BTC'), owner_strategy_id='offline-strategy',
            owner_book_id='offline-book', ownership_epoch=1,
            cost_evidence=t.Coverage(scope=self.scope, query_succeeded=False, pagination_complete=False,
                                     supported=False, missing=('ECONOMIC_HISTORY',)),
            realized_pnl=None, unrealized_valuation=None, active_exit_plan_id=None, risk_locks=())
        authorization = t.Authorization(authorization_id='offline-pregrant', owner_strategy_id='offline-strategy',
            environment=self.scope.environment, deployment_id='offline-deployment', scopes=(self.scope,),
            maximum_capital=t.Money(amount=Decimal('10'), currency='USDT'), policy_revision=1,
            protection_options=('NATIVE_REQUIRED',), model_cost_budget=t.Money(amount=Decimal('0'), currency='USD'),
            valid_from=NOW, expires_at=EXPIRY, revoked_at=None)
        entries = self.owner.journal.snapshot()
        return BackupState(shard=self.shard, lease=self.lease, created_at=NOW,
            authorizations=(authorization,), risk_locks=self.locks, commands=tuple(e.command for e in entries),
            positions=(position,), reservations=(), protection_plans=(), unresolved_cases=(),
            journal_head=self.owner.journal.head, journal_records=tuple(e.record_json for e in entries),
            kernel_cache=self.ref('kernel-cache'), credential_recovery_instructions=self.ref('recovery-instructions-only'),
            research_snapshot_index=self.ref('research-index'), paused_task_ids=('old-research-task',))

    def close(self):
        self.codec.close()


@pytest.fixture
def runtime_case(tmp_path):
    case = RuntimeCase(tmp_path)
    try:
        yield case
    finally:
        case.close()
