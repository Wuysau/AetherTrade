"""Read-only memory observability API. No write or execution authority."""
from __future__ import annotations

from fastapi import Depends, Query

from trading_system.contracts.canonical import domain_payload
from trading_system.server.errors import ApiError
from trading_system.server.store import strategies
from sqlalchemy import select

from .assembler import scope_for_strategy


def install_memory_routes(app, memory, store, authenticated):
    def _strategy(strategy_id: str):
        with store.transaction() as connection:
            strategy = connection.execute(select(strategies.c.data).where(
                strategies.c.id == strategy_id)).scalar_one_or_none()
        if strategy is None:
            raise ApiError("STRATEGY_NOT_FOUND", "策略不存在。", 404)
        return strategy, scope_for_strategy(strategy)

    @app.get("/api/strategies/{strategy_id}/memory")
    def strategy_memory(strategy_id: str, _=Depends(authenticated)):
        strategy, scope = _strategy(strategy_id)
        state = memory.get_state(scope)
        checkpoints = memory.repository.list_checkpoints(scope, limit=1)
        checkpoint = checkpoints[0] if checkpoints else None
        validation = (checkpoint.context.sections.get("validation") or {}) if checkpoint else {}
        return {
            "strategy_id": strategy_id,
            "scope": scope.as_payload(),
            "state": domain_payload(state),
            "active_thesis": None if state.active_thesis is None else domain_payload(state.active_thesis),
            "active_plan": None if state.active_plan is None else domain_payload(state.active_plan),
            "state_revision": state.state_revision,
            "last_checkpoint": None if checkpoint is None else {
                "checkpoint_id": checkpoint.checkpoint_id,
                "created_at": checkpoint.created_at,
                "completeness": checkpoint.context.completeness.value,
                "conflicts": list(checkpoint.context.conflicts),
                "missing": list(checkpoint.context.missing),
                "token_estimate": checkpoint.context.token_estimate,
                "block_new_risk": bool(validation.get("block_new_risk")),
                "prompt_versions": checkpoint.context.prompt_versions,
                "schema_versions": checkpoint.context.schema_versions,
            },
        }

    @app.get("/api/strategies/{strategy_id}/memory/events")
    def strategy_memory_events(strategy_id: str, cursor: int = Query(0, ge=0),
                               limit: int = Query(50, ge=1, le=200), _=Depends(authenticated)):
        _, scope = _strategy(strategy_id)
        events = memory.list_events_since(scope, cursor)[:limit]
        return {"events": [{
            "event_id": event.event_id, "sequence": event.sequence,
            "event_type": event.event_type.value, "source": event.source,
            "occurred_at": event.occurred_at, "recorded_at": event.recorded_at,
            "state_revision": event.state_revision,
        } for event in events]}

    @app.get("/api/strategies/{strategy_id}/memory/decisions")
    def strategy_memory_decisions(strategy_id: str, limit: int = Query(20, ge=1, le=100),
                                  since: str | None = Query(None, max_length=40), _=Depends(authenticated)):
        _, scope = _strategy(strategy_id)
        return {"decisions": [domain_payload(item) for item in
                              memory.list_recent_decisions(scope, limit=limit, since=since)]}

    @app.get("/api/strategies/{strategy_id}/memory/knowledge")
    def strategy_memory_knowledge(strategy_id: str, _=Depends(authenticated)):
        _, scope = _strategy(strategy_id)
        return {"knowledge": [domain_payload(item) for item in
                              memory.repository.list_knowledge(scope, limit=200)]}

    @app.get("/api/strategies/{strategy_id}/memory/health")
    def strategy_memory_health(strategy_id: str, _=Depends(authenticated)):
        _, scope = _strategy(strategy_id)
        state = memory.get_state(scope)
        facts = memory.get_execution_facts(scope)
        checkpoints = memory.repository.list_checkpoints(scope, limit=1)
        checkpoint = checkpoints[0] if checkpoints else None
        return {
            "strategy_id": strategy_id, "state_revision": state.state_revision,
            "event_cursor": state.event_cursor, "status": state.status,
            "manual_action": state.manual_action,
            "execution_unknown": list(facts.get("unknown", [])),
            "execution_stale": list(facts.get("stale", [])),
            "completeness": None if checkpoint is None else checkpoint.context.completeness.value,
            "conflicts": [] if checkpoint is None else list(checkpoint.context.conflicts),
            "missing": [] if checkpoint is None else list(checkpoint.context.missing),
            "block_new_risk": bool(((checkpoint.context.sections.get("validation") or {})
                                    if checkpoint else {}).get("block_new_risk")),
            "provider_calls_replayed": False,
        }

    @app.get("/api/decisions/{run_id}/context")
    def decision_context(run_id: str, _=Depends(authenticated)):
        checkpoint = memory.repository.find_checkpoint_by_run(run_id)
        if checkpoint is None:
            raise ApiError("MEMORY_CHECKPOINT_NOT_FOUND", "未找到该决策的上下文检查点。", 404)
        return checkpoint.as_payload()
