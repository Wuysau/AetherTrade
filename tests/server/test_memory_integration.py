"""MEM-10/11 integration: manual model runs and scheduled automation share memory."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from trading_system.memory.assembler import DecisionContextAssembler, scope_for_strategy
from trading_system.memory.repository import MemoryRepository
from trading_system.memory.service import StrategyMemoryService
from trading_system.server import model_client, model_repository
from trading_system.server.automation import AutomationService, read, schedules
from trading_system.server.config import Settings
from trading_system.server.model_service import ModelService
from trading_system.server.store import Store, now
from server.test_store import market
from support.decision_fixture import PROFILE, ResearchFeed, result as assessment_result


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def snapshot():
    now = datetime.now(timezone.utc)
    return {
        "schema_revision": "analysis-snapshot-v1", "snapshot_id": "snapshot:test",
        "snapshot_hash": "b" * 64, "evidence_cutoff": now.isoformat(),
        "instrument_ref": "console-paper:BTCUSDT", "product": "SPOT",
        "evidence_refs": ["quote"], "task_profile": {
            "strategy_revision": 1, "decision_interval": "1h", "holding_period_seconds": 3600,
            "allowed_operations": ["SET_TARGET", "HOLD"], "required": ["quote", "candles:1h"],
            "optional": [], "windows": {"1h": 3}},
        "market": {"quote": {"symbol": "BTCUSDT", "price": "100", "as_of": now.isoformat()}},
        "portfolio": {"account": {"cash": "1000", "equity": "1000", "currency": "USDT", "paused": False},
                      "position": None},
        "constraints": {"maximum_target_notional": "1000", "protection_supported": True},
        "costs": {"complete": True},
        "quality": {"status": "READY", "missing_required": [],
                    "quote_check": {"age_seconds": "0", "maximum_age_seconds": "90"}},
    }


def manual_strategy():
    return {"id": "manual:BTCUSDT", "symbol": "BTCUSDT", "revision": 1, "status": "ACTIVE"}


def test_manual_assessment_records_checkpoint_thesis_plan_and_decision(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'manual.sqlite'}")
    store.initialize()
    store.setup("owner", "a-long-password", "100000", 24)
    memory = StrategyMemoryService(MemoryRepository(store))
    service = ModelService(store, tmp_path / "key", memory=memory)
    monkeypatch.setattr(service, "_clean_context", lambda symbol, context: context)
    with store.transaction() as connection:
        model_repository.write_profile(connection, "default", dict(
            model_repository.DEFAULT, id="default", name="default", model="fixture",
            enabled=True, local_mode=True, base_url="http://127.0.0.1", protocol="openai_chat"))
    assembler = DecisionContextAssembler(memory)
    context = assembler.build_from_market_context(manual_strategy(), "manual:run-1", {
        "analysis_snapshot": snapshot(), "quote": {"price": "100", "as_of": stamp()},
        "account": {"cash": "1000", "equity": "1000"}, "position": None,
        "risk": {}, "market_status": "CONNECTED", "quote_max_age_seconds": 90},
        decision_mode="single")
    request_context = {
        "analysis_snapshot": snapshot(), "quote": {"price": "100", "as_of": stamp()},
        "strategy_memory": context.context.sections,
        "strategy_prompt": assembler.prompt_for(context)["text"],
        "memory_binding": {"checkpoint_id": context.checkpoint_id, "run_id": "manual:run-1"},
        "memory_record_decision": True,
    }
    value = assessment_result("BTCUSDT", 0, "manual:run-1", request_context, "BUY")["decision"]
    value = {key: value[key] for key in ("schema_revision", "summary", "proposal",
                                         "evidence_claims", "watch_conditions", "next_review_at")}

    async def request(*args):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    monkeypatch.setattr(model_client, "request", request)
    result = asyncio.run(service.analyze("BTCUSDT", "single", ["default"], {"default": 0},
                                         "manual:run-1", request_context))
    state = memory.get_state(scope_for_strategy(manual_strategy()))
    assert result["decision"]["memory_checkpoint_id"] == context.checkpoint_id
    assert state.active_thesis is not None
    assert any(plan.status.value == "PROPOSED" for plan in state.plans)
    assert memory.list_recent_decisions(scope_for_strategy(manual_strategy()))[0].run_id == "manual:run-1"
    asyncio.run(service.close())
    store.close()


def test_automation_uses_same_assembler_and_records_decision(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'automation-memory.sqlite'}")
    store.initialize()
    store.setup("owner", "a-long-password", "100000", 24)
    memory = StrategyMemoryService(MemoryRepository(store))
    models = ModelService(store, tmp_path / "key", memory=memory)
    with store.transaction() as connection:
        model_repository.write_profile(connection, "default", dict(
            model_repository.DEFAULT, id="default", name="default", model="fixture",
            enabled=True, local_mode=True, base_url="http://127.0.0.1", protocol="openai_chat"))
    settings = Settings(testing=True, model_key_path=tmp_path / "key")
    service = AutomationService(store, models, market, settings, memory=memory)
    service.feed = ResearchFeed(lambda: service.quote_snapshot())
    saved = store.create_strategy(dict(
        name="Memory automation", symbol="BTCUSDT", mode="AI", budget="10000", target_pct="10",
        status="ACTIVE", acknowledge_model_cost=True, decision_schema="assessment_v1",
        task_profile=PROFILE, analysis_mode="single", model_profile_ids=["default"]))
    calls = []

    async def analyze(symbol, mode, identifiers, revisions, key, context, **options):
        calls.append(context)
        value = assessment_result(symbol, revisions["default"], key, context, "BUY")
        with store.transaction() as connection:
            model_repository.write_run(connection, key, dict(status="SUCCEEDED", result={"decision": value["decision"]}), new=True)
        return {"decision": value["decision"]}
    service.models.analyze = analyze
    asyncio.run(service.tick())
    assert calls and calls[0]["strategy_memory"]["active_plan"] is None
    state = memory.get_state(scope_for_strategy(saved))
    assert state.active_thesis is not None
    assert any(plan.plan_id.startswith("plan:" + state.last_decision_id + ":") or
               plan.plan_id.startswith("plan:") for plan in state.plans)
    assert memory.list_recent_decisions(scope_for_strategy(saved))
    asyncio.run(models.close())
    store.close()
