"""MEM-12/14: change-triggered review and reviewed knowledge governance."""
from datetime import datetime, timedelta, timezone

import pytest

from trading_system.memory.assembler import scope_for_strategy
from trading_system.memory.contracts import EventType, KnowledgeStatus
from trading_system.memory.knowledge import KnowledgeGovernor
from trading_system.memory.repository import MemoryRepository
from trading_system.memory.service import StrategyMemoryService
from trading_system.server.automation import AutomationService, read, schedules
from trading_system.server.config import Settings
from trading_system.server.store import Store


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def strategy(**changes):
    value = {"id": "strategy-1", "symbol": "BTCUSDT", "revision": 1, "status": "ACTIVE",
             "mode": "AI", "auto_execute": False, "analysis_interval_seconds": 300,
             "_activation_authorized": True, "_activated_at": stamp()}
    value.update(changes)
    return value


@pytest.fixture
def memory(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'governance.sqlite'}")
    store.initialize()
    store.setup("owner", "a-long-password", "100000", 24)
    yield StrategyMemoryService(MemoryRepository(store)), store
    store.close()


def test_knowledge_requires_sample_threshold_and_provenance(memory):
    service, _ = memory
    scope = scope_for_strategy(strategy())
    governor = KnowledgeGovernor(service, minimum_sample_size=10)
    item = governor.propose(scope, "Breakout works only with volume confirmation.",
                            evidence_refs=("fill-1", "fill-2"), timeframe="1h")
    with pytest.raises(ValueError, match="MEMORY_KNOWLEDGE_SAMPLE_TOO_SMALL"):
        governor.validate(item, provenance="DETERMINISTIC_STATISTICS", sample_size=3,
                          evidence_refs=("fill-1",))
    with pytest.raises(ValueError, match="MEMORY_KNOWLEDGE_VALIDATION_PROVENANCE_REQUIRED"):
        governor.validate(item, provenance="MODEL_PROPOSAL", sample_size=30,
                          evidence_refs=("fill-1",))
    validated = governor.validate(item, provenance="DETERMINISTIC_STATISTICS", sample_size=30,
                                  evidence_refs=("fill-1", "fill-2"))
    assert validated.status == KnowledgeStatus.VALIDATED
    assert governor.relevant(scope)[0].knowledge_id == item.knowledge_id


def test_expired_or_other_scope_knowledge_is_not_retrieved(memory):
    service, _ = memory
    scope = scope_for_strategy(strategy())
    governor = KnowledgeGovernor(service, minimum_sample_size=1)
    item = governor.propose(scope, "Expired rule", evidence_refs=("fill-1",), expires_at=stamp(-1))
    governor.validate(item, provenance="USER_REVIEW", sample_size=1, evidence_refs=("fill-1",))
    assert governor.relevant(scope) == ()
    other = scope_for_strategy(strategy(id="strategy-2"))
    assert governor.relevant(other) == ()


def test_review_trigger_is_idempotent_and_cursor_is_persisted(memory):
    service, store = memory
    saved = store.create_strategy(dict(name="S", symbol="BTCUSDT", mode="AI", budget="10000",
                                       target_pct="10", status="ACTIVE", acknowledge_model_cost=True))
    scope = scope_for_strategy(saved)
    service.ensure_strategy(scope, strategy_version=1)
    service.append_event(scope, EventType.PLAN_PROPOSED, {
        "plan_id": "plan-expired", "action": "SET_TARGET", "target_side": "LONG",
        "target_sizing": "BASE_QUANTITY", "target_value": "0.01", "evidence_refs": ["quote"],
        "status": "PROPOSED", "valid_until": stamp(-10)}, source="MODEL", idempotency_key="p1")
    service.append_event(scope, EventType.PLAN_ACCEPTED, {"plan_id": "plan-expired"},
                         source="CONTROL", idempotency_key="p2")
    service.append_event(scope, EventType.PLAN_ACTIVATED, {"plan_id": "plan-expired"},
                         source="CONTROL", idempotency_key="p3")
    automation = AutomationService(store, None, lambda: {}, Settings(testing=True), memory=service)
    automation._review_triggers()
    with store.transaction() as connection:
        first = read(connection, schedules, saved["id"])
    assert first["review_kind"] == "EXECUTION_REVIEW"
    assert first["next_analysis_at"]
    automation._review_triggers()
    with store.transaction() as connection:
        second = read(connection, schedules, saved["id"])
    assert second["next_analysis_at"] == first["next_analysis_at"]
    assert second["last_trigger_key"] == first["last_trigger_key"]
    assert automation._trigger_key("X", [1], {"a": 2}) == automation._trigger_key("X", [1], {"a": 2})
