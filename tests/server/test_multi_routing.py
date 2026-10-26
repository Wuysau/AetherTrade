"""Strategy routing uses selected model snapshots and never weakens PAPER guards."""
import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from trading_system.server import model_repository as repository
from trading_system.server.automation import AutomationService, jobs, put, read
from trading_system.server.errors import ApiError
from trading_system.server.store import Store, now, stamp, strategies
from trading_system.server.validation import StrategyCreate
from server.test_automation import dashboard, due, fake, patch, strategy, system
from server.test_store import market


def profile(service, name):
    return service.models.create_config(dict(name=name, protocol="openai_chat", model=name,
                                             base_url="http://127.0.0.1:18083/v1", local_mode=True, enabled=True))


def analyze_fake(service, *, action="BUY", consensus="AGREED", callback=None):
    calls = []

    async def analyze(symbol, mode, identifiers, revisions, key, context):
        calls.append(dict(symbol=symbol, analysis_mode=mode, model_profile_ids=identifiers,
                          profile_revisions=revisions, key=key))
        if callback:
            await callback()
        decision = dict(id=key, symbol=symbol, action=action, summary="Controlled model result",
                        risk_status="NOT_EVALUATED", created_at=stamp(), analysis_mode=mode,
                        model_profile_ids=identifiers, profile_revisions=revisions)
        if mode == "consensus":
            decision.update(consensus_status=consensus, model_results=[
                dict(profile_id=identifier, profile_name=identifier, model="fixture", config_revision=revisions[identifier],
                     status="FAILED" if consensus == "INCOMPLETE" and index else "SUCCEEDED",
                     decision=dict(symbol=symbol, action="SELL" if consensus == "DISAGREED" and index else action))
                for index, identifier in enumerate(identifiers)
            ])
        result = dict(decision=decision, usage=None)
        with service.store.transaction() as connection:
            repository.write_run(connection, key, dict(status="SUCCEEDED", result=result), new=True)
        return result

    service.models.analyze = analyze
    return calls


def test_legacy_binding_and_explicit_empty_draft(system):
    saved = strategy(system, status="DRAFT", acknowledge_model_cost=False)
    assert saved["analysis_mode"] == "single" and saved["model_profile_ids"] == ["default"]
    empty = strategy(system, name="Incomplete", status="DRAFT", model_profile_ids=[])
    assert empty["model_profile_ids"] == []
    with pytest.raises(ApiError):
        patch(system, empty, status="ACTIVE", acknowledge_model_cost=True)
    with pytest.raises(ValidationError):
        StrategyCreate(name="Null binding", symbol="BTCUSDT", mode="AI", budget="100", target_pct="1", model_profile_ids=None)


def test_binding_validation_and_active_cost_acknowledgement(system):
    second = profile(system, "Second")
    third = profile(system, "Third")
    invalid = [dict(analysis_mode="bad"), dict(model_profile_ids=["missing"]),
               dict(model_profile_ids=["default", second["id"]]),
               dict(analysis_mode="consensus", model_profile_ids=["default"]),
               dict(analysis_mode="consensus", model_profile_ids=["default", "default"]),
               dict(analysis_mode="consensus", model_profile_ids=["default", second["id"], third["id"], "missing"])]
    for fields in invalid:
        with pytest.raises(ApiError):
            strategy(system, **fields)
    saved = strategy(system)
    with pytest.raises(ApiError) as exc:
        patch(system, saved, model_profile_ids=[second["id"]])
    assert exc.value.code == "MODEL_COST_ACK_REQUIRED"
    changed = patch(system, saved, model_profile_ids=[second["id"]], acknowledge_model_cost=True)
    assert changed["model_profile_ids"] == [second["id"]]
    assert patch(system, changed, name="Preserved binding")["model_profile_ids"] == [second["id"]]
    hybrid = strategy(system, mode="HYBRID", status="DRAFT", analysis_mode="consensus", model_profile_ids=["default", second["id"]])
    assert hybrid["status"] == "DRAFT"
    with pytest.raises(ApiError):
        patch(system, hybrid, status="ACTIVE", acknowledge_model_cost=True)
    with pytest.raises(ApiError):
        strategy(system, mode="RULE", status="DRAFT", model_profile_ids=[second["id"]])
    rule = strategy(system, mode="RULE", status="DRAFT")
    assert rule["model_profile_ids"] == []


