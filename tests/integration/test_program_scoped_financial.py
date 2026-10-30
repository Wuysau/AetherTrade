"""Actual group cash remains full while physical scope owns quantity mapping."""
from dataclasses import fields,replace
from decimal import Decimal as D
from uuid import uuid4

import pytest

from integration.test_program_submission_observation import (
    isolated_postgres,command_database,source_database,database,venue,observer,
)
from integration.test_program_financial_admission import real_case
from integration.test_program_commands_postgres import attach
from integration.test_paper_market_matcher import case,submit
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.intents.program_target_allocation import ProgramTargetFacts,ProgramScopedTargetFactsV2
from trading_system.risk.program_quantity_scope import (
    current_program_quantity_reservation_ids,validate_current_program_quantity_scope,
)
from trading_system.storage.models import MissingEvidence,scope_id


def paired(database):
    a=real_case(database,scoped_quantities=True,instrument_uid='quantity-a-'+uuid4().hex)
    a.write,a.request,a.bundle=a.service._transaction(lambda tx:attach(a,tx))
    b=real_case(database,scoped_quantities=True,venue_account_id=a.scope.venue_account_id,
        collateral_group_id=a.c.terms.collateral_group_id,instrument_uid='quantity-b-'+uuid4().hex)
    return a,b


def facts(c,tx,book,ids):
    original=c.c.facts.target_facts
    values={f.name:getattr(original,f.name) for f in fields(ProgramTargetFacts)}
    values.update(transaction_id=tx.transaction_id,book_sequence=book.sequence,book_hash=canonical_hash(domain_payload(book)))
    return ProgramScopedTargetFactsV2(**values,
        contract_version='2026-10-30',record_kind='PROGRAM_SCOPED_TARGET_FACTS_V2',
        quantity_scope=tx.scope,quantity_reservation_ids=ids)


def test_actual_other_instrument_cash_hold_is_not_local_quantity_and_keeps_its_full_projection(database):
    a,b=paired(database)
    with b.service.uow.transaction(b.scope) as tx:
        raw=tx.projections.book(b.scope);prepared=b.service._prepare_collateral(tx,raw)
        assert not prepared.rejection_codes and prepared.book.reservations==a.bundle.reservations
        assert current_program_quantity_reservation_ids(tx,raw,prepared.book)==()
        validate_current_program_quantity_scope(tx,raw,prepared.book,facts(b,tx,prepared.book,()))
        assert sum((p.held for p in prepared.book.reservations),D(0))>0 and not raw.reservations


@pytest.mark.parametrize('fault',['foreign_id','omitted_local','wrong_scope','changed_financial_projection'])
def test_claimed_subset_and_financial_projection_never_replace_physical_evidence(database,fault):
    a,b=paired(database);c=a if fault=='omitted_local' else b
    with c.service.uow.transaction(c.scope) as tx:
        raw=tx.projections.book(c.scope);financial=c.service._prepare_collateral(tx,raw).book
        ids=current_program_quantity_reservation_ids(tx,raw,financial)
        supplied=facts(c,tx,financial,ids)
        if fault=='foreign_id':supplied=replace(supplied,quantity_reservation_ids=(a.bundle.reservations[0].reservation.reservation_id,))
        elif fault=='omitted_local':
            assert ids
            supplied=replace(supplied,quantity_reservation_ids=())
        elif fault=='wrong_scope':supplied=replace(supplied,quantity_scope=a.scope)
        else:
            p=financial.reservations[0]
            financial=replace(financial,reservations=(replace(p,held=p.held-D(1)),))
        with pytest.raises(MissingEvidence,match='PROGRAM_CURRENT_(QUANTITY_RESERVATION_MAP|FINANCIAL_HOLDS)_CHANGED'):
            validate_current_program_quantity_scope(tx,raw,financial,supplied)


@pytest.mark.parametrize('field,value',[('asset','FAKE'),('amount',D('900000')),('venue_account_id','foreign-account'),
    ('intent_id','foreign-intent'),('owner_strategy_id','foreign-owner')])
