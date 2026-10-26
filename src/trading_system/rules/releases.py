"""Versioned release metadata, never a deployment or an execution-code loader.

The repository must provide durable atomic CAS in production. Callbacks belong
to trusted server composition, not to strategy code or a model tool registry.
"""
from __future__ import annotations

from threading import RLock
from typing import Callable, Protocol
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from .sandbox import validate_artifact, digest


class ReleaseDenied(ValueError):
    pass


def _token(value: str) -> bool:
    return type(value) is str and bool(value.strip()) and len(value) <= 256


def _environment(environment: t.Environment) -> None:
    if environment == t.Environment.LIVE:
        raise ReleaseDenied('LIVE_DISABLED')
    if type(environment) is not t.Environment:
        raise ReleaseDenied('INVALID_ENVIRONMENT')


def _change(change: t.StrategyChange) -> None:
    if (type(change) is not t.StrategyChange or not _token(change.strategy_id)
            or type(change.expected_revision) is not int or change.expected_revision < 0):
        raise ReleaseDenied('INVALID_CHANGE')
    try:
        validate_artifact(change.artifact)
    except ValueError as exc:
        raise ReleaseDenied('INVALID_ARTIFACT') from exc


def _candidate(candidate: t.ReleaseCandidate) -> None:
    if (type(candidate) is not t.ReleaseCandidate or not _token(candidate.candidate_id)
            or type(candidate.evaluation_refs) is not tuple or not candidate.evaluation_refs
            or len(candidate.evaluation_refs) > 32):
        raise ReleaseDenied('INVALID_CANDIDATE')
    _change(candidate.change)


class ReleaseRepository(Protocol):
    @property
    def environment(self) -> t.Environment: ...
    def revision(self, strategy_id: str) -> int: ...
    def save_candidate(self, candidate: t.ReleaseCandidate,
                       rollback_revision: int | None = None) -> None: ...
    def candidate(self, candidate_id: str) -> t.ReleaseCandidate: ...
    def rollback_target(self, candidate_id: str) -> int | None: ...
    def history(self, strategy_id: str) -> tuple[t.DeploymentRevision, ...]: ...
    def activate(self, candidate: t.ReleaseCandidate, approval: t.ReleaseApproval,
                 rollback_revision: int | None) -> t.DeploymentRevision:
        """Atomically compare expected revision, consume candidate and append.

        Reject duplicate/stale candidates and approvals for another candidate.
        Compare deployment identity and rollback target in the same transaction.
        Return only after durable commit. Preserve candidate, approval and hashes.
        """
        ...


