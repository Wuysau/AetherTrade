"""Actual V3 financial/MAY/consume + restricted SIMDB market execution."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4
import secrets
import pytest

from integration.test_program_commands_postgres import isolated_postgres,database,attach,boundary,lease
from integration.test_program_financial_admission import real_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload


@pytest.fixture(scope='module')
def venue(database):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from psycopg import sql
    from trading_system.engine.paper_market import provision_paper_market
    from trading_system.engine.paper_db_roles import provision_paper_engine_roles
    name='paper_engine_market_'+uuid4().hex[:12]
    with psycopg.connect(database,autocommit=True) as db:db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':name})
    suffix=uuid4().hex[:12]
    roles=provision_paper_engine_roles(admin,isolated_paper=True,runtime_role='paper_engine_runtime_'+suffix,
        event_role='paper_engine_events_'+suffix,runtime_password=secrets.token_urlsafe(32),event_password=secrets.token_urlsafe(32))
    provision_paper_market(admin,roles)
    return roles


def case(database,venue,tmp_path,*,tif='GTC',post_only=False,market=None):
    from trading_system.engine.paper import PaperEngine
    from trading_system.engine.paper_market import PaperMatchingModel
    from trading_system.runtime.execution_owner import ExecutionOwner
    from trading_system.contracts.order_program import TimeInForceSpec
    def transform(p):
        n=p.program.nodes[0]
        return replace(p,program=replace(p.program,nodes=(replace(n,order=replace(n.order,
            tif=TimeInForceSpec(kind='NONE' if post_only else tif,expires_at=None),post_only=post_only)),)))
    c=real_case(database,proposal_transform=transform,instrument_uid=market or 'isolated-market-'+uuid4().hex)
    c.write,c.request,c.bundle=c.service._transaction(lambda tx:attach(c,tx))
    c.gate=boundary(c);c.lease=lease(c);c.permit=None
    def unavailable(*args):raise ValueError('legacy guard never authorizes V3')
    c.engine=PaperEngine(c.scope,dsn=venue.runtime_dsn,event_dsn=venue.event_dsn,authority_guard=unavailable,
        owner_strategy_id=c.bundle.intent.owner_strategy_id,quantity_unit=c.bundle.intent.unit.native_unit,
        price_currency=c.bundle.archive.rules.quote_asset,clock=lambda:c.c.facts.inputs.clock,
        matching_model=PaperMatchingModel(model_id='ISOLATED_DEPTH_FIFO_ZERO_FEE_FIXTURE',quote_fee_rate='0'))
    @contextmanager
    def claim(shard):
        # Real Linux file exclusion; fixture proves execution logic, not dedicated
        # deployment UID/container qualification.
        import fcntl
        with (tmp_path/(c.scope.venue_account_id+'.lock')).open('a') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:yield
            finally:fcntl.flock(f,fcntl.LOCK_UN)
    c.owner=ExecutionOwner(c.lease,journal_path=tmp_path/(c.scope.venue_account_id+'.ndjson'),engine=c.engine,
        claim=claim,clock=lambda:c.c.facts.inputs.clock)
    return c


def submit(c):
    if c.permit is None:c.permit=c.gate.mark_may_have_sent(c.write.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        return c.engine.submit_creation(c.write,c.request,c.permit,boundary=c.gate,lease=c.lease,owner=c.owner)


def event(c,*,sequence=1,ask='81490',quantity='.4',kind='DEPTH_SNAPSHOT',event_id=None):
    from trading_system.engine.paper_market import PaperMarketEvent,DepthLevel
    return PaperMarketEvent(contract_version='2026-10-29',event_id=event_id or uuid4().hex,stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=sequence,kind=kind,
        bids=(DepthLevel(price='81489',quantity='1'),),asks=(DepthLevel(price=ask,quantity=quantity),),
        occurred_at=c.c.facts.inputs.clock.utc_now,source='PAPER_MARKET_REPLAY_SYNTHETIC')


def test_actual_v3_acceptance_shared_depth_partial_limit_restart_and_replay(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    market='shared-depth-'+uuid4().hex
    a=case(database,venue,tmp_path,market=market);b=case(database,venue,tmp_path,market=market)
    submit(a);submit(b)
    matcher=PaperMatcher(a.engine)
    first=event(a,ask='81500',quantity='1')
    assert matcher.apply_market_event(first).applied
    assert not a.engine.query_creation(a.write.command_id).fills
    cross=event(a,sequence=2,quantity='.6')
    assert matcher.apply_market_event(cross).applied
    ar=a.engine.query_creation(a.write.command_id);br=b.engine.query_creation(b.write.command_id)
    assert ar.filled_quantity==D('.6') and ar.remaining_quantity==D('.4') and br.filled_quantity==0
    assert ar.request==a.request and ar.fills[0].market_event_id==cross.event_id
    assert not PaperMatcher(a.engine).apply_market_event(cross).applied
    assert a.engine.query_creation(a.write.command_id)==ar
    # A new identical snapshot cannot supply another .6 to either order.
    matcher.apply_market_event(event(a,sequence=3,quantity='.6'))
    assert b.engine.query_creation(b.write.command_id).filled_quantity==0
    # Explicit positive depth delta is declared new model liquidity.
    matcher.apply_market_event(event(a,sequence=4,quantity='.7',kind='DEPTH_DELTA'))
    assert a.engine.query_creation(a.write.command_id).filled_quantity==1
    assert b.engine.query_creation(b.write.command_id).filled_quantity==D('.3')


@pytest.mark.parametrize('tif,expected,status',[('IOC','.4','EXPIRED'),('FOK','0','EXPIRED')])
def test_immediate_tif_uses_current_depth_without_fok_partial_consumption(database,venue,tmp_path,tif,expected,status):
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path,tif=tif)
    # New per-case market identity prevents cross-test model liquidity sharing.
    PaperMatcher(c.engine).apply_market_event(event(c,sequence=100+int(uuid4().hex[:4],16),quantity='.4'))
    submit(c)
    report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==D(expected) and report.status==status and report.remaining_quantity==0
    if tif=='FOK':
        following=case(database,venue,tmp_path,market=c.scope.instrument_uid)
        submit(following)
        assert following.engine.query_creation(following.write.command_id).filled_quantity==D('.4')


def test_post_only_rejects_crossing_without_fabricated_fill(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path,post_only=True)
    PaperMatcher(c.engine).apply_market_event(event(c,sequence=200000,quantity='1'))
    submit(c)
    report=c.engine.query_creation(c.write.command_id)
    assert report.status=='REJECTED' and not report.fills


def test_fake_or_reused_permit_and_runtime_market_mutation_are_denied(database,venue,tmp_path):
    import psycopg
    c=case(database,venue,tmp_path)
    c.permit=c.gate.mark_may_have_sent(c.write.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        with pytest.raises((ValueError,psycopg.Error)):
            c.engine.submit_creation(c.write,c.request,replace(c.permit,token='forged'),boundary=c.gate,lease=c.lease,owner=c.owner)
    assert c.engine.query_creation(c.write.command_id) is None
    # The protected wall-clock TTL continues elapsing during the failed proof;
    # exercise successful single consumption with a freshly admitted command.
    c=case(database,venue,tmp_path)
    submit(c)
    with pytest.raises((ValueError,psycopg.Error)):submit(c)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(venue.runtime_dsn) as db:db.execute('INSERT INTO public.paper_market_inputs DEFAULT VALUES')


def test_conflicting_market_event_is_rejected_with_original_fills_retained(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path);submit(c)
    e=event(c,sequence=300000)
    matcher=PaperMatcher(c.engine);matcher.apply_market_event(e)
    before=c.engine.query_creation(c.write.command_id)
    with pytest.raises(ValueError):matcher.apply_market_event(replace(e,asks=()))
    assert c.engine.query_creation(c.write.command_id)==before


def test_concurrent_duplicate_source_and_real_process_restart_do_not_reconsume(database,venue,tmp_path):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path);submit(c)
    e=event(c,quantity='.25')
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(lambda _:PaperMatcher(c.engine).apply_market_event(e),range(2)))
    assert sorted(r.applied for r in outcomes)==[False,True]
    assert c.engine.query_creation(c.write.command_id).filled_quantity==D('.25')
    second=event(c,sequence=2,kind='DEPTH_DELTA',quantity='.25')
    child=os.fork()
    if child==0:
        try:
            PaperMatcher(c.engine).apply_market_event(second)
        except BaseException:os._exit(71)
        os._exit(0)
    _,status=os.waitpid(child,0)
    assert os.waitstatus_to_exitcode(status)==0
    assert c.engine.query_creation(c.write.command_id).filled_quantity==D('.5')
    assert not PaperMatcher(c.engine).apply_market_event(second).applied


def test_resting_post_only_can_fill_on_later_market_event(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path,post_only=True);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,ask='81500',quantity='.4'));submit(c)
    assert c.engine.query_creation(c.write.command_id).status=='SUBMITTED'
    matcher.apply_market_event(event(c,sequence=2,quantity='.4'))
    report=c.engine.query_creation(c.write.command_id)
    assert report.status=='PARTIALLY_FILLED' and report.filled_quantity==D('.4')


def test_delta_that_crosses_the_persisted_book_is_atomic_data_rejection(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=case(database,venue,tmp_path);submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,ask='81500',quantity='.4'))
    before=c.engine.query_creation(c.write.command_id)
    invalid=replace(event(c,sequence=2,ask='81600',quantity='1',kind='DEPTH_DELTA'),
        bids=(DepthLevel(price='81501',quantity='1'),))
    with pytest.raises(ValueError,match='PAPER_CROSSED_SOURCE_BOOK'):matcher.apply_market_event(invalid)
    assert c.engine.query_creation(c.write.command_id)==before