def test_same_id_latest_projection_cannot_rewrite_immutable_creation_fields(database,field,value):
    import psycopg
    from psycopg.types.json import Jsonb
    a,b=paired(database);p=a.bundle.reservations[0]
    poisoned=replace(p,sequence=1,reservation=replace(p.reservation,**{field:value}))
    with psycopg.connect(database) as db:
        before=db.execute('SELECT payload,xmin::text FROM ledger.reservations WHERE scope_id=%s AND record_id=%s',
            (scope_id(a.scope),p.reservation.reservation_id)).fetchone()
        # Explicit isolated admin corruption, not an ordinary writer capability.
        # The live linked-reservation trigger rejects this normal-owner write.
        db.execute('SET LOCAL session_replication_role=replica')
        db.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,%s,1,%s,%s)',
            (scope_id(a.scope),'ip07:reservation:'+p.reservation.reservation_id,Jsonb(domain_payload(poisoned)),uuid4().hex))
        assert db.execute('SELECT payload,xmin::text FROM ledger.reservations WHERE scope_id=%s AND record_id=%s',
            (scope_id(a.scope),p.reservation.reservation_id)).fetchone()==before
    with b.service.uow.transaction(b.scope) as tx:
        raw=tx.projections.book(b.scope);financial=replace(raw,reservations=(poisoned,))
        with pytest.raises(MissingEvidence,match='PROGRAM_RESERVATION_PROJECTION_CHANGED'):
            validate_current_program_quantity_scope(tx,raw,financial,facts(b,tx,financial,()))


def test_real_two_accepted_unfilled_commands_count_cash_once_and_have_zero_cross_scope_quantity(database,venue,tmp_path):
    a=case(database,venue,tmp_path,scoped_quantities=True)
    submit(a);first,codec=observer(a,tmp_path/'a-source')
    try:assert first.observe(a.write.command_id).state=='ACCEPTED'
    finally:codec.close()
    initial=real_case(database,scoped_quantities=True,venue_account_id=a.scope.venue_account_id,
        collateral_group_id=a.c.terms.collateral_group_id,instrument_uid='accepted-b-'+uuid4().hex,
        financial_capacity=(t.Money(currency='USDT',amount=D('162980')),))
    b=case(database,venue,tmp_path,prepared_case=initial);submit(b)
    second,codec=observer(b,tmp_path/'b-source')
    try:assert second.observe(b.write.command_id).state=='ACCEPTED'
    finally:codec.close()
    assert not a.engine.query_creation(a.write.command_id).fills and not b.engine.query_creation(b.write.command_id).fills
    assert b.c.allocation.delta.working.value==0
    with b.service.uow.transaction(b.scope) as tx:
        context=tx.execute('SELECT context FROM ledger.program_permit_consumptions WHERE scope_id=%s AND command_id=%s',
            (scope_id(b.scope),b.write.command_id)).fetchone()[0]
        assert context['source_book_hash']==canonical_hash(domain_payload(tx.projections.book(b.scope)))
        assert context['financial_book_hash']!=context['source_book_hash']
        held=context['assessment']['book']['reservations']
        assert len(held)==1 and held[0]['reservation']['reservation_id']==a.bundle.reservations[0].reservation.reservation_id
        assert context['assessment']['target_facts']['quantity_reservation_ids']==[]
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE account_id=%s',(b.scope.venue_account_id,)).fetchone()==(0,)


def released_legacy_collateral(database):
    """Actual unsent abort + reconciled legacy release, then a newer raw book."""
    from integration.support_ip_07 import ReservationCase
    from trading_system.risk.reservations import ReleasedBalanceAttestation
    from trading_system.risk.reservation_repository import for_balance
    c=real_case(database,scoped_quantities=True,
        financial_capacity=(t.Money(currency='USDT',amount=D('81490')),))
    old=ReservationCase(database,group=c.c.terms.collateral_group_id,available='1000')
    outcome=old.reserve()
    assert outcome.accepted
    reservation=outcome.reservations[0].reservation
    old.command_state('ABORTED_UNSENT')
    evidence=old.evidence(reservation,reflected='0',certainty='NOT_SENT',terminal=True,available='1000')
    released=old.service.reconcile(evidence)
    assert released.held==0 and released.reservation.state==t.ReservationState.RELEASED
    with c.owner.transaction(c.scope) as tx:
        raw=tx.projections.book(c.scope)
        # This newer balance cannot inherit the older exact watermark itself.
        assert for_balance(released,raw).held==reservation.amount
    def current_release(tx,book,originals):
        assert released in originals
        return ReleasedBalanceAttestation(transaction_id=tx.transaction_id,
            book_hash=canonical_hash(domain_payload(book)),reservation_ids=(reservation.reservation_id,),
            evidence=book.evidence)
    c.service.load_released_balances=current_release
    return c,old,released,current_release


