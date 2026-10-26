"""Additive model persistence registered before Store.initialize()."""
from sqlalchemy import Column, Integer, JSON, String, Table, insert, select, update
from .store import metadata

configs = Table('console_model_config', metadata, Column('id', Integer, primary_key=True), Column('data', JSON, nullable=False))
runs = Table('console_model_runs', metadata, Column('key', String(128), primary_key=True), Column('data', JSON, nullable=False))

DEFAULT = dict(protocol='openai_responses', base_url='https://api.openai.com/v1', model='', enabled=False,
               local_mode=False, max_output_tokens=1024, timeout_seconds=30, anthropic_version='2023-06-01',
               revision=0, encrypted_key=None, connection_status='NOT_CONFIGURED', last_test_at=None, last_error=None)


def read_config(connection):
    return connection.execute(select(configs.c.data).where(configs.c.id == 1)).scalar_one_or_none() or dict(DEFAULT)


def write_config(connection, data):
    if connection.execute(select(configs.c.id).where(configs.c.id == 1)).first():
        connection.execute(update(configs).where(configs.c.id == 1).values(data=data))
    else:
        connection.execute(insert(configs).values(id=1, data=data))


def read_run(connection, key):
    return connection.execute(select(runs.c.data).where(runs.c.key == key)).scalar_one_or_none()


def write_run(connection, key, data, *, new=False):
    statement = insert(runs).values(key=key, data=data) if new else update(runs).where(runs.c.key == key).values(data=data)
    connection.execute(statement)
