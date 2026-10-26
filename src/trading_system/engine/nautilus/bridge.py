"""Fail-closed EnginePort with an explicitly offline recording boundary.

No native methods, execution clients, signing, network transports, or arbitrary
send callbacks are installed here. IP-02 has no qualified native bindings yet.
Offline observations exercise application checks; they are never venue receipts.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from time import monotonic_ns
from typing import Callable

from jsonschema.exceptions import ValidationError

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, utc_timestamp
from trading_system.contracts.wire import ContractCodec, ExecutionCommand
from .id_codec import VenueIdCodec


class BridgeDenied(ValueError):
    """Local rejection is not evidence that a durable command was never sent."""


@dataclass(frozen=True, kw_only=True)
class ClientBinding:
    scope: t.ScopeKey
    client_id: str
    native_instrument_id: str


@dataclass(frozen=True, kw_only=True)
class BridgeAuthority:
    """Fresh trusted owner snapshot, NOT authority accepted from a caller.

The guard must load the committed command/permit, recheck current authorization,
risk/target ownership, and serialize revocation until exit. No ledger transaction
may remain open across the boundary. The IP-10 dispatcher owns those checks;
the native, cross-process integration still requires independent qualification.
"""
    command: ExecutionCommand
    permit: t.DurablePermit
    lease: t.OwnerLease
    ownership_epoch: int
    policy_revision: int


def _clock() -> t.ClockReading:
    return t.ClockReading(utc_now=datetime.now(timezone.utc).isoformat(),
                          monotonic_ns=monotonic_ns(), clock_health='HEALTHY')


@dataclass(frozen=True, kw_only=True)
class OfflineBridge:
    scope: t.ScopeKey
    codec: VenueIdCodec
    bindings: tuple[ClientBinding, ...]
    authority_guard: Callable[[str], AbstractContextManager[BridgeAuthority]]
    clock: Callable[[], t.ClockReading] = _clock


@dataclass(frozen=True, kw_only=True)
class OfflineWrite:
    command: ExecutionCommand
    permit: t.DurablePermit
    binding: ClientBinding
    identity: t.OrderIdentity


class NautilusEngine:
    def __init__(self, compatibility: t.CompatibilityMap, *, offline: OfflineBridge | None = None):
        if not isinstance(compatibility, t.CompatibilityMap):
            raise TypeError('typed CompatibilityMap required')
        self.compatibility = compatibility
        self._offline = offline
        self._lock = RLock()
        self._consumed: set[str] = set()
        self._observed_clients: set[str] = set()
        self._writes: list[OfflineWrite] = []
        if offline is not None:
            if (offline.scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH)
                    or offline.codec.scope != offline.scope):
                raise BridgeDenied('OFFLINE_SCOPE_REQUIRED')
            bindings = offline.bindings
            if (not bindings or any(not b.client_id.strip() or not b.native_instrument_id.strip()
                                    or b.scope != offline.scope for b in bindings)
                    or len({b.scope for b in bindings}) != len(bindings)):
                raise BridgeDenied('EXPLICIT_UNAMBIGUOUS_CLIENT_AND_INSTRUMENT_REQUIRED')

    @property
    def live_enabled(self) -> bool:
        return False

    @property
    def offline_writes(self) -> tuple[OfflineWrite, ...]:
        with self._lock:
            return tuple(self._writes)

    def enable_native_path(self, path: str) -> None:
        # Even a caller-supplied CompatibilityMap cannot activate a native API.
        raise BridgeDenied('NATIVE_PATH_NOT_QUALIFIED')

    def submit(self, command: ExecutionCommand, permit: t.DurablePermit) -> t.CommandReceipt:
        return self._write(command, permit, frozenset({'SUBMIT', 'CLOSE', 'SET_PROTECTION'}))

    def cancel(self, command: ExecutionCommand, permit: t.DurablePermit) -> t.CommandReceipt:
        return self._write(command, permit, frozenset({'CANCEL'}))

    def amend(self, command: ExecutionCommand, permit: t.DurablePermit) -> t.CommandReceipt:
        return self._write(command, permit, frozenset({'AMEND'}))

    def _validate(self, command, permit, authority):
        offline = self._offline
        assert offline is not None
        if (not isinstance(authority, BridgeAuthority)
                or not isinstance(authority.lease, t.OwnerLease)
                or not isinstance(authority.lease.shard, t.ExecutionShard)):
            raise BridgeDenied('CURRENT_OWNER_EVIDENCE_MISSING')
        if (authority.command != command or authority.permit != permit
                or command.state != 'MAY_HAVE_SENT' or not command.durable_record_ref
                or permit.durable_record_ref != command.durable_record_ref
                or permit.command_id != command.command_id or permit.payload_hash != command.payload_hash
                or permit.scope != offline.scope):
            raise BridgeDenied('DURABLE_COMMAND_OR_PERMIT_MISMATCH')
        scope, lease = offline.scope, authority.lease
        shard = lease.shard
        if (command.environment != scope.environment or command.venue_account_id != scope.venue_account_id
                or command.product != scope.product or command.instrument_uid != scope.instrument_uid
                or shard.environment != scope.environment or shard.venue_account_id != scope.venue_account_id
                or shard.product != scope.product or not shard.node_id or not lease.instance_id):
            raise BridgeDenied('OWNER_OR_PRODUCT_SCOPE_MISMATCH')
        for actual, granted, current in (
                (command.execution_epoch, permit.execution_epoch, shard.execution_epoch),
                (command.ownership_epoch, permit.ownership_epoch, authority.ownership_epoch),
                (command.policy_revision, permit.policy_revision, authority.policy_revision)):
            if any(type(v) is not int or v < 1 for v in (actual, granted, current)) or not actual == granted == current:
                raise BridgeDenied('STALE_EPOCH_OR_POLICY')
        try:
            body = ContractCodec().encode(command)
        except (TypeError, ValueError, ValidationError) as exc:
            raise BridgeDenied('INVALID_COMMAND_CONTRACT') from exc
        if canonical_hash(body['canonical_payload']) != command.payload_hash:
            raise BridgeDenied('PAYLOAD_HASH_MISMATCH')
        position_side = body['canonical_payload'].get('position_side')
        if ((position_side is not None and position_side != scope.venue_leg)
                or (scope.venue_leg != 'BOTH' and position_side is None)):
            raise BridgeDenied('POSITION_LEG_MISMATCH')
        now = offline.clock()
        if (not isinstance(now, t.ClockReading)
                or now.clock_health != 'HEALTHY' or type(now.monotonic_ns) is not int
                or now.monotonic_ns < 0):
            raise BridgeDenied('CLOCK_UNHEALTHY')
        for expiry, deadline in ((lease.expires_at, lease.deadline_monotonic_ns),
                                 (permit.expires_at, permit.deadline_monotonic_ns)):
            if (type(deadline) is not int or now.monotonic_ns >= deadline
                    or utc_timestamp(now.utc_now) >= utc_timestamp(expiry)):
                raise BridgeDenied('PERMIT_OR_LEASE_EXPIRED')
        if utc_timestamp(now.utc_now) < utc_timestamp(command.created_at):
            raise BridgeDenied('COMMAND_FROM_FUTURE')

    def _write(self, command, permit, operations):
        if not isinstance(command, ExecutionCommand) or not isinstance(permit, t.DurablePermit):
            raise BridgeDenied('DURABLE_PERMIT_REQUIRED')
        if command.operation not in operations:
            raise BridgeDenied('WRONG_ENGINE_METHOD')
        if self._offline is None:
            raise BridgeDenied('NATIVE_BINDING_NOT_QUALIFIED')
        offline = self._offline
        with self._lock, offline.authority_guard(command.command_id) as authority:
            self._validate(command, permit, authority)
            if command.command_id in self._consumed:
                raise BridgeDenied('COMMAND_ALREADY_OBSERVED_QUERY_SAME_ID')
            if command.venue_client_order_id in self._observed_clients:
                raise BridgeDenied('CLIENT_ID_ALREADY_OBSERVED_QUERY_SAME_ID')
            identity = offline.codec.decode(command.venue_client_order_id)
            role = offline.codec.role(command.venue_client_order_id)
            if role in ('ALGORITHM', 'CHILD'):
                raise BridgeDenied('NATIVE_CHILD_DISABLED_USE_CONTROLLED_COMMANDS')
            if (identity.logical_client_order_id != command.logical_client_order_id
                    or identity.venue_order_id != command.venue_order_id):
                raise BridgeDenied('REGISTERED_IDENTITY_MISMATCH')
            if command.operation in ('CANCEL', 'AMEND'):
                if not command.target_order_id:
                    raise BridgeDenied('OWNED_TARGET_REQUIRED')
                # Exact registry membership is necessary but not sufficient:
                # current strategy ownership must also be checked by the guard.
                offline.codec.decode_order(command.target_order_id)
            binding = next(b for b in offline.bindings if b.scope == offline.scope)
            # Command/clock/authority are rechecked before the observation point.
            self._validate(command, permit, authority)
            self._consumed.add(command.command_id)
            self._observed_clients.add(command.venue_client_order_id)
            self._writes.append(OfflineWrite(command=command, permit=permit,
                                             binding=binding, identity=identity))
        return t.CommandReceipt(receipt_id='ip11:offline:' + command.command_id,
            command_id=command.command_id, status=t.CommandState.UNKNOWN,
            reason='OFFLINE_OBSERVATION_ONLY_NO_VENUE_EVIDENCE', existing_result_ref=None)

    def query(self, query: t.ReconciliationCase) -> t.VenueReport:
        if self._offline is not None and query.scope != self._offline.scope:
            raise BridgeDenied('QUERY_SCOPE_MISMATCH')
        return t.VenueReport(coverage=t.Coverage(scope=query.scope, query_succeeded=False,
            pagination_complete=False, supported=False, missing=('NATIVE_QUERY_NOT_QUALIFIED',)),
            orders=(), positions=(), balances=(), fills=(), evidence=(), next_cursor=None,
            as_of=_clock().utc_now)
