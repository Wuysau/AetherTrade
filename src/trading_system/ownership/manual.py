"""External observations create durable risk/reconciliation work, never orders.

An observed total quantity is an account-risk fact, NOT an extra position to
sum with the existing position, and NOT a strategy target. No strategy_id from
engine events is accepted. Resolution/takeover requires a separate reviewed
control flow; repeated events or a restart cannot unlock replenishment.
"""
from dataclasses import dataclass, replace

from trading_system.contracts import types as t
from trading_system.recovery.reconciler import utc_now, valid_ref
from .registry import digest, offline


@dataclass(frozen=True, kw_only=True)
class ExternalAttestation:
    scope: t.ScopeKey
    event_hash: str
    # Source sequence is verified by the archive reader. It must be strictly
    # increasing for this management scope across all external-change streams.
    source_sequence: int


@dataclass(frozen=True, kw_only=True)
class ExternalRiskObservation:
    scope: t.ScopeKey
    classification: str
    total_observed_quantity: t.Quantity | None
    quantity_evidence: t.EvidenceRef | None
    latest_event: t.ExternalChange
    source_sequence: int
    sequence: int
    strategy_id: str | None = None
    reconciliation_required: bool = True


@dataclass(frozen=True, kw_only=True)
class InterventionRecord:
    event_hash: str
    case: t.InterventionCase
    reconciliation: t.ReconciliationCase


class ManualInterventionService:
    def __init__(self, registry, *, read_attestation=None, clock=utc_now):
        self.registry, self.read_attestation, self.clock = registry, read_attestation, clock

    def observe(self, event: t.ExternalChange) -> t.InterventionCase:
        offline(event.scope)
        now = self.clock()
        if (event.kind not in {'ORDER', 'QUANTITY', 'MODE'}
                or not valid_ref(event.evidence, event.scope, now, 60)
                or (event.kind == 'QUANTITY' and event.observed_quantity is None)):
            raise ValueError('INVALID_EXTERNAL_CHANGE')
        key = 'ip16:external:' + digest((event.scope, event.evidence.source, event.evidence.id))
        with self.registry.transaction(event.scope) as tx:
            old = tx.get('audit_events', key, InterventionRecord)
            if old:
                if old.event_hash != digest(event):
                    raise ValueError('EXTERNAL_EVENT_CONFLICT')
                return old.case
            try:
                attestation = self.read_attestation(tx, event) if self.read_attestation else None
            except Exception:
                attestation = None
            if (not isinstance(attestation, ExternalAttestation) or attestation.scope != event.scope
                    or attestation.event_hash != digest(event)
                    or type(attestation.source_sequence) is not int or attestation.source_sequence < 1):
                raise ValueError('EXTERNAL_ARCHIVE_EVIDENCE_REQUIRED')
            risk = tx.get('audit_events', 'ip16:external-risk', ExternalRiskObservation)
            if risk and attestation.source_sequence <= risk.source_sequence:
                raise ValueError('STALE_EXTERNAL_OBSERVATION')
            owner = tx.owner()
            quantity = event.observed_quantity
            expected_unit = (owner.position.actual_quantity.unit if owner else
                             risk.total_observed_quantity.unit if risk and risk.total_observed_quantity else None)
            if quantity is not None and expected_unit is not None and quantity.unit != expected_unit:
                raise ValueError('QUANTITY_UNIT_MISMATCH')
            lock = t.RiskLock(lock_id=key + ':lock', scope=event.scope, cause='EXTERNAL_CHANGE',
                state='ACTIVE', permission=t.RiskPermission.NO_NEW_RISK,
                required_evidence=('OWNERSHIP_RECONCILED', 'EXPLICIT_INTERVENTION_DECISION',
                                   'TARGET_REBASED'), raised_at=now)
            case = t.InterventionCase(case_id=key, change=event, decision='PENDING', lock=lock)
            reconcile = t.ReconciliationCase(case_id=key + ':reconcile', scope=event.scope,
                command_ids=(), position_ids=(owner.position.position_id,) if owner else (),
                missing=('EXTERNAL_CHANGE', 'POSITIONS', 'ORDERS', 'PROTECTION', 'OWNERSHIP', 'TARGET_REBASE'),
                query_budget=t.QueryBudget(maximum_requests=1, window_seconds=60,
                    minimum_interval_ms=1000, retry_after=None), attempts=0, next_query_at=now, state='OPEN')
            observation = ExternalRiskObservation(scope=event.scope, classification='EXTERNAL',
                total_observed_quantity=quantity if quantity is not None else
                    risk.total_observed_quantity if risk else None,
                quantity_evidence=event.evidence if quantity is not None else
                    risk.quantity_evidence if risk else None,
                latest_event=event, source_sequence=attestation.source_sequence,
                sequence=(risk.sequence if risk else 0) + 1)
            tx.raise_lock(lock, event.evidence)
            tx.append('reconciliation_cases', reconcile.case_id, reconcile, 0)
            tx.append('audit_events', key, InterventionRecord(event_hash=digest(event),
                case=case, reconciliation=reconcile), 0)
            tx.append('audit_events', 'ip16:external-risk', observation, risk.sequence if risk else 0)
            if owner:
                tx.save_owner(owner, replace(owner, sequence=owner.sequence + 1,
                    intervention_ids=(*owner.intervention_ids, case.case_id)))
            return case

    def risk_observation(self, scope):
        """Consumer must reconcile total exposure; unknown quantity is not zero."""
        with self.registry.transaction(scope) as tx:
            return tx.get('audit_events', 'ip16:external-risk', ExternalRiskObservation)
