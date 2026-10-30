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
    # A separate real admitted DAY order cannot trade after its associated close.
    other=advanced_case(database,venue_v2,tmp_path,product=t.Product.US_EQUITY,actual='1',target='0',quantity='1',side='SELL',
        plan_proposal_transform=rth_day,
        financial_capacity=(t.Money(currency='USD',amount=D('1000000')),t.Money(currency='AAPL',amount=D('10'))),
        financial_buffer=(t.Money(currency='USD',amount=D(0)),t.Money(currency='AAPL',amount=D(0))))
    submit(other);other_matcher=PaperMatcher(other.engine)
    other_matcher.apply_market_event(event(other,quantity='1'))
    other_matcher.apply_market_event(replace(calendar,stream_id=other.scope.instrument_uid,instrument_uid=other.scope.instrument_uid))
    c=other;base=other.c.facts.inputs.clock
    other_matcher.apply_market_event(clock(3,'2026-10-28T20:00:00Z'))
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
