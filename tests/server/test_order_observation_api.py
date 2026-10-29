from fastapi.testclient import TestClient
import pytest
from trading_system.server.app import create_app
from trading_system.server.config import Settings


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path/'observation.sqlite'}", market_enabled=False,
        automation_enabled=False, testing=True, model_key_path=tmp_path/'key', allowed_origins=('http://testserver',)))
    with TestClient(app) as client:
        client.headers['Origin'] = 'http://testserver'
        yield client


def login(client):
    return client.post('/api/setup', json=dict(username='owner', password='a-long-password', initial_cash='100000'))


def test_observation_requires_existing_authentication(client):
    assert client.get('/api/order-programs').status_code == 401
    assert client.get('/api/order-capabilities').status_code == 401


def test_unconfigured_ledger_is_explicit_and_all46_targets_remain(client):
    login(client)
    response = client.get('/api/order-programs')
    assert response.status_code == 200
    assert response.json()['observation_status'] == 'UNCONFIGURED'
    assert response.json()['items'] == []
    assert client.get('/api/order-programs?limit=100000').status_code == 422
    catalog = client.get('/api/order-capabilities').json()
    assert len(catalog['features']) == 46
    assert all(f['target_required'] and f['venue_qualification']=='NOT_RUN' for f in catalog['features'])
    assert catalog['current_qualification'] == 'UNKNOWN'
    assert catalog['execution_authorized'] is False
    assert client.post('/api/order-programs').status_code == 405


def test_injected_trusted_reader_receives_only_scope_key_and_pagination(client):
    login(client)
    class Reader:
        def list(self, **params):
            assert params == dict(scope='server-bound-scope', limit=20, cursor='cursor')
            return {'items':[], 'next_cursor':None, 'scopes':[], 'read_only':True}
        def detail(self, identifier, **params):
            assert identifier == 'original-program' and params == {'scope':'server-bound-scope'}
            return {'execution_authorized':False}
    client.app.state.order_observer = Reader()
    assert client.get('/api/order-programs?scope=server-bound-scope&limit=20&cursor=cursor').status_code == 200
    assert client.get('/api/order-programs/original-program?scope=server-bound-scope').json()['execution_authorized'] is False

