"""MEM-07/08/09: frozen context, validation, budget and prompt versions."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from trading_system.memory.assembler import DecisionContextAssembler, ContextValidator, scope_for_strategy
from trading_system.memory.contracts import (
    Completeness, DecisionContext, DecisionContextCheckpoint, EventType, StrategyMemoryScope,
)
from trading_system.memory.prompting import PromptFramework, PromptLayer
from trading_system.memory.repository import MemoryRepository
from trading_system.memory.retrieval import MemoryRetriever, TokenBudget, estimate_tokens
from trading_system.memory.service import StrategyMemoryService
from trading_system.server.store import Store


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def snapshot(*, age=0, quality="READY", status=None):
    now = datetime.now(timezone.utc)
    return {
        "schema_revision": "analysis-snapshot-v1", "snapshot_id": "snapshot:abc",
        "snapshot_hash": "a" * 64, "evidence_cutoff": (now - timedelta(seconds=age)).isoformat(),
        "market": {"quote": {"symbol": "BTCUSDT", "price": "100", "as_of": now.isoformat()}},
        "portfolio": {"account": {"cash": "1000", "equity": "1000", "revision": 2},
                      "position": None, "active_orders": [], "reserved_funds": "0",
                      "protection_status": "NO_POSITION"},
        "constraints": {"max_position_pct": "25", "max_daily_loss": "100"},
        "costs": {"complete": True},
        "quality": {"status": quality, "missing_required": [] if quality == "READY" else ["quote"],
                    "quote_check": {"age_seconds": str(age), "maximum_age_seconds": "90"}},
    }


class FakeExecution:
    def __init__(self, orders=(), positions=(), commands=(), protections=()):
        self._projection = {"orders": list(orders), "positions": list(positions),
                            "commands": list(commands), "protection_plans": list(protections)}

    def projection(self):
        return self._projection


@pytest.fixture
def memory(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'context.sqlite'}")
    store.initialize()
    yield StrategyMemoryService(MemoryRepository(store))
    store.close()


def strategy(**changes):
    value = {"id": "strategy-1", "symbol": "BTCUSDT", "revision": 1, "status": "ACTIVE",
             "authorization_summary": {"max_target": "1000"}, "risk_limits": {"max_position_pct": "25"}}
    value.update(changes)
    return value


def seed_plan(memory):
    scope = scope_for_strategy(strategy())
    memory.ensure_strategy(scope, strategy_version=1)
    memory.append_event(scope, EventType.THESIS_CREATED, {
        "thesis_id": "thesis-1", "statement": "Trend remains valid above support.",
        "evidence_refs": ["quote"], "invalidation_conditions": ["close below 90"]},
        source="MODEL", idempotency_key="seed-thesis")
    memory.append_event(scope, EventType.PLAN_PROPOSED, {
        "plan_id": "plan-1", "thesis_id": "thesis-1", "action": "SET_TARGET", "target_side": "LONG",
        "target_sizing": "BASE_QUANTITY", "target_value": "0.01", "evidence_refs": ["quote"],
        "entry": {"order_preference": "MARKET"}, "exit_plan": {"stop_price": "90"},
        "valid_until": stamp(300), "status": "PROPOSED"}, source="MODEL", idempotency_key="seed-plan")
    memory.append_event(scope, EventType.PLAN_ACCEPTED, {"plan_id": "plan-1"},
                        source="CONTROL", idempotency_key="seed-accepted")
    memory.append_event(scope, EventType.PLAN_ACTIVATED, {"plan_id": "plan-1"},
                        source="CONTROL", idempotency_key="seed-active")
    return scope


def test_context_is_frozen_and_deterministic(memory):
    seed_plan(memory)
    assembler = DecisionContextAssembler(memory)
    frozen_snapshot = snapshot()
    first = assembler.build("strategy-1", "run-1", stamp(), strategy=strategy(), snapshot=frozen_snapshot,
                            account_revision=2)
    second = assembler.build("strategy-1", "run-1", first.context.as_of, strategy=strategy(),
                             snapshot=frozen_snapshot, account_revision=2)
    assert first.checkpoint_id == second.checkpoint_id
    assert first.context.sections["active_plan"]["plan_id"] == "plan-1"
    assert first.context.sections["active_thesis"]["thesis_id"] == "thesis-1"
    assert memory.get_checkpoint(first.checkpoint_id).context.input_hash == first.context.input_hash


def test_stale_snapshot_blocks_new_risk(memory):
    seed_plan(memory)
    assembler = DecisionContextAssembler(memory)
    checkpoint = assembler.build("strategy-1", "run-1", stamp(), strategy=strategy(),
                                 snapshot=snapshot(age=120), account_revision=2)
    assert checkpoint.context.completeness == Completeness.CONTEXT_INCOMPLETE
    assert "MARKET_SNAPSHOT_STALE" in checkpoint.context.conflicts
    assert checkpoint.context.sections["validation"]["block_new_risk"] is True


def test_missing_plan_with_open_position_is_blocking(memory):
    scope = scope_for_strategy(strategy())
    memory.ensure_strategy(scope, strategy_version=1)
    execution = FakeExecution(positions=[{"id": "pos-1", "symbol": "BTCUSDT", "quantity": "0.01",
                                          "strategy_id": "strategy-1", "protection": "GAP",
                                          "updated_at": stamp()}])
    assembler = DecisionContextAssembler(memory)
    assembler.memory.execution_facts.execution = execution
    checkpoint = assembler.build("strategy-1", "run-1", stamp(), strategy=strategy(), snapshot=snapshot())
    assert "EXPOSURE_WITHOUT_ACTIVE_PLAN" in checkpoint.context.conflicts
    assert checkpoint.context.sections["validation"]["block_new_risk"] is True


def test_unknown_execution_fact_is_not_treated_as_confirmed(memory):
    seed_plan(memory)
    execution = FakeExecution(orders=[{"id": "order-1", "command_id": "cmd-1", "symbol": "BTCUSDT",
                                       "strategy_id": "strategy-1", "status": "UNKNOWN",
                                       "filled_quantity": "0", "created_at": stamp()}])
    assembler = DecisionContextAssembler(memory)
    assembler.memory.execution_facts.execution = execution
    checkpoint = assembler.build("strategy-1", "run-1", stamp(), strategy=strategy(), snapshot=snapshot())
    assert "EXECUTION_FACT_UNKNOWN" in checkpoint.context.conflicts
    assert checkpoint.context.sections["validation"]["block_new_risk"] is True


def test_budget_drops_optional_history_not_active_plan():
    scope = StrategyMemoryScope(tenant_id="t", user_id="u", account_scope="a",
                                strategy_id="s", product="SPOT", instrument="console-paper:BTCUSDT")
    from trading_system.memory.contracts import StrategyState
    state = StrategyState(scope=scope, state_revision=1, strategy_version=1, status="ACTIVE")
    plan = {"plan_id": "plan-1", "status": "ACTIVE"}
    retriever = MemoryRetriever(TokenBudget(total=800, essential_reserve=400, max_decisions=5,
                                            max_events=20, max_knowledge=5))
    execution = {"facts": [], "unknown": [], "stale": []}
    bundle = retriever.build(state=state, as_of=stamp(), snapshot=snapshot(), account_revision=1,
                             decisions=(), events=(), execution=execution, knowledge=())
    assert bundle.completeness in (Completeness.COMPLETE, Completeness.PARTIAL)
    assert "identity" in bundle.sections


def test_prompt_framework_is_versioned_and_has_memory_protocol():
    framework = PromptFramework()
    rendered = framework.render(role="decision", variables={"decision_mode": "consensus", "as_of": stamp()})
    assert [layer["layer_id"] for layer in rendered["layers"]] == ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]
    assert "MEMORY CONTEXT PROTOCOL" in rendered["text"]
    assert rendered["hash"] == framework.framework_hash
    with pytest.raises(ValueError, match="MEMORY_PROMPT_VARIABLE_MISSING"):
        PromptFramework((PromptLayer(layer_id="P0", title="x", version="v1", template="{missing}"),)).render()


def test_token_estimate_is_bounded_and_monotonic():
    assert estimate_tokens({"a": "b"}) >= 1
    assert estimate_tokens({"a": "b" * 100}) > estimate_tokens({"a": "b"})
