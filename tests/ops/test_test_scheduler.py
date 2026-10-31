"""Offline bounded test-scheduler regressions.

No Docker, PostgreSQL or TEST access. Lease/queue/budget/status semantics are
proven with an injected clock and temporary JSON state. Real enforcement still
requires OS permissions and the fixed shared DEV service (NOT_RUN).
"""
from dataclasses import replace
import json
import pytest

from trading_system.environment.budget import DEFAULT_BUDGET
from trading_system.environment.scheduler import (
    Level, RunStatus, SubmissionStatus, TestRequest, TestScheduler,
)


def request(**changes):
    base = dict(task_id='T1', batch_id='B1', run_id='R1', source_sha='a' * 40,
                level=Level.TASK_LIGHT, selection=('tests/intents/test_a.py',),
                reason='focused', idempotency_key='key-1', timeout_seconds=60,
                environment='DEV')
    base.update(changes)
    return TestRequest(**base)


def scheduler(tmp_path, *, budget=DEFAULT_BUDGET, now=1000):
    clock = now if callable(now) else (lambda: now)
    return TestScheduler(tmp_path / 'scheduler.json', clock=clock, budget=budget)


def test_test_and_live_targets_are_never_accepted(tmp_path):
    s = scheduler(tmp_path)
    for environment in ('TEST', 'TESTNET', 'LIVE', 'dev'):
        result = s.submit(request(environment=environment))
        assert result.status is SubmissionStatus.REJECTED and result.reason


@pytest.mark.parametrize('mutation', [
    {'source_sha': 'abc'}, {'source_sha': 'g' * 40}, {'selection': ()},
    {'selection': ('tests/x.py',) * 200}, {'timeout_seconds': 0},
    {'idempotency_key': ''}, {'task_id': ''}, {'run_id': ''},
])
def test_request_validation_is_exact(tmp_path, mutation):
    result = scheduler(tmp_path).submit(request(**mutation))
    assert result.status is SubmissionStatus.REJECTED and result.reason


def test_duplicate_idempotency_never_starts_a_second_run(tmp_path):
    s = scheduler(tmp_path)
    first = s.submit(request())
    assert first.status is SubmissionStatus.QUEUED
    duplicate = s.submit(request())
    assert duplicate.status is SubmissionStatus.DUPLICATE and duplicate.run_id == first.run_id
    assert len(s.runs) == 1
    conflict = s.submit(request(selection=('tests/intents/test_b.py',)))
    assert conflict.status is SubmissionStatus.REJECTED and conflict.reason


def test_heavy_layers_require_explicit_authorization_and_batch_readiness(tmp_path):
    for level in (Level.BATCH_FULL, Level.TARGETED_INTEGRATION, Level.FINAL_REGRESSION):
        s = scheduler(tmp_path / level.value)
        assert s.submit(request(level=level)).status is SubmissionStatus.REJECTED
        assert s.submit(request(level=level), heavy_authorized=True).status is (
            SubmissionStatus.REJECTED if level is Level.BATCH_FULL else SubmissionStatus.QUEUED)
    s = scheduler(tmp_path / 'batch')
    assert s.submit(request(run_id='R2', level=Level.BATCH_FULL), heavy_authorized=True,
                    batch_ready=True).status is SubmissionStatus.QUEUED


def test_light_pass_cannot_be_escalated_to_full_acceptance(tmp_path):
    s = scheduler(tmp_path)
    s.submit(request())
    lease = s.lease()
    s.complete(lease, status=RunStatus.PASS, exit_code=0, evidence_hash='h' * 64)
    with pytest.raises(ValueError):
        s.escalate('R1', Level.BATCH_FULL)
    assert s.run('R1').level is Level.TASK_LIGHT


def test_single_heavy_concurrency_and_owner_token(tmp_path):
    s = scheduler(tmp_path, budget=replace(DEFAULT_BUDGET, heavy_runs_per_stage=9))
    s.submit(request())
    s.submit(request(run_id='R2', idempotency_key='key-2'))
    lease = s.lease()
    assert lease is not None and lease.run_id == 'R1'
    assert s.lease() is None
    with pytest.raises(ValueError):
        s.complete(replace(lease, token='forged'), status=RunStatus.PASS,
                   exit_code=0, evidence_hash='h' * 64)
    s.complete(lease, status=RunStatus.PASS, exit_code=0, evidence_hash='h' * 64)
    assert s.lease().run_id == 'R2'