def test_strategies_route_independently_and_claim_snapshots(system):
    second = profile(system, "Second")
    first_strategy = strategy(system, name="Default")
    second_strategy = strategy(system, name="Independent", model_profile_ids=[second["id"]])
    calls = analyze_fake(system)
    asyncio.run(system.tick())
    asyncio.run(system.tick())
    assert [call["model_profile_ids"] for call in calls] == [["default"], [second["id"]]]
    with system.store.transaction() as connection:
        saved_jobs = list(connection.execute(select(jobs.c.data)).scalars())
    assert {job["strategy_id"] for job in saved_jobs} == {first_strategy["id"], second_strategy["id"]}
    assert all(job["analysis_mode"] == "single" for job in saved_jobs)
    assert [job["profile_revisions"] for job in saved_jobs] == [{"default": 1}, {second["id"]: 1}]


@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("disable", [True, False])
@pytest.mark.parametrize("mode", ["single", "consensus"])
def test_only_selected_profile_changes_during_await_prevent_fill(system, selected, disable, mode):
    second, unrelated = profile(system, "Second"), profile(system, "Unrelated")
    strategy(system, auto_execute=True, analysis_mode=mode,
             model_profile_ids=[second["id"]] if mode == "single" else ["default", second["id"]])
    edited = second if selected else unrelated

    async def revise():
        system.models.save_config(dict(expected_revision=edited["revision"], **({"enabled": False} if disable else {"model": "edited"})), edited["id"])

    analyze_fake(system, callback=revise)
    asyncio.run(system.tick())
    result = dashboard(system)
    assert len(result["orders"]) == (0 if selected else 1)
    assert result["decisions"][0]["execution_status"] == ("SKIPPED" if selected else "FILLED")


@pytest.mark.parametrize("status,action,orders", [("AGREED", "BUY", 1), ("AGREED", "HOLD", 0),
                                                  ("DISAGREED", "BUY", 0), ("INCOMPLETE", "BUY", 0)])
def test_consensus_execution_requires_complete_direction_agreement(system, status, action, orders):
    second = profile(system, "Second")
    strategy(system, analysis_mode="consensus", model_profile_ids=["default", second["id"]], auto_execute=True)
    calls = analyze_fake(system, action=action, consensus=status)
    asyncio.run(system.tick())
    result = dashboard(system)
    assert len(calls) == 1 and calls[0]["analysis_mode"] == "consensus"
    assert len(result["orders"]) == orders
    decision = result["decisions"][0]
    assert len(decision["model_results"]) == 2
    notification = result["notifications"]["items"][0]
    assert notification["decision_id"] == decision["id"]
    if status == "INCOMPLETE":
        assert notification["kind"] == "AI_ERROR" and notification["severity"] == "warning"
        assert "失败" in notification["message"] or "不完整" in notification["message"]
    if status == "DISAGREED":
        assert "分歧" in notification["message"] and notification["severity"] != "success"


def test_consensus_selected_profile_edit_and_rebinding_skip(system):
    second = profile(system, "Second")
    saved = strategy(system, analysis_mode="consensus", model_profile_ids=["default", second["id"]], auto_execute=True)

    async def rebind():
        patch(system, saved, analysis_mode="single", model_profile_ids=["default"], acknowledge_model_cost=True)

    analyze_fake(system, callback=rebind)
    asyncio.run(system.tick())
    assert dashboard(system)["orders"] == []
    assert dashboard(system)["decisions"][0]["execution_status"] == "SKIPPED"


@pytest.mark.parametrize("status", ["DISAGREED", "INCOMPLETE"])
def test_non_agreed_result_cannot_execute_under_single_binding(system, status):
    strategy(system, auto_execute=True)
    analyze_fake(system)
    analyze = system.models.analyze

    async def inconsistent(*args):
        result = await analyze(*args)
        result["decision"]["consensus_status"] = status
        return result

    system.models.analyze = inconsistent
    asyncio.run(system.tick())
    assert dashboard(system)["orders"] == []


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "failed", "different_revision", "different_direction"])
def test_agreed_label_does_not_override_missing_or_invalid_participant(system, invalid):
    second = profile(system, "Second")
    strategy(system, analysis_mode="consensus", model_profile_ids=["default", second["id"]], auto_execute=True)
    analyze_fake(system)
    analyze = system.models.analyze

    async def inconsistent(*args):
        result = await analyze(*args)
        outcomes = result["decision"]["model_results"]
        if invalid == "missing":
            outcomes.pop()
        elif invalid == "duplicate":
            outcomes[1] = dict(outcomes[0])
        elif invalid == "failed":
            outcomes[1]["status"] = "FAILED"
        elif invalid == "different_revision":
            outcomes[1]["config_revision"] += 1
        else:
            outcomes[1]["decision"]["action"] = "SELL"
        return result

    system.models.analyze = inconsistent
    asyncio.run(system.tick())
    result = dashboard(system)
    assert result["orders"] == []
    assert result["decisions"][0]["consensus_status"] in ("DISAGREED", "INCOMPLETE")


