"""Real funded quote admission through the public PAPER execution boundary."""
from contextlib import contextmanager
from decimal import Decimal as D
from dataclasses import replace
import pytest
from integration.test_quote_current_boundary_postgres import database, base_database, isolated_postgres, admitted, gate
from integration.test_paper_market_matcher import venue, event, submit
from integration.test_program_commands_postgres import lease
from trading_system.execution.program_commands import ProgramCommandRepository


@pytest.fixture(scope='module')
def quote_venue(database,venue):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from trading_system.engine.paper_market_quote import provision_paper_quote
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(venue.runtime_dsn)['dbname']})
    provision_paper_quote(admin,venue)
    return venue


def execution_case(database, venue, tmp_path, *, side='BUY', fees=(), rate='0'):
    from trading_system.engine.paper import PaperEngine
    from trading_system.engine.paper_market import PaperMatchingModel
    from trading_system.runtime.execution_owner import ExecutionOwner
    from uuid import uuid4
    from integration.test_quote_financial_kernel_postgres import quote_case
    from integration.test_quote_program_admission_postgres import public
    from trading_system.contracts import types as t
    assets=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10')))
    options={'instrument_uid':'quote-'+uuid4().hex}
    if side=='SELL':options.update(actual='.15',quantity='.85')
    c=quote_case(database,real_options=options,quote_options={'side':side,'actual':'.15' if side=='SELL' else '0','fees':fees},quote_assets=assets)
    receipt=public(c).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan);c.cid=receipt.attachment.command_ids[0]
    with c.owner.transaction(c.scope) as tx:
        repo=ProgramCommandRepository(tx);c.write=repo.write(c.cid);c.request=repo.request(c.cid)
    c.gate=gate(c);c.lease=lease(c);c.permit=None
    def denied(*args):raise ValueError('legacy cannot authorize quote')
    c.engine=PaperEngine(c.scope,dsn=venue.runtime_dsn,event_dsn=venue.event_dsn,
        authority_guard=denied,owner_strategy_id=c.write.owner_strategy_id,
        quantity_unit='BASE',price_currency='USDT',clock=lambda:c.c.facts.inputs.clock,
        matching_model=PaperMatchingModel(model_id='ISOLATED_QUOTE_DEPTH_V1',quote_fee_rate=rate))
    @contextmanager
    def claim(shard):
        from integration.portable_owner_lock import exclusive_file_lock
        with exclusive_file_lock(tmp_path/(c.scope.venue_account_id+'.lock')):
            yield
    c.owner=ExecutionOwner(c.lease,journal_path=tmp_path/'quote.ndjson',engine=c.engine,
        claim=claim,clock=lambda:c.c.facts.inputs.clock)
    return c


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('price,expected_base,expected_gross',[('50000','.08','4000'),('60000','.066666','3999.96')])
def test_native_quote_request_executes_actual_depth_without_original_base_quantity(database,quote_venue,tmp_path,price,expected_base,expected_gross,side):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=execution_case(database,quote_venue,tmp_path,side=side)
    snapshot=replace(event(c),asks=(DepthLevel(price=price if side=='BUY' else '60001',quantity='.1'),),
        bids=(DepthLevel(price=price if side=='SELL' else '49999',quantity='.1'),))
    PaperMatcher(c.engine).apply_market_event(snapshot)
    submit(c)
    report=c.engine.query_creation(c.cid)
    assert dict(report.request.form_parameters())['quoteOrderQty']=='4000'
    assert 'quantity' not in dict(report.request.form_parameters())
    assert report.record_kind=='PAPER_QUOTE_REPORT_V1'
    assert report.actual_base==D(expected_base)
    assert report.gross_quote==D(expected_gross)
    assert report.remaining_quote==4000-D(expected_gross)
    assert sum((f.quantity*f.price for f in report.fills),D(0))==report.gross_quote





    from trading_system.execution.paper_quote_reports import verify_quote_report
    assert verify_quote_report(c.engine,report,boundary=c.gate).discrepancies==()
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        verify_quote_report(c.engine,replace(report,gross_quote=report.gross_quote+1),boundary=c.gate)


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('price,quantity,base',[('50000','.02','.02'),('49999','.1','0'),('60001','.1','0'),('50000','0','0')])
def test_partial_and_out_of_band_liquidity_remain_terminal_with_unused_budget(database,quote_venue,tmp_path,side,price,quantity,base):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    from trading_system.execution.paper_quote_reports import verify_quote_report
    from trading_system.storage.models import scope_id
    c=execution_case(database,quote_venue,tmp_path,side=side)
    snapshot=replace(event(c),asks=(DepthLevel(price=price if side=='BUY' else '70000',quantity=quantity),),
        bids=(DepthLevel(price=price if side=='SELL' else '40000',quantity=quantity),))
    matcher=PaperMatcher(c.engine);matcher.apply_market_event(snapshot);submit(c)
    report=c.engine.query_creation(c.cid)
    assert report.actual_base==D(base) and report.status=='EXPIRED'
    assert report.remaining_quote==4000-D(base)*D(price)
    assert not matcher.apply_market_event(snapshot).applied
    assert c.engine.query_creation(c.cid)==report
    assert verify_quote_report(c.engine,report,boundary=c.gate).discrepancies==()
    import psycopg
    def state():
        with c.gate.uow.transaction(c.scope) as tx:
            return (tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE command_id=%s',
                        (c.cid,)).fetchone()[0],
                    tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',
                        (scope_id(c.scope),)).fetchone()[0],
                    tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',
                        (scope_id(c.scope),)).fetchone()[0])
    before=state()
    with c.engine._connect() as db:
        venue_commands=db.execute('SELECT count(*) AS total FROM public.paper_engine_commands').fetchone()['total']
    # The committed original permit is single-use. Depending on whether the
    # bounded test work crosses the permit TTL, PostgreSQL rejects the second
    # consume either at the current-permit guard or at the single-use primary
    # key. Both are the existing database constraint, before any second send or
    # posting can occur.
    with pytest.raises((psycopg.errors.CheckViolation,psycopg.errors.UniqueViolation)) as denied:
        submit(c)
    message=str(denied.value)
    if isinstance(denied.value,psycopg.errors.CheckViolation):
        assert 'committed unused current permit required' in message
    else:
        assert 'program_permit_consumptions_pkey' in message
    assert before[0]==1
    assert state()==before
    with c.engine._connect() as db:
        assert db.execute('SELECT count(*) AS total FROM public.paper_engine_commands').fetchone()['total']==venue_commands
    assert c.engine.query_creation(c.cid)==report