@pytest.mark.parametrize('status,exit_code,evidence,skipped,expected', [
    (RunStatus.PASS, 0, 'h' * 64, 0, RunStatus.PASS),
    (RunStatus.PASS, 1, 'h' * 64, 0, RunStatus.FAIL),
    (RunStatus.PASS, 0, '', 0, RunStatus.BLOCKED),
    (RunStatus.PASS, 0, 'h' * 64, 1, RunStatus.BLOCKED),
    (RunStatus.BLOCKED, 0, '', 0, RunStatus.BLOCKED),
    (RunStatus.NOT_RUN, 0, '', 0, RunStatus.NOT_RUN),
])
def test_status_is_never_upgraded(tmp_path, status, exit_code, evidence, skipped, expected):
    s = scheduler(tmp_path)
    s.submit(request())
    result = s.complete(s.lease(), status=status, exit_code=exit_code,
                        evidence_hash=evidence, skipped_security_cases=skipped)
    assert result.status is expected


def test_expired_lease_is_not_automatically_reassigned(tmp_path):
    now = [1000]
    s = scheduler(tmp_path, budget=replace(DEFAULT_BUDGET, lease_seconds=5,
                                           heavy_runs_per_stage=9), now=lambda: now[0])
    s.submit(request())
    s.submit(request(run_id='R2', idempotency_key='key-2'))
    lease = s.lease()
    now[0] = 1006
    assert s.lease() is None
    assert s.run('R1').status is RunStatus.BLOCKED
    assert s.run('R1').reason == 'LEASE_EXPIRED'
    with pytest.raises(ValueError):
        s.recover_expired('R1', reason='', evidence_hash='')
    recovered = s.recover_expired('R1', reason='process terminated', evidence_hash='e' * 64)
    assert recovered.status is RunStatus.NOT_RUN
    assert s.lease().run_id == 'R2'


def test_state_survives_restart_without_replaying_work(tmp_path):
    first = scheduler(tmp_path)
    first.submit(request())
    lease = first.lease()
    restarted = scheduler(tmp_path)
    assert restarted.run('R1').status is RunStatus.RUNNING
    assert restarted.lease() is None
    restarted.complete(replace(lease), status=RunStatus.PASS, exit_code=0,
                       evidence_hash='h' * 64)
    assert scheduler(tmp_path).run('R1').status is RunStatus.PASS


def test_run_count_disk_and_artifact_budgets_refuse_admission(tmp_path):
    s = scheduler(tmp_path, budget=replace(DEFAULT_BUDGET, heavy_runs_per_stage=1))
    s.submit(request())
    s.complete(s.lease(), status=RunStatus.PASS, exit_code=0, evidence_hash='h' * 64)
    assert s.submit(request(run_id='R2', idempotency_key='key-2',
                            level=Level.TARGETED_INTEGRATION),
                    heavy_authorized=True).status is SubmissionStatus.REJECTED
    with pytest.raises(ValueError):
        s.reserve_artifact(10 ** 9)
    assert s.reserve_artifact(10) == 10


def test_automatic_retry_is_disabled_and_requires_explicit_authorization(tmp_path):
    s = scheduler(tmp_path)
    s.submit(request())
    s.complete(s.lease(), status=RunStatus.FAIL, exit_code=1)
    assert s.request_retry('R1', 'flaky').status is SubmissionStatus.REJECTED
    assert s.request_retry('R1', 'recorded failure cause', authorized=True).status is SubmissionStatus.QUEUED
    assert s.run('R1').attempts == 2


def test_evidence_and_selection_are_preserved_for_audit(tmp_path):
    s = scheduler(tmp_path)
    s.submit(request())
    s.complete(s.lease(), status=RunStatus.BLOCKED, exit_code=78)
    payload = json.loads((tmp_path / 'scheduler.json').read_text('utf-8'))
    stored = payload['runs']['R1']
    assert stored['source_sha'] == 'a' * 40
    assert stored['selection'] == ['tests/intents/test_a.py']
    assert stored['exit_code'] == 78 and stored['status'] == 'BLOCKED'
    assert stored['evidence_hash'] == ''
