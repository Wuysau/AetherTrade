"""IP-17 offline regressions; none of these qualify T52-T57 or Q07.

Synthetic DB/fence/quantity readers exercise real application gates and the
existing offline Bridge. Linux-only probes exercise flock on temporary files,
not native engine, credential-volume isolation, or real host failover.
"""
from dataclasses import replace
from decimal import Decimal
import json
import os
import subprocess
import sys

import pytest

from support_ip_17 import NOW, ROOT, runtime_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.runtime import emergency_journal as journal_module
from trading_system.runtime.backup import plan_restore, write_backup
from trading_system.runtime.emergency_journal import EmergencyJournal, JournalDenied
from trading_system.runtime.execution_owner import (
    EMERGENCY_CHECKS, LinuxShardLock, OwnerDenied, OwnerEvent, PriorityInbox,
)


def event(kind, key, received=100, deadline=1000):
    return OwnerEvent(kind=kind, key=key, received_monotonic_ns=received,
                      deadline_monotonic_ns=deadline)


def fail_storage(*args, **kwargs):
    raise OSError('injected offline storage failure')


def test_db_down_emergency_only(runtime_case, monkeypatch):
    case = runtime_case
    record = case.record()
    synced, order = set(), []
    original_sync = journal_module.os.fsync
    original_submit = case.engine.submit

    def sync(fd):
        original_sync(fd)
        metadata = os.fstat(fd)
        synced.add((metadata.st_dev, metadata.st_ino))
        order.append('fsync')

    def submit(command, permit):
        metadata = case.owner.journal.path.stat()
        assert (metadata.st_dev, metadata.st_ino) in synced
        assert not case.owner.journal.marker.exists()
        entry, = case.owner.journal.snapshot()
        assert entry.command == command
        assert command.state == 'MAY_HAVE_SENT'
        assert command.durable_record_ref == permit.durable_record_ref
        order.append('original-bridge')
        return original_submit(command, permit)

    monkeypatch.setattr(journal_module.os, 'fsync', sync)
    monkeypatch.setattr(case.engine, 'submit', submit)
    with case.owner.session(case.shard):
        receipt = case.owner.emergency(record)
        assert receipt.status == t.CommandState.UNKNOWN
        assert receipt.reason == 'OFFLINE_ATTEMPT_ONLY_EXIT_UNCONFIRMED'
        assert not case.owner.recovery(case.scope).ready
        with pytest.raises(OwnerDenied):
            case.owner.dispatch('new-risk', case.scope)
    write, = case.engine.offline_writes
    assert write.binding.client_id == 'offline-original-client'
    assert order.index('fsync') < order.index('original-bridge')
    assert case.owner.live_enabled is False
    assert case.engine.live_enabled is False


@pytest.mark.parametrize('operation', ['SUBMIT', 'AMEND'])
def test_new_risk_or_unapproved_operation_cannot_enter_journal(runtime_case, operation):
    case = runtime_case
    record = case.record(operation=operation)
    with case.owner.session(case.shard), pytest.raises(OwnerDenied):
        case.owner.emergency(record)
    assert not case.owner.journal.path.exists()
    assert case.engine.offline_writes == ()


@pytest.mark.parametrize('missing', sorted(EMERGENCY_CHECKS))
def test_every_emergency_attestation_is_required(runtime_case, missing):
    case = runtime_case
    record = case.record()
    case.grant(record, checks=EMERGENCY_CHECKS - {missing})
    with case.owner.session(case.shard), pytest.raises(OwnerDenied):
        case.owner.emergency(record)
    assert case.engine.offline_writes == ()
    assert not case.owner.journal.path.exists()


@pytest.mark.parametrize('payload', [
    {'side': 'BUY'}, {'quantity': '0.002'}, {'full_position_exit': True},
    {'order_type': 'MARKET'}, {'position_side': 'SHORT'},
])
def test_emergency_is_bounded_to_owned_quantity_and_policy(runtime_case, payload):
    case = runtime_case
    record = case.record(**payload)
    with case.owner.session(case.shard), pytest.raises(OwnerDenied):
        case.owner.emergency(record)
    assert case.engine.offline_writes == ()


