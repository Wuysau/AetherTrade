"""Real PostgreSQL review and owner-maintenance regressions."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os

import pytest
from sqlalchemy import select

from trading_system.server.paper_runtime import UnifiedPaperRuntime, attach_console_runtime, scope_for
from trading_system.server.paper_maintenance import run_owner, review_daily
from trading_system.server.paper_provision import provision
from trading_system.server.store import Store, cause_locks
from trading_system.contracts import types as t
from integration.test_unified_paper_runtime import market, data


@pytest.fixture
def paper(tmp_path):
    assert os.environ.get('PAPER_RUNTIME_ISOLATED_DATABASE') == 'YES', 'NOT_RUN: isolated PG required'
    profile = provision(os.environ['PAPER_RUNTIME_ADMIN_DSN'], initial_cash='10000',
        journal_directory=tmp_path/'owner', isolated_paper=True)
    runtime = UnifiedPaperRuntime(**profile.runtime_kwargs())
    store = Store(profile.console_database_url)
    store.initialize()
    store.setup('owner', 'a-long-password', '10000', 24)
    attach_console_runtime(store, runtime, market)
    yield runtime, store, profile
    store.close()


def test_owner_survives_storage_failure_and_reports_recovery():
    class Worker:
        count = 0
        def tick(self):
            self.count += 1
            if self.count == 1:
                raise ConnectionError('must-not-print-sensitive-dsn')
            return []
    worker = Worker()
    reports = []
    run_owner(worker, stopped=lambda: worker.count == 3, sleep=lambda _: None, report=reports.append)
    assert worker.count == 3
    assert reports == ['PAPER_OWNER_NEEDS_OPERATOR_REVIEW ConnectionError',
        'PAPER_OWNER_STORAGE_RESTORED; durable locks and original IDs retained']


def test_unchanged_queries_preserve_pending_basis(paper):
    runtime, store, _ = paper
    entry = runtime.accept(dict(symbol='BTCUSDT',side='BUY',quantity='1'),'basis',market(),90,data())
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        before = tx.projections.book(tx.scope)
        assert runtime._book(tx,tx.scope,refresh=True) == before
    assert runtime.dispatch(entry['command_id'])['status'] == 'VENUE_CONFIRMED'
    runtime.reconcile(entry['command_id'])
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        before = tx.projections.book(tx.scope)
    runtime.reconcile(entry['command_id'])
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        assert tx.projections.book(tx.scope) == before


def test_owner_repairs_partial_without_model_or_fabricated_verification(paper):
    runtime, store, _ = paper
    entry = runtime.accept(dict(symbol='BTCUSDT',side='BUY',quantity='1'),'safety',market(),90,data())
    runtime.dispatch(entry['command_id'])
    runtime.inject(entry['command_id'],dict(event_id='forty',status='PARTIALLY_FILLED',cumulative_quantity='0.4',price='100'))
    runtime.tick()
    commands = runtime.projection()['commands']
    stops = [c for c in commands if c.get('operation') == 'SET_PROTECTION']
    # Explicit synthetic acceptance is still not protection verification.
    with runtime.connect() as conn:
        assert conn.execute("SELECT count(*) FROM ledger.commands WHERE sequence=1 AND payload->>'operation'='SET_PROTECTION'").fetchone()[0] == 1
    runtime.tick()
    assert runtime.projection()['targets'][0]['protected_quantity'] == '0'
    assert runtime.projection()['risk_locks']


def test_daily_review_requires_next_day_flat_proof_and_separate_role(paper):
    runtime, store, profile = paper
    from zoneinfo import ZoneInfo
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    record = dict(lock_id='console-daily:'+today, scope={}, cause='DAILY_LOSS',state='ACTIVE',
        permission='NO_NEW_RISK',required_evidence=['DAILY_LOSS_REVIEW'],raised_at=now.isoformat(),
        risk_day=today,risk_day_timezone='Asia/Shanghai',baseline='10000',observed_equity='7000',prospective_fee='0',policy_revision=1)
    with store.transaction() as conn:
        store._save_daily_lock(conn,record)
    with pytest.raises(ValueError,match='NEXT_CONFIGURED_RISK_DAY'):
        review_daily(runtime,record['lock_id'],resolver_dsn=profile.resolver_dsn,operator_review_id='explicit-review')
    reading = runtime.clock()
    runtime.clock = lambda: replace(reading,utc_now=(now+timedelta(days=1)).isoformat(),monotonic_ns=reading.monotonic_ns+86400*10**9)
    with pytest.raises(ValueError,match='SEPARATE_RESTRICTED'):
        review_daily(runtime,record['lock_id'],resolver_dsn=profile.ledger_dsn,operator_review_id='explicit-review')
    assert runtime.projection()['risk_locks']
    result = review_daily(runtime,record['lock_id'],resolver_dsn=profile.resolver_dsn,operator_review_id='explicit-review')
    assert result['resolved'], result
    with store.transaction() as conn:
        assert conn.execute(select(cause_locks.c.data)).scalar_one()['state'] == 'RESOLVED'
    assert runtime.projection()['risk_locks'] == []

def test_later_cycle_rearms_resolved_protection_duty(paper):
    runtime, store, profile = paper
    first = runtime.accept(dict(symbol='BTCUSDT',side='BUY',quantity='1'),'cycle-one',market(),90,data())
    runtime.dispatch(first['command_id'])
    runtime.inject(first['command_id'],dict(event_id='first',status='FILLED',cumulative_quantity='1',price='100'))
    stop = runtime.protect(first['command_id'],'first-stop')
    runtime.dispatch(stop['command_id'])
    runtime.inject(stop['command_id'],dict(event_id='first-ok',status='PROTECTION_VERIFIED',cumulative_quantity='0'))
    assert all(r['resolved'] for r in runtime.recover(first['command_id'],resolver_dsn=profile.resolver_dsn)['resolutions'])
    runtime.inject(stop['command_id'],dict(event_id='first-exit',status='FILLED',cumulative_quantity='1',price='95'))
    second = runtime.accept(dict(symbol='BTCUSDT',side='BUY',quantity='1'),'cycle-two',market(),90,data())
    runtime.dispatch(second['command_id'])
    runtime.inject(second['command_id'],dict(event_id='second',status='PARTIALLY_FILLED',cumulative_quantity='0.4',price='100'))
    assert any(lock['cause']=='PROTECTION_PENDING' for lock in runtime.projection()['risk_locks'])

def test_daily_review_retains_lock_for_nonflat_account(paper):
    from zoneinfo import ZoneInfo
    runtime, store, profile = paper
    entry = runtime.accept(dict(symbol='BTCUSDT',side='BUY',quantity='1'),'not-flat',market(),90,data())
    runtime.dispatch(entry['command_id'])
    runtime.inject(entry['command_id'],dict(event_id='held',status='PARTIALLY_FILLED',cumulative_quantity='0.4',price='100'))
    now = datetime.now(timezone.utc)
    day = (now-timedelta(days=1)).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    record = dict(lock_id='console-daily:'+day,scope={},cause='DAILY_LOSS',state='ACTIVE',
        permission='NO_NEW_RISK',required_evidence=['DAILY_LOSS_REVIEW'],raised_at=(now-timedelta(days=1)).isoformat(),
        risk_day=day,risk_day_timezone='Asia/Shanghai',baseline='10000',observed_equity='7000',prospective_fee='0',policy_revision=1)
    with store.transaction() as conn:
        store._save_daily_lock(conn,record)
    with pytest.raises(ValueError,match='EXPLICIT_OPERATOR_REVIEW_REQUIRED'):
        review_daily(runtime,record['lock_id'],resolver_dsn=profile.resolver_dsn,operator_review_id='')
    with pytest.raises(ValueError,match='FLAT_COMPLETE_ACCOUNT_REQUIRED'):
        review_daily(runtime,record['lock_id'],resolver_dsn=profile.resolver_dsn,operator_review_id='review')
    assert any(lock['cause']=='DAILY_LOSS' for lock in runtime.projection()['risk_locks'])

def test_maintenance_outlives_entry_quote_and_entry_grant_without_model(paper, monkeypatch):
    runtime, store, _ = paper
    entry = runtime.accept(dict(symbol='BTCUSDT',side='BUY',quantity='1'),'old-entry',market(),90,data())
    runtime.dispatch(entry['command_id'])
    runtime.inject(entry['command_id'],dict(event_id='old-partial',status='PARTIALLY_FILLED',cumulative_quantity='0.4',price='100'))
    reading = runtime.clock()
    future = datetime.fromisoformat(reading.utc_now)+timedelta(minutes=5)
    runtime.clock = lambda: replace(reading,utc_now=future.isoformat(),monotonic_ns=reading.monotonic_ns+300*10**9)
    def unavailable(*_):
        raise AssertionError('entry/model/control path unavailable')
    runtime.current_control = unavailable
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        original = runtime._request(tx,entry['command_id'])
        assert datetime.fromisoformat(original.authorization.authorization.expires_at) < future
        assert (future-datetime.fromisoformat(original.quote_at)).total_seconds() > original.maximum_age
    from trading_system.execution.dispatcher import CommandDispatcher
    diagnostics = []
    check = CommandDispatcher._check
    def traced(self, *args, **kwargs):
        try:
            return check(self, *args, **kwargs)
        except Exception as exc:
            diagnostics.append(str(exc))
            raise
    monkeypatch.setattr(CommandDispatcher, '_check', traced)
    results = runtime.tick()
    assert not any(r.get('error_type') for r in results), (results, diagnostics)
    with runtime.connect() as conn:
        rows = conn.execute("SELECT record_id FROM ledger.commands WHERE sequence=1 AND payload->>'operation'='SET_PROTECTION'").fetchall()
        assert len(rows) == 1
    with runtime.uow.transaction(scope_for('BTCUSDT')) as tx:
        request = runtime._request(tx, rows[0][0])
        assert request.mode == 'MAINTENANCE' and request.quantity == '0.4'
        assert request.quote_at == original.quote_at  # no fabricated fresh quote
        assert datetime.fromisoformat(request.authorization.authorization.valid_from) == future
    assert runtime.projection()['targets'][0]['protected_quantity'] == '0'
    assert runtime.projection()['risk_locks']
