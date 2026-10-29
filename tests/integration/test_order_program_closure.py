"""Isolated PostgreSQL role and reconciliation probes; never venue qualification."""
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import json

import pytest

from integration.test_order_programs import isolated_postgres, pre_upgrade, order_db, scenario as base_scenario, repo, proof, NOW
from integration.support_ip_03 import ROOT
from trading_system.contracts import types as t
from trading_system.contracts.order_lifecycle import ParentObservation, ChildState
from trading_system.contracts.canonical import canonical_hash, domain_payload, canonical_json
from trading_system.storage.models import scope_id, encode, decode, MissingEvidence
from trading_system.storage.uow import UnitOfWork
from trading_system.risk.reservation_repository import ReservationRepository
from trading_system.risk.order_program_closure import (
    ClosureReview, ReviewedObligation, REQUIREMENTS, OrderProgramClosureService, closure_context,
)


@pytest.fixture(scope='module')
def closure_db(order_db):
    import psycopg
    with psycopg.connect(order_db) as c:
        c.execute((ROOT/'migrations/0009_order_program_closure.sql').read_text(encoding='utf-8'))
        # Independent synthetic archive. The ordinary owner cannot publish or
        # overwrite review material. This is test evidence, not venue evidence.
        c.execute('CREATE TABLE ledger.test_closure_reviews (context_hash text PRIMARY KEY,payload jsonb NOT NULL)')
        c.execute('REVOKE ALL ON ledger.test_closure_reviews FROM PUBLIC,ledger_owner')
        c.execute('GRANT SELECT ON ledger.test_closure_reviews TO ledger_order_closure_resolver')
    return order_db


@pytest.fixture
def scenario(closure_db, base_scenario):
    s = base_scenario
    a = s.authority
    e = ParentObservation(contract_version='2026-10-28', observation_id='closed', scope=s.scope,
        venue=a.venue, namespace=a.namespace, parent_client_id=a.parent_client_id,
        parent_venue_id=a.parent_venue_id, status='COMPLETED', unallocated_quantity='0',
        cumulative_quantity='0', occurred_at=NOW)
    evidence = proof(e)
    book = t.BookSnapshot(scope=s.scope, positions=(), active_orders=(), reservations=(),
        balances=(t.Money(amount=Decimal('10000'),currency='USDT'),), sequence=1,
        basis_fingerprint=canonical_hash(domain_payload(e)),
        coverage=t.Coverage(scope=s.scope,query_succeeded=True,pagination_complete=True,supported=True,missing=()),
        evidence=(evidence,))
    with s.uow.transaction(s.scope) as tx:
        assert repo(tx).observe_parent(s.program.program_id,e,evidence).restriction is None
        tx.projections.save_book(book,0)
    def connect_resolver():
        c=s.connect_admin(); c.execute('SET ROLE ledger_order_closure_resolver');c.commit();return c
    s.resolver_uow=UnitOfWork(connect_resolver)
    s.targets={s.reservation.reservation_id:Decimal(0)}
    def verify(tx,context):
        row=tx.execute('SELECT payload FROM ledger.test_closure_reviews WHERE context_hash=%s',
                       (canonical_hash(context),)).fetchone()
        return decode(ClosureReview,row[0]) if row else None
    s.service=OrderProgramClosureService(resolver_uow=s.resolver_uow,verify=verify)
    return s


def publish_review(s, *, disposition='FLAT_RECONCILED', context=None):
    if context is None:
        with s.uow.transaction(s.scope) as tx:context=closure_context(tx,s.program.program_id,s.targets)
    ref=t.EvidenceRef(id='independent-archive',hash=canonical_hash(context),environment=s.scope.environment,
                      source='ISOLATED_RECONCILIATION_ARCHIVE',visible_at=NOW)
    reviewed=datetime.now(timezone.utc)
    review=ClosureReview(context_hash=canonical_hash(context),
        obligations=tuple(ReviewedObligation(requirement=k,evidence=(ref,)) for k in sorted(REQUIREMENTS)),
        protection_disposition=disposition,reviewed_at=reviewed.isoformat(),expires_at=(reviewed+timedelta(seconds=60)).isoformat())
    with s.connect_admin() as c:
        c.execute('INSERT INTO ledger.test_closure_reviews VALUES(%s,%s::jsonb)',(review.context_hash,encode(review)))
    return context,review


