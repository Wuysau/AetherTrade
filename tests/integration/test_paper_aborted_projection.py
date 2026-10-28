"""Authoritatively unsent commands remain distinct from uncertain orders."""
import psycopg
import pytest

from integration.test_unified_paper_runtime import runtime, runtime_config, market, data
from trading_system.execution.commands import CommandRepository
from trading_system.server.paper_runtime import scope_for

pytestmark = pytest.mark.paper_runtime


def test_proven_unsent_abort_never_looks_like_unknown_working_quantity(runtime, runtime_config):
    root = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'original', market(), 90, data())
    runtime.dispatch(root['command_id'])
    runtime.inject(root['command_id'], dict(event_id='original-fill', status='FILLED', cumulative_quantity='1', price='100'))
    stop = runtime.protect(root['command_id'], 'unsent-stop')['command_id']
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        CommandRepository(tx).abort_unsent(stop, 'EXPLICIT_UNSENT_RETIREMENT')
    assert runtime.dispatch(stop)['status'] == 'ABORTED_UNSENT'
    command = next(row for row in runtime.projection()['commands'] if row['command_id'] == stop)
    assert command['order_status'] == 'ABORTED_UNSENT'
    assert command['filled_quantity'] == command['leaves_quantity'] == '0'
    with psycopg.connect(runtime_config['venue_dsn']) as connection:
        assert connection.execute('SELECT count(*) FROM paper_engine_commands WHERE command_id=%s', (stop,)).fetchone()[0] == 0
