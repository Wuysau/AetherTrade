"""Demo operator routes exercise injected offline fakes; no venue calls."""
from fastapi.testclient import TestClient
from trading_system.server.app import create_app
from trading_system.server.config import Settings


class Runtime:
    def __init__(self): self.calls = []
    def status(self, session): return {'configured': True, 'execution_authorized': False}
    def operator_status(self, session): return self.status(session)
    def operator_observations(self, session, *, limit=50): self.calls.append(('observe', session, limit)); return {'decisions': [], 'scope': 'session'}
    def bind_session(self, session): self.calls.append(('bind', session)); return self.status(session)
    def configure_strategy(self, session, value): self.calls.append(('configure', session, value)); return {'revision': 1, 'execution_authorized': False}
    def start_window(self, session, **kwargs): self.calls.append(('start', session, kwargs)); return {'state': 'START_REQUESTED'}
    def start_recovery(self, session, **kwargs): self.calls.append(('recover', session, kwargs)); return {'state': 'START_REQUESTED'}
    def request_stop(self, session): self.calls.append(('stop', session)); return {'state': 'STOP_REQUESTED'}
    def takeover_status(self, session): return {'state':'NEEDS_OPERATOR','digest':'a'*64,'flat_account_verified':False}
    def acknowledge_takeover(self, session, expected_digest): self.calls.append(('takeover',session,expected_digest)); return {'state':'TAKEOVER_ACKNOWLEDGED','execution_authorized':False}
    def close_session(self, session): pass
    def close(self): pass


def client_for(tmp_path, runtime):
    settings = Settings(database_url=f'sqlite:///{tmp_path / "app.sqlite"}', testing=True, market_enabled=False, model_key_path=tmp_path/'key')
    app = create_app(settings, demo_runtime_factory=(lambda **kwargs: runtime) if runtime is not None else None)
    return TestClient(app, headers={'origin': 'http://localhost:8787'})


def auth(client):
    response = client.post('/api/setup', json={'username':'owner', 'password':'long-password-here', 'initial_cash':'10000'})
    client.headers['x-csrf-token'] = response.json()['csrf_token']


def test_takeover_endpoint_requires_auth_csrf_and_explicit_responsibility(tmp_path):
    runtime=Runtime()
    with client_for(tmp_path,runtime) as client:
        assert client.get('/api/demo/runtime/takeover').status_code==401
        auth(client)
        assert client.get('/api/demo/runtime/takeover').json()['flat_account_verified'] is False
        payload={'expected_digest':'a'*64,'acknowledge_responsibility':True}
        token=client.headers.pop('x-csrf-token')
        assert client.post('/api/demo/runtime/takeover',json=payload).status_code==403
        client.headers['x-csrf-token']=token
        assert client.post('/api/demo/runtime/takeover',json={**payload,'acknowledge_responsibility':False}).status_code==422
        assert client.post('/api/demo/runtime/takeover',json=payload).json()['state']=='TAKEOVER_ACKNOWLEDGED'
        assert runtime.calls[-1][2]=='a'*64


def test_operator_routes_require_auth_csrf_and_explicit_acknowledgement(tmp_path):
    runtime = Runtime()
    with client_for(tmp_path, runtime) as client:
        assert client.get('/api/demo/runtime/status').status_code == 401
        assert client.get('/api/demo/runtime/observations').status_code == 401
        auth(client)
        assert client.get('/api/demo/runtime/observations?limit=101').status_code == 422
        assert client.get('/api/demo/runtime/observations?limit=5&scope=other').json()['scope'] == 'session'
        assert runtime.calls[-1][0] == 'observe'
        runtime.calls.clear()
        token = client.headers.pop('x-csrf-token')
        assert client.post('/api/demo/runtime/bind', json={'acknowledge_account': True}).status_code == 403
        client.headers['x-csrf-token'] = token
        assert client.post('/api/demo/runtime/bind', json={'acknowledge_account': False}).status_code == 422
        assert client.post('/api/demo/runtime/bind', json={'acknowledge_account': True, 'account_id': 'client-supplied'}).status_code == 422
        assert not runtime.calls
        assert client.post('/api/demo/runtime/bind', json={'acknowledge_account': True}).status_code == 200
        config = {'model_profile_id':'default', 'model_revision':1, 'expected_strategy_revision':0, 'budget':'100', 'max_planned_loss':'2', 'max_daily_loss':'5', 'fee_rate_cap':'0.001', 'holding_period_seconds':300, 'authorization_seconds':300, 'acknowledge_model_cost':True, 'acknowledge_demo_execution':True}
        assert client.post('/api/demo/runtime/strategy', json={**config, 'acknowledge_model_cost': False}).status_code == 422
        assert client.post('/api/demo/runtime/strategy', json=config).status_code == 200
        window = {'duration_seconds':60, 'decision_interval_seconds':30, 'expected_strategy_revision':1, 'expected_grant_hash':'f'*64, 'acknowledge_model_cost':True, 'acknowledge_demo_orders':True}
        assert client.post('/api/demo/runtime/start', json={**window, 'acknowledge_demo_orders':False}).status_code == 422
        assert client.post('/api/demo/runtime/start', json=window).json()['state'] == 'START_REQUESTED'
        assert client.post('/api/demo/runtime/stop').json()['state'] == 'STOP_REQUESTED'
        assert client.post('/api/demo/runtime/recover', json={'duration_seconds':60, 'acknowledge_state_changes':True}).json()['state'] == 'START_REQUESTED'
        assert [item[0] for item in runtime.calls] == ['bind','configure','start','stop','recover']