def test_no_authority_or_active_owner_means_no_permit(runtime_case):
    case = runtime_case
    record = case.record()
    with pytest.raises(OwnerDenied):
        case.owner.emergency(record)
    with pytest.raises(JournalDenied):
        EmergencyJournal(case.directory / 'unauthorized.jsonl').append_and_fsync(record)
    case.facts.clear()
    with case.owner.session(case.shard), pytest.raises(OwnerDenied):
        case.owner.emergency(record)
    assert case.engine.offline_writes == ()


@pytest.mark.parametrize('database_state', [True, None])
def test_emergency_requires_explicit_database_outage(runtime_case, database_state):
    case = runtime_case
    record = case.record()
    case.db = database_state
    with case.owner.session(case.shard), pytest.raises(OwnerDenied, match='EXPLICIT_DB_OUTAGE'):
        case.owner.emergency(record)


def test_owned_cancel_uses_original_bridge(runtime_case):
    case = runtime_case
    record = case.record(operation='CANCEL')
    with case.owner.session(case.shard):
        assert case.owner.emergency(record).status == t.CommandState.UNKNOWN
    write, = case.engine.offline_writes
    assert write.command.operation == 'CANCEL'
    assert write.command.target_order_id == 'owned-venue-order'


@pytest.mark.parametrize('failure', ['fsync', 'append', 'backing'])
def test_all_storage_down(runtime_case, monkeypatch, failure):
    case = runtime_case
    record = case.record()
    if failure == 'fsync':
        monkeypatch.setattr(journal_module.os, 'fsync', fail_storage)
    elif failure == 'append':
        monkeypatch.setattr(journal_module.os, 'write', fail_storage)
    else:
        monkeypatch.setattr(case.engine, 'submit', fail_storage)
    original_engine = case.owner.engine
    with case.owner.session(case.shard):
        receipt = case.owner.emergency(record)
        assert receipt.status == t.CommandState.UNKNOWN
        assert receipt.reason == 'STORAGE_OR_BACKING_FAILURE_EXIT_UNCONFIRMED'
        assert 'EXIT_UNCONFIRMED_KEEP_NATIVE_PROTECTION' in case.owner.faults
        with pytest.raises(OwnerDenied, match='QUERY_ORIGINAL'):
            case.owner.emergency(record)
    assert case.owner.engine is original_engine
    assert case.engine.offline_writes == ()
    if failure != 'backing':
        with pytest.raises(JournalDenied):
            EmergencyJournal(case.owner.journal.path).snapshot()


def test_fsync_failure_after_append_never_issues_permit(runtime_case, monkeypatch):
    case = runtime_case
    record = case.record()
    original_sync = journal_module.os.fsync

    def fail_journal_sync(fd):
        if case.owner.journal.path.exists():
            opened, journal = os.fstat(fd), case.owner.journal.path.stat()
            if (opened.st_dev, opened.st_ino) == (journal.st_dev, journal.st_ino):
                raise OSError('journal fsync uncertain after append')
        original_sync(fd)

    monkeypatch.setattr(journal_module.os, 'fsync', fail_journal_sync)
    with case.owner.session(case.shard):
        assert case.owner.emergency(record).status == t.CommandState.UNKNOWN
    assert case.owner.journal.path.stat().st_size > 0
    assert case.owner.journal.marker.exists()
    assert case.engine.offline_writes == ()
    assert case.make_owner().run(case.shard).exit_code != 0


def test_partial_writes_are_completed_before_bridge(runtime_case, monkeypatch):
    case = runtime_case
    record = case.record()
    original_write = journal_module.os.write
    calls = []

    def partial(fd, data):
        calls.append(len(data))
        return original_write(fd, data[:31])

    monkeypatch.setattr(journal_module.os, 'write', partial)
    with case.owner.session(case.shard):
        case.owner.emergency(record)
    assert len(calls) > 1
    assert len(case.engine.offline_writes) == 1
    assert len(case.owner.journal.snapshot()) == 1


