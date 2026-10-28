"""Actual isolated three-database protected reduction; no implicit fills."""
from datetime import datetime, timezone
import os
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.paper_runtime

from trading_system.server.errors import ApiError
from trading_system.server.paper_provision import provision
from trading_system.server.paper_runtime import UnifiedPaperRuntime


def market():
    return dict(status='CONNECTED', quotes=[dict(symbol='BTCUSDT', price='100',
        as_of=datetime.now(timezone.utc).isoformat(), stale=False)])


def data():
    return dict(account=dict(initial_cash='10000', cash='10000', paused=False),
        risk=dict(max_position_pct='25', max_daily_loss='2000', revision=1))


def record_ledger(runtime, stage):
    """Optional secret-free original facts for the reproducible evidence runner."""
    destination = os.environ.get('PAPER_REDUCTION_EVIDENCE_DIR')
    if not destination:
        return
    with runtime.connect() as conn:
        ledger = {table: [row[0] for row in conn.execute('SELECT payload FROM ledger.' + table
            + ' ORDER BY scope_id,record_id,sequence').fetchall()] for table in
            ('commands', 'order_projections', 'fills', 'risk_locks', 'protection_plans')}
        ledger['adjustment_audits'] = [row[0] for row in conn.execute("SELECT payload FROM ledger.audit_events "
            "WHERE record_id LIKE 'paper:adjustment%%' ORDER BY scope_id,record_id,sequence").fetchall()]
    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    (path / (stage + '.json')).write_text(json.dumps(dict(projection=runtime.projection(), ledger=ledger),
        ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.fixture
def prepared(tmp_path):
    if os.environ.get('PAPER_RUNTIME_ISOLATED_DATABASE') != 'YES':
        pytest.fail('NOT_RUN: explicit isolated PostgreSQL required')
    profile = provision(os.environ['PAPER_RUNTIME_ADMIN_DSN'], initial_cash='10000',
        journal_directory=tmp_path, isolated_paper=True)
    runtime = UnifiedPaperRuntime(**profile.runtime_kwargs())
    entry = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'entry', market(), 90, data())
    runtime.dispatch(entry['command_id'])
    runtime.inject(entry['command_id'], dict(event_id='entry-fill', status='FILLED', cumulative_quantity='1', price='100'))
    stop = runtime.protect(entry['command_id'], 'stop')
    runtime.dispatch(stop['command_id'])
    runtime.inject(stop['command_id'], dict(event_id='stop-ok', status='PROTECTION_VERIFIED', cumulative_quantity='0'))
    runtime.recover(entry['command_id'], resolver_dsn=profile.resolver_dsn)
    assert runtime.projection()['risk_locks'] == []
    return runtime, profile, stop['command_id']


def test_explicit_cancel_confirm_reduce_reprotect_survives_restart(prepared):
    runtime, profile, stop = prepared
    record_ledger(runtime, '01-healthy-verified-stop')
    with pytest.raises(ApiError):
        runtime.cancel(stop, 'ordinary-cancel')
    receipt = runtime.accept(dict(symbol='BTCUSDT', side='SELL', quantity='0.5'), 'ordinary-reduction', market(), 90, data())
    assert receipt['operation'] == 'PROTECTED_REDUCTION'
    cancel = receipt['command_id']
    assert runtime.projection()['targets'][0]['protected_quantity'] == '1'
    assert runtime.dispatch(cancel)['status'] == 'UNKNOWN'
    record_ledger(runtime, '02-cancel-unknown-duty-retained')
    with pytest.raises(ApiError) as error:
        runtime.continue_reduction(cancel, market(), 90, data())
    assert error.value.code == 'REDUCTION_CANCEL_UNCONFIRMED'
    runtime = UnifiedPaperRuntime(**profile.runtime_kwargs())
    runtime.inject(cancel, dict(event_id='stop-cancel-ok', status='CANCELED', cumulative_quantity='0'))
    assert runtime.projection()['targets'][0]['protected_quantity'] == '0'
    assert runtime.projection()['risk_locks']
    assert runtime.projection()['protection_adjustments'][0]['phase'] == 'REVIEW_REQUIRED'
    with pytest.raises(ApiError) as review_error:
        runtime.continue_reduction(cancel, market(), 90, data())
    assert review_error.value.code == 'REDUCTION_REVIEW_REQUIRED'
    from trading_system.server.paper_runtime import scope_for
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        assert runtime.pending_protected_adjustment(tx)
    runtime.recover(cancel, resolver_dsn=profile.resolver_dsn)
    record_ledger(runtime, '03-cancel-confirmed-reviewed-gap')
    close = runtime.continue_reduction(cancel, market(), 90, data())['command_id']
    assert runtime.dispatch(close)['status'] == 'VENUE_CONFIRMED'
    assert runtime.account_view()['positions'][0]['quantity'] == '1'
    runtime.inject(close, dict(event_id='close-fill', status='FILLED', cumulative_quantity='0.5', price='100'))
    assert runtime.account_view()['positions'][0]['quantity'] == '0.5'
    record_ledger(runtime, '04-explicit-reduction-filled')
    repair = runtime.protect(close, 'residual-stop')['command_id']
    assert runtime.dispatch(repair)['status'] == 'VENUE_CONFIRMED'
    runtime.inject(repair, dict(event_id='residual-ok', status='PROTECTION_VERIFIED', cumulative_quantity='0'))
    assert runtime.projection()['targets'][0]['protected_quantity'] == '0.5'
    record_ledger(runtime, '05-residual-protection-verified')
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        assert not runtime.pending_protected_adjustment(tx)
    assert runtime.accept(dict(symbol='BTCUSDT', side='SELL', quantity='0.5'),
        'ordinary-reduction', market(), 90, data())['command_id'] == cancel
    assert runtime.continue_reduction(cancel, market(), 90, data())['command_id'] == close


def test_adjustment_rejects_unbounded_quantity_and_changed_actual(prepared):
    runtime, profile, stop = prepared
    with pytest.raises(ApiError):
        runtime.begin_reduction(stop, '1.1', 'too-large')
    cancel = runtime.begin_reduction(stop, '0.5', 'adjust-stop')['command_id']
    runtime.dispatch(cancel)
    # A racing stop fill is real evidence; never assume the canceled amount remains.
    runtime.inject(stop, dict(event_id='stop-race', status='PARTIALLY_FILLED', cumulative_quantity='0.25', price='95'))
    runtime.inject(cancel, dict(event_id='stop-race-canceled', status='CANCELED', cumulative_quantity='0.25'))
    with pytest.raises(ApiError) as error:
        runtime.continue_reduction(cancel, market(), 90, data())
    assert error.value.code == 'REDUCTION_CANCEL_UNCONFIRMED'


def test_failed_protection_exit_only_is_never_reclassified_by_adjustment(prepared):
    from trading_system.contracts import types as t
    from trading_system.server.paper_runtime import scope_for
    runtime, profile, stop = prepared
    scope = scope_for('BTCUSDT')
    with runtime.uow.transaction(scope) as tx:
        tx.risk_locks.append(t.RiskLock(lock_id='prior-real-failure', scope=scope, cause='PROTECTION_GAP',
            state='ACTIVE', permission=t.RiskPermission.EXIT_ONLY,
            required_evidence=('PROTECTION_OR_EXIT_CONFIRMED',), raised_at=runtime.clock().utc_now), 0)
    with pytest.raises(ApiError) as error:
        runtime.begin_reduction(stop, '0.5', 'partial-under-exit-only')
    assert error.value.code == 'PROTECTION_ADJUSTMENT_UNVERIFIED'
    assert any(lock['lock_id'] == 'prior-real-failure' for lock in runtime.projection()['risk_locks'])


def test_existing_store_sell_returns_durable_adjustment_receipt(prepared):
    from sqlalchemy import select, update
    from trading_system.server.store import Store, state
    from trading_system.server.paper_runtime import attach_console_runtime
    runtime, profile, stop = prepared
    store = Store(profile.console_database_url)
    try:
        store.initialize()
        store.setup('owner', 'test-local-long-password', '10000', 24)
        attach_console_runtime(store, runtime, market)
        with store.engine.begin() as connection:
            control = connection.execute(select(state.c.data).where(state.c.id == 1)).scalar_one()
            control['account']['initial_cash'] = '10000'
            control['account']['cash'] = '10000'
            connection.execute(update(state).where(state.c.id == 1).values(data=control))
        payload = dict(symbol='BTCUSDT', side='SELL', quantity='0.5',
            expected_revision=control['account']['revision'])
        receipt = store.order(payload, 'store-reduce', market(), 90)
        assert receipt['order']['operation'] == 'PROTECTED_REDUCTION'
        assert receipt['order']['phase'] == 'CANCEL_CONFIRM_REQUIRED'
        assert store.order(payload, 'store-reduce', market(), 90) == receipt
        assert runtime.account_view()['positions'][0]['quantity'] == '1'
        assert runtime.dispatch(receipt['order']['command_id'])['status'] == 'UNKNOWN'
    finally:
        store.close()


def test_obsolete_adjustment_is_durably_invalidated_after_review_and_allows_repair(prepared):
    from trading_system.server.paper_runtime import scope_for
    runtime, profile, stop = prepared
    cancel = runtime.begin_reduction(stop, '0.5', 'race-adjust')['command_id']
    runtime.dispatch(cancel)
    runtime.inject(stop, dict(event_id='race-quarter', status='PARTIALLY_FILLED', cumulative_quantity='0.25', price='95'))
    runtime.inject(cancel, dict(event_id='race-terminal', status='CANCELED', cumulative_quantity='0.25'))
    record_ledger(runtime, 'race-01-terminal-cancel-unknown-review-required')
    from trading_system.server.paper_maintenance import maintain
    assert maintain(runtime) == []
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        assert runtime.pending_protected_adjustment(tx)
    runtime.recover(cancel, resolver_dsn=profile.resolver_dsn)
    assert any(lock['cause'] == 'PROTECTION_PENDING' for lock in runtime.projection()['risk_locks'])
    assert runtime.projection()['protection_adjustments'][0]['phase'] == 'REVIEW_REQUIRED'
    with pytest.raises(ApiError):
        runtime.continue_reduction(cancel, market(), 90, data())
    receipts = maintain(runtime)
    assert len(receipts) == 1 and receipts[0]['status'] == 'VENUE_CONFIRMED'
    record_ledger(runtime, 'race-02-invalidated-and-residual-protection-submitted')
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        assert not runtime.pending_protected_adjustment(tx)

    runtime = UnifiedPaperRuntime(**profile.runtime_kwargs())
    assert runtime.projection()['protection_adjustments'][0]['phase'] == 'INVALIDATED_QUANTITY_CHANGED'
    assert runtime.account_view()['positions'][0]['quantity'] == '0.75'
    assert all(c['reason'] != '受权人工 PAPER 请求' or c['quantity'] != '0.5'
        for c in runtime.projection()['commands'])
    repair = receipts[0]['command_id']
    assert maintain(runtime) == []
    assert runtime.account_view()['positions'][0]['quantity'] == '0.75'
    runtime.inject(repair, dict(event_id='raced-repair-ok', status='PROTECTION_VERIFIED', cumulative_quantity='0'))
    assert runtime.projection()['targets'][0]['protected_quantity'] == '0.75'
    assert runtime.projection()['risk_locks']  # Repair never silently resolves duties.
    record_ledger(runtime, 'race-03-residual-protection-verified')