def test_unconfigured_operator_routes_fail_closed(tmp_path):
    with client_for(tmp_path, None) as client:
        auth(client)
        result = client.post('/api/demo/runtime/bind', json={'acknowledge_account': True})
        assert result.status_code == 503 and result.json()['error']['code'] == 'DEMO_RUNTIME_NOT_CONFIGURED'


def test_real_runtime_routes_configure_but_sqlite_cannot_start_execution(tmp_path, monkeypatch):
    import time
    from sqlalchemy import create_engine
    from trading_system.storage.demo import DemoRepository, metadata
    from trading_system.runtime.demo_service import DemoRuntimeService
    from trading_system.engine.binance_demo.contracts import DemoScope
    class Transport:
        def __init__(self, provider, ref): self.credential_ref = ref
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def close(self): pass
        def sync_time(self): return 1
        def account(self): return {'uid':123, 'accountType':'SPOT', 'canTrade':True, 'balances':[]}
        def exchange_info(self, *args): return {'symbols':[{'symbol':'BTCUSDT'}]}
        def open_orders(self, *args): return []
    engine = create_engine(f'sqlite:///{tmp_path / "demo.sqlite"}', connect_args={'check_same_thread':False}); metadata.create_all(engine)
    def factory(*, connections, model_service):
        return DemoRuntimeService(connections=connections, model_service=model_service, repository=DemoRepository(engine),
            scope_resolver=lambda account: DemoScope(account_id=str(account['uid']),account_epoch='test',strategy_id='s1'), current_code_sha='a'*40)
    settings = Settings(database_url=f'sqlite:///{tmp_path / "console.sqlite"}', testing=True, market_enabled=False, model_key_path=tmp_path/'key')
    app = create_app(settings, demo_runtime_factory=factory, demo_transport_factory=Transport)
    monkeypatch.setattr(app.state.models, 'get_config', lambda profile: {'id':profile,'revision':4,'enabled':True,'key_available':True,'local_mode':False})
    with TestClient(app, headers={'origin':'http://localhost:8787'}) as client:
        auth(client)
        epoch = client.get('/api/demo/status').json()['connection_epoch']
        connected = client.post('/api/demo/connect', json={'api_key':'fixture-key-123456789','api_secret':'fixture-secret-123456','acknowledge_demo_only':True,'connection_epoch':epoch})
        assert connected.status_code == 200
        assert client.post('/api/demo/runtime/bind', json={'acknowledge_account':True}).status_code == 200
        config = {'model_profile_id':'model-a','model_revision':4,'expected_strategy_revision':0,'budget':'100','max_planned_loss':'2','max_daily_loss':'5','fee_rate_cap':'0.001','holding_period_seconds':300,'authorization_seconds':300,'acknowledge_model_cost':True,'acknowledge_demo_execution':True}
        assert client.post('/api/demo/runtime/strategy', json=config).json()['revision'] == 1
        assert client.get('/api/demo/runtime/status').json()['strategy_revision'] == 1
        observations = client.get('/api/demo/runtime/observations').json()
        assert observations['orders'] == [] and observations['pnl']['state'] == 'UNAVAILABLE'
        window = {'duration_seconds':60,'decision_interval_seconds':30,'expected_strategy_revision':1,'expected_grant_hash':'f'*64,'acknowledge_model_cost':True,'acknowledge_demo_orders':True}
        rejected = client.post('/api/demo/runtime/start', json=window)
        assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'DEMO_POSTGRESQL_OWNER_REQUIRED'
        assert not app.state.demo_runtime._runtime(next(iter(app.state.demo_runtime._sessions))).running
    engine.dispose()


def test_slow_start_admission_does_not_block_async_server():
    import asyncio
    from threading import Event
    from types import SimpleNamespace
    import httpx
    from fastapi import FastAPI
    from trading_system.server.demo_control_api import install_demo_control_routes
    entered, release = Event(), Event()
    class SlowRuntime:
        def start_window(self, session, **kwargs):
            entered.set(); assert release.wait(3)
            return {'state':'START_REQUESTED'}
    app = FastAPI()
    install_demo_control_routes(app, SimpleNamespace(runtime=SlowRuntime()), lambda:True, lambda:True, SimpleNamespace(cookie_name='session'))
    @app.get('/ping')
    async def ping(): return {'ok':True}
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            payload = {'duration_seconds':60,'decision_interval_seconds':30,'expected_strategy_revision':1,'expected_grant_hash':'f'*64,'acknowledge_model_cost':True,'acknowledge_demo_orders':True}
            start = asyncio.create_task(client.post('/api/demo/runtime/start',json=payload))
            assert await asyncio.to_thread(entered.wait,2)
            response = await asyncio.wait_for(client.get('/ping'),timeout=0.2)
            assert response.json() == {'ok':True}
            release.set(); assert (await start).status_code == 200
    asyncio.run(scenario())