def test_reviewed_full_release_preserves_creation_and_funding_link(scenario):
    s=scenario;publish_review(s)
    result=s.service.close(s.scope,s.program.program_id,s.targets)
    assert result[0].held==0 and result[0].reservation.state==t.ReservationState.RELEASED
    with s.uow.transaction(s.scope) as tx:
        assert tx.reservations.for_intent(s.intent.intent_id)==(s.reservation,)
        assert tx.execute('SELECT count(*) FROM ledger.order_funding_links WHERE scope_id=%s',(scope_id(s.scope),)).fetchone()[0]==1


def test_reviewed_partial_reduction_keeps_residual_hold(scenario):
    s=scenario;s.targets[s.reservation.reservation_id]=Decimal('123.00000001');publish_review(s)
    result=s.service.close(s.scope,s.program.program_id,s.targets)[0]
    assert result.held==Decimal('123.00000001') and result.reservation.state==t.ReservationState.HELD


def test_missing_or_scalar_verifier_never_clears(scenario):
    s=scenario
    with pytest.raises(MissingEvidence):OrderProgramClosureService().close(s.scope,s.program.program_id,s.targets)
    with pytest.raises(ValueError,match='STRUCTURED'):
        OrderProgramClosureService(resolver_uow=s.resolver_uow,verify=lambda *a:True,clock=lambda:NOW).close(s.scope,s.program.program_id,s.targets)


def test_owner_audit_forgery_cannot_release_linked_hold(scenario):
    s=scenario
    with s.uow.transaction(s.scope) as tx:context=closure_context(tx,s.program.program_id,s.targets)
    target=context['targets'][0]['after']
    with pytest.raises(Exception,match='dedicated program closure'):
        with s.uow.transaction(s.scope) as tx:
            tx.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,%s,1,%s::jsonb,%s)',
                (scope_id(s.scope),'ip07:reservation:'+s.reservation.reservation_id,canonical_json(target),tx.transaction_id))
    with pytest.raises(MissingEvidence,match='PROGRAM_RESERVATION'):
        with s.uow.transaction(s.scope) as tx:
            tx._append('audit_events','forged-closure',ClosureReview(context_hash=canonical_hash(context),obligations=(),protection_disposition='FLAT_RECONCILED',reviewed_at=NOW,expires_at=NOW),0)
            ReservationRepository(tx).save(decode(t.ReservationProjection,target))


def test_runtime_role_cannot_mint_approval_or_escalate(scenario):
    s=scenario;context,review=publish_review(s)
    for statement,params in [
        ('SET ROLE ledger_order_closure_resolver',()),
        ('SELECT ledger.approve_order_program_closure(%s,%s,%s::jsonb,%s)',
         (scope_id(s.scope),s.program.program_id,canonical_json({**context,'review':domain_payload(review)}),'forged')),
        ("INSERT INTO ledger.order_closure_approvals VALUES ('s','p','r',1,'{}','{}','{}','t',pg_current_xact_id())",()),
    ]:
        # A non-superuser session authorization is essential: SET ROLE from the
        # test admin alone would still retain admin's right to change role.
        with pytest.raises(Exception,match='permission denied'):
            with s.connect_admin() as c:
                c.execute('SET SESSION AUTHORIZATION ledger_owner');c.execute(statement,params)


def test_committed_approval_cannot_be_replayed_in_another_transaction(scenario):
    s=scenario;context,review=publish_review(s)
    with s.resolver_uow.transaction(s.scope) as tx:
        tx.execute('SELECT ledger.approve_order_program_closure(%s,%s,%s::jsonb,%s)',
            (scope_id(s.scope),s.program.program_id,canonical_json({**context,'review':domain_payload(review)}),tx.transaction_id))
    with pytest.raises(MissingEvidence,match='PROGRAM_RESERVATION'):
        with s.uow.transaction(s.scope) as tx:
            ReservationRepository(tx).save(decode(t.ReservationProjection,context['targets'][0]['after']))


