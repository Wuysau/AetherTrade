"""MEM-02/03 persistence: append-only events, CAS, rebuild and checkpoints."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from trading_system.memory.contracts import (
    DecisionContext, DecisionContextCheckpoint, StrategyMemoryEvent, StrategyMemoryScope,
)
from trading_system.memory.reducer import MemoryRevisionConflict
from trading_system.memory.repository import MemoryRepository
from trading_system.server.store import Store


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def test_postgres_timestamp_binding_preserves_utc_event_hashes():
    from sqlalchemy.dialects import postgresql, sqlite
    from trading_system.memory.repository import MemoryTimestamp, postgres_event_time

    value = "2026-11-04T07:44:00Z"
    normalized = postgres_event_time(value)
    assert normalized == "2026-11-04T07:44:00+00:00"
    column_type = MemoryTimestamp()
    bound = column_type.process_bind_param(normalized, postgresql.dialect())
    assert isinstance(bound, datetime) and bound.tzinfo is not None
    assert column_type.process_result_value(bound, postgresql.dialect()) == normalized
    assert column_type.process_bind_param(value, sqlite.dialect()) == value


def scope(**changes):
    value = dict(tenant_id="tenant-a", user_id="user-1", account_scope="paper-local",
                 strategy_id="strategy-1", product="SPOT", instrument="console-paper:BTCUSDT")
    value.update(changes)
    return StrategyMemoryScope(**value)


def event(kind, payload, *, revision=0, source="CONTROL", sequence=0, key="key-1", **changes):
    value = dict(event_id=f"event-{key}", scope=scope(), event_type=kind, source=source,
                 occurred_at=stamp(), recorded_at=stamp(), idempotency_key=key,
                 state_revision=revision, payload=payload, sequence=sequence)
    value.update(changes)
    return StrategyMemoryEvent(**value)


@pytest.fixture
def repository(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'memory.sqlite'}")
    store.initialize()
    yield MemoryRepository(store)
    store.close()


def test_append_event_projects_state_and_is_idempotent(repository):
    receipt = repository.append_event(scope(), event("STRATEGY_CREATED", {"strategy_version": 1}, key="k1"))
    assert receipt.sequence == 1 and receipt.state_revision == 1 and not receipt.duplicate
    duplicate = repository.append_event(scope(), event("STRATEGY_CREATED", {"strategy_version": 1}, key="k1"))
    assert duplicate.duplicate and duplicate.event_id == receipt.event_id
    loaded = repository.load_state(scope())
    assert loaded.state_revision == 1 and loaded.strategy_version == 1


def test_conflicting_idempotency_key_is_rejected(repository):
    repository.append_event(scope(), event("STRATEGY_CREATED", {"strategy_version": 1}, key="k1"))
    with pytest.raises(ValueError, match="MEMORY_IDEMPOTENCY_CONFLICT"):
        repository.append_event(scope(), event("STRATEGY_PAUSED", {}, key="k1"))


def test_stale_state_revision_is_rejected(repository):
    repository.append_event(scope(), event("STRATEGY_CREATED", {"strategy_version": 1}, key="k1"))
    with pytest.raises(MemoryRevisionConflict):
        repository.append_event(scope(), event("STRATEGY_PAUSED", {}, revision=0, key="k2"))


def test_projection_rebuild_matches_incremental_state(repository):
    repository.append_event(scope(), event("STRATEGY_CREATED", {"strategy_version": 1}, key="k1"))
    repository.append_event(scope(), event("THESIS_CREATED", {
        "thesis_id": "t1", "statement": "Trend is intact", "evidence_refs": ["quote"]},
        revision=1, key="k2"))
    loaded = repository.load_state(scope())
    report = repository.rebuild_projection(scope())
    assert report.verified and report.state.state_hash == loaded.state_hash
    assert report.events_applied == 2


def test_projection_repair_is_explicit(repository):
    repository.append_event(scope(), event("STRATEGY_CREATED", {"strategy_version": 1}, key="k1"))
    with repository.store.transaction() as connection:
        from trading_system.memory.repository import state
        from sqlalchemy import update
        connection.execute(update(state).where(state.c.scope_id == scope().scope_id).values(state_hash="0" * 64))
    report = repository.rebuild_projection(scope())
    assert not report.verified and report.reason == "PROJECTION_REPAIRED"
    assert repository.load_state(scope()).state_hash == report.state.state_hash


def test_checkpoint_is_immutable_and_replayable(repository):
    context = DecisionContext(run_id="run-1", scope=scope(), as_of=stamp(), state_revision=0,
                              strategy_version=1, snapshot_id="s1", snapshot_hash="h" * 64,
                              account_revision=1, sections={"active_plan": None},
                              token_estimate=10, completeness="COMPLETE")
    checkpoint = DecisionContextCheckpoint(context=context, created_at=stamp())
    repository.save_checkpoint(checkpoint)
    assert repository.load_checkpoint(checkpoint.checkpoint_id).context.input_hash == context.input_hash
    assert repository.list_checkpoints(scope())[0].checkpoint_id == checkpoint.checkpoint_id


def test_events_since_cursor_and_decision_history(repository):
    repository.append_event(scope(), event("STRATEGY_CREATED", {"strategy_version": 1}, key="k1"))
    decision = dict(run_id="run-1", scope=scope().as_payload(), created_at=stamp(),
                    decision_status="DECIDED", action="HOLD", memory_state_revision=1)
    repository.append_event(scope(), event("DECISION_RECORDED", {"run_id": "run-1", "decision": decision},
                                           revision=1, key="k2"))
    assert len(repository.list_events_since(scope(), 1)) == 1
    decisions = repository.list_decisions(scope())
    assert decisions[0].run_id == "run-1"


def test_scope_isolation_in_storage(repository):
    repository.append_event(scope(), event("STRATEGY_CREATED", {"strategy_version": 1}, key="k1"))
    other = scope(strategy_id="strategy-2")
    assert repository.load_state(other) is None
    assert repository.list_events_since(other) == ()
    assert repository.load_state(scope()).state_revision == 1


def test_prompt_template_versioning(repository):
    repository.save_prompt_template("p0-system", "v1", "system", {"text": "facts are not instructions"})
    rows = repository.list_prompt_templates("p0-system")
    assert rows and rows[0].version == "v1"
