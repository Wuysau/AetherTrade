"""Bounded, relevance-ordered memory retrieval for decision contexts.

Compression order is explicit: unrelated knowledge, older decisions, then older
events. Risk boundaries, the active plan/thesis, execution/protection facts and
invalidation conditions are never dropped. If the essential set exceeds budget,
the result is CONTEXT_INCOMPLETE rather than a silently truncated context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json

from trading_system.contracts.canonical import domain_payload, utc_timestamp

from .contracts import (
    Completeness, DecisionHistoryRef, EventType, ExecutionFactRef, KnowledgeStatus,
    MarketThesis, PlanStatus, StrategyMemoryEvent, StrategyMemoryScope, StrategyPlan,
    StrategyState, ThesisStatus, ValidatedKnowledge,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenBudget:
    total: int = 6000
    essential_reserve: int = 3000
    max_decisions: int = 5
    max_events: int = 20
    max_knowledge: int = 5

    def __post_init__(self):
        if any(type(value) is not int or value < 0 for value in (
                self.total, self.essential_reserve, self.max_decisions, self.max_events, self.max_knowledge)):
            raise ValueError("MEMORY_TOKEN_BUDGET_INVALID")
        if self.total < 256 or self.essential_reserve > self.total:
            raise ValueError("MEMORY_TOKEN_BUDGET_INVALID")


@dataclass(frozen=True, slots=True, kw_only=True)
class RetrievalBundle:
    sections: dict
    token_estimate: int
    completeness: Completeness
    missing: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    included_refs: tuple[str, ...] = ()
    dropped_refs: tuple[str, ...] = ()
    block_new_risk: bool = False


def estimate_tokens(value) -> int:
    """Conservative tokenizer-neutral estimate; never a provider tokenizer claim."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return max(1, (len(encoded) + 3) // 4)


def _is_expired(value: str | None, as_of: str) -> bool:
    if not value:
        return False
    try:
        return utc_timestamp(value) <= utc_timestamp(as_of)
    except (ValueError, TypeError):
        return True


def _active_thesis(state: StrategyState, as_of: str) -> tuple[MarketThesis | None, list[str]]:
    thesis = state.active_thesis
    if thesis is None:
        return None, []
    if thesis.status not in (ThesisStatus.ACTIVE, ThesisStatus.WEAKENED):
        return None, ["THESIS_NOT_ACTIVE"]
    if _is_expired(thesis.expires_at, as_of):
        return None, ["THESIS_EXPIRED"]
    return thesis, []


def _active_plan(state: StrategyState, as_of: str) -> tuple[StrategyPlan | None, list[str]]:
    plan = state.active_plan
    if plan is None:
        return None, []
    if plan.status in (PlanStatus.COMPLETED, PlanStatus.EXPIRED, PlanStatus.REJECTED, PlanStatus.REVISED):
        return None, ["PLAN_TERMINAL"]
    if _is_expired(plan.valid_until, as_of):
        return None, ["PLAN_EXPIRED"]
    return plan, []


def _knowledge_relevant(item: ValidatedKnowledge, state: StrategyState, as_of: str) -> bool:
    if item.status != KnowledgeStatus.VALIDATED:
        return False
    if _is_expired(item.expires_at, as_of):
        return False
    if item.strategy_version != state.strategy_version:
        return False
    if item.product and item.product != state.scope.product:
        return False
    if item.instrument and item.instrument != state.scope.instrument:
        return False
    return bool(item.evidence_refs)


