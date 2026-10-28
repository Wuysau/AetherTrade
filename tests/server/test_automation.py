import asyncio
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from trading_system.server.automation import AutomationService, jobs, schedules, read, put
from trading_system.server.config import Settings
from trading_system.server.errors import ApiError
from trading_system.server.model_service import ModelService
from trading_system.server import model_repository as repository
from trading_system.server.store import Store, now, stamp, positions
from server.test_store import market


@pytest.fixture
def system(tmp_path, request):
    harness = None
    provider = [market]
    if request.node.get_closest_marker('paper_runtime'):
        from support.unified_console import provision_console
        harness = provision_console(tmp_path, market_snapshot=lambda: provider[0]())
        store = harness.store
    else:
        store = Store(f"sqlite:///{tmp_path / 'automation.sqlite'}")
        store.initialize()
        store.setup("owner", "a-long-password", "100000", 24)
    model = ModelService(store, tmp_path / "test-only.key")
    model.save_config(dict(expected_revision=0, model="fixture", base_url="http://127.0.0.1:18083/v1", local_mode=True, enabled=True))
    service = AutomationService(store, model, market, Settings(testing=True, model_key_path=tmp_path / "test-only.key"))
    service.test_console = harness
    provider[0] = lambda: service.quote_snapshot()
    yield service
    asyncio.run(model.close())
    if harness:
        harness.close()
    else:
        store.close()


def complete_pending(service):
    """Explicit synthetic controller events on real PG, separate from advice."""
    assert service.test_console is not None
    return service.test_console.fill_pending(service.quote_snapshot())


def strategy(service, **changes):
    payload = dict(name="Test AI", symbol="BTCUSDT", mode="AI", budget="10000", target_pct="10", status="ACTIVE", acknowledge_model_cost=True)
    payload.update(changes)
    return service.store.create_strategy(payload)


def patch(service, saved, **changes):
    return service.store.update_strategy(saved["id"], dict(expected_revision=saved["revision"], **changes))


def fake(service, action="BUY", callback=None):
    calls = []
    async def generate(symbol, revision, key, context):
        calls.append(key)
        if callback:
            await callback()
        decision = dict(id=key, symbol=symbol, action=action, summary="Fixture advice", risk_status="NOT_EVALUATED", created_at=stamp(),
                        data_as_of=context['quote']['as_of'])
        result = {"decision": decision}
        with service.store.transaction() as connection:
            repository.write_run(connection, key, dict(status="SUCCEEDED", result=result), new=True)
        return result
    service.models.generate = generate
    return calls


def due(service, saved):
    with service.store.transaction() as connection:
        schedule = read(connection, schedules, saved["id"])
        schedule["next_analysis_at"] = (now() - timedelta(seconds=1)).isoformat()
        put(connection, schedules, saved["id"], schedule)


def dashboard(service):
    result = service.store.dashboard(service.quote_snapshot())
    result["decisions"] = service.models.decisions()
    result["model_config"] = service.models.get_config()
    return service.enrich_dashboard(result)


def test_legacy_draft_and_explicit_consent(system):
    calls = fake(system)
    strategy(system, status="DRAFT", acknowledge_model_cost=False)
    asyncio.run(system.tick())
    assert calls == []
    with pytest.raises(ApiError) as exc:
        strategy(system, acknowledge_model_cost=False)
    assert exc.value.code == "MODEL_COST_ACK_REQUIRED"
    for changes in [dict(mode="RULE"), dict(auto_execute=True, status="DRAFT"), dict(analysis_interval_seconds=59), dict(analysis_interval_seconds=True)]:
        with pytest.raises(ApiError):
            strategy(system, **changes)


