"""Actual protected commands + additive venue reference/trigger execution."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from integration.test_paper_market_matcher import isolated_postgres,database,venue,case,submit,event
from trading_system.contracts import types as t
from trading_system.contracts.order_program import PriceSpec,TriggerSpec,TimeInForceSpec


@pytest.fixture(scope='module')
def venue_v2(database,venue):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from trading_system.engine.paper_market_v2 import provision_paper_market_v2
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(venue.runtime_dsn)['dbname']})
    provision_paper_market_v2(admin,venue)
    return venue


def advanced_case(database,venue_v2,tmp_path,**options):
    from trading_system.engine.paper_market_v2 import PaperMatchingModelV2
    c=case(database,venue_v2,tmp_path,**options)
    c.engine._matching_model=PaperMatchingModelV2(model_id='ISOLATED_V2_VISIBLE_DEPTH_AND_REFERENCE_FIXTURE',quote_fee_rate='0')
    return c


def reference(c,sequence,source,price):
    from trading_system.engine.paper_market_v2 import PaperReferenceEventV2,ReferencePrice
    return PaperReferenceEventV2(contract_version='2026-10-30',event_id='reference-'+str(sequence),stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=sequence,kind='REFERENCE_PRICES',
        prices=(ReferencePrice(source=source,price=price),),occurred_at=c.c.facts.inputs.clock.utc_now,
        source='PAPER_MARKET_REPLAY_SYNTHETIC')


def market_order(order):
    return replace(order,price=PriceSpec(kind='MARKET',value=None,peg_type=None,offset_levels=None,price_match=None))


def test_actual_v2_base_market_uses_visible_opposing_quote_and_partial_expiry(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    c=advanced_case(database,venue_v2,tmp_path,tif='NONE',order_transform=market_order)
    PaperMatcher(c.engine).apply_market_event(event(c,ask='81500',quantity='.4'))
    submit(c);report=c.engine.query_creation(c.write.command_id)
    assert report.status=='EXPIRED' and report.filled_quantity==D('.4') and report.remaining_quantity==0
    assert report.fills[0].price==D('81500') and report.request==c.request
    assert all(f.name!='price' for f in c.request.fields)


def test_stop_limit_waits_for_exact_last_source_then_keeps_native_limit(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    def stop_limit(order):
        return replace(order,trigger=TriggerSpec(kind='STATIC',purpose='STOP_LOSS',source='LAST',price='83000',
            activation_price=None,offset=None,offset_unit=None,price_protect=False))
    c=advanced_case(database,venue_v2,tmp_path,order_transform=stop_limit);submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,quantity='.4'))
    assert not c.engine.query_creation(c.write.command_id).fills
    matcher.apply_market_event(reference(c,2,'MARK_PRICE','83000'))
    assert not c.engine.query_creation(c.write.command_id).fills
    matcher.apply_market_event(reference(c,3,'LAST','83000'))
    report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==D('.4') and report.fills[0].price==D('81490')
    assert report.orders[0].runtime.activation_event_id=='reference-3'


def test_trailing_extrema_survive_recreated_worker_and_use_depth_not_trigger_price(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    def trailing(order):
        return replace(market_order(order),trigger=TriggerSpec(kind='TRAILING',purpose='STOP_LOSS',source='LAST',price=None,
            activation_price=None,offset='100',offset_unit='BIPS',price_protect=False))
    c=advanced_case(database,venue_v2,tmp_path,tif='NONE',order_transform=trailing,actual='1',target='0',quantity='1',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,quantity='1'))
    matcher.apply_market_event(reference(c,2,'LAST','82000'));matcher.apply_market_event(reference(c,3,'LAST','83000'))
    assert not c.engine.query_creation(c.write.command_id).fills
    PaperMatcher(c.engine).apply_market_event(reference(c,4,'LAST','82500'))
    assert not c.engine.query_creation(c.write.command_id).fills
    PaperMatcher(c.engine).apply_market_event(reference(c,5,'LAST','82100'))
    report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==1 and report.fills[0].price==D('81489')
    assert report.orders[0].runtime.high_water==D('83000')
    proof=c.engine.query_market_evidence(c.write.command_id)
    assert {i.event.sequence for i in proof.inputs}=={1,2,3,4,5}
    assert {i.event.event_id for i in proof.inputs if i.event.kind=='REFERENCE_PRICES'}=={
        'reference-2','reference-3','reference-4','reference-5'}


def test_v1_and_v2_competing_accounts_share_one_actual_depth_source(database,venue_v2,tmp_path):
    from uuid import uuid4
    from trading_system.engine.paper_market import PaperMatcher,PaperCreationReport
    market='mixed-market-'+uuid4().hex
    first=case(database,venue_v2,tmp_path,market=market);submit(first)
    second=advanced_case(database,venue_v2,tmp_path,market=market,tif='NONE',order_transform=market_order)
    snapshot=event(first,quantity='1.5')
    PaperMatcher(first.engine).apply_market_event(snapshot)
    submit(second)
    before=first.engine.query_creation(first.write.command_id)
    after=second.engine.query_creation(second.write.command_id)
    assert type(before) is PaperCreationReport and before.filled_quantity==1
    assert after.filled_quantity==D('.5') and after.status=='EXPIRED'
    proof=second.engine.query_market_evidence(second.write.command_id)
    assert len(proof.inputs)==1 and sum((r.quantity for r in proof.consumptions),D(0))==D('1.5')
    assert not PaperMatcher(second.engine).apply_market_event(snapshot).applied


def test_runtime_cannot_publish_price_sources_or_change_activation_progress(venue_v2):
    import psycopg
    for statement in ('INSERT INTO public.paper_reference_prices_v2 DEFAULT VALUES',
                      'UPDATE public.paper_order_runtime_v2 SET active=true'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(venue_v2.runtime_dsn) as db:db.execute(statement)


def test_primary_peg_latches_placement_price_without_rewriting_native_spec(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    def peg(order):
        return replace(order,price=PriceSpec(kind='PEG',value=None,peg_type='PRIMARY_PEG',offset_levels=1,price_match=None))
    c=advanced_case(database,venue_v2,tmp_path,order_transform=peg);matcher=PaperMatcher(c.engine)
    snapshot=replace(event(c,ask='81500',quantity='1'),bids=(DepthLevel(price='81489',quantity='1'),DepthLevel(price='81488',quantity='1')))
    matcher.apply_market_event(snapshot);submit(c)
    report=c.engine.query_creation(c.write.command_id)
    assert not report.fills and report.orders[0].runtime.effective_limit==D('81488')
    moved=replace(event(c,sequence=2,ask='81500',quantity='.4'),bids=(DepthLevel(price='81487',quantity='1'),),
        asks=(DepthLevel(price='81488',quantity='.4'),))
    matcher.apply_market_event(moved);report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==D('.4') and report.orders[0].runtime.effective_limit==D('81488')
    assert report.accepted_contexts[0].terms.spec.price.kind=='PEG' and all(f.name!='price' for f in c.request.fields)


def test_iceberg_hidden_quantity_keeps_budget_and_reveal_loses_queue_priority(database,venue_v2,tmp_path):
    from uuid import uuid4
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.risk.reservation_repository import ReservationRepository
    market='iceberg-market-'+uuid4().hex
    c=advanced_case(database,venue_v2,tmp_path,market=market,order_transform=lambda order:replace(order,iceberg_quantity='0.1'))
    other=advanced_case(database,venue_v2,tmp_path,market=market)
    submit(c);submit(other);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,quantity='.25'))
    report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==D('.1') and report.remaining_quantity==D('.9')
    assert report.orders[0].runtime.visible_remaining==0
    assert other.engine.query_creation(other.write.command_id).filled_quantity==D('.15')
    matcher.apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
    assert c.engine.query_creation(c.write.command_id).filled_quantity==D('.1')
    assert other.engine.query_creation(other.write.command_id).filled_quantity==D('.35')
    with c.service.uow.transaction(c.scope) as tx:
        hold=ReservationRepository(tx).get(c.bundle.reservations[0].reservation.reservation_id)
        assert hold.held==c.bundle.reservations[0].held


def test_terminal_v2_order_is_not_reactivated_by_later_reference_or_depth(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    def stop_limit(order):
        return replace(order,trigger=TriggerSpec(kind='STATIC',purpose='STOP_LOSS',source='LAST',price='83000',
            activation_price=None,offset=None,offset_unit=None,price_protect=False))
    c=advanced_case(database,venue_v2,tmp_path,order_transform=stop_limit);matcher=PaperMatcher(c.engine)
    submit(c);matcher.apply_market_event(event(c,quantity='1'));matcher.apply_market_event(reference(c,2,'LAST','83000'))
    before=c.engine.query_creation(c.write.command_id)
    assert before.status=='FILLED'
    matcher.apply_market_event(reference(c,3,'LAST','83100'))
    after=c.engine.query_creation(c.write.command_id)
    assert after.status=='FILLED' and after.filled_quantity==1 and not after.orders[0].runtime.active
    assert after.orders[0].lifecycle_revision==before.orders[0].lifecycle_revision


def test_gtd_expires_from_actual_clock_source_after_proposal_window_without_fake_fill(database,venue_v2,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.engine.paper_market_v2 import PaperClockEventV2
    expiry='2026-10-28T10:15:00Z'
    c=advanced_case(database,venue_v2,tmp_path,product=t.Product.USD_M_PERP,
        order_transform=lambda o:replace(o,tif=TimeInForceSpec(kind='GTD',expires_at=expiry)))
    submit(c);matcher=PaperMatcher(c.engine);matcher.apply_market_event(event(c,ask='81500',quantity='1'))
    assert c.engine.query_creation(c.write.command_id).status=='SUBMITTED'
    base=c.c.facts.inputs.clock
    advanced=replace(base,utc_now=expiry,monotonic_ns=base.monotonic_ns+15*60*10**9)
    c.engine._clock=lambda:advanced
    clock=PaperClockEventV2(contract_version='2026-10-30',event_id='gtd-clock',stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=2,kind='CLOCK',occurred_at=expiry,
        source='PAPER_MARKET_REPLAY_SYNTHETIC')
    matcher.apply_market_event(clock);report=c.engine.query_creation(c.write.command_id)
    assert report.status=='EXPIRED' and not report.fills and report.remaining_quantity==0
    assert report.dispositions[-1].reason=='VENUE_GTD_EXPIRED'
    assert utc_timestamp(report.dispositions[-1].occurred_at)==utc_timestamp(expiry)


def test_equity_rth_day_waits_for_published_window_and_expires_without_consuming_closed_depth(database,venue_v2,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.engine.paper_market_v2 import PaperCalendarEventV2,PaperSessionWindowV2,PaperClockEventV2
    from trading_system.engine.paper_market import DepthLevel
    def rth_day(p):
        n=p.program.nodes[0]
        return replace(p,program=replace(p.program,nodes=(replace(n,order=replace(n.order,trading_session='RTH',
            tif=TimeInForceSpec(kind='DAY',expires_at=None))),)))
    c=advanced_case(database,venue_v2,tmp_path,product=t.Product.US_EQUITY,actual='1',target='0',quantity='1',side='SELL',
        plan_proposal_transform=rth_day,
        financial_capacity=(t.Money(currency='USD',amount=D('1000000')),t.Money(currency='AAPL',amount=D('10'))),
        financial_buffer=(t.Money(currency='USD',amount=D(0)),t.Money(currency='AAPL',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(replace(event(c,ask='81500',quantity='1'),bids=(DepthLevel(price='81490',quantity='1'),)))
    assert not c.engine.query_creation(c.write.command_id).fills
    calendar=PaperCalendarEventV2(contract_version='2026-10-30',event_id='calendar-rth',stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=2,kind='SESSION_CALENDAR',
        calendar_id='SYNTHETIC_US_EQUITY_CALENDAR',revision='2026a',timezone='America/New_York',
        windows=(PaperSessionWindowV2(session='RTH',trading_date='2026-10-28',opens_at='2026-10-28T13:30:00Z',
            closes_at='2026-10-28T20:00:00Z'),),valid_until='2026-10-29T00:00:00Z',
        occurred_at=c.c.facts.inputs.clock.utc_now,source='PAPER_MARKET_REPLAY_SYNTHETIC')
    matcher.apply_market_event(calendar)
    assert not c.engine.query_creation(c.write.command_id).fills
    # A second independently admitted account shares this same AAPL book.
    other=advanced_case(database,venue_v2,tmp_path,product=t.Product.US_EQUITY,actual='1',target='0',quantity='1',side='SELL',
        plan_proposal_transform=rth_day,
        financial_capacity=(t.Money(currency='USD',amount=D('1000000')),t.Money(currency='AAPL',amount=D('10'))),
        financial_buffer=(t.Money(currency='USD',amount=D(0)),t.Money(currency='AAPL',amount=D(0))))
    submit(other)
    base=c.c.facts.inputs.clock
    def clock(sequence,when):
        elapsed=int((utc_timestamp(when)-utc_timestamp(base.utc_now)).total_seconds()*10**9)
        c.engine._clock=lambda:replace(base,utc_now=when,monotonic_ns=base.monotonic_ns+elapsed)
        return PaperClockEventV2(contract_version='2026-10-30',event_id='session-clock-'+str(sequence),stream_id=c.scope.instrument_uid,
            product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=sequence,kind='CLOCK',occurred_at=when,
            source='PAPER_MARKET_REPLAY_SYNTHETIC')
    matcher.apply_market_event(clock(3,'2026-10-28T13:30:00Z'))
    report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==1 and report.fills[0].price==D('81490')
    proof=c.engine.query_market_evidence(c.write.command_id)
    assert any(i.event.event_id=='calendar-rth' for i in proof.inputs)
    # The residual real DAY order expires at the same market's close.
    other_matcher=PaperMatcher(other.engine)
    c=other;base=other.c.facts.inputs.clock
    other_matcher.apply_market_event(clock(4,'2026-10-28T20:00:00Z'))
    expired=other.engine.query_creation(other.write.command_id)
    assert expired.status=='EXPIRED' and not expired.fills
    assert expired.dispositions[-1].reason=='VENUE_DAY_EXPIRED'


def test_calendar_timezone_windows_keep_real_dst_offsets_and_reject_inverted_or_overlapping_windows():
    from trading_system.engine.paper_market_v2 import PaperCalendarEventV2,PaperSessionWindowV2
    windows=(PaperSessionWindowV2(session='RTH',trading_date='2026-10-30',opens_at='2026-10-30T13:30:00Z',closes_at='2026-10-30T20:00:00Z'),
        PaperSessionWindowV2(session='RTH',trading_date='2026-11-02',opens_at='2026-11-02T14:30:00Z',closes_at='2026-11-02T21:00:00Z'))
    source=PaperCalendarEventV2(contract_version='2026-10-30',event_id='dst-calendar',stream_id='AAPL',product=t.Product.US_EQUITY,
        instrument_uid='AAPL',sequence=1,kind='SESSION_CALENDAR',calendar_id='SYNTHETIC_DST_CALENDAR',revision='2026a',
        timezone='America/New_York',windows=windows,valid_until='2026-11-03T00:00:00Z',occurred_at='2026-10-28T10:00:00Z',
        source='PAPER_MARKET_REPLAY_SYNTHETIC')
    assert source.windows==windows
    with pytest.raises(ValueError):replace(source,windows=(windows[0],windows[0]))
    with pytest.raises(ValueError):replace(windows[0],closes_at=windows[0].opens_at)


@pytest.mark.parametrize('mode,terminal,maker_remaining,taker_prevented',[
    ('EXPIRE_TAKER',True,'0.4','1'),('EXPIRE_MAKER',False,'0','0'),('EXPIRE_BOTH',True,'0','1')])
def test_stp_uses_explicit_current_liquidity_owner_and_never_turns_prevention_into_fill(
        database,venue_v2,tmp_path,mode,terminal,maker_remaining,taker_prevented):
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.engine.paper_market_v2 import PaperLiquidityOwnershipEventV2,PaperLiquidityOwnerV2
    c=advanced_case(database,venue_v2,tmp_path,order_transform=lambda o:replace(o,stp=mode),
        profile_transform=lambda p:replace(p,profile_id='SYNTHETIC_V2_STP_'+mode,
            spot=replace(p.spot,allowed_stp=('NONE',mode))))
    submit(c);matcher=PaperMatcher(c.engine);depth=event(c,quantity='0.4');matcher.apply_market_event(depth)
    report=c.engine.query_creation(c.write.command_id)
    assert not report.fills and report.orders[0].runtime.phase=='WAITING_COUNTERPARTY_DATA'
    ownership=PaperLiquidityOwnershipEventV2(contract_version='2026-10-30',event_id='owned-liquidity',stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=2,kind='LIQUIDITY_OWNERSHIP',
        depth_event_id=depth.event_id,depth_hash=__import__('trading_system.engine.paper_market',fromlist=['digest']).digest(depth),
        owners=(PaperLiquidityOwnerV2(side='ASK',price='81490',source_quantity='0.4',venue_account_id=c.scope.venue_account_id,
            venue_order_id='synthetic-observed-maker-1'),),occurred_at=c.c.facts.inputs.clock.utc_now,source='PAPER_MARKET_REPLAY_SYNTHETIC')
    matcher.apply_market_event(ownership);report=c.engine.query_creation(c.write.command_id)
    assert not report.fills and report.filled_quantity==0 and report.orders[0].runtime.prevented_quantity==D(taker_prevented)
    assert report.status==('EXPIRED' if terminal else 'SUBMITTED')
    assert report.dispositions[-1].reason=='STP_'+mode
    proof=c.engine.query_market_evidence(c.write.command_id)
    assert len(proof.preventions)==1 and proof.preventions[0].maker_quantity==D('0.4')-D(maker_remaining)
    assert proof.preventions[0].taker_quantity==D(taker_prevented) and not proof.consumptions
    assert {i.event.event_id for i in proof.inputs}=={depth.event_id,ownership.event_id}
    other=case(database,venue_v2,tmp_path,market=c.scope.instrument_uid);submit(other)
    assert other.engine.query_creation(other.write.command_id).filled_quantity==D(maker_remaining)
    assert not matcher.apply_market_event(ownership).applied


def test_actual_consumed_command_cannot_start_against_a_market_book_from_its_future(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    c=advanced_case(database,venue_v2,tmp_path)
    base=c.c.facts.inputs.clock
    c.engine._clock=lambda:replace(base,utc_now='2026-10-28T10:00:01Z',monotonic_ns=base.monotonic_ns+10**9)
    PaperMatcher(c.engine).apply_market_event(replace(event(c,quantity='1'),occurred_at='2026-10-28T10:00:01Z'))
    c.engine._clock=lambda:base
    with pytest.raises(ValueError,match='PAPER_MARKET_CLOCK_AHEAD_OF_ACCEPTANCE'):submit(c)
    assert c.engine.query_creation(c.write.command_id) is None
    from trading_system.storage.models import scope_id
    with c.service.uow.transaction(c.scope) as tx:
        # Real MAY/consume remains; rollback of external SIM admission cannot
        # erase the original-ID query obligation in the business database.
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s AND command_id=%s',
            (scope_id(c.scope),c.write.command_id)).fetchone()[0]==1


def test_owner_source_rejects_wrong_depth_or_quantity_and_runtime_cannot_publish_it(database,venue_v2,tmp_path):
    import psycopg
    from trading_system.engine.paper_market import PaperMatcher,digest
    from trading_system.engine.paper_market_v2 import PaperLiquidityOwnershipEventV2,PaperLiquidityOwnerV2
    c=advanced_case(database,venue_v2,tmp_path);submit(c);matcher=PaperMatcher(c.engine)
    depth=event(c,ask='81500',quantity='1');matcher.apply_market_event(depth)
    ownership=PaperLiquidityOwnershipEventV2(contract_version='2026-10-30',event_id='bad-owner',stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=2,kind='LIQUIDITY_OWNERSHIP',
        depth_event_id=depth.event_id,depth_hash=digest(depth),owners=(PaperLiquidityOwnerV2(side='ASK',price='81500',source_quantity='2',
        venue_account_id=c.scope.venue_account_id,venue_order_id='synthetic-source-maker'),),
        occurred_at=c.c.facts.inputs.clock.utc_now,source='PAPER_MARKET_REPLAY_SYNTHETIC')
    with pytest.raises(psycopg.Error):matcher.apply_market_event(ownership)
    with pytest.raises(psycopg.Error):matcher.apply_market_event(replace(ownership,depth_hash='0'*64))
    for statement in ('INSERT INTO public.paper_liquidity_owners_v2 DEFAULT VALUES',
            'UPDATE public.paper_session_calendars_v2 SET calendar=calendar',
            'DELETE FROM public.paper_market_preventions_v2'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(venue_v2.runtime_dsn) as db:db.execute(statement)


def test_native_oco_has_one_real_parent_command_and_observed_member_cancellation_on_activation(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=advanced_case(database,venue_v2,tmp_path,template='OCO',actual='1',target='0',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine)
    pending=c.engine.query_creation(c.write.command_id)
    assert len(pending.orders)==2 and pending.native_parent.parent_client_id==c.request.assigned_ids.parent_client_id
    assert pending.native_parent.venue_parent_id and len(pending.native_parent.members)==2
    matcher.apply_market_event(replace(event(c,ask='83001',quantity='1'),bids=(DepthLevel(price='83000',quantity='0.4'),)))
    assert not c.engine.query_creation(c.write.command_id).fills
    matcher.apply_market_event(reference(c,2,'LAST','83000'))
    report=c.engine.query_creation(c.write.command_id)
    by_node={o.node_id:o for o in report.orders}
    assert by_node['above'].filled_quantity==D('0.4') and by_node['above'].remaining_quantity==D('0.6')
    assert by_node['below'].status=='CANCELED' and by_node['below'].filled_quantity==0
    assert report.dispositions[-1].reason=='NATIVE_EXCLUSIVE_SIBLING_ACTIVATED'
    assert {f.node_id for f in report.fills}=={'above'}
    with c.engine._connect() as db:
        assert db.execute('SELECT count(*) AS n FROM public.paper_engine_commands WHERE scope_key=%s',
            (c.engine._scope_key,)).fetchone()['n']==1
    matcher.apply_market_event(reference(c,3,'LAST','81300'))
    after=c.engine.query_creation(c.write.command_id)
    assert {f.node_id for f in after.fills}=={'above'} and {o.node_id:o for o in after.orders}['below'].status=='CANCELED'


@pytest.mark.parametrize('template',['OTO','OTOCO'])
def test_native_pending_members_wait_for_actual_full_working_fill_then_activate_without_application_commands(
        database,venue_v2,tmp_path,template):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=advanced_case(database,venue_v2,tmp_path,template=template,
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,quantity='0.4'))
    partial=c.engine.query_creation(c.write.command_id)
    working=next(n.original.node_id for n in c.bundle.archive.compilation.nodes if n.original.role=='WORKING')
    assert {o.node_id:o for o in partial.orders}[working].filled_quantity==D('0.4')
    assert all(o.filled_quantity==0 and o.runtime.phase=='WAITING_FULL_WORKING_FILL' for o in partial.orders if o.node_id!=working)
    matcher.apply_market_event(event(c,sequence=2,quantity='0.6',kind='DEPTH_DELTA'))
    full=c.engine.query_creation(c.write.command_id)
    assert {o.node_id:o for o in full.orders}[working].status=='FILLED'
    assert all(o.runtime.parent_activation_fill_id for o in full.orders if o.node_id!=working)
    matcher.apply_market_event(replace(event(c,sequence=3,ask='83001',quantity='1'),bids=(DepthLevel(price='83000',quantity='1'),)))
    if template=='OTOCO':matcher.apply_market_event(reference(c,4,'LAST','83000'))
    after=c.engine.query_creation(c.write.command_id)
    assert sum((f.quantity for f in after.fills if f.node_id!=working),D(0))==1,(
        [(o.node_id,o.status,o.runtime.phase,o.runtime.effective_limit,o.runtime.parent_activation_fill_id) for o in after.orders],
        [(d.client_id,d.reason) for d in after.dispositions])
    assert after.native_parent.status=='ALL_DONE'
    with c.engine._connect() as db:
        assert db.execute('SELECT count(*) AS n FROM public.paper_engine_commands WHERE scope_key=%s',(c.engine._scope_key,)).fetchone()['n']==1


def test_native_pending_node_cannot_borrow_program_gross_to_exceed_its_individual_policy_limit(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,template='OTO',risk_policy_transform=lambda p:replace(p,maximum_order_notional='82000'),
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    with pytest.raises(ValueError,match='PAPER_NATIVE_NODE_POLICY_EXCEEDED'):submit(c)
    assert c.engine.query_creation(c.write.command_id) is None


def test_stp_decrement_retains_original_quantity_and_exact_prevented_leaves_then_fills_only_external_remainder(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher,digest
    from trading_system.engine.paper_market_v2 import PaperLiquidityOwnershipEventV2,PaperLiquidityOwnerV2
    c=advanced_case(database,venue_v2,tmp_path,order_transform=lambda o:replace(o,stp='DECREMENT'),
        profile_transform=lambda p:replace(p,profile_id='SYNTHETIC_V2_DECREMENT',spot=replace(p.spot,allowed_stp=('NONE','DECREMENT'))))
    submit(c);matcher=PaperMatcher(c.engine);depth=event(c,quantity='0.4');matcher.apply_market_event(depth)
    owned=PaperLiquidityOwnershipEventV2(contract_version='2026-10-30',event_id='decrement-owner',stream_id=c.scope.instrument_uid,
        product=c.scope.product,instrument_uid=c.scope.instrument_uid,sequence=2,kind='LIQUIDITY_OWNERSHIP',depth_event_id=depth.event_id,
        depth_hash=digest(depth),owners=(PaperLiquidityOwnerV2(side='ASK',price='81490',source_quantity='0.4',
        venue_account_id=c.scope.venue_account_id,venue_order_id='synthetic-owned-maker'),),occurred_at=c.c.facts.inputs.clock.utc_now,
        source='PAPER_MARKET_REPLAY_SYNTHETIC')
    matcher.apply_market_event(owned);prevented=c.engine.query_creation(c.write.command_id)
    assert not prevented.fills and prevented.orders[0].quantity==1 and prevented.remaining_quantity==D('0.6')
    assert prevented.orders[0].runtime.prevented_quantity==D('0.4') and prevented.status=='SUBMITTED'
    delta=event(c,sequence=3,quantity='0.6',kind='DEPTH_DELTA');matcher.apply_market_event(delta)
    external=replace(owned,event_id='decrement-external',sequence=4,depth_event_id=delta.event_id,depth_hash=digest(delta),
        owners=(replace(owned.owners[0],source_quantity='1.0',venue_account_id='synthetic-other-account',venue_order_id='synthetic-external-maker'),))
    matcher.apply_market_event(external);after=c.engine.query_creation(c.write.command_id)
    assert after.status=='FILLED' and after.orders[0].quantity==1 and after.filled_quantity==D('0.6') and after.remaining_quantity==0
    proof=c.engine.query_market_evidence(c.write.command_id)
    assert sum((p.taker_quantity for p in proof.preventions),D(0))==D('0.4')
    assert sum((a.quantity for a in proof.consumptions),D(0))==D('0.6')


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
        row=db.execute('SELECT quantity,known FROM public.paper_venue_positions_v2 WHERE account_id=%s',
            (c.scope.venue_account_id,)).fetchone()
        assert row['quantity']==D('0.8') and row['known']
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
