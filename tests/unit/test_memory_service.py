"""MEM-04/05/06: decision history, execution-fact linkage and memory service."""
from datetime import datetime, timedelta, timezone

import pytest

from trading_system.memory.assembler import scope_for_strategy
from trading_system.memory.contracts import (
    DecisionContext, DecisionContextCheckpoint, KnowledgeStatus, PlanStatus, ValidatedKnowledge,
)
from trading_system.memory.repository import MemoryRepository
from trading_system.memory.service import StrategyMemoryService
from trading_system.server.store import Store


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def strategy(**changes):
    value = {"id": "strategy-1", "symbol": "BTCUSDT", "revision": 1, "status": "ACTIVE"}
    value.update(changes)
    return value


def proposal(**changes):
    value = {"contract_version": "2026-10-20", "proposal_id": "p1", "instrument_ref": "console-paper:BTCUSDT",
             "action": "SET_TARGET", "target": {"side": "LONG", "sizing": "BASE_QUANTITY", "value": "0.01"},
             "entry": {"order_preference": "MARKET", "limit_price": None},
             "exit_plan": {"stop_price": "90", "thesis_invalidation": "close below support"},
             "evidence_refs": ["quote"], "valid_until": stamp(300)}
    value.update(changes)
    return value


def decision(**changes):
    value = {"assessment_status": "DECIDED", "action": "SET_TARGET", "summary": "Trend intact above support.",
             "proposal": proposal(), "evidence_claims": [{"evidence_ref": "quote"}],
             "invalidation": "close below support", "created_at": stamp()}
    value.update(changes)
    return value


@pytest.fixture
def memory(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'service.sqlite'}")
    store.initialize()
    yield StrategyMemoryService(MemoryRepository(store))
    store.close()


def checkpoint(memory, run_id="run-1"):
    scope = scope_for_strategy(strategy())
    context = DecisionContext(run_id=run_id, scope=scope, as_of=stamp(), state_revision=0,
                              strategy_version=1, snapshot_id="snapshot:1", snapshot_hash="a" * 64,
                              account_revision=1, sections={}, token_estimate=1,
                              completeness="COMPLETE")
    return DecisionContextCheckpoint(context=context, created_at=stamp())


def test_record_decision_creates_thesis_and_proposed_plan(memory):
    scope = scope_for_strategy(strategy())
    frozen = checkpoint(memory)
    state = memory.record_decision(frozen, decision())
    assert state.active_thesis is not None
    assert state.thesis(state.active_thesis.thesis_id).statement.startswith("Trend intact")
    plan = next(item for item in state.plans if item.status == PlanStatus.PROPOSED)
    assert plan.target_value == "0.01" and plan.thesis_id == state.active_thesis.thesis_id
    refs = memory.list_recent_decisions(scope)
    assert refs and refs[0].run_id == "run-1" and refs[0].checkpoint_id == frozen.checkpoint_id
    assert memory.get_active_plan(scope) is None  # proposals are not active plans


def test_stale_context_does_not_create_thesis_or_plan(memory):
    scope = scope_for_strategy(strategy())
    frozen = checkpoint(memory)
    memory.record_manual_intervention(scope, "PAUSE", "manual-stale")
    state = memory.record_decision(frozen, decision(memory_context_stale=True))
    assert state.theses == () and state.plans == ()
    ref = memory.list_recent_decisions(scope)[0]
    assert ref.memory_state_revision == frozen.context.state_revision


def test_execution_accepts_and_completes_plan(memory):
    scope = scope_for_strategy(strategy())
    state = memory.record_decision(checkpoint(memory), decision())
    plan = next(item for item in state.plans if item.status == PlanStatus.PROPOSED)
    memory.record_execution_outcome(scope, plan.plan_id, "PENDING", command_id="cmd-1")
    assert memory.get_active_plan(scope).status == PlanStatus.ACTIVE
    memory.record_execution_outcome(scope, plan.plan_id, "FILLED", order_id="order-1")
    assert memory.get_active_plan(scope) is None
    assert memory.get_state(scope).plan(plan.plan_id).status == PlanStatus.COMPLETED


def test_manual_intervention_cancels_model_plan(memory):
    scope = scope_for_strategy(strategy())
    state = memory.record_decision(checkpoint(memory), decision())
    plan = next(item for item in state.plans if item.status == PlanStatus.PROPOSED)
    memory.record_execution_outcome(scope, plan.plan_id, "PENDING", command_id="cmd-1")
    memory.record_manual_intervention(scope, "REDUCE", "manual-1")
    state = memory.get_state(scope)
    assert state.manual_action == "REDUCE"
    assert state.plan(plan.plan_id).status == PlanStatus.CANCEL_PENDING


def test_unknown_execution_facts_are_marked_not_confirmed(memory):
    class Execution:
        def projection(self):
            return {"orders": [{"id": "o1", "command_id": "c1", "symbol": "BTCUSDT",
                                "strategy_id": "strategy-1", "status": "UNKNOWN",
                                "filled_quantity": "0", "created_at": stamp()}],
                    "commands": [], "positions": [], "protection_plans": []}
    memory.execution_facts.execution = Execution()
    summary = memory.get_execution_facts(scope_for_strategy(strategy()))
    assert summary["unknown"] == ["o1"]
    assert summary["authority"] == "EXECUTION_LEDGER"


def test_model_cannot_validate_knowledge(memory):
    scope = scope_for_strategy(strategy())
    item = ValidatedKnowledge(knowledge_id="k1", scope=scope, statement="Small sample",
                              status=KnowledgeStatus.PROPOSED, sample_size=2,
                              evidence_refs=("fill-1",), proposed_at=stamp())
    memory.propose_knowledge(item)
    with pytest.raises(ValueError, match="MEMORY_KNOWLEDGE_VALIDATION_PROVENANCE_REQUIRED"):
        memory.validate_knowledge(item, provenance="MODEL_PROPOSAL", sample_size=2,
                                  evidence_refs=("fill-1",))
    state = memory.validate_knowledge(item, provenance="DETERMINISTIC_STATISTICS", sample_size=30,
                                      evidence_refs=("fill-1", "fill-2"))
    assert state.knowledge[0].status == KnowledgeStatus.VALIDATED


def test_model_switch_does_not_reset_state(memory):
    scope = scope_for_strategy(strategy())
    memory.record_decision(checkpoint(memory), decision())
    before = memory.get_state(scope).state_revision
    memory.record_model_switch(scope, "gpt", "claude", run_id="run-2")
    state = memory.get_state(scope)
    assert state.state_revision == before + 1
    assert state.active_thesis is not None
    assert state.active_plan is None