@pytest.mark.parametrize('change', ['revocation', 'quantity', 'expiry'])
def test_authority_rechecked_after_fsync(runtime_case, monkeypatch, change):
    case = runtime_case
    record = case.record()
    append = case.owner.journal.append_and_fsync

    def changed(record):
        permit = append(record)
        facts = case.facts[record.command.command_id]
        if change == 'revocation':
            case.facts.clear()
        elif change == 'quantity':
            case.facts[record.command.command_id] = replace(facts,
                context=replace(facts.context, reducible_quantity=t.Quantity(value=Decimal('0'), unit='BTC')))
        else:
            case.now = replace(case.now, monotonic_ns=31_000_000_000, utc_now='2026-10-23T00:00:31Z')
        return permit

    monkeypatch.setattr(case.owner.journal, 'append_and_fsync', changed)
    with case.owner.session(case.shard):
        assert case.owner.emergency(record).status == t.CommandState.UNKNOWN
    assert len(case.owner.journal.snapshot()) == 1
    assert case.engine.offline_writes == ()


def test_crash_restart_queries_original_never_replays_permit(runtime_case):
    case = runtime_case
    record = case.record()
    with case.owner.session(case.shard):
        case.owner.emergency(record)
    old_write, = case.engine.offline_writes
    case.owner = case.make_owner()
    with case.owner.session(case.shard):
        with pytest.raises(OwnerDenied, match='QUERY_ORIGINAL'):
            case.owner.emergency(record)
        with pytest.raises(OwnerDenied):
            case.engine.submit(old_write.command, old_write.permit)
    assert len(case.engine.offline_writes) == 1
    result = case.owner.run(case.shard)
    assert result.exit_code != 0
    assert result.unresolved[0].command_ids == (record.command.command_id,)


@pytest.mark.parametrize('damage', ['torn', 'hash', 'writer'])
def test_corrupt_journal_is_not_repaired_or_truncated(runtime_case, damage):
    case = runtime_case
    record = case.record()
    with case.owner.session(case.shard):
        case.owner.emergency(record)
    path = case.owner.journal.path
    original = path.read_bytes()
    if damage == 'torn':
        path.write_bytes(original[:-1])
    elif damage == 'hash':
        frame = json.loads(original)
        frame['hash'] = 'f' * 64
        path.write_bytes((json.dumps(frame) + '\n').encode())
    else:
        case.owner.journal.marker.write_bytes(b'')
    damaged = path.read_bytes()
    with pytest.raises(JournalDenied):
        EmergencyJournal(path).snapshot()
    assert case.make_owner().run(case.shard).exit_code != 0
    assert path.read_bytes() == damaged


def test_merge_and_platform_evidence_required_before_recovery(runtime_case):
    case = runtime_case
    record = case.record()
    with case.owner.session(case.shard):
        case.owner.emergency(record)
        case.db = True
        assert not case.owner.recovery(case.scope).ready
        case.merge_journal()
        case.merge = replace(case.merge, platform_reconciled=False)
        assert not case.owner.recovery(case.scope).ready
        case.merge = replace(case.merge, platform_reconciled=True)
        assert case.owner.recovery(case.scope).ready
    assert len(case.engine.offline_writes) == 1  # recovery issues no write


@pytest.mark.parametrize('field', ['journal_head', 'execution_epoch', 'command_ids', 'scope'])
def test_merge_is_bound_to_exact_journal_scope_and_epoch(runtime_case, field):
    case = runtime_case
    record = case.record()
    with case.owner.session(case.shard):
        case.owner.emergency(record)
        case.db = True
        case.merge_journal()
        values = {'journal_head': 'f' * 64, 'execution_epoch': 6,
                  'command_ids': (), 'scope': replace(case.scope, instrument_uid='foreign')}
        case.merge = replace(case.merge, **{field: values[field]})
        assert not case.owner.recovery(case.scope).ready


