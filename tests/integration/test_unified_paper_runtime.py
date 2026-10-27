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
    assert restarted.projection()['risk_locks'][0]['cause'] == 'PROTECTION_GAP'
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


def test_partial_cancel_unknown_keeps_full_reservation_and_protection_duty(runtime, runtime_config):
    from trading_system.server.paper_runtime import scope_for
    from trading_system.risk.reservation_repository import ReservationRepository
    initial = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'partial-cancel', market(), 90, data())
    command_id = initial['command_id']
    assert runtime.dispatch(command_id)['status'] == 'VENUE_CONFIRMED'
    runtime.inject(command_id, dict(event_id='partial', status='PARTIALLY_FILLED', cumulative_quantity='0.4', price='100'))
    cancel = runtime.cancel(command_id, 'cancel-original')
    result = runtime.dispatch(cancel['command_id'])
    assert result['status'] == 'UNKNOWN'
    runtime.inject(cancel['command_id'], dict(event_id='cancel-timeout', status='CANCEL_UNKNOWN', cumulative_quantity='0.4'))
    restarted = UnifiedPaperRuntime(**runtime_config)
    assert restarted.reconcile(cancel['command_id'])['filled_quantity'] == '0.4'
    with restarted.uow.transaction(scope_for('BTCUSDT')) as tx:
        holds = ReservationRepository(tx).group()
        assert sum(p.held for p in holds) > 100
        assert any(lock.cause == 'PROTECTION_GAP' for lock in tx.risk_locks.applicable(tx.scope))
    result = restarted.inject(cancel['command_id'], dict(event_id='cancel-confirmed', status='CANCELED', cumulative_quantity='0.4'))
    assert result['status'] == 'CANCELED'
    with restarted.uow.transaction(scope_for('BTCUSDT')) as tx:
        assert sum(p.held for p in ReservationRepository(tx).group()) == 0
