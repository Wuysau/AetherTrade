"""Preserved console guarantees through real, explicitly event-driven PAPER."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.test_store import market, buy
from support.unified_console import provision_console
from trading_system.server.errors import ApiError
from trading_system.server.store import orders
from sqlalchemy import insert, select


def test_postgresql_race_restart_and_concurrent_transaction_receipts(tmp_path):
    harness = provision_console(tmp_path, setup=False)
    primary, secondary = harness.store, harness.reopen()
    try:
        def setup_owner(store):
            try:
                return store.setup('owner', 'a-long-password', '100000', 24)
            except ApiError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            setup = list(pool.map(setup_owner, [primary, secondary]))
        assert setup.count('ALREADY_CONFIGURED') == 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda store: store.order(buy(), 'pg-duplicate', market(), 90),
                [primary, secondary]))
        assert receipts[0] == receipts[1]
        assert receipts[0]['order']['status'] == 'PENDING'
        assert primary.dashboard(market())['account']['cash'] == '100000'
        harness.fill(receipts[0], market())
        primary.close()
        restored = harness.reopen()
        harness.store = restored
        result = restored.dashboard(market(), '24H')
        assert result['account']['cash'] == '94995'
        assert len(result['orders']) == 1
        assert restored.order(buy(), 'pg-duplicate', market(stale=True), 90) == receipts[0]
        action = dict(action='exit_owned', expected_revision=result['account']['revision'], confirmation='EXIT PAPER')
        assert restored.action(action, 'pg-exit', market(), 90)['affected_count'] == 1
        assert len(restored.dashboard(market())['positions']) == 1
        assert len(harness.fill_pending(market())) == 1
        assert restored.dashboard(market())['positions'] == []
        assert restored.dashboard(market())['account']['cash'] == '99990'
    finally:
        harness.close()


def test_postgresql_daily_loss_denial_rebound_restart_and_reduction(tmp_path):
    harness = provision_console(tmp_path)
    try:
        harness.execute(buy(), 'pg-latch-entry', market())
        with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
            harness.store.order(buy('0.01'), 'pg-latch-loss', market('10000'), 90)
        reopened = harness.reopen()
        harness.store = reopened
        with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
            reopened.order(buy('0.01'), 'pg-latch-rebound', market(), 90)
        assert any(lock['cause'] == 'DAILY_LOSS' for lock in reopened.dashboard(market())['risk_locks'])
        assert len(reopened.dashboard(market())['orders']) == 1
        # Reduction still needs the same ownership, risk and command path.
        harness.execute(dict(buy(), side='SELL'), 'pg-latch-reduce', market())
        result = reopened.dashboard(market())
        assert result['positions'] == []
        assert result['account']['cash'] == '99990'
        assert any(lock['cause'] == 'DAILY_LOSS' for lock in result['risk_locks'])
    finally:
        harness.close()


def test_real_unified_console_retains_pause_and_no_short_checks(tmp_path):
    harness = provision_console(tmp_path)
    try:
        store = harness.store
        with pytest.raises(ApiError, match='POSITION_LIMIT'):
            store.order(buy('1'), 'oversize', market(), 90)
        store.action(dict(action='pause_new', expected_revision=1), 'pause', market(), 90)
        with pytest.raises(ApiError, match='ENTRIES_PAUSED'):
            store.order(buy(), 'paused', market(), 90)
        with pytest.raises(ApiError, match='INSUFFICIENT_POSITION'):
            store.order(dict(buy(), side='SELL'), 'short', market(), 90)
        assert store.execution.projection()['commands'] == []
    finally:
        harness.close()


def test_existing_legacy_fill_is_read_only_and_never_imported_as_execution(tmp_path):
    harness = provision_console(tmp_path)
    try:
        legacy = dict(id='legacy-immediate-fill', symbol='BTCUSDT', side='BUY', quantity='0.1',
            price='50000', notional='5000', fee='5', status='FILLED', strategy_id=None,
            created_at='2026-10-20T00:00:00Z', provenance='LEGACY_LOCAL_IMMEDIATE_FILL')
        with harness.store.transaction() as conn:
            conn.execute(insert(orders).values(id=legacy['id'], data=legacy))
        with pytest.raises(ApiError, match='LEGACY_LEDGER_READ_ONLY'):
            harness.store.order(buy(), 'legacy-must-not-execute', market(), 90)
        with harness.store.engine.connect() as conn:
            assert conn.execute(select(orders.c.data)).scalar_one() == legacy
        assert harness.runtime.projection()['commands'] == []
        assert harness.runtime.account_view()['cash'] == '100000'
    finally:
        harness.close()
