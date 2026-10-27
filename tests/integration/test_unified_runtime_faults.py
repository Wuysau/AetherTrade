"""Independent Linux/PostgreSQL PAPER fault probes; never venue qualification."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import multiprocessing
import os
from pathlib import Path
import signal
from threading import Event
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest
from sqlalchemy.engine import URL

from integration.test_unified_paper_runtime import runtime_config, runtime, market, data
from trading_system.contracts import types as t
from trading_system.server.errors import ApiError
from trading_system.server.paper_runtime import UnifiedPaperRuntime, scope_for, ACCOUNT
from trading_system.server.store import Store


def request(quantity='1', symbol='BTCUSDT', side='BUY', strategy_id=None):
    return dict(symbol=symbol, side=side, quantity=quantity, strategy_id=strategy_id)


def snapshot(config):
    """Export synthetic lifecycle facts only, excluding all credentials/auth tables."""
    tables = ('commands', 'reservations', 'order_projections', 'fills', 'positions',
              'protection_plans', 'risk_locks', 'book_snapshots', 'transaction_observations',
              'audit_events', 'domain_events')
    runtime = UnifiedPaperRuntime(**config)
    result = {'ledger': {}, 'venue': {}}
    with runtime.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        for table in tables:
            rows = conn.execute(sql.SQL('SELECT row_to_json(r) FROM ledger.{} r').format(sql.Identifier(table))).fetchall()
            result['ledger'][table] = sorted((r[0] for r in rows), key=lambda r: json.dumps(r, sort_keys=True))
    with psycopg.connect(config['venue_dsn']) as conn:
        for table in ('paper_engine_commands', 'paper_engine_orders', 'paper_engine_events'):
            rows = conn.execute(sql.SQL('SELECT row_to_json(r) FROM {} r').format(sql.Identifier(table))).fetchall()
            result['venue'][table] = sorted((r[0] for r in rows), key=lambda r: json.dumps(r, sort_keys=True))
    return result


def preserve(name, states):
    destination = os.environ.get('PAPER_RUNTIME_EVIDENCE_DIR')
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (name + '-' + uuid4().hex + '.json')).write_text(
            json.dumps(states, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')


def kill_worker(config, phase, command_id, marker):
    """Terminate only this spawned child at a real durable/side-effect boundary."""
    from trading_system.engine.paper import PaperEngine
    from trading_system.storage.uow import LedgerTx

    def die():
        marker.send(phase)
        os.kill(os.getpid(), signal.SIGKILL)

    current = UnifiedPaperRuntime(**config)
    if phase == 'durable':
        current.accept(request(), 'killed-durable', market(), 90, data())
        die()
    elif phase in ('before-venue', 'after-venue'):
        original = PaperEngine.submit

        def submit(engine, command, permit):
            if phase == 'before-venue':
                die()
            receipt = original(engine, command, permit)
            die()
            return receipt

        PaperEngine.submit = submit
        current.dispatch(command_id)
    elif phase == 'after-ack':
        current._reconcile = lambda *args, **kwargs: die()
        current.dispatch(command_id)
    elif phase == 'protection-staged':
        original = LedgerTx._append

        def append(tx, table, *args, **kwargs):
            result = original(tx, table, *args, **kwargs)
            if table == 'protection_plans':
                die()
            return result

        LedgerTx._append = append
        current.inject(command_id, dict(event_id='kill-fill', status='FILLED', cumulative_quantity='1', price='100'))
    raise AssertionError('configured fault boundary was not reached')


def crash(config, phase, command_id=None):
    context = multiprocessing.get_context('spawn')
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=kill_worker, args=(config, phase, command_id, send))
    process.start()
    send.close()
    try:
        process.join(30)
        if process.is_alive():
            process.kill()
            process.join(10)
            pytest.fail('PAPER child exceeded bounded fault deadline')
        assert receive.poll(2), 'the child must reach the exact fault boundary'
        assert receive.recv() == phase
        assert process.exitcode == -signal.SIGKILL
    finally:
        receive.close()
        if process.is_alive():
            process.kill()
            process.join(10)


@pytest.mark.parametrize('phase', ['durable', 'before-venue', 'after-venue', 'after-ack'])
def test_process_kill_queries_original_id_without_duplicate(runtime, runtime_config, phase):
    command_id = None
    if phase != 'durable':
        command_id = runtime.accept(request(), 'killed-' + phase, market(), 90, data())['command_id']
    states = {'before': snapshot(runtime_config)}
    try:
        crash(runtime_config, phase, command_id)
        states['after_crash'] = snapshot(runtime_config)
        restarted = UnifiedPaperRuntime(**runtime_config)
        commands = restarted.projection()['commands']
        assert len(commands) == 1
        command_id = commands[0]['command_id']
        original_identity = states['after_crash']['ledger']['commands'][0]['payload']['venue_client_order_id']
        count = len(states['after_crash']['venue']['paper_engine_commands'])
        expected_count = 0 if phase in ('durable', 'before-venue') else 1
        assert count == expected_count
        queried = restarted.reconcile(command_id)
        assert queried['status'] == ('UNKNOWN' if expected_count == 0 else 'SUBMITTED')
        restarted.dispatch(command_id)
        restarted.dispatch(command_id)
        states['after_recovery'] = snapshot(runtime_config)
        final_count = len(states['after_recovery']['venue']['paper_engine_commands'])
        # A proven READY command can be explicitly submitted with its original
        # identity; MAY_HAVE_SENT without an affirmative result stays UNKNOWN.
        assert final_count == (0 if phase == 'before-venue' else 1)
        if final_count:
            assert states['after_recovery']['venue']['paper_engine_commands'][0]['client_id'] == original_identity
        assert restarted.projection()['targets'][0]['actual_quantity'] == '0'
        assert not states['after_recovery']['ledger']['fills']
    finally:
        preserve('process-' + phase, states)


def test_process_kill_rolls_back_partial_protection_projection_and_recovers_fill(runtime, runtime_config):
    command_id = runtime.accept(request(), 'kill-protection', market(), 90, data())['command_id']
    runtime.dispatch(command_id)
    states = {'before': snapshot(runtime_config)}
    try:
        crash(runtime_config, 'protection-staged', command_id)
        states['after_crash'] = snapshot(runtime_config)
        assert len(states['after_crash']['venue']['paper_engine_events']) == 1
        for table in ('fills', 'positions', 'protection_plans'):
            assert states['after_crash']['ledger'][table] == states['before']['ledger'][table]
        restarted = UnifiedPaperRuntime(**runtime_config)
        assert restarted.reconcile(command_id)['filled_quantity'] == '1'
        assert restarted.reconcile(command_id)['filled_quantity'] == '1'
        states['after_recovery'] = snapshot(runtime_config)
        assert len(states['after_recovery']['ledger']['fills']) == 1
        assert restarted.projection()['targets'][0]['actual_quantity'] == '1'
        assert any(lock['cause'] == 'PROTECTION_GAP' for lock in restarted.projection()['risk_locks'])
        assert all(row['payload']['state'] == 'GAP' for row in states['after_recovery']['ledger']['protection_plans'])
    finally:
        preserve('process-protection', states)


def test_restart_ready_command_queries_original_identity_before_submit(runtime_config, monkeypatch):
    from trading_system.engine.paper import PaperEngine
    crash(runtime_config, 'durable')
    restarted = UnifiedPaperRuntime(**runtime_config)
    command_id = restarted.projection()['commands'][0]['command_id']
    calls = []
    original_query, original_submit = PaperEngine.query, PaperEngine.submit

    def query(engine, case):
        calls.append(('query', tuple(case.command_ids)))
        return original_query(engine, case)

    def submit(engine, command, permit):
        calls.append(('submit', command.command_id))
        return original_submit(engine, command, permit)

    monkeypatch.setattr(PaperEngine, 'query', query)
    monkeypatch.setattr(PaperEngine, 'submit', submit)
    before = snapshot(runtime_config)
    restarted.dispatch(command_id)
    after = snapshot(runtime_config)
    preserve('ready-query-order', {'before': before, 'after': after, 'calls': calls})
    assert calls[0] == ('query', (command_id,))
    assert next(index for index, call in enumerate(calls) if call[0] == 'query') < next(
        index for index, call in enumerate(calls) if call[0] == 'submit')
    assert len(after['venue']['paper_engine_commands']) == 1


def test_two_strategies_cannot_reserve_the_same_collateral(runtime, runtime_config):
    account = data()
    account['risk']['max_position_pct'] = '100'
    quotes = market()
    quotes['quotes'].append(dict(quotes['quotes'][0], symbol='ETHUSDT'))

    def accept(index):
        symbol = ('BTCUSDT', 'ETHUSDT')[index]
        strategy = dict(id='competing-' + str(index), mode='RULE', revision=1, budget='10000', rule={})
        try:
            return runtime.accept(request('60', symbol, strategy_id=strategy['id']),
                'competing-' + str(index), quotes, 90, account, strategy)
        except ApiError as exc:
            return exc.code

    states = {'before': snapshot(runtime_config)}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(accept, range(2)))
        assert sum(isinstance(result, dict) for result in outcomes) == 1
        assert sum(isinstance(result, str) for result in outcomes) == 1
        states['after'] = snapshot(runtime_config)
        assert len({row['record_id'] for row in states['after']['ledger']['commands']}) == 1
        reservations = states['after']['ledger']['reservations']
        assert sum(Decimal(row['payload']['amount']) for row in reservations) <= Decimal('10000')
        assert not states['after']['venue']['paper_engine_commands']
    finally:
        preserve('competing-strategies', states)


def test_stale_quote_cannot_create_risk_or_reservations(runtime, runtime_config):
    quote = market()
    quote['quotes'][0]['as_of'] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    before = snapshot(runtime_config)
    with pytest.raises(ApiError, match='QUOTE_UNAVAILABLE'):
        runtime.accept(request(), 'stale-quote', quote, 90, data())
    after = snapshot(runtime_config)
    assert after == before
    preserve('stale-quote', {'before': before, 'after': after})


def test_manual_reduction_blocks_old_automatic_target_after_restart(runtime, runtime_config):
    strategy = dict(id='old-rule', mode='RULE', revision=1, budget='2000', rule={},
        _activated_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    automatic = dict(request(strategy_id=strategy['id']), decision_run_id='old-rule:initial')
    entry = runtime.accept(automatic, 'old-target-entry', market(), 90, data(), strategy)['command_id']
    runtime.dispatch(entry)
    runtime.inject(entry, dict(event_id='old-target-filled', status='FILLED', cumulative_quantity='1', price='100'))
    reduce = runtime.accept(request('0.25', side='SELL'), 'manual-target-change', market(), 90, data())['command_id']
    runtime.dispatch(reduce)
    runtime.inject(reduce, dict(event_id='manual-target-filled', status='FILLED', cumulative_quantity='0.25', price='100'))
    states = {'before_old_target_retry': snapshot(runtime_config)}
    try:
        restarted = UnifiedPaperRuntime(**runtime_config)
        restarted.dispatch(entry)
        proposal = dict(request('0.25', strategy_id=strategy['id']), decision_run_id='old-rule:stale-target')
        with pytest.raises(ApiError) as denied:
            restarted.accept(proposal, 'old-target-refill', market(), 90, data(), strategy)
        assert denied.value.code == 'MANUAL_REDUCTION_REAUTH_REQUIRED'
        assert restarted.account_view()['positions'][0]['quantity'] == '0.75'
        states['after_old_target_retry'] = snapshot(runtime_config)
        assert len(states['after_old_target_retry']['venue']['paper_engine_commands']) == 2
    finally:
        preserve('manual-reduction-old-target', states)


def test_pause_transaction_serializes_with_owner_in_distinct_database(runtime, runtime_config):
    """A console transaction must share the actual owner guard across DBs."""
    options = conninfo_to_dict(os.environ['PAPER_RUNTIME_ADMIN_DSN'])
    database = 'paper_console_guard_' + uuid4().hex[:12]
    with psycopg.connect(make_conninfo(**options), autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    url = URL.create('postgresql+psycopg', username=options['user'], password=options.get('password'),
        host=options['host'], port=int(options['port']), database=database,
        query={key: options[key] for key in ('hostaddr', 'sslmode', 'connect_timeout') if key in options})
    store = Store(url)
    store.initialize()
    store.setup('synthetic-owner', 'synthetic-long-password', '10000', 24)
    store.execution = runtime
    started = Event()
    shard = t.ExecutionShard(environment=t.Environment.PAPER, venue_account_id=ACCOUNT,
        product=t.Product.SPOT, node_id='independent-guard-probe', execution_epoch=1)

    def pause():
        started.set()
        return store.action(dict(action='pause_new', expected_revision=1), 'guard-pause', market(), 90)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with runtime._claim(shard):
                future = pool.submit(pause)
                assert started.wait(2)
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.3)
            assert future.result(timeout=5)['affected_count'] == 1
    finally:
        store.close()
