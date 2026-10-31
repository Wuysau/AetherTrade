"""Offline governance regressions: identity isolation, Docker policy, budgets.

These tests never contact Docker, PostgreSQL, the network or TEST. They prove
policy decisions and refusal behaviour only; real OS permission isolation and
the fixed DEV service remain NOT_RUN until provisioned and verified externally.
"""
from dataclasses import replace
import math
import pytest

from trading_system.environment.budget import Budget, validate_budget, DEFAULT_BUDGET
from trading_system.environment.docker_policy import authorize
from trading_system.environment.identity import (
    DEV, TEST, FIXED_DEV, FIXED_TEST, IsolationVerdict, Role, ResourceIdentity,
    fixed_service, verify_isolation, verify_resource_identity,
)
from trading_system.environment.resources import ResourceRecord, evaluate_resources
from trading_system.environment.tempdb import TempDatabaseManager, TempDatabaseRefused


def test_fixed_environments_are_disjoint_and_paper_only():
    verdict = verify_isolation(FIXED_DEV, FIXED_TEST)
    assert verdict.status == 'PASS' and verdict.isolated and verdict.findings == ()
    assert FIXED_DEV.role is DEV and FIXED_TEST.role is TEST
    assert FIXED_DEV.branch == 'public-snapshot' and FIXED_TEST.branch == 'dev'
    assert FIXED_DEV.trading_mode == FIXED_TEST.trading_mode == 'PAPER'
    assert fixed_service(DEV) == FIXED_DEV and fixed_service(TEST) == FIXED_TEST
    # Explicit, different physical identities: not merely different names.
    for field in ('service_id', 'compose_project', 'database_identity', 'port',
                  'network', 'volume', 'credential_id'):
        assert getattr(FIXED_DEV, field) != getattr(FIXED_TEST, field)


@pytest.mark.parametrize('field', ['service_id', 'compose_project', 'database_identity',
                                   'port', 'network', 'volume', 'credential_id'])
def test_shared_resource_or_credential_blocks_isolation(field):
    verdict = verify_isolation(FIXED_DEV, replace(FIXED_TEST, **{field: getattr(FIXED_DEV, field)}))
    assert verdict.status == 'BLOCKED' and not verdict.isolated
    assert any(field.upper() in finding for finding in verdict.findings)


@pytest.mark.parametrize('mutation,code', [
    ({'trading_mode': 'TESTNET'}, 'TRADING_MODE'),
    ({'trading_mode': 'LIVE'}, 'TRADING_MODE'),
    ({'branch': 'public-snapshot'}, 'BRANCH'),
    ({'deployed_commit': None}, 'DEPLOYED_COMMIT'),
    ({'deployed_commit': 'not-a-sha'}, 'DEPLOYED_COMMIT'),
    ({'role': DEV}, 'ROLE'),
])
def test_test_identity_requires_pinned_paper_deployment(mutation, code):
    verdict = verify_isolation(FIXED_DEV, replace(FIXED_TEST, **mutation))
    assert verdict.status == 'BLOCKED'
    assert any(code in finding for finding in verdict.findings)


def test_dev_cannot_be_mistaken_for_test():
    verdict = verify_isolation(replace(FIXED_DEV, role=TEST), FIXED_TEST)
    assert verdict.status == 'BLOCKED'


def test_resource_identity_requires_exact_instance_and_oid():
    expected = ResourceIdentity(cluster_instance_id='dev-cluster-1', database_oid=4242,
                                connection_role='at_dev_runner', lease_id='lease-1')
    assert verify_resource_identity(expected, expected)
    for change in (replace(expected, cluster_instance_id='test-cluster-1'),
                   replace(expected, database_oid=4343),
                   replace(expected, connection_role='ledger_owner'),
                   replace(expected, lease_id='lease-2')):
        assert not verify_resource_identity(expected, change)


