"""Market configuration tests use a disposable SQLite DB and generated vault only."""
import json

import pytest
from sqlalchemy import select

from trading_system.server.errors import ApiError
from trading_system.server.market_config import DEFAULT, MarketConfigService, configs
from trading_system.server.model_repository import profiles
from trading_system.server.store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'market.sqlite'}")
    store.initialize()
    service = MarketConfigService(store, tmp_path / 'fixture.key')
    service.migrate()
    yield service
    store.close()


def custom(**changes):
    return dict(expected_revision=0, provider='standard_json', name='Fixture market',
                rest_url='http://127.0.0.1:19876/market', ws_url='ws://localhost:19876/feed',
                auth_scheme='bearer', local_mode=True, **changes)


def test_defaults_are_public_offline_and_migration_preserves_data(service):
    first = service.get_config()
    assert first == {**{k: v for k, v in DEFAULT.items() if k != 'encrypted_key'},
                     'has_api_key': False, 'key_available': False}
    assert not service.vault.path.exists()
    first['name'] = 'detached'
    assert service.get_config()['name'] == DEFAULT['name']
    saved = service.save_config(custom(api_key='fixture-market-secret'))
    with service.store.transaction() as connection:
        cipher = connection.execute(select(configs.c.data)).scalar_one()['encrypted_key']
        model_before = list(connection.execute(select(profiles.c.data)).scalars())
    service.migrate()
    assert service.get_config() == saved
    with service.store.transaction() as connection:
        assert connection.execute(select(configs.c.data)).scalar_one()['encrypted_key'] == cipher
        assert list(connection.execute(select(profiles.c.data)).scalars()) == model_before
    assert cipher != 'fixture-market-secret'
    assert service.vault.decrypt(cipher) == 'fixture-market-secret'
    assert 'fixture-market-secret' not in json.dumps(saved)
    assert 'encrypted_key' not in saved and 'api_key' not in saved


def test_runtime_is_detached_and_only_runtime_exposes_plaintext(service):
    service.save_config(custom(api_key='fixture-key'))
    runtime = service.runtime_config()
    assert runtime['api_key'] == 'fixture-key'
    assert 'encrypted_key' not in runtime
    runtime.update(name='mutated', api_key='mutated')
    assert service.runtime_config()['api_key'] == 'fixture-key'
    assert service.get_config()['name'] == 'Fixture market'


def test_cas_is_strict_and_shared_between_service_instances(service):
    other = MarketConfigService(service.store, service.vault.path)
    service.save_config(custom())
    with pytest.raises(ApiError) as error:
        other.save_config({'expected_revision': 0, 'enabled': False})
    assert error.value.code == 'MARKET_REVISION_CONFLICT' and error.value.status == 409
    assert service.get_config()['enabled'] is True
    assert other.save_config({'expected_revision': 1, 'enabled': False})['revision'] == 2


@pytest.mark.parametrize('edit', [
    {'rest_url': 'http://127.0.0.1:19877/market'},
    {'ws_url': 'ws://localhost:19877/feed'}, {'ws_url': ''},
    {'auth_scheme': 'x_api_key'}, {'provider': 'binance_compatible'},
])
def test_destination_changes_require_key_reentry_or_explicit_clear(service, edit):
    service.save_config(custom(api_key='fixture-key'))
    with pytest.raises(ApiError) as error:
        service.save_config(dict(expected_revision=1, **edit))
    assert error.value.code == 'MARKET_KEY_REENTRY_REQUIRED'
    assert service.get_config()['revision'] == 1
    saved = service.save_config(dict(expected_revision=1, clear_api_key=True, **edit))
    assert not saved['has_api_key']


