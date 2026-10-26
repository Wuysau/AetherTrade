"""Synthetic owner/command inputs driving real IP-11 code, no qualification.

The guard is an in-memory fixture, NOT evidence of durable commits, risk approval,
external fencing, native execution, or platform behavior. No database is seeded.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
from pathlib import Path
from threading import RLock

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec
from trading_system.engine.nautilus.bridge import (
    BridgeAuthority, BridgeDenied, ClientBinding, NautilusEngine, OfflineBridge,
)
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.engine.nautilus.native_events import DecodedNativeEvent


ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-10-23T00:00:00Z'
EXPIRY = '2026-10-23T00:01:00Z'


def compatibility():
    # Empty mapping intentionally cannot enable native functionality.
    return t.CompatibilityMap(build_digest='a' * 64, entries=(), send_path_inventory=(),
                              uncovered_paths=('ALL_NATIVE_PATHS',))


@dataclass(frozen=True)
class SubmissionResult:
    rejected: bool
    wire_requests: tuple  # Offline observations only; never network requests.


class BridgeCase:
    def __init__(self, directory, product=t.Product.SPOT):
        self.scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='paper-account-demo',
            product=product, instrument_uid='offline-' + str(product) + '-BTCUSDT', venue_leg='BOTH')
        self.codec = VenueIdCodec(self.scope, database=directory / (str(product) + '.sqlite'))
        self.shard = t.ExecutionShard(environment=self.scope.environment,
            venue_account_id=self.scope.venue_account_id, product=product, node_id='offline-node',
            execution_epoch=7)
        self.lease = t.OwnerLease(shard=self.shard, instance_id='offline-owner',
                                  expires_at=EXPIRY, deadline_monotonic_ns=10000)
        self.reading = t.ClockReading(utc_now=NOW, monotonic_ns=100, clock_health='HEALTHY')
        self.lock = RLock()
        self.authorities = {}
        self.revoked = False
        self.guard_active = False
        self.clock_calls = 0
        self.on_clock = None
        self.binding = ClientBinding(scope=self.scope, client_id='offline-client-' + str(product),
                                     native_instrument_id='BTCUSDT.' + str(product))
        self.configuration = OfflineBridge(scope=self.scope, codec=self.codec,
            bindings=(self.binding,), authority_guard=self.guard, clock=self.clock)
        self.engine = NautilusEngine(compatibility(), offline=self.configuration)
        self.command, self.permit = self.make_command()

    def make_command(self, operation='SUBMIT', *, suffix='', role='ORDER', parent=None):
        command = ContractCodec().decode('execution-command', json.loads((ROOT /
            'tests/fixtures/contracts/examples/execution-command.paper.json').read_text('utf-8')))
        logical = 'offline-' + operation + suffix
        client = self.codec.encode(logical, self.scope.product, role)
        target = None
        if operation in ('CANCEL', 'AMEND'):
            target_client = self.codec.encode('original-owned-order', self.scope.product, 'ORDER')
            self.codec.bind_venue_order(target_client, 'original-venue-order')
            target = 'original-venue-order'
        command = replace(command, command_id='command-' + operation + suffix,
            attempt_id='attempt-' + operation + suffix, environment=str(self.scope.environment),
            venue_account_id=self.scope.venue_account_id, product=str(self.scope.product),
            instrument_uid=self.scope.instrument_uid,
            logical_client_order_id=logical, venue_client_order_id=client, operation=operation,
            target_order_id=target, parent_command_id=parent, execution_epoch=7,
            state='MAY_HAVE_SENT', durable_record_ref='synthetic-committed-record-' + operation + suffix)
        permit = t.DurablePermit(durable_record_ref=command.durable_record_ref,
            command_id=command.command_id, payload_hash=command.payload_hash, scope=self.scope,
            ownership_epoch=command.ownership_epoch, execution_epoch=command.execution_epoch,
            policy_revision=command.policy_revision, expires_at=EXPIRY, deadline_monotonic_ns=1000)
        self.trust(command, permit)
        return command, permit

    def trust(self, command, permit):
        self.authorities[command.command_id] = BridgeAuthority(command=command, permit=permit,
            lease=self.lease, ownership_epoch=1, policy_revision=1)

    @contextmanager
    def guard(self, command_id):
        with self.lock:
            if self.revoked or command_id not in self.authorities:
                raise BridgeDenied('FIXTURE_AUTHORITY_UNAVAILABLE')
            self.guard_active = True
            try:
                yield self.authorities[command_id]
            finally:
                self.guard_active = False

    def clock(self):
        assert self.guard_active
        self.clock_calls += 1
        if self.on_clock:
            self.on_clock(self.clock_calls)
        return self.reading

    def try_submit(self, permit):
        try:
            self.engine.submit(self.command, permit)
            rejected = False
        except BridgeDenied:
            rejected = True
        return SubmissionResult(rejected, self.engine.offline_writes)


@pytest.fixture
def bridge_case(tmp_path):
    case = BridgeCase(tmp_path)
    yield case
    case.codec.close()


@pytest.fixture
def build():
    return t.QualifiedBuild(candidate=t.CandidateSpec(candidate_id='synthetic-offline-build',
        review_ref='1' * 40, deployment_ref='2' * 40, source_digest='3' * 64,
        build_environment=t.BuildEnvironment(os='linux', arch='x86_64', python_abi='cp312',
            dependency_digest='4' * 64, image_digest='5' * 64)), artifact_digest='6' * 64,
        probe_results=(), qualification_ref=t.EvidenceRef(id='synthetic-NOT-qualification',
            hash='7' * 64, environment=t.Environment.PAPER, source='offline-fixture', visible_at=NOW))


def query(scope):
    return t.ReconciliationCase(case_id='offline-case', scope=scope, command_ids=('command-SUBMIT',),
        position_ids=(), missing=('VENUE_EVIDENCE',), query_budget=t.QueryBudget(maximum_requests=1,
            window_seconds=60, minimum_interval_ms=1000, retry_after=None),
        attempts=0, next_query_at=NOW, state='OPEN')


def decode_fixture(payload):
    """Fictional JSON format; not a Nautilus decoder."""
    return DecodedNativeEvent(**json.loads(payload))


def event_bytes(identity, **changes):
    fields = dict(source_event_id='offline-event', source_sequence='1', occurred_at=NOW,
        native_client_order_id=identity.native_client_order_id,
        venue_client_order_id=identity.venue_client_order_id, venue_order_id=identity.venue_order_id)
    fields.update(changes)
    return json.dumps(fields).encode('utf-8')
