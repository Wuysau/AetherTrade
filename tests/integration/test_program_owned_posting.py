"""Actual native source -> one owner transaction for assets/position/fees/book."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from integration.test_program_account_cash import isolated_postgres,command_database,source_database,database,venue,prepared
from integration.test_program_fill_source_approval import actual_verifier,source_uow
from integration.test_paper_market_matcher import case,submit,event
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.engine.paper_market import PaperMatcher
from trading_system.execution.program_account_opening import read_program_account_opening
from trading_system.ownership.registry import OwnershipRegistry,RegistryTx
from trading_system.storage.models import scope_id


def setup(database,venue,tmp_path,*,owned=True):
    from trading_system.execution.program_accounting import ProgramPaperAccountingService
    from trading_system.execution.program_owned_posting import ProgramOwnedSpotPosting
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    from uuid import uuid4
    from integration.test_program_financial_admission import real_case
    from integration.test_program_account_opening import provision_uow,inputs
    from trading_system.risk.program_exposure import FeeBound
    from trading_system.execution.program_account_opening import ProgramAccountOpeningService
    initial=real_case(database,instrument_uid='owned-fee-market-'+uuid4().hex,
        node_fees=(FeeBound(amount=t.Money(currency='USDT',amount=D('100')),debit='EXTERNAL_BALANCE'),))
    balances,proof=inputs(initial)
    ProgramAccountOpeningService(provision_uow(database),initial.scope).initialize(balances,evidence=proof)
    c=case(database,venue,tmp_path,prepared_case=initial,quote_fee_rate='.001')
    if owned:
        # Existing trusted ownership registration of a genuinely empty owned
        # book; the account's initial BTC10 is intentionally not allocated.
        p=t.ManagedPosition(position_id='owned:'+c.scope.instrument_uid,scope=c.scope,
            actual_quantity=t.Quantity(value=D(0),unit='BASE'),owner_strategy_id=c.c.terms.owner_strategy_id,
            owner_book_id='isolated-owned-book',ownership_epoch=c.c.terms.ownership_epoch,
            cost_evidence=c.c.book.coverage,realized_pnl=None,unrealized_valuation=None,
            active_exit_plan_id=None,risk_locks=())
        OwnershipRegistry(c.service.uow).register(p,c.c.terms.policy_revision)
    submit(c);PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    verifier,codec=actual_verifier(c,tmp_path/'archive')
    source=ProgramFillSourceService(source_uow(database),c.scope,verifier=verifier)
    writer=ProgramOwnedSpotPosting(read_opening=read_program_account_opening)
    s=ProgramPaperAccountingService(c.service.uow,c.scope,source_service=source,trade_id_scope='INSTRUMENT',financial_posting=writer)
    return c,s,codec


def test_genuine_native_assets_owned_position_fees_and_book_commit_in_one_transaction(database,venue,tmp_path):
    from trading_system.execution.program_book_cash import require_current_program_book_cash
    c,s,codec=setup(database,venue,tmp_path)
    try:result=s.apply(c.write.command_id)
    finally:codec.close()
    assert result.status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
    assert not result.execution_authorized and not result.funds_release_authorized
    with c.service.uow.transaction(c.scope) as tx:
        book=tx.projections.book(c.scope);owner=RegistryTx(tx).owner()
        assert owner.position.actual_quantity.value==D('.4') # never initial BTC10.4
        assert book.positions==(owner.position,)
        assert {m.currency:m.amount for m in book.balances}==dict(BTC=D('10.4'),USDT=D('967371.404'))
        assert owner.position.realized_pnl is None and owner.position.cost_evidence.missing
        frame=require_current_program_book_cash(tx,book,read_opening=read_program_account_opening)
        assert len(frame.native_identities)==1
        fee=tx.execute('SELECT payload,xmin::text,transaction_id FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()
        assert fee[0]['classification']=='FEE' and fee[0]['amount']['currency']=='USDT'
        assert D(fee[0]['amount']['amount'])==D('-32.596')
        fill=tx.execute('SELECT xmin::text,transaction_id FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()
        # SAVEPOINT writes have their own xmin subtransaction; one top-level
        # commit/rollback and the common business TX bind the whole operation.
        assert fee[2]==fill[1]
        assert tx.execute('SELECT xmin::text FROM ledger.transaction_observations WHERE transaction_id=%s',
            (fill[1],)).fetchone()==(fill[0],)
        for table,record in [('positions',owner.position.position_id),('book_snapshots','book'),('audit_events','ip16:owner')]:
            assert tx.execute('SELECT xmin::text,transaction_id FROM ledger.'+table+
                ' WHERE scope_id=%s AND record_id=%s ORDER BY sequence DESC LIMIT 1',
                (scope_id(c.scope),record)).fetchone()==(fee[1],fill[1])
        assert tx.reservations.for_intent(c.bundle.intent.intent_id) # no release or FLAT grant


def test_replay_restart_and_additional_native_fill_only_apply_new_owned_delta(database,venue,tmp_path):
    c,s,codec=setup(database,venue,tmp_path)
    try:
        s.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:old=tx.projections.book(c.scope)
        s.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:assert tx.projections.book(c.scope)==old
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        s.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.6')
            assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(2,)
            assert {m.currency:m.amount for m in tx.projections.book(c.scope).balances}==dict(BTC=D('10.6'),USDT=D('951057.106'))
    finally:codec.close()


def test_missing_owner_retains_native_facts_without_claiming_position_or_cash(database,venue,tmp_path):
    c,s,codec=setup(database,venue,tmp_path,owned=False)
    try:result=s.apply(c.write.command_id)
    finally:codec.close()
    assert result.status=='RAW_RECORDED_ECONOMICS_BLOCKED' and 'CURRENT_OWNED_POSITION_REQUIRED' in result.missing
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.positions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.projections.book(c.scope).positions==()


def test_failed_financial_commit_rolls_back_all_owner_effects_and_retry_posts_once(database,venue,tmp_path):
    from trading_system.storage.uow import UnitOfWork
    c,s,codec=setup(database,venue,tmp_path)
    original=s.uow
    class FailCommit:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def commit(self):raise RuntimeError('financial commit failure')
    s.uow=UnitOfWork(lambda:FailCommit(original.connection_factory()))
    try:
        with pytest.raises(RuntimeError,match='financial commit failure'):s.apply(c.write.command_id)
        with original.transaction(c.scope) as tx:
            for table in ('fills','positions','cashflows'):
                assert tx.execute('SELECT count(*) FROM ledger.'+table+' WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
            assert RegistryTx(tx).owner().position.actual_quantity.value==0
        s.uow=original
        assert s.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
    finally:codec.close()


def test_concurrent_independent_source_approvals_apply_one_native_owned_effect(database,venue,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from trading_system.execution.program_accounting import ProgramPaperAccountingService
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    from trading_system.execution.program_owned_posting import ProgramOwnedSpotPosting
    c,s,codec=setup(database,venue,tmp_path);ready=Barrier(2)
    def apply(index):
        verifier,local_codec=actual_verifier(c,tmp_path/('archive-'+str(index)))
        service=ProgramPaperAccountingService(c.service.uow,c.scope,
            source_service=ProgramFillSourceService(source_uow(database),c.scope,verifier=verifier),
            trade_id_scope='INSTRUMENT',financial_posting=ProgramOwnedSpotPosting(read_opening=read_program_account_opening))
        try:
            ready.wait(timeout=20)
            return service.apply(c.write.command_id).status
        finally:local_codec.close()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(apply,(1,2)))
        assert results==['ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED']*2
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.4')
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
            assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
            assert tx.projections.book(c.scope).sequence==2
    finally:codec.close()


def test_real_process_restart_does_not_duplicate_position_or_fees(database,venue,tmp_path):
    import os
    from trading_system.execution.program_accounting import ProgramPaperAccountingService
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    from trading_system.execution.program_owned_posting import ProgramOwnedSpotPosting
    c,s,codec=setup(database,venue,tmp_path)
    try:
        s.apply(c.write.command_id)
        read_end,write_end=os.pipe();child=os.fork()
        if child==0:
            os.close(read_end)
            try:
                verifier,fresh_codec=actual_verifier(c,tmp_path/'restarted-archive')
                fresh=ProgramPaperAccountingService(c.service.uow,c.scope,
                    source_service=ProgramFillSourceService(source_uow(database),c.scope,verifier=verifier),
                    trade_id_scope='INSTRUMENT',financial_posting=ProgramOwnedSpotPosting(read_opening=read_program_account_opening))
                result=fresh.apply(c.write.command_id)
                fresh_codec.close()
                with os.fdopen(write_end,'w') as output:output.write(result.status)
            except BaseException:os._exit(71)
            os._exit(0)
        os.close(write_end)
        with os.fdopen(read_end) as incoming:status=incoming.read(256)
        _,exit_status=os.waitpid(child,0)
        assert os.waitstatus_to_exitcode(exit_status)==0 and status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.4')
            assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
            assert tx.projections.book(c.scope).sequence==2
    finally:codec.close()


def test_low_decimal_precision_preserves_genuine_native_fee_cashflow(database,venue,tmp_path):
    from decimal import localcontext
    c,s,codec=setup(database,venue,tmp_path)
    try:
        report=c.engine.query_creation(c.write.command_id)
        assert [D(fill.fees[0].amount) for fill in report.fills]==[D('32.596')]
        with localcontext() as arithmetic:
            arithmetic.prec=3
            result=s.apply(c.write.command_id)
        assert result.status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED',result.missing
        with c.service.uow.transaction(c.scope) as tx:
            fees=tx.execute('SELECT payload FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchall()
            assert [D(row[0]['amount']['amount']) for row in fees]==[D('-32.596')]
            assert {m.currency:m.amount for m in tx.projections.book(c.scope).balances}==dict(BTC=D('10.4'),USDT=D('967371.404'))
    finally:codec.close()


def test_owner_forged_replay_marker_cannot_claim_unchanged_position_as_posted(database,venue,tmp_path):
    from trading_system.execution.program_asset_reader import ProgramAssetPostingReader
    from trading_system.execution.program_asset_postings import make_program_asset_posting,economic_signature
    from trading_system.execution.program_owned_posting import ProgramOwnedPostingRecord
    from trading_system.execution.program_book_cash import reconcile_program_book_cash
    from trading_system.storage.models import MissingEvidence
    c,s,codec=setup(database,venue,tmp_path);financial=s.financial_posting
    try:
        s.financial_posting=None
        assert s.apply(c.write.command_id).status=='RAW_RECORDED_ECONOMICS_BLOCKED'
        approval=s.source_service.approve(c.write.command_id).approvals[0]
        with c.service.uow.transaction(c.scope) as tx:
            fact=ProgramAssetPostingReader(tx,fill_ledger=s._fills).read(approval.approval_id)
            asset=make_program_asset_posting(fact)
            tx._append('audit_events',asset.record_id,asset,0)
            forged=ProgramOwnedPostingRecord(contract_version='2026-10-30',record_kind='PROGRAM_OWNED_POSTING_V1',
                identity_hash=approval.identity_hash,source_economic_hash=economic_signature(fact),
                owner_after=RegistryTx(tx).owner(),position_sequence=0,book_sequence=0)
            tx._append('audit_events','program-owned-posting:'+approval.identity_hash,forged,0)
            with pytest.raises(MissingEvidence,match='OWNED_POSTING_HISTORY_REQUIRED'):
                reconcile_program_book_cash(tx,read_opening=read_program_account_opening)
        s.financial_posting=financial
        result=s.apply(c.write.command_id)
        assert result.status=='RAW_RECORDED_ECONOMICS_BLOCKED' and 'OWNED_POSTING_HISTORY_REQUIRED' in result.missing
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==0
            assert tx.execute('SELECT count(*) FROM ledger.positions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
            assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
    finally:codec.close()


def test_every_native_member_of_a_real_owned_batch_needs_its_same_transaction_marker(database,venue,tmp_path):
    import os
    import psycopg
    from trading_system.execution.program_asset_reader import ProgramAssetPostingReader
    from trading_system.execution.program_owned_posting import require_owned_program_posting
    from trading_system.storage.models import MissingEvidence
    c,s,codec=setup(database,venue,tmp_path)
    try:
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        assert s.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        approvals=s.source_service.approve(c.write.command_id).approvals
        assert len(approvals)==2
        marker='program-owned-posting:'+approvals[1].identity_hash
        # An incomplete historical restore is injected by the isolated admin,
        # never through an owner/source role. Restore the guard BEFORE reading.
        assert os.environ.get('IP03_ISOLATED_DATABASE')=='YES'
        with pytest.raises(psycopg.errors.CheckViolation):
            with psycopg.connect(database) as db:
                db.execute('DELETE FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
                    (scope_id(c.scope),marker))
        with psycopg.connect(database) as db:
            assert db.execute('SELECT current_database()').fetchone()[0].startswith('ip03_test_')
            db.execute('ALTER TABLE ledger.audit_events DISABLE TRIGGER immutable_history')
            removed=db.execute('DELETE FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s RETURNING record_id',
                (scope_id(c.scope),marker)).fetchall()
            db.execute('ALTER TABLE ledger.audit_events ENABLE TRIGGER immutable_history')
            assert removed==[(marker,)]
            assert db.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid='ledger.audit_events'::regclass AND tgname='immutable_history'").fetchone()==('O',)
        with c.service.uow.transaction(c.scope) as tx:
            fact=ProgramAssetPostingReader(tx,fill_ledger=s._fills).read(approvals[0].approval_id)
            with pytest.raises(MissingEvidence,match='OWNED_POSTING_BATCH_INCOMPLETE'):
                require_owned_program_posting(tx,fact)
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.6')
    finally:codec.close()


def test_disjoint_native_batches_cannot_reuse_one_actual_owned_transition(database,venue,tmp_path):
    from trading_system.execution.program_asset_reader import ProgramAssetPostingReader
    from trading_system.execution.program_asset_postings import make_program_asset_posting,economic_signature
    from trading_system.execution.program_owned_posting import ProgramOwnedPostingRecord,fee_cashflow
    from trading_system.execution.program_account_cash import read_program_account_cash
    from trading_system.contracts.canonical import canonical_hash
    from trading_system.storage.events import EventStore
    from trading_system.storage.models import MissingEvidence
    c,s,codec=setup(database,venue,tmp_path);financial=s.financial_posting
    try:
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.4',kind='DEPTH_DELTA'))
        s.financial_posting=None;s.apply(c.write.command_id)
        approvals=s.source_service.approve(c.write.command_id).approvals
        assert len(approvals)==2
        with c.service.uow.transaction(c.scope) as tx:
            _,complete=financial.post(tx,(approvals[0],),s._fills)
            assert not complete
            first=tx._latest('audit_events','program-owned-posting:'+approvals[0].identity_hash,ProgramOwnedPostingRecord)
            other=ProgramAssetPostingReader(tx,fill_ledger=s._fills).read(approvals[1].approval_id)
            asset=make_program_asset_posting(other);tx._append('audit_events',asset.record_id,asset,0)
            forged=replace(first,identity_hash=other.approval.identity_hash,source_economic_hash=economic_signature(other),
                source_approval_ids=(other.approval.approval_id,))
            tx._append('audit_events','program-owned-posting:'+other.approval.identity_hash,forged,0)
            for index,fee in enumerate(other.effects.fees):
                tx._append('cashflows','program-fee:'+other.approval.identity_hash+':'+str(index),fee_cashflow(other,index,fee),0)
            event_id='program-position:'+canonical_hash(dict(scope=domain_payload(c.scope),identities=[other.approval.identity_hash]))
            EventStore().append(tx,t.DomainEvent(event_id=event_id,scope=c.scope,
                aggregate_id='program-position:'+first.owner_after.position.position_id,revision=2,
                occurred_at=other.fill.evidence.visible_at,evidence=(other.fill.evidence,),payload=first.owner_after.position),1)
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.4')
            assert tx.execute('SELECT sum(quantity) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(D('.8'),)
            with pytest.raises(MissingEvidence,match='OWNED_POSTING_TRANSITION_REUSED'):
                read_program_account_cash(tx,read_opening=read_program_account_opening)
    finally:codec.close()


def test_two_real_commands_same_account_can_post_own_scope_then_reconcile_without_cross_writes(database,venue,tmp_path):
    from uuid import uuid4
    from integration.test_program_financial_admission import real_case
    from integration.test_program_account_opening import inputs,provision_uow
    from trading_system.execution.program_account_opening import ProgramAccountOpeningService
    from trading_system.execution.program_accounting import ProgramPaperAccountingService
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    from trading_system.execution.program_owned_posting import ProgramOwnedSpotPosting
    from trading_system.execution.program_book_cash import require_current_program_book_cash,reconcile_program_book_cash
    from trading_system.storage.models import MissingEvidence
    first=real_case(database,instrument_uid='peer-a-'+uuid4().hex)
    balances,proof=inputs(first)
    ProgramAccountOpeningService(provision_uow(database),first.scope).initialize(balances,evidence=proof)
    group=first.c.terms.collateral_group_id
    def current_loader(c):
        from trading_system.contracts.canonical import canonical_hash
        from trading_system.risk.reservation_repository import ReservationRepository
        from trading_system.risk.program_exposure import derive_program_exposure
        original=c.load
        def load(tx,source,intake,book,clock):
            others=tuple(p for p in ReservationRepository(tx).group() if p.reservation.intent_id!=intake.intent_id)
            target=replace(c.c.facts.target_facts,book_sequence=book.sequence,book_hash=canonical_hash(domain_payload(book)))
            budget=replace(c.c.facts.budget_facts,reservations=others)
            c.c.facts=replace(c.c.facts,target_facts=target,budget_facts=budget)
            c.c.exposure=derive_program_exposure(c.c.plan,budget,now=clock.utc_now)
            return original(tx,source,intake,book,clock)
        c.load=load
    current_loader(first)
    first=case(database,venue,tmp_path,prepared_case=first)
    second=real_case(database,venue_account_id=first.scope.venue_account_id,collateral_group_id=group,
        instrument_uid='peer-b-'+uuid4().hex)
    current_loader(second)
    cases=[first,case(database,venue,tmp_path,prepared_case=second)]
    services=[];codecs=[]
    try:
        for index,c in enumerate(cases):
            p=t.ManagedPosition(position_id='owned:'+c.scope.instrument_uid,scope=c.scope,
                actual_quantity=t.Quantity(unit='BASE',value=D(0)),owner_strategy_id=c.c.terms.owner_strategy_id,
                owner_book_id='book:'+c.scope.instrument_uid,ownership_epoch=1,cost_evidence=c.c.book.coverage,
                realized_pnl=None,unrealized_valuation=None,active_exit_plan_id=None,risk_locks=())
            OwnershipRegistry(c.service.uow).register(p,c.c.terms.policy_revision)
            submit(c);PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
            observer,codec=actual_verifier(c,tmp_path/('peer-source-'+str(index)));codecs.append(codec)
            source=ProgramFillSourceService(source_uow(database),c.scope,verifier=observer)
            s=ProgramPaperAccountingService(c.service.uow,c.scope,source_service=source,trade_id_scope='INSTRUMENT')
            assert s.apply(c.write.command_id).status=='RAW_RECORDED_ECONOMICS_BLOCKED'
            s.financial_posting=ProgramOwnedSpotPosting(read_opening=read_program_account_opening);services.append(s)
        a,b=cases
        assert services[0].apply(a.write.command_id).status=='OWN_SCOPE_POSTED_ACCOUNT_RECONCILIATION_REQUIRED'
        with a.service.uow.transaction(a.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.4')
            before=tx.projections.book(a.scope)
            with pytest.raises(MissingEvidence):require_current_program_book_cash(tx,before,read_opening=read_program_account_opening)
        assert services[1].apply(b.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        with a.service.uow.transaction(a.scope) as tx:
            assert tx.projections.book(a.scope)==before # B did not write A
            with pytest.raises(MissingEvidence,match='BOOK_CASH_BINDING_REQUIRED'):
                require_current_program_book_cash(tx,before,read_opening=read_program_account_opening)
            current=reconcile_program_book_cash(tx,read_opening=read_program_account_opening)
            assert current.sequence==before.sequence+1
            assert {m.currency:m.amount for m in current.balances}==dict(BTC=D('10.8'),USDT=D('934808'))
            require_current_program_book_cash(tx,current,read_opening=read_program_account_opening)
        with b.service.uow.transaction(b.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.4')
            assert tx.projections.book(b.scope).sequence==2
    finally:
        for codec in codecs:codec.close()
