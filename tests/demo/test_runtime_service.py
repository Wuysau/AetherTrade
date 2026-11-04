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
        async def assess_once(self, run):
            counters['assessing'] = True
            if fail_model:
                await asyncio.sleep(0.1)
                raise DemoError('DEMO_MODEL_ERROR')
            await asyncio.sleep(10)
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
        def strategy(self, scope): return {'allow_execution': True, 'authorized_until': time.time()+1000}
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