def test_advice_only_periodic_and_notification_reads(system):
    calls = fake(system)
    saved = strategy(system)
    asyncio.run(system.tick())
    asyncio.run(system.tick())
    result = dashboard(system)
    assert len(calls) == 1 and result["orders"] == []
    assert result["decisions"][0]["execution_status"] == "NOT_ENABLED"
    assert result["decisions"][0]["risk_status"] == "NOT_EVALUATED"
    assert result["notifications"]["unread_count"] == 1
    notification = result["notifications"]["items"][0]
    assert system.mark_read({"ids": [notification["id"]]})["unread_count"] == 0
    due(system, saved)
    asyncio.run(system.tick())
    assert len(calls) == 2 and calls[0] != calls[1]
    assert system.mark_read({"all": True})["unread_count"] == 0
    for bad in [{}, {"all": False}, {"all": 1}, {"ids": "bad"}, {"all": True, "ids": []}]:
        with pytest.raises(ApiError):
            system.mark_read(bad)


@pytest.mark.paper_runtime
def test_buy_target_includes_fees_and_does_not_accumulate(system):
    fake(system)
    saved = strategy(system, auto_execute=True)
    asyncio.run(system.tick())
    assert dashboard(system)["orders"], [d.get("execution_reason") for d in dashboard(system)["decisions"]]
    assert dashboard(system)["orders"][0]["status"] == "PENDING"
    complete_pending(system)
    first = dashboard(system)
    assert len(first["orders"]) == 1
    order = first["orders"][0]
    assert Decimal(order["notional"]) + Decimal(order["fee"]) <= 10000
    assert first["decisions"][0]["risk_status"] == "PAPER_PASSED"
    due(system, saved)
    asyncio.run(system.tick())
    assert len(dashboard(system)["orders"]) == 1
    assert dashboard(system)["decisions"][0]["execution_status"] == "NO_ACTION"


@pytest.mark.parametrize("change", [dict(status="PAUSED"), dict(auto_execute=False), dict(budget="9000")])
def test_permission_revision_during_await_prevents_fill(system, change):
    saved = strategy(system, auto_execute=True)
    async def mutation():
        # This executes a transaction while generate is awaiting: no held account lock.
        await asyncio.to_thread(patch, system, saved, **change)
    fake(system, callback=mutation)
    asyncio.run(system.tick())
    result = dashboard(system)
    assert result["orders"] == []
    assert result["decisions"][0]["execution_status"] == "SKIPPED"


@pytest.mark.paper_runtime
def test_concurrent_workers_claim_once(system):
    strategy(system, auto_execute=True)
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        async def waiting():
            started.set()
            await finish.wait()
        calls = fake(system, callback=waiting)
        other = AutomationService(system.store, system.models, market, system.settings)
        first = asyncio.create_task(system.tick())
        await started.wait()
        assert await other.tick() is None
        finish.set()
        await first
        assert len(calls) == 1
    asyncio.run(scenario())
    assert len(dashboard(system)["orders"]) == 1


@pytest.mark.paper_runtime
def test_expired_job_recovered_without_resend_or_duplicate_notification(system):
    calls = fake(system)
    saved = strategy(system, auto_execute=True)
    job = system._claim(system.models.get_config(), market())
    with system.store.transaction() as connection:
        job["lease_until"] = (now() - timedelta(seconds=1)).isoformat()
        put(connection, jobs, job["id"], job)
    asyncio.run(system.tick())
    asyncio.run(system.tick())
    assert calls == []
    assert system.notifications()["unread_count"] == 1
    assert dashboard(system)["orders"] == []
    # Late completion of expired work cannot execute or produce another notification.
    system._finish(job, {"decision": dict(id="late", symbol="BTCUSDT", action="BUY")})
    assert system.notifications()["unread_count"] == 1
    due(system, saved)
    asyncio.run(system.tick())
    assert len(calls) == 1 and len(dashboard(system)["orders"]) == 1