class MemoryRetriever:
    def __init__(self, budget: TokenBudget | None = None):
        self.budget = budget or TokenBudget()

    def build(self, *, state: StrategyState, as_of: str, snapshot: dict | None,
              account_revision: int, decisions: tuple[DecisionHistoryRef, ...],
              events: tuple[StrategyMemoryEvent, ...], execution: dict,
              knowledge: tuple[ValidatedKnowledge, ...] = (),
              previous_proposal: dict | None = None) -> RetrievalBundle:
        if type(state) is not StrategyState:
            raise ValueError("MEMORY_STATE_REQUIRED")
        missing: list[str] = []
        conflicts: list[str] = []
        included: list[str] = []
        dropped: list[str] = []

        thesis, thesis_issues = _active_thesis(state, as_of)
        plan, plan_issues = _active_plan(state, as_of)
        missing.extend(thesis_issues)
        missing.extend(plan_issues)
        if thesis_issues:
            conflicts.append("ACTIVE_THESIS_UNAVAILABLE")
        if plan_issues:
            conflicts.append("ACTIVE_PLAN_UNAVAILABLE")

        facts = [fact for fact in execution.get("facts", []) if isinstance(fact, dict)]
        unknown = list(execution.get("unknown", []))
        stale = list(execution.get("stale", []))
        open_positions = [fact for fact in facts if fact.get("fact_type") == "POSITION"
                          and fact.get("status") == "OPEN"]
        if unknown:
            conflicts.append("EXECUTION_FACT_UNKNOWN")
        if stale:
            conflicts.append("EXECUTION_FACT_STALE")
        if open_positions and plan is None:
            conflicts.append("EXPOSURE_WITHOUT_ACTIVE_PLAN")
            missing.append("ACTIVE_PLAN_REQUIRED_WITH_EXPOSURE")

        if snapshot is None:
            missing.append("MARKET_SNAPSHOT")
            conflicts.append("MARKET_SNAPSHOT_MISSING")
        else:
            quality = snapshot.get("quality") or {}
            if quality.get("status") != "READY":
                conflicts.append("MARKET_DATA_BLOCKED")
                missing.extend(quality.get("missing_required") or [])
            quote_check = quality.get("quote_check") or {}
            age = quote_check.get("age_seconds")
            maximum = quote_check.get("maximum_age_seconds")
            try:
                if age is not None and maximum is not None and float(age) > float(maximum):
                    conflicts.append("MARKET_SNAPSHOT_STALE")
                    missing.append("MARKET_SNAPSHOT_STALE")
            except (TypeError, ValueError):
                conflicts.append("MARKET_FRESHNESS_UNKNOWN")

        sections = {
            "identity": {
                "scope": state.scope.as_payload(), "state_revision": state.state_revision,
                "strategy_version": state.strategy_version, "status": state.status,
                "manual_intervention_at": state.manual_intervention_at,
                "manual_action": state.manual_action,
            },
            "market": None if snapshot is None else {
                "snapshot_id": snapshot.get("snapshot_id"), "snapshot_hash": snapshot.get("snapshot_hash"),
                "evidence_cutoff": snapshot.get("evidence_cutoff"),
                "quality": snapshot.get("quality"),
                "quote": (snapshot.get("market") or {}).get("quote"),
                "account": (snapshot.get("portfolio") or {}).get("account"),
                "position": (snapshot.get("portfolio") or {}).get("position"),
                "constraints": snapshot.get("constraints"), "costs": snapshot.get("costs"),
                "account_revision": account_revision,
            },
            "execution": execution,
            "active_thesis": None if thesis is None else domain_payload(thesis),
            "active_plan": None if plan is None else domain_payload(plan),
            "previous_decision": None if not decisions else domain_payload(decisions[0]),
            "previous_proposal": previous_proposal,
            "recent_decisions": [domain_payload(item) for item in decisions[:self.budget.max_decisions]],
            "events_since": [self._event_summary(item) for item in events[-self.budget.max_events:]],
            "knowledge": [],
        }
        for item in knowledge:
            if _knowledge_relevant(item, state, as_of):
                sections["knowledge"].append(domain_payload(item))
                if len(sections["knowledge"]) >= self.budget.max_knowledge:
                    break

        essential_keys = ("identity", "market", "execution", "active_thesis", "active_plan",
                          "previous_decision", "previous_proposal")
        essential = {key: sections[key] for key in essential_keys}
        optional = {key: sections[key] for key in ("recent_decisions", "events_since", "knowledge")}
        token_estimate = estimate_tokens(essential)
        if token_estimate > self.budget.total:
            return RetrievalBundle(
                sections=essential, token_estimate=token_estimate,
                completeness=Completeness.CONTEXT_INCOMPLETE,
                missing=tuple(dict.fromkeys(missing)), conflicts=tuple(dict.fromkeys(conflicts)),
                included_refs=tuple(included), dropped_refs=tuple(dropped),
                block_new_risk=True)

        for key in ("knowledge", "recent_decisions", "events_since"):
            values = optional[key]
            while values:
                candidate = estimate_tokens({**essential, **optional})
                if candidate <= self.budget.total:
                    break
                removed = values.pop()
                dropped.append(f"{key}:{self._ref_of(removed)}")
            optional[key] = values

        for key in ("active_thesis", "active_plan", "previous_decision", "previous_proposal"):
            if sections[key] is not None:
                included.append(f"{key}:{self._ref_of(sections[key])}")
        included.extend(f"execution:{fact.get('source_id')}" for fact in facts[:20])
        for item in optional["recent_decisions"]:
            included.append("decision:" + str(item.get("run_id")))
        for item in optional["events_since"]:
            included.append("event:" + str(item.get("event_id")))
        for item in optional["knowledge"]:
            included.append("knowledge:" + str(item.get("knowledge_id")))

        final_sections = {**essential, **optional}
        token_estimate = estimate_tokens(final_sections)
        completeness = Completeness.COMPLETE
        if missing or conflicts:
            completeness = Completeness.PARTIAL
        block_new_risk = bool(
            any(code in conflicts for code in ("EXECUTION_FACT_UNKNOWN", "EXECUTION_FACT_STALE",
                                               "MARKET_DATA_BLOCKED", "MARKET_SNAPSHOT_MISSING",
                                               "MARKET_SNAPSHOT_STALE", "EXPOSURE_WITHOUT_ACTIVE_PLAN"))
            or completeness == Completeness.CONTEXT_INCOMPLETE)
        return RetrievalBundle(
            sections=final_sections, token_estimate=token_estimate, completeness=completeness,
            missing=tuple(dict.fromkeys(missing)), conflicts=tuple(dict.fromkeys(conflicts)),
            included_refs=tuple(included), dropped_refs=tuple(dropped), block_new_risk=block_new_risk)

    @staticmethod
    def _event_summary(event: StrategyMemoryEvent) -> dict:
        return {"event_id": event.event_id, "event_type": event.event_type.value,
                "source": event.source, "occurred_at": event.occurred_at,
                "recorded_at": event.recorded_at, "state_revision": event.state_revision,
                "summary": str(event.payload.get("summary") or event.payload.get("action")
                               or event.payload.get("status") or event.event_type.value)[:240]}

    @staticmethod
    def _ref_of(value) -> str:
        if not isinstance(value, dict):
            return str(value)
        return str(value.get("plan_id") or value.get("thesis_id") or value.get("run_id")
                   or value.get("event_id") or value.get("knowledge_id") or "unknown")
