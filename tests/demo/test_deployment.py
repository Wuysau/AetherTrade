"""Explicit deployment wiring does not contact a venue or database."""
import json
import asyncio
import pytest
from sqlalchemy import create_engine

from trading_system.server.config import Settings
from trading_system.engine.binance_demo.contracts import DemoError


def config():
    return {'database_url': 'postgresql+psycopg://demo:secret@127.0.0.1/demo_isolated',
            'account_id': '123', 'account_epoch': 'verified-epoch', 'strategy_id': 's1',
            'build_sha': 'a' * 40}


def test_deployment_is_lazy_and_requires_signed_fixed_uid(tmp_path):
    from trading_system.server.demo_deployment import load_demo_factory
    path = tmp_path / 'config.json'; path.write_text(json.dumps(config()))
    factory = load_demo_factory(path, Settings(testing=True))
    runtime = factory(connections=None, model_service=None)
    assert runtime.repository.engine.dialect.name == 'postgresql'
    assert runtime.status('missing')['build_attestation'] == 'UNVERIFIED_OPERATOR_DECLARATION'
    assert runtime.status('missing')['deployment_execution_enabled'] is False
    assert runtime.status('missing')['binding_blocker'] == 'DEMO_CREDENTIAL_FENCING_UNQUALIFIED'
    with pytest.raises(DemoError, match='DEMO_CREDENTIAL_FENCING_UNQUALIFIED'):
        runtime.bind_session('missing')
    with pytest.raises(DemoError, match='DEMO_BUILD_ATTESTATION_REQUIRED'):
        asyncio.run(runtime.run_window('missing', duration_seconds=1, execute=True))
    assert runtime.scope_resolver({'uid': 123}).account_epoch == 'verified-epoch'
    for uid in (124, '123', True, None):
        with pytest.raises(DemoError, match='DEMO_VERIFIED_ACCOUNT_IDENTITY_REQUIRED'):
            runtime.scope_resolver({'uid': uid})
    runtime.close()


@pytest.mark.parametrize('changes', [
    {'database_url': 'sqlite:///demo.sqlite'},
    {'database_url': 'postgresql+psycopg://demo:secret@remote.example/demo_isolated'},
    {'database_url': 'postgresql+psycopg://demo:secret@localhost/demo_isolated'},
    {'database_url': 'postgresql+psycopg://demo:secret@127.0.0.1/console'},
    {'database_url': 'postgresql+psycopg://demo:secret@localhost/demo_isolated?options=-csearch_path%3Dpublic'},
    {'account_id': '0123'}, {'account_id': 123}, {'account_epoch': ''},
    {'build_sha': 'z' * 40}, {'api_key': 'must-not-be-in-config'},
])
def test_deployment_rejects_unsafe_configuration(tmp_path, changes):
    from trading_system.server.demo_deployment import load_demo_factory
    value = config(); value.update(changes)
    path = tmp_path / 'config.json'; path.write_text(json.dumps(value))
    settings = Settings(database_url='postgresql+psycopg://user:password@otherhost/console', testing=True)
    with pytest.raises(ValueError): load_demo_factory(path, settings)


def test_cli_enables_factory_only_when_explicit_config_supplied(tmp_path, monkeypatch):
    from trading_system.server import cli, app as app_module
    import uvicorn
    path = tmp_path / 'config.json'; path.write_text(json.dumps(config()))
    captured = []
    monkeypatch.setattr(cli.Settings, 'from_env', classmethod(lambda cls: Settings(testing=True, market_enabled=False)))
    monkeypatch.setattr(app_module, 'create_app', lambda settings, **kw: captured.append(kw['demo_runtime_factory']) or object())
    servers = []
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kw: servers.append(kw))
    assert cli.main(['serve', '--no-market']) == 0
    assert servers[-1]['workers'] == 1
    assert captured[-1] is None
    assert cli.main(['serve', '--no-market', '--demo-config', str(path)]) == 0
    assert callable(captured[-1])


def test_cli_composition_bind_is_blocked_before_credentials_or_database(tmp_path):
    from fastapi.testclient import TestClient
    from trading_system.server.app import create_app
    from trading_system.server.demo_deployment import load_demo_factory
    settings = Settings(database_url=f'sqlite:///{tmp_path / "app.sqlite"}', testing=True, market_enabled=False, model_key_path=tmp_path/'key')
    path = tmp_path / 'config.json'; path.write_text(json.dumps(config()))
    app = create_app(settings, demo_runtime_factory=load_demo_factory(path, settings))
    with TestClient(app, headers={'origin': 'http://localhost:8787'}) as client:
        setup = client.post('/api/setup', json={'username': 'owner', 'password': 'long-password-here', 'initial_cash': '10000'})
        client.headers['x-csrf-token'] = setup.json()['csrf_token']
        status = client.get('/api/demo/status').json()['runtime']
        assert status['binding_blocker'] == 'DEMO_CREDENTIAL_FENCING_UNQUALIFIED'
        assert status['build_attestation'] == 'UNVERIFIED_OPERATOR_DECLARATION'
        result = client.post('/api/demo/runtime/bind', json={'acknowledge_account': True})
        assert result.status_code == 422 and result.json()['error']['code'] == 'DEMO_CREDENTIAL_FENCING_UNQUALIFIED'