class ReleaseService:
    def __init__(self, *, repository: ReleaseRepository, environment: t.Environment,
                 now: Callable[[], str], evaluate: Callable,
                 authorize: Callable[[t.ReleaseCandidate, t.ReleaseApproval], bool],
                 verify_evidence: Callable[[t.EvidenceRef, t.StrategyChange, str], bool]):
        _environment(environment)
        if repository.environment != environment:
            raise ReleaseDenied('REPOSITORY_ENVIRONMENT_MISMATCH')
        self._repository, self._environment = repository, environment
        self._now, self._evaluate = now, evaluate
        self._authorize, self._verify_evidence = authorize, verify_evidence

    def _evidence(self, ref: t.EvidenceRef, change: t.StrategyChange, purpose: str) -> None:
        try:
            if type(ref) is not t.EvidenceRef:
                raise ValueError('invalid evidence type')
            digest(ref.hash)
            valid = (_token(ref.id) and _token(ref.source)
                and ref.environment == self._environment
                and utc_timestamp(ref.visible_at) <= utc_timestamp(self._now()))
        except (TypeError, ValueError) as exc:
            raise ReleaseDenied('UNVERIFIED_RELEASE_EVIDENCE') from exc
        # Existence of a hash alone is not a review of THIS artifact/dependency
        # lock, nor an evaluation of THIS change. The trusted resolver binds both.
        if not valid or self._verify_evidence(ref, change, purpose) is not True:
            raise ReleaseDenied('UNVERIFIED_RELEASE_EVIDENCE')

    def propose(self, change: t.StrategyChange) -> t.ReleaseCandidate:
        return self._propose(change, rollback_revision=None)

    def _propose(self, change: t.StrategyChange, rollback_revision: int | None) -> t.ReleaseCandidate:
        _change(change)
        if self._repository.revision(change.strategy_id) != change.expected_revision:
            raise ReleaseDenied('STALE_REVISION')
        self._evidence(change.review_ref, change, 'REVIEW')
        # The evaluator runs outside the repository transaction/lock. Materialize
        # only a bounded number of refs; no model or platform calls under a lock.
        refs = []
        for ref in self._evaluate(change):
            if len(refs) >= 32:
                raise ReleaseDenied('EVALUATION_LIMIT')
            refs.append(ref)
        if not refs:
            raise ReleaseDenied('EVALUATION_REQUIRED')
        for ref in refs:
            self._evidence(ref, change, 'EVALUATION')
        candidate = t.ReleaseCandidate(candidate_id=uuid4().hex, change=change, evaluation_refs=tuple(refs))
        self._repository.save_candidate(candidate, rollback_revision=rollback_revision)
        return candidate

    def activate(self, candidate: t.ReleaseCandidate,
                 approval: t.ReleaseApproval) -> t.DeploymentRevision:
        _candidate(candidate)
        if self._repository.candidate(candidate.candidate_id) != candidate:
            raise ReleaseDenied('CANDIDATE_TAMPERED')
        if type(approval) is not t.ReleaseApproval or type(approval.actor) is not t.ActorContext:
            raise ReleaseDenied('APPROVAL_SCOPE_INVALID')
        actor = approval.actor
        if (approval.candidate_id != candidate.candidate_id or not _token(actor.identity_id)
                or not _token(actor.deployment_id) or not _token(approval.authorization_id)
                or approval.authorization_id != actor.authorization_id
                or actor.environment != self._environment
                or type(actor.policy_revision) is not int or actor.policy_revision < 1
                or type(actor.ownership_epoch) is not int or actor.ownership_epoch < 1
                or type(actor.scopes) is not tuple or not actor.scopes
                or any(type(scope) is not t.ScopeKey or scope.environment != self._environment
                       for scope in actor.scopes)):
            raise ReleaseDenied('APPROVAL_SCOPE_INVALID')
        try:
            if utc_timestamp(approval.approved_at) > utc_timestamp(self._now()):
                raise ValueError('future approval')
        except (ValueError, TypeError) as exc:
            raise ReleaseDenied('APPROVAL_SCOPE_INVALID') from exc
        self._evidence(candidate.change.review_ref, candidate.change, 'REVIEW')
        for ref in candidate.evaluation_refs:
            self._evidence(ref, candidate.change, 'EVALUATION')
        # Server authorizer must bind human identity, strategy/deployment, artifact,
        # current authorization/policy/ownership, expiry and revocation. A nonempty
        # authorization ID, profitable evaluation or model suggestion is insufficient.
        if self._authorize(candidate, approval) is not True:
            raise ReleaseDenied('RELEASE_PERMISSION_REQUIRED')
        history = self._repository.history(candidate.change.strategy_id)
        if history and history[-1].deployment_id != actor.deployment_id:
            raise ReleaseDenied('DEPLOYMENT_MISMATCH')
        # Preserve the explicitly selected revision, even when the same artifact
        # occurs at several revisions. Equality of code is not a rollback request.
        target = self._repository.rollback_target(candidate.candidate_id)
        return self._repository.activate(candidate, approval, target)

    def propose_rollback(self, strategy_id: str, revision: int,
                         review_ref: t.EvidenceRef) -> t.ReleaseCandidate:
        if not _token(strategy_id) or type(revision) is not int or revision < 1:
            raise ReleaseDenied('ROLLBACK_REVISION_UNKNOWN')
        matches = [r for r in self._repository.history(strategy_id) if r.revision == revision]
        if len(matches) != 1:
            raise ReleaseDenied('ROLLBACK_REVISION_UNKNOWN')
        return self._propose(t.StrategyChange(strategy_id=strategy_id,
            expected_revision=self._repository.revision(strategy_id),
            artifact=matches[0].candidate.change.artifact, review_ref=review_ref), rollback_revision=revision)


