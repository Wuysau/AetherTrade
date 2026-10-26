"""IP-32 regressions. Opt-in probes are separate from T/Q qualification."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.observability.metrics import (
    DeploymentVerifier, HealthPublisher, LatencyMetrics, REFERENCE_LOAD, SERVICE_POLICY,
    TARGETS, deployment_faults,
)
from trading_system.observability.notifier import Notifier
from trading_system.recovery.gate import RecoveryGate
from trading_system.runtime.backup import plan_restore, write_backup
from trading_system.runtime.supervisor import FenceObservation, Supervisor
from trading_system.runtime.execution_owner import LinuxShardLock
from ops.support_ip_32 import (
    NOW, LATER, ROOT, SCOPE, ContainerCase, alert, backup_state, deployment_case,
    dependency_lock, manifest_inputs, pinned_image, ref, rendered_config,
)


def health(state=t.ProcessState.READY, observed_at=NOW, faults=()):
    return t.ProcessHealth(process_id='owner', state=state, observed_at=observed_at, faults=faults)


def test_health_never_clears_durable_permission():
    for permission in t.RiskPermission:
        result = HealthPublisher(('owner',), read_process=lambda _: health(),
            read_permission=lambda: permission, clock=lambda: NOW).snapshot()
        assert result.risk_permission == permission
        assert not result.missing


@pytest.mark.parametrize('observation,missing', [
    (None, 'PROCESS_UNAVAILABLE:owner'),
    (health(observed_at='2026-10-22T23:59:58Z'), 'STALE_PROCESS:owner'),
    (health(observed_at=LATER), 'STALE_PROCESS:owner'),
    (health(state=t.ProcessState.RECONCILING), 'UNHEALTHY_PROCESS:owner'),
    (health(faults=('PRIVATE_STREAM_UNAVAILABLE',)), 'UNHEALTHY_PROCESS:owner'),
    (replace(health(), process_id='another'), 'PROCESS_UNAVAILABLE:owner'),
])
def test_health_fails_closed(observation, missing):
    result = HealthPublisher(('owner',), read_process=lambda _: observation,
        read_permission=lambda: t.RiskPermission.NORMAL, clock=lambda: NOW).snapshot()
    assert missing in result.missing
    assert result.risk_permission == t.RiskPermission.RECOVERY_REQUIRED


def test_health_reader_and_clock_failure():
    def broken(*args):
        raise OSError('sensitive exception must not be published')
    result = HealthPublisher(('owner', 'notifier'), read_process=broken,
                             read_permission=broken, clock=lambda: NOW).snapshot()
    assert result.missing == ('PROCESS_UNAVAILABLE:owner', 'PROCESS_UNAVAILABLE:notifier',
                              'RISK_PERMISSION_UNAVAILABLE')
    assert HealthPublisher(('owner',), read_process=broken, clock=broken).snapshot().missing == ('CLOCK_UNAVAILABLE',)


def test_notification_delivery_deduplicates_each_cause_and_scope(tmp_path):
    sent = []
    send = lambda notice: sent.append(notice) or True
    path = tmp_path / 'notifications.sqlite'
    notifier = Notifier(path, send=send, clock=lambda: NOW)
    assert notifier.publish(alert()).status == 'DELIVERED'
    assert notifier.publish(alert(identity='same-cause-new-event')).status == 'DELIVERED'
    assert len(sent) == 1
    assert notifier.publish(alert('PROTECTION_MISSING', 'protection')).status == 'DELIVERED'
    foreign = replace(SCOPE, instrument_uid='synthetic:ETHUSDT')
    assert notifier.publish(alert(identity='other-scope', scope=foreign)).status == 'DELIVERED'
    assert len(sent) == 3 and len({n.dedup_key for n in sent}) == 3
    # Restart retains dedup and history; no in-memory success default.
    restarted = Notifier(path, send=send, clock=lambda: NOW)
    assert restarted.publish(alert()).status == 'DELIVERED'
    assert len(sent) == 3 and len(restarted.history()) == 3
    locks = backup_state().risk_locks
    assert all(lock.state == 'ACTIVE' for lock in locks)


def test_notification_failure_is_retryable_and_no_transport_is_not_delivery(tmp_path):
    path = tmp_path / 'spool'
    notifier = Notifier(path, clock=lambda: NOW)
    assert notifier.publish(alert()).status == 'FAILED'
    notifier.send = lambda _: True
    assert notifier.publish(alert()).status == 'DELIVERED'
    assert [r[2] for r in notifier.history()] == ['FAILED', 'DELIVERED']


def test_notifier_periodically_repeats_unresolved_causes(tmp_path):
    sent, now = [], [NOW]
    notifier = Notifier(tmp_path / 'spool', send=lambda n: sent.append(n) or True, clock=lambda: now[0])
    notifier.publish(alert())
    now[0] = LATER
    notifier.publish(alert())
    assert len(sent) == 2
    assert sent[0].delivery_key != sent[1].delivery_key
    assert sent[1].occurrences == 2


def test_notifier_parallel_publish_reserves_one_attempt(tmp_path):
    path, sent = tmp_path / 'spool', []
    notifiers = [Notifier(path, send=lambda n: sent.append(n) or True, clock=lambda: NOW) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda n: n.publish(alert()), notifiers))
    assert len(sent) == 1
    assert all(r.status in {'DELIVERED', 'QUEUED'} for r in results)
    assert len(notifiers[0].history()) == 1


def test_notifier_crash_pending_attempt_retries_after_expiry(tmp_path):
    path, now, sent = tmp_path / 'spool', [NOW], []
    notifier = Notifier(path, send=lambda n: sent.append(n) or True, clock=lambda: now[0])
    notifier.publish(alert())
    # Inject a crash window after durable reservation, before delivery result.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE notices SET status='QUEUED',delivered_at=NULL")
        db.execute("UPDATE attempts SET status='QUEUED'")
    assert notifier.publish(alert()).status == 'QUEUED'
    assert len(sent) == 1
    now[0] = LATER
    assert notifier.publish(alert()).status == 'DELIVERED'
    assert len(sent) == 2
    assert [r[2] for r in notifier.history()] == ['QUEUED', 'DELIVERED']


def test_notifier_redacts_content_and_rejects_identity_reuse(tmp_path):
    sent, path = [], tmp_path / 'spool'
    notifier = Notifier(path, send=lambda n: sent.append(n) or True, clock=lambda: NOW)
    sensitive = replace(alert(), cause='UNTRUSTED-CONTENT-SECRET',
                         evidence=(replace(ref('failure'), id='PRIVATE-EVIDENCE-SECRET'),))
    assert notifier.publish(sensitive).status == 'DELIVERED'
    assert sent[0].cause == 'UNCLASSIFIED_RISK'
    assert notifier.publish(alert()).status == 'FAILED'
    assert len(sent) == 1
    text = repr(sent) + repr(notifier.history())
    for value in ('UNTRUSTED-CONTENT-SECRET', 'PRIVATE-EVIDENCE-SECRET', SCOPE.venue_account_id):
        assert value not in text and value.encode() not in path.read_bytes()


def test_notifier_storage_clock_and_sender_failures_are_visible(tmp_path):
    sent = []
    path = tmp_path / 'spool'
    notifier = Notifier(path, send=lambda n: sent.append(n) or True, clock=lambda: NOW)
    path.write_bytes(b'invalid database')
    assert notifier.publish(alert()).status == 'FAILED'
    assert sent == []
    def broken(_):
        raise RuntimeError('SECRET')
    notifier = Notifier(tmp_path / 'other', send=broken, clock=lambda: NOW)
    assert notifier.publish(alert()).status == 'FAILED'
    notifier.send = lambda _: True
    notifier.clock = lambda: 'invalid'
    assert notifier.publish(alert()).status == 'FAILED'


@pytest.mark.parametrize('environment', [t.Environment.LIVE, t.Environment.TESTNET])
def test_notifier_never_uses_real_environments(tmp_path, environment):
    sent = []
    notifier = Notifier(tmp_path / 'spool', send=lambda n: sent.append(n) or True, clock=lambda: NOW)
    assert notifier.publish(alert(scope=replace(SCOPE, environment=environment))).status == 'FAILED'
    assert not sent


def test_deployment_preflight_never_claims_native_pass():
    manifest, artifact, config = manifest_inputs()
    result = DeploymentVerifier(read_artifact=lambda _: artifact, read_config=lambda _: config,
                                read_dependency_lock=dependency_lock).check(manifest)
    assert deployment_faults(json.loads(config)) == ()
    assert result.status == 'NOT_RUN' and result.evidence == ()
    assert 'NATIVE_ISOLATION_BACKING_RESTORE_AND_LOAD_NOT_RUN' in result.faults


@pytest.mark.parametrize('mutation', ['live', 'host-network', 'host-volume', 'socket', 'privileged',
    'root', 'reload', 'replicas', 'ports', 'floating', 'shared-network', 'writable', 'extra-service'])
def test_deployment_rejects_unsafe_overrides(mutation):
    config = rendered_config()
    service = config['services']['decision-worker']
    if mutation == 'live': config['x-ip32']['live_enabled'] = True
    elif mutation == 'host-network': service['network_mode'] = 'host'
    elif mutation == 'host-volume': service['volumes'] = ['/home:/host']
    elif mutation == 'socket': service['volumes'] = ['/var/run/docker.sock:/var/run/docker.sock']
    elif mutation == 'privileged': service['privileged'] = True
    elif mutation == 'root': service['user'] = '0:0'
    elif mutation == 'reload': service['command'] = ['uvicorn', '--reload']
    elif mutation == 'replicas': config['services']['execution-owner']['deploy'] = {'replicas': 2}
    elif mutation == 'ports': service['ports'] = ['8765:8765']
    elif mutation == 'floating': service['image'] = 'python:latest'
    elif mutation == 'shared-network': service['networks'].append('owner')
    elif mutation == 'writable': service['read_only'] = False
    elif mutation == 'extra-service': config['services']['owner-copy'] = config['services']['execution-owner']
    manifest, artifact, body = manifest_inputs(config)
    result = DeploymentVerifier(read_artifact=lambda _: artifact, read_config=lambda _: body,
                                read_dependency_lock=dependency_lock).check(manifest)
    assert result.status == 'BLOCKED'
    assert any(f.startswith(('OFFLINE_POLICY_REQUIRED', 'UNSUPPORTED_SERVICE_FIELDS',
        'ISOLATION_POLICY_MISMATCH', 'PINNED_IMAGE_REQUIRED', 'EXACT_PROCESS_SET_REQUIRED')) for f in result.faults)


def test_deployment_rejects_missing_unpinned_or_mismatched_inputs():
    manifest, artifact, config = manifest_inputs()
    verifier = DeploymentVerifier(read_artifact=lambda _: artifact, read_config=lambda _: config)
    assert verifier.check(replace(manifest, environment=t.Environment.LIVE)).status == 'BLOCKED'
    assert verifier.check(replace(manifest, artifact_digest='b' * 64)).faults == ('CONTENT_DIGEST_MISMATCH',)
    assert verifier.check(replace(manifest, processes=('execution-owner',))).status == 'BLOCKED'
    raw = json.loads((ROOT / 'deploy/compose.yaml').read_text('utf-8'))
    assert any(f.startswith('PINNED_IMAGE_REQUIRED') for f in deployment_faults(raw))
    def unavailable(_): raise OSError('unavailable')
    assert DeploymentVerifier(read_artifact=unavailable, read_config=unavailable).check(manifest).status == 'BLOCKED'
    assert 'DEPENDENCY_LOCK_UNAVAILABLE' in verifier.check(manifest).faults
    wrong_lock = DeploymentVerifier(read_artifact=lambda _: artifact, read_config=lambda _: config,
                                    read_dependency_lock=lambda: b'tampered')
    assert 'DEPENDENCY_LOCK_DIGEST_MISMATCH' in wrong_lock.check(manifest).faults


@pytest.mark.parametrize('mutation', ['read-only-number', 'network-number', 'live-number',
    'reload-number', 'float-pids', 'null-volume', 'false-volume', 'nocopy-number'])
def test_deployment_profile_preserves_json_types(mutation):
    config = rendered_config()
    service = config['services']['execution-owner']
    if mutation == 'read-only-number': service['read_only'] = 1
    elif mutation == 'network-number': config['networks']['owner']['internal'] = 1
    elif mutation == 'live-number': config['x-ip32']['live_enabled'] = 0
    elif mutation == 'reload-number': config['x-ip32']['hot_reload'] = 0
    elif mutation == 'float-pids': service['pids_limit'] = 64.0
    elif mutation == 'null-volume': config['volumes']['owner-state'] = None
    elif mutation == 'false-volume': config['volumes']['owner-state'] = False
    else: service['volumes'][0]['volume']['nocopy'] = 1
    manifest, artifact, body = manifest_inputs(config)
    result = DeploymentVerifier(read_artifact=lambda _: artifact, read_config=lambda _: body,
                                read_dependency_lock=dependency_lock).check(manifest)
    assert result.status == 'BLOCKED'


@pytest.mark.parametrize('target', ['config', 'inventory'])
def test_deployment_rejects_ambiguous_json_even_with_matching_digest(target):
    manifest, artifact, config = manifest_inputs()
    if target == 'config':
        # The last duplicate is safe-looking; a last-key-wins parser would pass.
        config = config.replace(b'"live_enabled": false', b'"live_enabled": true, "live_enabled": false')
        manifest = replace(manifest, config_digest=hashlib.sha256(config).hexdigest())
    else:
        artifact = b'{"images": {},' + artifact[1:]
        manifest = replace(manifest, artifact_digest=hashlib.sha256(artifact).hexdigest())
    result = DeploymentVerifier(read_artifact=lambda _: artifact, read_config=lambda _: config,
                                read_dependency_lock=dependency_lock).check(manifest)
    assert result.status == 'BLOCKED' and result.evidence == ()


@pytest.mark.parametrize('processes', [None, ('execution-owner', []), ('execution-owner',) * 2])
def test_malformed_manifest_returns_blocked_without_reading_artifacts(processes):
    manifest, _, _ = manifest_inputs()
    calls = []
    verifier = DeploymentVerifier(read_artifact=lambda _: calls.append('artifact'),
                                  read_config=lambda _: calls.append('config'))
    result = verifier.check(replace(manifest, processes=processes))
    assert result.status == 'BLOCKED' and 'OFFLINE_MANIFEST_REQUIRED' in result.faults
    assert calls == []


def test_container_timeout_preserves_diagnostic_log(tmp_path, monkeypatch):
    # Unit coverage of log persistence only: no Docker invocation or OS evidence.
    case = ContainerCase(tmp_path, 'unused-owner-image', 'unused-decision-image')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 25, output=b'partial output', stderr=b'partial error')
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        case.docker('image', 'inspect', 'unused-owner-image')
    case.close()  # No resources were created; only the local diagnostic is written.
    log = json.loads((tmp_path / 'container-probe.json').read_text('utf-8'))
    assert log['native_qualification'] == 'NOT_RUN'
    assert log['commands'][0]['returncode'] is None
    assert log['commands'][0]['timeout_seconds'] == 25
    assert log['commands'][0]['stdout'] == 'partial output'
    assert log['commands'][0]['stderr'] == 'partial error'


def test_metrics_no_samples_are_not_target_measurements():
    metrics = LatencyMetrics(source='OFFLINE_UNIT_ONLY')
    for name, (percentile, target) in TARGETS.items():
        report = metrics.report(name)
        assert report.samples == report.total_observations == 0
        assert report.p95_ns is report.p99_ns is None
        assert (report.target_percentile, report.target_ns) == (percentile, target)
        assert report.acceptance == 'NOT_RUN'


def test_metrics_nearest_rank_bounded_window_and_invalid_clocks():
    metrics = LatencyMetrics(source='SYNTHETIC_PERCENTILE_UNIT', capacity=100)
    for n in range(1, 102):
        metrics.observe('fault_to_no_new_risk', started_ns=0, finished_ns=n)
    report = metrics.report('fault_to_no_new_risk')
    assert (report.samples, report.total_observations, report.p95_ns, report.p99_ns) == (100, 101, 96, 100)
    assert report.acceptance == 'NOT_RUN'
    for start, finish in [(2, 1), (-1, 0), (True, 2), (0, float('nan'))]:
        with pytest.raises(ValueError):
            metrics.observe('fault_to_no_new_risk', started_ns=start, finished_ns=finish)


def test_metrics_targets(tmp_path):
    # Raw monotonic endpoint observations must be supplied by the independently
    # registered full-system load runner. Unit loop timing is not this workload.
    name = os.environ.get('IP32_REFERENCE_SAMPLES')
    expected = os.environ.get('IP32_REFERENCE_SAMPLES_SHA256')
    if not name or not expected:
        pytest.skip('NOT_RUN: registered 50/5/10 workload endpoint observations unavailable')
    path = Path(name).resolve()
    assert path.is_relative_to(ROOT.resolve())
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected
    data = json.loads(raw)
    assert tuple(data['load']) == REFERENCE_LOAD
    assert data['build_sha'] == os.environ['IP32_BATCH_SHA']
    assert data['source'] == 'REGISTERED_OFFLINE_FULL_SYSTEM_LOAD'
    assert set(data['samples']) == set(TARGETS)
    metrics = LatencyMetrics(load=tuple(data['load']), source=data['source'],
                             capacity=max(len(v) for v in data['samples'].values()))
    reports = {}
    for metric, samples in data['samples'].items():
        assert len(samples) >= 100  # measurement floor, not a statistical proof
        for start, finish in samples:
            metrics.observe(metric, started_ns=start, finished_ns=finish)
        result = metrics.report(metric)
        reports[metric] = domain_payload(result)
    (tmp_path / 'latency-report.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
    for metric, report in reports.items():
        percentile, target = TARGETS[metric]
        assert report['p' + str(percentile) + '_ns'] <= target
        assert report['acceptance'] == 'NOT_RUN'  # never upgrades T/Q or registration


def test_privilege_matrix_in_real_containers(deployment_case):
    result = deployment_case.probe_decision_container_access()
    assert result.owner_positive_control
    assert result.can_read_signing_key is False
    assert result.can_reach_owner_write_endpoint is False
    assert result.shared_volume_readable is False


def test_owner_process_and_backing(deployment_case):
    assert deployment_case.duplicate_owner_exit() == 3
    assert deployment_case.backing_roles(pinned_image('IP32_POSTGRES_IMAGE'))
    config = rendered_config()
    assert deployment_faults(config) == ()
    assert config['x-ip32']['hot_reload'] is False
    assert config['services']['ledger']['volumes'] != config['services']['kernel-cache']['volumes']


def test_application_shard_lock_excludes_second_process(tmp_path):
    if sys.platform != 'linux' or os.environ.get('IP32_RUN_CONTAINERS') != '1' or os.getuid() == 0:
        pytest.skip('NOT_RUN: explicitly provisioned nonroot Linux lock probe required')
    tmp_path.chmod(0o700)
    shard = backup_state().shard
    lock = LinuxShardLock(tmp_path, expected_uid=os.getuid())
    code = """import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from trading_system.contracts import types as t
