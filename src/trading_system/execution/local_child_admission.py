"""Protected local-child admission, independent command and single-use permit.

This is the writer boundary for ORD-33/44. It consumes the existing activation
source, exact child compilation/request, cumulative quantity preview and a
trusted current-facts port (target, funding, owner, clock, lease, risk). It
persists the child allocation and its own durable command in the same
transaction that already holds the collateral mutex.

It deliberately does not mint a native parent command, reuse a parent permit,
release funds, infer cancellation, or reinterpret an old UNKNOWN. The storage
migration that grants these writes is a dormant candidate until shared DEV
qualification runs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, domain_payload, utc_timestamp
from trading_system.contracts.order_program import amount, identifier
from trading_system.intents.local_child_compilation import LocalChildCompilation
from trading_system.storage.models import account_key, decode, scope_id

from .creation_requests import digest, require
from .local_child_quantity import LocalChildCommitment, preview_local_child_quantity
from .local_child_requests import LocalChildRequest, materialize_local_child_request
from .local_program_activation import read_local_activation


VERSION = '2026-10-31'
MAX_LOCAL_CHILD_WRITES = 3


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildCommand:
    contract_version: Literal['2026-10-31']
    record_kind: Literal['LOCAL_CHILD_COMMAND_V1']
    state: Literal['DURABLE_READY', 'MAY_HAVE_SENT', 'UNKNOWN']
    command_id: str
    scope: t.ScopeKey
    execution_epoch: int
    activation_id: str
    activation_bundle_hash: str
    parent_hash: str
    request_hash: str
    commitment_hash: str
    target_hash: str
    funding_hash: str
    risk_hash: str
    authorization_id: str
    owner_strategy_id: str
    ownership_epoch: int
    policy_revision: int
    lease_hash: str
    created_at: str
    valid_until: str

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildCurrentFacts:
    """Trusted current inputs supplied by the owner-transaction composition.

    The loader must derive these from protected source/risk/funding readers
    under the same collateral mutex. Constructing this record grants nothing.
    """
    clock: t.ClockReading
    lease: t.OwnerLease
    target: object
    funding: object
    target_hash: str
    funding_hash: str
    authorization_id: str
    risk_clear: bool
    risk_hash: str
    valid_until: str
    volume: object | None = None
    interval_seconds: int | None = None
    quantity_step: str | None = None
    participation_bps: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildAdmission:
    command: LocalChildCommand
    request: LocalChildRequest
    commitment: LocalChildCommitment
    committed_quantity: str
    unallocated_quantity: str
    quantity_unit: str
    duplicate: bool

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildPermit:
    command_id: str
    scope: t.ScopeKey
    request_hash: str
    commitment_hash: str
    token: str
    expires_at: str
    deadline_monotonic_ns: int


def _token_hash(token: str) -> str:
    return canonical_hash({'local_child_permit_token': token})


def _scope_of(value: t.ScopeKey) -> str:
    return scope_id(value)


def _require_schema(tx) -> None:
    row = tx.execute("SELECT to_regclass('ledger.local_child_writes')").fetchone()
    require(row is not None and row[0] is not None, 'LOCAL_CHILD_WRITE_SCHEMA_NOT_INSTALLED')
    row = tx.execute("SELECT to_regclass('ledger.local_child_commitments_v1')").fetchone()
    require(row is not None and row[0] is not None, 'LOCAL_CHILD_STORAGE_NOT_INSTALLED')


def read_local_child_command(tx, command_id: str) -> LocalChildCommand | None:
    identifier(command_id)
    rows = tx.execute('SELECT sequence,payload FROM ledger.local_child_writes '
                      'WHERE scope_id=%s AND command_id=%s ORDER BY sequence',
                      (_scope_of(tx.scope), command_id)).fetchall()
    if not rows:
        return None
    require(1 <= len(rows) <= MAX_LOCAL_CHILD_WRITES, 'LOCAL_CHILD_WRITE_HISTORY_INVALID')
    writes = tuple(decode(LocalChildCommand, payload) for _, payload in rows)
    require(tuple(sequence for sequence, _ in rows) == tuple(range(1, len(rows) + 1)),
            'LOCAL_CHILD_WRITE_HISTORY_INVALID')
    require(writes[0].state == 'DURABLE_READY' and writes[0].command_id == command_id
            and writes[0].scope == tx.scope, 'LOCAL_CHILD_WRITE_HISTORY_INVALID')
    for index, current in enumerate(writes[1:], 1):
        expected = 'MAY_HAVE_SENT' if index == 1 else 'UNKNOWN'
        require(current.state == expected
                and digest(replace(current, state='DURABLE_READY')) == digest(writes[0]),
                'LOCAL_CHILD_WRITE_HISTORY_INVALID')
    return writes[-1]


def _validate_current(current, activation, child, command_id):
    require(type(current) is LocalChildCurrentFacts, 'LOCAL_CHILD_CURRENT_FACTS_REQUIRED')
    clock, lease = current.clock, current.lease
    require(type(clock) is t.ClockReading and clock.clock_health == 'HEALTHY'
            and type(clock.monotonic_ns) is int and clock.monotonic_ns >= 0,
            'LOCAL_CHILD_CURRENT_CLOCK_REQUIRED')
    require(type(lease) is t.OwnerLease and type(lease.shard) is t.ExecutionShard
            and isinstance(lease.instance_id, str) and bool(lease.instance_id)
            and lease.shard.execution_epoch > 0
            and (lease.shard.environment, lease.shard.venue_account_id, lease.shard.product) == (
                child.parent.scope.environment, child.parent.scope.venue_account_id,
                child.parent.scope.product)
            and type(lease.deadline_monotonic_ns) is int
            and clock.monotonic_ns < lease.deadline_monotonic_ns,
            'LOCAL_CHILD_CURRENT_LEASE_REQUIRED')
    now = utc_timestamp(clock.utc_now)
    authority = activation.authority
    require(now < utc_timestamp(lease.expires_at), 'LOCAL_CHILD_LEASE_EXPIRED')
    require(now < utc_timestamp(authority.submission_valid_until),
            'LOCAL_CHILD_PARENT_AUTHORITY_EXPIRED')
    require(now < utc_timestamp(current.valid_until), 'LOCAL_CHILD_CURRENT_FACTS_EXPIRED')
    require(current.risk_clear is True and isinstance(current.risk_hash, str)
            and bool(current.risk_hash), 'LOCAL_CHILD_CURRENT_RISK_REQUIRED')
    require(current.authorization_id == authority.authorization_id,
            'LOCAL_CHILD_AUTHORIZATION_CHANGED')
    target = current.target
    require(target is not None and getattr(target, 'compilation_hash', None) == digest(child)
            and getattr(target, 'facts_hash', None) == current.target_hash
            and getattr(target, 'unallocated_target_quantity', None) is not None
            and target.unallocated_target_quantity.value >= 0,
            'LOCAL_CHILD_CURRENT_TARGET_REQUIRED')
    funding = current.funding
    require(funding is not None and getattr(funding, 'blockers', None) == ()
            and getattr(funding, 'execution_authorized', None) is False
            and isinstance(current.funding_hash, str) and bool(current.funding_hash),
            'LOCAL_CHILD_CURRENT_FUNDING_REQUIRED')
    template = child.parent.original_program.template
    if template == 'VP':
        volume = current.volume
        require(volume is not None
                and getattr(volume, 'source_verified', None) is True
                and getattr(volume, 'coverage_complete', None) is True
                and getattr(volume, 'scan_status', None) == 'PAGE_BOUNDARY_OBSERVED'
                and getattr(volume, 'quantity_unit', None) == child.parent.nodes[0].quantity_unit,
                'LOCAL_VP_VERIFIED_VOLUME_REQUIRED')
        require(type(current.interval_seconds) is int and current.interval_seconds > 0
                and isinstance(current.quantity_step, str) and bool(current.quantity_step)
                and isinstance(current.participation_bps, str) and bool(current.participation_bps),
                'LOCAL_VP_SCHEDULE_POLICY_REQUIRED')
    elif current.interval_seconds is not None:
        require(type(current.interval_seconds) is int and current.interval_seconds > 0
                and isinstance(current.quantity_step, str) and bool(current.quantity_step),
                'LOCAL_TWAP_SCHEDULE_POLICY_REQUIRED')
    return min(utc_timestamp(authority.submission_valid_until),
               utc_timestamp(current.valid_until), utc_timestamp(lease.expires_at))


def _owner_current(tx, activation):
    authority = activation.authority
    row = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
                     'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s '
                     'AND venue_leg=%s FOR SHARE',
                     (*account_key(authority.scope), authority.scope.instrument_uid,
                      authority.scope.venue_leg)).fetchone()
    require(row is not None and row[0] == authority.owner_strategy_id
            and row[1] == authority.ownership_epoch, 'LOCAL_CHILD_STALE_OWNER')
    require(type(row[2]) is int and row[2] >= 1, 'LOCAL_CHILD_STALE_OWNER')
    return row[2]


def _build_command(activation, child, request, current, command_id, expires, policy_revision):
    authority = activation.authority
    return LocalChildCommand(
        contract_version=VERSION, record_kind='LOCAL_CHILD_COMMAND_V1',
        state='DURABLE_READY', command_id=command_id, scope=child.parent.scope,
        execution_epoch=current.lease.shard.execution_epoch,
        activation_id=activation.activation_id,
        activation_bundle_hash=activation.bundle_hash,
        parent_hash=digest(child.parent), request_hash=digest(request),
        commitment_hash=digest(LocalChildCommitment(compilation=child, request=request)),
        target_hash=current.target_hash, funding_hash=current.funding_hash,
        risk_hash=current.risk_hash, authorization_id=authority.authorization_id,
        owner_strategy_id=authority.owner_strategy_id,
        ownership_epoch=authority.ownership_epoch, policy_revision=policy_revision,
        lease_hash=digest(current.lease), created_at=current.clock.utc_now,
        valid_until=expires.isoformat().replace('+00:00', 'Z'))


def admit_local_child(tx, *, activation_id, child, command_id, rules, capabilities,
                      load_clock, load_lease, load_current, **context):
    """Atomically persist one application-child allocation and durable command."""
    require(tx.active is True, 'LOCAL_CHILD_ACTIVE_TRANSACTION_REQUIRED')
    require(type(child) is LocalChildCompilation, 'LOCAL_CHILD_COMPILATION_REQUIRED')
    identifier(activation_id)
    identifier(command_id)
    require(child.parent.scope == tx.scope, 'LOCAL_CHILD_FOREIGN_SCOPE')
    require(tx.scope.environment in (t.Environment.PAPER, t.Environment.RESEARCH),
            'ENVIRONMENT_DISABLED')
    tx.require_scope(child.parent.scope)
    _require_schema(tx)
    activation = read_local_activation(tx, activation_id)
    require(activation is not None and activation.authority.scope == child.parent.scope
            and activation.authority.compilation_hash == digest(child.parent),
            'LOCAL_CHILD_ACTIVATION_REQUIRED')
    request = materialize_local_child_request(child, activation_id, command_id, rules,
                                              capabilities, **context)
    from trading_system.storage.local_child_commitments import read_local_child_commitments
    snapshot = read_local_child_commitments(tx, activation_id)
    history = snapshot.commitments
    committed = tuple(history)
    preview = preview_local_child_quantity(child.parent, activation_id, committed,
                                           LocalChildCommitment(compilation=child, request=request))
    # Replay is read-only and does not require a fresh permit or clock.
    existing = read_local_child_command(tx, command_id)
    if existing is not None:
        require(existing.activation_id == activation_id
                and existing.activation_bundle_hash == activation.bundle_hash
                and existing.parent_hash == digest(child.parent)
                and existing.request_hash == digest(request)
                and existing.commitment_hash == digest(LocalChildCommitment(compilation=child,
                                                                           request=request)),
                'LOCAL_CHILD_COMMAND_REPLAY_CONFLICT')
        return LocalChildAdmission(command=existing, request=request,
                                   commitment=LocalChildCommitment(compilation=child,
                                                                   request=request),
                                   committed_quantity=preview.committed_quantity,
                                   unallocated_quantity=preview.unallocated_quantity,
                                   quantity_unit=preview.quantity_unit, duplicate=True)

    clock = load_clock(tx) if load_clock else None
    lease = load_lease(tx) if load_lease else None
    initial = load_current(tx, activation, child, committed, command_id)
    require(type(initial) is LocalChildCurrentFacts and initial.clock is clock
            and initial.lease is lease, 'LOCAL_CHILD_CURRENT_FACTS_REQUIRED')
    expires = _validate_current(initial, activation, child, command_id)
    if initial.interval_seconds is not None:
        from .local_child_schedule import preview_local_schedule
        vp = child.parent.original_program.template == 'VP'
        volume = initial.volume if vp else None
        schedule = preview_local_schedule(
            child.parent, activated_at=activation.activated_at,
            now=initial.clock.utc_now, interval_seconds=initial.interval_seconds,
            committed_quantity=preview.committed_quantity,
            quantity_step=initial.quantity_step,
            observed_volume=volume.observed_quantity if vp else None,
            volume_unit=volume.quantity_unit if vp else None,
            participation_bps=initial.participation_bps)
        require(amount(child.node.quantity.value) <= amount(schedule.available_quantity),
                'LOCAL_CHILD_SCHEDULE_EXCEEDED')
    policy_revision = _owner_current(tx, activation)
    command = _build_command(activation, child, request, initial, command_id, expires,
                             policy_revision)
    physical = _scope_of(child.parent.scope)
    # Write first, allocation second; the SQL deferred constraint trigger proves
    # both exist at commit, and the allocation trigger proves the write was in
    # this same database transaction.
    tx._append('local_child_writes', command_id, command, 0)
    tx.execute('INSERT INTO ledger.local_child_commitments_v1 '
               '(scope_id,command_id,activation_id,commitment_hash,payload,transaction_id) '
               'VALUES(%s,%s,%s,%s,%s::jsonb,%s)',
               (physical, command_id, activation_id,
                digest(LocalChildCommitment(compilation=child, request=request)),
                canonical_json(domain_payload(LocalChildCommitment(compilation=child,
                                                                   request=request))),
                tx.transaction_id))
    ids = request.request.assigned_ids
    identities = tuple(m.client_id for m in ids.members) + (
        (ids.parent_client_id,) if ids.parent_client_id else ())
    for client_id in identities:
        tx.execute('INSERT INTO ledger.program_client_identities '
                   '(environment,account_id,product,service_namespace,client_id,scope_id,command_id) '
                   'VALUES(%s,%s,%s,%s,%s,%s,%s)',
                   (*account_key(child.parent.scope), request.request.namespace,
                    client_id, physical, command_id))
    tx.execute('INSERT INTO ledger.outbox(message_id,scope_id,aggregate_id,sequence,kind,transaction_id) '
               'VALUES(%s,%s,%s,1,%s,%s)',
               ('local-child-ready:' + command_id, physical, command_id,
                'LOCAL_CHILD_COMMAND_READY', tx.transaction_id))
    final = load_current(tx, activation, child, committed, command_id)
    require(type(final) is LocalChildCurrentFacts
            and final.clock.monotonic_ns >= initial.clock.monotonic_ns
            and utc_timestamp(final.clock.utc_now) >= utc_timestamp(initial.clock.utc_now)
            and utc_timestamp(final.clock.utc_now) < utc_timestamp(final.valid_until)
            and final.target_hash == initial.target_hash
            and final.funding_hash == initial.funding_hash
            and final.risk_hash == initial.risk_hash
            and final.authorization_id == initial.authorization_id
            and final.risk_clear is True,
            'LOCAL_CHILD_CURRENT_STATE_CHANGED')
    return LocalChildAdmission(command=command, request=request,
                               commitment=LocalChildCommitment(compilation=child,
                                                               request=request),
                               committed_quantity=preview.committed_quantity,
                               unallocated_quantity=preview.unallocated_quantity,
                               quantity_unit=preview.quantity_unit, duplicate=False)


def _current_for_command(tx, command, activation, child, committed, load_current):
    current = load_current(tx, activation, child, committed, command.command_id)
    expires = _validate_current(current, activation, child, command.command_id)
    require(digest(current.lease) == command.lease_hash, 'LOCAL_CHILD_LEASE_CHANGED')
    require(current.target_hash == command.target_hash,
            'LOCAL_CHILD_CURRENT_TARGET_CHANGED')
    require(current.funding_hash == command.funding_hash,
            'LOCAL_CHILD_CURRENT_FUNDING_CHANGED')
    require(current.risk_hash == command.risk_hash, 'LOCAL_CHILD_CURRENT_RISK_CHANGED')
    policy_revision = _owner_current(tx, activation)
    require(policy_revision == command.policy_revision, 'LOCAL_CHILD_STALE_OWNER')
    return current, expires


def _restore_for_command(tx, command_id):
    from trading_system.storage.local_child_commitments import read_local_child_commitments
    command = read_local_child_command(tx, command_id)
    require(command is not None, 'LOCAL_CHILD_COMMAND_REQUIRED')
    activation = read_local_activation(tx, command.activation_id)
    require(activation is not None and activation.authority.scope == tx.scope
            and activation.bundle_hash == command.activation_bundle_hash,
            'LOCAL_CHILD_ACTIVATION_REQUIRED')
    snapshot = read_local_child_commitments(tx, command.activation_id)
    item = next((x for x in snapshot.commitments if x.request.request.command_id == command_id),
                None)
    require(item is not None and digest(item.request) == command.request_hash
            and digest(item) == command.commitment_hash
            and digest(item.compilation.parent) == command.parent_hash,
            'LOCAL_CHILD_COMMITMENT_SOURCE_CHANGED')
    return command, activation, item.compilation, snapshot.commitments


def mark_local_child_may(tx, command_id, lease, *, token_hash, load_clock, load_lease,
                         load_current, permit_ttl_ms):
    require(tx.active is True, 'LOCAL_CHILD_ACTIVE_TRANSACTION_REQUIRED')
    command, activation, child, committed = _restore_for_command(tx, command_id)
    require(command.state == 'DURABLE_READY', 'LOCAL_CHILD_QUERY_ORIGINAL_IDS')
    require(type(lease) is t.OwnerLease and digest(lease) == command.lease_hash,
            'LOCAL_CHILD_LEASE_CHANGED')
    current, expires = _current_for_command(tx, command, activation, child, committed,
                                            load_current)
    now = utc_timestamp(current.clock.utc_now)
    ttl = min(permit_ttl_ms, int((expires - now).total_seconds() * 1000),
              (lease.deadline_monotonic_ns - current.clock.monotonic_ns) // 1_000_000)
    require(type(ttl) is int and ttl >= 1, 'LOCAL_CHILD_PERMIT_WINDOW_EXPIRED')
    context = {'command': domain_payload(command), 'clock': domain_payload(current.clock),
               'lease': domain_payload(lease), 'target_hash': command.target_hash,
               'funding_hash': command.funding_hash, 'risk_hash': command.risk_hash}
    tx.execute('INSERT INTO ledger.local_child_dispatch_marks '
               '(scope_id,command_id,context,token_hash,expires_at,transaction_id) '
               'VALUES(%s,%s,%s::jsonb,%s,clock_timestamp()+(%s*interval \'1 millisecond\'),%s)',
               (_scope_of(command.scope), command_id, canonical_json(context), token_hash, ttl,
                tx.transaction_id))
    tx._append('local_child_writes', command_id, replace(command, state='MAY_HAVE_SENT'), 1)
    return replace(command, state='MAY_HAVE_SENT'), current.clock, ttl


def consume_local_child_permit(tx, permit, lease, *, load_clock, load_lease, load_current):
    require(type(permit) is LocalChildPermit and tx.active is True,
            'LOCAL_CHILD_PERMIT_REQUIRED')
    command, activation, child, committed = _restore_for_command(tx, permit.command_id)
    require(command.state == 'MAY_HAVE_SENT' and command.scope == permit.scope
            and command.request_hash == permit.request_hash
            and command.commitment_hash == permit.commitment_hash
            and digest(lease) == command.lease_hash, 'LOCAL_CHILD_PERMIT_BINDING_CHANGED')
    current, expires = _current_for_command(tx, command, activation, child, committed,
                                            load_current)
    now = utc_timestamp(current.clock.utc_now)
    require(now < utc_timestamp(permit.expires_at)
            and current.clock.monotonic_ns < permit.deadline_monotonic_ns,
            'LOCAL_CHILD_PERMIT_EXPIRED')
    stored = tx.execute('SELECT token_hash FROM ledger.local_child_dispatch_marks '
                        'WHERE scope_id=%s AND command_id=%s',
                        (_scope_of(command.scope), permit.command_id)).fetchone()
    require(stored is not None and stored[0] == _token_hash(permit.token),
            'LOCAL_CHILD_PERMIT_TOKEN_CHANGED')
    used = tx.execute('SELECT 1 FROM ledger.local_child_permit_consumptions '
                      'WHERE scope_id=%s AND command_id=%s',
                      (_scope_of(command.scope), permit.command_id)).fetchone()
    require(used is None, 'LOCAL_CHILD_PERMIT_ALREADY_CONSUMED')
    tx.execute('INSERT INTO ledger.local_child_permit_consumptions '
               '(scope_id,command_id,token_hash,context,transaction_id) '
               'VALUES(%s,%s,%s,%s::jsonb,%s)',
               (_scope_of(command.scope), permit.command_id, _token_hash(permit.token),
                canonical_json({'clock': domain_payload(current.clock), 'lease': domain_payload(lease),
                 'target_hash': command.target_hash, 'funding_hash': command.funding_hash,
                 'risk_hash': command.risk_hash}), tx.transaction_id))
    return command


def record_local_child_unknown(tx, command_id):
    command = read_local_child_command(tx, command_id)
    require(command is not None and command.state == 'MAY_HAVE_SENT',
            'LOCAL_CHILD_DURABLE_MAY_REQUIRED')
    tx._append('local_child_writes', command_id, replace(command, state='UNKNOWN'), 2)
    return command


class LocalChildCommandBoundary:
    """Server-only composition; no transport, no parent permit reuse."""

    def __init__(self, uow, scope, *, load_clock, load_lease, load_current, permit_ttl_ms=1000):
        self.uow, self.scope = uow, scope
        self.loaders = dict(load_clock=load_clock, load_lease=load_lease,
                            load_current=load_current)
        self.permit_ttl_ms = permit_ttl_ms

    def admit(self, **kwargs):
        with self.uow.transaction(self.scope) as tx:
            return admit_local_child(tx, load_clock=self.loaders['load_clock'],
                                     load_lease=self.loaders['load_lease'],
                                     load_current=self.loaders['load_current'], **kwargs)

    def mark_may_have_sent(self, command_id, lease):
        import secrets
        token = secrets.token_urlsafe(32)
        with self.uow.transaction(self.scope) as tx:
            command, clock, ttl = mark_local_child_may(
                tx, command_id, lease, token_hash=_token_hash(token),
                load_clock=self.loaders['load_clock'], load_lease=self.loaders['load_lease'],
                load_current=self.loaders['load_current'], permit_ttl_ms=self.permit_ttl_ms)
        expires = (utc_timestamp(clock.utc_now) + timedelta(milliseconds=ttl))
        return LocalChildPermit(command_id=command_id, scope=command.scope,
                                request_hash=command.request_hash,
                                commitment_hash=command.commitment_hash, token=token,
                                expires_at=expires.isoformat().replace('+00:00', 'Z'),
                                deadline_monotonic_ns=clock.monotonic_ns + ttl * 1_000_000)

    def consume(self, permit, lease):
        with self.uow.transaction(self.scope) as tx:
            return consume_local_child_permit(
                tx, permit, lease, load_clock=self.loaders['load_clock'],
                load_lease=self.loaders['load_lease'],
                load_current=self.loaders['load_current'])

    def record_unknown(self, command_id):
        with self.uow.transaction(self.scope) as tx:
            return record_local_child_unknown(tx, command_id)