def test_key_reentry_moves_only_with_explicit_authorization_and_preset_clears(service):
    service.save_config(custom(api_key='fixture-key'))
    moved = service.save_config({'expected_revision': 1, 'rest_url': 'http://127.0.0.1:19877',
                                 'api_key': 'fixture-key-new'})
    assert moved['has_api_key'] and service.runtime_config()['api_key'] == 'fixture-key-new'
    preset = service.save_config({'expected_revision': 2, 'provider': 'binance'})
    assert preset['rest_url'] == DEFAULT['rest_url'] and preset['ws_url'] == DEFAULT['ws_url']
    assert preset['auth_scheme'] == 'none' and preset['local_mode'] is False
    assert preset['name'] == DEFAULT['name'] and not preset['has_api_key']


def test_lost_vault_is_publicly_unavailable_and_sanitized_at_runtime(service):
    service.save_config(custom(api_key='fixture-key'))
    service.vault.path.unlink()
    public = service.get_config()
    assert public['has_api_key'] and not public['key_available']
    with pytest.raises(ApiError) as error:
        service.runtime_config()
    assert error.value.code == 'MARKET_KEY_UNAVAILABLE'
    assert 'fixture-key' not in error.value.message
    assert str(service.vault.path) not in error.value.message
    service.save_config({'expected_revision': 1, 'api_key': 'fixture-replacement'})
    assert service.get_config()['key_available']


@pytest.mark.parametrize('payload', [
    None, [], {}, {'expected_revision': True}, {'expected_revision': '0'},
    {'expected_revision': 0, 'revision': 0}, {'expected_revision': 0, 'has_api_key': True},
    {'expected_revision': 0, 'encrypted_key': 'bad'},
    {'expected_revision': 0, 'enabled': 1}, {'expected_revision': 0, 'local_mode': 'false'},
    {'expected_revision': 0, 'poll_seconds': 1}, {'expected_revision': 0, 'poll_seconds': 61},
    {'expected_revision': 0, 'poll_seconds': 5.0}, {'expected_revision': 0, 'timeout_seconds': True},
    {'expected_revision': 0, 'timeout_seconds': 21}, {'expected_revision': 0, 'timeout_seconds': 1},
    {'expected_revision': 0, 'provider': []}, {'expected_revision': 0, 'auth_scheme': None},
    {'expected_revision': 0, 'name': ''}, {'expected_revision': 0, 'name': 'x' * 81},
    {'expected_revision': 0, 'name': 'bad\nname'}, {'expected_revision': 0, 'ws_url': None},
    {'expected_revision': 0, 'api_key': ''}, {'expected_revision': 0, 'api_key': None},
    {'expected_revision': 0, 'api_key': 'bad\r\nkey'}, {'expected_revision': 0, 'clear_api_key': 1},
    {'expected_revision': 0, 'api_key': 'fixture-key', 'clear_api_key': False},
])
def test_invalid_or_coerced_payloads_do_not_write(service, payload):
    with pytest.raises(ApiError):
        service.save_config(payload)
    assert service.get_config()['revision'] == 0
    assert not service.vault.path.exists()


@pytest.mark.parametrize('field,value', [
    ('rest_url', 'http://example.com'), ('rest_url', 'https://10.0.0.1'),
    ('rest_url', 'https://169.254.169.254'), ('rest_url', 'https://user:pass@example.com'),
    ('rest_url', 'https://224.0.0.1'), ('rest_url', 'https://[ff02::1]'),
    ('rest_url', 'https://example.com?'), ('rest_url', 'https://example.com/#secret'),
    ('rest_url', 'https://localhost'), ('rest_url', 'https://service.internal'),
    ('rest_url', 'https://example.com:0'), ('ws_url', 'ws://example.com'),
    ('ws_url', 'wss://127.0.0.1'), ('ws_url', 'wss://example.com?token=secret'),
])
def test_unsafe_remote_urls_are_rejected_without_network(service, field, value):
    with pytest.raises(ApiError) as error:
        service.save_config({'expected_revision': 0, field: value})
    assert error.value.code == 'MARKET_INVALID_URL'


def test_local_mode_only_accepts_explicit_loopback_and_empty_ws_is_supported(service):
    with pytest.raises(ApiError):
        service.save_config(dict(custom(), rest_url='http://source.example.com'))
    saved = service.save_config(dict(custom(), ws_url=''))
    assert saved['ws_url'] == ''