@pytest.mark.parametrize('kind', [
    'unique_process', 'commands', 'funds', 'private_stream', 'positions', 'protection', 'authorization',
])
def test_every_recovery_obligation_remains_required(runtime_case, kind):
    case = runtime_case
    case.db = True
    case.checks.pop(kind)
    with case.owner.session(case.shard):
        verdict = case.owner.recovery(case.scope)
    assert not verdict.ready
    assert kind.upper() + '_EVIDENCE_REQUIRED' in verdict.missing


def test_unjournaled_unknown_cannot_hide_behind_older_merged_entries(runtime_case, monkeypatch):
    case = runtime_case
    first = case.record()
    with case.owner.session(case.shard):
        case.owner.emergency(first)
        case.db = True
        case.merge_journal()
        assert case.owner.recovery(case.scope).ready
        second = case.record('two')
        case.db = False
        monkeypatch.setattr(case.owner.journal, 'append_and_fsync', fail_storage)
        case.owner.emergency(second)
        case.db = True
        assert not case.owner.recovery(case.scope).ready
    assert len(case.engine.offline_writes) == 1


def test_database_change_during_recovery_denies_readiness(runtime_case, monkeypatch):
    case = runtime_case
    case.db = True
    evaluate = case.gate.evaluate

    def disconnect(checklist):
        verdict = evaluate(checklist)
        case.db = False
        return verdict

    monkeypatch.setattr(case.gate, 'evaluate', disconnect)
    with case.owner.session(case.shard):
        assert not case.owner.recovery(case.scope).ready


def test_old_host_not_fenced(runtime_case):
    case = runtime_case
    supervisor, target, proof = case.takeover_case(isolated=False, expired=True)
    verdict = supervisor.takeover(target, proof)
    assert verdict.allowed is False
    assert 'EXTERNAL_ISOLATION_UNVERIFIED_LEASE_IS_NOT_FENCE' in verdict.missing
    assert case.engine.offline_writes == ()


def test_fence_then_recovery_is_eligibility_only(runtime_case):
    case = runtime_case
    supervisor, target, proof = case.takeover_case(isolated=True)
    verdict = supervisor.takeover(target, proof)
    assert verdict.allowed
    assert verdict.fence_proof == proof
    assert case.engine.offline_writes == ()
    case.checks.pop('private_stream')
    assert not supervisor.takeover(target, proof).allowed


@pytest.mark.parametrize('mutation', ['instance', 'epoch', 'environment', 'stale', 'reader'])
def test_takeover_rejects_unbound_or_stale_fence(runtime_case, mutation):
    case = runtime_case
    supervisor, target, proof = case.takeover_case(isolated=True)
    if mutation == 'instance':
        proof = replace(proof, old_instance_id='different-instance')
    elif mutation == 'epoch':
        target = replace(target, execution_epoch=case.shard.execution_epoch)
    elif mutation == 'environment':
        target = replace(target, environment=t.Environment.LIVE)
    elif mutation == 'stale':
        proof = replace(proof, evidence=replace(proof.evidence, visible_at='2026-10-22T00:00:00Z'))
    else:
        supervisor.read_fence = None
    assert not supervisor.takeover(target, proof).allowed
    assert case.engine.offline_writes == ()


def test_fence_rechecked_after_recovery(runtime_case):
    case = runtime_case
    supervisor, target, proof = case.takeover_case(isolated=True)
    original = supervisor.read_fence(proof, target)

    def recovery(shard):
        supervisor.read_fence = lambda p, s: replace(original, external_write_isolated=False)
        return case.gate.evaluate(case.checklist)

    supervisor.read_recovery = recovery
    verdict = supervisor.takeover(target, proof)
    assert not verdict.allowed
    assert 'FENCE_CHANGED_DURING_RECOVERY' in verdict.missing


def test_control_restart_independent(runtime_case):
    case = runtime_case
    case.inbox.put(event('CONTROL_DISCONNECTED', 'web-killed'))
    case.inbox.put(event('PROTECTION', 'position-protection'))
    case.inbox.put(event('CONTROL_DISCONNECTED', 'tui-closed'))
    case.inbox.put(event('ORDER', 'fill'))
    result = case.owner.run(case.shard)
    assert result.exit_code == 0
    assert [item.key for item in case.handled] == ['position-protection', 'fill']
    assert case.owner.last_reason == 'OFFLINE_DRAIN_COMPLETE_NOT_NATIVE_READINESS'
    assert result.evidence == ()  # not a real process-kill/UI-restoration test


