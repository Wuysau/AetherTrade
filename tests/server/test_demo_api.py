"""Authenticated Demo setup, with no real keys or venue access."""
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from trading_system.server.app import create_app
from trading_system.server.config import Settings
from trading_system.engine.binance_demo.transport import BinanceDemoTransport


def make_client(tmp_path, failure=False):
    def handle(request):
        if failure:
            return httpx.Response(400, json={'code': -2015, 'msg': 'fixture-secret-123456'})
        if request.url.path.endswith('/time'):
            return httpx.Response(200, json={'serverTime': int(time.time() * 1000)})
        if request.url.path.endswith('/account'):
            return httpx.Response(200, json={'accountType': 'SPOT', 'canTrade': True, 'balances': [{'asset': 'USDT', 'free': '45', 'locked': '5'}]})
        if request.url.path.endswith('/exchangeInfo'):
            return httpx.Response(200, json={'symbols': [{'symbol': 'BTCUSDT', 'status': 'TRADING', 'baseAsset': 'BTC', 'quoteAsset': 'USDT'}]})
        if request.url.path.endswith('/openOrders'):
            return httpx.Response(200, json=[])
        pytest.fail('Unexpected Demo endpoint: ' + request.url.path)
    settings = Settings(database_url=f'sqlite:///{tmp_path / "console.sqlite3"}', testing=True, market_enabled=False,
        allowed_origins=('http://testserver',), model_key_path=tmp_path / 'key')
    app = create_app(settings, demo_transport_factory=lambda provider, ref: BinanceDemoTransport(provider, ref, http_transport=httpx.MockTransport(handle)))
    return TestClient(app)


def login(client):
    client.headers['Origin'] = 'http://testserver'
    result = client.post('/api/setup', json={'username': 'owner', 'password': 'long-password-here', 'initial_cash': '10000'})
    client.headers['X-CSRF-Token'] = result.json()['csrf_token']


PAYLOAD = {'api_key': 'fixture-key-123456789', 'api_secret': 'fixture-secret-123456', 'acknowledge_demo_only': True}


def test_demo_requires_auth_csrf_and_explicit_ack(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get('/api/demo/status').status_code == 401
        login(client)
        token = client.headers.pop('X-CSRF-Token')
        assert client.post('/api/demo/connect', json=PAYLOAD).status_code == 403
        client.headers['X-CSRF-Token'] = token
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'acknowledge_demo_only': False}).status_code == 422
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'base_url': 'https://api.binance.com'}).status_code == 422


def test_user_filled_key_only_returns_demo_balance_and_no_authority(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        response = client.post('/api/demo/connect', json=PAYLOAD)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['environment'] == 'BINANCE_DEMO'
        assert data['state'] == 'READ_ONLY'
        assert data['balances'] == [{'asset': 'USDT', 'free': '45', 'locked': '5'}]
        assert data['execution_authorized'] is False
        assert data['credential_storage'] == 'MEMORY_ONLY'
        assert 'fixture-key' not in response.text and 'fixture-secret' not in response.text
        assert client.get('/api/bootstrap').json()['environment'] == 'PAPER'
        assert client.post('/api/demo/orders', json={}).status_code in {404, 405}
        assert client.post('/api/session/logout').status_code == 200
        assert client.app.state.demo.connection_count == 0
    raw = (tmp_path / 'console.sqlite3').read_bytes()
    assert b'fixture-secret' not in raw and b'fixture-key' not in raw


def test_demo_errors_are_redacted_and_failed_key_discarded(tmp_path):
    with make_client(tmp_path, failure=True) as client:
        login(client)
        response = client.post('/api/demo/connect', json=PAYLOAD)
        assert response.status_code == 422
        assert 'fixture-secret' not in response.text
        assert client.app.state.demo.connection_count == 0
        assert client.get('/api/demo/status').json()['state'] == 'DISABLED'


def test_disconnect_clears_ephemeral_session_without_touching_paper(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        assert client.post('/api/demo/connect', json=PAYLOAD).status_code == 200
        result = client.post('/api/demo/disconnect')
        assert result.json()['state'] == 'DISABLED'
        assert client.app.state.demo.connection_count == 0
        assert client.get('/api/dashboard').json()['account']['cash'] == '10000'


def test_disconnect_during_connect_does_not_resurrect_credentials():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from trading_system.server.demo_api import DemoConnect, DemoConnectionService
    from trading_system.engine.binance_demo.contracts import DemoError
    entered, release = Event(), Event()
    class Transport:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def sync_time(self): return {}
        def account(self):
            entered.set()
            assert release.wait(2)
            return {'accountType': 'SPOT', 'balances': []}
        def exchange_info(self, symbol): return {'symbols': [{'symbol': symbol}]}
        def open_orders(self, symbol): return []
    service = DemoConnectionService(Transport, clock=lambda: 100)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.connect, 'session', DemoConnect(**PAYLOAD), session_expires_at=1000)
        assert entered.wait(2)
        assert service.disconnect('session')['state'] == 'DISABLED'
        release.set()
        with pytest.raises(DemoError, match='CONNECTION_CANCELED'):
            future.result(2)
    assert service.connection_count == 0
    assert service.status('session')['state'] == 'DISABLED'
    service.close()
