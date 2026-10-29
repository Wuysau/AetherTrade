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


def raw_owner_insert(s,projection):
    # Ordinary login authority, without retaining the test admin's SET ROLE rights.
    with s.connect_admin() as connection:
        connection.execute('SET SESSION AUTHORIZATION ledger_owner')
        connection.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
            'VALUES(%s,%s,%s,%s::jsonb,%s)',(scope_id(s.scope),'ip07:reservation:'+s.reservation.reservation_id,
            projection.sequence,encode(projection),'ordinary-forged-credit'))


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
