"""Current whole-account send risk, against new isolated PostgreSQL databases."""
from decimal import Decimal

import pytest
import psycopg
from sqlalchemy import select

from support.unified_console import provision_console, default_market
from trading_system.server.errors import ApiError
from trading_system.server.store import cause_locks

pytestmark = pytest.mark.paper_runtime


def prepare(tmp_path):
    harness = provision_console(tmp_path, initial_cash='10000')
    harness.execute(dict(symbol='BTCUSDT', side='BUY', quantity='0.04'), 'btc-fill', harness.quotes)
    assert harness.runtime.account_view()['cash'] == '7998'
    entry = harness.store.order(dict(symbol='ETHUSDT', side='BUY', quantity='1.2'),
        'eth-accepted', harness.quotes, 90)
    return harness, entry['order']['command_id']


def test_dispatch_other_symbol_loss_latches_before_denial_and_survives_rebound(tmp_path):
    harness, command = prepare(tmp_path)
    try:
        harness.quotes = default_market()
        harness.quotes['quotes'][0]['price'] = '1'
        assert Decimal('7998') + Decimal('.04') == Decimal('7998.04')
        with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
            harness.runtime.dispatch(command)
        with harness.store.engine.connect() as conn:
            lock = conn.execute(select(cause_locks.c.data)).scalar_one()
        assert lock['cause'] == 'DAILY_LOSS'
        assert lock['observed_equity'] == '7998.04'
        assert any(p['cause'] == 'DAILY_LOSS' for p in harness.runtime.projection()['risk_locks'])
        harness.quotes = default_market()
        harness.store = harness.reopen()
        with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
            harness.runtime.dispatch(command)
        order = next(p for p in harness.runtime.projection()['orders'] if p['command_id'] == command)
        assert order['status'] == 'PENDING'
    finally:
        harness.close()


def test_dispatch_rechecks_position_cap_even_without_daily_breach(tmp_path):
    harness, command = prepare(tmp_path)
    try:
        # Loss is below 2000; ETH 2400 exceeds 25% of current equity after fee.
        harness.quotes = default_market()
        harness.quotes['quotes'][0]['price'] = '25000'
        with pytest.raises(ApiError, match='POSITION_LIMIT'):
            harness.runtime.dispatch(command)
        with harness.store.engine.connect() as conn:
            assert conn.execute(select(cause_locks.c.id)).first() is None
    finally:
        harness.close()


def test_daily_lock_keeps_bounded_exit_available_with_unrelated_stale_quote(tmp_path):
    harness, command = prepare(tmp_path)
    try:
        harness.quotes = default_market()
        harness.quotes['quotes'][0]['price'] = '1'
        with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
            harness.runtime.dispatch(command)
        harness.quotes['quotes'][1]['stale'] = True
        with pytest.raises(ApiError, match='INSUFFICIENT_POSITION'):
            harness.store.order(dict(symbol='BTCUSDT', side='SELL', quantity='0.05'),
                'oversell-remains-denied', harness.quotes, 90)
        reduction = harness.store.order(dict(symbol='BTCUSDT', side='SELL', quantity='0.005'),
            'bounded-loss-exit', harness.quotes, 90)
        assert harness.runtime.dispatch(reduction['order']['command_id'])['status'] == 'VENUE_CONFIRMED'
        assert any(p['cause'] == 'DAILY_LOSS' for p in harness.runtime.projection()['risk_locks'])
    finally:
        harness.close()


@pytest.mark.parametrize('boundary', [2, 3, 4, 5, 6])
def test_loss_after_durable_or_during_venue_write_persists_outside_rollback(tmp_path, boundary):
    harness, command = prepare(tmp_path)
    try:
        reader = harness.runtime.current_account_risk
        calls = []

        def change_at_boundary(request, pending):
            calls.append(pending.command_id)
            if len(calls) == boundary:
                harness.quotes = default_market()
                harness.quotes['quotes'][0]['price'] = '1'
            return reader(request, pending)

        harness.runtime.current_account_risk = change_at_boundary
        # All late denials retain the durable ambiguity/reservation rather than
        # asserting NOT_SENT from a local exception.
        assert harness.runtime.dispatch(command)['status'] == 'UNKNOWN'
        assert len(calls) == boundary
        with psycopg.connect(harness.runtime_config['venue_dsn']) as conn:
            assert conn.execute('SELECT count(*) FROM paper_engine_commands WHERE command_id=%s',
                (command,)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM paper_engine_orders WHERE quantity=1.2").fetchone()[0] == 0
        with harness.store.engine.connect() as conn:
            assert conn.execute(select(cause_locks.c.data)).scalar_one()['observed_equity'] == '7998.04'
        projection = harness.runtime.projection()
        assert any(p['cause'] == 'DAILY_LOSS' for p in projection['risk_locks'])
        harness.quotes = default_market()
        harness.store = harness.reopen()
        assert harness.runtime.dispatch(command)['status'] == 'UNKNOWN'
    finally:
        harness.close()