def test_quote_reducer_tables_and_function_are_private(quote_venue):
    import psycopg
    for statement in ('UPDATE public.paper_quote_runtime_v1 SET actual_base=1',
        'INSERT INTO public.paper_quote_allocations_v1 DEFAULT VALUES',
        'DELETE FROM public.paper_quote_terms_v1'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(quote_venue.runtime_dsn) as db:db.execute(statement)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(quote_venue.event_dsn) as db:
            db.execute("SELECT public.paper_accept_quote_v1('x','y','z','m','{}')")


@pytest.mark.parametrize('fault',['expiry','fence'])
def test_quote_actual_lock_wait_does_not_renew_consumed_permit(database,quote_venue,tmp_path,fault):
    import psycopg,time
    from concurrent.futures import ThreadPoolExecutor
    from psycopg.conninfo import conninfo_to_dict
    from trading_system.engine.paper_market import market_key
    c=execution_case(database,quote_venue,tmp_path);clock=c.c.facts.inputs.clock
    dbname=conninfo_to_dict(quote_venue.runtime_dsn)['dbname']
    with psycopg.connect(quote_venue.event_dsn) as blocker:
        blocker.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(market_key(c.scope.product,c.scope.instrument_uid),))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(submit,c)
            try:
                with psycopg.connect(database) as observer:
                    for _ in range(300):
                        waiting=observer.execute("SELECT EXISTS(SELECT FROM pg_locks l JOIN pg_database d ON d.oid=l.database WHERE d.datname=%s AND NOT l.granted AND l.locktype='advisory')",(dbname,)).fetchone()[0]
                        if waiting:break
                        if pending.done():pending.result()
                        time.sleep(.02)
                    else:raise AssertionError('actual SIM advisory wait not observed')
                if fault=='expiry':
                    c.engine._clock=lambda:replace(clock,utc_now=c.permit.expires_at,monotonic_ns=c.permit.deadline_monotonic_ns)
                else:
                    from trading_system.intents.ingress import revision_key,IngressRevision
                    with c.service.uow.transaction(c.scope) as tx:
                        key=revision_key(c.qrecord.context);previous=tx._latest('audit_events',key,IngressRevision)
                        tx._append('audit_events',key,replace(previous,sequence=previous.sequence+1,enabled=False),previous.sequence)
            finally:blocker.commit()
            with pytest.raises(ValueError,match='PAPER_CONSUMED_PERMIT_EXPIRED|DEPLOYMENT_FENCE_REQUIRED|STALE_REVISION|PAPER_CURRENT_FENCE_CHANGED'):pending.result(timeout=30)
    assert c.engine.query_creation(c.cid) is None
    with c.gate.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE command_id=%s',(c.cid,)).fetchone()==(1,)


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_quote_external_fee_and_two_opposing_levels(database,quote_venue,tmp_path,side):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    from trading_system.risk.program_exposure import FeeBound
    from trading_system.contracts import types as t
    from trading_system.execution.paper_quote_reports import verify_quote_report
    from decimal import localcontext
    fees=(FeeBound(amount=t.Money(currency='USDT',amount=D(4)),debit='EXTERNAL_BALANCE'),)
    c=execution_case(database,quote_venue,tmp_path,side=side,fees=fees,rate='.001')
    levels=(DepthLevel(price='50000',quantity='.04'),DepthLevel(price='60000',quantity='.04'))
    snapshot=replace(event(c),asks=levels if side=='BUY' else (),bids=levels if side=='SELL' else ())
    PaperMatcher(c.engine).apply_market_event(snapshot)
    with localcontext() as ambient:
        ambient.prec=3
        submit(c);report=c.engine.query_creation(c.cid)
        assert verify_quote_report(c.engine,report,boundary=c.gate).discrepancies==()
    expected=D('3999.98') if side=='BUY' else D(4000)
    assert len(report.fills)==2 and report.gross_quote==expected
    assert sum((f.quote_fee for f in report.fills),D(0))==expected*D('.001')


