"""Existing owner and ledgers composed into one explicit isolated PAPER path."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from integration.test_program_owned_posting import (
    isolated_postgres,command_database,source_database,cash_database,database,venue,
)
from integration.test_paper_market_matcher import case,event
from integration.test_program_fill_source_approval import actual_verifier,source_uow
from trading_system.contracts import types as t
from trading_system.engine.paper_market import PaperMatcher
from trading_system.ownership.registry import OwnershipRegistry,RegistryTx
from trading_system.storage.models import MissingEvidence,scope_id


@pytest.fixture(scope='module')
def host_venue(database,venue):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from trading_system.engine.paper_market_v2 import provision_paper_market_v2
    from trading_system.engine.paper_market_quote import provision_paper_quote
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(venue.runtime_dsn)['dbname']})
    provision_paper_market_v2(admin,venue);provision_paper_quote(admin,venue)
    return venue


def host(c,database,path,*,version=1):
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    from trading_system.runtime.program_paper_host import ProgramPaperHost
    if version==1:verifier,codec=actual_verifier(c,path)
    else:
        from trading_system.engine.nautilus.id_codec import VenueIdCodec
        from trading_system.engine.nautilus.native_events import NativeEventBridge
        from trading_system.execution.paper_program_v2_reports import PaperProgramV2ReportVerifier
        codec=VenueIdCodec(c.scope)
        verifier=PaperProgramV2ReportVerifier(c.service.uow,c.scope,engine=c.engine,archive_bridge=NativeEventBridge(codec,path))
    source=ProgramFillSourceService(source_uow(database),c.scope,verifier=verifier)
    return ProgramPaperHost(owner=c.owner,boundary=c.gate,owner_uow=c.service.uow,
        source_service=source,trade_id_scope='INSTRUMENT'),codec


def empty_owner(c):
    p=t.ManagedPosition(position_id='host-owned:'+c.scope.instrument_uid,scope=c.scope,
        actual_quantity=t.Quantity(unit='BASE',value=D(0)),owner_strategy_id=c.c.terms.owner_strategy_id,
        owner_book_id='isolated-host-book',ownership_epoch=c.c.terms.ownership_epoch,
        cost_evidence=c.c.book.coverage,realized_pnl=None,unrealized_valuation=None,
        active_exit_plan_id=None,risk_locks=())
    OwnershipRegistry(c.service.uow).register(p,c.c.terms.policy_revision)


@pytest.mark.parametrize('version',[1,2])
def test_actual_host_dispatch_observation_owned_posting_and_working_book(database,host_venue,tmp_path,version):
    from trading_system.risk.program_exposure import FeeBound
    from trading_system.execution.program_book_cash import require_current_program_book_cash
    from trading_system.execution.program_legacy_opening import read_program_local_account_opening
    c=case(database,host_venue,tmp_path,scoped_quantities=True,quote_fee_rate='.001',
        node_fees=(FeeBound(amount=t.Money(currency='USDT',amount=D(100)),debit='EXTERNAL_BALANCE'),))
    if version==2:
        from trading_system.engine.paper_market_v2 import PaperMatchingModelV2
        c.engine._matching_model=PaperMatchingModelV2(model_id='HOST_V2_ACTUAL_DEPTH',quote_fee_rate='.001')
    empty_owner(c);runtime,codec=host(c,database,tmp_path/'host',version=version)
    try:
        runtime.dispatch(c.write.command_id,c.lease)
        initial=runtime.observe(c.write.command_id)
        assert initial.accounting.status=='NO_FILLS'
        assert initial.orders.status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
        observed=runtime.observe(c.write.command_id)
        assert observed.accounting.status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        assert observed.orders.status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        assert not observed.execution_authorized and not observed.funds_release_authorized
        with c.service.uow.transaction(c.scope) as tx:
            book=tx.projections.book(c.scope)
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.4')
            assert book.positions[0].cost_evidence.missing
            assert book.active_orders[0].leaves_quantity.value==D('.6')
            assert {m.currency:m.amount for m in book.balances}==dict(BTC=D('10.4'),USDT=D('967371.404'))
            require_current_program_book_cash(tx,book,read_opening=read_program_local_account_opening)
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
            assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
        runtime.observe(c.write.command_id)
        assert runtime.reconcile_book()==book
        with c.service.uow.transaction(c.scope) as tx:assert tx.projections.book(c.scope)==book
        with pytest.raises(ValueError,match='NO_RETRY'):runtime.dispatch(c.write.command_id,c.lease)
    finally:codec.close()


def test_missing_owned_position_retains_raw_fill_and_financial_block(database,host_venue,tmp_path):
    c=case(database,host_venue,tmp_path,scoped_quantities=True);runtime,codec=host(c,database,tmp_path/'missing-owner')
    try:
        runtime.dispatch(c.write.command_id,c.lease)
        PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
        result=runtime.observe(c.write.command_id)
        assert result.accounting.status=='RAW_RECORDED_ECONOMICS_BLOCKED'
        assert 'CURRENT_OWNED_POSITION_REQUIRED' in result.accounting.missing
        assert result.orders.status=='ORDER_PROJECTED_FINANCIAL_RECONCILIATION_REQUIRED'
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
            assert not tx.projections.book(c.scope).positions
            assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
    finally:codec.close()


def test_historical_gtc_query_does_not_renew_the_creation_permit(database,host_venue,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    c=case(database,host_venue,tmp_path,scoped_quantities=True);runtime,codec=host(c,database,tmp_path/'historical')
    try:
        runtime.dispatch(c.write.command_id,c.lease)
        before=c.c.facts.inputs.clock
        c.engine._clock=lambda:replace(before,utc_now=(utc_timestamp(before.utc_now)+timedelta(seconds=40)).isoformat(),
            monotonic_ns=before.monotonic_ns+40*10**9)
        assert runtime.observe(c.write.command_id).orders.status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        with pytest.raises(ValueError,match='NO_RETRY'):runtime.dispatch(c.write.command_id,c.lease)
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(1,)
    finally:codec.close()


@pytest.mark.parametrize('family',['native','quote'])
def test_unqualified_execution_family_never_borrows_the_base_source_bridge(database,host_venue,tmp_path,family):
    if family=='native':c=case(database,host_venue,tmp_path,template='TWAP',tif='NONE')
    else:
        from integration.test_paper_quote_budget_postgres import execution_case
        c=execution_case(database,host_venue,tmp_path)
    runtime,codec=host(c,database,tmp_path/family)
    try:
        with pytest.raises(ValueError,match='EXECUTION_FAMILY_UNQUALIFIED'):runtime.dispatch(c.write.command_id,c.lease)
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_dispatch_marks WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(0,)
    finally:codec.close()


def test_unknown_source_retains_current_book_and_creates_no_new_token(database,host_venue,tmp_path):
    c=case(database,host_venue,tmp_path,scoped_quantities=True);runtime,codec=host(c,database,tmp_path/'unknown')
    try:
        runtime.dispatch(c.write.command_id,c.lease);runtime.observe(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:before=tx.projections.book(c.scope)
        c.engine.query_creation=lambda _:None
        result=runtime.observe(c.write.command_id)
        assert result.accounting.status=='SOURCE_BLOCKED' and result.orders is None
        with pytest.raises(MissingEvidence,match='PROGRAM_ORDER_QUERY_UNKNOWN'):runtime.working_inputs()
        with c.service.uow.transaction(c.scope) as tx:
            from trading_system.execution.program_submission_observation import read_program_submission_observation
            view=read_program_submission_observation(tx,c.write)
            assert not view.submission_known and view.ever_accepted
            assert tx.projections.book(c.scope)==before
            assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(1,)
    finally:codec.close()


def host_state(c):
    """Actual business rows whose values/xmin cannot change on a failed read."""
    with c.service.uow.transaction(c.scope) as tx:
        return tuple((table,tx.execute('SELECT row_to_json(r)::text,xmin::text FROM ledger.'+table+
            ' r WHERE scope_id=%s ORDER BY row_to_json(r)::text',(scope_id(c.scope),)).fetchall())
            for table in ('book_snapshots','reservations','order_projections','positions','cashflows',
                'order_writes_v2','program_dispatch_marks','program_permit_consumptions'))


IO_CASES=[(version,point,kind,1) for version in (1,2) for point in ('query_creation','query_market_evidence')
    for kind in ('os','timeout','postgres')]+[(version,point,'os',2)
        for version in (1,2) for point in ('query_creation','query_market_evidence')]


@pytest.mark.parametrize('version,point,kind,attempt',IO_CASES)
def test_host_source_io_exception_blocks_current_working_before_io_and_preserves_history(
        database,host_venue,tmp_path,version,point,kind,attempt):
    import psycopg
    from trading_system.execution.program_submission_observation import read_program_submission_observation
    c=case(database,host_venue,tmp_path,scoped_quantities=True)
    if version==2:
        from trading_system.engine.paper_market_v2 import PaperMatchingModelV2
        c.engine._matching_model=PaperMatchingModelV2(model_id='HOST_IO_V2_ACTUAL_SOURCE',quote_fee_rate='0')
    runtime,codec=host(c,database,tmp_path/'io-source',version=version)
    try:
        runtime.dispatch(c.write.command_id,c.lease)
        assert runtime.observe(c.write.command_id).orders.status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        assert runtime.working_inputs().order_sides
        with c.service.uow.transaction(c.scope) as tx:
            prior=read_program_submission_observation(tx,c.write)
            assert prior.state=='ACCEPTED' and prior.submission_known and prior.ever_accepted
        before=host_state(c);original=getattr(c.engine,point);calls=[];seen=[]
        error={'os':OSError,'timeout':TimeoutError,'postgres':psycopg.OperationalError}[kind]('ISOLATED_SOURCE_IO_FAILURE')
        def failed_query(command_id):
            calls.append(command_id)
            if len(calls)!=attempt:return original(command_id)
            # Only the venue read is fault-injected. This independent actual
            # ledger read proves the UNKNOWN barrier was committed beforehand.
            with c.service.uow.transaction(c.scope) as tx:
                view=read_program_submission_observation(tx,c.write)
                seen.append(view.submission_known)
            raise error
        setattr(c.engine,point,failed_query)
        with pytest.raises(type(error)) as captured:runtime.observe(c.write.command_id)
        assert captured.value is error # original exception is not converted or swallowed
        with c.service.uow.transaction(c.scope) as tx:
            current=read_program_submission_observation(tx,c.write)
            assert not current.submission_known and current.state=='UNKNOWN' and current.ever_accepted
        assert seen==[False]
        assert host_state(c)==before
        with pytest.raises(MissingEvidence,match='PROGRAM_ORDER_QUERY_UNKNOWN'):runtime.working_inputs()
        setattr(c.engine,point,original)
        assert runtime.observe(c.write.command_id).orders.status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        assert runtime.working_inputs().order_sides and host_state(c)==before
    finally:codec.close()


def test_host_commits_source_barrier_before_any_venue_query(database,host_venue,tmp_path):
    from trading_system.storage.uow import UnitOfWork
    c=case(database,host_venue,tmp_path,scoped_quantities=True);runtime,codec=host(c,database,tmp_path/'commit-failure')
    try:
        runtime.dispatch(c.write.command_id,c.lease);runtime.observe(c.write.command_id)
        before=host_state(c);source=runtime.orders.observer.uow;calls=[]
        class FailCommit:
            def __init__(self,db):self.db=db
            def __getattr__(self,name):return getattr(self.db,name)
            def commit(self):raise RuntimeError('SOURCE_BARRIER_COMMIT_FAILED')
        runtime.orders.observer.uow=UnitOfWork(lambda:FailCommit(source.connection_factory()))
        original=c.engine.query_creation
        def query(command_id):calls.append(command_id);return original(command_id)
        c.engine.query_creation=query
        with pytest.raises(RuntimeError,match='SOURCE_BARRIER_COMMIT_FAILED'):runtime.observe(c.write.command_id)
        assert not calls and host_state(c)==before
    finally:codec.close()


@pytest.mark.parametrize('version',[1,2])
def test_host_cost_reconciliation_replays_and_rejects_later_unknown(database,host_venue,tmp_path,version):
    c=case(database,host_venue,tmp_path,scoped_quantities=True,quote_fee_rate='0')
    if version==2:
        from trading_system.engine.paper_market_v2 import PaperMatchingModelV2
        c.engine._matching_model=PaperMatchingModelV2(model_id='HOST_COST_V2',quote_fee_rate='0')
    empty_owner(c);runtime,codec=host(c,database,tmp_path/'host-cost',version=version)
    try:
        runtime.dispatch(c.write.command_id,c.lease)
        PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
        runtime.observe(c.write.command_id)
        before=dict(host_state(c))
        proof=runtime.reconcile_cost_history()
        assert D(proof.remaining_cost_numerator)/D(proof.remaining_cost_denominator)==D('32596')
        after=dict(host_state(c))
        for table in ('reservations','cashflows','order_writes_v2','program_dispatch_marks','program_permit_consumptions'):
            assert after[table]==before[table]
        assert runtime.reconcile_cost_history()==proof
        assert dict(host_state(c))==after
        c.engine.query_creation=lambda _:None
        assert runtime.observe(c.write.command_id).accounting.status=='SOURCE_BLOCKED'
        with pytest.raises(MissingEvidence,match='COST_CURRENT_QUERY_REQUIRED'):
            runtime.reconcile_cost_history()
        assert dict(host_state(c))==after
    finally:codec.close()
