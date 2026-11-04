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
        expires_at = time.time() + 1000
        def validate(self): return True
    class Host:
        scope = 'scope'
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
