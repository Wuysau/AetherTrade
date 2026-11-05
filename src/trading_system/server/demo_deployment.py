"""Explicit opt-in deployment composition. No database or venue I/O at load.

The operator owns the verified account/epoch mapping and the build identity.
This module does not establish qualification or grant strategy authorization.
"""
import json
import re

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope
from trading_system.runtime.demo_service import DemoRuntimeService
from trading_system.storage.demo import DemoRepository


class _UnqualifiedDemoRuntime(DemoRuntimeService):
    """Deployment is fail-closed until fencing and build attestation exist."""
    def bind_session(self, session_key):
        raise DemoError('DEMO_CREDENTIAL_FENCING_UNQUALIFIED')

    def start_window(self, session_key, *, duration_seconds, decision_interval_seconds, expected_strategy_revision, expected_grant_hash, loop=None):
        raise DemoError('DEMO_BUILD_ATTESTATION_REQUIRED')

    async def run_window(self, session_key, *, duration_seconds, execute=False, **kwargs):
        if execute:
            raise DemoError('DEMO_BUILD_ATTESTATION_REQUIRED')
        return await super().run_window(session_key, duration_seconds=duration_seconds, execute=False, **kwargs)

    def status(self, session_key):
        return {**super().status(session_key), 'build_attestation': 'UNVERIFIED_OPERATOR_DECLARATION',
                'deployment_execution_enabled': False, 'binding_blocker': 'DEMO_CREDENTIAL_FENCING_UNQUALIFIED'}


def load_demo_factory(path, settings):
    value = json.loads(path.read_text(encoding='utf-8'))
    required = {'database_url', 'account_id', 'account_epoch', 'strategy_id', 'build_sha'}
    if type(value) is not dict or set(value) != required or any(type(value[k]) is not str for k in required):
        raise ValueError('Invalid Demo deployment configuration')
    try:
        url = make_url(value['database_url'])
        others = [make_url(dsn) for dsn in (settings.database_url, settings.paper_ledger_dsn,
                  settings.paper_venue_dsn, settings.paper_event_dsn) if dsn]
        if (url.drivername != 'postgresql+psycopg' or url.host not in {'127.0.0.1', '::1'} or not url.database or url.query
                or any(other.drivername.startswith('postgresql') and other.database == url.database for other in others)):
            raise ValueError('Demo requires a separate PostgreSQL database')
        if not re.fullmatch(r'[1-9][0-9]{0,19}', value['account_id']) or not re.fullmatch(r'[0-9a-f]{40}', value['build_sha']):
            raise ValueError('Invalid trusted identity or build SHA')
        scope = DemoScope(account_id=value['account_id'], account_epoch=value['account_epoch'], strategy_id=value['strategy_id'])
    except (DemoError, TypeError):
        raise ValueError('Invalid Demo deployment configuration') from None

    def resolve(account):
        if type(account) is not dict or type(account.get('uid')) is not int or str(account['uid']) != scope.account_id:
            raise DemoError('DEMO_VERIFIED_ACCOUNT_IDENTITY_REQUIRED')
        return scope

    def factory(*, connections, model_service):
        # SQLAlchemy engine construction is lazy: it does not connect or migrate.
        # Pin the numeric destination, overriding libpq PGHOSTADDR environment.
        engine = create_engine(url, connect_args={'hostaddr': url.host})
        return _UnqualifiedDemoRuntime(connections=connections, model_service=model_service,
            repository=DemoRepository(engine), scope_resolver=resolve,
            current_code_sha=value['build_sha'], close_repository=engine.dispose)
    return factory
