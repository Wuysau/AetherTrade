"""MEM-13: restart recovery without provider replay or trade replay."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from trading_system.memory.assembler import scope_for_strategy
from trading_system.memory.contracts import EventType
from trading_system.memory.recovery import MemoryRecoveryService
from trading_system.memory.repository import MemoryRepository, state as memory_state
from trading_system.memory.service import StrategyMemoryService
from trading_system.server import model_repository
from trading_system.server.store import Store


def stamp():
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def system(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'recovery.sqlite'}")
    store.initialize()
    store.setup("owner", "a-long-password", "100000", 24)
    memory = StrategyMemoryService(MemoryRepository(store))
    saved = store.create_strategy(dict(name="Recovery", symbol="BTCUSDT", mode="AI", budget="10000",
                                       target_pct="10", status="ACTIVE", acknowledge_model_cost=True))
    yield store, memory, saved
    store.close()


def test_projection_repair_and_unfinished_run_are_reported(system):
    store, memory, saved = system
    scope = scope_for_strategy(saved)
    memory.ensure_strategy(scope, strategy_version=1)
    memory.append_event(scope, EventType.THESIS_CREATED, {
        "thesis_id": "t1", "statement": "Trend intact", "evidence_refs": ["quote"]},
        source="MODEL", idempotency_key="t1")
    with store.transaction() as connection:
        connection.execute(update(memory_state).where(memory_state.c.scope_id == scope.scope_id)
                           .values(state_hash="0" * 64))
    with store.transaction() as connection:
        model_repository.write_run(connection, "interrupted-run", dict(
            status="IN_FLIGHT", created_at=stamp(), fingerprint="x"), new=True)
    report = MemoryRecoveryService(memory).recover()
    assert report.status == "DEGRADED"
    assert scope.strategy_id in report.repaired
    assert "interrupted-run" in report.unfinished_runs
    assert report.details["provider_calls_replayed"] is False
    assert report.details["trade_writes_replayed"] is False
    assert memory.get_state(scope).thesis("t1") is not None


def test_unknown_execution_fact_blocks_new_risk_after_restart(system):
    store, memory, saved = system
    scope = scope_for_strategy(saved)
    memory.ensure_strategy(scope, strategy_version=1)

    class Execution:
        def projection(self):
            return {"orders": [{"id": "o1", "command_id": "c1", "symbol": "BTCUSDT",
                                "strategy_id": saved["id"], "status": "UNKNOWN",
                                "filled_quantity": "0", "created_at": stamp()}],
                    "commands": [], "positions": [], "protection_plans": []}
    memory.execution_facts.execution = Execution()
    report = MemoryRecoveryService(memory).recover()
    assert report.status == "DEGRADED"
    assert "o1" in report.unknown_facts
