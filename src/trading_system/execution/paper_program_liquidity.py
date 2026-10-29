"""Independent bounded checks of PAPER depth/allocation observations.

An empty discrepancy list establishes arithmetic consistency only. Source
authentication and protected-command linkage remain the report host's duties.
No fill, permission, funds release or exchange side effect is produced here.
"""
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from fractions import Fraction
from hashlib import sha256

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.contracts.order_lifecycle_v3 import exact_time
from trading_system.engine.paper_market import (
    SOURCE,PaperMarketEvidence,PaperMarketInput,PaperMarketEvent,
    PaperDepthState,PaperLiquidityConsumption,
)

MAX_BYTES=16777216


def _number(value):
    if type(value) is str:
        if not value or len(value)>16384:raise ValueError('source numeric size')
        value=Decimal(value)
    if type(value) is not Decimal or not value.is_finite():raise ValueError('source numeric type')
    if value==0:return Fraction(0)
    # Resource bounds on observations, not trade authorization or rounding.
    if len(value.as_tuple().digits)>16384 or abs(value.adjusted())>4096:
        raise ValueError('source numeric range')
    return Fraction(value)


def market_evidence_codes(evidence,*,scope,command_id,request_hash,now):
    errors=[]
    def issue(code):
        if code not in errors:errors.append(code)
    if (type(evidence) is not PaperMarketEvidence or type(evidence.inputs) is not tuple
            or type(evidence.consumptions) is not tuple):return ('PAPER_MARKET_EVIDENCE_REQUIRED',)
    if len(evidence.inputs)>4096 or len(evidence.consumptions)>100000:
        return ('PAPER_MARKET_EVIDENCE_BOUND_EXCEEDED',)
    if (scope.environment!=t.Environment.PAPER or evidence.command_id!=command_id
            or evidence.request_hash!=request_hash):issue('PAPER_MARKET_REQUEST_CHANGED')
    ref=evidence.evidence
    try:
        if (ref.environment!=t.Environment.PAPER or ref.source!=SOURCE
                or ref.id!='paper-market-source:'+command_id or exact_time(ref.visible_at)>exact_time(now)):
            issue('PAPER_MARKET_SOURCE_CHANGED')
        size=512
        for item in (*evidence.inputs,*evidence.consumptions):
            if type(item) not in (PaperMarketInput,PaperLiquidityConsumption):
                return ('PAPER_MARKET_SOURCE_TYPE_INVALID',)
            if type(item) is PaperMarketInput and (len(item.before_levels)>8192
                    or type(item.raw_payload_json) is not str or len(item.raw_payload_json.encode())>1048576):
                return ('PAPER_MARKET_EVIDENCE_BOUND_EXCEEDED',)
            size+=len(canonical_json(domain_payload(item)).encode('utf-8'))
            if size>MAX_BYTES:return ('PAPER_MARKET_EVIDENCE_BOUND_EXCEEDED',)
        body=dict(command_id=command_id,request_hash=request_hash,
            inputs=[domain_payload(i) for i in evidence.inputs],
            consumptions=[domain_payload(c) for c in evidence.consumptions])
        if canonical_hash(body)!=ref.hash:issue('PAPER_MARKET_SOURCE_HASH_CHANGED')
    except (ValueError,TypeError,AttributeError,InvalidOperation):
        return ('PAPER_MARKET_SOURCE_SHAPE_INVALID',)
    expected_market=canonical_hash(dict(environment='PAPER',venue='BINANCE',product=str(scope.product),
        instrument_uid=scope.instrument_uid))
    allocations=defaultdict(list);seen_fills=set()
    for allocation in evidence.consumptions:
        if type(allocation.scope) is not t.ScopeKey:
            issue('PAPER_LIQUIDITY_SCOPE_CHANGED');continue
        native_key=(allocation.scope.venue_account_id,str(allocation.scope.product),
            allocation.scope.instrument_uid,allocation.fill_id)
        if native_key in seen_fills:issue('PAPER_LIQUIDITY_FILL_DUPLICATED')
        seen_fills.add(native_key)
        if (allocation.market_key!=expected_market or allocation.scope.environment!=t.Environment.PAPER
                or allocation.scope.product!=scope.product or allocation.scope.instrument_uid!=scope.instrument_uid
                or not allocation.scope.venue_account_id or not allocation.client_id or not allocation.fill_id):
            issue('PAPER_LIQUIDITY_SCOPE_CHANGED')
        allocations[(allocation.market_key,allocation.event_id)].append(allocation)
    seen_events=set();previous=None
    for item in evidence.inputs:
        event=item.event
        if type(event) is not PaperMarketEvent:
            issue('PAPER_MARKET_EVENT_TYPE_INVALID');continue
        key=(event.market_key,event.event_id)
        if key in seen_events:issue('PAPER_MARKET_EVENT_DUPLICATED')
        seen_events.add(key)
        if (event.market_key!=expected_market or event.product!=scope.product
                or event.instrument_uid!=scope.instrument_uid or event.source!=SOURCE
                or event.contract_version!='2026-10-29'):
            issue('PAPER_MARKET_EVENT_SCOPE_CHANGED')
        raw=item.raw_payload_json.encode('utf-8')
        if (sha256(raw).hexdigest()!=item.event_hash
                or canonical_json(domain_payload(event))!=item.raw_payload_json):
            issue('PAPER_MARKET_RAW_HASH_CHANGED')
        try:
            clock=item.observed_clock
            if (type(clock) is not t.ClockReading or clock.clock_health!='HEALTHY'
                    or type(clock.monotonic_ns) is not int or clock.monotonic_ns<0
                    or exact_time(event.occurred_at)>exact_time(clock.utc_now)
                    or exact_time(clock.utc_now)>exact_time(now)):
                issue('PAPER_MARKET_CLOCK_INVALID')
            if previous is not None and (previous.event.stream_id!=event.stream_id
                    or event.sequence<=previous.event.sequence
                    or exact_time(event.occurred_at)<exact_time(previous.event.occurred_at)):
                issue('PAPER_MARKET_EVENT_ORDER_INVALID')
            previous=item
            levels={}
            for before in item.before_levels:
                if type(before) is not PaperDepthState or before.side not in ('BID','ASK'):
                    raise ValueError('source depth type')
                price,reported,available=map(_number,(before.price,before.reported,before.available))
                index=(before.side,price)
                if index in levels:raise ValueError('source duplicate depth')
                if price<=0 or not 0<=available<=reported:raise ValueError('source depth range')
                levels[index]=(reported,available)
            present=set()
            for side,rows in (('BID',event.bids),('ASK',event.asks)):
                for row in rows:
                    price=_number(row.price);quantity=_number(row.quantity)
                    index=(side,price)
                    if price<=0 or index in present:raise ValueError('source level price')
                    present.add(index)
                    prior=levels.get(index)
                    if event.kind=='DEPTH_SNAPSHOT':
                        reported=quantity;available=quantity if prior is None else min(prior[1],quantity)
                    elif event.kind=='DEPTH_DELTA':
                        reported=(prior[0] if prior else Fraction(0))+quantity
                        available=max(Fraction(0),(prior[1] if prior else Fraction(0))+quantity)
                    else:raise ValueError('source event kind')
                    if not 0<=available<=reported:raise ValueError('source derived depth range')
                    levels[index]=(reported,available)
            if event.kind=='DEPTH_SNAPSHOT':
                levels={index:value for index,value in levels.items() if index in present}
            bids=[p for (side,p),(reported,_) in levels.items() if side=='BID' and reported>0]
            asks=[p for (side,p),(reported,_) in levels.items() if side=='ASK' and reported>0]
            if bids and asks and max(bids)>=min(asks):raise ValueError('source crossed depth')
            used=defaultdict(Fraction)
            for allocation in allocations.get(key,()):
                price,quantity=_number(allocation.price),_number(allocation.quantity)
                if allocation.side not in ('BID','ASK') or price<=0 or quantity<=0:
                    issue('PAPER_LIQUIDITY_ALLOCATION_INVALID');continue
                index=(allocation.side,price)
                used[index]+=quantity
                if index not in levels or used[index]>levels[index][1]:issue('PAPER_LIQUIDITY_OVERSPENT')
        except (ValueError,TypeError,AttributeError,InvalidOperation,OverflowError):
            issue('PAPER_DEPTH_STATE_INVALID')
    if any(key not in seen_events for key in allocations):issue('PAPER_LIQUIDITY_SOURCE_EVENT_MISSING')
    return tuple(errors)
