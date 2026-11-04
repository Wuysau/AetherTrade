"""Unified strategy memory service.

Reads are scope-filtered. Writes go through the controlled event service; model
output can only create PROPOSED plans and model-sourced thesis updates. Orders,
fills, positions and fees remain owned by the execution ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from trading_system.contracts.canonical import domain_payload

from .contracts import (
    DecisionContextCheckpoint, DecisionHistoryRef, EventType, ExecutionFactRef, KnowledgeStatus,
    MarketThesis, PlanStatus, StrategyMemoryEvent, StrategyMemoryScope, StrategyPlan,
    StrategyState, ThesisStatus, ValidatedKnowledge,
)
from .execution_facts import ExecutionFactProjector
from .reducer import StrategyStateReducer
from .repository import EventReceipt, MemoryRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value, fallback="") -> str:
    if not isinstance(value, str):
        return fallback
    return " ".join(value.replace("\x00", " ").split())[:4000]


class StrategyMemoryService:
    def __init__(self, repository: MemoryRepository, *, execution=None, clock=None,
                 reducer: StrategyStateReducer | None = None):
        self.repository = repository
        self.clock = clock or _now
        self.reducer = reducer or StrategyStateReducer()
        self.execution_facts = ExecutionFactProjector(execution, clock=self.clock)

    # ---- lifecycle / controlled writes ---------------------------------

    def ensure_strategy(self, scope: StrategyMemoryScope, *, strategy_version: int = 1,
                        status: str = "ACTIVE", authorization_summary: dict | None = None,
                        risk_limits: dict | None = None) -> StrategyState:
        state = self.repository.load_state(scope)
        if state is None:
            return self.append_event(
                scope, EventType.STRATEGY_CREATED,
                {"strategy_version": strategy_version, "status": status,
                 "authorization_summary": authorization_summary or {},
                 "risk_limits": risk_limits or {}},
                source="CONTROL", idempotency_key=f"strategy-created:{scope.scope_id}:{strategy_version}")
        if state.strategy_version != strategy_version:
            return self.append_event(
                scope, EventType.STRATEGY_CONFIG_CHANGED,
                {"strategy_version": strategy_version, "status": status,
                 "authorization_summary": authorization_summary or {},
                 "risk_limits": risk_limits or {}},
                source="CONTROL",
                idempotency_key=f"strategy-config:{scope.scope_id}:{strategy_version}")
        return state

    def append_event(self, scope: StrategyMemoryScope, event_type: EventType | str, payload: dict,
                     *, source: str, idempotency_key: str, occurred_at: str | None = None,
                     recorded_at: str | None = None) -> StrategyState:
        if type(scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        current = self.repository.load_state(scope) or self.reducer.initial(scope)
        occurred = occurred_at or self.clock()
        recorded = recorded_at or self.clock()
        event = StrategyMemoryEvent(
            event_id=uuid4().hex, scope=scope, event_type=event_type, source=source,
            occurred_at=occurred, recorded_at=recorded, idempotency_key=idempotency_key,
            state_revision=current.state_revision, payload=payload, sequence=0)
        self.repository.append_event(scope, event, idempotency_key)
        return self.repository.load_state(scope) or current

    def record_manual_intervention(self, scope: StrategyMemoryScope, action: str, source_id: str,
                                   *, execution_refs: tuple[str, ...] = (),
                                   occurred_at: str | None = None) -> StrategyState:
        return self.append_event(
            scope, EventType.MANUAL_INTERVENTION,
            {"action": action, "source_id": source_id, "execution_refs": list(execution_refs)},
            source="MANUAL", idempotency_key=f"manual:{scope.scope_id}:{source_id}",
            occurred_at=occurred_at)

    def record_model_switch(self, scope: StrategyMemoryScope, from_model: str, to_model: str,
                            *, run_id: str) -> StrategyState:
        return self.append_event(
            scope, EventType.MODEL_SWITCHED, {"from_model": from_model, "to_model": to_model,
                                              "run_id": run_id},
            source="CONTROL", idempotency_key=f"model-switch:{scope.scope_id}:{run_id}")

    # ---- decision history ----------------------------------------------

    def record_decision(self, checkpoint: DecisionContextCheckpoint, decision: dict, *,
                        model_provider: str | None = None, model_name: str | None = None,
                        prompt_revision: str | None = None, schema_revision: str | None = None,
                        policy_revision: str | None = None) -> StrategyState:
        if type(checkpoint) is not DecisionContextCheckpoint:
            raise ValueError("MEMORY_CHECKPOINT_REQUIRED")
        scope = checkpoint.context.scope
        run_id = checkpoint.context.run_id
        self.ensure_strategy(scope, strategy_version=checkpoint.context.strategy_version)
        state = self.repository.load_state(scope)
        created_at = str(decision.get("created_at") or self.clock())
        status = str(decision.get("assessment_status") or decision.get("status") or "UNKNOWN")
        proposal = decision.get("proposal") if isinstance(decision.get("proposal"), dict) else None
        action = decision.get("action") or (proposal or {}).get("action")
        thesis_id = None
        plan_id = None
        if status == "DECIDED" and isinstance(decision.get("summary"), str) and decision["summary"].strip():
            thesis_id = self._record_thesis(scope, state, decision, run_id, created_at)
            if proposal is not None and action in ("SET_TARGET", "UPDATE_EXIT", "REDUCE", "EXIT"):
                plan_id = self._record_plan(scope, state, decision, proposal, thesis_id, run_id, created_at)
        current = self.repository.load_state(scope) or state
        ref = DecisionHistoryRef(
            run_id=run_id, scope=scope, created_at=created_at, decision_status=status,
            action=action if action in ("BUY", "SELL", "HOLD", "SET_TARGET", "UPDATE_EXIT", "REDUCE", "EXIT") else None,
            model_provider=model_provider, model_name=model_name, prompt_revision=prompt_revision,
            schema_revision=schema_revision, policy_revision=policy_revision,
            snapshot_id=checkpoint.context.snapshot_id, snapshot_hash=checkpoint.context.snapshot_hash,
            memory_state_revision=current.state_revision, checkpoint_id=checkpoint.checkpoint_id,
            thesis_id=thesis_id or current.active_thesis_id, plan_id=plan_id,
            execution_status=decision.get("execution_status"))
        return self.append_event(
            scope, EventType.DECISION_RECORDED, {"run_id": run_id, "decision": domain_payload(ref)},
            source="CONTROL", idempotency_key=f"decision:{scope.scope_id}:{run_id}",
            occurred_at=created_at)

    def _record_thesis(self, scope, state, decision, run_id, created_at) -> str | None:
        evidence = self._evidence_refs(decision)
        if not evidence:
            return None
        invalidation = self._invalidation(decision)
        statement = _text(decision.get("summary"))
        if not statement:
            return None
        active = state.active_thesis
        if active is None:
            thesis = MarketThesis(
                thesis_id=f"thesis:{run_id}", scope=scope, statement=statement,
                status=ThesisStatus.ACTIVE, evidence_refs=tuple(evidence),
                invalidation_conditions=tuple(invalidation), created_at=created_at,
                updated_at=created_at, source="MODEL")
            self.append_event(scope, EventType.THESIS_CREATED, domain_payload(thesis),
                              source="MODEL", idempotency_key=f"thesis:{scope.scope_id}:{run_id}",
                              occurred_at=created_at)
            return thesis.thesis_id
        target = ThesisStatus.WEAKENED if decision.get("action") in ("EXIT", "REDUCE") else ThesisStatus.ACTIVE
        self.append_event(scope, EventType.THESIS_UPDATED, {
            "thesis_id": active.thesis_id, "statement": statement, "status": target.value,
            "evidence_refs": list(evidence), "invalidation_conditions": list(invalidation)},
            source="MODEL", idempotency_key=f"thesis:{scope.scope_id}:{run_id}", occurred_at=created_at)
        return active.thesis_id

    def _record_plan(self, scope, state, decision, proposal, thesis_id, run_id, created_at) -> str:
        target = proposal.get("target") or {}
        exit_plan = proposal.get("exit_plan") if isinstance(proposal.get("exit_plan"), dict) else {}
        plan = StrategyPlan(
            plan_id=f"plan:{run_id}:{proposal.get('proposal_id') or 'main'}", scope=scope,
            thesis_id=thesis_id or state.active_thesis_id, status=PlanStatus.PROPOSED,
            action=str(proposal.get("action") or "SET_TARGET"),
            target_side=str(target.get("side") or "FLAT"),
            target_sizing=str(target.get("sizing") or "BASE_QUANTITY"),
            target_value=str(target.get("value") or "0"),
            entry=proposal.get("entry") if isinstance(proposal.get("entry"), dict) else {},
            exit_plan=exit_plan,
            evidence_refs=tuple(self._evidence_refs(decision)),
            invalidation_conditions=tuple(self._invalidation(decision)),
            valid_until=proposal.get("valid_until"), created_at=created_at,
            updated_at=created_at, source="MODEL", source_proposal=dict(proposal))
        self.append_event(scope, EventType.PLAN_PROPOSED, domain_payload(plan),
                          source="MODEL", idempotency_key=f"plan:{scope.scope_id}:{run_id}",
                          occurred_at=created_at)
        return plan.plan_id

    @staticmethod
    def _evidence_refs(decision) -> list[str]:
        proposal = decision.get("proposal") if isinstance(decision.get("proposal"), dict) else {}
        refs = list(proposal.get("evidence_refs") or ())
        refs.extend(claim.get("evidence_ref") for claim in decision.get("evidence_claims", [])
                    if isinstance(claim, dict) and claim.get("evidence_ref"))
        result = []
        for ref in refs:
            if isinstance(ref, str) and ref and ref not in result:
                result.append(ref)
        return result

    @staticmethod
    def _invalidation(decision) -> list[str]:
        values = []
        if isinstance(decision.get("invalidation"), str) and decision["invalidation"].strip():
            values.append(_text(decision["invalidation"]))
        proposal = decision.get("proposal") if isinstance(decision.get("proposal"), dict) else {}
        exit_plan = proposal.get("exit_plan") if isinstance(proposal.get("exit_plan"), dict) else {}
        if isinstance(exit_plan.get("thesis_invalidation"), str) and exit_plan["thesis_invalidation"].strip():
            values.append(_text(exit_plan["thesis_invalidation"]))
        return list(dict.fromkeys(values))

    def record_execution_outcome(self, scope: StrategyMemoryScope, plan_id: str | None, status: str,
                                 *, order_id: str | None = None, command_id: str | None = None,
                                 run_id: str | None = None,
                                 authorization_ref: str | None = None) -> StrategyState:
        state = self.repository.load_state(scope)
        if state is None:
            return self.ensure_strategy(scope)
        plan = state.plan(plan_id) if plan_id else state.active_plan
        status = str(status).upper()
        key = f"execution:{scope.scope_id}:{plan_id or 'active'}:{status}:{order_id or command_id or run_id or ''}"
        facts = self.execution_facts.facts(scope, plan_id=plan.plan_id if plan else plan_id)
        fact_payload = [domain_payload(fact) for fact in facts]
        if plan is not None:
            if status in ("PENDING", "ACCEPTED", "ADMITTED"):
                if plan.status == PlanStatus.PROPOSED:
                    self.append_event(scope, EventType.PLAN_ACCEPTED,
                                      {"plan_id": plan.plan_id, "authorization_ref": authorization_ref,
                                       "run_id": run_id}, source="CONTROL",
                                      idempotency_key=key + ":accepted")
                current = self.repository.load_state(scope)
                current_plan = current.plan(plan.plan_id) if current else None
                if current_plan is not None and current_plan.status == PlanStatus.ACCEPTED:
                    self.append_event(scope, EventType.PLAN_ACTIVATED, {"plan_id": plan.plan_id},
                                      source="CONTROL", idempotency_key=key + ":active")
            elif status in ("FILLED", "COMPLETED"):
                if facts:
                    self._link_execution(scope, fact_payload, plan.plan_id, "COMPLETED", key + ":filled", run_id)
                else:
                    self.append_event(scope, EventType.PLAN_COMPLETED,
                                      {"plan_id": plan.plan_id, "run_id": run_id,
                                       "execution_ref": order_id or command_id or run_id},
                                      source="EXECUTION_LEDGER", idempotency_key=key + ":filled")
            elif status in ("CANCELED", "CANCELLED"):
                if facts:
                    self._link_execution(scope, fact_payload, plan.plan_id, "COMPLETED", key + ":canceled", run_id)
                else:
                    self.append_event(scope, EventType.PLAN_COMPLETED,
                                      {"plan_id": plan.plan_id, "run_id": run_id,
                                       "execution_ref": order_id or command_id or run_id},
                                      source="EXECUTION_LEDGER", idempotency_key=key + ":canceled")
            elif status == "EXPIRED":
                if facts:
                    self._link_execution(scope, fact_payload, plan.plan_id, "EXPIRED", key + ":expired", run_id)
                else:
                    self.append_event(scope, EventType.PLAN_EXPIRED,
                                      {"plan_id": plan.plan_id, "run_id": run_id},
                                      source="SYSTEM", idempotency_key=key + ":expired")
            elif status in ("BLOCKED", "REJECTED", "INVALID_PLAN", "MODEL_ERROR"):
                if plan.status in (PlanStatus.PROPOSED, PlanStatus.ACCEPTED):
                    self.append_event(scope, EventType.PLAN_REJECTED,
                                      {"plan_id": plan.plan_id, "reason": status, "run_id": run_id},
                                      source="CONTROL", idempotency_key=key + ":rejected")
            elif status in ("PARTIAL", "PARTIALLY_FILLED", "PENDING_FILL"):
                self._link_execution(scope, fact_payload, plan.plan_id, None, key + ":partial", run_id)
            elif status == "NO_ACTION":
                if plan.status in (PlanStatus.PROPOSED, PlanStatus.ACCEPTED):
                    self.append_event(scope, EventType.PLAN_REJECTED,
                                      {"plan_id": plan.plan_id, "reason": "NO_ACTION", "run_id": run_id},
                                      source="CONTROL", idempotency_key=key + ":no-action")
        return self.repository.load_state(scope) or state

    def _link_execution(self, scope, facts, plan_id, plan_status, key, run_id):
        payload = {"facts": facts, "run_id": run_id}
        if plan_status:
            payload["facts"] = [dict(fact, plan_id=plan_id, plan_status=plan_status) for fact in facts]
        self.append_event(scope, EventType.EXECUTION_FACT_LINKED, payload,
                          source="EXECUTION_LEDGER", idempotency_key=key)

    # ---- reads ----------------------------------------------------------

    def get_state(self, scope: StrategyMemoryScope) -> StrategyState:
        return self.repository.load_state(scope) or self.reducer.initial(scope)

    def get_active_thesis(self, scope: StrategyMemoryScope) -> MarketThesis | None:
        return self.get_state(scope).active_thesis

    def get_active_plan(self, scope: StrategyMemoryScope) -> StrategyPlan | None:
        return self.get_state(scope).active_plan

    def list_recent_decisions(self, scope: StrategyMemoryScope, *, limit: int = 20,
                              since: str | None = None) -> tuple[DecisionHistoryRef, ...]:
        return self.repository.list_decisions(scope, limit=limit, since=since)

    def list_events_since(self, scope: StrategyMemoryScope, cursor: int = 0):
        return self.repository.list_events_since(scope, cursor)

    def get_execution_facts(self, scope: StrategyMemoryScope, plan_id: str | None = None) -> dict:
        return self.execution_facts.summary(scope, plan_id=plan_id)

    def save_checkpoint(self, checkpoint: DecisionContextCheckpoint) -> DecisionContextCheckpoint:
        return self.repository.save_checkpoint(checkpoint)

    def get_checkpoint(self, checkpoint_id: str):
        return self.repository.load_checkpoint(checkpoint_id)

    # ---- knowledge governance ------------------------------------------

    def propose_knowledge(self, item: ValidatedKnowledge) -> StrategyState:
        if item.status != KnowledgeStatus.PROPOSED:
            raise ValueError("MEMORY_KNOWLEDGE_MUST_START_PROPOSED")
        return self.append_event(
            item.scope, EventType.KNOWLEDGE_PROPOSED, {"knowledge": domain_payload(item)},
            source="MODEL", idempotency_key=f"knowledge-proposed:{item.scope.scope_id}:{item.knowledge_id}:{item.version}",
            occurred_at=item.proposed_at)

    def validate_knowledge(self, item: ValidatedKnowledge, *, provenance: str, sample_size: int,
                           evidence_refs: tuple[str, ...]) -> StrategyState:
        if provenance not in ("DETERMINISTIC_STATISTICS", "USER_REVIEW"):
            raise ValueError("MEMORY_KNOWLEDGE_VALIDATION_PROVENANCE_REQUIRED")
        if sample_size < 1 or not evidence_refs:
            raise ValueError("MEMORY_KNOWLEDGE_VALIDATION_EVIDENCE_REQUIRED")
        return self.append_event(
            item.scope, EventType.KNOWLEDGE_VALIDATED,
            {"knowledge_id": item.knowledge_id, "provenance": provenance,
             "sample_size": sample_size, "evidence_refs": list(evidence_refs)},
            source="CONTROL",
            idempotency_key=f"knowledge-validated:{item.scope.scope_id}:{item.knowledge_id}:{item.version}")
