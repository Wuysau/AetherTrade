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


def case(database,venue,tmp_path,*,tif='GTC',post_only=False,market=None,quote_fee_rate='0',node_fees=None,**risk_options):
    from trading_system.engine.paper import PaperEngine
    from trading_system.engine.paper_market import PaperMatchingModel
    from trading_system.runtime.execution_owner import ExecutionOwner
    from trading_system.contracts.order_program import TimeInForceSpec
    def transform(p):
        n=p.program.nodes[0]
        return replace(p,program=replace(p.program,nodes=(replace(n,order=replace(n.order,
            tif=TimeInForceSpec(kind='NONE' if post_only else tif,expires_at=None),post_only=post_only)),)))
    c=real_case(database,proposal_transform=transform,instrument_uid=market or 'isolated-market-'+uuid4().hex,node_fees=node_fees,**risk_options)
    c.write,c.request,c.bundle=c.service._transaction(lambda tx:attach(c,tx))
    c.gate=boundary(c);c.lease=lease(c);c.permit=None
    def unavailable(*args):raise ValueError('legacy guard never authorizes V3')
    c.engine=PaperEngine(c.scope,dsn=venue.runtime_dsn,event_dsn=venue.event_dsn,authority_guard=unavailable,
        owner_strategy_id=c.bundle.intent.owner_strategy_id,quantity_unit=c.bundle.intent.unit.native_unit,
        price_currency=c.bundle.archive.rules.quote_asset,clock=lambda:c.c.facts.inputs.clock,
        matching_model=PaperMatchingModel(model_id='ISOLATED_DEPTH_FIFO_EXPLICIT_FEE_FIXTURE',quote_fee_rate=quote_fee_rate))
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
    from hashlib import sha256
    assert sha256(ar.fills[0].raw_payload_json.encode('utf-8')).hexdigest()==ar.fills[0].evidence.hash
    assert ar.orders[0].venue_order_id
    assert not PaperMatcher(a.engine).apply_market_event(cross).applied
    assert a.engine.query_creation(a.write.command_id)==ar
    # A new identical snapshot cannot supply another .6 to either order.
    matcher.apply_market_event(event(a,sequence=3,quantity='.6'))
    assert b.engine.query_creation(b.write.command_id).filled_quantity==0
    # Explicit positive depth delta is declared new model liquidity.
    matcher.apply_market_event(event(a,sequence=4,quantity='.7',kind='DEPTH_DELTA'))
    assert a.engine.query_creation(a.write.command_id).filled_quantity==1
    assert b.engine.query_creation(b.write.command_id).filled_quantity==D('.3')
    report=a.engine.query_creation(a.write.command_id)
    context=report.accepted_contexts[0]
    assert context.request==a.request and context.compilation==a.bundle.archive.compilation
    assert context.rules==a.bundle.archive.rules and context.terms.side=='BUY'
    proof=a.engine.query_market_evidence(a.write.command_id)
    assert proof.request_hash==canonical_hash(domain_payload(a.request)) and len(proof.inputs)==2
    assert len(proof.consumptions)==3 and sum((r.quantity for r in proof.consumptions),D(0))==D('1.3')
    assert {r.scope.venue_account_id for r in proof.consumptions}=={a.scope.venue_account_id,b.scope.venue_account_id}
    assert proof.inputs[1].before_levels


@pytest.mark.parametrize('tif,expected,status',[('IOC','.4','EXPIRED'),('FOK','0','EXPIRED')])
def test_immediate_tif_uses_current_depth_without_fok_partial_consumption(database,venue,tmp_path,tif,expected,status):
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path,tif=tif)
    # New per-case market identity prevents cross-test model liquidity sharing.
    PaperMatcher(c.engine).apply_market_event(event(c,sequence=100+int(uuid4().hex[:4],16),quantity='.4'))
    submit(c)
    report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==D(expected) and report.status==status and report.remaining_quantity==0
    assert report.dispositions and report.dispositions[-1].status=='EXPIRED'
    from hashlib import sha256
    assert sha256(report.dispositions[-1].raw_payload_json.encode()).hexdigest()==report.dispositions[-1].evidence.hash
    assert len(c.engine.query_market_evidence(c.write.command_id).inputs)==1
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
    assert report.orders[0].venue_order_id and report.dispositions[-1].reason=='POST_ONLY_WOULD_TAKE'
    assert len(c.engine.query_market_evidence(c.write.command_id).inputs)==1


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