def test_base_and_quote_orders_share_each_actual_visible_unit(database,quote_venue,tmp_path):
    from integration.test_paper_market_matcher import case
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=execution_case(database,quote_venue,tmp_path)
    def lower_limit(proposal):
        node=proposal.program.nodes[0]
        node=replace(node,order=replace(node.order,price=replace(node.order.price,value='50000')))
        return replace(proposal,program=replace(proposal.program,nodes=(node,)))
    base=case(database,quote_venue,tmp_path,market=c.scope.instrument_uid,quantity='.02',target='.02',
        stop='40000',plan_proposal_transform=lower_limit)
    submit(base)
    snapshot=replace(event(c),asks=(DepthLevel(price='50000',quantity='.05'),),bids=())
    PaperMatcher(c.engine).apply_market_event(snapshot);submit(c)
    assert base.engine.query_creation(base.write.command_id).filled_quantity==D('.02')
    assert c.engine.query_creation(c.cid).actual_base==D('.03')
    with c.engine._connect() as db:
        assert db.execute("SELECT available FROM public.paper_market_levels WHERE market_key=%s AND side='ASK'",(snapshot.market_key,)).fetchone()['available']==0

@pytest.mark.parametrize('side',['BUY','SELL'])
def test_actual_insufficient_quote_or_base_capacity_denies_before_command(database,side):
    from uuid import uuid4
    from integration.test_quote_financial_kernel_postgres import quote_case
    from integration.test_quote_program_admission_postgres import public
    from trading_system.contracts import types as t
    options={'instrument_uid':'denial-'+uuid4().hex}
    if side=='SELL':options.update(actual='.15',quantity='.85')
    assets=(t.Money(currency='USDT',amount=D(1) if side=='BUY' else D('1000000')),
        t.Money(currency='BTC',amount=D('.001')))
    c=quote_case(database,real_options=options,quote_options={'side':side,'actual':'.15' if side=='SELL' else '0'},quote_assets=assets)
    with pytest.raises(ValueError):public(c).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.qrecord.intent_id) is None
        assert tx.reservations.for_intent(c.qrecord.intent_id)==()