@pytest.mark.parametrize('changed',['state','book','owner','reservation'])
def test_stale_context_is_rejected_by_database(scenario,changed):
    s=scenario;context,review=publish_review(s)
    if changed=='owner':
        # Test administrator models an independently completed owner change.
        with s.connect_admin() as c:
            c.execute('ALTER TABLE ledger.active_owners DISABLE TRIGGER owner_immutable')
            c.execute('UPDATE ledger.active_owners SET policy_revision=2 WHERE account_id=%s',(s.scope.venue_account_id,))
            c.execute('ALTER TABLE ledger.active_owners ENABLE TRIGGER owner_immutable')
    else:
        with s.uow.transaction(s.scope) as tx:
            if changed=='state':
                state=repo(tx).state(s.program.program_id)
                tx._append('order_program_states',s.program.program_id,replace(state,sequence=state.sequence+1),state.sequence)
            elif changed=='book':
                book=tx.projections.book(s.scope);tx.projections.save_book(replace(book,sequence=2),1)
            else:
                r=ReservationRepository(tx);p=r.get(s.reservation.reservation_id)
                r.save(replace(p,sequence=p.sequence+1))
    with pytest.raises(Exception,match='stale'):
        with s.resolver_uow.transaction(s.scope) as tx:
            tx.execute('SELECT ledger.approve_order_program_closure(%s,%s,%s::jsonb,%s)',
                (scope_id(s.scope),s.program.program_id,canonical_json({**context,'review':domain_payload(review)}),tx.transaction_id))


@pytest.mark.parametrize('status',['SUBMITTED','UNKNOWN','CANCEL_UNKNOWN','PARTIALLY_FILLED'])
def test_parent_stop_does_not_clear_open_or_unknown_child(scenario,status):
    s=scenario
    with s.uow.transaction(s.scope) as tx:
        state=repo(tx).state(s.program.program_id)
        child=ChildState(child_order_id='child',child_client_id=None,side='BUY',status=status,quantity='1',cumulative_quantity='0',fills=(),occurred_at=NOW)
        tx._append('order_program_states',s.program.program_id,replace(state,status='STOPPED',children=(child,),sequence=state.sequence+1),state.sequence)
    with pytest.raises(MissingEvidence,match='ALL_CHILDREN_TERMINAL'):
        s.service.close(s.scope,s.program.program_id,s.targets)


def test_active_risk_blocks_even_with_independent_review(scenario):
    s=scenario;publish_review(s)
    with s.uow.transaction(s.scope) as tx:
        tx.risk_locks.append(t.RiskLock(lock_id='unknown',scope=s.scope,cause='ORDER_OUTCOME_UNKNOWN',state='ACTIVE',permission=t.RiskPermission.RECOVERY_REQUIRED,required_evidence=('ORIGINAL_COMMAND_RECONCILIATION',),raised_at=NOW),0)
    with pytest.raises(MissingEvidence,match='ACTIVE_RISK'):s.service.close(s.scope,s.program.program_id,s.targets)


def test_residual_position_without_protection_stays_held(scenario):
    s=scenario
    with s.uow.transaction(s.scope) as tx:
        state=repo(tx).state(s.program.program_id)
        tx._append('order_program_states',s.program.program_id,replace(state,protection_quantity='1',sequence=state.sequence+1),state.sequence)
    with pytest.raises(MissingEvidence,match='POSITION_RECONCILIATION'):
        s.service.close(s.scope,s.program.program_id,s.targets)


def test_unlinked_reservation_save_preserves_old_behavior(scenario):
    from integration.support_ip_03 import LedgerScenario
    s=LedgerScenario(scenario.dsn)
    with s.uow.transaction(s.scope) as tx:
        s.write_bundle(tx);r=ReservationRepository(tx);p=r.get(s.reservation.reservation_id)
        r.save(replace(p,sequence=1,held=Decimal(0)))
    with s.uow.transaction(s.scope) as tx:assert ReservationRepository(tx).get(s.reservation.reservation_id).held==0