def test_matcher_definer_and_runtime_keep_actual_sql_role_boundaries(venue):
    import psycopg
    with psycopg.connect(venue.runtime_dsn) as db:
        row=db.execute("SELECT p.prosecdef,p.proconfig,r.rolcanlogin,r.rolsuper,r.rolinherit FROM pg_proc p "
            "JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid='public.paper_match_order(text,text)'::regprocedure").fetchone()
        assert row==(True,['search_path=pg_catalog'],False,False,False)
        assert db.execute("SELECT r.rolcanlogin FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner "
            "WHERE p.oid='public.paper_market_immutable()'::regprocedure").fetchone()==(False,)
        assert not db.execute("SELECT coalesce(bool_or(a.grantee=0 AND a.privilege_type='EXECUTE'),false) FROM pg_proc p, "
            "LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE p.oid='public.paper_match_order(text,text)'::regprocedure").fetchone()[0]
    for statement in ('UPDATE public.paper_engine_orders SET filled=0',
                      "UPDATE public.paper_order_terms SET terms='{}'::jsonb",
                      'UPDATE public.paper_market_levels SET available=100'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(venue.runtime_dsn) as db:db.execute(statement)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(venue.event_dsn) as db:db.execute('INSERT INTO public.paper_engine_commands DEFAULT VALUES')


def test_unbacked_projection_fill_is_not_normalized_as_market_evidence(database,venue,tmp_path):
    import psycopg
    c=case(database,venue,tmp_path);submit(c)
    with psycopg.connect(venue.event_dsn) as db:
        db.execute('UPDATE public.paper_engine_orders SET filled=%s WHERE scope_key=%s AND client_id=%s',
            (D('.1'),c.engine._scope_key,c.request.assigned_ids.members[0].client_id))
    with pytest.raises(ValueError,match='PAPER_FILL_SOURCE_INCOMPLETE'):
        c.engine.query_creation(c.write.command_id)


def test_quote_fee_is_actual_source_segregated_and_bounded_by_real_admission(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.risk.program_exposure import FeeBound
    fees=(FeeBound(amount=t.Money(currency='USDT',amount=D('100')),debit='EXTERNAL_BALANCE'),)
    c=case(database,venue,tmp_path,quote_fee_rate='.001',node_fees=fees);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.5'))
    report=c.engine.query_creation(c.write.command_id);fill=report.fills[0]
    assert fill.quantity_unit==c.bundle.intent.unit.native_unit and fill.price_asset=='USDT'
    assert fill.gross_notional==D('40745') and fill.fees[0].asset=='USDT' and D(fill.fees[0].amount)==D('40.745')
    assert fill.fees[0].position_effect=='NO_POSITION_DEBIT'
    unreserved=case(database,venue,tmp_path,quote_fee_rate='.001')
    with pytest.raises(ValueError,match='PAPER_FEE_MODEL_EXCEEDS_ADMITTED_BOUND'):submit(unreserved)
    assert unreserved.engine.query_creation(unreserved.write.command_id) is None


def test_existing_book_fill_cannot_precede_actual_order_acceptance(database,venue,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path)
    raw=event(c,quantity='1');PaperMatcher(c.engine).apply_market_event(raw)
    accepted=(utc_timestamp(c.c.facts.inputs.clock.utc_now)+timedelta(seconds=1)).isoformat()
    current=replace(c.c.facts.inputs.clock,utc_now=accepted,monotonic_ns=c.c.facts.inputs.clock.monotonic_ns+10**9)
    c.engine._clock=lambda:current;c.owner.clock=lambda:current
    submit(c)
    report=c.engine.query_creation(c.write.command_id)
    assert utc_timestamp(report.fills[0].occurred_at)>=utc_timestamp(accepted)
    assert c.engine.query_market_evidence(c.write.command_id).inputs[0].event.occurred_at==raw.occurred_at


def test_structured_fill_identity_handles_valid_multi_level_sell_collisions(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=case(database,venue,tmp_path,product=t.Product.USD_M_PERP,actual='10.3',target='0',quantity='10.3',side='SELL',reduce_only=True)
    submit(c)
    raw=replace(event(c,ask='81500',quantity='1'),bids=(DepthLevel(price='81490.11',quantity='.3'),DepthLevel(price='81490.1',quantity='10')))
    PaperMatcher(c.engine).apply_market_event(raw)
    report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==D('10.3') and len(report.fills)==2
    assert len({f.fill_id for f in report.fills})==2


def test_original_4000_quote_limit_uses_compiled_native_quantity_without_rewriting_source(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path,preserve_quote_limit=True)
    assert c.bundle.intent.proposal.target_value=='4000'
    original=c.bundle.archive.compilation.nodes[0].original.order
    assert original.quantity.kind=='QUOTE_NOTIONAL' and original.quantity.value=='4000'
    assert original.price.value=='81490.00' and c.bundle.intent.proposal.exit_plan.stop_price=='81330.00'
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,ask='81500',quantity='1'))
    assert not c.engine.query_creation(c.write.command_id).fills
    matcher.apply_market_event(event(c,sequence=2,quantity='.02'))
    report=c.engine.query_creation(c.write.command_id)
    assert report.orders[0].quantity==D('.049085') and report.filled_quantity==D('.02') and report.remaining_quantity==D('.029085')
    assert report.accepted_contexts[0].compilation.nodes[0].original.order.quantity.kind=='QUOTE_NOTIONAL'


def test_consumed_permit_cannot_start_a_side_effect_after_actual_sim_lock_wait(database,venue,tmp_path):
    import psycopg,time
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from psycopg.conninfo import conninfo_to_dict
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.engine.paper_market import market_key
    c=case(database,venue,tmp_path);key=market_key(c.scope.product,c.scope.instrument_uid)
    dbname=conninfo_to_dict(venue.runtime_dsn)['dbname']
    with psycopg.connect(venue.event_dsn) as blocker:
        blocker.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(submit,c)
            deadline=time.monotonic()+30
            with psycopg.connect(database) as observer:
                while time.monotonic()<deadline:
                    waiting=observer.execute('SELECT EXISTS(SELECT FROM pg_locks l JOIN pg_database d ON d.oid=l.database '
                        "WHERE d.datname=%s AND l.locktype='advisory' AND NOT l.granted)",(dbname,)).fetchone()[0]
                    if waiting:break
                    if pending.done():pending.result()
                    time.sleep(.02)
                else:raise AssertionError('actual SIM advisory wait not observed')
            # This is a healthy clock advance, while its execution-owner lease
            # still has nearly a minute. It does not create or renew a permit.
            current=replace(c.c.facts.inputs.clock,utc_now=(utc_timestamp(c.c.facts.inputs.clock.utc_now)+timedelta(seconds=6)).isoformat(),
                monotonic_ns=c.c.facts.inputs.clock.monotonic_ns+6*10**9)
            c.engine._clock=lambda:current;c.owner.clock=lambda:current
            blocker.commit()
            with pytest.raises(ValueError,match='PAPER_CONSUMED_PERMIT_EXPIRED'):pending.result(timeout=30)
    assert c.engine.query_creation(c.write.command_id) is None
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE command_id=%s',
            (c.write.command_id,)).fetchone()==(1,)


