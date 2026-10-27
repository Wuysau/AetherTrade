"""Real database/offline-calculator probes. T09/T10/T11 remain NOT_RUN."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from threading import Barrier, Event

import pytest

from integration.support_ip_07 import ip07_database, reservation_case, ReservationCase, money, reserve_worker
from trading_system.contracts import types as t
from trading_system.risk.reservation_repository import ReservationRepository, for_balance
from trading_system.risk.reservations import ReservationService
from trading_system.risk.evaluator import RiskEvaluator
from trading_system.storage.models import MissingEvidence, SequenceConflict, scope_id, encode
from trading_system.execution.commands import CommandRepository, CommandOutcome, OUTCOME_PREFIX, command_hash
from trading_system.storage.command_state import command_request
from trading_system.contracts.canonical import canonical_json


def test_two_strategies_compete(reservation_case, ip07_database):
    a = reservation_case
    b = ReservationCase(ip07_database, group=a.group)
    barrier = Barrier(2)
    def run(case):
        barrier.wait(timeout=10)
        return case.reserve()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, (a, b)))
    assert sum(r.accepted for r in results) == 1
    with a.ledger.uow.transaction(a.scope) as tx:
        holds = ReservationRepository(tx).group()
    assert sum(p.held for p in holds) == Decimal('70')
    loser = b if results[0].accepted else a
    assert len(loser.calls[-1].reservations) == 1  # reread after mutex acquisition
    assert loser.count('commands') == loser.count('intents') == 0


def test_atomic_bundle_and_idempotency(reservation_case):
    c = reservation_case
    first = c.reserve()
    assert first.accepted
    assert c.reserve() == first
    assert [c.count(table) for table in ('intents', 'commands', 'reservations', 'audit_events', 'outbox')] == [1, 1, 1, 2, 1]
    c.case.intent = replace(c.case.intent, target_quantity='0.8')
    with pytest.raises(ValueError, match='identity reused'):
        c.reserve()


def test_command_failure_rolls_back_entire_bundle(reservation_case):
    c = reservation_case
    c.command = replace(c.command, payload_hash='0'*64)
    with pytest.raises(ValueError, match='payload hash'):
        c.reserve()
    assert [c.count(table) for table in ('intents', 'commands', 'reservations', 'audit_events', 'outbox')] == [0]*5


def test_zero_asset_reduction_still_has_one_durable_command(ip07_database):
    c = ReservationCase(ip07_database, sell=True, product=t.Product.USD_M_PERP)
    result = c.reserve()
    assert result.accepted and result.reservations == ()
    assert c.reserve() == result
    assert [c.count(table) for table in ('intents', 'commands', 'reservations', 'audit_events', 'outbox')] == [1, 1, 0, 2, 1]
    c.command_state('VENUE_CONFIRMED')
    assert c.reserve() == result  # creation bundle survives later transitions


def test_zero_asset_incomplete_bundle_is_not_idempotent_success(ip07_database):
    c = ReservationCase(ip07_database, sell=True, product=t.Product.USD_M_PERP)
    # Seed a partial imported bundle using the real ledger, without deleting
    # immutable rows or pretending IP-07 itself committed only half a bundle.
    with c.ledger.uow.transaction(c.scope) as tx:
        inputs = c.inputs(tx, c.case.intent, c.case.book)
        decision = RiskEvaluator(inputs.risk).evaluate(c.case.intent, c.case.book, inputs.market, inputs.policy)
        assert decision.verdict == 'ALLOW' and decision.reserved_assets == ()
        tx.intents.append(c.case.intent)
        ReservationRepository(tx).risk(decision)
    with pytest.raises(MissingEvidence, match='incomplete atomic bundle'):
        c.reserve()
    assert c.count('commands') == c.count('reservations') == c.count('outbox') == 0


def test_expired_unknown_remains_held(reservation_case):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    # Actual durable MAY_HAVE_SENT transition, no bridge/send operation.
    with c.ledger.uow.transaction(c.scope) as tx:
        tx.commands.mark_may_have_sent(tx, c.command.command_id, 'DURABLE_READY')
    c.service.clock = lambda: '2026-10-23T12:00:00Z'
    e = c.evidence(r, reflected='0', certainty='UNKNOWN', terminal=True, mapped=False)
    p = c.service.reconcile(e)
    assert p.held == p.release_pending == Decimal('70')
    assert p.reservation.state != t.ReservationState.RELEASED
    assert c.count('risk_locks') == c.count('reconciliation_cases') == 1
    assert c.service.reconcile(e) == p


def test_reflected_not_double_deduct(reservation_case, ip07_database):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state('VENUE_CONFIRMED')
    old_book = c.case.book
    e = c.evidence(r, mapped=False)
    p = c.service.reconcile(e)
    assert p.held == p.release_pending == Decimal('70')
    assert p.reflected == 0
    e = c.evidence(r)
    p = c.service.reconcile(e)
    assert p.held == p.reflected == Decimal('70')
    assert p.release_pending == 0
    assert for_balance(p, c.case.book).held - for_balance(p, c.case.book).reflected == 0
    assert for_balance(p, old_book).reflected == 0  # late balance gets no credit
    assert p.reservation.state != t.ReservationState.RELEASED
    # The same evidence ID on a different sequence/balance is not a mapping.
    late = replace(c.case.book, sequence=99, balances=(money('100'), money('10', 'ASSET')))
    assert for_balance(p, late).reflected == 0
    c.next_intent()
    duplicate = c.reserve(c.case.book.sequence)
    assert not duplicate.accepted and duplicate.rejection_codes == ('TARGET_RECONCILIATION_REQUIRED',)
    # Preserve the financial capacity counterexample with a separate account:
    # its 100 cash minus the group's existing 70 hold still admits a 20 buy.
    independent = ReservationCase(ip07_database, group=c.group, available='100')
    independent.next_intent('0.2')
    assert independent.reserve().accepted


def test_cash_sells_reserve_base(ip07_database):
    c = ReservationCase(ip07_database, sell=True)
    result = c.reserve()
    assert result.accepted, result.rejection_codes
    assert [(p.reservation.asset, p.held) for p in result.reservations] == [('ASSET', Decimal('2'))]


@pytest.mark.parametrize('field', ['group_capacity', 'strategy_capacity', 'buffer'])
def test_missing_asset_limit_fails_closed(reservation_case, field):
    c = reservation_case
    setattr(c, field, ())
    assert not c.reserve().accepted
    assert c.count('commands') == 0


def test_buffer_and_strategy_limit(reservation_case):
    c = reservation_case
    c.buffer = (money('31'), money('0', 'ASSET'))
    assert not c.reserve().accepted
    c.buffer = (money('0'), money('0', 'ASSET'))
    c.strategy_capacity = (money('69'), money('10', 'ASSET'))
    assert not c.reserve().accepted


def test_group_limit_applies_across_strategy_allocations(reservation_case, ip07_database):
    a = reservation_case
    b = ReservationCase(ip07_database, group=a.group, available='1000')
    a.group_capacity = b.group_capacity = (money('100'), money('10', 'ASSET'))
    assert a.reserve().accepted
    result = b.reserve()
    assert not result.accepted and 'INSUFFICIENT_CAPACITY:USDT' in result.rejection_codes
    assert b.count('commands') == b.count('intents') == 0


def test_stale_book_requests_replan(reservation_case):
    c = reservation_case
    result = c.reserve(0)
    assert not result.accepted and 'REPLAN_REQUIRED:STALE_BOOK' in result.rejection_codes
    assert c.calls == []


def test_release_requires_mapping_and_cleared_obligations(reservation_case):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state('VENUE_CONFIRMED')
    old_book = c.case.book
    e = c.evidence(r, reflected='0', terminal=True, mapped=False)
    assert c.service.reconcile(e).held == 70
    e = c.evidence(r, reflected='0', terminal=True)
    c.proof = replace(c.proof, obligations_cleared=False)
    assert c.service.reconcile(e).held == 70
    c.proof = replace(c.proof, obligations_cleared=True)
    p = c.service.reconcile(e)
    assert p.held == 0 and p.reservation.state == t.ReservationState.RELEASED
    assert for_balance(p, old_book).held == 70  # old balance cannot reuse consumed cash
    assert c.service.reconcile(e) == p


@pytest.mark.parametrize('mismatch', ['scope', 'intent_id', 'asset', 'command_refs', 'book_sequence'])
def test_wrong_mapping_never_credits_reflection(reservation_case, mismatch):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state('VENUE_CONFIRMED')
    e = c.evidence(r)
    wrong = {'scope': replace(c.scope, venue_account_id='other'), 'intent_id': 'other',
             'asset': 'OTHER', 'command_refs': ('other',), 'book_sequence': 999}
    c.proof = replace(c.proof, **{mismatch: wrong[mismatch]})
    p = c.service.reconcile(e)
    assert p.reflected == 0 and p.held == 70


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', '-1', '71'])
def test_invalid_reflected_amount(reservation_case, amount):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    e = c.evidence(r, mapped=False)
    with pytest.raises(ValueError):
        c.service.reconcile(replace(e, reflected_amount=Decimal(amount)))
    assert c.projection(r.reservation_id).sequence == 0


def test_no_live_service(reservation_case):
    c = reservation_case
    with pytest.raises(ValueError, match='LIVE_DISABLED'):
        ReservationService(c.ledger.uow, replace(c.scope, environment=t.Environment.LIVE),
                           c.inputs, clock=lambda: '2026-10-22T12:00:00Z')


@pytest.mark.parametrize('sqlstate', ['40001', '40P01'])
def test_transaction_conflict_reloads_inputs(reservation_case, sqlstate):
    c = reservation_case
    transactions = []
    def load(tx, intent, book):
        transactions.append(tx.transaction_id)
        inputs = c.inputs(tx, intent, book)
        if len(transactions) == 1:
            # A real server error rolls back the first transaction. This is
            # fault injection, not a claim of organically observed deadlock.
            tx.execute("DO $$ BEGIN RAISE EXCEPTION 'injected retry' USING ERRCODE = '" + sqlstate + "'; END $$")
        return inputs
    c.service.load_inputs = load
    assert c.reserve().accepted
    assert len(set(transactions)) == len(c.calls) == 2
    assert c.count('commands') == c.count('reservations') == 1


def test_transaction_retry_is_bounded(reservation_case):
    c = reservation_case
    calls = []
    def load(tx, intent, book):
        calls.append(tx.transaction_id)
        tx.execute("DO $$ BEGIN RAISE EXCEPTION 'injected retry' USING ERRCODE = '40001'; END $$")
    c.service.load_inputs = load
    with pytest.raises(Exception) as failure:
        c.reserve()
    assert failure.value.sqlstate == '40001'
    assert len(calls) == 3
    assert c.count('commands') == c.count('intents') == 0


def test_authorization_denial_writes_nothing(reservation_case):
    c = reservation_case
    def load(tx, intent, book):
        inputs = c.inputs(tx, intent, book)
        return replace(inputs, authorization=replace(inputs.authorization, allowed=False))
    c.service.load_inputs = load
    assert not c.reserve().accepted
    assert c.count('commands') == c.count('reservations') == 0


def test_may_have_sent_cannot_be_released_by_asserted_terminal(reservation_case):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    with c.ledger.uow.transaction(c.scope) as tx:
        tx.commands.mark_may_have_sent(tx, c.command.command_id, 'DURABLE_READY')
    e = c.evidence(r, reflected='0', terminal=True)
    p = c.service.reconcile(e)
    assert p.held == p.release_pending == 70
    assert c.count('risk_locks') == c.count('reconciliation_cases') == 1


def test_partial_reflection_keeps_remainder(reservation_case):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state('VENUE_CONFIRMED')
    p = c.service.reconcile(c.evidence(r, reflected='20', available='80'))
    assert p.held - p.reflected == 50
    assert p.release_pending == 0


def test_cash_sell_cannot_use_quote_balance_as_inventory(ip07_database):
    c = ReservationCase(ip07_database, sell=True)
    c.save_book(replace(c.case.book, sequence=2, balances=(money('100000'), money('1', 'ASSET'))))
    assert not c.reserve(2).accepted
    assert c.count('commands') == 0


@pytest.mark.parametrize('cross_product', [False, True])
def test_separate_processes_share_collateral_lock(reservation_case, ip07_database, cross_product):
    import multiprocessing
    a = reservation_case
    b = ReservationCase(ip07_database, group=a.group,
        product=t.Product.USD_M_PERP if cross_product else t.Product.SPOT,
        account=a.scope.venue_account_id if cross_product else None)
    if cross_product:
        b.next_intent('1.4')  # 1.4 * 100 / 2 = 70 in the same USDT pool
    ctx = multiprocessing.get_context('spawn')
    barrier, output = ctx.Barrier(2), ctx.Queue()
    workers = [ctx.Process(target=reserve_worker, args=(ip07_database, c.ledger.suffix,
        c.case, c.command, c.group, barrier, output)) for c in (a, b)]
    try:
        for worker in workers:
            worker.start()
        results = [output.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(timeout=20)
        assert all(worker.exitcode == 0 for worker in workers)
        assert sorted(result[0] for result in results) == [False, True]
        assert next(result for result in results if not result[0])[1] == 1
        with a.ledger.uow.transaction(a.scope) as tx:
            assert sum(p.held for p in ReservationRepository(tx).group()) == 70
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        output.close()


def test_snapshot_unknown_hold_is_not_dropped(reservation_case):
    c = reservation_case
    r = replace(c.ledger.reservation, reservation_id='external', intent_id='external-intent',
        owner_strategy_id=c.owner, amount=Decimal('40'), expires_at='2026-10-01T00:00:00Z')
    p = ReservationRepository.initial(r)
    c.save_book(replace(c.case.book, sequence=2, reservations=(p,)))
    result = c.reserve(2)
    assert not result.accepted
    assert len(c.calls[-1].reservations) == 1
    assert c.calls[-1].reservations[0].held == 40
    assert c.count('commands') == 0


def test_local_hold_is_not_counted_twice_from_snapshot(reservation_case, ip07_database):
    c = reservation_case
    result = c.reserve()
    c.save_book(replace(c.case.book, sequence=2, reservations=result.reservations))
    c.next_intent()
    assert not c.reserve(2).accepted  # same target scope retains its quantity
    assert len(c.calls[-1].reservations) == 1
    independent = ReservationCase(ip07_database, group=c.group, available='100')
    independent.next_intent('0.2')
    assert independent.reserve().accepted
    assert len(independent.calls[-1].reservations) == 1


def test_ready_command_cannot_lose_backing_reservation(reservation_case):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    for certainty in ('CONFIRMED', 'NOT_SENT'):
        e = c.evidence(r, reflected='0', certainty=certainty, terminal=True)
        assert c.service.reconcile(e).held == 70
    e = c.evidence(r, reflected='70')
    assert c.service.reconcile(e).reflected == 0
    with c.ledger.uow.transaction(c.scope) as tx:
        assert tx.commands.get(c.command.command_id).state == 'DURABLE_READY'


@pytest.mark.parametrize('state,certainty', [
    ('ABORTED_UNSENT', 'NOT_SENT'), ('NOT_SENT', 'NOT_SENT'),
    ('VENUE_REJECTED', 'CONFIRMED'), ('VENUE_CONFIRMED', 'CONFIRMED')])
def test_proven_terminal_release_can_follow_new_balance(reservation_case, state, certainty):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state(state)
    first = c.evidence(r, reflected='0', terminal=True, certainty=certainty)
    p = c.service.reconcile(first)
    assert p.held == 0
    later = c.evidence(r, reflected='0', terminal=True, certainty=certainty)
    assert for_balance(p, c.case.book).held == 70
    updated = c.service.reconcile(later)
    assert updated.held == 0 and updated.sequence == p.sequence + 1
    assert for_balance(updated, c.case.book).held == 0


def test_released_unsent_reservation_cannot_mint_base_permit_or_sql_send(reservation_case):
    import psycopg
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state('ABORTED_UNSENT')
    proof = c.evidence(r, reflected='0', terminal=True, certainty='NOT_SENT')
    assert c.service.reconcile(proof).held == 0
    with pytest.raises(SequenceConflict, match='not eligible'):
        with c.ledger.uow.transaction(c.scope) as tx:
            assert tx.commands.get(c.command.command_id).state == 'ABORTED_UNSENT'
            tx.commands.mark_may_have_sent(tx, c.command.command_id, 'DURABLE_READY')
    with pytest.raises(psycopg.errors.CheckViolation, match='aborted command'):
        with c.ledger.uow.transaction(c.scope) as tx:
            tx.execute('INSERT INTO ledger.commands '
                '(scope_id,record_id,sequence,payload,transaction_id) VALUES (%s,%s,2,%s::jsonb,%s)',
                (scope_id(c.scope), c.command.command_id,
                 encode(replace(c.command, state='MAY_HAVE_SENT', durable_record_ref='postgres:forged')),
                 tx.transaction_id))
    assert c.projection(r.reservation_id).held == 0
    with c.ledger.uow.transaction(c.scope) as tx:
        assert tx.commands.get(c.command.command_id).state == 'ABORTED_UNSENT'


@pytest.mark.parametrize('first', ['abort', 'send'])
@pytest.mark.parametrize('send_port', ['base', 'raw_sql'])
def test_abort_and_send_compete_under_one_collateral_mutex(reservation_case, first, send_port):
    import psycopg
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    first_locked, second_attempting = Event(), Event()
    def run(action, initial):
        if not initial:
            assert first_locked.wait(10)
            second_attempting.set()
        try:
            # The second raw SQL sender deliberately bypasses the UoW mutex;
            # its INSERT trigger must acquire the same mutex independently.
            if action == 'send' and send_port == 'raw_sql' and not initial:
                with c.ledger.connect_owner() as conn:
                    conn.execute('INSERT INTO ledger.commands '
                        '(scope_id,record_id,sequence,payload,transaction_id) VALUES (%s,%s,2,%s::jsonb,%s)',
                        (scope_id(c.scope), c.command.command_id,
                         encode(replace(c.command, state='MAY_HAVE_SENT', durable_record_ref='postgres:raw')),
                         'raw-send-race'))
                return 'send'
            with c.ledger.uow.transaction(c.scope) as tx:
                if initial:
                    first_locked.set()
                    assert second_attempting.wait(10)
                if action == 'abort':
                    CommandRepository(tx).abort_unsent(c.command.command_id, 'SYNTHETIC_ABORT')
                elif send_port == 'base':
                    tx.commands.mark_may_have_sent(tx, c.command.command_id, 'DURABLE_READY')
                else:
                    tx.execute('INSERT INTO ledger.commands '
                        '(scope_id,record_id,sequence,payload,transaction_id) VALUES (%s,%s,2,%s::jsonb,%s)',
                        (scope_id(c.scope), c.command.command_id,
                         encode(replace(c.command, state='MAY_HAVE_SENT', durable_record_ref='postgres:raw')),
                         tx.transaction_id))
            return action
        except (SequenceConflict, psycopg.errors.CheckViolation):
            return 'DENIED'
    with ThreadPoolExecutor(2) as pool:
        initial = pool.submit(run, first, True)
        following = pool.submit(run, 'send' if first == 'abort' else 'abort', False)
        assert (initial.result(timeout=20), following.result(timeout=20)) == (first, 'DENIED')
    evidence = c.evidence(r, reflected='0', terminal=True, certainty='NOT_SENT')
    assert c.service.reconcile(evidence).held == (0 if first == 'abort' else 70)
    with pytest.raises(SequenceConflict, match='not eligible'):
        with c.ledger.uow.transaction(c.scope) as tx:
            tx.commands.mark_may_have_sent(tx, c.command.command_id, 'DURABLE_READY')


def test_audit_only_unsent_forgery_does_not_release_backing(reservation_case):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    with c.ledger.uow.transaction(c.scope) as tx:
        receipt = t.CommandReceipt(receipt_id='forged-abort', command_id=c.command.command_id,
            status=t.CommandState.ABORTED_UNSENT, reason='FORGED',
            existing_result_ref=c.command.durable_record_ref)
        tx._append('audit_events', OUTCOME_PREFIX + c.command.command_id,
            CommandOutcome(command_id=c.command.command_id, request_hash=command_hash(c.command),
                durable_record_ref=c.command.durable_record_ref, receipt=receipt,
                provenance='LOCAL_UNSENT_ABORT'), 0)
    evidence = c.evidence(r, reflected='0', terminal=True, certainty='NOT_SENT')
    with pytest.raises(MissingEvidence, match='authoritative abort'):
        c.service.reconcile(evidence)
    assert c.projection(r.reservation_id).held == 70


@pytest.mark.parametrize('isolation', ['REPEATABLE READ', 'SERIALIZABLE'])
@pytest.mark.parametrize('committed', ['abort', 'send'])
def test_stale_owner_sql_snapshot_cannot_cross_abort_send_boundary(reservation_case, isolation, committed):
    import psycopg
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    with c.ledger.connect_owner() as stale:
        # Establish a real snapshot before the other owner session commits.
        stale.execute('SET TRANSACTION ISOLATION LEVEL ' + isolation)
        assert stale.execute('SHOW transaction_isolation').fetchone()[0] == isolation.lower()
        assert stale.execute('SELECT current_user').fetchone()[0] == 'ledger_owner'
        assert stale.execute('SELECT payload->>\'state\' FROM ledger.commands '
            'WHERE scope_id=%s AND record_id=%s',
            (scope_id(c.scope), c.command.command_id)).fetchone()[0] == 'DURABLE_READY'
        if committed == 'abort':
            c.command_state('ABORTED_UNSENT')
            proof = c.evidence(r, reflected='0', terminal=True, certainty='NOT_SENT')
            assert c.service.reconcile(proof).held == 0
            with pytest.raises(psycopg.errors.CheckViolation, match='READ COMMITTED'):
                stale.execute('INSERT INTO ledger.commands '
                    '(scope_id,record_id,sequence,payload,transaction_id) VALUES (%s,%s,2,%s::jsonb,%s)',
                    (scope_id(c.scope), c.command.command_id,
                     encode(replace(c.command, state='MAY_HAVE_SENT', durable_record_ref='postgres:stale')),
                     'stale-snapshot-send'))
        else:
            with c.ledger.uow.transaction(c.scope) as tx:
                tx.commands.mark_may_have_sent(tx, c.command.command_id, 'DURABLE_READY')
            receipt = t.CommandReceipt(receipt_id='stale-abort', command_id=c.command.command_id,
                status=t.CommandState.ABORTED_UNSENT, reason='STALE_SNAPSHOT',
                existing_result_ref=c.command.durable_record_ref)
            outcome = CommandOutcome(command_id=c.command.command_id, request_hash=command_hash(c.command),
                durable_record_ref=c.command.durable_record_ref, receipt=receipt, provenance='LOCAL_UNSENT_ABORT')
            with pytest.raises(psycopg.errors.CheckViolation, match='READ COMMITTED'):
                stale.execute('SELECT ledger.abort_unsent_command(%s,%s,%s::jsonb,%s,%s::jsonb,%s)',
                    (scope_id(c.scope), c.command.command_id, encode(c.command),
                     canonical_json(command_request(c.command)), encode(outcome), 'stale-snapshot-abort'))
        stale.rollback()
    with c.ledger.uow.transaction(c.scope) as tx:
        assert tx.execute('SHOW transaction_isolation').fetchone()[0] == 'read committed'
        assert tx.commands.get(c.command.command_id).state == (
            'ABORTED_UNSENT' if committed == 'abort' else 'MAY_HAVE_SENT')
        assert tx.execute('SELECT count(*) FROM ledger.command_aborts WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()[0] == (1 if committed == 'abort' else 0)
    assert c.projection(r.reservation_id).held == (0 if committed == 'abort' else 70)


@pytest.mark.parametrize('fault', ['hash', 'future', 'source', 'watermark', 'coverage'])
def test_unusable_freeze_evidence_keeps_hold(reservation_case, fault):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state('VENUE_CONFIRMED')
    e = c.evidence(r)
    ref = e.venue_freeze_ref
    if fault == 'hash':
        ref = replace(ref, hash='not-a-digest')
    elif fault == 'future':
        ref = replace(ref, visible_at='2027-01-01T00:00:00Z')
    elif fault == 'source':
        ref = replace(ref, source='')
    e = replace(e, venue_freeze_ref=ref, watermark='wrong' if fault == 'watermark' else ref.id + ':' + ref.hash)
    book = replace(c.case.book, sequence=c.case.book.sequence + 1, evidence=(ref,))
    if fault == 'coverage':
        book = replace(book, coverage=replace(book.coverage, pagination_complete=False))
    c.save_book(book)
    c.proof = replace(c.proof, evidence=e, book_sequence=book.sequence)
    p = c.service.reconcile(e)
    assert p.held == p.release_pending == 70 and p.reflected == 0


def test_retry_recalculates_changed_capacity(reservation_case):
    c = reservation_case
    def load(tx, intent, book):
        inputs = c.inputs(tx, intent, book)
        if len(c.calls) == 1:
            c.group_capacity = (money('69'), money('10', 'ASSET'))
            tx.execute("DO $$ BEGIN RAISE EXCEPTION 'injected retry' USING ERRCODE = '40001'; END $$")
        return inputs
    c.service.load_inputs = load
    assert not c.reserve().accepted
    assert len(c.calls) == 2
    assert c.count('commands') == 0


def test_retry_reads_new_book_and_requires_replan(reservation_case):
    c = reservation_case
    transactions = []
    original_uow = c.service.uow

    class AdvanceBookAfterRollback:
        @contextmanager
        def transaction(self, scope):
            try:
                with original_uow.transaction(scope) as tx:
                    transactions.append(tx.transaction_id)
                    yield tx
            except Exception as exc:
                if getattr(exc, 'sqlstate', None) == '40001' and len(transactions) == 1:
                    # Only after the real rollback releases the group mutex.
                    c.save_book(replace(c.case.book, sequence=2,
                        balances=(money('60'), money('10', 'ASSET'))))
                raise

    def load(tx, intent, book):
        inputs = c.inputs(tx, intent, book)
        if len(c.calls) == 1:
            tx.execute("DO $$ BEGIN RAISE EXCEPTION 'injected retry' USING ERRCODE = '40001'; END $$")
        return inputs

    c.service.uow = AdvanceBookAfterRollback()
    c.service.load_inputs = load
    result = c.reserve()
    assert not result.accepted and result.book_sequence == 2
    assert result.rejection_codes == ('REPLAN_REQUIRED:STALE_BOOK',)
    assert len(set(transactions)) == 2 and len(c.calls) == 1
    assert c.count('intents') == c.count('commands') == 0


def test_reconcile_conflict_reloads_proof(reservation_case):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state('VENUE_CONFIRMED')
    e = c.evidence(r)
    calls = []
    def proof(tx, evidence):
        calls.append(tx.transaction_id)
        if len(calls) == 1:
            tx.execute("DO $$ BEGIN RAISE EXCEPTION 'injected retry' USING ERRCODE = '40001'; END $$")
        return c.proof
    c.service.load_proof = proof
    assert c.service.reconcile(e).reflected == 70
    assert len(set(calls)) == 2
    assert c.projection(r.reservation_id).sequence == 1


def test_cash_sell_reserves_inventory_and_fee_separately(ip07_database):
    c = ReservationCase(ip07_database, sell=True)
    c.case.inputs = replace(c.case.inputs, fee_reserve=(money('3'),))
    result = c.reserve()
    assert result.accepted, result.rejection_codes
    assert {p.reservation.asset: p.held for p in result.reservations} == {
        'ASSET': Decimal('2'), 'USDT': Decimal('3')}


@pytest.mark.parametrize('fault', ['side', 'quantity', 'operation', 'price', 'scope'])
def test_unevaluated_command_is_rejected_atomically(reservation_case, fault):
    c = reservation_case
    if fault == 'scope':
        c.command = replace(c.command, venue_account_id='other')
    elif fault == 'operation':
        c.command = replace(c.command, operation='CLOSE')
    else:
        value = {'side': 'SELL', 'quantity': '0.8', 'price': '100'}[fault]
        c.command = replace(c.command, canonical_payload=replace(c.command.canonical_payload, **{fault: value}))
    with pytest.raises(ValueError):
        c.reserve()
    assert c.count('commands') == c.count('reservations') == c.count('intents') == 0


def test_stale_owner_and_expired_intent_write_nothing(reservation_case):
    c = reservation_case
    original = c.case.intent
    c.case.intent = replace(original, ownership_epoch=2)
    assert 'STALE_OWNER' in c.reserve().rejection_codes
    c.case.intent = replace(original, valid_until='2026-10-21T00:00:00Z')
    assert not c.reserve().accepted
    assert c.count('commands') == c.count('intents') == 0


def test_unknown_lock_blocks_further_reservation(reservation_case):
    c = reservation_case
    r = c.reserve().reservations[0].reservation
    c.command_state('UNKNOWN')
    c.service.reconcile(c.evidence(r, reflected='0', certainty='UNKNOWN', mapped=False))
    c.next_intent()
    assert 'ACTIVE_RISK_LOCK' in c.reserve().rejection_codes