def residual_fixture(s, *, accounted=True, protected=True):
    """Explicit synthetic migrated terminal trade and matching ledger facts."""
    from trading_system.contracts.order_lifecycle import ChildFill,AssetBudget
    from trading_system.storage.order_programs import ChildLink
    from trading_system.storage.events import EventStore
    with s.uow.transaction(s.scope) as tx:
        state=repo(tx).state(s.program.program_id)
        observed=ChildFill(native_trade_id='closed-trade',quantity='1',price='99',
            fees=(AssetBudget(asset='USDT',amount='0.1'),),occurred_at=NOW)
        child=ChildState(child_order_id='closed-child',child_client_id=None,side='BUY',status='FILLED',
            quantity='1',cumulative_quantity='1',fills=(observed,),occurred_at=NOW)
        tx._append('order_program_states',s.program.program_id,replace(state,children=(child,),protection_quantity='1',sequence=state.sequence+1),state.sequence)
        child_key=canonical_hash(dict(venue='BINANCE',namespace='ORDINARY',child_id=child.child_order_id))
        tx._append('order_child_links',child_key,ChildLink(program_id=s.program.program_id,parent_venue_id=s.authority.parent_venue_id,
            child_order_id=child.child_order_id,namespace='ORDINARY',evidence_id='synthetic-terminal-child'),0)
        if accounted:
            fill=replace(s.fill(native_id=observed.native_trade_id),quantity=t.Quantity(value=Decimal(1),unit='BTC'),
                price=t.Price(value=Decimal(99),currency='USDT'),fees=(t.Money(amount=Decimal('0.1'),currency='USDT'),),
                order=t.OrderIdentity(scope=s.scope,logical_client_order_id='closed',native_client_order_id='closed',venue_client_order_id='closed',venue_order_id=child.child_order_id),occurred_at=NOW)
            EventStore().append(tx,replace(s.event(fill=fill),occurred_at=NOW),0)
        coverage=t.Coverage(scope=s.scope,query_succeeded=True,pagination_complete=True,supported=True,missing=())
        position=t.ManagedPosition(position_id='residual',scope=s.scope,actual_quantity=t.Quantity(value=Decimal(1),unit='BTC'),
            owner_strategy_id=s.authority.owner_strategy_id,owner_book_id='book',ownership_epoch=s.authority.ownership_epoch,
            cost_evidence=coverage,realized_pnl=None,unrealized_valuation=None,active_exit_plan_id='retained' if protected else None,risk_locks=())
        book=tx.projections.book(s.scope)
        tx.projections.save_book(replace(book,sequence=2,positions=(position,)),1)
        if protected:
            tx._append('protection_plans','retained',t.ProtectionPlan(plan_id='retained',mode='HOSTED',scope=s.scope,
                coverage_quantity=t.Quantity(value=Decimal(1),unit='BTC'),stop_price=t.Price(value=Decimal(90),currency='USDT'),
                stop_trigger='LAST',native_order_ids=(),monitor_id='independently-verified-host',state=t.ProtectionState.VERIFIED_HOSTED,
                last_verified_at=NOW,coverage_gap_since=None,replacement_revision=1,on_gap_policy='EXIT'),0)


def test_nonflat_requires_review_explicitly_retaining_protection(scenario):
    s=scenario;residual_fixture(s);publish_review(s)
    with pytest.raises(ValueError,match='EXPLICIT_PROTECTION'):
        s.service.close(s.scope,s.program.program_id,s.targets)


def test_reviewed_residual_with_accounted_fees_and_retained_protection_can_release(scenario):
    s=scenario;residual_fixture(s);publish_review(s,disposition='RESPONSIBILITY_RETAINED')
    assert s.service.close(s.scope,s.program.program_id,s.targets)[0].held==0
    with s.uow.transaction(s.scope) as tx:
        assert tx.projections.book(s.scope).positions[0].actual_quantity.value==1
        assert tx._latest('protection_plans','retained',t.ProtectionPlan).state==t.ProtectionState.VERIFIED_HOSTED


