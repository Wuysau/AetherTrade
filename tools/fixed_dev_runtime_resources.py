"""Pytest-only witness of database creation, with exact per-case reclamation.

No database is claimed from a catalog difference or name prefix alone. The
connection that successfully executed CREATE DATABASE records the actual OID.
Runtime roles are retained for audit rather than deleting unknown dependencies.
"""
import json
import os
from pathlib import Path
import re

import psycopg
from psycopg import sql
import pytest

_connect = psycopg.connect
_created = {}
_harnesses = []
_original_provision = None


def close_harnesses():
    while _harnesses:
        _harnesses.pop().close()


def record(event):
    with Path(os.environ['AETHER_FIXED_DEV_RESOURCE_JOURNAL']).open('a',encoding='utf-8') as f:
        f.write(json.dumps(event)+'\n')


class RecordingConnection(psycopg.Connection):
    def execute(self, query, params=None, **kwargs):
        text = query.as_string(self) if isinstance(query,sql.Composable) else query
        match = re.fullmatch(r'CREATE DATABASE "([a-z0-9_]+)"',text.strip(),re.I) if isinstance(text,str) else None
        if match:
            name=match[1]
            if not re.fullmatch(r'paper_(?:runtime|engine|control|console_guard)_[a-z0-9_]+',name) or name.endswith('_dev'):
                raise RuntimeError('unregistered database namespace')
            if super().execute('SELECT oid FROM pg_database WHERE datname=%s',(name,)).fetchone():
                raise RuntimeError('database already exists')
            record({'action':'CREATE_REQUEST','name':name})
        result=super().execute(query,params,**kwargs)
        if match:
            oid=super().execute('SELECT oid FROM pg_database WHERE datname=%s',(name,)).fetchone()[0]
            _created[name]=oid
            record({'action':'CREATED','name':name,'oid':oid})
        return result


def cleanup():
    with _connect(os.environ['PAPER_RUNTIME_ADMIN_DSN'],autocommit=True) as admin:
        for name,oid in list(_created.items()):
            current=admin.execute('SELECT oid FROM pg_database WHERE datname=%s',(name,)).fetchone()
            if current!=(oid,):
                raise RuntimeError('recorded database identity changed')
            if admin.execute('SELECT count(*) FROM pg_stat_activity WHERE datid=%s',(oid,)).fetchone()[0]:
                raise RuntimeError('recorded database still has active connections')
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            if admin.execute('SELECT oid FROM pg_database WHERE datname=%s',(name,)).fetchone():
                raise RuntimeError('database reclamation not verified')
            record({'action':'REMOVED','name':name,'oid':oid})
            del _created[name]


def pytest_configure(config):
    global _original_provision
    if os.environ.get('AETHER_FIXED_DEV_DATABASE_TESTS')!='YES':
        raise RuntimeError('fixed DEV opt-in missing')
    if not os.environ.get('AETHER_FIXED_DEV_RESOURCE_JOURNAL'):
        raise RuntimeError('resource journal required')
    psycopg.connect=RecordingConnection.connect
    from support import unified_console
    _original_provision=unified_console.provision_console
    def tracked_console(*args,**kwargs):
        harness=_original_provision(*args,**kwargs)
        _harnesses.append(harness)
        return harness
    unified_console.provision_console=tracked_console


@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_runtest_teardown(item,nextitem):
    yield
    try:
        close_harnesses()
        cleanup()
    except Exception as exc:
        record({'action':'CLEANUP_BLOCKED','error_type':type(exc).__name__})
        pytest.exit('Exact runtime database cleanup blocked; retained evidence and resources',returncode=78)


def pytest_unconfigure(config):
    psycopg.connect=_connect
    if _original_provision is not None:
        from support import unified_console
        unified_console.provision_console=_original_provision
