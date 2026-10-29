"""Independent source checks; real venue execution is covered separately in PG."""
from dataclasses import replace
from decimal import Decimal as D,localcontext

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.engine.paper_market import (
    SOURCE,DepthLevel,PaperMarketEvent,PaperMarketInput,PaperDepthState,
    PaperLiquidityConsumption,PaperMarketEvidence,
)

NOW='2026-10-28T10:00:00Z'
SCOPE=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='account-a',product=t.Product.SPOT,
    instrument_uid='asset-x',venue_leg='BOTH')


def packet(*,kind='DEPTH_SNAPSHOT',ask='1',before=(),amounts=('.6','.4')):
    event=PaperMarketEvent(contract_version='2026-10-29',event_id='event-one',stream_id='stream-one',
        product=SCOPE.product,instrument_uid=SCOPE.instrument_uid,sequence=1,kind=kind,
        bids=(DepthLevel(price='100',quantity='2'),) if kind=='DEPTH_SNAPSHOT' else (),
        asks=(DepthLevel(price='101',quantity=ask),),occurred_at=NOW,source=SOURCE)
    source=PaperMarketInput(event=event,event_hash=canonical_hash(domain_payload(event)),
        raw_payload_json=canonical_json(domain_payload(event)),before_levels=before,
        observed_clock=t.ClockReading(utc_now=NOW,monotonic_ns=100,clock_health='HEALTHY'))
    allocations=tuple(PaperLiquidityConsumption(market_key=event.market_key,event_id=event.event_id,
        scope=replace(SCOPE,venue_account_id='account-'+str(index)),client_id='client-'+str(index),
        fill_id='fill-'+str(index),side='ASK',price=D('101'),quantity=D(amount))
        for index,amount in enumerate(amounts))
    return reseal(PaperMarketEvidence(command_id='command-one',request_hash='a'*64,inputs=(source,),
        consumptions=allocations,evidence=t.EvidenceRef(id='paper-market-source:command-one',hash='0'*64,
            environment=t.Environment.PAPER,source=SOURCE,visible_at=NOW)))


def reseal(value):
    body=dict(command_id=value.command_id,request_hash=value.request_hash,
        inputs=[domain_payload(i) for i in value.inputs],consumptions=[domain_payload(c) for c in value.consumptions])
    return replace(value,evidence=replace(value.evidence,hash=canonical_hash(body)))


def codes(value):
    from trading_system.execution.paper_program_liquidity import market_evidence_codes
    return market_evidence_codes(value,scope=SCOPE,command_id='command-one',request_hash='a'*64,now=NOW)


def test_all_competing_accounts_consume_one_shared_liquidity_pool():
    assert codes(packet())==()
    assert 'PAPER_LIQUIDITY_OVERSPENT' in codes(packet(amounts=('.6','.5')))


def test_snapshot_cannot_replenish_previously_consumed_depth():
    before=(PaperDepthState(side='ASK',price='101',reported='1',available='.1'),)
    assert codes(packet(before=before,amounts=('.1',)))==()
    assert 'PAPER_LIQUIDITY_OVERSPENT' in codes(packet(before=before,amounts=('.2',)))


def test_explicit_positive_delta_adds_only_declared_new_liquidity():
    before=(PaperDepthState(side='ASK',price='101',reported='1',available='.1'),)
    assert codes(packet(kind='DEPTH_DELTA',ask='.5',before=before,amounts=('.6',)))==()
    assert 'PAPER_LIQUIDITY_OVERSPENT' in codes(packet(kind='DEPTH_DELTA',ask='.5',before=before,amounts=('.7',)))


def test_negative_delta_never_creates_negative_or_reusable_liquidity():
    before=(PaperDepthState(side='ASK',price='101',reported='1',available='.1'),)
    assert codes(packet(kind='DEPTH_DELTA',ask='-.5',before=before,amounts=()))==()
    assert 'PAPER_LIQUIDITY_OVERSPENT' in codes(packet(kind='DEPTH_DELTA',ask='-.5',before=before,amounts=('.01',)))
    assert 'PAPER_DEPTH_STATE_INVALID' in codes(packet(kind='DEPTH_DELTA',ask='-2',before=before,amounts=()))


@pytest.mark.parametrize('fault',['duplicate_fill','missing_event','other_instrument','other_product','wrong_price','wrong_side','negative','nondecimal','untyped_scope'])
def test_allocations_cannot_escape_exact_source_scope_side_price_and_identity(fault):
    value=packet(amounts=('.2',))
    first=value.consumptions[0]
    if fault=='duplicate_fill':value=replace(value,consumptions=(first,first))
    if fault=='missing_event':value=replace(value,inputs=())
    if fault=='other_instrument':value=replace(value,consumptions=(replace(first,scope=replace(first.scope,instrument_uid='other')),))
    if fault=='other_product':value=replace(value,consumptions=(replace(first,scope=replace(first.scope,product=t.Product.USD_M_PERP)),))
    if fault=='wrong_price':value=replace(value,consumptions=(replace(first,price=D('102')),))
    if fault=='wrong_side':value=replace(value,consumptions=(replace(first,side='BID',price=D('101')),))
    if fault=='negative':value=replace(value,consumptions=(replace(first,quantity=D('-.1')),))
    if fault=='nondecimal':value=replace(value,consumptions=(replace(first,quantity=True),))
    if fault=='untyped_scope':value=replace(value,consumptions=(replace(first,scope=None),))
    assert codes(reseal(value))


@pytest.mark.parametrize('fault',['source_hash','raw_hash','future_clock','unhealthy_clock','duplicate_event','before_available','before_duplicate','crossed_book'])
def test_raw_market_context_is_checked_independently_of_its_outer_hash(fault):
    value=packet();first=value.inputs[0]
    if fault=='source_hash':return_value=replace(value,evidence=replace(value.evidence,hash='f'*64))
    else:
        if fault=='raw_hash':first=replace(first,raw_payload_json='{}')
        if fault=='future_clock':first=replace(first,observed_clock=replace(first.observed_clock,utc_now='2026-10-28T10:01:00Z'))
        if fault=='unhealthy_clock':first=replace(first,observed_clock=replace(first.observed_clock,clock_health='UNHEALTHY'))
        if fault=='duplicate_event':value=replace(value,inputs=(first,first))
        if fault=='before_available':first=replace(first,before_levels=(PaperDepthState(side='ASK',price='101',reported='1',available='2'),))
        if fault=='before_duplicate':first=replace(first,before_levels=(PaperDepthState(side='ASK',price='101',reported='1',available='1'),)*2)
        if fault=='crossed_book':
            value=packet(kind='DEPTH_DELTA',ask='1',amounts=())
            first=replace(value.inputs[0],before_levels=(PaperDepthState(side='BID',price='102',reported='1',available='1'),))
        if fault!='duplicate_event':value=replace(value,inputs=(first,))
        return_value=reseal(value)
    assert codes(return_value)


def test_ambient_rounding_does_not_hide_aggregate_liquidity_overspend():
    value=packet(ask='1.000000',amounts=('.999999','.000002'))
    with localcontext() as context:
        context.prec=3
        result=codes(value)
    assert 'PAPER_LIQUIDITY_OVERSPENT' in result