def test_clock_jump_and_backlog(runtime_case):
    case = runtime_case
    case.inbox.put(event('RESEARCH', 'quote', received=90))
    case.inbox.put(event('RESEARCH', 'quote', received=100))
    case.inbox.put(event('RESEARCH', 'expired', received=1, deadline=99))
    case.inbox.put(event('ORDER', 'old-fill', received=1, deadline=2))
    case.inbox.put(event('PROTECTION', 'safety'))
    assert case.owner.run(case.shard).exit_code == 0
    assert [item.key for item in case.handled] == ['old-fill', 'safety', 'quote']
    assert case.handled[-1].received_monotonic_ns == 100
    case.inbox.put(event('RESEARCH', 'new-research'))
    case.inbox.put(event('PROTECTION', 'still-process-safety'))
    case.now = replace(case.now, utc_now='2026-10-22T23:00:00Z', monotonic_ns=101)
    assert case.owner.run(case.shard).exit_code != 0
    assert case.handled[-1].key == 'still-process-safety'
    assert 'CLOCK_UNHEALTHY' in case.owner.faults


@pytest.mark.parametrize('utc,monotonic', [
    ('2026-10-23T00:00:31Z', 31_000_000_000),
    ('2026-10-23T01:00:00Z', 101),
    ('2026-10-22T23:00:00Z', 101),
    (NOW, 99),
])
def test_clock_changes_cannot_extend_authority(runtime_case, utc, monotonic):
    case = runtime_case
    record = case.record()
    with case.owner.session(case.shard):
        with case.owner.guard(case.lease):
            pass
        case.now = replace(case.now, utc_now=utc, monotonic_ns=monotonic)
        with pytest.raises(OwnerDenied):
            case.owner.emergency(record)
    assert case.engine.offline_writes == ()


def test_backlog_latches_recovery_and_retains_accepted_safety_events(runtime_case):
    case = runtime_case
    case.db = True
    case.owner.events = inbox = PriorityInbox(research_capacity=2, safety_capacity=1)
    inbox.put(event('ORDER', 'retained'))
    with pytest.raises(OwnerDenied, match='SAFETY_BACKLOG'):
        inbox.put(event('ORDER', 'producer-must-retain'))
    with case.owner.session(case.shard):
        assert not case.owner.recovery(case.scope).ready
    assert case.owner.run(case.shard).exit_code != 0
    assert [item.key for item in case.handled] == ['retained']
    assert inbox.overloaded


def test_research_capacity_coalesces_and_discards_old_keys(runtime_case):
    inbox = PriorityInbox(research_capacity=2)
    for key in ('a', 'b', 'c'):
        inbox.put(event('RESEARCH', key))
    inbox.put(event('RESEARCH', 'b', received=90))
    assert [inbox.pop(runtime_case.now).key for _ in range(2)] == ['b', 'c']
    assert inbox.pop(runtime_case.now) is None


def test_exclusive_session_and_wrong_shard_are_rejected(runtime_case):
    case = runtime_case
    other = case.make_owner()
    with case.owner.session(case.shard):
        with pytest.raises(OwnerDenied):
            with case.owner.session(case.shard):
                pass
        assert other.run(case.shard).exit_code != 0
    with pytest.raises(OwnerDenied):
        with case.owner.session(replace(case.shard, execution_epoch=8)):
            pass


def test_linux_lock_rejects_other_platforms(runtime_case, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    with pytest.raises(OwnerDenied, match='FIXED_LINUX'):
        with LinuxShardLock(runtime_case.directory, expected_uid=1000).claim(runtime_case.shard):
            pass


@pytest.mark.skipif(sys.platform != 'linux' or not hasattr(os, 'getuid') or os.getuid() == 0,
                    reason='requires isolated Linux non-root runner; not native qualification')
def test_linux_flock_excludes_second_process_and_releases_on_close(runtime_case):
    case = runtime_case
    case.directory.chmod(0o700)
    lock = LinuxShardLock(case.directory, expected_uid=os.getuid())
    # Only a temporary lock directory is accessed. No credential or native client.
    script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[2]) / 'src'))
