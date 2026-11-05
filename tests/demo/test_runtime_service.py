"""Composition and revocation never enable venue work by construction."""
import asyncio
import time
from sqlalchemy import create_engine
import pytest
from trading_system.storage.demo import metadata, DemoRepository
from trading_system.engine.binance_demo.contracts import DemoScope, DemoError
from trading_system.server.demo_api import DemoConnectionService, DemoConnect
from trading_system.runtime.demo_service import DemoRuntimeService


class Transport:
    calls = []
    def __init__(self, provider, ref): self.credential_ref = ref; self.closed = False
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self): self.closed = True
    def sync_time(self): self.calls.append('time'); return 1
    def account(self): return {'uid': 123, 'accountType': 'SPOT', 'canTrade': True, 'balances': []}
    def exchange_info(self, *args): return {'symbols': [{'symbol': 'BTCUSDT'}]}
    def open_orders(self, *args): return []


def test_runtime_binding_is_explicit_verified_and_revoked(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'runtime.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=DemoRepository(engine),
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='verified-epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    assert not manager.status('session')['configured']
    connections.connect('session', DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True), session_expires_at=time.time()+1000)
    assert manager.bind_session('session')['account_state'] == 'READ_ONLY'
    assert manager.status('session')['execution_authorized'] is False
    runtime = manager._runtime('session')
    assert runtime.host.stream is runtime.stream and not runtime.stream.healthy
    assert not runtime.running
    connections.disconnect('session')
    assert runtime.revoked and runtime.transport.closed
    assert manager.repository.account(runtime.host.scope)['state'] == 'RECOVERY_REQUIRED'
    assert not manager.status('session')['configured']
    connections.close(); engine.dispose()


def test_runtime_factory_is_injected_but_never_auto_starts(tmp_path):
    from fastapi.testclient import TestClient
    from trading_system.server.app import create_app
    from trading_system.server.config import Settings
    events = []
    class Runtime:
        def close(self): events.append('close')
    def factory(**kwargs):
        assert set(kwargs) == {'connections', 'model_service'}
        events.append('constructed'); return Runtime()
    app = create_app(Settings(database_url=f'sqlite:///{tmp_path / "app.sqlite"}', testing=True, market_enabled=False, model_key_path=tmp_path/'key'), demo_runtime_factory=factory)
    assert events == ['constructed']
    with TestClient(app): assert app.state.demo_runtime is not None
    assert events == ['constructed', 'close']


@pytest.mark.parametrize('fail_model', [False, True])
def test_slow_assessment_does_not_starve_protection_supervisor(fail_model):
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    counters = {'protect': 0, 'during_model': 0, 'assessing': False}
    class Lease:
        credential_ref = 'demo-session:fixture'
        expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='account', account_epoch='epoch', strategy_id='s1')
        memory = None
        async def assess_once(self, run, **kwargs):
            counters['assessing'] = True
            if fail_model:
                await asyncio.sleep(0.1)
                raise DemoError('DEMO_MODEL_ERROR')
            await asyncio.sleep(2)
        def drain_inbox(self): pass
        def protection_tick(self):
            counters['protect'] += 1
            if counters['assessing']: counters['during_model'] += 1
            return {'commands': []}
        def status(self): return {'execution_authorized': False}
    class Stream:
        healthy = True
        async def run_window(self, transport, *, on_subscribed, **kwargs):
            try: await asyncio.wait_for(on_subscribed(self), timeout=1.2)
            except TimeoutError: pass
    class Repository:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        authorization_deadline = time.time() + 1000
        def strategy(self, scope): return {'allow_execution': True, 'authorized_until': self.authorization_deadline}
        def account(self, scope): return {'credential_ref': 'demo-session:fixture'}
        def active_orders(self, scope): return []
        def health(self, scope): return {'unknown': 0}
        def abandon_stale_unsent(self, *a, **kw): pass
        def pause(self, *a): pass
    class Reconciler:
        def reconcile(self, *a, **kw): pass
        def reconcile_and_resume(self, *a, **kw): pass
    manager = DemoRuntimeService(connections=None, model_service=None, repository=Repository(), scope_resolver=None, current_code_sha='a'*40)
    manager._sessions['session'] = _Runtime(Lease(), None, Host(), Stream(), Reconciler())
    asyncio.run(manager.run_window('session', duration_seconds=2, execute=True))
    assert counters['during_model'] >= 1 and counters['protect'] >= 2