def test_selected_profile_change_before_dispatch_is_not_called(system):
    second = profile(system, "Second")
    strategy(system, model_profile_ids=[second["id"]], auto_execute=True)
    calls = analyze_fake(system)
    original = system.store.analysis_context

    def context(*args):
        value = original(*args)
        system.models.save_config(dict(expected_revision=1, enabled=False), second["id"])
        return value

    system.store.analysis_context = context
    asyncio.run(system.tick())
    assert calls == [] and dashboard(system)["orders"] == []


def test_selected_unavailable_profile_waits_without_default_fallback(system):
    second = profile(system, "Second")
    strategy(system, model_profile_ids=[second["id"]])
    calls = analyze_fake(system)
    system.models.save_config(dict(expected_revision=1, enabled=False), second["id"])
    asyncio.run(system.tick())
    assert calls == []
    assert dashboard(system)["strategies"][0]["runtime_status"] == "WAITING_MODEL"
    assert system.notifications()["unread_count"] == 0


@pytest.mark.parametrize("directions,expected_status,order_count", [
    (("BUY", "BUY"), "AGREED", 1), (("HOLD", "HOLD"), "AGREED", 0),
    (("BUY", "SELL"), "DISAGREED", 0), (("BUY", "FAIL"), "INCOMPLETE", 0),
])
def test_real_consensus_service_controls_paper_execution(system, directions, expected_status, order_count):
    system.models.save_config(dict(expected_revision=1, protocol="openai_chat"))
    second = profile(system, "Second")
    strategy(system, analysis_mode="consensus", model_profile_ids=["default", second["id"]], auto_execute=True)
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        action = directions[0 if body["model"] == "fixture" else 1]
        if action == "FAIL":
            return httpx.Response(503, json={"error": "controlled unavailable"})
        advice = dict(symbol="BTCUSDT", action=action, summary="Controlled response", counter_evidence=["Risk"], invalidation="Change")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(advice)}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    system.models.client = client
    try:
        asyncio.run(system.tick())
        result = dashboard(system)
        assert len(requests) == 2 and len(result["decisions"]) == 1
        decision = result["decisions"][0]
        assert decision["consensus_status"] == expected_status
        assert len(decision["model_results"]) == 2
        assert len(result["orders"]) == order_count
        assert result["notifications"]["items"][0]["decision_id"] == decision["id"]
    finally:
        asyncio.run(client.aclose())


def test_consensus_sell_respects_strategy_position_and_disabled_default_does_not_block_other(system):
    second, third = profile(system, "Second"), profile(system, "Third")
    saved = strategy(system, model_profile_ids=[second["id"]], auto_execute=True)
    system.models.save_config(dict(expected_revision=1, enabled=False))
    analyze_fake(system)
    asyncio.run(system.tick())
    assert len(dashboard(system)["orders"]) == 1
    saved = patch(system, saved, analysis_mode="consensus", model_profile_ids=[second["id"], third["id"]], acknowledge_model_cost=True)
    due(system, saved)
    analyze_fake(system, action="SELL")
    asyncio.run(system.tick())
    assert len(dashboard(system)["orders"]) == 2
    assert dashboard(system)["positions"] == []


@pytest.mark.parametrize("guard", ["permission", "paused", "stale", "lease"])
def test_consensus_keeps_permission_risk_quote_and_lease_guards(system, guard):
    second = profile(system, "Second")
    strategy(system, analysis_mode="consensus", model_profile_ids=["default", second["id"]], auto_execute=guard != "permission")

    async def change():
        if guard == "paused":
            system.store.action(dict(action="pause_new", expected_revision=1), "pause", market(), 90)
        elif guard == "stale":
            system.quote_snapshot = lambda: market(stale=True)
        elif guard == "lease":
            with system.store.transaction() as connection:
                job = connection.execute(select(jobs.c.data)).scalar_one()
                job["lease_until"] = (now() - timedelta(seconds=1)).isoformat()
                put(connection, jobs, job["id"], job)

    analyze_fake(system, callback=change)
    asyncio.run(system.tick())
    result = dashboard(system)
    assert result["orders"] == []
    assert result["decisions"][0]["execution_status"] == {"permission": "NOT_ENABLED", "paused": "BLOCKED", "stale": "BLOCKED", "lease": "SKIPPED"}[guard]
    assert result["notifications"]["unread_count"] == 1