from trading_system.contracts import types as t
from trading_system.runtime.execution_owner import LinuxShardLock
shard = t.ExecutionShard(environment=t.Environment.PAPER, venue_account_id='offline-ip17',
    product=t.Product.SPOT, node_id='another-node', execution_epoch=999)
try:
    with LinuxShardLock(Path(sys.argv[1]), expected_uid=os.getuid()).claim(shard):
        pass
except BlockingIOError:
    sys.exit(3)
'''
    args = [sys.executable, '-c', script, str(case.directory), str(ROOT)]
    with lock.claim(case.shard):
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        assert result.returncode == 3, result.stderr
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_backup_restore_preserves_safety_state_but_never_resumes(runtime_case):
    case = runtime_case
    record = case.record()
    case.locks = (t.RiskLock(lock_id='offline-lock', scope=case.scope, cause='UNKNOWN_COMMAND',
        state='ACTIVE', permission=t.RiskPermission.RECOVERY_REQUIRED,
        required_evidence=('PLATFORM_QUERY',), raised_at=NOW),)
    with case.owner.session(case.shard):
        case.owner.emergency(record)
    state = case.backup_state()
    path = case.directory / 'backup.json'
    receipt = write_backup(path, state)
    plan = plan_restore(path, expected_hash=receipt.payload_hash,
                        target=replace(case.shard, execution_epoch=8, node_id='new-host'))
    assert json.loads(plan.snapshot_json) == domain_payload(state)
    assert plan.recovery_required
    assert plan.flush_on_start is False
    assert plan.resume_task_ids == ()
    assert plan.sendable_permits == ()
    assert 'EXTERNAL_FENCE_REQUIRED' in plan.missing
    assert len(case.engine.offline_writes) == 1
    assert not any('secret' in key.lower() for key in json.loads(plan.snapshot_json))
    with pytest.raises(FileExistsError):
        write_backup(path, state)


@pytest.mark.parametrize('change', ['hash', 'epoch', 'scope', 'live', 'bytes'])
def test_restore_rejects_corruption_and_wrong_identity(runtime_case, change):
    case = runtime_case
    path = case.directory / 'backup.json'
    receipt = write_backup(path, case.backup_state())
    target = replace(case.shard, execution_epoch=8)
    digest = receipt.payload_hash
    if change == 'hash':
        digest = 'f' * 64
    elif change == 'epoch':
        target = case.shard
    elif change == 'scope':
        target = replace(target, venue_account_id='foreign')
    elif change == 'live':
        target = replace(target, environment=t.Environment.LIVE)
    else:
        envelope = json.loads(path.read_text('utf-8'))
        envelope['payload']['risk_locks'] = ['tampered']
        path.write_text(json.dumps(envelope), encoding='utf-8')
    with pytest.raises(ValueError):
        plan_restore(path, expected_hash=digest, target=target)


def test_backup_rejects_missing_journal_inventory_and_foreign_authorization(runtime_case):
    case = runtime_case
    record = case.record()
    with case.owner.session(case.shard):
        case.owner.emergency(record)
    state = case.backup_state()
    with pytest.raises(ValueError, match='inventory'):
        write_backup(case.directory / 'missing.json', replace(state, commands=()))
    foreign = replace(state.authorizations[0], scopes=(replace(case.scope, venue_account_id='foreign'),))
    with pytest.raises(ValueError, match='foreign authorization'):
        write_backup(case.directory / 'foreign.json', replace(state, authorizations=(foreign,)))


def test_backup_storage_failure_returns_no_receipt(runtime_case, monkeypatch):
    monkeypatch.setattr(journal_module.os, 'fsync', fail_storage)
    with pytest.raises(OSError):
        write_backup(runtime_case.directory / 'uncertain.json', runtime_case.backup_state())
