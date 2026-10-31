"""Bounded test scheduling: queue, leases, budgets and honest statuses.

State is persisted as canonical JSON so a crash does not silently release a
lease or replay work. Expired leases are marked BLOCKED and require an explicit,
evidenced recovery; they are never reassigned automatically. No automatic retry
exists. This is offline policy logic: it does not launch processes or Docker.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from .budget import Budget, validate_budget, DEFAULT_BUDGET


class Level(str, Enum):
    TASK_LIGHT = 'TASK_LIGHT'
    MERGE_QUICK = 'MERGE_QUICK'
    TARGETED_INTEGRATION = 'TARGETED_INTEGRATION'
    BATCH_FULL = 'BATCH_FULL'
    REPAIR_TARGETED = 'REPAIR_TARGETED'
    FINAL_REGRESSION = 'FINAL_REGRESSION'


class RunStatus(str, Enum):
    QUEUED = 'QUEUED'
    RUNNING = 'RUNNING'
    PASS = 'PASS'
    FAIL = 'FAIL'
    BLOCKED = 'BLOCKED'
    NOT_RUN = 'NOT_RUN'


class SubmissionStatus(str, Enum):
    QUEUED = 'QUEUED'
    DUPLICATE = 'DUPLICATE'
    REJECTED = 'REJECTED'


HEAVY = frozenset({Level.TARGETED_INTEGRATION, Level.BATCH_FULL,
                   Level.REPAIR_TARGETED, Level.FINAL_REGRESSION})
MAX_LIGHT_CASES = 50
_SHA = re.compile(r'[0-9a-f]{40}\Z')


@dataclass(frozen=True, slots=True)
class TestRequest:
    __test__ = False
    task_id: str
    batch_id: str
    run_id: str
    source_sha: str
    level: Level
    selection: tuple[str, ...]
    reason: str
    idempotency_key: str
    timeout_seconds: int
    environment: str = 'DEV'


@dataclass(frozen=True, slots=True)
class Submission:
    status: SubmissionStatus
    run_id: str
    reason: str = ''


@dataclass(frozen=True, slots=True)
class Lease:
    run_id: str
    token: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    task_id: str
    batch_id: str
    source_sha: str
    level: Level
    selection: tuple[str, ...]
    reason: str
    idempotency_key: str
    timeout_seconds: int
    environment: str
    status: RunStatus
    submitted_at: int
    attempts: int = 1
    token: str | None = None
    started_at: int | None = None
    finished_at: int | None = None
    expires_at: int | None = None
    exit_code: int | None = None
    evidence_hash: str = ''
    skipped_security_cases: int = 0
    expired_recovered: bool = False


def _fingerprint(request: TestRequest) -> str:
    payload = json.dumps({'task_id': request.task_id, 'batch_id': request.batch_id,
                          'source_sha': request.source_sha, 'level': request.level.value,
                          'selection': list(request.selection),
                          'timeout_seconds': request.timeout_seconds},
                         sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class TestScheduler:
    __test__ = False

    def __init__(self, path, *, clock, budget: Budget = DEFAULT_BUDGET):
        validate_budget(budget)
        self.path = Path(path)
        self.clock = clock
        self.budget = budget
        self._state = {'version': 1, 'runs': {}, 'idempotency': {}, 'artifacts_bytes': 0}
        if self.path.exists():
            self._state = json.loads(self.path.read_text(encoding='utf-8'))

    @property
    def runs(self) -> dict[str, RunRecord]:
        return {key: self._run(value) for key, value in self._state['runs'].items()}

    def run(self, run_id: str) -> RunRecord:
        try:
            return self._run(self._state['runs'][run_id])
        except KeyError as exc:
            raise KeyError('RUN_NOT_FOUND') from exc

    @staticmethod
    def _run(value: dict) -> RunRecord:
        return RunRecord(run_id=value['run_id'], task_id=value['task_id'],
                         batch_id=value['batch_id'], source_sha=value['source_sha'],
                         level=Level(value['level']), selection=tuple(value['selection']),
                         reason=value['reason'], idempotency_key=value['idempotency_key'],
                         timeout_seconds=value['timeout_seconds'],
                         environment=value['environment'], status=RunStatus(value['status']),
                         submitted_at=value['submitted_at'], attempts=value['attempts'],
                         token=value['token'], started_at=value['started_at'],
                         finished_at=value['finished_at'], expires_at=value['expires_at'],
                         exit_code=value['exit_code'], evidence_hash=value['evidence_hash'],
                         skipped_security_cases=value['skipped_security_cases'],
                         expired_recovered=value['expired_recovered'])

    @staticmethod
    def _store(record: RunRecord) -> dict:
        return {'run_id': record.run_id, 'task_id': record.task_id,
                'batch_id': record.batch_id, 'source_sha': record.source_sha,
                'level': record.level.value, 'selection': list(record.selection),
                'reason': record.reason, 'idempotency_key': record.idempotency_key,
                'timeout_seconds': record.timeout_seconds,
                'environment': record.environment, 'status': record.status.value,
                'submitted_at': record.submitted_at, 'attempts': record.attempts,
                'token': record.token, 'started_at': record.started_at,
                'finished_at': record.finished_at, 'expires_at': record.expires_at,
                'exit_code': record.exit_code, 'evidence_hash': record.evidence_hash,
                'skipped_security_cases': record.skipped_security_cases,
                'expired_recovered': record.expired_recovered}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self._state, sort_keys=True, indent=2), encoding='utf-8')
        os.replace(temporary, self.path)

    def _update(self, record: RunRecord) -> None:
        self._state['runs'][record.run_id] = self._store(record)

    def submit(self, request: TestRequest, *, heavy_authorized: bool = False,
               batch_ready: bool = False) -> Submission:
        invalid = self._invalid(request)
        if invalid:
            return Submission(SubmissionStatus.REJECTED, request.run_id, invalid)
        fingerprint = _fingerprint(request)
        existing = self._state['idempotency'].get(request.idempotency_key)
        if existing is not None:
            if existing['fingerprint'] == fingerprint:
                return Submission(SubmissionStatus.DUPLICATE, existing['run_id'])
            return Submission(SubmissionStatus.REJECTED, request.run_id,
                              'IDEMPOTENCY_KEY_CONFLICT')
        if request.run_id in self._state['runs']:
            return Submission(SubmissionStatus.REJECTED, request.run_id, 'RUN_ID_EXISTS')
        if request.level in HEAVY and not heavy_authorized:
            return Submission(SubmissionStatus.REJECTED, request.run_id,
                              'HEAVY_RUN_AUTHORIZATION_REQUIRED')
        if request.level is Level.BATCH_FULL and not batch_ready:
            return Submission(SubmissionStatus.REJECTED, request.run_id,
                              'BATCH_NOT_READY')
        if request.level in HEAVY:
            count = sum(1 for run in self._state['runs'].values()
                        if run['batch_id'] == request.batch_id)
            if count >= self.budget.heavy_runs_per_stage:
                return Submission(SubmissionStatus.REJECTED, request.run_id,
                                  'STAGE_RUN_BUDGET_EXHAUSTED')
        record = RunRecord(run_id=request.run_id, task_id=request.task_id,
                           batch_id=request.batch_id, source_sha=request.source_sha,
                           level=request.level, selection=request.selection,
                           reason=request.reason, idempotency_key=request.idempotency_key,
                           timeout_seconds=request.timeout_seconds,
                           environment=request.environment,
                           status=RunStatus.QUEUED, submitted_at=self.clock())
        self._update(record)
        self._state['idempotency'][request.idempotency_key] = {
            'fingerprint': fingerprint, 'run_id': request.run_id}
        self._save()
        return Submission(SubmissionStatus.QUEUED, request.run_id)

    def _invalid(self, request: TestRequest) -> str:
        if type(request) is not TestRequest:
            return 'REQUEST_REQUIRED'
        if request.environment != 'DEV':
            return 'ONLY_SHARED_DEV_TESTS_ARE_ALLOWED'
        if not isinstance(request.source_sha, str) or _SHA.fullmatch(request.source_sha) is None:
            return 'SOURCE_SHA_REQUIRED'
        for name in ('task_id', 'batch_id', 'run_id', 'idempotency_key', 'reason'):
            value = getattr(request, name)
            if not isinstance(value, str) or not value:
                return name.upper() + '_REQUIRED'
        if type(request.level) is not Level:
            return 'TEST_LEVEL_REQUIRED'
        if type(request.selection) is not tuple or not 1 <= len(request.selection) <= MAX_LIGHT_CASES:
            return 'SELECTION_BOUND_REQUIRED'
        if not all(isinstance(item, str) and item for item in request.selection):
            return 'SELECTION_INVALID'
        if (type(request.timeout_seconds) is not int or request.timeout_seconds <= 0
                or request.timeout_seconds > self.budget.heavy_runtime_seconds):
            return 'TIMEOUT_BUDGET_EXCEEDED'
        return ''

    def lease(self) -> Lease | None:
        now = self.clock()
        running = [r for r in self._state['runs'].values() if r['status'] == RunStatus.RUNNING.value]
        for value in running:
            if now >= value['expires_at']:
                record = replace(self._run(value), status=RunStatus.BLOCKED,
                                 reason='LEASE_EXPIRED', token=None)
                self._update(record)
                self._save()
                return None
            return None
        if any(r['status'] == RunStatus.BLOCKED.value and r['reason'] == 'LEASE_EXPIRED'
               and not r['expired_recovered'] for r in self._state['runs'].values()):
            return None
        queued = sorted((r for r in self._state['runs'].values()
                         if r['status'] == RunStatus.QUEUED.value),
                        key=lambda r: (r['submitted_at'], r['run_id']))
        if not queued:
            return None
        value = queued[0]
        token = secrets.token_urlsafe(24)
        expires_at = now + self.budget.lease_seconds
        record = replace(self._run(value), status=RunStatus.RUNNING, token=token,
                         started_at=now, expires_at=expires_at)
        self._update(record)
        self._save()
        return Lease(run_id=record.run_id, token=token, expires_at=expires_at)

    def recover_expired(self, run_id: str, *, reason: str, evidence_hash: str) -> RunRecord:
        record = self.run(run_id)
        if record.status is not RunStatus.BLOCKED or record.reason != 'LEASE_EXPIRED':
            raise ValueError('NO_EXPIRED_LEASE_TO_RECOVER')
        if not isinstance(reason, str) or not reason or not isinstance(evidence_hash, str) \
                or not evidence_hash:
            raise ValueError('RECOVERY_EVIDENCE_REQUIRED')
        record = replace(record, status=RunStatus.NOT_RUN, reason='RECOVERED:' + reason,
                         evidence_hash=evidence_hash, expired_recovered=True,
                         finished_at=self.clock())
        self._update(record)
        self._save()
        return record

    def complete(self, lease: Lease, *, status: RunStatus, exit_code: int | None,
                 evidence_hash: str = '', skipped_security_cases: int = 0) -> RunRecord:
        record = self.run(lease.run_id)
        if record.status is not RunStatus.RUNNING or record.token != lease.token:
            raise ValueError('LEASE_NOT_OWNED')
        if type(skipped_security_cases) is not int or skipped_security_cases < 0:
            raise ValueError('SKIPPED_CASE_COUNT_INVALID')
        if status is RunStatus.PASS:
            if exit_code != 0:
                final, reason = RunStatus.FAIL, 'NONZERO_EXIT'
            elif not evidence_hash:
                final, reason = RunStatus.BLOCKED, 'EVIDENCE_REQUIRED'
            elif skipped_security_cases:
                final, reason = RunStatus.BLOCKED, 'SECURITY_CASE_SKIPPED'
            else:
                final, reason = RunStatus.PASS, record.reason
        else:
            if type(status) is not RunStatus:
                raise ValueError('RUN_STATUS_REQUIRED')
            final, reason = status, record.reason
        record = replace(record, status=final, reason=reason, token=None,
                         finished_at=self.clock(), exit_code=exit_code,
                         evidence_hash=evidence_hash,
                         skipped_security_cases=skipped_security_cases)
        self._update(record)
        self._save()
        return record

    def escalate(self, run_id: str, new_level: Level) -> RunRecord:
        record = self.run(run_id)
        raise ValueError('LEVEL_ESCALATION_REQUIRES_NEW_BATCH:' + record.level.value)

    def request_retry(self, run_id: str, reason: str, *, authorized: bool = False) -> Submission:
        record = self.run(run_id)
        if not authorized:
            return Submission(SubmissionStatus.REJECTED, run_id,
                              'AUTOMATIC_RETRY_DISABLED')
        if record.status not in (RunStatus.FAIL, RunStatus.BLOCKED, RunStatus.NOT_RUN):
            return Submission(SubmissionStatus.REJECTED, run_id, 'RUN_NOT_RETRYABLE')
        if not isinstance(reason, str) or not reason:
            return Submission(SubmissionStatus.REJECTED, run_id, 'RETRY_REASON_REQUIRED')
        if record.attempts >= 2:
            return Submission(SubmissionStatus.REJECTED, run_id, 'RETRY_BUDGET_EXHAUSTED')
        record = replace(record, status=RunStatus.QUEUED, reason='RETRY:' + reason,
                         attempts=record.attempts + 1, token=None, started_at=None,
                         finished_at=None, expires_at=None, exit_code=None,
                         evidence_hash='')
        self._update(record)
        self._save()
        return Submission(SubmissionStatus.QUEUED, run_id)

    def reserve_artifact(self, size: int) -> int:
        if type(size) is not int or size <= 0:
            raise ValueError('ARTIFACT_SIZE_INVALID')
        if self._state['artifacts_bytes'] + size > self.budget.artifact_budget_bytes:
            raise ValueError('ARTIFACT_BUDGET_EXHAUSTED')
        self._state['artifacts_bytes'] += size
        self._save()
        return size