def test_saved_consensus_recovers_after_restart_without_execution_or_resend(system):
    second = profile(system, "Second")
    strategy(system, analysis_mode="consensus", model_profile_ids=["default", second["id"]], auto_execute=True)
    calls = analyze_fake(system)
    job = system._claim(system.models.list_configs(), market())
    context = system.store.analysis_context(job["symbol"], market(), 90)
    result = asyncio.run(system.models.analyze(job["symbol"], job["analysis_mode"], job["model_profile_ids"], job["profile_revisions"], job["model_key"], context))
    with system.store.transaction() as connection:
        job["lease_until"] = (now() - timedelta(seconds=1)).isoformat()
        put(connection, jobs, job["id"], job)
    other_store = Store(system.store.engine.url.render_as_string(hide_password=False))
    try:
        other_store.initialize()
        restarted = AutomationService(other_store, system.models, market, system.settings)
        asyncio.run(restarted.tick())
        asyncio.run(restarted.tick())
        system._finish(job, result)
        assert len(calls) == 1 and dashboard(system)["orders"] == []
        notifications = restarted.notifications()
        assert notifications["unread_count"] == 1
        assert notifications["items"][0]["decision_id"] == result["decision"]["id"]
        with other_store.transaction() as connection:
            recovered = read(connection, jobs, job["id"])
        assert recovered["execution_status"] == "SKIPPED"
        assert recovered["consensus_status"] == "AGREED" and len(recovered["model_results"]) == 2
    finally:
        other_store.close()


def test_migration_preserves_legacy_strategy_status_revision_and_default_ciphertext(system):
    system.models.save_config(dict(expected_revision=1, api_key="controlled-fixture-key-not-real"))
    draft = strategy(system, status="DRAFT", acknowledge_model_cost=False)
    active = strategy(system, name="Existing authorized")
    legacy = strategy(system, name="Legacy without consent")
    identifiers = (draft["id"], active["id"], legacy["id"])
    with system.store.transaction() as connection:
        for identifier in identifiers:
            value = read(connection, strategies, identifier)
            value.pop("analysis_mode", None)
            value.pop("model_profile_ids", None)
            if identifier == legacy["id"]:
                value.pop("_activation_authorized", None)
                value.pop("_activated_at", None)
            put(connection, strategies, identifier, value)
        config = repository.read_config(connection)
        before = dict(config)
        assert before["encrypted_key"] and before["revision"] == 2
    system.store.initialize()
    system.store.initialize()
    with system.store.transaction() as connection:
        migrated = [read(connection, strategies, identifier) for identifier in identifiers]
        assert repository.read_config(connection) == before
    assert all(item["model_profile_ids"] == ["default"] and item["analysis_mode"] == "single" for item in migrated)
    assert [item["status"] for item in migrated] == ["DRAFT", "ACTIVE", "ACTIVE"]
    assert [item["revision"] for item in migrated] == [1, 1, 1]
    assert not migrated[0].get("_activation_authorized")
    assert migrated[1]["_activation_authorized"] is True
    assert not migrated[2].get("_activation_authorized")


def test_legacy_default_claim_and_restarted_recovery_never_resends(system):
    strategy(system, auto_execute=True)
    calls = fake(system)
    job = system._claim(system.models.get_config(), market())
    with system.store.transaction() as connection:
        for key in ("analysis_mode", "model_profile_ids", "profile_revisions"):
            job.pop(key, None)
        job["lease_until"] = (now() - timedelta(seconds=1)).isoformat()
        put(connection, jobs, job["id"], job)
    other_store = Store(system.store.engine.url.render_as_string(hide_password=False))
    try:
        other_store.initialize()
        restarted = AutomationService(other_store, system.models, market, system.settings)
        asyncio.run(restarted.tick())
        asyncio.run(restarted.tick())
        assert calls == [] and dashboard(system)["orders"] == []
        assert restarted.notifications()["unread_count"] == 1
    finally:
        other_store.close()