def test_budget_must_be_finite_bounded_and_complete():
    validate_budget(DEFAULT_BUDGET)
    assert DEFAULT_BUDGET.heavy_concurrency == 1
    for field in ('active_worktree_limit', 'heavy_concurrency', 'heavy_runtime_seconds',
                  'heavy_runs_per_stage', 'temp_database_limit', 'container_limit',
                  'disk_budget_bytes', 'artifact_budget_bytes', 'lease_seconds'):
        for bad in (0, -1, None, math.inf):
            with pytest.raises(ValueError):
                validate_budget(replace(DEFAULT_BUDGET, **{field: bad}))
    with pytest.raises(ValueError):
        validate_budget(replace(DEFAULT_BUDGET, heavy_concurrency=2))
    with pytest.raises(ValueError):
        validate_budget(replace(DEFAULT_BUDGET, trading_mode='TESTNET'))


@pytest.mark.parametrize('operation,arguments', [
    ('version', ()), ('info', ()), ('ps', ()), ('inspect', ('dev-ledger',)),
    ('logs', ('--tail=50', 'dev-ledger')),
])
def test_docker_read_only_allowlist(operation, arguments):
    decision = authorize(operation, arguments)
    assert decision.allowed and decision.reason == ''


@pytest.mark.parametrize('operation,arguments', [
    ('system', ('prune', '-a', '--volumes')),
    ('container', ('prune', '-f')),
    ('image', ('prune', '-a')),
    ('volume', ('rm', 'dev-data')),
    ('volume', ('prune',)),
    ('rm', ('-f', 'dev-ledger')),
    ('rmi', ('sha256:abc',)),
    ('build', ('.',)),
    ('pull', ('postgres:18',)),
    ('run', ('--privileged', 'postgres:18')),
    ('exec', ('dev-ledger', 'sh')),
    ('cp', ('secret', 'dev-ledger:/tmp')),
    ('mount', ('vhd', 'C:/disk.vhdx')),
    ('inspect', ('--format={{.Config.Env}}', 'dev-ledger')),
    ('inspect', ('--privileged=true', 'dev-ledger')),
    ('logs', ('--follow', 'dev-ledger')),
    ('logs', ('dev-ledger', '; rm -rf /')),
    ('unknown-command', ()),
])
def test_docker_mutating_global_or_arbitrary_operations_are_denied(operation, arguments):
    decision = authorize(operation, arguments)
    assert not decision.allowed and decision.reason


@pytest.mark.parametrize('operation', ['prune', 'system', 'rm', 'rmi', 'vhd', 'mount',
                                       'reset', 'migrate', 'compose'])
def test_global_cleanup_vhdx_and_migration_are_always_denied(operation):
    assert not authorize(operation, ()).allowed


def test_resource_monitor_reports_legacy_and_unregistered_without_deleting():
    records = (
        ResourceRecord(kind='CONTAINER', resource_id='dev-ledger', instance_id='dev-cluster-1',
                       owner_task='DEV', registered=True, bytes=0),
        ResourceRecord(kind='DATABASE', resource_id='at_dev_run1', instance_id='dev-cluster-1',
                       owner_task='run1', registered=True, bytes=1024),
        ResourceRecord(kind='DATABASE', resource_id='old_unknown', instance_id='dev-cluster-1',
                       owner_task=None, registered=False, bytes=2048),
        ResourceRecord(kind='CONTAINER', resource_id='task-runner-9', instance_id='dev-cluster-1',
                       owner_task='old-task', registered=False, bytes=4096),
    )
    report = evaluate_resources(records, DEFAULT_BUDGET, disk_used_bytes=1024)
    assert report.status == 'BLOCKED'
    assert {r.resource_id for r in report.unregistered} == {'old_unknown', 'task-runner-9'}
    assert report.legacy == ('old_unknown', 'task-runner-9')
    assert not hasattr(report, 'cleanup') and not hasattr(report, 'delete')
    # The report is diagnostic; it never authorises automatic removal.
    assert report.action == 'BLOCK_NEW_ADMISSION_AND_REPORT_OWNER'


