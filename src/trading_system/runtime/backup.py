"""Offline backup envelope and quarantined restore plan, not a DB installer.

The exporter supplies a coherent snapshot under its storage boundary. Kernel
cache and research objects are immutable artifact references, not guessed API
calls. Credentials are excluded; recovery instructions are references only.
Restoring bytes cannot restore process authority or a sendable monotonic permit.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, domain_payload, utc_timestamp
from trading_system.contracts.wire import ContractCodec, ExecutionCommand
from .emergency_journal import BINARY, GENESIS, regular_file, sync_directory
from .execution_owner import shard_key


@dataclass(frozen=True, kw_only=True)
class BackupState:
    shard: t.ExecutionShard
    lease: t.OwnerLease
    created_at: str
    authorizations: tuple[t.Authorization, ...]
    risk_locks: tuple[t.RiskLock, ...]
    commands: tuple[ExecutionCommand, ...]
    positions: tuple[t.ManagedPosition, ...]
    reservations: tuple[t.ReservationProjection, ...]
    protection_plans: tuple[t.ProtectionPlan, ...]
    unresolved_cases: tuple[t.ReconciliationCase, ...]
    journal_head: str
    journal_records: tuple[str, ...]
    kernel_cache: t.EvidenceRef
    credential_recovery_instructions: t.EvidenceRef
    research_snapshot_index: t.EvidenceRef
    paused_task_ids: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class BackupReceipt:
    path: str
    payload_hash: str


@dataclass(frozen=True, kw_only=True)
class RestorePlan:
    snapshot_json: str
    source_hash: str
    target: t.ExecutionShard
    recovery_required: bool = True
    flush_on_start: bool = False
    resume_task_ids: tuple[str, ...] = ()
    sendable_permits: tuple[t.DurablePermit, ...] = ()
    missing: tuple[str, ...] = ('REVOKE_OLD_RUNNING_AUTHORITY', 'EXTERNAL_FENCE_REQUIRED',
        'MERGE_EMERGENCY_JOURNAL', 'RECONCILE_ORIGINAL_COMMAND_IDS', 'CURRENT_RECOVERY_GATE',
        'KERNEL_CACHE_AND_SNAPSHOT_ARTIFACTS_UNVERIFIED')


def write_backup(path: Path, state: BackupState) -> BackupReceipt:
    if (state.shard.environment not in (t.Environment.PAPER, t.Environment.RESEARCH)
            or state.lease.shard != state.shard or not state.lease.instance_id
            or type(state.shard.execution_epoch) is not int or state.shard.execution_epoch < 1):
        raise ValueError('coherent offline shard snapshot required')
    utc_timestamp(state.created_at)
    for ref in (state.kernel_cache, state.credential_recovery_instructions, state.research_snapshot_index):
        if (ref.environment != state.shard.environment or not ref.id or not ref.source
                or len(ref.hash) != 64 or any(c not in '0123456789abcdef' for c in ref.hash)):
            raise ValueError('bound immutable artifact reference required')
    if len(state.journal_head) != 64 or any(c not in '0123456789abcdef' for c in state.journal_head):
        raise ValueError('journal head required')
    commands = {}
    for command in state.commands:
        if (command.environment, command.venue_account_id, command.product) != shard_key(state.shard):
            raise ValueError('foreign command in snapshot')
        if (command.command_id in commands
                or canonical_hash(ContractCodec().encode(command)['canonical_payload']) != command.payload_hash):
            raise ValueError('duplicate or invalid command in snapshot')
        commands[command.command_id] = command
    for authorization in state.authorizations:
        if (authorization.environment != state.shard.environment or not authorization.scopes
                or any((s.environment, s.venue_account_id, s.product) != shard_key(state.shard)
                       for s in authorization.scopes)):
            raise ValueError('foreign authorization in snapshot')
    for projection in state.reservations:
        reservation = projection.reservation
        if (reservation.environment != state.shard.environment
                or reservation.venue_account_id != state.shard.venue_account_id):
            raise ValueError('foreign reservation in snapshot')
    for record in (*state.positions, *state.risk_locks, *state.unresolved_cases, *state.protection_plans):
        scope = record.scope
        if (scope.environment, scope.venue_account_id, scope.product) != shard_key(state.shard):
            raise ValueError('foreign scope in snapshot')
    previous, journal_ids = GENESIS, set()
    for sequence, record_json in enumerate(state.journal_records, 1):
        record = json.loads(record_json)
        command = ContractCodec().decode('execution-command', record['command'])
        if (record['prior_record_hash'] != previous or command.state != 'MAY_HAVE_SENT'
                or command.durable_record_ref != 'emergency:' + record['record_id']
                or command.command_id in journal_ids or command.command_id not in commands):
            raise ValueError('journal chain or command inventory mismatch')
        # A reconciled ledger command may have advanced its outcome, but its
        # original identity, payload, epochs and durable reference must survive.
        original = ContractCodec().encode(command)
        current = ContractCodec().encode(commands[command.command_id])
        for key in ('state', 'outcome_provenance'):
            original.pop(key)
            current.pop(key)
        if original != current:
            raise ValueError('journal command binding mismatch')
        journal_ids.add(command.command_id)
        previous = canonical_hash({'sequence': sequence, 'record': record})
    if previous != state.journal_head:
        raise ValueError('journal head mismatch')
    payload = domain_payload(state)
    digest = canonical_hash(payload)
    envelope = canonical_json({'version': 1, 'payload': payload, 'hash': digest}).encode('utf-8')
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | BINARY, 0o600)
    try:
        regular_file(fd)
        data = memoryview(envelope)
        while data:
            written = os.write(fd, data)
            if written <= 0:
                raise OSError('short backup write')
            data = data[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(path.parent)
    return BackupReceipt(path=str(path), payload_hash=digest)


def plan_restore(path: Path, *, expected_hash: str, target: t.ExecutionShard) -> RestorePlan:
    """expected_hash comes from the trusted backup inventory, not this envelope."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('backup symlink rejected')
    fd = os.open(path, os.O_RDONLY | BINARY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        regular_file(fd)
        with os.fdopen(fd, 'r', encoding='utf-8', closefd=False) as stream:
            envelope = json.load(stream)
    finally:
        os.close(fd)
    if (envelope.keys() != {'version', 'payload', 'hash'} or envelope['version'] != 1
            or canonical_hash(envelope['payload']) != expected_hash or envelope['hash'] != expected_hash):
        raise ValueError('backup fingerprint mismatch')
    payload = envelope['payload']
    if payload.keys() != BackupState.__dataclass_fields__.keys():
        raise ValueError('incomplete snapshot')
    source = payload['shard']
    if (target.environment not in (t.Environment.PAPER, t.Environment.RESEARCH)
            or (source['environment'], source['venue_account_id'], source['product']) != shard_key(target)
            or type(target.execution_epoch) is not int or type(source['execution_epoch']) is not int
            or target.execution_epoch <= source['execution_epoch'] or not target.node_id):
        raise ValueError('restore requires same scope and a newer execution epoch')
    return RestorePlan(snapshot_json=canonical_json(payload), source_hash=expected_hash, target=target)