def test_disabled_actual_source_fence_after_consume_blocks_sim_side_effect(database,venue,tmp_path):
    import psycopg,time
    from concurrent.futures import ThreadPoolExecutor
    from psycopg.conninfo import conninfo_to_dict
    from trading_system.engine.paper_market import market_key
    from trading_system.intents.ingress import revision_key,IngressRevision
    c=case(database,venue,tmp_path);dbname=conninfo_to_dict(venue.runtime_dsn)['dbname']
    with psycopg.connect(venue.event_dsn) as blocker:
        blocker.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
            (market_key(c.scope.product,c.scope.instrument_uid),))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(submit,c);deadline=time.monotonic()+30
            with psycopg.connect(database) as observer:
                while time.monotonic()<deadline:
                    if observer.execute('SELECT EXISTS(SELECT FROM pg_locks l JOIN pg_database d ON d.oid=l.database '
                            "WHERE d.datname=%s AND l.locktype='advisory' AND NOT l.granted)",(dbname,)).fetchone()[0]:break
                    if pending.done():pending.result()
                    time.sleep(.02)
                else:raise AssertionError('actual SIM advisory wait not observed')
            with c.service.uow.transaction(c.scope) as tx:
                key=revision_key(c.bundle.archive.source.context)
                previous=tx._latest('audit_events',key,IngressRevision)
                tx._append('audit_events',key,replace(previous,sequence=previous.sequence+1,enabled=False),previous.sequence)
            blocker.commit()
            with pytest.raises(ValueError,match='DEPLOYMENT_FENCE_REQUIRED|STALE_REVISION|PAPER_CURRENT_FENCE_CHANGED'):
                pending.result(timeout=30)
    assert c.engine.query_creation(c.write.command_id) is None