class OfflineReleaseRepository:
    """Process-local metadata for offline use only; not a durable IP-03 adapter.

    No execution configuration is switched. Real activation still requires a
    durable repository and control-plane authorization at the actual switch.
    This implementation lets offline callers exercise real atomic CAS/history
    behavior instead of implementing those invariants in a test fixture.
    """
    def __init__(self, *, environment: t.Environment):
        _environment(environment)
        self._environment = environment
        self._candidates: dict[str, t.ReleaseCandidate] = {}
        self._rollback_targets: dict[str, int | None] = {}
        self._records: dict[str, tuple[t.DeploymentRevision, ...]] = {}
        self._consumed: set[str] = set()
        self._lock = RLock()

    @property
    def environment(self) -> t.Environment:
        return self._environment

    def revision(self, strategy_id: str) -> int:
        with self._lock:
            return len(self._records.get(strategy_id, ()))

    def save_candidate(self, candidate: t.ReleaseCandidate,
                       rollback_revision: int | None = None) -> None:
        _candidate(candidate)
        refs = (candidate.change.review_ref,) + candidate.evaluation_refs
        if any(type(ref) is not t.EvidenceRef or ref.environment != self.environment for ref in refs):
            raise ReleaseDenied('CANDIDATE_ENVIRONMENT_MISMATCH')
        with self._lock:
            if candidate.candidate_id in self._candidates:
                raise ReleaseDenied('DUPLICATE_CANDIDATE')
            if self.revision(candidate.change.strategy_id) != candidate.change.expected_revision:
                raise ReleaseDenied('STALE_REVISION')
            if rollback_revision is not None and (type(rollback_revision) is not int or not any(
                    r.revision == rollback_revision and r.candidate.change.artifact == candidate.change.artifact
                    for r in self.history(candidate.change.strategy_id))):
                raise ReleaseDenied('ROLLBACK_REVISION_UNKNOWN')
            self._candidates[candidate.candidate_id] = candidate
            self._rollback_targets[candidate.candidate_id] = rollback_revision

    def candidate(self, candidate_id: str) -> t.ReleaseCandidate:
        with self._lock:
            try:
                return self._candidates[candidate_id]
            except KeyError as exc:
                raise ReleaseDenied('UNKNOWN_CANDIDATE') from exc

    def history(self, strategy_id: str) -> tuple[t.DeploymentRevision, ...]:
        with self._lock:
            return self._records.get(strategy_id, ())

    def rollback_target(self, candidate_id: str) -> int | None:
        with self._lock:
            self.candidate(candidate_id)
            return self._rollback_targets[candidate_id]

    def activate(self, candidate: t.ReleaseCandidate, approval: t.ReleaseApproval,
                 rollback_revision: int | None) -> t.DeploymentRevision:
        with self._lock:
            sid = candidate.change.strategy_id
            if (self._candidates.get(candidate.candidate_id) != candidate
                    or candidate.candidate_id in self._consumed
                    or self.revision(sid) != candidate.change.expected_revision):
                raise ReleaseDenied('STALE_OR_CONSUMED')
            if (approval.candidate_id != candidate.candidate_id
                    or approval.actor.environment != self.environment
                    or approval.authorization_id != approval.actor.authorization_id):
                raise ReleaseDenied('APPROVAL_SCOPE_INVALID')
            history = self.history(sid)
            if history and history[-1].deployment_id != approval.actor.deployment_id:
                raise ReleaseDenied('DEPLOYMENT_MISMATCH')
            if rollback_revision != self._rollback_targets[candidate.candidate_id]:
                raise ReleaseDenied('ROLLBACK_TARGET_CHANGED')
            if rollback_revision is not None and not any(
                    r.revision == rollback_revision and r.candidate.change.artifact == candidate.change.artifact
                    for r in history):
                raise ReleaseDenied('ROLLBACK_REVISION_UNKNOWN')
            record = t.DeploymentRevision(deployment_id=approval.actor.deployment_id,
                revision=len(history) + 1, candidate=candidate, approval=approval,
                rollback_revision=rollback_revision)
            self._records[sid] = history + (record,)
            self._consumed.add(candidate.candidate_id)
            return record
