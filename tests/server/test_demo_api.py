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


def payload_for(client):
    return {**PAYLOAD, 'connection_epoch': client.get('/api/demo/status').json()['connection_epoch']}


def disconnect_for(client):
    epoch = client.get('/api/demo/status').json()['connection_epoch']
    return client.post('/api/demo/disconnect', json={'connection_epoch': epoch})


def test_demo_requires_auth_csrf_and_explicit_ack(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get('/api/demo/status').status_code == 401
        login(client)
        token = client.headers.pop('X-CSRF-Token')
        assert client.post('/api/demo/connect', json=payload_for(client)).status_code == 403
        client.headers['X-CSRF-Token'] = token
        assert client.post('/api/demo/connect', json={**payload_for(client), 'acknowledge_demo_only': False}).status_code == 422
        assert client.post('/api/demo/connect', json={**payload_for(client), 'base_url': 'https://api.binance.com'}).status_code == 422


def test_user_filled_key_only_returns_demo_balance_and_no_authority(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        response = client.post('/api/demo/connect', json=payload_for(client))
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
        response = client.post('/api/demo/connect', json=payload_for(client))
        assert response.status_code == 422
        assert 'fixture-secret' not in response.text
        assert client.app.state.demo.connection_count == 0
        assert client.get('/api/demo/status').json()['state'] == 'DISABLED'


def test_disconnect_clears_ephemeral_session_without_touching_paper(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        assert client.post('/api/demo/connect', json=payload_for(client)).status_code == 200
        result = disconnect_for(client)
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


def test_connection_lease_is_internal_session_bound_and_revoked_on_disconnect(tmp_path):
    from trading_system.server.demo_api import session_key
    from trading_system.engine.binance_demo.contracts import DemoError
    with make_client(tmp_path) as client:
        login(client)
        client.post('/api/demo/connect', json=payload_for(client))
        service = client.app.state.demo
        key = next(iter(service._entries))
        lease = service.connection_lease(key)
        assert lease.validate() is True
        with pytest.raises(DemoError): service.connection_lease('different-session')
        disconnect_for(client)
        with pytest.raises(DemoError): lease.validate()


def test_logout_invalidates_already_authenticated_connect_before_credential_storage(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    with make_client(tmp_path) as client:
        login(client)
        service = client.app.state.demo
        original = service.connect
        def delayed(*args, **kwargs):
            entered.set(); assert release.wait(3)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, 'connect', delayed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            connect = pool.submit(client.post, '/api/demo/connect', json=payload_for(client))
            assert entered.wait(3)
            assert client.post('/api/session/logout').status_code == 200
            release.set()
            response = connect.result(timeout=3)
        assert response.status_code != 200
        assert service.connection_count == 0
        assert not service.provider._values


def test_disconnect_invalidates_prior_authenticated_connect_but_allows_new_connect(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    with make_client(tmp_path) as client:
        login(client)
        service = client.app.state.demo
        original = service.connect
        def delayed(*args, **kwargs):
            entered.set(); assert release.wait(3)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, 'connect', delayed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            connect = pool.submit(client.post, '/api/demo/connect', json=payload_for(client))
            assert entered.wait(3)
            assert client.get('/api/demo/status').json()['state'] == 'CONNECTING'
            assert disconnect_for(client).json()['state'] == 'DISABLED'
            release.set()
            assert connect.result(timeout=3).status_code != 200
        assert service.connection_count == 0 and not service.provider._values
        monkeypatch.setattr(service, 'connect', original)
        assert client.post('/api/demo/connect', json=payload_for(client)).status_code == 200
        assert service.connection_count == 1


def test_preflight_epoch_is_required_and_old_epoch_rejected_after_disconnect(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        first = client.get('/api/demo/status').json()['connection_epoch']
        assert client.post('/api/demo/connect', json=PAYLOAD).status_code == 422
        after = disconnect_for(client).json()['connection_epoch']
        assert first != after
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': first}).status_code == 422
        assert client.app.state.demo.connection_count == 0
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': after}).status_code == 200


def test_connect_delayed_before_authorization_cannot_cross_disconnect(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    with make_client(tmp_path) as client:
        login(client)
        epoch = client.get('/api/demo/status').json()['connection_epoch']
        service = client.app.state.demo; original = service.authorize_connect
        def delayed(*args, **kwargs):
            entered.set(); assert release.wait(3)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, 'authorize_connect', delayed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            connect = pool.submit(client.post, '/api/demo/connect', json={**PAYLOAD, 'connection_epoch': epoch})
            assert entered.wait(3)
            assert disconnect_for(client).status_code == 200
            release.set()
            assert connect.result(timeout=3).status_code == 422
        assert service.connection_count == 0 and not service.provider._values


def test_pending_reconnect_does_not_present_existing_connection_as_current(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    with make_client(tmp_path) as client:
        login(client)
        first = client.post('/api/demo/connect', json=payload_for(client))
        assert first.status_code == 200
        service = client.app.state.demo; original = service.connect
        def delayed(*args, **kwargs):
            entered.set(); assert release.wait(3)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, 'connect', delayed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            reconnect = pool.submit(client.post, '/api/demo/connect', json={**PAYLOAD, 'connection_epoch': first.json()['connection_epoch']})
            assert entered.wait(3)
            assert client.get('/api/demo/status').json()['state'] == 'CONNECTING'
            release.set()
            assert reconnect.result(timeout=3).status_code == 200


def test_preflight_epoch_cannot_be_replayed_or_used_by_another_session(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        token_a = client.get('/api/demo/status').json()['connection_epoch']
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': token_a}).status_code == 200
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': token_a}).status_code == 422
        assert client.app.state.demo.connection_count == 1
        other = client.post('/api/session/login', json={'username': 'owner', 'password': 'long-password-here'})
        client.headers['X-CSRF-Token'] = other.json()['csrf_token']
        client.get('/api/demo/status')
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': token_a}).status_code == 422
        assert client.app.state.demo.connection_count == 1


def test_logout_prevents_connect_delayed_before_authentication(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    with make_client(tmp_path) as client:
        login(client)
        epoch = client.get('/api/demo/status').json()['connection_epoch']
        service = client.app.state.demo; original = service.authorize_connect
        def delayed(*args, **kwargs):
            entered.set(); assert release.wait(3)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, 'authorize_connect', delayed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            connect = pool.submit(client.post, '/api/demo/connect', json={**PAYLOAD, 'connection_epoch': epoch})
            assert entered.wait(3)
            assert client.post('/api/session/logout').status_code == 200
            release.set()
            assert connect.result(timeout=3).status_code == 401
        assert service.connection_count == 0 and not service.provider._values


def test_closed_service_cannot_issue_new_tickets_or_restore_credentials():
    from trading_system.server.demo_api import DemoConnectionService, DemoConnect
    from trading_system.engine.binance_demo.contracts import DemoError
    service = DemoConnectionService(clock=lambda: 100)
    service.close()
    with pytest.raises(DemoError, match='DEMO_SERVICE_CLOSED'):
        service.authorize_connect('session', lambda: {'expires_at': '2030-01-01T00:00:00Z'})
    with pytest.raises(DemoError, match='DEMO_SERVICE_CLOSED'):
        service.connect('session', DemoConnect(**PAYLOAD), session_expires_at=1000)
    assert not service.provider._values


def test_invalid_connect_body_does_not_leave_a_phantom_pending_ticket(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        payload = {**payload_for(client), 'unexpected_field': True}
        assert client.post('/api/demo/connect', json=payload).status_code == 422
        assert client.get('/api/demo/status').json()['state'] == 'DISABLED'
        assert not client.app.state.demo._pending_connect


def test_expired_preflight_epoch_fails_closed_and_can_be_refreshed(tmp_path, monkeypatch):
    with make_client(tmp_path) as client:
        login(client)
        service = client.app.state.demo
        current = [100.0]
        monkeypatch.setattr(service, 'clock', lambda: current[0])
        expired = client.get('/api/demo/status').json()['connection_epoch']
        current[0] += 901
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': expired}).status_code == 422
        assert service.connection_count == 0 and not service.provider._values
        fresh = client.get('/api/demo/status').json()['connection_epoch']
        assert fresh != expired
        assert client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': fresh}).status_code == 200


def test_non_ascii_preflight_epoch_is_rejected_without_pending_ticket(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        bad = {**payload_for(client), 'connection_epoch': '误' * 16}
        assert client.post('/api/demo/connect', json=bad).status_code == 422
        assert client.get('/api/demo/status').json()['state'] == 'DISABLED'
        assert not client.app.state.demo._pending_connect


def test_delayed_old_disconnect_cannot_remove_new_connection(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    with make_client(tmp_path) as client:
        login(client)
        old_epoch = client.get('/api/demo/status').json()['connection_epoch']
        service = client.app.state.demo; original = service.disconnect
        def delayed(*args, **kwargs):
            entered.set(); assert release.wait(3)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, 'disconnect', delayed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            remove = pool.submit(client.post, '/api/demo/disconnect', json={'connection_epoch': old_epoch})
            assert entered.wait(3)
            assert client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': old_epoch}).status_code == 200
            release.set()
            assert remove.result(timeout=3).status_code == 422
        assert service.connection_count == 1


def test_stale_preflight_does_not_cancel_valid_pending_connect(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    with make_client(tmp_path) as client:
        login(client)
        epoch = client.get('/api/demo/status').json()['connection_epoch']
        service = client.app.state.demo; original = service.connect
        def delayed(*args, **kwargs):
            entered.set(); assert release.wait(3)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, 'connect', delayed)
        with ThreadPoolExecutor(max_workers=1) as pool:
            valid = pool.submit(client.post, '/api/demo/connect', json={**PAYLOAD, 'connection_epoch': epoch})
            assert entered.wait(3)
            stale = client.post('/api/demo/connect', json={**PAYLOAD, 'connection_epoch': 'stale-epoch-1234567890'})
            assert stale.status_code == 422
            release.set()
            assert valid.result(timeout=3).status_code == 200
        assert service.connection_count == 1


def test_missing_disconnect_epoch_fails_closed(tmp_path):
    with make_client(tmp_path) as client:
        login(client)
        assert client.post('/api/demo/connect', json=payload_for(client)).status_code == 200
        assert client.post('/api/demo/disconnect').status_code == 422
        assert client.app.state.demo.connection_count == 1
