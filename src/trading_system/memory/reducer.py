"""Deterministic strategy-state projection from append-only memory events.

The reducer is pure: it never performs I/O. Repository code persists the event and
the resulting state in one transaction and uses the event's expected
``state_revision`` as a compare-and-swap precondition.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .contracts import (
    EventType, MarketThesis, PlanStatus, StrategyMemoryEvent, StrategyMemoryScope,
    StrategyPlan, StrategyState, ThesisStatus, ValidatedKnowledge,
)


class MemoryRevisionConflict(ValueError):
    """The caller's expected state revision is stale."""


class MemoryTransitionRejected(ValueError):
    """The event would produce an illegal state transition."""


_THESIS_TRANSITIONS = {
    ThesisStatus.ACTIVE: {ThesisStatus.WEAKENED, ThesisStatus.INVALIDATED, ThesisStatus.EXPIRED, ThesisStatus.SUPERSEDED},
    ThesisStatus.WEAKENED: {ThesisStatus.ACTIVE, ThesisStatus.INVALIDATED, ThesisStatus.EXPIRED, ThesisStatus.SUPERSEDED},
    ThesisStatus.INVALIDATED: set(),
    ThesisStatus.EXPIRED: set(),
    ThesisStatus.SUPERSEDED: set(),
}

_PLAN_TRANSITIONS = {
    PlanStatus.PROPOSED: {PlanStatus.ACCEPTED, PlanStatus.REJECTED, PlanStatus.EXPIRED},
    PlanStatus.ACCEPTED: {PlanStatus.ACTIVE, PlanStatus.CANCEL_PENDING, PlanStatus.REJECTED, PlanStatus.EXPIRED},
    PlanStatus.ACTIVE: {PlanStatus.REVISED, PlanStatus.CANCEL_PENDING, PlanStatus.COMPLETED, PlanStatus.EXPIRED},
    PlanStatus.CANCEL_PENDING: {PlanStatus.COMPLETED, PlanStatus.EXPIRED, PlanStatus.REVISED},
    PlanStatus.REVISED: set(),
    PlanStatus.COMPLETED: set(),
    PlanStatus.EXPIRED: set(),
    PlanStatus.REJECTED: set(),
}

_PLAN_EVENT_STATUS = {
    EventType.PLAN_ACCEPTED: PlanStatus.ACCEPTED,
    EventType.PLAN_ACTIVATED: PlanStatus.ACTIVE,
    EventType.PLAN_CANCEL_PENDING: PlanStatus.CANCEL_PENDING,
    EventType.PLAN_REJECTED: PlanStatus.REJECTED,
    EventType.PLAN_EXPIRED: PlanStatus.EXPIRED,
    EventType.PLAN_COMPLETED: PlanStatus.COMPLETED,
}

_PLAN_EVENT_SOURCES = {
    EventType.PLAN_ACCEPTED: {"CONTROL"},
    EventType.PLAN_ACTIVATED: {"CONTROL"},
    EventType.PLAN_CANCEL_PENDING: {"CONTROL", "MANUAL", "EXECUTION_LEDGER"},
    EventType.PLAN_REJECTED: {"CONTROL"},
    EventType.PLAN_EXPIRED: {"CONTROL", "SYSTEM"},
    EventType.PLAN_COMPLETED: {"EXECUTION_LEDGER", "MANUAL", "SYSTEM"},
    EventType.PLAN_REVISED: {"CONTROL", "EXECUTION_LEDGER"},
}


