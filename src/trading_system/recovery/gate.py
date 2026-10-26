"""Fail-closed recovery evidence gate, never an execution permission issuer."""
from dataclasses import dataclass
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.risk.locks import ACTIONS
from trading_system.recovery.reconciler import utc_now, valid_ref


REQUIREMENTS = {
    'unique_process': frozenset({'EXCLUSIVE_OWNER', 'CURRENT_EPOCH', 'OLD_OWNER_FENCED'}),
    'commands': frozenset({'ALL_COMMANDS_RECONCILED', 'NO_UNKNOWN_COMMANDS'}),
    'funds': frozenset({'BALANCES_RECONCILED', 'RESERVATIONS_RECONCILED'}),
    'private_stream': frozenset({'AUTHENTICATED', 'SUBSCRIBED', 'SNAPSHOT_SYNCHRONIZED', 'GAP_FREE'}),
    'positions': frozenset({'CURRENT_POSITIONS_RECONCILED', 'OWNERSHIP_RECONCILED'}),
    'protection': frozenset({'CURRENT_PROTECTION_VERIFIED'}),
    'authorization': frozenset({'VALID_AUTHORIZATION', 'CURRENT_SCOPE_AND_EPOCH'}),
}


@dataclass(frozen=True, kw_only=True)
class RecoveryEvidence:
    """Trusted local reader output, not a client-supplied authorization DTO.

    Reader must bind each obligation to current deployment/ownership/execution
    epochs and durable, archived evidence, and recheck revocation on every call.
    All records must describe a coherent current recovery snapshot, including
    account/product locks inherited by this scope. No production reader is
    defaulted; callers cannot treat cached flags or client DTOs as this reader.
    """
    ref: t.EvidenceRef
    scope: t.ScopeKey
    kind: str
    verified: tuple[str, ...]
    valid_until: str
    economic_history_complete: bool = False


@dataclass(frozen=True, kw_only=True)
class RecoveryAssessment:
    verdict: t.RecoveryVerdict
    allow_new_risk: bool
    allow_manage_current: bool
    performance_complete: bool


class RecoveryGate:
    def __init__(self, *, read_evidence: Callable | None = None,
                 read_locks: Callable | None = None, clock: Callable = utc_now,
                 maximum_age_seconds: int = 60):
        if type(maximum_age_seconds) is not int or maximum_age_seconds <= 0:
            raise ValueError('positive evidence age required')
        self.read_evidence = read_evidence
        self.read_locks = read_locks
        self.clock = clock
        self.maximum_age_seconds = maximum_age_seconds

    def evaluate(self, checklist: t.RecoveryChecklist) -> t.RecoveryVerdict:
        return self.assess(checklist).verdict

    def assess(self, checklist: t.RecoveryChecklist) -> RecoveryAssessment:
        now = self.clock()
        missing = []
        verified = {}
        if checklist.scope.environment not in (t.Environment.RESEARCH, t.Environment.PAPER):
            missing.append('ENVIRONMENT_NOT_ENABLED')
        for kind, requirements in REQUIREMENTS.items():
            ref = getattr(checklist, kind)
            record = None
            if ref is not None and valid_ref(ref, checklist.scope, now, self.maximum_age_seconds):
                try:
                    record = self.read_evidence(checklist.scope, kind, ref) if self.read_evidence else None
                    valid = (isinstance(record, RecoveryEvidence) and record.ref == ref
                             and record.scope == checklist.scope and record.kind == kind
                             and utc_timestamp(record.valid_until) > utc_timestamp(now)
                             and requirements.issubset(record.verified))
                except Exception:
                    valid = False
            else:
                valid = False
            if valid:
                verified[kind] = record
            else:
                missing.append(kind.upper() + '_EVIDENCE_REQUIRED')
        for case in checklist.unresolved_cases:
            if case.scope != checklist.scope:
                missing.append('CASE_SCOPE_MISMATCH')
            elif case.state != 'RESOLVED':
                missing.append('UNRESOLVED_CASE:' + case.case_id)
        # The authoritative commands reader must enumerate unknowns itself;
        # omission from unresolved_cases is never proof of their absence.
        locks = ()
        try:
            if self.read_locks is None:
                raise ValueError('lock reader missing')
            supplied = tuple(self.read_locks(checklist.scope))
            if any(not isinstance(lock, t.RiskLock) or lock.scope != checklist.scope
                   or lock.state not in {'ACTIVE', 'RESOLVED'} for lock in supplied):
                raise ValueError('invalid risk lock snapshot')
            locks = tuple(lock for lock in supplied if lock.state == 'ACTIVE')
        except Exception:
            missing.append('RISK_LOCKS_UNVERIFIED')
        allowed = ACTIONS[t.RiskPermission.NORMAL]
        for lock in locks:
            allowed = allowed & ACTIONS.get(lock.permission, frozenset({'RECONCILE'}))
        current_verified = not missing
        ready = current_verified and not locks
        if locks:
            missing.append('ACTIVE_RISK_LOCKS')
        history = (all(kind in verified for kind in ('funds', 'positions'))
                   and verified['funds'].economic_history_complete is True
                   and verified['positions'].economic_history_complete is True)
        verdict = t.RecoveryVerdict(ready=ready, missing=tuple(dict.fromkeys(missing)), risk_locks=locks)
        # These are recovery eligibility hints only. Every proposed action still
        # needs quantity/ownership/authorization and the sole durable send gate.
        return RecoveryAssessment(verdict=verdict, allow_new_risk=ready and 'NEW_RISK' in allowed,
            allow_manage_current=current_verified and 'MANAGE' in allowed,
            performance_complete=history)
