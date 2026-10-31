"""Independent exact V2 source/depth/all-rival conservation, not permission."""
from collections import defaultdict
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.contracts.order_lifecycle_v3 import exact_time
from trading_system.engine.paper_market import SOURCE,PaperMarketEvent,PaperMarketInput,PaperDepthState,PaperLiquidityConsumption
from trading_system.engine.paper_market_v2 import (PaperMarketEvidenceV2,PaperReferenceEventV2,PaperClockEventV2,
    PaperCalendarEventV2,PaperLiquidityOwnershipEventV2,PaperSTPPreventionV2)

EVENTS=(PaperMarketEvent,PaperReferenceEventV2,PaperClockEventV2,PaperCalendarEventV2,PaperLiquidityOwnershipEventV2)


def number(value):
    if type(value) is str:
        if not 0<len(value)<=16384:raise ValueError('numeric source size')
        value=Decimal(value)
    if type(value) is not Decimal or not value.is_finite():raise ValueError('finite decimal required')
    if value and (len(value.as_tuple().digits)>16384 or abs(value.adjusted())>4096):raise ValueError('numeric source range')
    return Fraction(value)


def source_depth_levels(event,before_levels):
    """Exact visible capacity; non-depth events carry the archived prior levels."""
    if type(event) not in EVENTS:raise ValueError('event type')
    event.__post_init__()
    if len(before_levels)>8192:raise ValueError('depth bound')
    levels={}
    for before in before_levels:
        if type(before) is not PaperDepthState or before.side not in ('BID','ASK'):raise ValueError('depth type')
        p,r,a=map(number,(before.price,before.reported,before.available));index=(before.side,p)
        if index in levels or p<=0 or not 0<=a<=r:raise ValueError('depth state')
        levels[index]=(r,a)
    if type(event) is PaperMarketEvent:
        present=set()
        for side,rows in (('BID',event.bids),('ASK',event.asks)):
            for row in rows:
                price,qty=number(row.price),number(row.quantity);index=(side,price)
                if index in present or price<=0:raise ValueError('depth duplicate')
                present.add(index);prior=levels.get(index,(Fraction(0),Fraction(0)))
                if event.kind=='DEPTH_SNAPSHOT':r,a=qty,(qty if index not in levels else min(prior[1],qty))
                else:r,a=prior[0]+qty,max(Fraction(0),prior[1]+qty)
                if not 0<=a<=r:raise ValueError('depth availability')
                levels[index]=(r,a)
        if event.kind=='DEPTH_SNAPSHOT':levels={i:v for i,v in levels.items() if i in present}
    return levels