@pytest.mark.parametrize('fault',['request','terms','fill','runtime'])
def test_resealed_quote_dto_never_substitutes_for_source(database,quote_venue,tmp_path,fault):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel,digest
    from trading_system.execution.paper_quote_reports import verify_quote_report
    c=execution_case(database,quote_venue,tmp_path)
    PaperMatcher(c.engine).apply_market_event(replace(event(c),asks=(DepthLevel(price='50000',quantity='.1'),),bids=()))
    submit(c);report=c.engine.query_creation(c.cid)
    if fault=='request':changed=replace(report,request=replace(report.request,command_id='forged'))
    elif fault=='terms':changed=replace(report,terms=replace(report.terms,requested_quote='5000'))
    elif fault=='fill':changed=replace(report,fills=(replace(report.fills[0],quantity=D('.1')),))
    else:changed=replace(report,status='EXPIRED')
    changed=replace(changed,evidence=replace(changed.evidence,hash=digest(changed)))
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):verify_quote_report(c.engine,changed,boundary=c.gate)



@pytest.mark.parametrize('fault',['missing_depth','fee_asset','fee_overrun','owner'])
def test_quote_missing_execution_evidence_and_owner_leave_no_sim_order(database,quote_venue,tmp_path,fault):
    from trading_system.risk.program_exposure import FeeBound
    from trading_system.contracts import types as t
    fees=(FeeBound(amount=t.Money(currency='BTC',amount=D('.000001')),debit='FROM_RECEIPT'),) if fault=='fee_asset' else ()
    c=execution_case(database,quote_venue,tmp_path,fees=fees,rate='.01' if fault=='fee_overrun' else '0')
    if fault=='owner':
        c.permit=c.gate.mark_may_have_sent(c.cid,c.lease)
        with c.owner.session(c.lease.shard):
            with pytest.raises(ValueError,match='BOUNDARY_OWNER_REQUIRED'):
                c.engine.submit_creation(c.write,c.request,c.permit,boundary=c.gate,lease=c.lease,owner=object())
    else:
        with pytest.raises(ValueError,match='DEPTH_REQUIRED|FEE_MODEL_UNSUPPORTED|FEE_MODEL_EXCEEDS'):
            submit(c)
    assert c.engine.query_creation(c.cid) is None

@pytest.mark.parametrize('changed',['client','market'])
def test_independent_quote_verifier_binds_actual_sql_row_identity(database,quote_venue,tmp_path,changed):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from psycopg.types.json import Jsonb
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=execution_case(database,quote_venue,tmp_path)
    PaperMatcher(c.engine).apply_market_event(replace(event(c),asks=(DepthLevel(price='50000',quantity='.1'),),bids=()))
    submit(c)
    # Administrator adversarial corruption of source identity, not a DTO forgery.
    # A real runtime cannot disable immutable source triggers (tested separately).
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(quote_venue.runtime_dsn)['dbname']})
    with psycopg.connect(admin) as db:
        db.execute('ALTER TABLE public.paper_quote_terms_v1 DISABLE TRIGGER immutable_quote_terms')
        column='client_id' if changed=='client' else 'market_key'
        from psycopg import sql
        db.execute(sql.SQL('UPDATE public.paper_quote_terms_v1 SET {}=%s WHERE command_id=%s').format(sql.Identifier(column)),('forged',c.cid))
        db.execute('ALTER TABLE public.paper_quote_terms_v1 ENABLE TRIGGER immutable_quote_terms')
    with pytest.raises(ValueError,match='SOURCE_BINDING_CHANGED'):c.engine.query_creation(c.cid)