@pytest.mark.parametrize("guard", ["paused", "position", "daily", "stale", "foreign"])
@pytest.mark.paper_runtime
def test_risk_rejection_is_atomic_and_notified(system, guard):
    saved = strategy(system, auto_execute=True, budget="90000", target_pct="90") if guard == "position" else strategy(system, auto_execute=True)
    if guard == "paused":
        system.store.action(dict(action="pause_new", expected_revision=1), "pause", market(), 90)
    if guard == "daily":
        system.store.update_risk(dict(max_position_pct="25", max_daily_loss="1", expected_revision=1))
    if guard == "foreign":
        system.test_console.execute(dict(symbol="BTCUSDT", side="BUY", quantity="0.01", strategy_id=None), "manual", market(), 90)
    before = system.store.dashboard(market())
    async def stale():
        system.quote_snapshot = lambda: market(stale=True)
    fake(system, callback=stale if guard == "stale" else None)
    asyncio.run(system.tick())
    result = dashboard(system)
    assert result["orders"] == before["orders"]
    assert result["account"]["cash"] == before["account"]["cash"]
    assert result["decisions"][0]["execution_status"] == "BLOCKED"
    assert result["decisions"][0]["risk_status"] == "PAPER_REJECTED"
    assert result["notifications"]["unread_count"] == 1


@pytest.mark.paper_runtime
def test_sell_scoped_and_hold(system):
    saved = strategy(system, auto_execute=True)
    fake(system)
    asyncio.run(system.tick())
    complete_pending(system)
    fake(system, "SELL")
    due(system, saved)
    asyncio.run(system.tick())
    complete_pending(system)
    assert dashboard(system)["positions"] == []
    assert len(dashboard(system)["orders"]) == 2
    fake(system, "HOLD")
    due(system, saved)
    asyncio.run(system.tick())
    assert len(dashboard(system)["orders"]) == 2


def test_missing_model_and_quotes_wait_quietly(system):
    strategy(system)
    calls = fake(system)
    system.models.save_config(dict(expected_revision=1, enabled=False))
    asyncio.run(system.tick())
    assert dashboard(system)["strategies"][0]["runtime_status"] == "WAITING_MODEL"
    system.models.save_config(dict(expected_revision=2, enabled=True))
    system.quote_snapshot = lambda: market(stale=True)
    asyncio.run(system.tick())
    assert dashboard(system)["strategies"][0]["runtime_status"] == "WAITING_QUOTES"
    assert calls == [] and system.notifications()["unread_count"] == 0


def test_failure_no_order_and_model_revision_change(system):
    strategy(system, auto_execute=True)
    async def failure(*args):
        raise RuntimeError("secret must not leak")
    system.models.generate = failure
    asyncio.run(system.tick())
    result = dashboard(system)
    assert result["orders"] == []
    assert result["notifications"]["items"][0]["kind"] == "AI_ERROR"
    assert "secret" not in str(result["notifications"])


def test_model_revision_during_await_skips(system):
    strategy(system, auto_execute=True)
    async def revise():
        system.models.save_config(dict(expected_revision=1, model="different-fixture"))
    fake(system, callback=revise)
    asyncio.run(system.tick())
    assert dashboard(system)["decisions"][0]["execution_status"] == "SKIPPED"
    assert dashboard(system)["orders"] == []


def test_late_model_completion_after_recovery_remains_annotated(system):
    strategy(system, auto_execute=True)
    job = system._claim(system.models.get_config(), market())
    with system.store.transaction() as connection:
        job["lease_until"] = (now() - timedelta(seconds=1)).isoformat()
        put(connection, jobs, job["id"], job)
    asyncio.run(system.tick())
    result = {"decision": dict(id="late-advice", symbol="BTCUSDT", action="BUY", created_at=stamp(), risk_status="NOT_EVALUATED")}
    with system.store.transaction() as connection:
        repository.write_run(connection, job["model_key"], dict(status="SUCCEEDED", result=result), new=True)
    system._finish(job, result)
    assert dashboard(system)["decisions"][0]["execution_status"] == "SKIPPED"
    assert system.notifications()["items"][0]["decision_id"] == "late-advice"
    assert system.notifications()["unread_count"] == 1
    assert dashboard(system)["orders"] == []


