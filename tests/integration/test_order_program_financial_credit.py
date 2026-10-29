"""Linked financial credit requires independent authority even at unchanged held."""
from dataclasses import replace
from decimal import Decimal

import pytest

from integration.test_order_program_closure import (
    isolated_postgres, pre_upgrade, order_db, closure_db, base_scenario, scenario, publish_review,
)
from trading_system.contracts import types as t
from trading_system.risk.reservation_repository import ReservationRepository, balance_watermark, for_balance
from trading_system.storage.models import MissingEvidence, encode, scope_id
from integration.support_ip_03 import ROOT


def debit(projection, book):
    current=for_balance(projection,book)
    return max(current.held-current.reflected,current.release_pending)


def forged_reflection(s):
    with s.uow.transaction(s.scope) as tx:
        current=ReservationRepository(tx).get(s.reservation.reservation_id)
        book=tx.projections.book(s.scope)
        forged=replace(current,sequence=current.sequence+1,reflected=current.held,release_pending=Decimal(0),
            reservation=replace(current.reservation,state=t.ReservationState.VENUE_REFLECTED,
                reflected_amount=current.held,evidence_watermark=balance_watermark(book,book.evidence[0])))
    assert forged.held==current.held and debit(current,book)==current.held and debit(forged,book)==0
    return forged


def raw_owner_insert(s,projection,transaction_id='ordinary-forged-credit'):
    # Ordinary login authority, without retaining the test admin's SET ROLE rights.
    with s.connect_admin() as connection:
        connection.execute('SET SESSION AUTHORIZATION ledger_owner')
        connection.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
            'VALUES(%s,%s,%s,%s::jsonb,%s)',(scope_id(s.scope),'ip07:reservation:'+s.reservation.reservation_id,
            projection.sequence,encode(projection),transaction_id))


def history(s):
    with s.connect_admin() as connection:
        return {table:connection.execute('SELECT scope_id,record_id,sequence,payload,transaction_id '
            'FROM ledger.'+table+' WHERE scope_id=%s ORDER BY record_id,sequence',
            (scope_id(s.scope),)).fetchall() for table in ('commands','reservations','audit_events',
            'order_funding_links','order_program_bindings','order_program_states')}


def historical_guard():
    historical=(ROOT/'migrations/0009_order_program_closure.sql').read_text(encoding='utf-8')
    start=historical.index('CREATE FUNCTION ledger.guard_program_reservation_reduction()')
    end=historical.index('REVOKE ALL ON FUNCTION ledger.guard_program_reservation_reduction()',start)
    return historical[start:end].replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION',1)


@pytest.mark.parametrize('path',['repository','raw_sql'])
def test_same_held_forged_reflection_cannot_free_parent_capital(scenario,path):
    s=scenario;forged=forged_reflection(s)
    with pytest.raises(Exception,match='PROGRAM_RESERVATION|dedicated program closure'):
        if path=='repository':
            with s.uow.transaction(s.scope) as tx:ReservationRepository(tx).save(forged)
        else:raw_owner_insert(s,forged)
    with s.uow.transaction(s.scope) as tx:
        assert debit(ReservationRepository(tx).get(s.reservation.reservation_id),tx.projections.book(s.scope))==s.reservation.amount


@pytest.mark.parametrize('path',['repository','raw_sql'])
def test_released_hold_watermark_cannot_be_forged_for_new_book(scenario,path):
    s=scenario;publish_review(s)
    released=s.service.close(s.scope,s.program.program_id,s.targets)[0]
    with s.uow.transaction(s.scope) as tx:
        book=tx.projections.book(s.scope)
        newer=replace(book,sequence=book.sequence+1)
        tx.projections.save_book(newer,book.sequence)
    assert debit(released,newer)==s.reservation.amount
    forged=replace(released,sequence=released.sequence+1,reservation=replace(released.reservation,
        evidence_watermark=balance_watermark(newer,newer.evidence[0])))
    assert forged.held==released.held==0 and debit(forged,newer)==0
    with pytest.raises(Exception,match='PROGRAM_RESERVATION|dedicated program closure'):
        if path=='repository':
            with s.uow.transaction(s.scope) as tx:ReservationRepository(tx).save(forged)
        else:raw_owner_insert(s,forged)