def test_resource_monitor_disk_thresholds_are_advisory_then_blocking():
    records = (ResourceRecord(kind='CONTAINER', resource_id='dev-ledger', instance_id='dev-cluster-1',
                              owner_task='DEV', registered=True, bytes=0),)
    budget = replace(DEFAULT_BUDGET, disk_budget_bytes=1000)
    assert evaluate_resources(records, budget, disk_used_bytes=500).status == 'OK'
    alert = evaluate_resources(records, budget, disk_used_bytes=800)
    assert alert.status == 'ALERT' and alert.action == 'STOP_NEW_HEAVY_RUNS'
    blocked = evaluate_resources(records, budget, disk_used_bytes=1000)
    assert blocked.status == 'BLOCKED' and blocked.action == 'BLOCK_NEW_ADMISSION_AND_REPORT_OWNER'
    # Disk pressure never relaxes prohibitions.
    assert not authorize('system', ('prune', '-a')).allowed


def test_temp_database_lease_requires_fixed_dev_identity_and_exact_reclaim(tmp_path):
    observed = {'at_dev_run1': ('dev-cluster-1', 'at_dev_run1', 4242)}
    manager = TempDatabaseManager(DEFAULT_BUDGET, clock=lambda: 1000,
                                  observe=lambda name: observed.get(name))
    lease = manager.allocate(run_id='run1', instance_id='dev-cluster-1',
                             database_name='at_dev_run1', database_oid=4242,
                             connection_role='at_dev_runner')
    assert lease.database_oid == 4242 and manager.active_count == 1
    with pytest.raises(TempDatabaseRefused):
        manager.allocate(run_id='run2', instance_id='test-cluster-1',
                         database_name='at_dev_run2', database_oid=4243,
                         connection_role='at_dev_runner')
    with pytest.raises(TempDatabaseRefused):
        manager.allocate(run_id='run2', instance_id='dev-cluster-1',
                         database_name='at_dev_run2', database_oid=4242,
                         connection_role='at_dev_runner')
    assert manager.release(lease, active_connections=0, ownership_proof='owner:run1').status == 'RELEASED'
    assert manager.active_count == 0
    assert manager.release(lease, active_connections=0, ownership_proof='owner:run1').status == 'ALREADY_RELEASED'


@pytest.mark.parametrize('fault', ['active_connections', 'missing_proof', 'identity_changed',
                                   'not_owner', 'unknown_resource'])
def test_temp_database_reclaim_refuses_uncertain_or_foreign_resources(fault):
    observed = {'at_dev_run1': ('dev-cluster-1', 'at_dev_run1', 4242)}
    manager = TempDatabaseManager(DEFAULT_BUDGET, clock=lambda: 1000,
                                  observe=lambda name: observed.get(name))
    lease = manager.allocate(run_id='run1', instance_id='dev-cluster-1',
                             database_name='at_dev_run1', database_oid=4242,
                             connection_role='at_dev_runner')
    kwargs = dict(active_connections=0, ownership_proof='owner:run1')
    if fault == 'active_connections':
        kwargs['active_connections'] = 1
    elif fault == 'missing_proof':
        kwargs['ownership_proof'] = ''
    elif fault == 'identity_changed':
        observed['at_dev_run1'] = ('dev-cluster-1', 'at_dev_run1', 9999)
    elif fault == 'not_owner':
        kwargs['ownership_proof'] = 'owner:run2'
    elif fault == 'unknown_resource':
        observed.clear()
    result = manager.release(lease, **kwargs)
    assert result.status == 'BLOCKED' and result.reason
    assert manager.active_count == 1


def test_temp_database_expired_lease_blocks_and_is_not_auto_taken_over():
    observed = {'at_dev_run1': ('dev-cluster-1', 'at_dev_run1', 4242)}
    now = [1000]
    manager = TempDatabaseManager(replace(DEFAULT_BUDGET, lease_seconds=10),
                                  clock=lambda: now[0], observe=lambda name: observed.get(name))
    lease = manager.allocate(run_id='run1', instance_id='dev-cluster-1',
                             database_name='at_dev_run1', database_oid=4242,
                             connection_role='at_dev_runner')
    now[0] = 1011
    result = manager.release(lease, active_connections=0, ownership_proof='owner:run1')
    assert result.status == 'BLOCKED' and 'EXPIRED' in result.reason
    assert manager.active_count == 1
    with pytest.raises(TempDatabaseRefused):
        manager.allocate(run_id='run2', instance_id='dev-cluster-1',
                         database_name='at_dev_run2', database_oid=4243,
                         connection_role='at_dev_runner')
