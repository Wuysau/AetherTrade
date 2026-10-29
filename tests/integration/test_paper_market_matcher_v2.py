"""Actual protected commands + additive venue reference/trigger execution."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from integration.test_paper_market_matcher import isolated_postgres,database,venue,case,submit,event
from trading_system.contracts import types as t
from trading_system.contracts.order_program import PriceSpec,TriggerSpec


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
    c=advanced_case(database,venue_v2,tmp_path,tif='NONE',order_transform=market_order);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(event(c,quantity='1'));submit(c)
    before=c.engine.query_creation(c.write.command_id)
    assert before.status=='FILLED'
    matcher.apply_market_event(reference(c,2,'LAST','83000'))
    after=c.engine.query_creation(c.write.command_id)
    assert after.status=='FILLED' and after.filled_quantity==1 and not after.orders[0].runtime.active
    assert after.orders[0].lifecycle_revision==before.orders[0].lifecycle_revision