def test_reactivation_is_due_immediately(system):
    calls = fake(system)
    saved = strategy(system)
    asyncio.run(system.tick())
    saved = patch(system, saved, status="PAUSED")
    asyncio.run(system.tick())
    assert len(calls) == 1
    saved = patch(system, saved, status="ACTIVE", acknowledge_model_cost=True)
    asyncio.run(system.tick())
    assert len(calls) == 2


@pytest.mark.paper_runtime
def test_real_model_transport_and_notification_api(tmp_path):
    import json
    from fastapi.testclient import TestClient
    from trading_system.server.app import create_app
    from server.test_api import Feed, setup
    requests = []
    def handle(request):
        requests.append(request)
        advice = dict(symbol="BTCUSDT", action="BUY", summary="Fixture BUY", counter_evidence=["Risk"], invalidation="Change")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(advice)}}]})
    transport = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    from support.unified_console import provision_console
    harness = provision_console(tmp_path, setup=False)
    settings = Settings(**harness.settings_kwargs, testing=True, market_enabled=False,
                        allowed_origins=("http://testserver",), model_key_path=tmp_path / 'test-only.key')
    app = create_app(settings, quote_provider=Feed(), model_client=transport)
    with TestClient(app) as client:
        client.headers['Origin'] = 'http://testserver'
        assert client.get('/api/notifications').status_code == 401
        setup(client)
        assert client.put('/api/model-config', json=dict(expected_revision=0, protocol='openai_chat', model='fixture', local_mode=True,
                                                         enabled=True, base_url='http://127.0.0.1:18083/v1')).status_code == 200
        payload = dict(name='API AI', mode='AI', symbol='BTCUSDT', budget='10000', target_pct='10', status='ACTIVE')
        assert client.post('/api/strategies', json=payload).status_code == 409
        payload.update(acknowledge_model_cost=True, auto_execute=True)
        created = client.post('/api/strategies', json=payload)
        assert created.status_code == 200
        assert '_activation_authorized' not in created.json()
        assert requests == []  # settings.testing never starts the worker.
        asyncio.run(app.state.automation.tick())
        assert len(requests) == 1 and requests[0].url.path == '/v1/chat/completions'
        result = client.get('/api/dashboard').json()
        assert result['decisions'][0]['execution_status'] == 'PENDING'
        assert result['orders'][0]['status'] == 'PENDING'
        assert result['positions'] == []  # Receipt is not a fill.
        command = result['orders'][0]
        app.state.store.execution.dispatch(command['command_id'])
        app.state.store.execution.inject(command['command_id'], dict(event_id='model-api-fill',
            status='FILLED', cumulative_quantity=command['quantity'], price='50000'))
        result = client.get('/api/dashboard').json()
        assert result['orders'][0]['status'] == 'FILLED'
        assert result['decisions'][0]['order_id'] == result['orders'][0]['id']
        assert result['notifications']['unread_count'] == 1
        csrf = client.headers.pop('X-CSRF-Token')
        assert client.post('/api/notifications/read', json={'all': True}).status_code == 403
        client.headers['X-CSRF-Token'] = csrf
        assert client.post('/api/notifications/read', json={'all': True}).json()['unread_count'] == 0
        # Manual one-off analysis remains advice only, even with an armed strategy.
        manual = client.post('/api/decisions/generate', json=dict(symbol='BTCUSDT', expected_revision=1, acknowledge_cost=True),
                             headers={'Idempotency-Key': 'manual-secondary'})
        assert manual.status_code == 200
        assert manual.json()['decision']['risk_status'] == 'NOT_EVALUATED'
        assert len(client.get('/api/dashboard').json()['orders']) == 1
    asyncio.run(transport.aclose())


