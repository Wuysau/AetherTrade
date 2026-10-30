"""Genuine protected V2 venue observations -> new trusted verifier (no posting)."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4
import pytest

from integration.test_paper_market_matcher_v2 import isolated_postgres,database,venue,venue_v2,advanced_case,reference,market_order
from integration.test_paper_market_matcher import submit,event
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.order_program import TriggerSpec,PriceSpec,TimeInForceSpec
from trading_system.engine.paper_market import PaperMatcher,PaperMarketFill,DepthLevel
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.engine.nautilus.native_events import NativeEventBridge
from trading_system.storage.models import scope_id


def verifier(c,path):
    from trading_system.execution.paper_program_v2_reports import PaperProgramV2ReportVerifier
    codec=VenueIdCodec(c.scope)
    return PaperProgramV2ReportVerifier(c.service.uow,c.scope,engine=c.engine,archive_bridge=NativeEventBridge(codec,path)),codec


def observe(c,path):
    service,codec=verifier(c,path)
    try:return service.verify(c.write.command_id)
    finally:codec.close()


def reseal_report(report):
    body=dict(request=domain_payload(report.request),orders=[domain_payload(o) for o in report.orders],
        fills=[domain_payload(f) for f in report.fills],accepted_contexts=[domain_payload(c) for c in report.accepted_contexts],
        dispositions=[domain_payload(d) for d in report.dispositions],contract_version=report.contract_version,
        record_kind=report.record_kind,native_parent=domain_payload(report.native_parent) if report.native_parent is not None else None)
    return replace(report,evidence=replace(report.evidence,hash=canonical_hash(body)))


def reseal_market(report):
    body=dict(command_id=report.command_id,request_hash=report.request_hash,inputs=[domain_payload(i) for i in report.inputs],
        consumptions=[domain_payload(i) for i in report.consumptions],preventions=[domain_payload(i) for i in report.preventions],
        contract_version=report.contract_version,record_kind=report.record_kind)
    return replace(report,evidence=replace(report.evidence,hash=canonical_hash(body)))


def trailing_case(database,venue_v2,tmp_path):
    def trailing(order):
        return replace(market_order(order),trigger=TriggerSpec(kind='TRAILING',purpose='STOP_LOSS',source='LAST',price=None,
            activation_price=None,offset='100',offset_unit='BIPS',price_protect=False))
    c=advanced_case(database,venue_v2,tmp_path,tif='NONE',order_transform=trailing,actual='1',target='0',quantity='1',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine);matcher.apply_market_event(event(c,quantity='1'))
    matcher.apply_market_event(reference(c,2,'LAST','82000'));matcher.apply_market_event(reference(c,3,'LAST','83000'))
    matcher.apply_market_event(reference(c,4,'LAST','82500'));matcher.apply_market_event(reference(c,5,'LAST','82100'))
    return c


def test_actual_v2_market_partial_preserves_native_economics_and_explicit_v2_provenance(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,tif='NONE',order_transform=market_order)
    PaperMatcher(c.engine).apply_market_event(event(c,ask='81500',quantity='.4'));submit(c)
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert result.contract_version=='2026-10-30' and result.record_kind=='PAPER_PROGRAM_VERIFICATION_V2'
    fill,=result.fills
    assert type(fill.raw) is PaperMarketFill and fill.source_v2.record_kind=='PAPER_FILL_SOURCE_V2'
    assert fill.fill.quantity.value==D('.4') and fill.fill.price.value==D('81500')
    assert fill.economics.reported_gross.amount==D('32600') and result.orders[0].leaves_quantity.value==0
    assert not result.execution_authorized and not result.funds_release_authorized
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


def test_actual_trailing_observation_survives_creation_window_and_keeps_every_causal_input(database,venue_v2,tmp_path):
    c=trailing_case(database,venue_v2,tmp_path);clock=c.c.facts.inputs.clock
    later=replace(clock,utc_now=(utc_timestamp(clock.utc_now)+timedelta(seconds=10)).isoformat(),monotonic_ns=clock.monotonic_ns+10**10)
    c.engine._clock=lambda:later
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert result.fills[0].fill.quantity.value==1 and result.fills[0].fill.price.value==D('81489')
    assert {i.event.sequence for i in result.market_evidence.inputs}=={1,2,3,4,5}
    assert result.fills[0].source_v2.causal_input_ids


def test_missing_nonfinal_trailing_dependency_is_archived_but_never_qualified(database,venue_v2,tmp_path):
    c=trailing_case(database,venue_v2,tmp_path);original=c.engine.query_market_evidence
    def corrupt(command_id):
        report=original(command_id)
        changed=replace(report,inputs=tuple(i for i in report.inputs if i.event.sequence!=4))
        body=dict(command_id=changed.command_id,request_hash=changed.request_hash,inputs=[domain_payload(i) for i in changed.inputs],
            consumptions=[domain_payload(i) for i in changed.consumptions],preventions=[domain_payload(i) for i in changed.preventions],
            contract_version=changed.contract_version,record_kind=changed.record_kind)
        return replace(changed,evidence=replace(changed.evidence,hash=canonical_hash(body)))
    c.engine.query_market_evidence=corrupt
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and not result.fills and result.raw_refs
    assert 'PAPER_V2_CAUSAL_INPUT_SET_CHANGED' in result.discrepancies


def test_actual_native_oco_uses_real_parent_members_and_no_fictitious_child_command(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,template='OCO',actual='1',target='0',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(replace(event(c,ask='83001',quantity='1'),bids=(DepthLevel(price='83000',quantity='.4'),)))
    matcher.apply_market_event(reference(c,2,'LAST','83000'))
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert len(result.orders)==2 and result.raw_report.native_parent.parent_client_id==c.request.assigned_ids.parent_client_id
    assert result.fills[0].raw.node_id=='above' and result.fills[0].fill.quantity.value==D('.4')
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s AND sequence=1',(scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)


def test_changed_native_parent_venue_identity_cannot_be_authenticated_by_a_resealed_dto(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,template='OCO',actual='1',target='0',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);report=c.engine.query_creation(c.write.command_id)
    changed=reseal_report(replace(report,native_parent=replace(report.native_parent,venue_parent_id='foreign-parent')))
    c.engine.query_creation=lambda _:changed
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and result.raw_refs and not result.fills
    assert 'PAPER_V2_NATIVE_PARENT_SOURCE_CHANGED' in result.discrepancies


def test_missing_real_competing_account_allocation_cannot_be_hidden_by_resealing_evidence(database,venue_v2,tmp_path):
    market='actual-v2-shared-'+uuid4().hex
    a=advanced_case(database,venue_v2,tmp_path,market=market);b=advanced_case(database,venue_v2,tmp_path,market=market)
    submit(a);submit(b);PaperMatcher(a.engine).apply_market_event(event(a,quantity='1.4'))
    assert a.engine.query_creation(a.write.command_id).filled_quantity==1
    assert b.engine.query_creation(b.write.command_id).filled_quantity==D('.4')
    original=a.engine.query_market_evidence
    def omit(command):
        report=original(command)
        return reseal_market(replace(report,consumptions=tuple(c for c in report.consumptions if c.scope==a.scope)))
    a.engine.query_market_evidence=omit
    result=observe(a,tmp_path/'raw')
    assert not result.source_verified and result.raw_refs and not result.fills
    assert 'PAPER_V2_ALL_RIVAL_ALLOCATION_SET_CHANGED' in result.discrepancies


def test_complete_causal_graph_is_stored_once_not_copied_into_every_fill_provenance(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path);submit(c);matcher=PaperMatcher(c.engine)
    for sequence in range(1,11):
        matcher.apply_market_event(event(c,sequence=sequence,quantity='.1',kind='DEPTH_SNAPSHOT' if sequence==1 else 'DEPTH_DELTA'))
    result=observe(c,tmp_path/'raw')
    assert result.source_verified and len(result.fills)==10,result.discrepancies
    assert len(result.causal_dependencies)==10 and result.causal_graph_hash
    assert all(f.source_v2.causal_graph_hash==result.causal_graph_hash for f in result.fills)
    assert all(len(f.source_v2.causal_input_ids)<=2 for f in result.fills)


def completed_oto(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,template='OTO',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,quantity='.4'))
    clock=c.c.facts.inputs.clock
    later=replace(clock,utc_now=(utc_timestamp(clock.utc_now)+timedelta(seconds=10)).isoformat(),monotonic_ns=clock.monotonic_ns+10**10)
    c.engine._clock=lambda:later
    matcher.apply_market_event(event(c,sequence=2,kind='DEPTH_DELTA',quantity='.6'))
    matcher.apply_market_event(replace(event(c,sequence=3,ask='81491',quantity='1'),bids=(DepthLevel(price='81490',quantity='.4'),)))
    return c


def test_accepted_native_oto_pending_observation_uses_complete_working_fills_not_new_permit_or_creation_ttl(database,venue_v2,tmp_path):
    c=completed_oto(database,venue_v2,tmp_path);result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    by_node={o.node_id:o for o in result.raw_report.orders}
    assert by_node['exit'].runtime.parent_activation_fill_id
    assert by_node['exit'].filled_quantity==D('.4') and by_node['exit'].remaining_quantity==D('.6')
    assert sum((f.fill.quantity.value for f in result.fills if f.raw.node_id!='exit'),D(0))==1
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s AND sequence=1',(scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)


def test_fake_native_pending_working_fill_link_is_archived_not_accepted(database,venue_v2,tmp_path):
    c=completed_oto(database,venue_v2,tmp_path);report=c.engine.query_creation(c.write.command_id)
    changed=reseal_report(replace(report,orders=tuple(replace(o,runtime=replace(o.runtime,parent_activation_fill_id='foreign-working-fill'))
        if o.node_id=='exit' else o for o in report.orders)))
    c.engine.query_creation=lambda _:changed
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and result.raw_refs
    assert 'PAPER_V2_NATIVE_WORKING_FILL_LINEAGE_CHANGED' in result.discrepancies


def stp_case(database,venue_v2,tmp_path,mode):
    from trading_system.engine.paper_market import digest
    from trading_system.engine.paper_market_v2 import PaperLiquidityOwnershipEventV2,PaperLiquidityOwnerV2
    c=advanced_case(database,venue_v2,tmp_path,order_transform=lambda o:replace(o,stp=mode),
        profile_transform=lambda p:replace(p,profile_id='SYNTHETIC_V2_STP_'+mode,
            spot=replace(p.spot,allowed_stp=('NONE',mode))))
    submit(c);matcher=PaperMatcher(c.engine);depth=event(c,quantity='.4');matcher.apply_market_event(depth)
    owned=PaperLiquidityOwnershipEventV2(contract_version='2026-10-30',event_id='owned-liquidity',stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=2,kind='LIQUIDITY_OWNERSHIP',
        depth_event_id=depth.event_id,depth_hash=digest(depth),
        owners=(PaperLiquidityOwnerV2(side='ASK',price='81490',source_quantity='.4',venue_account_id=c.scope.venue_account_id,
            venue_order_id='synthetic-observed-maker'),),occurred_at=c.c.facts.inputs.clock.utc_now,source='PAPER_MARKET_REPLAY_SYNTHETIC')
    matcher.apply_market_event(owned)
    return c,owned


@pytest.mark.parametrize('mode,prevented,remaining',[('EXPIRE_TAKER','1','0'),('EXPIRE_MAKER','0','1'),
    ('EXPIRE_BOTH','1','0'),('DECREMENT','.4','.6')])
def test_actual_stp_preventions_have_complete_owned_depth_causality_and_never_become_fills(
        database,venue_v2,tmp_path,mode,prevented,remaining):
    c,_=stp_case(database,venue_v2,tmp_path,mode);result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert not result.fills and len(result.market_evidence.preventions)==1
    assert result.raw_report.orders[0].runtime.prevented_quantity==D(prevented)
    assert result.orders[0].leaves_quantity.value==D(remaining)
    assert not result.funds_release_authorized


def test_actual_decrement_restarted_verifier_preserves_original_quantity_and_only_external_fill(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import digest
    c,owned=stp_case(database,venue_v2,tmp_path,'DECREMENT');matcher=PaperMatcher(c.engine)
    delta=event(c,sequence=3,quantity='.6',kind='DEPTH_DELTA');matcher.apply_market_event(delta)
    matcher.apply_market_event(replace(owned,event_id='external-owner',sequence=4,depth_event_id=delta.event_id,depth_hash=digest(delta),
        owners=(replace(owned.owners[0],source_quantity='1',venue_account_id='external-account',venue_order_id='external-maker'),)))
    first=observe(c,tmp_path/'first');second=observe(c,tmp_path/'second')
    assert first.source_verified and second.source_verified,(first.discrepancies,second.discrepancies)
    assert first.fills==second.fills and len(first.fills)==1 and first.fills[0].fill.quantity.value==D('.6')
    assert first.raw_report.orders[0].quantity==1 and first.raw_report.orders[0].runtime.prevented_quantity==D('.4')


def test_actual_primary_peg_uses_earlier_placement_depth_not_later_quote(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,order_transform=lambda o:replace(o,
        price=PriceSpec(kind='PEG',value=None,peg_type='PRIMARY_PEG',offset_levels=1,price_match=None)))
    matcher=PaperMatcher(c.engine)
    placement=replace(event(c,ask='81500'),bids=(DepthLevel(price='81489',quantity='1'),DepthLevel(price='81488',quantity='1')))
    matcher.apply_market_event(placement)
    submit(c)
    moved=replace(event(c,sequence=2,ask='81500',quantity='.4'),asks=(DepthLevel(price='81488',quantity='.4'),),bids=(DepthLevel(price='81487',quantity='1'),))
    matcher.apply_market_event(moved)
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert result.fills[0].fill.price.value==D('81488') and result.raw_report.orders[0].runtime.effective_limit==D('81488')
    assert {d.event_id for d in result.causal_dependencies}=={placement.event_id,moved.event_id}


def test_actual_iceberg_hidden_quantity_is_not_fill_or_released_reservation(database,venue_v2,tmp_path):
    from trading_system.risk.reservation_repository import ReservationRepository
    market='verifier-iceberg-'+uuid4().hex
    c=advanced_case(database,venue_v2,tmp_path,market=market,order_transform=lambda o:replace(o,iceberg_quantity='0.1'))
    rival=advanced_case(database,venue_v2,tmp_path,market=market);submit(c);submit(rival);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,quantity='.25'));matcher.apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert len(result.fills)==1 and result.fills[0].fill.quantity.value==D('.1') and result.orders[0].leaves_quantity.value==D('.9')
    assert len(result.market_evidence.consumptions)==3
    with c.service.uow.transaction(c.scope) as tx:
        assert ReservationRepository(tx).get(c.bundle.reservations[0].reservation.reservation_id).held==c.bundle.reservations[0].held


def test_actual_equity_calendar_clock_can_use_prior_depth_without_replenishing_it(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market_v2 import PaperCalendarEventV2,PaperSessionWindowV2,PaperClockEventV2
    def rth(p):
        n=p.program.nodes[0]
        return replace(p,program=replace(p.program,nodes=(replace(n,order=replace(n.order,trading_session='RTH',
            tif=TimeInForceSpec(kind='DAY',expires_at=None))),)))
    c=advanced_case(database,venue_v2,tmp_path,product=t.Product.US_EQUITY,actual='1',target='0',quantity='1',side='SELL',
        plan_proposal_transform=rth,
        financial_capacity=(t.Money(currency='USD',amount=D('1000000')),t.Money(currency='AAPL',amount=D('10'))),
        financial_buffer=(t.Money(currency='USD',amount=D(0)),t.Money(currency='AAPL',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(replace(event(c,ask='81500',quantity='1'),bids=(DepthLevel(price='81490',quantity='.4'),)))
    matcher.apply_market_event(PaperCalendarEventV2(contract_version='2026-10-30',event_id='calendar-rth',stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=2,kind='SESSION_CALENDAR',
        calendar_id='SYNTHETIC_US_EQUITY_CALENDAR',revision='2026a',timezone='America/New_York',
        windows=(PaperSessionWindowV2(session='RTH',trading_date='2026-10-28',opens_at='2026-10-28T13:30:00Z',
            closes_at='2026-10-28T20:00:00Z'),),valid_until='2026-10-29T00:00:00Z',
        occurred_at=c.c.facts.inputs.clock.utc_now,source='PAPER_MARKET_REPLAY_SYNTHETIC'))
    base=c.c.facts.inputs.clock
    for sequence,when in ((3,'2026-10-28T13:30:00Z'),(4,'2026-10-28T20:00:00Z')):
        elapsed=int((utc_timestamp(when)-utc_timestamp(base.utc_now)).total_seconds()*10**9)
        c.engine._clock=lambda:replace(base,utc_now=when,monotonic_ns=base.monotonic_ns+elapsed)
        matcher.apply_market_event(PaperClockEventV2(contract_version='2026-10-30',event_id='clock-'+str(sequence),stream_id=c.scope.instrument_uid,
            product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=sequence,kind='CLOCK',occurred_at=when,
            source='PAPER_MARKET_REPLAY_SYNTHETIC'))
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert len(result.fills)==1 and result.fills[0].fill.quantity.value==D('.4') and result.orders[0].leaves_quantity.value==0
    assert result.raw_report.dispositions[-1].reason=='VENUE_DAY_EXPIRED'
    assert len(result.causal_dependencies)==4


def test_duplicate_actual_fill_cannot_be_replayed_inside_resealed_report(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path);submit(c);PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    report=c.engine.query_creation(c.write.command_id)
    c.engine.query_creation=lambda _:reseal_report(replace(report,fills=report.fills+report.fills))
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and not result.fills and result.raw_refs
    assert 'PAPER_V2_DUPLICATE_EVENT' in result.discrepancies


def test_disposition_market_identity_cannot_be_changed_while_actual_raw_is_retained(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,tif='NONE',order_transform=market_order)
    PaperMatcher(c.engine).apply_market_event(event(c,ask='81500',quantity='.4'));submit(c)
    report=c.engine.query_creation(c.write.command_id)
    c.engine.query_creation=lambda _:reseal_report(replace(report,dispositions=tuple(replace(d,market_event_id='foreign-depth') for d in report.dispositions)))
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and not result.fills and result.raw_refs
    assert 'PAPER_V2_DISPOSITION_SOURCE_CHANGED' in result.discrepancies


@pytest.mark.parametrize('changed',['before_levels','observed_clock'])
def test_resealed_input_snapshot_must_match_actual_archived_predepth_and_clock(database,venue_v2,tmp_path,changed):
    from trading_system.engine.paper_market import PaperDepthState
    c=advanced_case(database,venue_v2,tmp_path);submit(c);PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    market=c.engine.query_market_evidence(c.write.command_id);item=market.inputs[0]
    replacement=replace(item,before_levels=(PaperDepthState(side='ASK',price='81490',reported='0.1',available='0.1'),)) if changed=='before_levels' else replace(
        item,observed_clock=replace(item.observed_clock,monotonic_ns=item.observed_clock.monotonic_ns+1))
    c.engine.query_market_evidence=lambda _:reseal_market(replace(market,inputs=(replacement,)))
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and not result.fills and result.raw_refs
    assert 'PAPER_V2_CAUSAL_INPUT_SOURCE_CHANGED' in result.discrepancies


def test_accepted_model_must_be_actual_stored_model_even_without_any_fill(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path);submit(c);report=c.engine.query_creation(c.write.command_id)
    altered=tuple(replace(a,model=replace(a.model,model_id='CALLER_MODEL')) for a in report.accepted_contexts)
    c.engine.query_creation=lambda _:reseal_report(replace(report,accepted_contexts=altered))
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and result.raw_refs
    assert 'PAPER_V2_ACCEPTED_DURABLE_TERMS_CHANGED' in result.discrepancies