def v2_market_evidence_codes(evidence,*,scope,command_id,request_hash,now):
    errors=[]
    def issue(code):
        if code not in errors:errors.append(code)
    if type(evidence) is not PaperMarketEvidenceV2:return ('PAPER_V2_MARKET_EVIDENCE_REQUIRED',)
    if (type(evidence.inputs) is not tuple or type(evidence.consumptions) is not tuple or type(evidence.preventions) is not tuple
            or len(evidence.inputs)>4096 or len(evidence.consumptions)+len(evidence.preventions)>100000):
        return ('PAPER_V2_MARKET_SOURCE_BOUND',)
    if (evidence.contract_version!='2026-10-30' or evidence.record_kind!='PAPER_MARKET_EVIDENCE_V2'
            or evidence.command_id!=command_id or evidence.request_hash!=request_hash
            or scope.environment!=t.Environment.PAPER):issue('PAPER_V2_MARKET_REQUEST_CHANGED')
    market=canonical_hash(dict(environment='PAPER',venue='BINANCE',product=str(scope.product),instrument_uid=scope.instrument_uid))
    try:
        body=dict(command_id=evidence.command_id,request_hash=evidence.request_hash,
            inputs=[domain_payload(i) for i in evidence.inputs],consumptions=[domain_payload(c) for c in evidence.consumptions],
            preventions=[domain_payload(p) for p in evidence.preventions],contract_version=evidence.contract_version,record_kind=evidence.record_kind)
        encoded=canonical_json(body).encode('utf-8');ref=evidence.evidence
        if len(encoded)>16777216:return ('PAPER_V2_MARKET_SOURCE_BOUND',)
        if (ref.source!=SOURCE or ref.environment!=t.Environment.PAPER or ref.id!='paper-market-v2-source:'+command_id
                or exact_time(ref.visible_at)>exact_time(now) or canonical_hash(body)!=ref.hash):issue('PAPER_V2_MARKET_SOURCE_HASH_CHANGED')
    except (ValueError,TypeError,AttributeError):return ('PAPER_V2_MARKET_SOURCE_SHAPE_INVALID',)
    allocations=defaultdict(list);preventions=defaultdict(list);seen=set()
    for allocation in (*evidence.consumptions,*evidence.preventions):
        if type(allocation) not in (PaperLiquidityConsumption,PaperSTPPreventionV2):issue('PAPER_V2_ALLOCATION_TYPE_INVALID');continue
        s=allocation.scope
        if (type(s) is not t.ScopeKey or s.environment!=t.Environment.PAPER or s.product!=scope.product
                or s.instrument_uid!=scope.instrument_uid or allocation.market_key!=market):
            issue('PAPER_V2_ALLOCATION_SCOPE_CHANGED');continue
        identifier=allocation.fill_id if type(allocation) is PaperLiquidityConsumption else allocation.prevention_id
        key=(s.venue_account_id,identifier,type(allocation).__name__)
        if key in seen:issue('PAPER_V2_ALLOCATION_DUPLICATED')
        seen.add(key)
        target=allocations if type(allocation) is PaperLiquidityConsumption else preventions
        target[(allocation.market_key,allocation.event_id)].append(allocation)
    known={};last=None;ownership={};current_depth=None
    for item in evidence.inputs:
        try:
            if type(item) is not PaperMarketInput or type(item.event) not in EVENTS:raise ValueError('event type')
            event=item.event;key=(event.market_key,event.event_id)
            if key in known:issue('PAPER_V2_INPUT_DUPLICATED')
            known[key]=item
            if (event.market_key!=market or event.product!=scope.product or event.instrument_uid!=scope.instrument_uid
                    or event.source!=SOURCE):issue('PAPER_V2_INPUT_SCOPE_CHANGED')
            event.__post_init__()
            raw=item.raw_payload_json.encode('utf-8')
            if len(raw)>1048576 or sha256(raw).hexdigest()!=item.event_hash or canonical_json(domain_payload(event))!=item.raw_payload_json:
                issue('PAPER_V2_INPUT_RAW_HASH_CHANGED')
            clock=item.observed_clock
            if (type(clock) is not t.ClockReading or clock.clock_health!='HEALTHY' or type(clock.monotonic_ns) is not int
                    or clock.monotonic_ns<0 or not exact_time(event.occurred_at)<=exact_time(clock.utc_now)<=exact_time(now)):
                issue('PAPER_V2_INPUT_CLOCK_CHANGED')
            if last is not None and (last.event.stream_id!=event.stream_id or last.event.sequence>=event.sequence
                    or exact_time(last.event.occurred_at)>exact_time(event.occurred_at)
                    or last.observed_clock.monotonic_ns>clock.monotonic_ns):issue('PAPER_V2_INPUT_ORDER_CHANGED')
            last=item
            levels=source_depth_levels(event,item.before_levels)
            if type(event) is PaperMarketEvent:current_depth=event.event_id
            # Every other event leaves depth unchanged. In particular a clock,
            # reference or session window is never fresh executable liquidity.
            if type(event) is PaperLiquidityOwnershipEventV2:
                depth=known.get((market,event.depth_event_id))
                if depth is None or type(depth.event) is not PaperMarketEvent or depth.event_hash!=event.depth_hash or event.depth_event_id!=current_depth:
                    issue('PAPER_V2_OWNERSHIP_DEPTH_LINK_CHANGED')
                for owner in event.owners:
                    if levels.get((owner.side,number(owner.price)),(None,None))[0]!=number(owner.source_quantity):
                        issue('PAPER_V2_OWNERSHIP_QUANTITY_CHANGED')
                    ownership[(event.depth_event_id,owner.side,number(owner.price))]=owner
            used=defaultdict(Fraction)
            for allocation in allocations.get(key,()):
                price,qty=number(allocation.price),number(allocation.quantity);index=(allocation.side,price)
                if allocation.side not in ('BID','ASK') or price<=0 or qty<=0:raise ValueError('allocation value')
                used[index]+=qty
            for prevention in preventions.get(key,()):
                price,maker,taker=map(number,(prevention.price,prevention.maker_quantity,prevention.taker_quantity));index=(prevention.side,price)
                owner=ownership.get((prevention.depth_event_id,prevention.side,price))
                if (maker<0 or taker<0 or maker+taker<=0 or owner is None
                        or owner.venue_account_id!=prevention.venue_account_id or owner.venue_order_id!=prevention.venue_order_id
                        or prevention.venue_account_id!=prevention.scope.venue_account_id
                        or prevention.mode not in ('EXPIRE_TAKER','EXPIRE_MAKER','EXPIRE_BOTH','DECREMENT')):
                    issue('PAPER_V2_PREVENTION_SOURCE_CHANGED')
                used[index]+=maker
            if any(index not in levels or qty>levels[index][1] for index,qty in used.items()):issue('PAPER_V2_LIQUIDITY_OVERSPENT')
        except (ValueError,TypeError,AttributeError,ArithmeticError):issue('PAPER_V2_DEPTH_OR_INPUT_INVALID')
    if any(key not in known for key in (*allocations,*preventions)):issue('PAPER_V2_ALLOCATION_INPUT_MISSING')
    return tuple(errors)
