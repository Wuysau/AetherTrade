"""Unified decision-context assembler and validator.

Every decision entry point (manual single/consensus, RULE, HYBRID, program and
scheduled automation) builds the same frozen checkpoint from the same strategy
state and execution facts. The assembler is read-only with respect to trading;
it persists only the context checkpoint.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from trading_system.contracts.canonical import canonical_hash, utc_timestamp

from .contracts import (
    CHECKPOINT_SCHEMA_REVISION, CONTEXT_SCHEMA_REVISION, MEMORY_SCHEMA_REVISION,
    Completeness, DecisionContext, DecisionContextCheckpoint, PlanStatus, StrategyMemoryScope,
    StrategyState, ThesisStatus,
)
from .prompting import PromptFramework
from .retrieval import MemoryRetriever, RetrievalBundle, TokenBudget


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def scope_for_strategy(strategy: dict, *, tenant_id: str = "local", user_id: str = "owner",
                       account_scope: str = "paper-local") -> StrategyMemoryScope:
    if not isinstance(strategy, dict) or not isinstance(strategy.get("id"), str) or not isinstance(strategy.get("symbol"), str):
        raise ValueError("MEMORY_STRATEGY_REQUIRED")
    return StrategyMemoryScope(
        tenant_id=tenant_id, user_id=user_id, account_scope=account_scope,
        strategy_id=strategy["id"], product="SPOT",
        instrument="console-paper:" + strategy["symbol"])


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationResult:
    valid: bool
    blocking: bool
    block_new_risk: bool
    reasons: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


class ContextValidator:
    def validate(self, checkpoint: DecisionContextCheckpoint, *, strategy: dict | None = None,
                 snapshot: dict | None = None, execution: dict | None = None,
                 state: StrategyState | None = None) -> ValidationResult:
        if type(checkpoint) is not DecisionContextCheckpoint:
            raise ValueError("MEMORY_CHECKPOINT_REQUIRED")
        context = checkpoint.context
        reasons: list[str] = []
        missing: list[str] = []
        conflicts: list[str] = []
        blocking = False
        block_new_risk = False

        if strategy is not None:
            expected = scope_for_strategy(strategy)
            if expected != context.scope:
                reasons.append("CONTEXT_SCOPE_MISMATCH")
                blocking = True
        if state is not None:
            if state.scope != context.scope:
                reasons.append("CONTEXT_STATE_SCOPE_MISMATCH")
                blocking = True
            if state.state_revision != context.state_revision:
                reasons.append("CONTEXT_STATE_REVISION_CHANGED")
                blocking = True
            if state.strategy_version != context.strategy_version:
                reasons.append("CONTEXT_STRATEGY_VERSION_CHANGED")
                blocking = True
            if state.status == "PAUSED":
                conflicts.append("STRATEGY_PAUSED")
                block_new_risk = True
            plan = state.active_plan
            if plan is not None and plan.status in (PlanStatus.COMPLETED, PlanStatus.EXPIRED,
                                                    PlanStatus.REJECTED, PlanStatus.REVISED):
                conflicts.append("ACTIVE_PLAN_TERMINAL")
            if plan is not None and plan.valid_until and _expired(plan.valid_until, context.as_of):
                conflicts.append("ACTIVE_PLAN_EXPIRED")
                block_new_risk = True
            thesis = state.active_thesis
            if thesis is not None and thesis.status in (ThesisStatus.INVALIDATED, ThesisStatus.EXPIRED,
                                                        ThesisStatus.SUPERSEDED):
                conflicts.append("ACTIVE_THESIS_TERMINAL")
                block_new_risk = True

        if snapshot is None:
            reasons.append("MARKET_SNAPSHOT_MISSING")
            missing.append("market_snapshot")
            blocking = True
        else:
            quality = snapshot.get("quality") or {}
            if quality.get("status") != "READY":
                reasons.append("MARKET_DATA_BLOCKED")
                missing.extend(quality.get("missing_required") or [])
                blocking = True
            try:
                cutoff = utc_timestamp(snapshot.get("evidence_cutoff"))
                if cutoff > utc_timestamp(context.as_of):
                    reasons.append("CONTEXT_AS_OF_BEFORE_SNAPSHOT")
                    blocking = True
            except (ValueError, TypeError):
                reasons.append("MARKET_SNAPSHOT_TIME_INVALID")
                blocking = True
            check = quality.get("quote_check") or {}
            try:
                age, maximum = check.get("age_seconds"), check.get("maximum_age_seconds")
                if age is not None and maximum is not None and float(age) > float(maximum):
                    reasons.append("MARKET_SNAPSHOT_STALE")
                    blocking = True
                    block_new_risk = True
            except (TypeError, ValueError):
                reasons.append("MARKET_FRESHNESS_UNKNOWN")
                blocking = True
                block_new_risk = True

        if execution is None:
            missing.append("execution_facts")
        else:
            if execution.get("unknown"):
                conflicts.append("EXECUTION_FACT_UNKNOWN")
                block_new_risk = True
            if execution.get("stale"):
                conflicts.append("EXECUTION_FACT_STALE")
                block_new_risk = True
            open_position = any(fact.get("fact_type") == "POSITION" and fact.get("status") == "OPEN"
                                for fact in execution.get("facts", []) if isinstance(fact, dict))
            if open_position and state is not None and state.active_plan is None:
                conflicts.append("EXPOSURE_WITHOUT_ACTIVE_PLAN")
                block_new_risk = True

        identity = context.sections.get("identity") or {}
        for field in ("scope", "state_revision", "strategy_version", "status"):
            if field not in identity:
                missing.append("identity." + field)
                blocking = True

        if context.completeness == Completeness.CONTEXT_INCOMPLETE:
            blocking = True
        valid = not blocking
        return ValidationResult(valid=valid, blocking=blocking, block_new_risk=block_new_risk or blocking,
                                reasons=tuple(dict.fromkeys(reasons)),
                                missing=tuple(dict.fromkeys(missing)),
                                conflicts=tuple(dict.fromkeys(conflicts)))


def _expired(value: str, as_of: str) -> bool:
    try:
        return utc_timestamp(value) <= utc_timestamp(as_of)
    except (ValueError, TypeError):
        return True


class DecisionContextAssembler:
    def __init__(self, memory, *, clock=None, budget: TokenBudget | None = None,
                 prompts: PromptFramework | None = None):
        self.memory = memory
        self.clock = clock or _now
        self.budget = budget or TokenBudget()
        self.retriever = MemoryRetriever(self.budget)
        self.prompts = prompts or PromptFramework()
        self.validator = ContextValidator()

    def build(self, strategy_id: str, run_id: str, as_of: str | None = None, *,
              scope: StrategyMemoryScope | None = None, strategy: dict | None = None,
              snapshot: dict | None = None, account_revision: int = 0,
              model_versions: dict | None = None, previous_proposal: dict | None = None,
              decision_mode: str = "single") -> DecisionContextCheckpoint:
        if scope is None:
            if strategy is None:
                raise ValueError("MEMORY_SCOPE_OR_STRATEGY_REQUIRED")
            scope = scope_for_strategy(strategy)
        if strategy is not None and scope_for_strategy(strategy) != scope:
            raise ValueError("MEMORY_SCOPE_MISMATCH")
        strategy_version = int((strategy or {}).get("revision", 1) or 1)
        status = (strategy or {}).get("status", "ACTIVE")
        state = self.memory.ensure_strategy(
            scope, strategy_version=strategy_version, status=status,
            authorization_summary=(strategy or {}).get("authorization_summary") or {},
            risk_limits=(strategy or {}).get("risk_limits") or {})
        decisions = self.memory.list_recent_decisions(scope, limit=max(2, self.budget.max_decisions))
        cursor = max(0, state.event_cursor - max(4, self.budget.max_events * 2))
        events = self.memory.list_events_since(scope, cursor)
        plan = state.active_plan
        execution = self.memory.get_execution_facts(scope, plan.plan_id if plan else None)
        knowledge = self.memory.repository.list_knowledge(scope, limit=max(2, self.budget.max_knowledge * 2))
        bundle = self.retriever.build(
            state=state, as_of=as_of or self.clock(), snapshot=snapshot,
            account_revision=account_revision, decisions=decisions, events=events,
            execution=execution, knowledge=knowledge, previous_proposal=previous_proposal)
        bundle.sections.setdefault("identity", {})["decision_mode"] = decision_mode
        checkpoint = self._checkpoint(scope, run_id, as_of or self.clock(), state, snapshot,
                                      account_revision, bundle, model_versions or {})
        validation = self.validator.validate(checkpoint, strategy=strategy, snapshot=snapshot,
                                             execution=execution, state=state)
        if validation.blocking or validation.block_new_risk:
            sections = dict(bundle.sections)
            sections["validation"] = {
                "valid": validation.valid, "blocking": validation.blocking,
                "block_new_risk": validation.block_new_risk,
                "reasons": list(validation.reasons), "missing": list(validation.missing),
                "conflicts": list(validation.conflicts)}
            completeness = (Completeness.CONTEXT_INCOMPLETE
                            if validation.blocking else bundle.completeness)
            checkpoint = self._checkpoint(scope, run_id, as_of or self.clock(), state, snapshot,
                                          account_revision, bundle, model_versions or {},
                                          sections=sections, completeness=completeness,
                                          missing=tuple(dict.fromkeys(bundle.missing + validation.missing)),
                                          conflicts=tuple(dict.fromkeys(bundle.conflicts + validation.conflicts)))
        return self.memory.save_checkpoint(checkpoint)

    def _checkpoint(self, scope, run_id, as_of, state, snapshot, account_revision, bundle,
                    model_versions, *, sections=None, completeness=None, missing=None,
                    conflicts=None) -> DecisionContextCheckpoint:
        snapshot_id = (snapshot or {}).get("snapshot_id") or "missing"
        snapshot_hash = (snapshot or {}).get("snapshot_hash") or "0" * 64
        context = DecisionContext(
            run_id=run_id, scope=scope, as_of=as_of, state_revision=state.state_revision,
            strategy_version=state.strategy_version, snapshot_id=snapshot_id,
            snapshot_hash=snapshot_hash, account_revision=max(0, int(account_revision or 0)),
            sections=sections if sections is not None else bundle.sections,
            token_estimate=bundle.token_estimate,
            completeness=completeness or bundle.completeness,
            missing=missing if missing is not None else bundle.missing,
            conflicts=conflicts if conflicts is not None else bundle.conflicts,
            evidence_refs=tuple(bundle.included_refs),
            prompt_versions=self.prompts.layer_versions,
            schema_versions={"memory": MEMORY_SCHEMA_REVISION, "context": CONTEXT_SCHEMA_REVISION,
                             "assessment": "assessment_v1",
                             "analysis_snapshot": (snapshot or {}).get("schema_revision") or "missing"},
            model_versions=model_versions,
            previous_plan_id=(state.active_plan.plan_id if state.active_plan else None),
            previous_thesis_id=(state.active_thesis.thesis_id if state.active_thesis else None),
            manual_intervention_at=state.manual_intervention_at)
        return DecisionContextCheckpoint(context=context, created_at=as_of)

    def build_from_market_context(self, strategy: dict, run_id: str, context: dict, *,
                                  decision_mode: str = "single",
                                  model_versions: dict | None = None) -> DecisionContextCheckpoint:
        snapshot = context.get("analysis_snapshot")
        if snapshot is None:
            snapshot = self._legacy_snapshot(context)
        as_of = (snapshot or {}).get("evidence_cutoff") or self.clock()
        account_revision = 0
        if isinstance(snapshot, dict):
            account = (snapshot.get("portfolio") or {}).get("account") or {}
            account_revision = int(account.get("revision", 0) or 0)
        previous_proposal = context.get("previous_proposal")
        if previous_proposal is None and isinstance(snapshot, dict):
            previous_proposal = (snapshot.get("portfolio") or {}).get("previous_proposal")
        return self.build(strategy["id"], run_id, as_of, scope=scope_for_strategy(strategy),
                          strategy=strategy, snapshot=snapshot, account_revision=account_revision,
                          model_versions=model_versions, previous_proposal=previous_proposal,
                          decision_mode=decision_mode)

    def _legacy_snapshot(self, context: dict) -> dict:
        quote = context.get("quote") if isinstance(context.get("quote"), dict) else {}
        as_of = quote.get("as_of") or self.clock()
        maximum_age = context.get("quote_max_age_seconds") or 90
        try:
            age = max(0.0, (utc_timestamp(self.clock()) - utc_timestamp(as_of)).total_seconds())
        except (ValueError, TypeError):
            age = float(maximum_age) + 1
        ready = bool(quote) and quote.get("stale") is False and age <= float(maximum_age) \
            and context.get("market_status") in ("CONNECTED", "PARTIAL")
        snapshot = {
            "schema_revision": "legacy-context-v1",
            "evidence_cutoff": as_of,
            "market": {"quote": quote or None},
            "portfolio": {"account": context.get("account"),
                          "position": context.get("position"),
                          "active_orders": None, "reserved_funds": None,
                          "protection_status": "UNKNOWN"},
            "constraints": context.get("risk") or {},
            "costs": {"complete": False},
            "quality": {"status": "READY" if ready else "DATA_BLOCKED",
                        "missing_required": [] if ready else ["quote"],
                        "quote_check": {"age_seconds": str(age),
                                        "maximum_age_seconds": str(maximum_age)}},
        }
        digest = canonical_hash(snapshot)
        snapshot["snapshot_hash"] = digest
        snapshot["snapshot_id"] = "legacy:" + digest[:32]
        return snapshot

    def prompt_for(self, checkpoint: DecisionContextCheckpoint, *, role: str = "decision",
                   output_schema: str = "assessment_v1") -> dict:
        context = checkpoint.context
        identity = context.sections.get("identity") or {}
        variables = {
            "strategy_id": context.scope.strategy_id,
            "instrument": context.scope.instrument,
            "product": context.scope.product,
            "strategy_version": str(context.strategy_version),
            "decision_mode": identity.get("decision_mode", "single"),
            "authorization_summary": json.dumps(identity.get("authorization_summary", {}), sort_keys=True),
            "risk_limits": json.dumps(identity.get("risk_limits", {}), sort_keys=True),
            "memory_completeness": context.completeness.value,
            "memory_conflicts": json.dumps(list(context.conflicts)),
            "memory_missing": json.dumps(list(context.missing)),
            "output_schema": output_schema,
        }
        return self.prompts.render(role=role, variables=variables)