from trading_system.runtime.execution_owner import LinuxShardLock
shard=t.ExecutionShard(environment=t.Environment.PAPER,venue_account_id='synthetic-ip32',
 product=t.Product.SPOT,node_id='different-host',execution_epoch=99)
try:
 with LinuxShardLock(Path(sys.argv[1]),expected_uid=os.getuid()).claim(shard): pass
except BlockingIOError: sys.exit(3)
"""
    def contend():
        return subprocess.run([sys.executable, '-c', code, str(tmp_path), str(ROOT / 'src')],
                              capture_output=True, text=True, timeout=15)
    with lock.claim(shard):
        result = contend()
        assert result.returncode == 3, result.stderr
    result = contend()
    assert result.returncode == 0, result.stderr


def test_backup_restore_with_risk_locks(tmp_path):
    state = backup_state()
    path = tmp_path / 'backup.json'
    receipt = write_backup(path, state)
    target = replace(state.shard, node_id='synthetic-new', execution_epoch=8)
    plan = plan_restore(path, expected_hash=receipt.payload_hash, target=target)
    restored = json.loads(plan.snapshot_json)
    assert restored == domain_payload(state)
    assert len(restored['risk_locks']) == 2
    assert restored['authorizations'][0]['authorization_id'] == 'synthetic-grant'
    assert restored['commands'][0]['state'] == 'MAY_HAVE_SENT'
    assert restored['commands'][0]['execution_epoch'] == 7
    assert restored['positions'][0]['ownership_epoch'] == 1
    assert plan.recovery_required and not plan.flush_on_start
    assert plan.sendable_permits == plan.resume_task_ids == ()
    assert 'EXTERNAL_FENCE_REQUIRED' in plan.missing
    # Drive the actual supervisor and recovery gate with explicitly synthetic
    # reader inputs. Neither snapshot bytes nor lease expiry proves isolation.
    proof = t.ExternalFenceProof(shard=state.shard, old_instance_id=state.lease.instance_id,
                                 method='PROCESS_TERMINATED', evidence=ref('fence'))
    now = t.ClockReading(utc_now=NOW, monotonic_ns=1, clock_health='HEALTHY')
    observation = FenceObservation(proof=proof, old_lease=state.lease, target=target,
        external_write_isolated=False, expires_at=LATER, deadline_monotonic_ns=60_000_000_000)
    recovery_calls = []
    checklist = t.RecoveryChecklist(scope=SCOPE, unique_process=None, commands=None, funds=None,
        private_stream=None, positions=None, protection=None, authorization=None, unresolved_cases=())
    gate = RecoveryGate(read_locks=lambda _: state.risk_locks, clock=lambda: NOW)
    def recover(_):
        recovery_calls.append('reconcile')
        return gate.evaluate(checklist)
    supervisor = Supervisor(old_lease=state.lease, scope=SCOPE,
        read_fence=lambda p, s: observation, read_recovery=recover, clock=lambda: now)
    assert not supervisor.takeover(target, proof).allowed
    assert recovery_calls == []
    observation = replace(observation, external_write_isolated=True)
    result = supervisor.takeover(target, proof)
    assert recovery_calls == ['reconcile']
    assert not result.allowed and 'POST_FENCE_SHARD_RECOVERY_REQUIRED' in result.missing
    assert gate.evaluate(checklist).risk_locks == state.risk_locks


@pytest.mark.parametrize('change', ['bytes', 'hash', 'epoch', 'scope', 'live'])
def test_backup_restore_rejects_untrusted_input(tmp_path, change):
    state = backup_state()
    path = tmp_path / 'backup.json'
    receipt = write_backup(path, state)
    target = replace(state.shard, execution_epoch=8)
    digest = receipt.payload_hash
    if change == 'bytes':
        payload = json.loads(path.read_text('utf-8'))
        payload['payload']['risk_locks'] = []
        path.write_text(json.dumps(payload), encoding='utf-8')
    elif change == 'hash': digest = 'b' * 64
    elif change == 'epoch': target = state.shard
    elif change == 'scope': target = replace(target, venue_account_id='foreign')
    else: target = replace(target, environment=t.Environment.LIVE)
    with pytest.raises(ValueError):
        plan_restore(path, expected_hash=digest, target=target)