@pytest.mark.paper_runtime
def test_cross_store_claim_and_restart_receipt(system):
    from concurrent.futures import ThreadPoolExecutor
    strategy(system, auto_execute=True)
    other_store = Store(system.store.engine.url.render_as_string(hide_password=False))
    system.test_console.attach(other_store)
    other = AutomationService(other_store, system.models, market, system.settings)
    config = system.models.get_config()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda svc: (svc, svc._claim(config, market())), [system, other]))
        winners = [(svc, job) for svc, job in claimed if job]
        assert len(winners) == 1
        winner, job = winners[0]
        result = {'decision': dict(id='claimed', symbol='BTCUSDT', action='BUY', created_at=stamp(),
            data_as_of=market()['quotes'][0]['as_of'], risk_status='NOT_EVALUATED')}
        winner._finish(job, result)
        winner._finish(job, result)
        assert len(system.store.dashboard(market())['orders']) == 1
        assert system.notifications()['unread_count'] == 1
        restarted = AutomationService(other_store, system.models, market, system.settings)
        assert restarted._claim(config, market()) is None
    finally:
        other_store.close()


@pytest.mark.paper_runtime
def test_cash_bound_includes_fee_and_sell_rejects_other_strategy(system):
    fake(system)
    saved = strategy(system, auto_execute=True, budget="100000", target_pct="100")
    system.store.update_risk(dict(max_position_pct="100", max_daily_loss="2000", expected_revision=1))
    asyncio.run(system.tick())
    assert dashboard(system)["orders"], [d.get("execution_reason") for d in dashboard(system)["decisions"]]
    assert dashboard(system)["orders"][0]["status"] == "PENDING"
    complete_pending(system)
    first = dashboard(system)
    assert len(first['orders']) == 1
    assert Decimal(first['account']['cash']) >= 0
    # The common risk contract reserves entry AND planned exit cost. Physical
    # cash retains one future exit fee rather than spending the full balance.
    entry_notional = Decimal(first['orders'][0]['notional'])
    entry_fee = Decimal(first['orders'][0]['fee'])
    remaining_cash = Decimal(first['account']['cash'])
    assert remaining_cash == Decimal('100000') - entry_notional - entry_fee
    assert remaining_cash >= entry_fee
    assert remaining_cash - entry_fee < Decimal('0.000001')
    assert '建议买入' in first['notifications']['items'][0]['message']
    assert 'Fixture advice' in first['notifications']['items'][0]['message']
    other = strategy(system, name='Other', auto_execute=True)
    fake(system, 'SELL')
    asyncio.run(system.tick())
    result = dashboard(system)
    assert len(result['orders']) == 1
    assert result['decisions'][0]['execution_status'] == 'BLOCKED'
    assert result['positions'][0]['strategy_id'] == saved['id']


@pytest.mark.paper_runtime
def test_stale_other_position_valuation_blocks_buy(system):
    system.test_console.execute(dict(symbol='ETHUSDT', side='BUY', quantity='1', strategy_id=None), 'other-position', market(), 90)
    strategy(system, auto_execute=True)
    def partially_stale():
        value = market()
        value['quotes'][1]['stale'] = True
        return value
    system.quote_snapshot = partially_stale
    fake(system)
    asyncio.run(system.tick())
    result = dashboard(system)
    assert len(result['orders']) == 1
    assert result['decisions'][0]['execution_status'] == 'BLOCKED'
    assert result['notifications']['unread_count'] == 1


def test_notification_references_retained_decision_beyond_model_page(system):
    strategy(system)
    fake(system)
    asyncio.run(system.tick())
    result = system.store.dashboard(market())
    result['decisions'] = []  # Manual model history page no longer contains this result.
    enriched = system.enrich_dashboard(result)
    assert enriched['decisions'][0]['id'] == enriched['notifications']['items'][0]['decision_id']


def test_kill_switch_and_model_directory_failure(system):
    from dataclasses import replace
    calls = fake(system)
    saved = strategy(system)
    system.settings = replace(system.settings, automation_enabled=False)
    asyncio.run(system.tick())
    assert calls == []
    system.settings = replace(system.settings, automation_enabled=True)
    with system.store.transaction() as connection:
        config = repository.read_config(connection)
        config['connection_status'] = 'UNAVAILABLE'
        config['last_error'] = 'Directory endpoint unsupported'
        repository.write_config(connection, config)
    asyncio.run(system.tick())
    assert len(calls) == 1  # Directory support is not required for valid inference providers.