def test_runtime_rebind_rotates_same_verified_epoch_and_preserves_unknown(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'rebind.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport)
    repository = DemoRepository(engine)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=repository,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='verified-epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    connect = lambda key: connections.connect('session', DemoConnect(api_key=key, api_secret='fixture-secret-123456', acknowledge_demo_only=True), session_expires_at=time.time()+1000)
    connect('fixture-key-123456'); manager.bind_session('session')
    original_runtime = manager._runtime('session'); scope = original_runtime.host.scope
    request = {'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'LIMIT', 'quantity': '0.001', 'price': '50000', 'newClientOrderId': 'original', 'timeInForce': 'GTC'}
    repository.stage(scope, command_id='original-command', request=request, reserve_asset='USDT', reserve_amount='50', available='1000', authorization_ref='grant', expires_at=200, now=100)
    repository.begin_send(scope, 'original-command', expected_revision=0, now=101)
    repository.mark_unknown(scope, 'original-command', 'LOST_RESPONSE')
    connect('fixture-key-987654')
    status = manager.bind_session('session')
    assert original_runtime.revoked and original_runtime.transport.closed
    assert status['account_state'] == 'READ_ONLY' and not status['execution_authorized']
    assert repository.account(scope)['credential_revision'] == 1
    assert repository.command(scope, 'original-command')['state'] == 'UNKNOWN'
    assert repository.command(scope, 'original-command')['client_id'] == 'original'
    assert repository.command(scope, 'original-command')['reserve_amount'] == '50'
    connections.close(); engine.dispose()


def test_disconnect_keeps_running_revoked_window_tracked_until_drained(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'drain.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=DemoRepository(engine),
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    payload = DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True)
    connect = lambda: connections.connect('session', payload, session_expires_at=time.time()+1000)
    connect(); manager.bind_session('session')
    runtime = manager._runtime('session'); runtime.running = True
    connections.disconnect('session')
    assert manager._sessions['session'] is runtime and runtime.revoked
    assert not manager.status('session')['configured']
    connect()
    with pytest.raises(DemoError, match='RUNTIME_DRAIN_REQUIRED'):
        manager.bind_session('session')
    assert manager._sessions['session'] is runtime
    runtime.running = False
    assert manager.bind_session('session')['account_state'] == 'READ_ONLY'
    connections.close(); engine.dispose()


def test_rebind_drains_original_revision_before_rotating(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'pending.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport); repo = DemoRepository(engine)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=repo,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    payload = DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True)
    connect = lambda: connections.connect('session', payload, session_expires_at=time.time()+1000)
    connect(); manager.bind_session('session'); scope = manager._runtime('session').host.scope
    old_ref = repo.account(scope)['credential_ref']
    request = {'symbol':'BTCUSDT', 'side':'BUY', 'type':'LIMIT', 'quantity':'0.001', 'price':'50000', 'newClientOrderId':'original', 'timeInForce':'GTC'}
    repo.stage(scope, command_id='original', request=request, reserve_asset='USDT', reserve_amount='50', available='1000', authorization_ref='grant', expires_at=200, now=100)
    repo.begin_send(scope, 'original', expected_revision=0, now=101)
    event = {'e':'executionReport', 's':'BTCUSDT', 'c':'original', 'S':'BUY', 'o':'LIMIT', 'q':'0.001', 'p':'50000', 'x':'NEW', 'X':'NEW', 'i':3, 'z':'0'}
    repo.observe(scope, source='WS_EXECUTION_REPORT', payload=event, received_at=101, credential_ref=old_ref)
    connect(); manager.bind_session('session')
    assert repo.health(scope)['pending_inbox'] == 0
    assert repo.command(scope, 'original')['order_status'] == 'NEW'
    assert repo.account(scope)['credential_revision'] == 1
    connections.close(); engine.dispose()


def test_second_session_cannot_rebind_running_same_account_epoch(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'multi.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport); repo = DemoRepository(engine)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=repo,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    payload = DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True)
    for session in ('a', 'b'):
        connections.connect(session, payload, session_expires_at=time.time()+1000)
    manager.bind_session('a'); runtime = manager._runtime('a'); runtime.running = True
    with pytest.raises(DemoError, match='ACCOUNT_RUNTIME_DRAIN_REQUIRED'):
        manager.bind_session('b')
    assert repo.account(runtime.host.scope)['credential_revision'] == 0
    runtime.running = False
    assert manager.bind_session('b')['account_state'] == 'READ_ONLY'
    connections.close(); engine.dispose()


def test_rebind_waits_for_final_pause_before_rotation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    engine = create_engine('sqlite:///' + str(tmp_path/'teardown.sqlite'), connect_args={'check_same_thread': False}); metadata.create_all(engine)
    connections = DemoConnectionService(Transport); repo = DemoRepository(engine)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=repo,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    payload = DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True)
    for session in ('a', 'b'):
        connections.connect(session, payload, session_expires_at=time.time()+1000)
    manager.bind_session('a'); runtime = manager._runtime('a')
    class Stream:
        healthy = False
        async def run_window(self, *args, **kwargs): return None
    runtime.stream = Stream(); runtime.host.stream = runtime.stream
    entered, release = Event(), Event(); original = repo.pause
    monkeypatch.setattr(repo.engine.dialect, 'name', 'postgresql')
    def pause(scope, reason='DEMO_RECOVERY_REQUIRED'):
        if reason == 'DEMO_RUNTIME_WINDOW_ENDED':
            entered.set(); assert release.wait(3)
            repo.engine.dialect.name = 'sqlite'
        return original(scope, reason)
    monkeypatch.setattr(repo, 'pause', pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(lambda: asyncio.run(manager.run_window('a', duration_seconds=1)))
        assert entered.wait(2)
        bind = pool.submit(manager.bind_session, 'b')
        with pytest.raises(TimeoutError): bind.result(timeout=0.1)
        assert runtime.running
        release.set(); future.result(timeout=3); bind.result(timeout=3)
    assert repo.account(runtime.host.scope)['credential_revision'] == 1
    connections.close(); engine.dispose()


def test_sqlite_read_only_window_is_blocked_before_stream(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'offline-only.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=DemoRepository(engine),
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    connections.connect('session', DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True), session_expires_at=time.time()+1000)
    manager.bind_session('session'); runtime = manager._runtime('session')
    with pytest.raises(DemoError, match='POSTGRESQL_OWNER_REQUIRED'):
        asyncio.run(manager.run_window('session', duration_seconds=1, execute=False))
    assert not runtime.running and not runtime.stream.healthy
    connections.close(); engine.dispose()


def test_postgresql_rebind_after_disconnect_is_explicitly_blocked(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path/'pg-boundary.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport); repo = DemoRepository(engine)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=repo,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    payload = DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True)
    connect = lambda: connections.connect('session', payload, session_expires_at=time.time()+1000)
    connect(); manager.bind_session('session')
    scope = manager._runtime('session').host.scope
    connections.disconnect('session')
    old = repo.account(scope)
    connect()
    # Exercise the explicit dialect gate using offline SQLite storage only; this
    # is not evidence of real PostgreSQL fencing or cross-process behavior.
    monkeypatch.setattr(engine.dialect, 'name', 'postgresql')
    assert manager.status('session')['credential_rebind'] == 'BLOCKED_UNQUALIFIED_FENCING'
    with pytest.raises(DemoError, match='DEMO_CREDENTIAL_REBIND_FENCING_UNQUALIFIED'):
        manager.bind_session('session')
    assert repo.account(scope)['credential_ref'] == old['credential_ref']
    assert repo.account(scope)['credential_revision'] == old['credential_revision']
    monkeypatch.setattr(engine.dialect, 'name', 'sqlite')
    connections.close(); engine.dispose()


def test_window_pause_failure_keeps_session_blocked_for_rebind():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        def status(self): return {'execution_authorized': False}
    class Stream:
        healthy = False
        async def run_window(self, *args, **kwargs): pass
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref': 'demo-session:fixture'}
        def pause(self, scope, reason): raise DemoError('DEMO_OWNER_BUSY')
    manager = DemoRuntimeService(connections=None, model_service=None, repository=Repo(), scope_resolver=None, current_code_sha='a'*40)
    runtime = _Runtime(Lease(), SimpleNamespace(close=lambda: None), Host(), Stream(), None)
    manager._sessions['session'] = runtime
    with pytest.raises(DemoError, match='DEMO_OWNER_BUSY'):
        asyncio.run(manager.run_window('session', duration_seconds=1))
    assert manager.status('session')['blocker'] == 'DEMO_DRAIN_PAUSE_UNCONFIRMED'
    with pytest.raises(DemoError, match='DEMO_DRAIN_PAUSE_UNCONFIRMED'):
        manager.bind_session('session')


def test_explicit_stop_waits_for_window_teardown_and_durable_pause():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        def status(self): return {'execution_authorized': False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        pauses = []
        def account(self, scope): return {'credential_ref': 'demo-session:fixture'}
        def pause(self, scope, reason): self.pauses.append(reason)
    async def scenario():
        entered = asyncio.Event()
        class Stream:
            healthy = False
            async def run_window(self, *args, **kwargs):
                entered.set(); await asyncio.Event().wait()
        repo = Repo()
        manager = DemoRuntimeService(connections=None, model_service=None, repository=repo, scope_resolver=None, current_code_sha='a'*40)
        runtime = _Runtime(Lease(), SimpleNamespace(close=lambda: None), Host(), Stream(), None)
        manager._sessions['session'] = runtime
        task = asyncio.create_task(manager.run_window('session', duration_seconds=30))
        await entered.wait()
        assert manager.request_stop('session')['state'] == 'STOP_REQUESTED'
        with pytest.raises(asyncio.CancelledError): await task
        assert not runtime.running and repo.pauses == ['DEMO_RUNTIME_WINDOW_ENDED']
    asyncio.run(scenario())


def test_registration_and_session_revocation_are_serialized(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    engine = create_engine('sqlite:///' + str(tmp_path/'registration-race.sqlite'), connect_args={'check_same_thread': False}); metadata.create_all(engine)
    connections = DemoConnectionService(Transport); repo = DemoRepository(engine)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=repo,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    payload = DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True)
    connections.connect('session', payload, session_expires_at=time.time()+1000)
    entered, release = Event(), Event(); original = repo.register_account
    def delayed(*args, **kwargs):
        entered.set(); assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'register_account', delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        bind = pool.submit(manager.bind_session, 'session')
        assert entered.wait(2)
        disconnect = pool.submit(connections.disconnect, 'session')
        with pytest.raises(TimeoutError): disconnect.result(timeout=0.1)
        release.set(); bind.result(timeout=3); disconnect.result(timeout=3)
    assert connections.connection_count == 0 and not manager.status('session')['configured']
    connections.close(); engine.dispose()


def test_explicit_ai_configuration_is_scoped_and_never_starts_a_window(tmp_path):
    from trading_system.runtime.demo_control import DemoStrategyInput
    engine = create_engine('sqlite:///' + str(tmp_path/'control.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport); repo = DemoRepository(engine)
    class Models:
        def get_config(self, profile_id): return {'id': profile_id, 'revision': 4, 'enabled': True, 'key_available': True, 'local_mode': False}
    manager = DemoRuntimeService(connections=connections, model_service=Models(), repository=repo,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']), account_epoch='epoch', strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    connections.connect('session', DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True), session_expires_at=time.time()+1000)
    manager.bind_session('session')
    payload = DemoStrategyInput(model_profile_id='model-a', model_revision=4, expected_strategy_revision=0,
        budget='100', max_planned_loss='2', max_daily_loss='5', fee_rate_cap='0.001',
        holding_period_seconds=300, authorization_seconds=300, acknowledge_model_cost=True, acknowledge_demo_execution=True)
    result = manager.configure_strategy('session', payload)
    runtime = manager._runtime('session'); saved = repo.strategy(runtime.host.scope)
    assert result['revision'] == 1 and not result['execution_authorized']
    assert manager.status('session')['strategy_revision'] == 1
    from trading_system.engine.binance_demo.contracts import digest
    summary = manager.operator_status('session')['saved_strategy']
    assert summary['grant_hash'] == digest(saved) and summary['model_profile_id'] == 'model-a'
    assert saved['mode'] == 'AI' and saved['model_revisions'] == {'model-a': 4}
    assert saved['budget'] == '100' and saved['max_daily_loss'] == '5'
    assert saved['allow_model_calls'] and saved['allow_execution']
    assert not runtime.running and not runtime.stream.healthy
    assert repo.account(runtime.host.scope)['qualification'] is None
    connections.close(); engine.dispose()


def test_operator_start_stop_owns_one_bounded_background_window():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        def status(self): return {'execution_authorized': False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        pauses = []
        def account(self, scope):
            proof = {'state':'DEMO_VERIFIED','evidence_sha256':'f'*64,'official_contract_revision':'fixture'}
            return {'credential_ref':'demo-session:fixture','credential_revision':0,'qualification':{'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,'credential_revision':0,'product':'SPOT','history_coverage':'COMPLETE','protection':'HOSTED','baseline_hash':'b'*64,'evidence_sha256':'e'*64,'official_contract_revision':'fixture','capabilities':{'LIMIT':proof,'CANCEL':proof}}}
        authorization_deadline = time.time() + 500
        def strategy(self, scope): return {'revision': 1, 'authorized': True, 'allow_execution': True, 'allow_model_calls': True, 'mode': 'AI', 'authorized_until': self.authorization_deadline}
        def pause(self, scope, reason): self.pauses.append(reason)
    async def scenario():
        entered = asyncio.Event()
        class Stream:
            healthy = False
            async def run_window(self, *args, **kwargs): entered.set(); await asyncio.Event().wait()
        repo = Repo()
        manager = DemoRuntimeService(connections=None, model_service=None, repository=repo, scope_resolver=None, current_code_sha='a'*40)
        runtime = _Runtime(Lease(), SimpleNamespace(close=lambda: None), Host(), Stream(), None)
        manager._sessions['session'] = runtime
        from trading_system.engine.binance_demo.contracts import digest
        grant = digest(repo.strategy(runtime.host.scope))
        assert manager.start_window('session', duration_seconds=60, decision_interval_seconds=30, expected_strategy_revision=1, expected_grant_hash=grant)['state'] == 'START_REQUESTED'
        await entered.wait()
        assert manager.status('session')['window']['state'] == 'RUNNING'
        with pytest.raises(DemoError, match='WINDOW_ACTIVE'):
            manager.start_window('session', duration_seconds=60, decision_interval_seconds=30, expected_strategy_revision=1, expected_grant_hash=grant)
        assert manager.request_stop('session')['state'] == 'STOP_REQUESTED'
        await manager._jobs['session']['task']
        assert manager.status('session')['window']['state'] == 'STOPPED_REVIEW_REQUIRED'
        assert repo.pauses == ['DEMO_RUNTIME_WINDOW_ENDED']
    asyncio.run(scenario())


def test_operator_recovery_window_does_not_require_model_or_enable_sends():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        def status(self): return {'execution_authorized': False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        pauses = []
        def account(self, scope): return {'credential_ref': 'demo-session:fixture'}
        def strategy(self, scope): raise AssertionError('Recovery must not require an AI strategy')
        def pause(self, scope, reason): self.pauses.append(reason)
    async def scenario():
        class Stream:
            healthy = False
            async def run_window(self, *args, **kwargs): pass
        repo = Repo()
        manager = DemoRuntimeService(connections=None, model_service=None, repository=repo, scope_resolver=None, current_code_sha='a'*40)
        manager._sessions['session'] = _Runtime(Lease(), SimpleNamespace(close=lambda: None), Host(), Stream(), None)
        assert manager.start_recovery('session', duration_seconds=30)['state'] == 'START_REQUESTED'
        await manager._jobs['session']['task']
        assert manager.status('session')['window']['state'] == 'ENDED_REVIEW_REQUIRED'
        assert repo.pauses == ['DEMO_RUNTIME_WINDOW_ENDED']
    asyncio.run(scenario())


def test_stop_remains_pending_until_inflight_worker_finishes_and_pause_commits():
    from types import SimpleNamespace
    from threading import Event
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event()
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        def status(self): return {'execution_authorized': False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        pauses = []
        def account(self, scope): return {'credential_ref': 'demo-session:fixture'}
        def pause(self, scope, reason): self.pauses.append(reason)
    class Reconciler:
        def reconcile(self, *args, **kwargs): entered.set(); assert release.wait(3)
    async def scenario():
        class Stream:
            healthy = True
            async def run_window(self, transport, *, on_subscribed, **kwargs): await on_subscribed(self)
        repo = Repo()
        manager = DemoRuntimeService(connections=None, model_service=None, repository=repo, scope_resolver=None, current_code_sha='a'*40)
        manager._sessions['session'] = _Runtime(Lease(), SimpleNamespace(close=lambda: None), Host(), Stream(), Reconciler())
        manager.start_recovery('session', duration_seconds=30)
        assert await asyncio.to_thread(entered.wait, 2)
        assert manager.request_stop('session')['state'] == 'STOP_REQUESTED'
        await asyncio.sleep(0.05)
        assert manager.status('session')['window']['state'] == 'STOP_PENDING'
        assert repo.pauses == []
        release.set()
        for _ in range(50):
            if manager.status('session')['window']['state'] == 'STOPPED_REVIEW_REQUIRED': break
            await asyncio.sleep(0.01)
        assert manager.status('session')['window']['state'] == 'STOPPED_REVIEW_REQUIRED'
        assert repo.pauses == ['DEMO_RUNTIME_WINDOW_ENDED']
    asyncio.run(scenario())


def test_strategy_configuration_rejects_pending_window_for_same_physical_account(tmp_path):
    from types import SimpleNamespace
    from contextlib import nullcontext
    from trading_system.runtime.demo_service import _Runtime
    from trading_system.runtime.demo_control import DemoStrategyInput
    class Lease:
        expires_at = time.time() + 1000
        def validate(self): return True
        def registration_guard(self): return nullcontext()
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def save_strategy(self, *args, **kwargs): raise AssertionError('must not save')
    class Model:
        def get_config(self, profile_id): return {'revision':4, 'enabled':True, 'key_available':True}
    manager = DemoRuntimeService(connections=None, model_service=Model(), repository=Repo(), scope_resolver=None, current_code_sha='a'*40)
    def runtime(epoch):
        host = SimpleNamespace(scope=DemoScope(account_id='123', account_epoch=epoch, strategy_id='s1'))
        return _Runtime(Lease(), None, host, None, None)
    manager._sessions = {'a':runtime('epoch-a'), 'b':runtime('epoch-b')}
    manager._jobs['a'] = {'state':'START_REQUESTED', 'error':None, 'task':SimpleNamespace(done=lambda: False)}
    payload = DemoStrategyInput(model_profile_id='model-a', model_revision=4, expected_strategy_revision=0,
        budget='100', max_planned_loss='2', max_daily_loss='5', fee_rate_cap='0.001',
        holding_period_seconds=300, authorization_seconds=300, acknowledge_model_cost=True, acknowledge_demo_execution=True)
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_RUNTIME_ACTIVE'):
        manager.configure_strategy('b', payload)


def test_operator_start_requires_bound_capability_and_baseline_evidence():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'demo-session:fixture', 'credential_revision':0, 'qualification':None}
        deadline = time.time() + 500
        def strategy(self, scope): return {'revision':1,'mode':'AI','authorized':True,'allow_model_calls':True,'allow_execution':True,'authorized_until':self.deadline}
    manager = DemoRuntimeService(connections=None, model_service=None, repository=Repo(), scope_resolver=None, current_code_sha='a'*40)
    manager._sessions['session'] = _Runtime(Lease(), None, Host(), None, None)
    from trading_system.engine.binance_demo.contracts import digest
    grant = digest(manager.repository.strategy(manager._sessions['session'].host.scope))
    with pytest.raises(DemoError, match='DEMO_QUALIFICATION_REQUIRED'):
        manager.start_window('session', duration_seconds=60, decision_interval_seconds=30, expected_strategy_revision=1, expected_grant_hash=grant)
    assert not manager._jobs


def test_start_rejects_a_stale_user_reviewed_strategy_before_scheduling():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'demo-session:fixture', 'credential_revision':0}
        def strategy(self, scope): return {'revision':2,'mode':'AI','authorized':True,'allow_model_calls':True,'allow_execution':True,'authorized_until':time.time()+500}
    manager = DemoRuntimeService(connections=None, model_service=None, repository=Repo(), scope_resolver=None, current_code_sha='a'*40)
    manager._sessions['session'] = _Runtime(Lease(), None, Host(), None, None)
    with pytest.raises(DemoError, match='DEMO_DECISION_BINDING_CHANGED'):
        manager.start_window('session', duration_seconds=60, decision_interval_seconds=30, expected_strategy_revision=1, expected_grant_hash='f'*64)
    assert not manager._jobs


def test_session_revocation_waits_for_inflight_worker_before_transport_close():
    from types import SimpleNamespace
    from threading import Event
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event()
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        def status(self): return {'execution_authorized': False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        pauses = []
        def account(self, scope): return {'credential_ref':'demo-session:fixture'}
        def pause(self, scope, reason): self.pauses.append(reason)
    class Transport:
        closed = False
        def close(self): self.closed = True
    class Reconciler:
        def reconcile(self, *args, **kwargs): entered.set(); assert release.wait(3)
    async def scenario():
        class Stream:
            healthy = True
            async def run_window(self, transport, *, on_subscribed, **kwargs): await on_subscribed(self)
        repo, transport = Repo(), Transport()
        manager = DemoRuntimeService(connections=None, model_service=None, repository=repo, scope_resolver=None, current_code_sha='a'*40)
        manager._sessions['session'] = _Runtime(Lease(), transport, Host(), Stream(), Reconciler())
        manager.start_recovery('session', duration_seconds=30)
        assert await asyncio.to_thread(entered.wait, 2)
        manager.close_session('session')
        await asyncio.sleep(0.05)
        assert not transport.closed and not repo.pauses
        release.set(); await manager._jobs['session']['task']
        assert transport.closed and repo.pauses == ['DEMO_RUNTIME_WINDOW_ENDED']
    asyncio.run(scenario())


def test_rebind_after_lease_revocation_waits_for_inflight_worker_before_close():
    from types import SimpleNamespace
    from threading import Event
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event()
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        revoked = False
        def validate(self):
            if self.revoked: raise DemoError('DEMO_SESSION_LEASE_REVOKED')
            return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        def status(self): return {'execution_authorized': False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        pauses = []
        def account(self, scope): return {'credential_ref':'demo-session:fixture'}
        def pause(self, scope, reason): self.pauses.append(reason)
    class Transport:
        closed = False
        def close(self): self.closed = True
    class Reconciler:
        def reconcile(self, *args, **kwargs): entered.set(); assert release.wait(3)
    async def scenario():
        class Stream:
            healthy = True
            async def run_window(self, transport, *, on_subscribed, **kwargs): await on_subscribed(self)
        repo, transport = Repo(), Transport()
        manager = DemoRuntimeService(connections=None, model_service=None, repository=repo, scope_resolver=None, current_code_sha='a'*40)
        manager._sessions['session'] = _Runtime(Lease(), transport, Host(), Stream(), Reconciler())
        manager.start_recovery('session', duration_seconds=30)
        assert await asyncio.to_thread(entered.wait, 2)
        manager._sessions['session'].lease.revoked = True
        with pytest.raises(DemoError, match='DEMO_RUNTIME_DRAIN_REQUIRED'):
            manager.bind_session('session')
        assert manager._sessions['session'].stop_requested
        await asyncio.sleep(0.05)
        assert not transport.closed and not repo.pauses
        release.set(); await manager._jobs['session']['task']
        assert transport.closed and repo.pauses == ['DEMO_RUNTIME_WINDOW_ENDED']
    asyncio.run(scenario())


def test_shutdown_waits_for_inflight_worker_before_repository_disposal():
    from types import SimpleNamespace
    from threading import Event
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event(); disposed = []
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        def status(self): return {'execution_authorized': False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'demo-session:fixture'}
        def pause(self, scope, reason): pass
    class Reconciler:
        def reconcile(self, *args, **kwargs): entered.set(); assert release.wait(3)
    async def scenario():
        class Stream:
            healthy = True
            async def run_window(self, transport, *, on_subscribed, **kwargs): await on_subscribed(self)
        manager = DemoRuntimeService(connections=None, model_service=None, repository=Repo(), scope_resolver=None, current_code_sha='a'*40, close_repository=lambda: disposed.append(True))
        manager._sessions['session'] = _Runtime(Lease(), SimpleNamespace(close=lambda: None), Host(), Stream(), Reconciler())
        manager.start_recovery('session', duration_seconds=30)
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(manager.aclose())
        await asyncio.sleep(0.05)
        assert not closing.done() and not disposed
        release.set(); await closing
        assert disposed == [True]
    asyncio.run(scenario())


def test_decision_failure_is_reported_as_paused_not_normal_completion():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    from trading_system.engine.binance_demo.contracts import digest
    class Lease:
        credential_ref='demo-session:fixture'; expires_at=time.time()+1000
        def validate(self): return True
    class Host:
        scope=DemoScope(account_id='123',account_epoch='epoch',strategy_id='s1'); memory=None
        def status(self): return {'execution_authorized':False}
        def drain_inbox(self): pass
        def protection_tick(self): return {'state':'NO_EXPOSURE','commands':[]}
    class Repo:
        engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        deadline=time.time()+500
        def account(self, scope):
            proof={'state':'DEMO_VERIFIED','evidence_sha256':'f'*64,'official_contract_revision':'fixture'}
            return {'credential_ref':'demo-session:fixture','credential_revision':0,'qualification':{'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,'credential_revision':0,'product':'SPOT','history_coverage':'COMPLETE','protection':'HOSTED','baseline_hash':'b'*64,'evidence_sha256':'e'*64,'official_contract_revision':'fixture','capabilities':{'LIMIT':proof,'CANCEL':proof}}}
        def strategy(self, scope): return {'revision':1,'mode':'AI','authorized':True,'allow_model_calls':True,'allow_execution':True,'authorized_until':self.deadline}
        def pause(self, *args): pass
        def active_orders(self, scope): return []
        def abandon_stale_unsent(self, *args, **kwargs): pass
        def health(self, scope): return {'unknown':0}
    class Reconciler:
        def reconcile(self, *args, **kwargs): pass
        def reconcile_and_resume(self, *args, **kwargs): raise DemoError('DEMO_EXTERNAL_ORDER_UNRESOLVED')
    async def scenario():
        class Stream:
            healthy=True
            async def run_window(self, transport, *, on_subscribed, **kwargs): await on_subscribed(self)
        repo=Repo(); manager=DemoRuntimeService(connections=None,model_service=None,repository=repo,scope_resolver=None,current_code_sha='a'*40)
        runtime=_Runtime(Lease(),SimpleNamespace(close=lambda: None),Host(),Stream(),Reconciler()); manager._sessions['session']=runtime
        grant=digest(repo.strategy(runtime.host.scope))
        manager.start_window('session',duration_seconds=1,decision_interval_seconds=30,expected_strategy_revision=1,expected_grant_hash=grant)
        await manager._jobs['session']['task']
        status=manager.status('session')
        assert status['new_risk_paused'] is True and status['last_error']=='DEMO_EXTERNAL_ORDER_UNRESOLVED'
        assert status['window']['state']=='ENDED_BLOCKED_REVIEW_REQUIRED'
        assert status['window']['error']=='DEMO_EXTERNAL_ORDER_UNRESOLVED'
    asyncio.run(scenario())

@pytest.mark.parametrize('cross_at', ['frame', 'repair', 'repo'])
def test_window_deadline_blocks_each_new_model_request(monkeypatch, cross_at):
    from types import SimpleNamespace
    import trading_system.runtime.demo_service as service_module
    from trading_system.runtime.demo_service import _Runtime
    clock = [10.0]; calls = []; cross_repo = [False]
    monkeypatch.setattr(service_module, 'time', SimpleNamespace(time=time.time, monotonic=lambda: clock[0]))
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
        memory = None
        async def assess_once(self, run, *, before_model, **kwargs):
            if cross_at == 'frame': clock[0] = 11.0
            if cross_at == 'repo': cross_repo[0] = True
            before_model(); calls.append('primary')
            clock[0] = 11.0
            before_model(); calls.append('repair')
        def drain_inbox(self): pass
        def protection_tick(self): return {'state':'ACTIVE', 'commands': []}
        def status(self): return {'execution_authorized':False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        authorization_deadline = time.time() + 100
        def account(self, scope):
            if cross_repo[0]: clock[0] = 11.0
            return {'credential_ref':'demo-session:fixture'}
        def strategy(self, scope): return {'allow_execution':True,'authorized_until':self.authorization_deadline}
        def active_orders(self, scope): return []
        def health(self, scope): return {'unknown':0}
        def abandon_stale_unsent(self, *args, **kwargs): pass
        def pause(self, *args): pass
    class Reconciler:
        def reconcile(self, *args, **kwargs): pass
        def reconcile_and_resume(self, *args, **kwargs): pass
    class Stream:
        healthy = True
        async def run_window(self, transport, *, on_subscribed, **kwargs):
            try: await asyncio.wait_for(on_subscribed(self), timeout=0.2)
            except TimeoutError: pass
    manager = DemoRuntimeService(connections=None, model_service=None, repository=Repo(), scope_resolver=None, current_code_sha='a'*40)
    runtime = _Runtime(Lease(), None, Host(), Stream(), Reconciler()); manager._sessions['session'] = runtime
    asyncio.run(manager.run_window('session', duration_seconds=1, execute=True))
    assert calls == (['primary'] if cross_at == 'repair' else [])
    assert runtime.last_error == 'DEMO_WINDOW_EXPIRED'


@pytest.mark.parametrize('cross_at', ['sync', 'signing'])
def test_owner_final_preflight_rejects_send_when_blocked_work_crosses_deadline(monkeypatch, cross_at):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import trading_system.runtime.demo_service as service_module
    from trading_system.runtime.demo_service import _Runtime
    from trading_system.execution.demo_commands import DemoExecutionOwner
    from trading_system.runtime.demo_host import DemoRuntimeHost, AdmissionContext
    clock = [10.0]; sent = []; may = []; unknown = []
    monkeypatch.setattr(service_module, 'time', SimpleNamespace(time=time.time, monotonic=lambda: clock[0]))
    monkeypatch.setattr(DemoRuntimeHost, 'admit', staticmethod(lambda context: None))
    scope = DemoScope(account_id='123', account_epoch='epoch', strategy_id='s1')
    class Lease:
        credential_ref = 'demo-session:fixture'; expires_at = time.time()+1000
        def validate(self): pass
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        auth_until = time.time()+100
        def account(self, scope):
            return {'credential_ref':'demo-session:fixture','credential_revision':0,'revision':0,'state':'READY',
                    'qualification':{'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,'credential_revision':0,'product':'SPOT',
                                     'capabilities':{'LIMIT':{'state':'DEMO_VERIFIED','evidence_sha256':'e','official_contract_revision':'v'}}}}
        def strategy(self, scope): return {'allow_execution':True,'authorized_until':self.auth_until}
        def owner_guard(self, scope): return nullcontext()
        def command(self, scope, command_id): return {'method':'POST','purpose':'ENTRY','request':{'type':'LIMIT'}}
        def health(self, scope): return {'unknown':0,'unprotected':0,'pending_inbox':0,'risk_breaches':0}
        def begin_send(self, *args, **kwargs):
            may.append(True); return {'method':'POST','request':{'type':'LIMIT'}}
        def mark_unknown(self, *args): unknown.append(True)
        def active_orders(self, scope): return []
        def abandon_stale_unsent(self, *args, **kwargs): pass
        def pause(self, *args): pass
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self):
            if cross_at == 'sync': clock[0] = 11.0
        def send_order(self, request, *, permit):
            permit.consume('POST', request, self.credential_ref)
            if cross_at == 'signing': clock[0] = 11.0
            permit.before_network(); sent.append(True)
    class Reconciler:
        def reconcile(self, *args, **kwargs): pass
        def reconcile_and_resume(self, *args, **kwargs): pass
    class Host:
        memory = None
        async def assess_once(self, run, **kwargs): return {}
        def admit_assessment(self, run): return {'command_id':'id'}
        def drain_inbox(self): pass
        def protection_tick(self): return {'state':'ACTIVE', 'commands':[]}
        def status(self): return {'execution_authorized':False}
        def dispatch(self, command):
            now = int(time.time())
            context = AdmissionContext(now=now,quote_at=now,account_at=now,quote_source='BINANCE_DEMO',model_source='assessment_v1',decision_expires_at=now+100,capability_state='DEMO_VERIFIED',code_sha='a'*40,qualified_sha='a'*40,private_stream_healthy=True,account_state='READY',authorized=True,budget='100',available='100',risk_limit='100',unresolved_unknown=0,unprotected=0)
            return owner.dispatch(scope, command, context=context)
    Host.scope = scope
    class Stream:
        healthy = True
        async def run_window(self, transport, *, on_subscribed, **kwargs):
            try: await asyncio.wait_for(on_subscribed(self), timeout=0.2)
            except TimeoutError: pass
    repo, transport = Repo(), Transport()
    manager = DemoRuntimeService(connections=None, model_service=None, repository=repo, scope_resolver=None, current_code_sha='a'*40)
    runtime = _Runtime(Lease(), transport, Host(), Stream(), Reconciler()); manager._sessions['session'] = runtime
    owner = DemoExecutionOwner(repo, transport, preflight=lambda: manager._sender_preflight(runtime), physical_owner=SimpleNamespace(validate=lambda scope: None))
    asyncio.run(manager.run_window('session', duration_seconds=1, execute=True))
    assert runtime.last_error == ('DEMO_WINDOW_EXPIRED' if cross_at == 'sync' else 'DEMO_SEND_OUTCOME_UNKNOWN')
    assert not sent
    assert may == ([] if cross_at == 'sync' else [True])
    assert unknown == ([] if cross_at == 'sync' else [True])
    with pytest.raises(DemoError, match='DEMO_EXECUTION_PERMISSION_REQUIRED'):
        manager._sender_preflight(runtime)


def test_different_epochs_for_same_uid_cannot_bind_while_fencing_unqualified(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'cross-epoch.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport); repo = DemoRepository(engine)
    epochs = iter(['first','second','second'])
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=repo,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']),account_epoch=next(epochs),strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    payload = DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True)
    for session in ('a','b'): connections.connect(session, payload, session_expires_at=time.time()+1000)
    manager.bind_session('a'); runtime = manager._runtime('a'); runtime.running = True
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_RUNTIME_DRAIN_REQUIRED'): manager.bind_session('b')
    runtime.running = False
    with pytest.raises(DemoError, match='DEMO_CROSS_EPOCH_FENCING_UNQUALIFIED'): manager.bind_session('b')
    connections.close(); engine.dispose()


def test_offline_rebind_drains_all_inbox_batches_before_rotation(tmp_path, monkeypatch):
    from dataclasses import asdict
    import trading_system.execution.demo_reports as reports
    engine = create_engine('sqlite:///' + str(tmp_path/'batch.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(Transport); repo = DemoRepository(engine)
    manager = DemoRuntimeService(connections=connections, model_service=None, repository=repo,
        scope_resolver=lambda account: DemoScope(account_id=str(account['uid']),account_epoch='epoch',strategy_id='s1'), current_code_sha='a'*40)
    connections.runtime = manager
    payload = DemoConnect(api_key='fixture-key-123456', api_secret='fixture-secret-123456', acknowledge_demo_only=True)
    def connect(): connections.connect('session', payload, session_expires_at=time.time()+1000)
    connect(); manager.bind_session('session'); scope = manager._runtime('session').host.scope
    pending = [{'scope':asdict(scope),'observation_id':str(i)} for i in range(1001)]; processed = []
    monkeypatch.setattr(repo, 'pending_observations', lambda scope, limit: list(pending[:limit]))
    class Verifier:
        def __init__(self, repository): pass
        def process(self, scope, observation_id):
            assert pending[0]['observation_id'] == observation_id
            processed.append(pending.pop(0))
    monkeypatch.setattr(reports, 'DemoReportVerifier', Verifier)
    connect(); manager.bind_session('session')
    assert len(processed) == 1001 and not pending
    assert repo.account(scope)['credential_revision'] == 1
    connections.close(); engine.dispose()


def test_physical_fence_releases_only_after_all_inbox_batches_and_pause():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    events = []
    class Owner:
        def validate(self, scope): events.append('validate')
        def close(self): events.append('release')
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
        pages = iter([{'processed':100,'remaining':1},{'processed':1,'remaining':0}])
        def drain_inbox(self, *, limit): events.append('drain'); return next(self.pages)
    class Repo:
        def pause(self, scope, reason): events.append('pause')
        def health(self, scope): return {'pending_inbox':0}
    manager = DemoRuntimeService(connections=None, model_service=None, repository=Repo(), scope_resolver=None, current_code_sha='a'*40)
    manager._sessions['s'] = _Runtime(None, SimpleNamespace(close=lambda:events.append('transport-close')), Host(), SimpleNamespace(healthy=True), None, physical_owner=Owner())
    manager.close_session('s')
    assert events.count('drain') == 2
    assert events.index('pause') > max(i for i, event in enumerate(events) if event == 'drain')
    assert events.index('release') > events.index('pause')


def test_physical_fence_not_released_when_inbox_cannot_drain():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    events = []
    class Owner:
        def validate(self, scope): pass
        def close(self): events.append('release')
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
        def drain_inbox(self, *, limit): return {'processed':0,'remaining':1}
    class Repo:
        def pause(self, scope, reason): events.append('pause')
    manager = DemoRuntimeService(connections=None, model_service=None, repository=Repo(), scope_resolver=None, current_code_sha='a'*40)
    runtime = _Runtime(None, SimpleNamespace(close=lambda:events.append('transport-close')), Host(), SimpleNamespace(healthy=True), None, physical_owner=Owner())
    manager._sessions['s'] = runtime
    with pytest.raises(DemoError, match='DEMO_INBOX_DRAIN_BLOCKED'): manager.close_session('s')
    assert runtime.drain_failed and 'release' not in events


def test_status_fails_closed_when_physical_owner_lost():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Lease:
        def validate(self): pass
    class Owner:
        def validate(self, scope): raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
        def close(self): pytest.fail('lost dirty fence must not be treated as released')
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
        def status(self): return {'configured':True, 'execution_authorized':True}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
    manager._sessions['s'] = _Runtime(Lease(),SimpleNamespace(close=lambda:None),Host(),SimpleNamespace(healthy=True),None,physical_owner=Owner())
    status = manager.status('s')
    assert not status['configured'] and not status['execution_authorized']
    assert status['blocker'] == 'DEMO_PHYSICAL_OWNER_LOST'
    assert manager._sessions['s'].drain_failed


@pytest.mark.parametrize('failure', ['transport','owner'])
def test_idle_physical_close_failure_latches_dirty_and_preserves_session(failure):
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Owner:
        def validate(self, scope): pass
        def close(self):
            if failure == 'owner': raise OSError('owner close failed')
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
        def drain_inbox(self, *, limit): return {'processed':0,'remaining':0}
    class Repo:
        def pause(self, scope, reason): pass
        def health(self, scope): return {'pending_inbox':0}
    def close_transport():
        if failure == 'transport': raise OSError('transport close failed')
    manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
    runtime = _Runtime(None,SimpleNamespace(close=close_transport),Host(),SimpleNamespace(healthy=True),None,physical_owner=Owner())
    manager._sessions['s'] = runtime
    with pytest.raises(DemoError, match='DEMO_DRAIN_CLOSE_UNCONFIRMED'): manager.close_session('s')
    assert runtime.drain_failed and runtime.last_error == 'DEMO_DRAIN_CLOSE_UNCONFIRMED'
    assert manager._sessions['s'] is runtime


def test_bind_failure_closes_physical_owner_even_if_transport_close_fails(tmp_path, monkeypatch):
    from trading_system.storage.demo_physical import PhysicalOwner
    events = []; fail_close = [False]
    class FaultyTransport(Transport):
        def close(self):
            events.append('transport-close')
            if fail_close[0]: raise OSError('close failed')
    class Owner:
        generation = 1
        def validate(self, scope): pass
        def close(self): events.append('physical-close')
    engine = create_engine('sqlite:///' + str(tmp_path/'bind-close.sqlite')); metadata.create_all(engine)
    connections = DemoConnectionService(FaultyTransport); repo = DemoRepository(engine)
    manager = DemoRuntimeService(connections=connections,model_service=None,repository=repo,
        scope_resolver=lambda account:DemoScope(account_id=str(account['uid']),account_epoch='epoch',strategy_id='s'),current_code_sha='a'*40)
    payload = DemoConnect(api_key='fixture-key-123456',api_secret='fixture-secret-123456',acknowledge_demo_only=True)
    connections.connect('s',payload,session_expires_at=time.time()+1000)
    monkeypatch.setattr(engine.dialect,'name','postgresql')
    monkeypatch.setattr(PhysicalOwner,'acquire',classmethod(lambda cls,*args,**kwargs:Owner()))
    monkeypatch.setattr(repo,'register_account',lambda *args,**kwargs:(_ for _ in ()).throw(DemoError('DEMO_REGISTRATION_REJECTED')))
    fail_close[0] = True
    with pytest.raises(DemoError, match='DEMO_REGISTRATION_REJECTED'): manager.bind_session('s')
    assert events[-2:] == ['transport-close','physical-close']
    fail_close[0] = False; connections.close(); engine.dispose()


def test_slow_start_validation_does_not_block_loop_and_cancel_drains():
    from threading import Event
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event(); events = []
    class Lease:
        credential_ref='fixture'; expires_at=time.time()+1000
        def validate(self): pass
    class Owner:
        def validate(self, scope): entered.set(); assert release.wait(3)
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'fixture'}
        def pause(self, scope, reason): events.append('pause')
    class Stream:
        healthy = True
        async def run_window(self, *args, **kwargs): pytest.fail('cancelled start must not open stream')
    async def scenario():
        manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
        runtime = _Runtime(Lease(),None,Host(),Stream(),None,physical_owner=Owner()); manager._sessions['s'] = runtime
        task = asyncio.create_task(manager.run_window('s',duration_seconds=2))
        assert await asyncio.to_thread(entered.wait,2)
        await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.2)
        task.cancel(); release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert not runtime.running and events == ['pause']
    asyncio.run(scenario())


def test_slow_final_drain_does_not_block_loop_or_report_stopped():
    from threading import Event
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event(); events = []
    class Lease:
        credential_ref='fixture'; expires_at=time.time()+1000
        def validate(self): pass
    class Owner:
        def validate(self, scope): pass
        def close(self): events.append('release')
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
        def drain_inbox(self, *, limit): entered.set(); assert release.wait(3); return {'processed':1,'remaining':0}
        def status(self): return {'execution_authorized':False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'fixture'}
        def pause(self, scope, reason): events.append('pause')
        def health(self, scope): return {'pending_inbox':0}
    async def scenario():
        started = asyncio.Event()
        class Stream:
            healthy = True
            async def run_window(self, *args, **kwargs): started.set(); await asyncio.Event().wait()
        manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
        runtime = _Runtime(Lease(),SimpleNamespace(close=lambda:events.append('close')),Host(),Stream(),None,physical_owner=Owner()); manager._sessions['s'] = runtime
        manager.start_recovery('s',duration_seconds=30); await started.wait()
        manager.close_session('s')
        assert await asyncio.to_thread(entered.wait,2)
        await asyncio.wait_for(asyncio.sleep(0.01),timeout=0.2)
        assert runtime.running and 'release' not in events
        release.set(); await manager._jobs['s']['task']
        assert not runtime.running and events == ['pause','close','release']
    asyncio.run(scenario())


def test_recovery_scheduled_from_worker_uses_owning_event_loop():
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    class Lease:
        credential_ref='fixture'; expires_at=time.time()+1000
        def validate(self): pass
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
        def status(self): return {'execution_authorized':False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'fixture'}
        def pause(self, scope, reason): pass
    class Stream:
        healthy = True
        async def run_window(self, *args, **kwargs): pass
    async def scenario():
        manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
        manager._sessions['s'] = _Runtime(Lease(),None,Host(),Stream(),None)
        result = await asyncio.to_thread(manager.start_recovery,'s',duration_seconds=30,loop=asyncio.get_running_loop())
        assert result['state'] == 'START_REQUESTED'
        await manager._jobs['s']['task']
        assert manager._jobs['s']['state'] == 'ENDED_REVIEW_REQUIRED'
    asyncio.run(scenario())


def test_unresponsive_loop_cannot_later_start_an_untracked_window(monkeypatch):
    from types import SimpleNamespace
    import trading_system.runtime.demo_service as module
    from trading_system.runtime.demo_service import _Runtime
    monkeypatch.setattr(module, '_SCHEDULE_WAIT_SECONDS', 0.01)
    class Lease:
        credential_ref='fixture'; expires_at=time.time()+1000
        def validate(self): pass
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'fixture'}
    class Loop:
        callback = None
        def is_running(self): return True
        def call_soon_threadsafe(self, callback): self.callback = callback
        def create_task(self, *args, **kwargs): pytest.fail('timed-out callback must not create a task')
    loop = Loop()
    manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
    manager._sessions['s'] = _Runtime(Lease(),None,Host(),None,None)
    with pytest.raises(DemoError, match='DEMO_EVENT_LOOP_UNAVAILABLE'):
        manager.start_recovery('s',duration_seconds=30,loop=loop)
    loop.callback()
    assert not manager._jobs


def test_shutdown_drain_does_not_block_loop_and_survives_caller_cancellation():
    from threading import Event
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event(); events = []
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
    class Repo:
        def pause(self, scope, reason): entered.set(); assert release.wait(3); events.append('pause')
    async def scenario():
        manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40,close_repository=lambda:events.append('dispose'))
        manager._sessions['s'] = _Runtime(None,SimpleNamespace(close=lambda:events.append('close')),Host(),SimpleNamespace(healthy=True),None)
        shutdown = asyncio.create_task(manager.aclose())
        assert await asyncio.to_thread(entered.wait,2)
        await asyncio.wait_for(asyncio.sleep(0.01),timeout=0.2)
        shutdown.cancel(); await asyncio.sleep(0.01); assert not shutdown.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await shutdown
        assert events == ['pause','close','dispose']
    asyncio.run(scenario())


def test_shutdown_rejects_later_bind_and_direct_window():
    from types import SimpleNamespace
    manager = DemoRuntimeService(connections=None,model_service=None,repository=SimpleNamespace(),scope_resolver=None,current_code_sha='a'*40)
    manager.close()
    with pytest.raises(DemoError, match='DEMO_SERVICE_CLOSED'): manager.bind_session('s')
    with pytest.raises(DemoError, match='DEMO_SERVICE_CLOSED'): asyncio.run(manager.run_window('s',duration_seconds=1))


def test_graceful_stop_waits_workers_then_final_reconciles_while_stream_healthy():
    from threading import Event
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event(); events = []
    class Lease:
        credential_ref='fixture'; expires_at=time.time()+1000
        def validate(self): pass
    class Owner:
        def validate(self, scope): pass
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s'); memory = None
        def drain_inbox(self, *, limit=100):
            if not entered.is_set(): entered.set(); assert release.wait(3); events.append('worker-settled')
            else: events.append('drain-final')
            return {'processed':0,'remaining':0}
        def status(self): return {'execution_authorized':False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'fixture'}
        def pause(self, scope, reason): events.append('pause')
        def health(self, scope): return {'pending_inbox':0}
    class Reconciler:
        def reconcile(self, *args, **kwargs): pass
        def reconcile_for_clean(self, scope, *, stream, current_code_sha):
            assert stream.healthy and events == ['worker-settled']
            events.append('final-reconcile'); return {'fixture':'not-qualified'}
    class Stream:
        healthy = True; supports_graceful_stop = True
        async def run_window(self, transport, *, on_subscribed, **kwargs):
            await on_subscribed(self); events.append('stream-closed'); self.healthy = False
    async def scenario():
        manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
        runtime = _Runtime(Lease(),None,Host(),Stream(),Reconciler(),physical_owner=Owner()); manager._sessions['s'] = runtime
        task = asyncio.create_task(manager.run_window('s',duration_seconds=5))
        assert await asyncio.to_thread(entered.wait,2)
        assert manager.request_stop('s')['state'] == 'STOP_REQUESTED'
        await asyncio.sleep(0.03); assert 'final-reconcile' not in events and not task.done()
        release.set(); await task
        assert events == ['worker-settled','final-reconcile','stream-closed','drain-final','pause']
        assert runtime.final_receipt == {'fixture':'not-qualified'}
    asyncio.run(scenario())


def test_disconnect_upgrades_pending_graceful_stop_to_hard_cancel():
    from threading import Event
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    entered, release = Event(), Event(); events = []
    class Lease:
        credential_ref='fixture'; expires_at=time.time()+1000
        def validate(self): pass
    class Owner:
        def validate(self, scope): pass
        def close(self): events.append('release')
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s'); memory = None
        def drain_inbox(self, *, limit=100): return {'processed':0,'remaining':0}
        def status(self): return {'execution_authorized':False}
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def account(self, scope): return {'credential_ref':'fixture'}
        def pause(self, scope, reason): events.append('pause')
        def health(self, scope): return {'pending_inbox':0}
    class Reconciler:
        def reconcile(self, *args, **kwargs): pass
        def reconcile_for_clean(self, *args, **kwargs): entered.set(); assert release.wait(3); return {'fixture':'unqualified'}
    class Stream:
        healthy=True; supports_graceful_stop=True
        async def run_window(self, transport, *, on_subscribed, **kwargs): await on_subscribed(self)
    async def scenario():
        manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
        runtime = _Runtime(Lease(),SimpleNamespace(close=lambda:events.append('close')),Host(),Stream(),Reconciler(),physical_owner=Owner()); manager._sessions['s']=runtime
        manager.start_recovery('s',duration_seconds=30)
        for _ in range(100):
            if runtime.running: break
            await asyncio.sleep(0.01)
        manager.request_stop('s'); assert await asyncio.to_thread(entered.wait,2)
        manager.close_session('s'); await asyncio.sleep(0.03)
        assert not manager._jobs['s']['task'].done() and 'release' not in events
        release.set(); await manager._jobs['s']['task']
        assert runtime.final_receipt is None and events == ['pause','close','release']
    asyncio.run(scenario())


@pytest.mark.parametrize('close_fails', [False, True])
def test_clean_handoff_published_only_after_commit_ack_and_transport_close(close_fails):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from trading_system.runtime.demo_service import _Runtime
    events = []
    class Owner:
        def validate(self, scope): pass
        def commit_clean(self, scope, receipt, **kwargs): events.append('clean-commit-ack'); return {'fixture':'acknowledged'}
        def close(self): events.append('owner-close')
    class Sender:
        def validate(self): pass
    class Host:
        scope = DemoScope(account_id='123',account_epoch='epoch',strategy_id='s')
        def drain_inbox(self, *, limit): events.append('drain'); return {'processed':0,'remaining':0}
    class Repo:
        def pause(self, scope, reason): events.append('pause')
        def health(self, scope): return {'pending_inbox':0}
        @contextmanager
        def owner_guard(self, scope): yield Sender()
    def close_transport():
        events.append('transport-close')
        if close_fails: raise OSError('close failed')
    manager = DemoRuntimeService(connections=None,model_service=None,repository=Repo(),scope_resolver=None,current_code_sha='a'*40)
    runtime = _Runtime(None,SimpleNamespace(close=close_transport),Host(),SimpleNamespace(healthy=False,closed_proof=object()),None,physical_owner=Owner(),final_receipt={'fixture':'not-qualified'})
    manager._sessions['s'] = runtime
    if close_fails:
        with pytest.raises(OSError): manager._finish_window('s',runtime)
        assert not manager._clean_handoffs and runtime.drain_failed
    else:
        manager._finish_window('s',runtime)
        assert manager._clean_handoffs[runtime.host.scope.physical_account_key] == {'fixture':'acknowledged'}
        assert 's' not in manager._sessions
    assert events[:4] == ['drain','pause','clean-commit-ack','transport-close']