def test_unlinked_reflection_behavior_remains_unchanged(scenario):
    from integration.support_ip_03 import LedgerScenario
    s=LedgerScenario(scenario.dsn)
    with s.uow.transaction(s.scope) as tx:
        s.write_bundle(tx)
        repo=ReservationRepository(tx);current=repo.get(s.reservation.reservation_id)
        reflected=replace(current,sequence=1,reflected=current.held,
            reservation=replace(current.reservation,state=t.ReservationState.VENUE_REFLECTED,reflected_amount=current.held))
        repo.save(reflected)
    with s.uow.transaction(s.scope) as tx:
        assert ReservationRepository(tx).get(s.reservation.reservation_id)==reflected


def test_additive_guard_upgrades_existing_linked_funding_without_rewriting_history(scenario):
    """Exercise real old guard -> additive replacement with existing native IDs."""
    import psycopg
    s=scenario
    forged=forged_reflection(s)
    upgrade=(ROOT/'migrations/0011_order_program_financial_credit_guard.sql').read_text(encoding='utf-8')
    before=history(s)
    try:
        # Only this test's fresh disposable database is changed. The old
        # same-held bypass is demonstrated and rolled back, never retained.
        with s.connect_admin() as connection:connection.execute(historical_guard())
        with s.connect_admin() as connection:
            connection.execute('SET SESSION AUTHORIZATION ledger_owner')
            connection.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
                'VALUES(%s,%s,%s,%s::jsonb,%s)',(scope_id(s.scope),'ip07:reservation:'+s.reservation.reservation_id,
                forged.sequence,encode(forged),'rolled-back-upgrade-probe'))
            connection.rollback()
        assert history(s)==before
        with s.connect_admin() as connection:connection.execute(upgrade)
        assert history(s)==before
        with pytest.raises(psycopg.errors.CheckViolation,match='dedicated program closure'):
            raw_owner_insert(s,forged)
        assert history(s)==before
    finally:
        with s.connect_admin() as connection:connection.execute(upgrade)


def test_upgrade_refuses_preexisting_unapproved_credit_without_rewriting_it(scenario):
    import psycopg
    s=scenario
    forged=forged_reflection(s)
    upgrade=(ROOT/'migrations/0011_order_program_financial_credit_guard.sql').read_text(encoding='utf-8')
    inserted=False
    try:
        with s.connect_admin() as connection:connection.execute(historical_guard())
        raw_owner_insert(s,forged)
        inserted=True
        # This bad old-version row is genuinely committed before migration.
        before=history(s)
        with pytest.raises(psycopg.errors.CheckViolation,match='unreviewed linked financial credit'):
            with s.connect_admin() as connection:connection.execute(upgrade)
        assert history(s)==before
    finally:
        if inserted:
            # Explicit conservative append is test cleanup, never an automatic
            # migration repair. Preserve the bad row and every original ID.
            conservative=replace(forged,sequence=forged.sequence+1,reflected=Decimal(0),
                release_pending=forged.held,reservation=replace(forged.reservation,
                    state=t.ReservationState.RELEASE_PENDING,reflected_amount=Decimal(0),
                    evidence_watermark='test-quarantine-uncredited'))
            raw_owner_insert(s,conservative,'explicit-test-quarantine-append')
        with s.connect_admin() as connection:connection.execute(upgrade)


def test_upgrade_accepts_archived_reviewed_credit_and_preserves_it(scenario):
    s=scenario
    publish_review(s)
    released=s.service.close(s.scope,s.program.program_id,s.targets)[0]
    assert released.held==0
    before=history(s)
    with s.connect_admin() as connection:
        connection.execute((ROOT/'migrations/0011_order_program_financial_credit_guard.sql').read_text(encoding='utf-8'))
    assert history(s)==before