def test_pause_before_provider_dispatch_prevents_new_call(system):
    calls = fake(system)
    saved = strategy(system, auto_execute=True)
    original = system.store.analysis_context
    def context(*args):
        result = original(*args)
        patch(system, saved, status='PAUSED')
        return result
    system.store.analysis_context = context
    asyncio.run(system.tick())
    assert calls == []
    assert dashboard(system)['orders'] == []


@pytest.mark.paper_runtime
@pytest.mark.parametrize('mode', ['AI', 'RULE', 'HYBRID'])
def test_three_modes_share_atomic_risk_owner_and_explicit_fill_path(system, mode):
    changes = dict(mode=mode, auto_execute=True, model_profile_ids=['default'] if mode != 'RULE' else [])
    if mode != 'AI':
        changes.update(rule=dict(kind='PRICE_THRESHOLD', operator='LTE', price='50000', stop_loss_pct='5'),
            acknowledge_paper_automation=True)
    if mode == 'RULE':
        changes.update(acknowledge_model_cost=False, model_profile_ids=[])
    strategy(system, **changes)
    calls = fake(system)
    asyncio.run(system.tick())
    result = dashboard(system)
    assert len(calls) == (0 if mode == 'RULE' else 1)
    assert len(result['orders']) == 1, result['decisions']
    order = result['orders'][0]
    assert order['status'] == 'PENDING' and order['filled_quantity'] == '0'
    assert result['positions'] == [] and result['account']['cash'] == '100000'
    from trading_system.server.paper_runtime import scope_for, RuntimeRequest
    from trading_system.contracts import types as t
    with system.store.execution.uow.transaction(scope_for('BTCUSDT')) as tx:
        command = tx.commands.get(order['command_id'])
        request = tx._latest('audit_events', 'console:request:' + command.command_id, RuntimeRequest)
        risk = tx._latest('audit_events', 'ip07:risk:' + command.intent_id, t.RiskDecision)
        assert request.mode == mode and command.ownership_epoch == 1
        assert command.state == 'DURABLE_READY' and risk.verdict == 'ALLOW'
        assert tx.reservations.for_intent(command.intent_id)
    complete_pending(system)
    assert dashboard(system)['orders'][0]['status'] == 'FILLED'
    assert len(dashboard(system)['positions']) == 1


@pytest.mark.paper_runtime
@pytest.mark.parametrize('mode', ['AI', 'RULE', 'HYBRID'])
def test_three_modes_cannot_override_single_position_limit(system, mode):
    changes = dict(mode=mode, auto_execute=True, budget='90000', target_pct='90',
                   model_profile_ids=['default'] if mode != 'RULE' else [])
    if mode != 'AI':
        changes.update(rule=dict(kind='PRICE_THRESHOLD', operator='LTE', price='50000', stop_loss_pct='5'),
            acknowledge_paper_automation=True)
    if mode == 'RULE':
        changes.update(acknowledge_model_cost=False, model_profile_ids=[])
    strategy(system, **changes)
    fake(system)
    asyncio.run(system.tick())
    result = dashboard(system)
    assert result['orders'] == [] and result['positions'] == []
    assert result['decisions'][0]['execution_status'] == 'BLOCKED'
    assert result['decisions'][0]['risk_status'] == 'PAPER_REJECTED'
    assert result['account']['cash'] == '100000'


@pytest.mark.paper_runtime
@pytest.mark.parametrize('field', ['created_at', 'data_as_of'])
def test_old_model_output_cannot_create_new_domain_command(system, field):
    strategy(system, auto_execute=True)
    fake(system)
    original = system.models.generate
    async def stale(*args):
        result = await original(*args)
        result['decision'][field] = (now() - timedelta(minutes=10)).isoformat()
        return result
    system.models.generate = stale
    asyncio.run(system.tick())
    result = dashboard(system)
    assert result['orders'] == [] and result['positions'] == []
    assert result['decisions'][0]['execution_status'] == 'BLOCKED'
    assert '过期' in result['decisions'][0]['execution_reason']
