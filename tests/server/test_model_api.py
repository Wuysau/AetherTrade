"""Real authenticated API/storage with controlled provider responses; no paid calls."""
import asyncio
from datetime import datetime, timezone
import json

import httpx
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from trading_system.server.app import create_app
from trading_system.server.config import Settings
from trading_system.server.instruments import ALL_SYMBOLS
from trading_system.server.model_repository import configs


class Quotes:
    stale = False

    def snapshot(self):
        return {'source': 'controlled-public', 'status': 'STALE' if self.stale else 'CONNECTED',
                'last_updated': datetime.now(timezone.utc).isoformat(), 'error': None,
                'quotes': [{'symbol': symbol, 'price': '200', 'change_pct': '1',
                            'as_of': datetime.now(timezone.utc).isoformat(), 'source': 'controlled-public',
                            'stale': self.stale} for symbol in ALL_SYMBOLS],
                'groups': [{'id': 'bstocks', 'label': 'bStocks', 'status': 'CONNECTED', 'last_updated': None, 'error': None}]}

    async def refresh(self):
        return self.snapshot()


@pytest.fixture
def api(tmp_path, request):
    requests = []
    def provider(request):
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'controlled-model'}]})
        body = json.loads(request.content)
        context = json.loads(body['messages'][-1]['content'])
        assert 'provider-fixture-secret' not in request.content.decode()
        assert set(context) <= {'symbol', 'quote', 'position', 'account', 'risk'}
        assert 'id' not in context['account'] and 'username' not in context['account']
        advice = {'symbol': context['symbol'], 'action': 'HOLD', 'summary': 'Controlled analysis',
                  'counter_evidence': ['Limited history'], 'invalidation': 'A new quote changes the context'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(advice)}}],
                                        'usage': {'prompt_tokens': 10, 'completion_tokens': 20}})
    model_client = httpx.AsyncClient(transport=httpx.MockTransport(provider), trust_env=False)
    runtime_options = {'database_url': f'sqlite:///{tmp_path / "app.sqlite3"}'}
    if request.node.get_closest_marker('paper_runtime'):
        from support.unified_console import provision_console
        runtime_options = provision_console(tmp_path, setup=False).settings_kwargs
    settings = Settings(**runtime_options, testing=True,
                        market_enabled=False, allowed_origins=('http://testserver',),
                        model_key_path=tmp_path / 'models.key', web_dist=tmp_path / 'web')
    feed = Quotes()
    app = create_app(settings, quote_provider=feed, model_client=model_client)
    with TestClient(app) as client:
        client.headers['Origin'] = 'http://testserver'
        yield client, app, feed, requests
    asyncio.run(model_client.aclose())


def login(client):
    response = client.post('/api/setup', json={'username': 'owner', 'password': 'local-fixture-password', 'initial_cash': '100000'})
    assert response.status_code == 200
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']


def configure(client):
    return client.put('/api/model-config', json={
        'protocol': 'openai_chat', 'base_url': 'http://127.0.0.1:18083/v1', 'model': 'controlled-model',
        'enabled': True, 'local_mode': True, 'expected_revision': 0, 'api_key': 'provider-fixture-secret'})


def test_config_auth_csrf_and_secret_storage(api):
    client, app, _, requests = api
    assert client.get('/api/model-config').status_code == 401
    login(client)
    csrf = client.headers.pop('X-CSRF-Token')
    assert configure(client).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    saved = configure(client)
    assert saved.status_code == 200 and saved.json()['has_api_key']
    assert not requests  # saving cannot call the provider
    assert 'provider-fixture-secret' not in saved.text
    assert 'provider-fixture-secret' not in client.get('/api/dashboard').text
    with app.state.store.engine.connect() as connection:
        row = connection.execute(select(configs.c.data)).scalar_one()
        assert row['encrypted_key'] and 'provider-fixture-secret' not in json.dumps(row)


def test_directory_check_is_separate_from_inference(api):
    client, _, _, requests = api
    login(client)
    assert configure(client).status_code == 200
    response = client.post('/api/model-config/test', json={'expected_revision': 1})
    assert response.status_code == 200 and response.json()['config']['connection_status'] == 'CONNECTED'
    assert [request.method for request in requests] == ['GET']
    assert client.get('/api/dashboard').json()['decisions'] == []


@pytest.mark.parametrize('acknowledgement', [False, 1, 'true'])
def test_inference_requires_explicit_boolean_acknowledgement(api, acknowledgement):
    client, _, _, requests = api
    login(client)
    configure(client)
    response = client.post('/api/decisions/generate', headers={'Idempotency-Key': 'run'}, json={
        'symbol': 'TSLABUSDT', 'expected_revision': 1, 'acknowledge_cost': acknowledgement})
    assert response.status_code == 422 and not requests


def test_bstock_advice_persists_without_orders_and_replays_when_quotes_stale(api):
    client, _, feed, requests = api
    login(client)
    configure(client)
    body = {'symbol': 'TSLABUSDT', 'expected_revision': 1, 'acknowledge_cost': True}
    first = client.post('/api/decisions/generate', headers={'Idempotency-Key': 'analysis'}, json=body)
    assert first.status_code == 200, first.text
    assert first.json()['decision']['risk_status'] == 'NOT_EVALUATED'
    dashboard = client.get('/api/dashboard').json()
    assert len(dashboard['decisions']) == 1 and not dashboard['orders'] and not dashboard['positions']
    assert {x['symbol'] for x in dashboard['instruments'] if x['asset_class'] == 'BSTOCK'} == {'TSLABUSDT', 'NVDABUSDT', 'AAPLBUSDT'}
    assert len(client.get('/api/instruments').json()['instruments']) == 6
    feed.stale = True
    repeat = client.post('/api/decisions/generate', headers={'Idempotency-Key': 'analysis'}, json=body)
    assert repeat.status_code == 200 and repeat.json() == first.json()
    assert len(requests) == 1
    new = client.post('/api/decisions/generate', headers={'Idempotency-Key': 'new-analysis'}, json=body)
    assert new.status_code == 409 and len(requests) == 1


@pytest.mark.paper_runtime
def test_partial_market_allows_fresh_crypto_but_rejects_stale_bstock_order(api):
    client, app, _, _ = api
    login(client)
    market = app.state.market.snapshot()
    market['status'] = 'PARTIAL'
    for quote in market['quotes']:
        quote['stale'] = quote['symbol'] in {'TSLABUSDT', 'NVDABUSDT', 'AAPLBUSDT'}
    original = app.state.market.snapshot
    app.state.market.snapshot = lambda: market
    try:
        crypto = client.post('/api/paper/orders', headers={'Idempotency-Key': 'crypto'}, json={
            'symbol': 'BTCUSDT', 'side': 'BUY', 'quantity': '1', 'strategy_id': None})
        stock = client.post('/api/paper/orders', headers={'Idempotency-Key': 'stock'}, json={
            'symbol': 'TSLABUSDT', 'side': 'BUY', 'quantity': '1', 'strategy_id': None})
        assert crypto.status_code == 200 and stock.status_code == 409
    finally:
        app.state.market.snapshot = original
