"""Offline cause locks over IP-03's append-only PostgreSQL ledger.

Resolution verifiers are trusted, local evidence readers, never model callbacks
or network calls. The default denies every resolution. No method grants orders.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.storage.events import EventStore
from trading_system.storage.models import encode, scope_id


POLICIES = {
    'MODEL_HEALTH': (t.RiskPermission.NO_NEW_RISK, ('VALID_MODEL_OUTPUT',)),
    'DAILY_LOSS': (t.RiskPermission.NO_NEW_RISK, ('DAILY_LOSS_REVIEW',)),
    'TOTAL_DRAWDOWN': (t.RiskPermission.EXIT_ONLY, ('USER_REVIEW', 'NEW_AUTHORIZATION')),
    'PROTECTION_GAP': (t.RiskPermission.EXIT_ONLY, ('PROTECTION_OR_EXIT_CONFIRMED',)),
    'EXTERNAL_CHANGE': (t.RiskPermission.NO_NEW_RISK, ('OWNERSHIP_RECONCILED',)),
    'IDENTITY_ERROR': (t.RiskPermission.RECOVERY_REQUIRED, ('IDENTITY_RECONCILED',)),
}
# These are upper bounds on actions, not execution authority. Even an exit must
# pass the execution owner's quantity, authorization and protection checks.
ACTIONS = {
    t.RiskPermission.NORMAL: frozenset({'NEW_RISK', 'MANAGE', 'EXIT', 'RECONCILE'}),
    t.RiskPermission.NO_NEW_RISK: frozenset({'MANAGE', 'EXIT', 'RECONCILE'}),
    t.RiskPermission.EXIT_ONLY: frozenset({'EXIT', 'RECONCILE'}),
    t.RiskPermission.RECOVERY_REQUIRED: frozenset({'RECONCILE'}),
}


@dataclass(frozen=True)
class ResolutionAudit:
    lock: t.RiskLock
    resolution: t.ResolutionEvidence
    resolved_at: str


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class RiskLockService:
    def __init__(self, uow, *, clock=utc_now,
                 verify: Callable | None = None, resolver_uow=None):
        self.uow = uow
        self.clock = clock
        self.verify = verify
        # Separate credentials/role for a trusted local evidence verifier only.
        # The ordinary execution-owner identity cannot resolve a risk lock.
        self.resolver_uow = resolver_uow

    def _evidence_valid(self, ref, scope, now):
        return (ref.environment == scope.environment and bool(ref.id) and bool(ref.source)
                and len(ref.hash) == 64 and all(c in '0123456789abcdef' for c in ref.hash)
                and utc_timestamp(ref.visible_at) <= utc_timestamp(now))

    def raise_lock(self, scope: t.ScopeKey, reason: str, evidence: t.EvidenceRef) -> t.RiskLock:
        now = self.clock()
        if not self._evidence_valid(evidence, scope, now):
            raise ValueError('invalid lock evidence')
        if not reason.strip():
            raise ValueError('cause required')
        permission, required = POLICIES.get(reason, (
            t.RiskPermission.RECOVERY_REQUIRED, ('UNSUPPORTED_CAUSE_REVIEW',)))
        lock = t.RiskLock(lock_id=uuid4().hex, scope=scope, cause=reason,
            state='ACTIVE', permission=permission, required_evidence=required, raised_at=now)
        with self.uow.transaction(scope) as tx:
            tx.risk_locks.append(lock, 0)
            EventStore().append(tx, self._event(lock, 1, (evidence,), now), 0)
        return lock

    @staticmethod
    def _event(lock, revision, evidence, now):
        return t.DomainEvent(event_id=uuid4().hex, scope=lock.scope,
            aggregate_id='risk-lock:' + lock.lock_id, revision=revision,
            occurred_at=now, evidence=evidence, payload=lock)

    def active(self, scope):
        with self.uow.transaction(scope) as tx:
            return tx.risk_locks.applicable(scope)

    def allowed_actions(self, scope):
        allowed = ACTIONS[t.RiskPermission.NORMAL]
        for lock in self.active(scope):
            allowed = allowed & ACTIONS.get(lock.permission, frozenset({'RECONCILE'}))
        return allowed

    def resolve(self, lock_id: str, evidence: t.ResolutionEvidence) -> t.LockResolution:
        # Scope comes from the authenticated caller's evidence, never a global
        # lock-id search. The collateral mutex covers validation and both writes.
        with (self.resolver_uow or self.uow).transaction(evidence.scope) as tx:
            active = tx.risk_locks.applicable(evidence.scope)
            lock = tx._latest('risk_locks', lock_id, t.RiskLock)
            missing = []
            now = self.clock()
            if lock is None or evidence.lock_id != lock_id:
                missing.append('LOCK_SCOPE_OR_ID_MISMATCH')
            elif lock.state == 'RESOLVED':
                return t.LockResolution(lock_id=lock_id, resolved=True,
                                        remaining_locks=active, missing=())
            else:
                if lock.cause not in POLICIES:
                    missing.append('UNSUPPORTED_CAUSE_REVIEW')
                if not evidence.evidence or any(
                    not self._evidence_valid(ref, lock.scope, now)
                    or utc_timestamp(ref.visible_at) < utc_timestamp(lock.raised_at)
                    for ref in evidence.evidence
                ):
                    missing.append('INVALID_OR_STALE_EVIDENCE')
                if lock.cause in {'DAILY_LOSS', 'TOTAL_DRAWDOWN'} and not evidence.authorization_id:
                    missing.append('AUTHORIZATION_REQUIRED')
                # A source label or arbitrary authorization string is not proof.
                # The verifier must check each obligation against authoritative
                # local records, including NEW_AUTHORIZATION issuance after raise.
                for requirement in lock.required_evidence:
                    if self.verify is None or self.verify(tx, lock, requirement, evidence) is not True:
                        missing.append(requirement)
                if self.resolver_uow is None:
                    missing.append('TRUSTED_RESOLUTION_AUTHORITY_REQUIRED')
            if missing:
                return t.LockResolution(lock_id=lock_id, resolved=False,
                                        remaining_locks=active, missing=tuple(missing))
            resolved = replace(lock, state='RESOLVED')
            EventStore().append(tx, self._event(resolved, 2, evidence.evidence, now), 1)
            # Dedicated resolution path; ordinary repository.append still rejects
            # RESOLVED. Both the event and projection commit or roll back together.
            tx.execute('SELECT ledger.resolve_risk_lock(%s,%s,%s,%s::jsonb,%s)',
                (scope_id(lock.scope), lock_id, 1, encode(resolved), tx.transaction_id))
            tx._append('audit_events', 'risk-resolution:' + lock_id,
                       ResolutionAudit(lock, evidence, now), 0)
            return t.LockResolution(lock_id=lock_id, resolved=True,
                remaining_locks=tuple(item for item in active if item.lock_id != lock_id), missing=())