def _required(payload: dict, *keys: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise MemoryTransitionRejected("MEMORY_EVENT_PAYLOAD_MISSING:" + ",".join(missing))


def _thesis_from_event(event: StrategyMemoryEvent) -> MarketThesis:
    payload = dict(event.payload)
    _required(payload, "thesis_id", "statement")
    payload.setdefault("scope", event.scope)
    payload.setdefault("created_at", event.recorded_at)
    payload.setdefault("updated_at", event.recorded_at)
    payload.setdefault("source", event.source)
    payload["scope"] = event.scope
    payload["source"] = event.source
    return MarketThesis(**payload)


def _plan_from_event(event: StrategyMemoryEvent) -> StrategyPlan:
    payload = dict(event.payload)
    _required(payload, "plan_id", "action", "target_side", "target_sizing", "target_value")
    payload.setdefault("thesis_id", None)
    payload.setdefault("scope", event.scope)
    payload.setdefault("created_at", event.recorded_at)
    payload.setdefault("updated_at", event.recorded_at)
    payload.setdefault("source", event.source)
    payload["scope"] = event.scope
    payload["source"] = event.source
    return StrategyPlan(**payload)


def _change_thesis(thesis: MarketThesis, event: StrategyMemoryEvent, forced_status: ThesisStatus | None = None) -> MarketThesis:
    payload = event.payload
    target = forced_status or ThesisStatus(payload.get("status", thesis.status))
    if target != thesis.status and target not in _THESIS_TRANSITIONS[thesis.status]:
        raise MemoryTransitionRejected("MEMORY_THESIS_TRANSITION_REJECTED")
    changes = {key: payload[key] for key in (
        "statement", "evidence_refs", "invalidation_conditions", "expires_at", "model_confidence")
        if key in payload}
    if "evidence_refs" in changes:
        changes["evidence_refs"] = tuple(changes["evidence_refs"])
    if "invalidation_conditions" in changes:
        changes["invalidation_conditions"] = tuple(changes["invalidation_conditions"])
    return replace(thesis, status=target, updated_at=event.recorded_at,
                   revision=thesis.revision + 1, **changes)


def _change_plan(plan: StrategyPlan, event: StrategyMemoryEvent, target: PlanStatus,
                 payload: dict | None = None) -> StrategyPlan:
    if target not in _PLAN_TRANSITIONS[plan.status]:
        raise MemoryTransitionRejected("MEMORY_PLAN_TRANSITION_REJECTED")
    source = event.payload if payload is None else payload
    return plan.with_status(target, updated_at=event.recorded_at,
                            authorization_ref=source.get("authorization_ref"),
                            execution_refs=tuple(source.get("execution_refs", ())))


@dataclass(frozen=True, slots=True, kw_only=True)
class RebuildReport:
    scope: StrategyMemoryScope
    events_applied: int
    state: StrategyState
    verified: bool
    reason: str | None = None


class StrategyStateReducer:
    """Apply typed events to an immutable strategy state."""

    def initial(self, scope: StrategyMemoryScope, *, strategy_version: int = 1, status: str = "DRAFT") -> StrategyState:
        return StrategyState(scope=scope, strategy_version=strategy_version, status=status)

    def apply(self, state: StrategyState, event: StrategyMemoryEvent) -> StrategyState:
        if type(state) is not StrategyState or type(event) is not StrategyMemoryEvent:
            raise ValueError("MEMORY_TYPED_STATE_AND_EVENT_REQUIRED")
        if event.scope != state.scope:
            raise MemoryTransitionRejected("MEMORY_SCOPE_MISMATCH")
        if event.schema_revision != state.schema_revision:
            raise MemoryTransitionRejected("MEMORY_SCHEMA_REVISION_MISMATCH")
        if event.state_revision != state.state_revision:
            raise MemoryRevisionConflict("MEMORY_STATE_REVISION_CONFLICT")

        theses = list(state.theses)
        plans = list(state.plans)
        knowledge = list(state.knowledge)
        active_thesis_id = state.active_thesis_id
        active_plan_id = state.active_plan_id
        status = state.status
        strategy_version = state.strategy_version
        last_decision_id = state.last_decision_id
        manual_intervention_at = state.manual_intervention_at
        manual_action = state.manual_action

        kind = event.event_type
        payload = event.payload

        if kind == EventType.STRATEGY_CREATED:
            strategy_version = payload.get("strategy_version", state.strategy_version)
            status = payload.get("status", state.status)
            if status not in ("DRAFT", "ACTIVE", "PAUSED"):
                raise MemoryTransitionRejected("MEMORY_STRATEGY_STATUS_INVALID")
        elif kind == EventType.STRATEGY_CONFIG_CHANGED:
            strategy_version = payload.get("strategy_version", state.strategy_version)
            if type(strategy_version) is not int or strategy_version < 1:
                raise MemoryTransitionRejected("MEMORY_STRATEGY_VERSION_INVALID")
            status = payload.get("status", status)
        elif kind == EventType.STRATEGY_PAUSED:
            status = "PAUSED"
        elif kind == EventType.STRATEGY_RESUMED:
            status = "ACTIVE"
        elif kind == EventType.THESIS_CREATED:
            thesis = _thesis_from_event(event)
            if any(item.thesis_id == thesis.thesis_id for item in theses):
                raise MemoryTransitionRejected("MEMORY_THESIS_ID_CONFLICT")
            theses.append(thesis)
            active_thesis_id = thesis.thesis_id
        elif kind in (EventType.THESIS_UPDATED, EventType.THESIS_INVALIDATED, EventType.THESIS_EXPIRED):
            thesis_id = payload.get("thesis_id")
            thesis = next((item for item in theses if item.thesis_id == thesis_id), None)
            if thesis is None:
                raise MemoryTransitionRejected("MEMORY_THESIS_NOT_FOUND")
            forced = None
            if kind == EventType.THESIS_INVALIDATED:
                forced = ThesisStatus.INVALIDATED
            elif kind == EventType.THESIS_EXPIRED:
                forced = ThesisStatus.EXPIRED
            changed = _change_thesis(thesis, event, forced)
            theses[theses.index(thesis)] = changed
            if changed.status in (ThesisStatus.INVALIDATED, ThesisStatus.EXPIRED, ThesisStatus.SUPERSEDED):
                if active_thesis_id == changed.thesis_id:
                    active_thesis_id = None
        elif kind == EventType.PLAN_PROPOSED:
            plan = _plan_from_event(event)
            if any(item.plan_id == plan.plan_id for item in plans):
                raise MemoryTransitionRejected("MEMORY_PLAN_ID_CONFLICT")
            if plan.supersedes_plan_id:
                old = next((item for item in plans if item.plan_id == plan.supersedes_plan_id), None)
                if old is None:
                    raise MemoryTransitionRejected("MEMORY_PLAN_SUPERSEDES_NOT_FOUND")
                if old.status not in (PlanStatus.ACTIVE, PlanStatus.ACCEPTED, PlanStatus.CANCEL_PENDING):
                    raise MemoryTransitionRejected("MEMORY_PLAN_SUPERSEDES_TERMINAL")
                plans[plans.index(old)] = old.with_status(PlanStatus.REVISED, updated_at=event.recorded_at)
            plans.append(plan)
            if plan.status == PlanStatus.ACTIVE:
                active_plan_id = plan.plan_id
        elif kind in _PLAN_EVENT_STATUS:
            plan_id = payload.get("plan_id")
            plan = next((item for item in plans if item.plan_id == plan_id), None)
            if plan is None:
                raise MemoryTransitionRejected("MEMORY_PLAN_NOT_FOUND")
            if event.source not in _PLAN_EVENT_SOURCES[kind]:
                raise MemoryTransitionRejected("MEMORY_PLAN_SOURCE_NOT_AUTHORIZED")
            changed = _change_plan(plan, event, _PLAN_EVENT_STATUS[kind])
            plans[plans.index(plan)] = changed
            if changed.status == PlanStatus.ACTIVE:
                active_plan_id = changed.plan_id
            elif changed.status in (PlanStatus.REJECTED, PlanStatus.EXPIRED, PlanStatus.COMPLETED):
                if active_plan_id == changed.plan_id:
                    active_plan_id = None
        elif kind == EventType.PLAN_REVISED:
            plan_id = payload.get("plan_id")
            plan = next((item for item in plans if item.plan_id == plan_id), None)
            if plan is None:
                raise MemoryTransitionRejected("MEMORY_PLAN_NOT_FOUND")
            if event.source not in _PLAN_EVENT_SOURCES[kind]:
                raise MemoryTransitionRejected("MEMORY_PLAN_SOURCE_NOT_AUTHORIZED")
            changed = _change_plan(plan, event, PlanStatus.REVISED)
            plans[plans.index(plan)] = changed
            if active_plan_id == changed.plan_id:
                active_plan_id = None
        elif kind == EventType.WATCH_CONDITION_TRIGGERED:
            _required(payload, "condition")
        elif kind == EventType.MANUAL_INTERVENTION:
            action = payload.get("action")
            if action not in ("PAUSE", "RESUME", "REDUCE", "EXIT", "CANCEL", "OVERRIDE"):
                raise MemoryTransitionRejected("MEMORY_MANUAL_ACTION_INVALID")
            manual_intervention_at = event.occurred_at
            manual_action = action
            if action == "PAUSE":
                status = "PAUSED"
            elif action == "RESUME":
                status = "ACTIVE"
            elif action in ("REDUCE", "EXIT", "CANCEL") and active_plan_id:
                plan = next((item for item in plans if item.plan_id == active_plan_id), None)
                if plan and plan.status in (PlanStatus.PROPOSED, PlanStatus.ACCEPTED, PlanStatus.ACTIVE):
                    plans[plans.index(plan)] = plan.with_status(
                        PlanStatus.CANCEL_PENDING, updated_at=event.recorded_at,
                        execution_refs=tuple(payload.get("execution_refs", ())))
        elif kind == EventType.DECISION_RECORDED:
            _required(payload, "run_id")
            last_decision_id = payload["run_id"]
        elif kind == EventType.EXECUTION_FACT_LINKED:
            facts = payload.get("facts", [])
            if type(facts) is not list:
                raise MemoryTransitionRejected("MEMORY_EXECUTION_FACTS_INVALID")
            for fact in facts:
                if type(fact) is not dict:
                    raise MemoryTransitionRejected("MEMORY_EXECUTION_FACTS_INVALID")
                plan_id = fact.get("plan_id")
                target = fact.get("plan_status")
                if plan_id and target:
                    plan = next((item for item in plans if item.plan_id == plan_id), None)
                    if plan is None:
                        raise MemoryTransitionRejected("MEMORY_PLAN_NOT_FOUND")
                    if event.source not in ("EXECUTION_LEDGER", "MANUAL", "SYSTEM"):
                        raise MemoryTransitionRejected("MEMORY_EXECUTION_FACT_SOURCE_INVALID")
                    try:
                        target_status = PlanStatus(target)
                    except ValueError:
                        raise MemoryTransitionRejected("MEMORY_PLAN_STATUS_INVALID") from None
                    changed = _change_plan(plan, event, target_status, payload=fact)
                    plans[plans.index(plan)] = changed
                    if changed.status == PlanStatus.ACTIVE:
                        active_plan_id = changed.plan_id
                    elif changed.status in (PlanStatus.COMPLETED, PlanStatus.EXPIRED, PlanStatus.REJECTED):
                        if active_plan_id == changed.plan_id:
                            active_plan_id = None
        elif kind == EventType.MODEL_SWITCHED:
            _required(payload, "from_model", "to_model")
        elif kind == EventType.KNOWLEDGE_PROPOSED:
            _required(payload, "knowledge")
            item = ValidatedKnowledge(**{**payload["knowledge"], "scope": event.scope})
            if any(existing.knowledge_id == item.knowledge_id for existing in knowledge):
                raise MemoryTransitionRejected("MEMORY_KNOWLEDGE_ID_CONFLICT")
            knowledge.append(item)
        elif kind in (EventType.KNOWLEDGE_VALIDATED, EventType.KNOWLEDGE_REJECTED, EventType.KNOWLEDGE_EXPIRED):
            knowledge_id = payload.get("knowledge_id")
            item = next((existing for existing in knowledge if existing.knowledge_id == knowledge_id), None)
            if item is None:
                raise MemoryTransitionRejected("MEMORY_KNOWLEDGE_NOT_FOUND")
            target = {"KNOWLEDGE_VALIDATED": "VALIDATED", "KNOWLEDGE_REJECTED": "REJECTED",
                      "KNOWLEDGE_EXPIRED": "EXPIRED"}[kind.value]
            if target == "VALIDATED" and event.source == "MODEL":
                raise MemoryTransitionRejected("MODEL_MAY_NOT_VALIDATE_KNOWLEDGE")
            changes = {"status": target, "version": item.version + 1}
            if target == "VALIDATED":
                changes.update(validated_at=event.recorded_at,
                               provenance=payload.get("provenance"),
                               sample_size=payload.get("sample_size", item.sample_size),
                               evidence_refs=tuple(payload.get("evidence_refs", item.evidence_refs)))
            knowledge[knowledge.index(item)] = replace(item, **changes)
        elif kind in (EventType.CHECKPOINT_RECORDED, EventType.PROJECTION_REBUILT):
            pass
        else:
            raise MemoryTransitionRejected("MEMORY_EVENT_UNSUPPORTED")

        updated_at = state.updated_at
        if not updated_at or event.recorded_at >= updated_at:
            updated_at = event.recorded_at
        return StrategyState(
            scope=state.scope, state_revision=state.state_revision + 1,
            schema_revision=state.schema_revision, strategy_version=strategy_version,
            status=status, active_thesis_id=active_thesis_id, active_plan_id=active_plan_id,
            last_event_id=event.event_id, last_decision_id=last_decision_id,
            event_cursor=max(state.event_cursor, event.sequence), updated_at=updated_at,
            manual_intervention_at=manual_intervention_at, manual_action=manual_action,
            theses=tuple(theses), plans=tuple(plans), knowledge=tuple(knowledge))

    def rebuild(self, scope: StrategyMemoryScope, events) -> RebuildReport:
        state = self.initial(scope)
        count = 0
        for event in events:
            if type(event) is not StrategyMemoryEvent:
                raise ValueError("MEMORY_TYPED_EVENT_REQUIRED")
            state = self.apply(state, event)
            count += 1
        return RebuildReport(scope=scope, events_applied=count, state=state, verified=True, reason=None)