def test_authenticated_newer_balance_reattestation_survives_scoped_admission_and_every_send_boundary(database):
    from integration.test_program_commands_postgres import boundary,lease
    c,old,released,reader=released_legacy_collateral(database)
    with c.owner.transaction(c.scope) as tx:
        before=tx.execute('SELECT payload,xmin::text FROM ledger.reservations WHERE scope_id=%s AND record_id=%s',
            (scope_id(old.scope),released.reservation.reservation_id)).fetchone()
        prepared=c.service._prepare_collateral(tx,tx.projections.book(c.scope))
        assert not prepared.rejection_codes and prepared.holds==(released,)
    write,request,bundle=c.service._transaction(lambda tx:attach(c,tx))
    assert bundle.archive.book.reservations==(released,)
    assert bundle.archive.target_facts.quantity_reservation_ids==()
    gate=boundary(c);gate.readers['load_released_balances']=reader
    permit=gate.mark_may_have_sent(write.command_id,lease(c))
    assert gate.consume(permit,lease(c))==request
    assert gate.recheck_consumed(permit,lease(c)).request==request
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT payload,xmin::text FROM ledger.reservations WHERE scope_id=%s AND record_id=%s',
            (scope_id(old.scope),released.reservation.reservation_id)).fetchone()==before


@pytest.mark.parametrize('phase',['admission','mark','consume','afterwait'])
def test_released_zero_hold_without_current_reattestation_never_grants_scoped_credit(database,phase):
    from integration.test_program_commands_postgres import boundary,lease
    c,old,released,reader=released_legacy_collateral(database)
    if phase=='admission':
        c.service.load_released_balances=None
        with c.owner.transaction(c.scope) as tx:
            raw=tx.projections.book(c.scope)
            assert c.service._prepare_collateral(tx,raw).holds[0].held==released.reservation.amount
            # Even the authentic historical zero projection is not current credit.
            with pytest.raises(MissingEvidence,match='PROGRAM_CURRENT_FINANCIAL_HOLDS_CHANGED'):
                current_program_quantity_reservation_ids(tx,raw,replace(raw,reservations=(released,)))
        with pytest.raises(ValueError,match='CAPACITY'):
            c.service._transaction(lambda tx:attach(c,tx))
        return
    write,request,bundle=c.service._transaction(lambda tx:attach(c,tx))
    gate=boundary(c);gate.readers['load_released_balances']=reader
    if phase=='mark':
        gate.readers['load_released_balances']=None
        with pytest.raises(ValueError,match='PROGRAM_CURRENT_(BOOK_CHANGED|GROUP_HOLDS_CHANGED|ECONOMICS_CHANGED)'):
            gate.mark_may_have_sent(write.command_id,lease(c))
        return
    permit=gate.mark_may_have_sent(write.command_id,lease(c))
    if phase=='consume':
        gate.readers['load_released_balances']=None
        with pytest.raises(ValueError,match='PROGRAM_CURRENT_(BOOK_CHANGED|GROUP_HOLDS_CHANGED|ECONOMICS_CHANGED)'):
            gate.consume(permit,lease(c))
        return
    assert gate.consume(permit,lease(c))==request
    gate.readers['load_released_balances']=None
    with pytest.raises(ValueError,match='PROGRAM_CONSUMED_FINANCIAL_BOOK_CHANGED'):
        gate.recheck_consumed(permit,lease(c))