def test_depth_delta_never_rounds_tiny_unexecutable_residual_into_a_fill(database,venue,tmp_path):
    from decimal import localcontext
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path);submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,quantity='.9999999999'))
    assert c.engine.query_creation(c.write.command_id).filled_quantity==D('.999999')
    with localcontext() as ambient:
        ambient.prec=3
        matcher.apply_market_event(event(c,sequence=2,quantity='.00000000001',kind='DEPTH_DELTA'))
        report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==D('.999999') and report.remaining_quantity==D('.000001')
    source=c.engine.query_market_evidence(c.write.command_id)
    assert sum((q.quantity for q in source.consumptions),D(0))==D('.999999')


@pytest.mark.parametrize('label',['expiry','deadline','both'])
def test_paper_side_effect_rechecks_the_committed_original_permit_labels(database,venue,tmp_path,label):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    c=case(database,venue,tmp_path)
    c.permit=c.gate.mark_may_have_sent(c.write.command_id,c.lease)
    changed=c.permit
    if label in ('expiry','both'):changed=replace(changed,
        expires_at=(utc_timestamp(changed.expires_at)+timedelta(milliseconds=1)).isoformat())
    if label in ('deadline','both'):changed=replace(changed,
        deadline_monotonic_ns=changed.deadline_monotonic_ns+1_000_000)
    with c.owner.session(c.lease.shard):
        with pytest.raises(ValueError,match='ORIGINAL_PERMIT_WINDOW_CHANGED'):
            c.engine.submit_creation(c.write,c.request,changed,boundary=c.gate,lease=c.lease,owner=c.owner)
    assert c.engine.query_creation(c.write.command_id) is None


def test_healthy_accept_clock_cannot_precede_the_current_observed_market_book(database,venue,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path);clock=c.c.facts.inputs.clock
    c.engine._clock=lambda:replace(clock,utc_now=(utc_timestamp(clock.utc_now)+timedelta(seconds=1)).isoformat(),
        monotonic_ns=clock.monotonic_ns+10**9)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.2'))
    c.engine._clock=lambda:clock
    with pytest.raises(ValueError,match='PAPER_MARKET_CLOCK_AHEAD_OF_ACCEPTANCE'):submit(c)
    assert c.engine.query_creation(c.write.command_id) is None


def test_original_protected_fee_ceiling_is_copied_exactly_under_low_ambient_precision(database,venue,tmp_path):
    from decimal import localcontext
    from trading_system.risk.program_exposure import FeeBound
    c=case(database,venue,tmp_path,node_fees=(FeeBound(
        amount=t.Money(currency='USDT',amount=D('.1119')),debit='EXTERNAL_BALANCE'),))
    with localcontext() as ambient:
        ambient.prec=3
        submit(c)
    context=c.engine.query_creation(c.write.command_id).accepted_contexts[0]
    assert context.execution_envelope.fee_ceilings[0].amount=='0.1119'
