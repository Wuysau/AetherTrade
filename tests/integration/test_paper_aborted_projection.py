"""Authoritatively unsent commands remain distinct from uncertain orders."""
import psycopg
import pytest
from decimal import Decimal

from integration.test_unified_paper_runtime import runtime, runtime_config, market, data
from trading_system.execution.commands import CommandRepository
from trading_system.server.paper_runtime import scope_for
from trading_system.contracts import types as t
from trading_system.risk.reservations import ReservationService, ReconciliationProof

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


@pytest.mark.parametrize('next_symbol', ['BTCUSDT', 'ETHUSDT'])
def test_released_unsent_entry_allows_next_intent_without_a_venue_order(runtime, next_symbol):
    root = runtime.accept(dict(symbol='BTCUSDT', side='BUY', quantity='1'), 'unsent-entry', market(), 90, data())
    scope = scope_for('BTCUSDT')
    with runtime.uow.transaction(scope) as tx:
        command = CommandRepository(tx).get(root['command_id'])
        CommandRepository(tx).abort_unsent(root['command_id'], 'EXPIRED_TEST_REQUEST')
        book = runtime._book(tx, scope, refresh=True)
        reservation, = tx.reservations.for_intent(command.intent_id)
        ref = book.evidence[0]
    evidence = t.ReservationEvidence(reservation_id=reservation.reservation_id,
        venue_freeze_ref=ref, reflected_amount=Decimal(0), command_certainty='NOT_SENT',
        terminal_verified=True, watermark=ref.id + ':' + ref.hash)
    proof = ReconciliationProof(evidence=evidence, scope=scope, intent_id=reservation.intent_id,
        asset=reservation.asset, command_refs=reservation.command_refs,
        book_sequence=book.sequence, obligations_cleared=True)
    released = ReservationService(runtime.uow, scope, None, clock=lambda: runtime.clock().utc_now,
        load_proof=lambda tx, ev: proof).reconcile(evidence)
    assert released.held == 0
    subsequent = market()
    subsequent['quotes'][0]['symbol'] = next_symbol
    entry = runtime.accept(dict(symbol=next_symbol, side='BUY', quantity='1'),
        'new-intent', subsequent, 90, data())
    assert entry['status'] == 'PENDING'
    runtime.dispatch(entry['command_id'])
    commands = {c['command_id']: c for c in runtime.projection()['commands']}
    assert commands[root['command_id']]['order_status'] == 'ABORTED_UNSENT'
    assert commands[entry['command_id']]['order_status'] == 'SUBMITTED'


def test_owner_waits_for_a_short_console_read_without_losing_the_claim(runtime):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    acquired = threading.Event()
    started = threading.Event()
    def owner():
        started.set()
        with runtime._claim(SimpleNamespace(venue_account_id='paper-local', product='SPOT')):
            acquired.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with runtime.control_guard():
            future = pool.submit(owner)
            assert started.wait(1)
            time.sleep(.2)
            assert not future.done()
            assert not acquired.is_set()
        future.result(timeout=3)
    assert acquired.is_set()
