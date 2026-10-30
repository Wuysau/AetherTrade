"""Additive native parent qualification against funded current ROOT."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from trading_system.contracts import types as t
from integration.test_paper_market_matcher_v2 import isolated_postgres,database,venue,venue_v2,advanced_case,event,submit

@pytest.mark.parametrize('product',[t.Product.SPOT,t.Product.USD_M_PERP])
def test_native_twap_accepts_real_parent_without_a_fictitious_initial_ordinary_order_and_recovers_pool(database,venue_v2,tmp_path,product):
    from trading_system.engine.paper import PaperEngine
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE',product=product)
    receipt=submit(c);report=c.engine.query_creation(c.write.command_id)
    assert receipt.client_ids==(c.request.assigned_ids.parent_client_id,) and not c.request.assigned_ids.members
    assert report.status=='WAITING_MARKET_SOURCE' and not report.orders and not report.fills
    assert report.native_parent.parent_client_id==c.request.assigned_ids.parent_client_id and report.native_parent.venue_parent_id
    assert report.pool.original_quantity==report.pool.remaining_pool_quantity==1
    assert report.pool.allocated_quantity==report.pool.executed_quantity==report.pool.unused_quantity==0
    assert report.accepted_contexts[0].request==c.request
    with c.engine._connect() as db:
        assert db.execute('SELECT target_client_id FROM public.paper_engine_commands WHERE scope_key=%s AND command_id=%s',
            (c.engine._scope_key,c.write.command_id)).fetchone()['target_client_id'] is None
        assert db.execute('SELECT count(*) AS n FROM public.paper_engine_orders WHERE scope_key=%s',(c.engine._scope_key,)).fetchone()['n']==0
    recreated=PaperEngine(c.scope,dsn=venue_v2.runtime_dsn,event_dsn=venue_v2.event_dsn,
        authority_guard=lambda _:None,owner_strategy_id=c.bundle.intent.owner_strategy_id,quantity_unit=c.bundle.intent.unit.native_unit,
        price_currency=c.bundle.archive.rules.quote_asset,clock=lambda:c.c.facts.inputs.clock,matching_model=c.engine._matching_model)
    assert recreated.query_creation(c.write.command_id)==report
    recreated._matching_model=None
    with pytest.raises(ValueError,match='PAPER_NATIVE_PARENT_READER_REQUIRED'):recreated.query_creation(c.write.command_id)


def test_native_parent_pool_cannot_change_its_protected_gross_ceiling_on_read(database,venue_v2,tmp_path):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE');submit(c)
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(venue_v2.runtime_dsn)['dbname']})
    with psycopg.connect(admin) as db:
        db.execute('UPDATE public.paper_native_algorithm_progress_v2 SET maximum_gross_notional=maximum_gross_notional+1 '
            'WHERE scope_key=%s AND parent_client_id=%s',(c.engine._scope_key,c.request.assigned_ids.parent_client_id))
    with pytest.raises(ValueError,match='PAPER_NATIVE_POOL_SOURCE_CHANGED'):c.engine.query_creation(c.write.command_id)
    service,codec=native_verifier(c,tmp_path/'invalid-source-raw')
    try:
        result=service.verify(c.write.command_id)
        assert not result.source_verified and result.raw_refs
    finally:codec.close()


def test_nullable_native_target_does_not_weaken_ordinary_or_incomplete_parent_command_constraints(database,venue_v2,tmp_path):
    import psycopg
    from psycopg.types.json import Jsonb
    from trading_system.contracts.canonical import domain_payload
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE');submit(c)
    for operation,request in [('SUBMIT',{}),('CREATE_REQUEST',domain_payload(c.request))]:
        with pytest.raises(psycopg.IntegrityError):
            with psycopg.connect(venue_v2.runtime_dsn) as db:
                # A null target alone does not manufacture a native parent.
                db.execute('INSERT INTO public.paper_engine_commands VALUES(%s,%s,%s,%s,NULL,%s,%s,%s)',
                    ('PAPER',c.engine._scope_key,'unbacked-'+operation,'f'*32,operation,'f'*64,Jsonb(request)))
    from trading_system.execution.creation_requests import assign_client_ids,materialize_creation
    from trading_system.engine.paper_market import digest
    p=c.bundle.archive.compilation
    unsigned=materialize_creation(p,0,c.bundle.archive.profile,assign_client_ids(p,0,'unbacked-valid-native-parent'))
    with pytest.raises(psycopg.errors.RaiseException,match='complete genuine pool'):
        with psycopg.connect(venue_v2.runtime_dsn) as db:
            db.execute('INSERT INTO public.paper_engine_commands VALUES(%s,%s,%s,%s,NULL,%s,%s,%s)',
                ('PAPER',c.engine._scope_key,unsigned.command_id,unsigned.assigned_ids.parent_client_id,'CREATE_REQUEST',
                    digest(unsigned),Jsonb(domain_payload(unsigned))))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(venue_v2.runtime_dsn) as db:db.execute('UPDATE public.paper_native_algorithm_progress_v2 SET allocated_quantity=0')


@pytest.mark.parametrize('template',['ATOMIC','TWAP'])
def test_actual_send_copies_protected_asset_fee_ceilings_exactly_under_low_caller_precision(database,venue_v2,tmp_path,template):
    from decimal import localcontext
    from trading_system.risk.program_exposure import FeeBound
    c=advanced_case(database,venue_v2,tmp_path,template=template,tif='NONE' if template=='TWAP' else 'GTC',
        node_fees=(FeeBound(amount=t.Money(currency='USDT',amount=D('0.1119')),debit='EXTERNAL_BALANCE'),))
    with localcontext() as arithmetic:
        arithmetic.prec=3
        submit(c)
        report=c.engine.query_creation(c.write.command_id)
    assert report.accepted_contexts[0].execution_envelope.fee_ceilings[0].amount=='0.1119'


@pytest.mark.parametrize('product',[t.Product.SPOT,t.Product.USD_M_PERP])
def test_native_twap_generates_observed_children_once_from_persisted_clock_and_shared_depth_pool(database,venue_v2,tmp_path,product):
    import psycopg
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.engine.paper_market_v2 import PaperClockEventV2
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE',product=product);submit(c)
    matcher=PaperMatcher(c.engine);snapshot=event(c,quantity='0.4');matcher.apply_market_event(snapshot)
    first=c.engine.query_creation(c.write.command_id)
    assert len(first.orders)==1 and first.fills[0].quantity==D('0.2')
    assert first.pool.allocated_quantity==first.pool.executed_quantity==D('0.2') and first.pool.remaining_pool_quantity==D('0.8')
    assert first.orders[0].namespace==('BINANCE_SPOT_ORDINARY' if product==t.Product.SPOT else 'BINANCE_USD_M_ORDINARY')
    assert first.orders[0].quantity_unit==c.bundle.intent.unit.native_unit
    assert first.accepted_contexts[0].request==c.request and first.request==c.request
    assert not matcher.apply_market_event(snapshot).applied
    assert c.engine.query_creation(c.write.command_id)==first
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.engine._connect() as db:db.execute('SELECT public.paper_advance_native_algorithms_v2(%s)',(snapshot.market_key,))
    with c.engine._connect() as db:db.execute('SELECT public.paper_match_order(%s,%s)',(c.engine._scope_key,first.orders[0].client_id))
    assert c.engine.query_creation(c.write.command_id)==first
    base=c.c.facts.inputs.clock
    for sequence,seconds in ((2,60),(3,120)):
        when=(utc_timestamp(base.utc_now)+timedelta(seconds=seconds)).isoformat()
        c.engine._clock=lambda when=when,seconds=seconds:replace(base,utc_now=when,monotonic_ns=base.monotonic_ns+seconds*10**9)
        matcher.apply_market_event(PaperClockEventV2(contract_version='2026-10-30',event_id='twap-clock-'+str(sequence),
            stream_id=c.scope.instrument_uid,product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=sequence,
            kind='CLOCK',occurred_at=when,source='PAPER_MARKET_REPLAY_SYNTHETIC'))
    report=c.engine.query_creation(c.write.command_id)
    assert len(report.orders)==3 and report.pool.allocated_quantity==D('0.6')
    assert report.pool.executed_quantity==D('0.4') and report.pool.unused_quantity==D('0.2') and report.pool.remaining_pool_quantity==D('0.4')
    assert sum((f.quantity for f in report.fills),D(0))==D('0.4')
    assert report.status=='EXECUTING'
    proof=c.engine.query_market_evidence(c.write.command_id)
    assert sum((a.quantity for a in proof.consumptions),D(0))==D('0.4')
    with c.engine._connect() as db:
        assert db.execute('SELECT count(*) AS n FROM public.paper_engine_commands WHERE scope_key=%s',(c.engine._scope_key,)).fetchone()['n']==1


def test_native_generation_cannot_execute_after_a_mutable_pool_ceiling_diverges_from_immutable_parent_source(database,venue_v2,tmp_path):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from trading_system.engine.paper_market import PaperMatcher
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE');submit(c)
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(venue_v2.runtime_dsn)['dbname']})
    with psycopg.connect(admin) as db:
        db.execute('UPDATE public.paper_native_algorithm_progress_v2 SET maximum_gross_notional=maximum_gross_notional+1 '
            'WHERE scope_key=%s AND parent_client_id=%s',(c.engine._scope_key,c.request.assigned_ids.parent_client_id))
    with pytest.raises(psycopg.errors.RaiseException,match='native pool immutable source changed'):
        PaperMatcher(c.engine).apply_market_event(event(c,quantity='1'))
    with c.engine._connect() as db:
        assert db.execute('SELECT count(*) AS n FROM public.paper_native_child_allocations_v2 WHERE scope_key=%s',(c.engine._scope_key,)).fetchone()['n']==0


def test_native_reduce_only_parent_holds_real_pool_when_current_venue_position_facts_are_missing(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE',product=t.Product.USD_M_PERP,
        actual='1',target='0',side='SELL',reduce_only=True)
    submit(c)
    assert c.request.assigned_ids.parent_client_id
    PaperMatcher(c.engine).apply_market_event(replace(event(c,ask='81500',quantity='1'),bids=(DepthLevel(price='81490',quantity='1'),)))
    report=c.engine.query_creation(c.write.command_id)
    assert report.status=='WAITING_POSITION_SOURCE' and not report.orders and not report.fills
    assert report.pool.original_quantity==report.pool.remaining_pool_quantity==1 and report.pool.allocated_quantity==0
    assert report.accepted_contexts[0].compilation.nodes[0].original.order.reduce_only is True


def position_source(c,sequence,quantity,through):
    from trading_system.engine.paper_market_v2 import PaperPositionEventV2
    return PaperPositionEventV2(contract_version='2026-10-30',event_id='position-'+str(sequence),stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=sequence,kind='ACCOUNT_POSITION',
        venue_account_id=c.scope.venue_account_id,venue_leg=c.scope.venue_leg,quantity_unit=c.bundle.intent.unit.native_unit,
        notional_multiplier=c.bundle.archive.compilation.nodes[0].quantity_multiplier,reported_quantity=quantity,through_sequence=through,
        occurred_at=c.c.facts.inputs.clock.utc_now,source='PAPER_MARKET_REPLAY_SYNTHETIC')


def test_actual_position_snapshot_and_fill_projection_constrain_native_reduce_only_without_lending_another_position(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE',product=t.Product.USD_M_PERP,
        actual='1',target='0',side='SELL',reduce_only=True);submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(position_source(c,1,'1',0))
    matcher.apply_market_event(replace(event(c,sequence=2,ask='81500',quantity='1'),bids=(DepthLevel(price='81490',quantity='1'),)))
    partial=c.engine.query_creation(c.write.command_id)
    assert partial.pool.executed_quantity==D('0.2') and len(partial.fills)==1
    with c.engine._connect() as db:
        row=db.execute('SELECT quantity,known,discrepancy FROM public.paper_venue_positions_v2 WHERE account_id=%s',
            (c.scope.venue_account_id,)).fetchone()
        assert row['quantity']==D('0.8') and row['known'] and row['discrepancy'] is None
    matcher.apply_market_event(position_source(c,3,'0',2))
    stopped=c.engine.query_creation(c.write.command_id)
    assert stopped.status=='STOPPED_NO_POSITION' and len(stopped.orders)==1 and stopped.pool.executed_quantity==D('0.2')
    assert stopped.pool.remaining_pool_quantity==D('0.8')
    proof=c.engine.query_market_evidence(c.write.command_id)
    assert {i.event.event_id for i in proof.inputs if i.event.kind=='ACCOUNT_POSITION'}=={'position-1','position-3'}


def test_stale_position_snapshot_replays_actual_fill_tail_and_conflicting_watermark_retains_unknown_facts(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE',product=t.Product.USD_M_PERP,
        actual='1',target='0',side='SELL',reduce_only=True);submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(position_source(c,1,'1',0))
    matcher.apply_market_event(replace(event(c,sequence=2,ask='81500',quantity='1'),bids=(DepthLevel(price='81490',quantity='1'),)))
    matcher.apply_market_event(position_source(c,3,'1',0))
    with c.engine._connect() as db:
        row=db.execute('SELECT quantity,known FROM public.paper_venue_positions_v2 WHERE account_id=%s',(c.scope.venue_account_id,)).fetchone()
        assert row['quantity']==D('0.8') and row['known']
    matcher.apply_market_event(position_source(c,4,'0.9',0))
    report=c.engine.query_creation(c.write.command_id)
    assert report.status=='WAITING_POSITION_SOURCE' and report.pool.executed_quantity==D('0.2')
    with c.engine._connect() as db:
        row=db.execute('SELECT quantity,known FROM public.paper_venue_positions_v2 WHERE account_id=%s',(c.scope.venue_account_id,)).fetchone()
        assert row['quantity']==D('0.8') and not row['known']
    assert any(i.event.event_id=='position-4' for i in c.engine.query_market_evidence(c.write.command_id).inputs)


def test_actual_reduce_only_position_capacity_is_debited_between_depth_levels(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=advanced_case(database,venue_v2,tmp_path,order_transform=lambda o:replace(o,price=replace(o.price,value='81489')),
        product=t.Product.USD_M_PERP,actual='1',target='0',side='SELL',reduce_only=True)
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(position_source(c,1,'0.15',0))
    matcher.apply_market_event(replace(event(c,sequence=2,ask='81500',quantity='1'),bids=(
        DepthLevel(price='81490',quantity='0.1'),DepthLevel(price='81489',quantity='0.1'))))
    report=c.engine.query_creation(c.write.command_id)
    assert {f.price:f.quantity for f in report.fills}=={D('81490'):D('0.1'),D('81489'):D('0.05')}
    assert report.filled_quantity==D('0.15') and report.remaining_quantity==D('0.85')
    assert report.status=='PARTIALLY_FILLED'
    with c.engine._connect() as db:
        row=db.execute('SELECT quantity,known,discrepancy FROM public.paper_venue_positions_v2 WHERE account_id=%s',
            (c.scope.venue_account_id,)).fetchone()
        assert row['quantity']==0 and row['known'] and row['discrepancy'] is None
    assert any(i.event.event_id=='position-1' for i in c.engine.query_market_evidence(c.write.command_id).inputs)
    from trading_system.risk.reservation_repository import ReservationRepository
    with c.service.uow.transaction(c.scope) as tx:
        assert not c.bundle.reservations and not ReservationRepository(tx).group()


def native_verifier(c,path):
    from trading_system.execution.paper_native_parent_reports import PaperNativeParentReportVerifier
    from trading_system.engine.nautilus.id_codec import VenueIdCodec
    from trading_system.engine.nautilus.native_events import NativeEventBridge
    codec=VenueIdCodec(c.scope)
    return PaperNativeParentReportVerifier(c.service.uow,c.scope,engine=c.engine,
        archive_bridge=NativeEventBridge(codec,path)),codec


@pytest.mark.parametrize('with_child',[False,True])
def test_native_source_authenticates_actual_parent_and_children(database,venue_v2,tmp_path,with_child):
    from trading_system.engine.paper_market import PaperMatcher
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE');submit(c)
    if with_child:PaperMatcher(c.engine).apply_market_event(event(c,quantity='.1'))
    service,codec=native_verifier(c,tmp_path/'native-raw')
    try:
        result=service.verify(c.write.command_id)
        assert result.source_verified,result.discrepancies
        assert result.record_kind=='PAPER_NATIVE_PARENT_VERIFICATION_V1'
        assert len(result.fills)==int(with_child)
        assert result.raw_report.pool.remaining_pool_quantity==D('.8') if with_child else result.raw_report.pool.remaining_pool_quantity==1
        assert not result.execution_authorized and not result.funds_release_authorized
    finally:codec.close()


@pytest.mark.parametrize('tamper',['parent','child','fill','budget','remaining','fill_time','market_hash','allocation'])
def test_native_source_rejects_fabricated_relationship_and_retains_raw(database,venue_v2,tmp_path,monkeypatch,tamper):
    from trading_system.engine.paper_market import PaperMatcher
    c=advanced_case(database,venue_v2,tmp_path,template='TWAP',tif='NONE');submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.1'))
    report=c.engine.query_creation(c.write.command_id)
    if tamper=='parent':report=replace(report,native_parent=replace(report.native_parent,venue_parent_id='invented'))
    if tamper=='child':report=replace(report,orders=(replace(report.orders[0],client_id='f'*32),))
    if tamper=='fill':report=replace(report,fills=(replace(report.fills[0],quantity=D('10')),))
    if tamper=='budget':report=replace(report,pool=replace(report.pool,maximum_gross_notional=D('999999')))
    if tamper=='remaining':report=replace(report,orders=(replace(report.orders[0],remaining_quantity=D('-1')),))
    if tamper=='fill_time':report=replace(report,fills=(replace(report.fills[0],occurred_at='2000-01-01T00:00:00+00:00'),))
    if tamper=='market_hash':report=replace(report,fills=(replace(report.fills[0],market_hash='a'*64),))
    if tamper=='allocation':report=replace(report,accepted_contexts=(report.accepted_contexts[0],replace(report.accepted_contexts[1],
        allocation=replace(report.accepted_contexts[1].allocation,quantity=D('99')))))
    monkeypatch.setattr(c.engine,'query_creation',lambda _:report)
    service,codec=native_verifier(c,tmp_path/'native-tampered')
    try:
        result=service.verify(c.write.command_id)
        assert not result.source_verified and result.status=='BLOCKED'
        assert result.raw_report==report and result.raw_refs
    finally:codec.close()


def test_native_parent_cannot_start_after_actual_sim_lock_wait(database,venue_v2,tmp_path):
    import psycopg,time
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from psycopg.conninfo import conninfo_to_dict
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.engine.paper_market import market_key
    c=advanced_case(database,venue_v2,tmp_path,template="TWAP",tif="NONE");key=market_key(c.scope.product,c.scope.instrument_uid)
    dbname=conninfo_to_dict(venue_v2.runtime_dsn)['dbname']
    with psycopg.connect(venue_v2.event_dsn) as blocker:
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
