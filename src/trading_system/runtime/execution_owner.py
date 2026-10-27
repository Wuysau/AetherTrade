"""Offline owner composition and fixed-Linux locking primitives.

No credentials, network client, native startup or LIVE path exists here. Trusted
readers must recheck authority under the same guard used by the dispatcher and
bridge. Injected probe claims are explicitly not OS isolation evidence.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
import os
from pathlib import Path
import sys
from threading import Lock, RLock, get_ident

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, decimal_string, domain_payload, utc_timestamp
from trading_system.contracts.wire import ContractCodec
from trading_system.engine.nautilus.bridge import BridgeAuthority, NautilusEngine
from trading_system.engine.paper import PaperEngine
from trading_system.execution.dispatcher import clock_now
from trading_system.recovery.gate import RecoveryGate
from trading_system.recovery.reconciler import valid_ref
from .emergency_journal import EmergencyJournal, JournalAuthority, regular_file


class OwnerDenied(ValueError):
    pass


def shard_key(shard: t.ExecutionShard) -> tuple:
    return shard.environment, shard.venue_account_id, shard.product


def scope_matches(scope: t.ScopeKey, shard: t.ExecutionShard) -> bool:
    return (scope.environment, scope.venue_account_id, scope.product) == shard_key(shard)


class LinuxShardLock:
    """Advisory flock for a provisioned private directory; never unlink the inode.

    Provisioning must give each account/product its own OS user and credential
    volume. This class checks the local uid and directory, not cross-host fencing
    or volume mounts. No stale PID or expired lease can remove a live flock.
    """
    def __init__(self, directory: Path, *, expected_uid: int):
        self.directory, self.expected_uid = Path(directory), expected_uid

    @contextmanager
    def claim(self, shard: t.ExecutionShard):
        if sys.platform != 'linux':
            raise OwnerDenied('FIXED_LINUX_HOST_REQUIRED')
        import fcntl
        directory = self.directory
        if (type(self.expected_uid) is not int or self.expected_uid <= 0
                or os.getuid() != self.expected_uid or directory.is_symlink() or not directory.is_dir()):
            raise OwnerDenied('DEDICATED_NONROOT_OS_USER_REQUIRED')
        metadata = directory.stat()
        if metadata.st_uid != self.expected_uid or metadata.st_mode & 0o077:
            raise OwnerDenied('PRIVATE_SHARD_DIRECTORY_REQUIRED')
        name = canonical_hash({'environment': str(shard.environment), 'account': shard.venue_account_id,
                               'product': str(shard.product)}) + '.lock'
        fd = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            regular_file(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


EMERGENCY_CHECKS = frozenset({'PREAUTHORIZED', 'CURRENT_OWNER', 'NOT_EXTERNALLY_FENCED',
    'NO_TAKEOVER', 'OWNED_TARGET', 'CURRENT_QUANTITY', 'PRICE_BOUND', 'CAPABILITY',
    'RISK_REDUCTION', 'NO_UNKNOWN_CONFLICT', 'ORIGINAL_COMMAND_UNSENT'})


@dataclass(frozen=True, kw_only=True)
class EmergencyFacts:
    """Fresh exact-request attestation from a server-held preauthorization reader.

    This reader must work independently of the failed primary ledger. Inability
    to prove no takeover/revocation, quantity or prior unknowns denies the action.
    It must serialize revocations with ExecutionOwner.guard; no default reader.
    """
    scope: t.ScopeKey
    lease: t.OwnerLease
    request_hash: str
    context: t.EmergencyContext
    closing_side: str
    checks: frozenset[str]
    expires_at: str
    deadline_monotonic_ns: int


@dataclass(frozen=True, kw_only=True)
class JournalMergeEvidence:
    scope: t.ScopeKey
    execution_epoch: int
    journal_head: str
    command_ids: tuple[str, ...]
    evidence: t.EvidenceRef
    platform_reconciled: bool


@dataclass(frozen=True, kw_only=True)
class OwnerEvent:
    kind: str
    key: str
    received_monotonic_ns: int
    deadline_monotonic_ns: int


class PriorityInbox:
    """Safety work is retained; research coalesces by key and sheds oldest keys.

    A rejected safety put remains the producer's responsibility: stop intake,
    retain/reconcile that event, and recover before installing a fresh inbox.
    An overload latch cannot be cleared by merely draining the queue.
    """
    def __init__(self, *, research_capacity=128, safety_capacity=4096):
        if any(type(n) is not int or n < 1 for n in (research_capacity, safety_capacity)):
            raise ValueError('positive bounded inbox capacities required')
        self._safety = deque()
        self._research = OrderedDict()
        self._lock = RLock()
        self.research_capacity, self.safety_capacity = research_capacity, safety_capacity
        self.overloaded = False

    def put(self, event: OwnerEvent):
        if (event.kind not in {'PROTECTION', 'ORDER', 'RESEARCH', 'CONTROL_DISCONNECTED'}
                or not event.key or type(event.received_monotonic_ns) is not int
                or type(event.deadline_monotonic_ns) is not int
                or event.received_monotonic_ns < 0
                or event.deadline_monotonic_ns <= event.received_monotonic_ns):
            raise OwnerDenied('INVALID_EVENT')
        with self._lock:
            if event.kind == 'CONTROL_DISCONNECTED':
                return  # A control client cannot stop the execution owner.
            if event.kind != 'RESEARCH':
                if len(self._safety) >= self.safety_capacity:
                    self.overloaded = True
                    raise OwnerDenied('SAFETY_BACKLOG_REQUIRES_RECOVERY')
                self._safety.append(event)
            else:
                previous = self._research.get(event.key)
                if previous is not None and previous.received_monotonic_ns > event.received_monotonic_ns:
                    return
                self._research[event.key] = event
                self._research.move_to_end(event.key)
                while len(self._research) > self.research_capacity:
                    self._research.popitem(last=False)

    def pop(self, now: t.ClockReading) -> OwnerEvent | None:
        with self._lock:
            # Order/position facts are processed even when old; only research is
            # dropped on expiry. Revocation/side-effect deadlines are checked later.
            if self._safety:
                return self._safety.popleft()
            if self.overloaded or now.clock_health != 'HEALTHY':
                self._research.clear()
                return None
            while self._research:
                _, event = self._research.popitem(last=False)
                if event.received_monotonic_ns <= now.monotonic_ns < event.deadline_monotonic_ns:
                    return event
            return None


class ExecutionOwner:
    def __init__(self, lease: t.OwnerLease, *, journal_path: Path, engine: NautilusEngine | PaperEngine,
                 claim, read_emergency=None, database_available=None, dispatcher=None,
                 recovery_gate: RecoveryGate | None = None, read_checklist=None,
                 read_merge=None, clock=clock_now, events=None, handle_event=None):
        concrete_offline_engine = type(engine) is NautilusEngine
        concrete_paper_engine = (type(engine) is PaperEngine
            and lease.shard.environment == t.Environment.PAPER
            and scope_matches(engine.scope, lease.shard))
        if (lease.shard.environment not in (t.Environment.PAPER, t.Environment.RESEARCH)
                or not lease.instance_id or not lease.shard.node_id
                or type(lease.shard.execution_epoch) is not int or lease.shard.execution_epoch < 1
                or not (concrete_offline_engine or concrete_paper_engine) or engine.live_enabled):
            raise OwnerDenied('OFFLINE_OWNER_COMPOSITION_REQUIRED')
        self.lease, self.engine, self.claim = lease, engine, claim
        self.read_emergency, self.database_available = read_emergency, database_available
        self.dispatcher, self.recovery_gate = dispatcher, recovery_gate
        self.read_checklist, self.read_merge, self.clock = read_checklist, read_merge, clock
        self.events, self.handle_event = events, handle_event
        self.journal = EmergencyJournal(journal_path, authorize=self._authorize, clock=clock)
        self._lock, self._run_lock = RLock(), Lock()
        self._thread = None
        self._authority = None
        self._last_monotonic = -1
        self._last_utc = None
        self._faults: set[str] = set()
        self._unresolved: dict[str, t.ReconciliationCase] = {}
        self.last_reason = 'NOT_RUN'

    @property
    def live_enabled(self):
        return False

    @property
    def faults(self):
        return tuple(sorted(self._faults))

    def _reading(self):
        now = self.clock()
        wall = utc_timestamp(now.utc_now)
        if (now.clock_health != 'HEALTHY' or type(now.monotonic_ns) is not int
                or now.monotonic_ns < self._last_monotonic or now.monotonic_ns < 0
                or (self._last_utc is not None and (
                    wall < self._last_utc or abs(
                        (wall - self._last_utc).total_seconds()
                        - (now.monotonic_ns - self._last_monotonic) / 1_000_000_000
                    ) > 1))):
            self._faults.add('CLOCK_UNHEALTHY')
            raise OwnerDenied('CLOCK_UNHEALTHY')
        self._last_monotonic, self._last_utc = now.monotonic_ns, wall
        if 'CLOCK_UNHEALTHY' in self._faults:
            raise OwnerDenied('CLOCK_RECOVERY_REQUIRED')
        return now

    @contextmanager
    def session(self, shard: t.ExecutionShard):
        if shard != self.lease.shard or not self._run_lock.acquire(blocking=False):
            raise OwnerDenied('OWNER_ALREADY_RUNNING_OR_SHARD_MISMATCH')
        try:
            with self.claim(shard):
                with self._lock:
                    self._thread = get_ident()
                    try:
                        for entry in self.journal.snapshot():
                            self._remember(entry.command, 'JOURNAL_REQUIRES_MERGE_AND_PLATFORM_QUERY')
                        yield self
                    finally:
                        self._authority, self._thread = None, None
        finally:
            self._run_lock.release()

    @contextmanager
    def guard(self, lease: t.OwnerLease):
        with self._lock:
            if self._thread != get_ident() or lease != self.lease:
                raise OwnerDenied('ACTIVE_EXCLUSIVE_OWNER_REQUIRED')
            now = self._reading()
            if (now.monotonic_ns >= lease.deadline_monotonic_ns
                    or utc_timestamp(now.utc_now) >= utc_timestamp(lease.expires_at)):
                raise OwnerDenied('OWNER_LEASE_EXPIRED')
            yield

    @contextmanager
    def bridge_authority(self, command_id: str):
        with self.guard(self.lease):
            authority = self._authority
            if authority is None or authority.command.command_id != command_id:
                raise OwnerDenied('NO_CURRENT_EMERGENCY_BRIDGE_AUTHORITY')
            yield authority

    def _authorize(self, record: t.EmergencyRecord) -> JournalAuthority:
        if self._thread != get_ident():
            raise OwnerDenied('ACTIVE_EXCLUSIVE_OWNER_REQUIRED')
        now = self._reading()
        facts = self.read_emergency(record) if self.read_emergency else None
        command, context = record.command, record.context
        if (not isinstance(facts, EmergencyFacts) or facts.lease != self.lease
                or facts.context != context or not scope_matches(facts.scope, self.lease.shard)
                or facts.request_hash != canonical_hash(domain_payload(record))
                or not EMERGENCY_CHECKS <= facts.checks
                or context.context.environment != facts.scope.environment
                or facts.scope not in context.context.scopes
                or not context.context.authorization_id or not context.context.identity_id
                or not context.context.deployment_id
                or command.ownership_epoch != context.context.ownership_epoch
                or command.policy_revision != context.context.policy_revision
                or command.execution_epoch != self.lease.shard.execution_epoch
                or command.state != 'DURABLE_READY'
                or command.operation not in {'CLOSE', 'CANCEL', 'SET_PROTECTION'}
                or (command.environment, command.venue_account_id, command.product, command.instrument_uid)
                != (facts.scope.environment, facts.scope.venue_account_id, facts.scope.product, facts.scope.instrument_uid)
                or not valid_ref(context.quantity_evidence, facts.scope, now.utc_now, 60)
                or utc_timestamp(now.utc_now) < utc_timestamp(command.created_at)):
            raise OwnerDenied('EMERGENCY_AUTHORITY_MISSING_OR_STALE')
        for value in (command.ownership_epoch, command.execution_epoch, command.policy_revision):
            if type(value) is not int or value < 1:
                raise OwnerDenied('INVALID_EPOCH_OR_REVISION')
        if (type(facts.deadline_monotonic_ns) is not int
                or now.monotonic_ns >= min(facts.deadline_monotonic_ns, self.lease.deadline_monotonic_ns)
                or any(utc_timestamp(now.utc_now) >= utc_timestamp(expiry)
                       for expiry in (facts.expires_at, context.expires_at, self.lease.expires_at))):
            raise OwnerDenied('EMERGENCY_AUTHORITY_EXPIRED')
        body = ContractCodec().encode(command)['canonical_payload']
        if canonical_hash(body) != command.payload_hash:
            raise OwnerDenied('PAYLOAD_HASH_MISMATCH')
        if command.operation == 'CANCEL':
            if not command.target_order_id:
                raise OwnerDenied('EXACT_OWNED_TARGET_REQUIRED')
        else:
            quantity = decimal_string(body.get('quantity'))
            if (not context.reducible_quantity.value.is_finite()
                    or not context.maximum_slippage_bps.is_finite() or context.maximum_slippage_bps < 0
                    or quantity <= 0 or quantity > context.reducible_quantity.value
                    or body.get('side') != facts.closing_side or facts.closing_side not in {'BUY', 'SELL'}
                    or body.get('position_side', 'BOTH') != facts.scope.venue_leg
                    or body.get('quote_notional') is not None or body.get('full_position_exit') is True
                    or (facts.scope.product == t.Product.USD_M_PERP and body.get('reduce_only') is not True)):
                raise OwnerDenied('BOUNDED_REDUCTION_REQUIRED')
            if command.operation == 'CLOSE' and body.get('order_type') not in context.allowed_order_types:
                raise OwnerDenied('EXIT_TYPE_NOT_PREAUTHORIZED')
        return JournalAuthority(scope=facts.scope, request_hash=facts.request_hash,
            expires_at=min((facts.expires_at, context.expires_at, self.lease.expires_at), key=utc_timestamp),
            deadline_monotonic_ns=min(facts.deadline_monotonic_ns, self.lease.deadline_monotonic_ns))

    def _remember(self, command, reason):
        scope = t.ScopeKey(environment=t.Environment(command.environment), venue_account_id=command.venue_account_id,
            product=t.Product(command.product), instrument_uid=command.instrument_uid,
            venue_leg=ContractCodec().encode(command)['canonical_payload'].get('position_side', 'BOTH'))
        self._unresolved[command.command_id] = t.ReconciliationCase(case_id='emergency:' + command.command_id,
            scope=scope, command_ids=(command.command_id,), position_ids=(), missing=(reason,),
            query_budget=t.QueryBudget(maximum_requests=1, window_seconds=60, minimum_interval_ms=1000, retry_after=None),
            attempts=0, next_query_at=self.clock().utc_now, state='OPEN')

    def emergency(self, record: t.EmergencyRecord) -> t.CommandReceipt:
        with self.guard(self.lease):
            if self.database_available is None or self.database_available() is not False:
                raise OwnerDenied('EXPLICIT_DB_OUTAGE_REQUIRED_USE_NORMAL_DISPATCH')
            self._authorize(record)
            if self._unresolved:
                raise OwnerDenied('QUERY_ORIGINAL_BEFORE_ANOTHER_EMERGENCY')
            command = record.command
            try:
                permit = self.journal.append_and_fsync(record)
                command = replace(command, state='MAY_HAVE_SENT', durable_record_ref=permit.durable_record_ref)
                self._remember(command, 'EXIT_UNCONFIRMED_QUERY_ORIGINAL')
                fresh = self._authorize(record)  # revocation/quantity may change during fsync
                if (fresh.deadline_monotonic_ns < permit.deadline_monotonic_ns
                        or utc_timestamp(fresh.expires_at) < utc_timestamp(permit.expires_at)):
                    raise OwnerDenied('AUTHORITY_CHANGED_DURING_FSYNC')
                self._authority = BridgeAuthority(command=command, permit=permit, lease=self.lease,
                    ownership_epoch=command.ownership_epoch, policy_revision=command.policy_revision)
                method = self.engine.cancel if command.operation == 'CANCEL' else self.engine.submit
                method(command, permit)
                reason = 'OFFLINE_ATTEMPT_ONLY_EXIT_UNCONFIRMED'
            except Exception:
                self._remember(command, 'STORAGE_OR_BACKING_FAILURE_EXIT_UNCONFIRMED')
                self._faults.add('EXIT_UNCONFIRMED_KEEP_NATIVE_PROTECTION')
                reason = 'STORAGE_OR_BACKING_FAILURE_EXIT_UNCONFIRMED'
            finally:
                self._authority = None
            # A bridge observation is never evidence of an exited position.
            return t.CommandReceipt(receipt_id='emergency:' + command.command_id,
                command_id=command.command_id, status=t.CommandState.UNKNOWN, reason=reason, existing_result_ref=None)

    def recovery(self, scope: t.ScopeKey) -> t.RecoveryVerdict:
        with self.guard(self.lease):
            try:
                if self.events is not None and self.events.overloaded:
                    self._faults.add('SAFETY_BACKLOG_REQUIRES_RECOVERY')
                if self._faults - {'EXIT_UNCONFIRMED_KEEP_NATIVE_PROTECTION'}:
                    raise OwnerDenied('OWNER_FAULT_REQUIRES_RECOVERY')
                if not scope_matches(scope, self.lease.shard) or self.database_available() is not True:
                    raise OwnerDenied('DATABASE_OR_SCOPE_UNAVAILABLE')
                entries = self.journal.snapshot()
                journal_ids = {e.command.command_id for e in entries}
                if not set(self._unresolved) <= journal_ids:
                    raise OwnerDenied('UNCERTAIN_JOURNAL_WRITE_REQUIRES_RECOVERY')
                # A per-instrument gate must not clear another instrument/leg.
                for entry in entries:
                    command = entry.command
                    leg = ContractCodec().encode(command)['canonical_payload'].get('position_side', 'BOTH')
                    if (command.environment, command.venue_account_id, command.product,
                            command.instrument_uid, leg) != (scope.environment, scope.venue_account_id,
                            scope.product, scope.instrument_uid, scope.venue_leg):
                        raise OwnerDenied('SHARD_WIDE_JOURNAL_RECOVERY_REQUIRED')
                merge = self.read_merge(scope) if self.read_merge else None
                if entries:
                    if (not isinstance(merge, JournalMergeEvidence) or merge.scope != scope
                            or merge.execution_epoch != self.lease.shard.execution_epoch
                            or merge.journal_head != entries[-1].record_hash or merge.platform_reconciled is not True
                            or len(merge.command_ids) != len(journal_ids)
                            or set(merge.command_ids) != journal_ids
                            or not valid_ref(merge.evidence, scope, self._reading().utc_now, 60)):
                        raise OwnerDenied('JOURNAL_MERGE_AND_PLATFORM_EVIDENCE_REQUIRED')
                elif self._unresolved:
                    raise OwnerDenied('UNCERTAIN_JOURNAL_WRITE_REQUIRES_RECOVERY')
                checklist = self.read_checklist(scope) if self.read_checklist else None
                if not isinstance(checklist, t.RecoveryChecklist) or checklist.scope != scope or self.recovery_gate is None:
                    raise OwnerDenied('RECOVERY_CHECKLIST_REQUIRED')
                verdict = self.recovery_gate.evaluate(checklist)
                # Readers may take time or observe a concurrent state change.
                with self.guard(self.lease):
                    pass
                if (self.database_available() is not True or self.journal.snapshot() != entries
                        or (entries and self.read_merge(scope) != merge)):
                    raise OwnerDenied('RECOVERY_SNAPSHOT_CHANGED')
                if verdict.ready and not verdict.missing and not verdict.risk_locks:
                    self._unresolved.clear()
                    self._faults.discard('EXIT_UNCONFIRMED_KEEP_NATIVE_PROTECTION')
                return verdict
            except Exception:
                return t.RecoveryVerdict(ready=False, missing=('JOURNAL_OR_RECOVERY_EVIDENCE_REQUIRED',), risk_locks=())

    def dispatch(self, command_id: str, scope: t.ScopeKey) -> t.CommandReceipt:
        with self.guard(self.lease):
            if (self.dispatcher is None or self.dispatcher.engine is not self.engine
                    or self.dispatcher.scope != scope or not self.recovery(scope).ready):
                raise OwnerDenied('RECOVERY_REQUIRED')
            return self.dispatcher.dispatch(command_id, self.lease)

    def run(self, shard: t.ExecutionShard) -> t.HostExit:
        code = 2
        try:
            with self.session(shard):
                if self.events is None or self.handle_event is None:
                    raise OwnerDenied('OFFLINE_EVENT_SOURCE_REQUIRED')
                while True:
                    if self.events.overloaded:
                        self._faults.add('SAFETY_BACKLOG_REQUIRES_RECOVERY')
                    try:
                        now = self._reading()
                    except OwnerDenied:
                        # Continue consuming safety facts; stale research cannot
                        # create fresh work while clock recovery is required.
                        now = replace(self.clock(), clock_health='DEGRADED')
                    event = self.events.pop(now)
                    if event is None:
                        break
                    self.handle_event(event, self)
                code = 0 if not self._unresolved and not self._faults else 2
                self.last_reason = 'OFFLINE_DRAIN_COMPLETE_NOT_NATIVE_READINESS'
        except Exception:
            self._faults.add('OWNER_STOPPED_RECOVERY_REQUIRED')
            self.last_reason = 'OWNER_STOPPED_RECOVERY_REQUIRED'
        return t.HostExit(instance_id=self.lease.instance_id, exit_code=code,
                          unresolved=tuple(self._unresolved.values()), evidence=())