@pytest.mark.parametrize('accounted,protected,reason',[(False,True,'LEDGER_FILL'),(True,False,'RETAINED_PROTECTION')])
def test_fill_accounting_and_protection_are_not_optional(scenario,accounted,protected,reason):
    s=scenario;residual_fixture(s,accounted=accounted,protected=protected)
    with pytest.raises(MissingEvidence,match=reason):s.service.close(s.scope,s.program.program_id,s.targets)


def test_approved_context_becoming_stale_in_same_transaction_is_denied(scenario):
    s=scenario;context,review=publish_review(s)
    # Administrator performs this adversarial same-transaction mutation solely
    # to probe the database guard, bypassing the resolver service's normal flow.
    with pytest.raises(MissingEvidence,match='PROGRAM_RESERVATION'):
        with UnitOfWork(s.connect_admin).transaction(s.scope) as tx:
            tx.execute('SELECT ledger.approve_order_program_closure(%s,%s,%s::jsonb,%s)',
                (scope_id(s.scope),s.program.program_id,canonical_json({**context,'review':domain_payload(review)}),tx.transaction_id))
            state=repo(tx).state(s.program.program_id)
            tx._append('order_program_states',s.program.program_id,replace(state,sequence=state.sequence+1),state.sequence)
            ReservationRepository(tx).save(decode(t.ReservationProjection,context['targets'][0]['after']))


def test_database_rejects_expired_review_even_on_resolver_path(scenario):
    s=scenario;context,review=publish_review(s)
    review=replace(review,reviewed_at=NOW,expires_at=NOW)
    with pytest.raises(Exception,match='expired or invalid'):
        with s.resolver_uow.transaction(s.scope) as tx:
            tx.execute('SELECT ledger.approve_order_program_closure(%s,%s,%s::jsonb,%s)',
                (scope_id(s.scope),s.program.program_id,canonical_json({**context,'review':domain_payload(review)}),tx.transaction_id))


def test_balance_credit_is_limited_to_reviewed_snapshot(scenario):
    from trading_system.risk.reservation_repository import for_balance
    s=scenario;publish_review(s);projection=s.service.close(s.scope,s.program.program_id,s.targets)[0]
    with s.uow.transaction(s.scope) as tx:book=tx.projections.book(s.scope)
    assert for_balance(projection,book).held==0
    assert for_balance(projection,replace(book,sequence=book.sequence+1)).held==s.reservation.amount


def test_later_independent_review_can_release_a_remaining_partial_hold(scenario):
    s=scenario;s.targets[s.reservation.reservation_id]=Decimal('100');publish_review(s)
    assert s.service.close(s.scope,s.program.program_id,s.targets)[0].held==100
    s.targets[s.reservation.reservation_id]=Decimal(0);publish_review(s)
    assert s.service.close(s.scope,s.program.program_id,s.targets)[0].held==0


def test_unchanged_hold_is_not_a_release_request(scenario):
    s=scenario;s.targets[s.reservation.reservation_id]=s.reservation.amount
    with pytest.raises(ValueError,match='NO_REVIEWED_REDUCTION'):
        s.service.close(s.scope,s.program.program_id,s.targets)


def test_approval_expiring_before_save_cannot_reduce_hold(scenario):
    s=scenario;context,review=publish_review(s)
    now=datetime.now(timezone.utc)
    review=replace(review,reviewed_at=now.isoformat(),expires_at=(now+timedelta(seconds=1)).isoformat())
    with pytest.raises(MissingEvidence,match='PROGRAM_RESERVATION'):
        with s.resolver_uow.transaction(s.scope) as tx:
            tx.execute('SELECT ledger.approve_order_program_closure(%s,%s,%s::jsonb,%s)',
                (scope_id(s.scope),s.program.program_id,canonical_json({**context,'review':domain_payload(review)}),tx.transaction_id))
            tx.execute('SELECT pg_sleep(1.1)')
            ReservationRepository(tx).save(decode(t.ReservationProjection,context['targets'][0]['after']))
