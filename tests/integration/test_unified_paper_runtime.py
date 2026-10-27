"""Real isolated PostgreSQL console-to-domain acceptance; no venue/model calls."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import secrets
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from scripts.provision_paper_engine_roles import provision_paper_engine_roles
from trading_system.server.paper_runtime import UnifiedPaperRuntime
from trading_system.server.errors import ApiError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runtime_config(tmp_path_factory):
    admin = os.environ.get('PAPER_RUNTIME_ADMIN_DSN')
    if not admin or os.environ.get('PAPER_RUNTIME_ISOLATED_DATABASE') != 'YES':
        pytest.fail('NOT_RUN: explicit isolated PAPER_RUNTIME PostgreSQL required')
    base = conninfo_to_dict(admin)
    suffix = uuid4().hex[:12]
    names = ['paper_runtime_' + suffix, 'paper_engine_runtime_' + suffix]
    with psycopg.connect(admin, autocommit=True) as conn:
        for name in names:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    ledger = make_conninfo(**dict(base, dbname=names[0]))
    venue = make_conninfo(**dict(base, dbname=names[1]))
    with psycopg.connect(ledger) as conn:
        for file in ['migrations/0001_business_ledger.sql', 'deploy/db/roles.sql',
                     'migrations/0004_command_abort_guard.sql', 'migrations/0006_proposal_intake_role.sql']:
            conn.execute((ROOT / file).read_text(encoding='utf-8'))
    roles = provision_paper_engine_roles(venue, isolated_paper=True,
        runtime_role='paper_engine_cr_' + suffix, event_role='paper_engine_ce_' + suffix,
        runtime_password=secrets.token_urlsafe(32), event_password=secrets.token_urlsafe(32))
    UnifiedPaperRuntime.provision(ledger, initial_cash='10000')
    login, password = 'paper_console_' + suffix, secrets.token_urlsafe(32)
    with psycopg.connect(ledger) as conn:
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE '
            'NOINHERIT NOREPLICATION NOBYPASSRLS').format(sql.Identifier(login), sql.Literal(password)))
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(names[0])))
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(names[0]), sql.Identifier(login)))
        conn.execute(sql.SQL('GRANT ledger_owner TO {}').format(sql.Identifier(login)))
    runtime_dsn = make_conninfo(**dict(base, dbname=names[0], user=login, password=password))
    config = dict(ledger_dsn=runtime_dsn, venue_dsn=roles.runtime_dsn, event_dsn=roles.event_dsn,
        journal_directory=tmp_path_factory.mktemp('runtime-journal'))
    yield config


@pytest.fixture
def runtime(runtime_config):
    return UnifiedPaperRuntime(**runtime_config)


def market():
    return {'status': 'CONNECTED', 'quotes': [dict(symbol='BTCUSDT', price='100',
        as_of=datetime.now(timezone.utc).isoformat(), stale=False)]}


def data():
    return {'account': {'initial_cash': '10000', 'cash': '10000', 'paused': False},
        'risk': {'max_position_pct': '25', 'max_daily_loss': '2000', 'revision': 1}}


def test_pending_receipt_submit_partial_fill_and_restart(runtime, runtime_config):
    receipt = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'initial', market(), 90, data())
    assert receipt['status'] == 'PENDING'
    command_id = receipt['command_id']
    projection = runtime.projection()
    assert projection['commands'][0]['order_status'] == 'PENDING'
    assert projection['targets'][0]['actual_quantity'] == '0'
    runtime.dispatch(command_id)
    assert runtime.projection()['commands'][0]['order_status'] == 'SUBMITTED'
    partial = runtime.inject(command_id, dict(event_id='partial-first', status='PARTIALLY_FILLED', cumulative_quantity='0.4', price='100'))
    assert partial['filled_quantity'] == '0.4' and partial['leaves_quantity'] == '0.6'
    assert partial['protection_state'] == 'GAP'
    restarted = UnifiedPaperRuntime(**runtime_config)
    result = restarted.reconcile(command_id)
    assert result['filled_quantity'] == '0.4' and result['leaves_quantity'] == '0.6'
    assert restarted.projection()['risk_locks'][0]['cause'] == 'PROTECTION_PENDING'
    final = restarted.inject(command_id, dict(event_id='final-first', status='FILLED', cumulative_quantity='1', price='101'))
    assert final['status'] == 'FILLED'
    assert restarted.projection()['targets'][0]['actual_quantity'] == '1'
    reduction = restarted.accept(dict(symbol='BTCUSDT', side='SELL', quantity='0.25'), 'manual-reduce', market(), 90, data())
    assert reduction['status'] == 'PENDING'
    restarted.dispatch(reduction['command_id'])
    result = restarted.inject(reduction['command_id'], dict(event_id='manual-reduction', status='FILLED', cumulative_quantity='0.25', price='100'))
    assert result['filled_quantity'] == '0.25'
    assert restarted.account_view()['positions'][0]['quantity'] == '0.75'


def test_lost_ack_queries_same_id_after_restart_and_never_resubmits(runtime, runtime_config):
    result = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'ack-lost', market(), 90, data())
    command_id = result['command_id']
    receipt = runtime.dispatch(command_id, lose_ack=True)
    assert receipt['status'] == 'UNKNOWN'
    restarted = UnifiedPaperRuntime(**runtime_config)
    assert restarted.reconcile(command_id)['status'] == 'SUBMITTED'
    restarted.dispatch(command_id)
    with psycopg.connect(runtime_config['venue_dsn']) as conn:
        assert conn.execute('SELECT count(*) FROM paper_engine_commands').fetchone()[0] == 1


def test_partial_cancel_unknown_keeps_full_reservation_and_protection_duty(runtime, runtime_config, monkeypatch):
    from trading_system.server.paper_runtime import scope_for
    from trading_system.risk.reservation_repository import ReservationRepository
    initial = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'partial-cancel', market(), 90, data())
    command_id = initial['command_id']
    assert runtime.dispatch(command_id)['status'] == 'VENUE_CONFIRMED'
    runtime.inject(command_id, dict(event_id='partial', status='PARTIALLY_FILLED', cumulative_quantity='0.4', price='100'))
    cancel = runtime.cancel(command_id, 'cancel-original')
    from trading_system.execution.dispatcher import CommandDispatcher
    diagnostic = []
    original_check = CommandDispatcher._check
    def traced_check(self, *args, **kwargs):
        try:
            return original_check(self, *args, **kwargs)
        except Exception as exc:
            diagnostic.append(type(exc).__name__ + ':' + str(exc))
            raise
    monkeypatch.setattr(CommandDispatcher, '_check', traced_check)
    result = runtime.dispatch(cancel['command_id'])
    assert result['status'] == 'UNKNOWN'
    with psycopg.connect(runtime_config['venue_dsn']) as conn:
        assert conn.execute('SELECT pending FROM paper_engine_orders').fetchone()[0] == 'CANCEL', diagnostic
    runtime.inject(cancel['command_id'], dict(event_id='cancel-timeout', status='CANCEL_UNKNOWN', cumulative_quantity='0.4'))
    restarted = UnifiedPaperRuntime(**runtime_config)
    assert restarted.reconcile(cancel['command_id'])['filled_quantity'] == '0.4'
    with restarted.uow.transaction(scope_for('BTCUSDT')) as tx:
        holds = ReservationRepository(tx).group()
        assert sum(p.held for p in holds) > 100
        assert any(lock.cause == 'PROTECTION_PENDING' for lock in tx.risk_locks.applicable(tx.scope))
    result = restarted.inject(cancel['command_id'], dict(event_id='cancel-confirmed', status='CANCELED', cumulative_quantity='0.4'))
    assert result['status'] == 'CANCELED'
    with restarted.uow.transaction(scope_for('BTCUSDT')) as tx:
        assert sum(p.held for p in ReservationRepository(tx).group()) == 0


def test_partial_protection_ack_is_not_verification_and_exact_event_verifies(runtime):
    entry = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'protected-partial', market(), 90, data())
    assert runtime.dispatch(entry['command_id'])['status'] == 'VENUE_CONFIRMED'
    runtime.inject(entry['command_id'], dict(event_id='forty', status='PARTIALLY_FILLED', cumulative_quantity='0.4', price='100'))
    protection = runtime.protect(entry['command_id'], 'protect-forty')
    assert runtime.dispatch(protection['command_id'])['status'] == 'VENUE_CONFIRMED'
    assert runtime.projection()['targets'][0]['protected_quantity'] == '0'
    result = runtime.inject(protection['command_id'], dict(event_id='verify-stop', status='PROTECTION_VERIFIED', cumulative_quantity='0'))
    assert result['protection_state'] == 'VERIFIED_SYNTHETIC'
    target = runtime.projection()['targets'][0]
    assert target['protected_quantity'] == '0.4' and target['in_flight_quantity'] == '0.6'


def test_stop_failure_and_exit_rejection_persist_exit_only_after_restart(runtime, runtime_config):
    entry = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'failed-protect', market(), 90, data())
    assert runtime.dispatch(entry['command_id'])['status'] == 'VENUE_CONFIRMED'
    runtime.inject(entry['command_id'], dict(event_id='entry-fill', status='FILLED', cumulative_quantity='1', price='100'))
    protection = runtime.protect(entry['command_id'], 'stop-will-fail')
    assert runtime.dispatch(protection['command_id'])['status'] == 'VENUE_CONFIRMED'
    runtime.inject(protection['command_id'], dict(event_id='stop-failure', status='PROTECTION_FAILED', cumulative_quantity='0'))
    assert any(lock['cause'] == 'PROTECTION_GAP' and lock['permission'] == 'EXIT_ONLY' for lock in runtime.projection()['risk_locks'])
    with pytest.raises(ApiError) as error:
        runtime.protect(entry['command_id'], 'no-repair-under-exit-only')
    assert error.value.code == 'PROTECTION_REPAIR_LOCKED'
    exit_order = runtime.accept(dict(symbol='BTCUSDT', side='SELL', quantity='1'), 'bounded-exit', market(), 90, data())
    assert runtime.dispatch(exit_order['command_id'])['status'] == 'VENUE_CONFIRMED'
    runtime.inject(exit_order['command_id'], dict(event_id='exit-failure', status='REJECTED', cumulative_quantity='0'))
    restarted = UnifiedPaperRuntime(**runtime_config)
    assert restarted.account_view()['positions'][0]['quantity'] == '1'
    assert any(lock['permission'] == 'EXIT_ONLY' for lock in restarted.projection()['risk_locks'])
    with pytest.raises(ApiError):
        restarted.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'forbid-new-risk', market(), 90, data())


def test_production_provisioner_creates_separate_restricted_connections(tmp_path):
    from trading_system.server.paper_provision import provision
    result = provision(os.environ['PAPER_RUNTIME_ADMIN_DSN'], initial_cash='10000',
        journal_directory=tmp_path / 'journal', output_path=tmp_path / 'config.json', isolated_paper=True)
    runtime = UnifiedPaperRuntime(**result.runtime_kwargs())
    assert runtime.account_view()['cash'] == '10000'
    with runtime.connect() as conn:
        assert conn.execute('SELECT current_user<>session_user').fetchone()[0]
    assert 'password' not in repr(result)


def test_expired_five_second_paper_permit_cannot_cross_side_effect_guard(runtime, runtime_config, monkeypatch):
    from contextlib import contextmanager
    import time
    from trading_system.execution.dispatcher import CommandDispatcher
    from trading_system.server.paper_runtime import scope_for
    from trading_system.risk.reservation_repository import ReservationRepository
    original = CommandDispatcher.__init__
    def slow_guard_init(self, *args, **kwargs):
        guard = kwargs['side_effect_guard']
        @contextmanager
        def slow(command, permit, lease):
            with guard(command, permit, lease):
                time.sleep(5.1)
                yield
        kwargs['side_effect_guard'] = slow
        original(self, *args, **kwargs)
    monkeypatch.setattr(CommandDispatcher, '__init__', slow_guard_init)
    order = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'expired-permit', market(), 90, data())
    result = runtime.dispatch(order['command_id'])
    assert result['status'] == 'UNKNOWN'
    with psycopg.connect(runtime_config['venue_dsn']) as conn:
        assert conn.execute('SELECT count(*) FROM paper_engine_commands').fetchone()[0] == 0
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        assert sum(p.held for p in ReservationRepository(tx).group()) > 100
