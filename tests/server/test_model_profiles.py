"""Profile persistence tests use only disposable SQLite and generated fixture keys."""
import asyncio
import json

import httpx
import pytest
from sqlalchemy import delete, insert, select, update

from trading_system.server import model_repository as repository
from trading_system.server.errors import ApiError
from trading_system.server.model_service import ModelService
from trading_system.server.store import Store, strategies


@pytest.fixture
def service(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'profiles.sqlite'}")
    store.initialize()
    svc = ModelService(store, tmp_path / 'fixture.key')
    yield svc
    asyncio.run(svc.close())
    store.close()


def profile_payload(name='Research', **changes):
    return dict(name=name, protocol='openai_chat', base_url='http://127.0.0.1:19876/v1',
                model='fixture-model', enabled=True, local_mode=True, **changes)


def test_default_migration_preserves_ciphertext_revision_and_does_not_overwrite(service):
    cipher = service.vault.encrypt('isolated-legacy-key')
    legacy = dict(repository.DEFAULT, encrypted_key=cipher, revision=7, enabled=True, model='old-model')
    with service.store.transaction() as connection:
        # Reproduce the persisted state before the additive profile migration.
        connection.execute(delete(repository.profiles))
        connection.execute(delete(repository.configs))
        connection.execute(insert(repository.configs).values(id=1, data=legacy))
    service.migrate()
    public = service.get_config()
    assert public['id'] == 'default' and public['name'] == '默认模型'
    assert public['revision'] == 7 and public['enabled'] is True and public['key_available']
    with service.store.transaction() as connection:
        assert repository.read_profile(connection, 'default')['encrypted_key'] == cipher
        connection.execute(update(repository.configs).values(data=dict(legacy, revision=2, enabled=False)))
    service.migrate()
    assert service.get_config()['revision'] == 7
    assert service.get_config()['enabled'] is True
    assert len(service.list_configs()['profiles']) == 1


def test_profile_keys_revisions_and_default_alias_are_independent(service):
    first = service.save_config(dict(profile_payload('Default renamed'), expected_revision=0, api_key='key-default'))
    second = service.create_config(profile_payload('Second'))
    assert second['revision'] == 1 and not second['has_api_key']
    second = service.save_config({'expected_revision': 1, 'api_key': 'key-second'}, second['id'])
    assert second['revision'] == 2 and second['key_available']
    assert service.get_config()['revision'] == first['revision']
    with service.store.transaction() as connection:
        a, b = repository.read_config(connection), repository.read_profile(connection, second['id'])
        assert service.vault.decrypt(a['encrypted_key']) == 'key-default'
        assert service.vault.decrypt(b['encrypted_key']) == 'key-second'
        assert connection.execute(select(repository.configs.c.data)).scalar_one() == a
    with pytest.raises(ApiError) as error:
        service.save_config({'expected_revision': 1, 'model': 'stale'}, second['id'])
    assert error.value.code == 'MODEL_REVISION_CONFLICT'
    with pytest.raises(ApiError) as error:
        service.save_config({'expected_revision': 2, 'base_url': 'http://127.0.0.1:19877/v1'}, second['id'])
    assert error.value.code == 'MODEL_KEY_REENTRY_REQUIRED'
    cleared = service.save_config({'expected_revision': 2, 'clear_api_key': True}, second['id'])
    assert not cleared['has_api_key'] and service.get_config()['has_api_key']
    assert 'key-default' not in json.dumps(service.list_configs())
    assert 'encrypted_key' not in json.dumps(service.list_configs())


@pytest.mark.parametrize('name', ['', '  ', 'x' * 81, 'bad\nname', None])
def test_invalid_profile_names_are_rejected(service, name):
    with pytest.raises(ApiError):
        service.create_config(profile_payload(name))


def test_profile_names_are_unique_and_profile_count_is_bounded(service):
    first = service.create_config(profile_payload('Research'))
    with pytest.raises(ApiError) as error:
        service.create_config(profile_payload(' research '))
    assert error.value.status == 409
    with pytest.raises(ApiError):
        service.save_config({'name': '默认模型', 'expected_revision': 1}, first['id'])
    for index in range(18):
        service.create_config(profile_payload(f'Model {index}'))
    with pytest.raises(ApiError) as error:
        service.create_config(profile_payload('Too many'))
    assert error.value.status == 409
    assert len(service.list_configs()['profiles']) == 20


def test_delete_requires_revision_and_unreferenced_nondefault_profile(service):
    saved = service.create_config(profile_payload())
    with pytest.raises(ApiError) as error:
        service.delete_config('default', 0)
    assert error.value.status == 409
    with pytest.raises(ApiError):
        service.delete_config(saved['id'], 0)
    with service.store.transaction() as connection:
        connection.execute(insert(strategies).values(id='draft', data={
            'id': 'draft', 'status': 'DRAFT', 'model_profile_ids': [saved['id']]}))
    with pytest.raises(ApiError) as error:
        service.delete_config(saved['id'], 1)
    assert error.value.status == 409
    with service.store.transaction() as connection:
        connection.execute(delete(strategies).where(strategies.c.id == 'draft'))
    assert service.delete_config(saved['id'], 1) == {'deleted': True, 'id': saved['id']}
    with pytest.raises(ApiError) as error:
        service.get_config(saved['id'])
    assert error.value.status == 404


def test_connection_proof_is_profile_scoped_and_rejects_selected_edit(service):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handle(request):
            assert request.headers['authorization'] == 'Bearer key-research'
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'data': [{'id': 'fixture-model'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.client = client
            saved = service.create_config(profile_payload(api_key='key-research'))
            pending = asyncio.create_task(service.test_connection(1, saved['id']))
            await entered.wait()
            service.save_config(dict(profile_payload('Default'), expected_revision=0, api_key='key-default'))
            release.set()
            result = await pending
            assert result['config']['connection_status'] == 'CONNECTED'
            assert service.get_config()['connection_status'] == 'CONFIGURED'
            entered.clear()
            release.clear()
            pending = asyncio.create_task(service.test_connection(1, saved['id']))
            await entered.wait()
            service.save_config({'expected_revision': 1, 'model': 'changed'}, saved['id'])
            release.set()
            with pytest.raises(ApiError) as error:
                await pending
            assert error.value.code == 'MODEL_REVISION_CONFLICT'
            assert service.get_config(saved['id'])['connection_status'] == 'CONFIGURED'
    asyncio.run(run())
