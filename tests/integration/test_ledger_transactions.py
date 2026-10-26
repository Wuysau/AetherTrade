"""These are database integration probes, not T01-T66/Q qualification evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier
import os
import subprocess
import sys

import pytest

from integration.support_ip_03 import (isolated_postgres, ledger_scenario, LedgerScenario,
                                      ROOT, STAMP)
from trading_system.contracts import types as t
from trading_system.storage.events import EventStore
from trading_system.storage.models import (scope_id, SequenceConflict, ScopeMismatch,
                                         MissingEvidence)


def test_atomic_commit_and_rollback(ledger_scenario):
    s = ledger_scenario
    result = s.fail_between_reservation_and_command()
    assert result.intent_count == result.reservation_count == result.command_count == 0
    with s.uow.transaction(s.scope) as tx:
        s.write_bundle(tx)
    assert tuple(vars(s.counts()).values()) == (1, 1, 1)
    with s.connect_admin() as conn:
        assert conn.execute('SELECT amount FROM ledger.reservation_assets WHERE reservation_id=%s',
                            (s.reservation.reservation_id,)).fetchone()[0] == Decimal('9007199254740993.00000001')
        assert conn.execute('SELECT count(*) FROM ledger.outbox WHERE scope_id=%s',
                            (scope_id(s.scope),)).fetchone()[0] == 1


@pytest.mark.parametrize('failure', ['intent','reservation','command'])
def test_each_write_failure_rolls_back_everything(ledger_scenario, failure):
    s = ledger_scenario
    with pytest.raises((SequenceConflict, ScopeMismatch, ValueError)):
        with s.uow.transaction(s.scope) as tx:
            tx.intents.append(s.intent, 1 if failure == 'intent' else 0)
            reservation = replace(s.reservation, collateral_group_id='wrong') if failure == 'reservation' else s.reservation
            tx.reservations.append(reservation, 0)
            command = replace(s.command, payload_hash='0'*64) if failure == 'command' else s.command
            tx.commands.append(command, 0)
    assert tuple(vars(s.counts()).values()) == (0, 0, 0)
    with s.connect_admin() as conn:
        for table in ('outbox','aggregate_heads','transaction_observations'):
            assert conn.execute(f'SELECT count(*) FROM ledger.{table} WHERE scope_id=%s',
                                (scope_id(s.scope),)).fetchone()[0] == 0


def test_caught_conflict_is_rollback_only(ledger_scenario):
    s = ledger_scenario
    with pytest.raises(RuntimeError, match='rollback-only'):
        with s.uow.transaction(s.scope) as tx:
            tx.intents.append(s.intent)
            with pytest.raises(SequenceConflict):
                tx.intents.append(s.intent, 0)
    assert s.counts().intent_count == 0


def test_aggregate_sequence(ledger_scenario):
    s = ledger_scenario
    barrier = Barrier(2)
    def append():
        barrier.wait(timeout=10)
        try:
            with s.uow.transaction(s.scope) as tx:
                EventStore().append(tx, s.event(), 0)
            return 'committed'
        except SequenceConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: append(), range(2)))
    assert sorted(results) == ['committed','conflict']
    with s.uow.transaction(s.scope) as tx:
        EventStore().append(tx, s.event(revision=2), 1)
        EventStore().append(tx, s.event(aggregate='other'), 0)
    with s.connect_admin() as conn:
        rows = conn.execute('SELECT aggregate_id,sequence FROM ledger.domain_events WHERE scope_id=%s '
                            'ORDER BY aggregate_id,sequence', (scope_id(s.scope),)).fetchall()
    assert rows == [('aggregate',1),('aggregate',2),('other',1)]


def test_different_collateral_groups_do_not_share_global_lock(ledger_scenario, isolated_postgres):
    first = ledger_scenario
    second = LedgerScenario(isolated_postgres)
    with first.uow.transaction(first.scope):
        def independent():
            with second.uow.transaction(second.scope) as tx:
                tx.execute("SET LOCAL lock_timeout='2s'")
                EventStore().append(tx, second.event(), 0)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(independent).result(timeout=10)


@pytest.mark.parametrize('role,allowed', [
    ('ledger_decision','proposals'), ('ledger_control','control_commands'), ('ledger_owner','audit_events')])
def test_database_roles(ledger_scenario, role, allowed):
    import psycopg
    s = ledger_scenario
    with s.connect_admin() as conn:
        conn.execute('SET ROLE ' + role)
        conn.execute(f'INSERT INTO ledger.{allowed}(scope_id,record_id,sequence,payload,transaction_id) '
                     "VALUES (%s,%s,1,'{}',%s)", (scope_id(s.scope), s.suffix, s.suffix))
    forbidden = ['fills','commands','domain_events','positions','reservations'] if role != 'ledger_owner' else ['authorizations','policies','account_bindings']
    for table in forbidden:
        with s.connect_admin() as conn:
            conn.execute('SET ROLE ' + role)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f'DELETE FROM ledger.{table}')
            conn.rollback()
            conn.execute('SET ROLE ' + role)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f'INSERT INTO ledger.{table} DEFAULT VALUES')
            conn.rollback()
    with s.connect_admin() as conn:
        conn.execute('SET ROLE ' + role)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('CREATE TABLE ledger.forbidden(id integer)')
        conn.rollback()


def test_append_correction(ledger_scenario):
    import psycopg
    s = ledger_scenario
    original = s.fill()
    correction = replace(s.fill(correction_of=original.fill_id),
                         quantity=t.Quantity(value=Decimal('-0.001'), unit='BTC'))
    with s.uow.transaction(s.scope) as tx:
        EventStore(STAMP).append(tx, s.event(fill=original), 0)
        EventStore(STAMP).append(tx, s.event(revision=2, fill=correction), 1)
    with s.connect_admin() as conn:
        rows = conn.execute('SELECT record_id,quantity,correction_of FROM ledger.fills WHERE scope_id=%s '
                            'ORDER BY correction_of NULLS FIRST', (scope_id(s.scope),)).fetchall()
        assert rows == [(original.fill_id,Decimal('0.001'),None),
                        (correction.fill_id,Decimal('-0.001'),original.fill_id)]
    for statement in ('UPDATE ledger.fills SET quantity=0 WHERE record_id=%s',
                      'DELETE FROM ledger.fills WHERE record_id=%s'):
        with s.connect_admin() as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(statement, (original.fill_id,))
            conn.rollback()
    with s.connect_admin() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute('TRUNCATE ledger.fills CASCADE')
        conn.rollback()


def test_duplicate_fill_rolls_back_event_and_outbox(ledger_scenario):
    import psycopg
    s = ledger_scenario
    original = s.fill()
    with s.uow.transaction(s.scope) as tx:
        EventStore().append(tx, s.event(fill=original), 0)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with s.uow.transaction(s.scope) as tx:
            EventStore().append(tx, s.event(revision=2, fill=replace(original, fill_id='duplicate')), 1)
    with s.connect_admin() as conn:
        for table in ('fills','domain_events','outbox'):
            assert conn.execute(f'SELECT count(*) FROM ledger.{table} WHERE scope_id=%s',
                                (scope_id(s.scope),)).fetchone()[0] == 1


def test_scope_and_missing_book_fail_closed(ledger_scenario):
    s = ledger_scenario
    with s.uow.transaction(s.scope) as tx:
        with pytest.raises(MissingEvidence):
            tx.projections.book(s.scope)
    with pytest.raises(ScopeMismatch):
        with s.uow.transaction(s.scope) as tx:
            EventStore().append(tx, replace(s.event(), scope=replace(s.scope, venue_leg='LONG')), 0)
    with pytest.raises(ValueError, match='LIVE'):
        with s.uow.transaction(replace(s.scope, environment=t.Environment.LIVE)):
            pytest.fail('LIVE must be rejected before connecting')


def test_reservation_unique_and_no_ttl_release(ledger_scenario):
    import psycopg
    s = ledger_scenario
    with s.uow.transaction(s.scope) as tx:
        s.write_bundle(tx)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with s.uow.transaction(s.scope) as tx:
            tx.reservations.append(replace(s.reservation,reservation_id='duplicate-' + s.suffix),0)
    with s.uow.transaction(s.scope) as tx:
        assert tx.reservations.for_intent(s.intent.intent_id) == (s.reservation,)
    with pytest.raises(ValueError, match='evidence-backed'):
        with s.uow.transaction(s.scope) as tx:
            tx.reservations.append(replace(s.reservation,state=t.ReservationState.RELEASED),1)


def test_risk_lock_cannot_be_silently_resolved(ledger_scenario):
    s = ledger_scenario
    lock = s.event().payload
    with s.uow.transaction(s.scope) as tx:
        tx.risk_locks.append(lock,0)
    with pytest.raises(ValueError, match='resolution'):
        with s.uow.transaction(s.scope) as tx:
            tx.risk_locks.append(replace(lock,state='RESOLVED'),1)
    with s.uow.transaction(s.scope) as tx:
        assert tx.risk_locks.active(s.scope) == (lock,)


def test_durable_marker_precedes_receipt_and_closes_transaction(ledger_scenario):
    s = ledger_scenario
    with s.uow.transaction(s.scope) as tx:
        s.write_bundle(tx)
        permit = tx.commands.mark_may_have_sent(tx,s.command.command_id,'DURABLE_READY')
        assert tx.committed and not tx.active
        with pytest.raises(RuntimeError):
            tx.commands.get(s.command.command_id)
        with s.connect_admin() as conn:
            assert conn.execute('SELECT phase FROM ledger.transaction_observations WHERE transaction_id=%s',
                                (tx.transaction_id,)).fetchone() == ('COMMIT_MARKER',)
        assert permit.durable_record_ref == 'postgres:' + tx.transaction_id
    with pytest.raises(SequenceConflict):
        with s.uow.transaction(s.scope) as tx:
            tx.commands.mark_may_have_sent(tx,s.command.command_id,'DURABLE_READY')


@pytest.mark.parametrize('phase,exit_code,count', [('before',73,0),('after',74,1)])
def test_process_crash_transaction_observation(ledger_scenario, phase, exit_code, count):
    s = ledger_scenario
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src')
    result = subprocess.run([sys.executable,str(ROOT / 'tests/integration/support_ip_03.py'),s.suffix,phase],
                            env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode == exit_code, result.stderr
    assert 'BEFORE_COMMIT:' in result.stdout
    assert ('AFTER_COMMIT:' in result.stdout) == (phase == 'after')
    assert tuple(vars(s.counts()).values()) == (count,count,count)
    with s.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.transaction_observations WHERE scope_id=%s',
                            (scope_id(s.scope),)).fetchone()[0] == count


def test_collateral_group_serializes_different_instruments(ledger_scenario):
    import psycopg
    from trading_system.storage.uow import UnitOfWork
    s = ledger_scenario
    other_scope = replace(s.scope, instrument_uid='another-instrument')

    def limited_connection():
        conn = s.connect_owner()
        conn.execute("SET lock_timeout='500ms'")
        conn.commit()
        return conn

    def competing_transaction():
        with UnitOfWork(limited_connection).transaction(other_scope):
            pytest.fail('same collateral group must remain locked')

    with s.uow.transaction(s.scope):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                pool.submit(competing_transaction).result(timeout=10)


def test_intent_cannot_be_revised_in_place(ledger_scenario):
    s = ledger_scenario
    with s.uow.transaction(s.scope) as tx:
        tx.intents.append(s.intent)
    with pytest.raises(SequenceConflict, match='immutable'):
        with s.uow.transaction(s.scope) as tx:
            tx.intents.append(replace(s.intent, target_quantity='2'), 1)
    assert s.counts().intent_count == 1


def test_reservation_owner_mismatch_rolls_back(ledger_scenario):
    s = ledger_scenario
    with pytest.raises(ScopeMismatch):
        with s.uow.transaction(s.scope) as tx:
            tx.intents.append(s.intent)
            tx.reservations.append(replace(s.reservation, owner_strategy_id='other-owner'), 0)
    assert s.counts().intent_count == 0


@pytest.mark.parametrize('field,value', [('ownership_epoch', 2), ('policy_revision', 2)])
def test_stale_command_rejected_atomically(ledger_scenario, field, value):
    s = ledger_scenario
    with pytest.raises(SequenceConflict):
        with s.uow.transaction(s.scope) as tx:
            tx.intents.append(s.intent)
            tx.commands.append(replace(s.command, **{field: value}), 0)
    assert s.counts().intent_count == s.counts().command_count == 0


def test_current_owner_identity_checked_before_permit(ledger_scenario):
    s = ledger_scenario
    with pytest.raises(SequenceConflict, match='owner/policy'):
        with s.uow.transaction(s.scope) as tx:
            tx.intents.append(replace(s.intent, owner_strategy_id='another-strategy'))
            tx.commands.append(s.command, 0)
            tx.commands.mark_may_have_sent(tx, s.command.command_id, 'DURABLE_READY')
    assert s.counts().intent_count == s.counts().command_count == 0


def test_event_payload_scope_and_evidence_cannot_cross_environment(ledger_scenario):
    s = ledger_scenario
    event = s.event()
    with pytest.raises(ScopeMismatch):
        with s.uow.transaction(s.scope) as tx:
            EventStore().append(tx, replace(event, payload=replace(
                event.payload, scope=replace(s.scope, venue_leg='LONG'))), 0)
    foreign_ref = replace(s.fill().evidence, environment=t.Environment.LIVE)
    with pytest.raises(ScopeMismatch):
        with s.uow.transaction(s.scope) as tx:
            EventStore().append(tx, replace(event, evidence=(foreign_ref,)), 0)


def test_event_timestamps_remain_distinct(ledger_scenario):
    s = ledger_scenario
    received = '2026-10-20T06:39:04Z'
    with s.uow.transaction(s.scope) as tx:
        event = s.event()
        EventStore(received).append(tx, event, 0)
    with s.connect_admin() as conn:
        occurred, receipt, processed = conn.execute(
            'SELECT occurred_at,received_at,processed_at FROM ledger.domain_events WHERE event_id=%s',
            (event.event_id,)).fetchone()
    assert occurred < receipt < processed


def test_correction_cannot_change_currency(ledger_scenario):
    import psycopg
    s = ledger_scenario
    original = s.fill()
    with s.uow.transaction(s.scope) as tx:
        EventStore().append(tx, s.event(fill=original), 0)
    correction = replace(s.fill(correction_of=original.fill_id),
                         price=t.Price(value=Decimal('50000'), currency='USD'))
    with pytest.raises(psycopg.errors.CheckViolation):
        with s.uow.transaction(s.scope) as tx:
            EventStore().append(tx, s.event(revision=2, fill=correction), 1)
    with s.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.domain_events WHERE scope_id=%s',
                            (scope_id(s.scope),)).fetchone()[0] == 1


def test_book_round_trip_preserves_numeric_units_and_sequence(ledger_scenario):
    s = ledger_scenario
    book = t.BookSnapshot(scope=s.scope, positions=(), active_orders=(), reservations=(),
        balances=(t.Money(amount=Decimal('9007199254740993.00000001'), currency='USDT'),),
        sequence=1, basis_fingerprint='a'*64,
        coverage=t.Coverage(scope=s.scope, query_succeeded=True, pagination_complete=True,
                            supported=True, missing=()), evidence=(s.fill().evidence,))
    with s.uow.transaction(s.scope) as tx:
        tx.projections.save_book(book, 0)
    with s.uow.transaction(s.scope) as tx:
        assert tx.projections.book(s.scope) == book
    with pytest.raises(SequenceConflict):
        with s.uow.transaction(s.scope) as tx:
            tx.projections.save_book(book, 0)


def test_durable_transaction_overrides_async_commit(ledger_scenario):
    from trading_system.storage.uow import UnitOfWork
    s = ledger_scenario

    def async_connection():
        conn = s.connect_owner()
        conn.execute('SET synchronous_commit = off')
        conn.commit()
        return conn

    with UnitOfWork(async_connection).transaction(s.scope) as tx:
        assert tx.execute('SHOW synchronous_commit').fetchone() == ('on',)


def test_database_disconnect_cannot_return_durable_permit(ledger_scenario):
    import psycopg
    s = ledger_scenario
    permit = None
    with pytest.raises(psycopg.OperationalError):
        with s.uow.transaction(s.scope) as tx:
            s.write_bundle(tx)
            pid = tx.execute('SELECT pg_backend_pid()').fetchone()[0]
            with s.connect_admin() as admin:
                assert admin.execute('SELECT pg_terminate_backend(%s)', (pid,)).fetchone() == (True,)
            permit = tx.commands.mark_may_have_sent(tx, s.command.command_id, 'DURABLE_READY')
    assert permit is None
    assert tuple(vars(s.counts()).values()) == (0, 0, 0)
