"""Additive model persistence registered before Store.initialize()."""
from sqlalchemy import Column, Integer, JSON, String, Table, delete, insert, select, update
from .store import metadata

configs = Table('console_model_config', metadata, Column('id', Integer, primary_key=True), Column('data', JSON, nullable=False))
profiles = Table('console_model_profiles', metadata, Column('id', String(64), primary_key=True), Column('data', JSON, nullable=False))
runs = Table('console_model_runs', metadata, Column('key', String(128), primary_key=True), Column('data', JSON, nullable=False))
DEFAULT_PROFILE_ID = 'default'

DEFAULT = dict(protocol='openai_responses', base_url='https://api.openai.com/v1', model='', enabled=False,
               local_mode=False, max_output_tokens=1024, timeout_seconds=30, anthropic_version='2023-06-01',
               revision=0, encrypted_key=None, connection_status='NOT_CONFIGURED', last_test_at=None, last_error=None)


def ensure_profiles(connection):
    """Migrate once, under the Store transaction lock; never re-encrypt a key."""
    if connection.execute(select(profiles.c.id).where(profiles.c.id == DEFAULT_PROFILE_ID)).first() is None:
        old = connection.execute(select(configs.c.data).where(configs.c.id == 1)).scalar_one_or_none()
        data = dict(DEFAULT, **(old or {}))
        data.update(id=DEFAULT_PROFILE_ID, name='默认模型')
        write_profile(connection, DEFAULT_PROFILE_ID, data)


def read_profile(connection, profile_id):
    ensure_profiles(connection)
    return connection.execute(select(profiles.c.data).where(profiles.c.id == profile_id)).scalar_one_or_none()


def read_profiles(connection):
    ensure_profiles(connection)
    return list(connection.execute(select(profiles.c.data).order_by(profiles.c.id)).scalars())


def write_profile(connection, profile_id, data):
    data = dict(data, id=profile_id)
    if connection.execute(select(profiles.c.id).where(profiles.c.id == profile_id)).first():
        connection.execute(update(profiles).where(profiles.c.id == profile_id).values(data=data))
    else:
        connection.execute(insert(profiles).values(id=profile_id, data=data))
    if profile_id == DEFAULT_PROFILE_ID:
        _write_legacy_config(connection, data)


def delete_profile(connection, profile_id):
    connection.execute(delete(profiles).where(profiles.c.id == profile_id))


def read_config(connection):
    return read_profile(connection, DEFAULT_PROFILE_ID)


def write_config(connection, data):
    write_profile(connection, DEFAULT_PROFILE_ID, dict(data, name=data.get('name', '默认模型')))


def _write_legacy_config(connection, data):
    if connection.execute(select(configs.c.id).where(configs.c.id == 1)).first():
        connection.execute(update(configs).where(configs.c.id == 1).values(data=data))
    else:
        connection.execute(insert(configs).values(id=1, data=data))


def read_run(connection, key):
    return connection.execute(select(runs.c.data).where(runs.c.key == key)).scalar_one_or_none()


def write_run(connection, key, data, *, new=False):
    statement = insert(runs).values(key=key, data=data) if new else update(runs).where(runs.c.key == key).values(data=data)
    connection.execute(statement)
