"""Authenticated market boundaries and SSE coalescing/revocation without upstream I/O."""
import asyncio
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from trading_system.server.app import create_app
from trading_system.server.config import Settings
from trading_system.server.errors import ApiError
from trading_system.server.market_api import market_events
from server.test_model_api import Quotes, login


class Feed(Quotes):
    started = 0
    configured = 0
    tested = None
    history = None

    async def start(self):
        self.started += 1

    async def reconfigure(self):
        self.configured += 1

    async def candles(self, symbol, interval, limit):
        self.history = (symbol, interval, limit)
        return dict(symbol=symbol, interval=interval, source_revision=self.configured, candles=[], source='controlled')

    async def test_connection(self, revision):
        self.tested = revision
        return dict(ok=True, revision=revision, message='Controlled adapter checked', quote_count=6)


@pytest.fixture
def market_api(tmp_path):
    feed = Feed()
    settings = Settings(database_url=f'sqlite:///{tmp_path / "market.sqlite3"}', testing=True,
                        market_enabled=False, allowed_origins=('http://testserver',),
                        model_key_path=tmp_path / 'secrets.key', web_dist=tmp_path / 'dist')
    app = create_app(settings, quote_provider=feed)
    with TestClient(app) as client:
        client.headers['Origin'] = 'http://testserver'
        yield client, feed


def test_market_endpoints_require_auth_and_write_csrf(market_api):
    client, feed = market_api
    for path in ('config', 'snapshot', 'candles?symbol=BTCUSDT', 'stream'):
        assert client.get('/api/market/' + path).status_code == 401
    login(client)
    config = client.get('/api/market/config').json()
    assert config['provider'] == 'binance' and config['revision'] == 0 and not config['has_api_key']
    csrf = client.headers.pop('X-CSRF-Token')
    assert client.put('/api/market/config', json={'expected_revision': 0, 'enabled': False}).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    saved = client.put('/api/market/config', json={'expected_revision': 0, 'enabled': False})
    assert saved.status_code == 200 and saved.json()['revision'] == 1 and feed.configured == 1
    assert client.put('/api/market/config', json={'expected_revision': 0, 'enabled': True}).status_code == 409
    assert feed.configured == 1
    assert client.get('/api/market/stream', headers={'Origin': 'https://untrusted.example'}).status_code == 403


def test_custom_key_is_write_only_and_test_is_explicit(market_api):
    client, feed = market_api
    login(client)
    response = client.put('/api/market/config', json={
        'expected_revision': 0, 'provider': 'standard_json', 'name': 'Controlled market',
        'rest_url': 'http://127.0.0.1:18084/v1', 'ws_url': 'ws://127.0.0.1:18084/v1/stream',
        'local_mode': True, 'auth_scheme': 'bearer', 'api_key': 'fixture-only-market-secret'})
    assert response.status_code == 200, response.text
    assert feed.tested is None and feed.started == 0
    assert response.json()['has_api_key']
    for path in ('/api/market/config', '/api/market/snapshot', '/api/dashboard'):
        assert 'fixture-only-market-secret' not in client.get(path).text
    assert client.post('/api/market/config/test', json={'expected_revision': 1}).status_code == 200
    assert feed.tested == 1
    assert client.post('/api/market/config/test', json={'expected_revision': True}).status_code == 422


@pytest.mark.parametrize('query', ['symbol=UNKNOWN', 'symbol=BTCUSDT&interval=2m', 'symbol=BTCUSDT&limit=0', 'symbol=BTCUSDT&limit=201'])
def test_history_input_rejected_before_feed(market_api, query):
    client, feed = market_api
    login(client)
    assert client.get('/api/market/candles?' + query).status_code == 422
    assert feed.history is None


def test_history_selection_forwarded(market_api):
    client, feed = market_api
    login(client)
    response = client.get('/api/market/candles?symbol=TSLABUSDT&interval=5m&limit=80')
    assert response.status_code == 200 and feed.history == ('TSLABUSDT', '5m', 80)


def test_global_no_market_is_truthful_and_history_cannot_start_network(tmp_path):
    settings = Settings(database_url=f'sqlite:///{tmp_path / "disabled.sqlite3"}', testing=True,
                        market_enabled=False, allowed_origins=('http://testserver',),
                        model_key_path=tmp_path / 'secrets.key', web_dist=tmp_path / 'dist')
    with TestClient(create_app(settings)) as client:
        client.headers['Origin'] = 'http://testserver'
        login(client)
        snapshot = client.get('/api/market/snapshot').json()
        assert snapshot['transport']['status'] == 'DISABLED' and snapshot['quotes'] == []
        assert client.get('/api/market/candles?symbol=BTCUSDT').status_code == 409


def test_sse_coalesces_static_snapshots_and_expires_revoked_session():
    async def scenario():
        elapsed = 0.0
        rechecked = 0
        state = {'sequence': 1, 'quotes': []}

        class Request:
            async def is_disconnected(self):
                return elapsed > 21

        async def sleep(seconds):
            nonlocal elapsed
            assert seconds == .5
            elapsed += seconds
            if elapsed == 3:
                state['sequence'] = 2

        async def validate():
            nonlocal rechecked
            rechecked += 1
            if rechecked == 2:
                raise ApiError('AUTH_REQUIRED', 'Login required', 401)

        events = [event async for event in market_events(Request(), lambda: deepcopy(state), validate,
                                                        clock=lambda: elapsed, sleep=sleep)]
        assert sum(event.startswith('event: market') for event in events) == 2
        assert ': heartbeat\n\n' in events
        assert events[-1].startswith('event: auth-expired') and rechecked == 2

    asyncio.run(scenario())
