"""Independent frozen PAPER V2 decision checks. Observations confer no authority."""
from collections import defaultdict
from decimal import Decimal
from fractions import Fraction

from trading_system.contracts import types as t
from trading_system.contracts.order_lifecycle_v3 import exact_time
from trading_system.engine.paper_market import PaperMarketEvent
from trading_system.engine.paper_market_v2 import PaperCalendarEventV2,PaperLiquidityOwnershipEventV2
from .paper_program_reports import _check,_raw_object


def reported_levels(item):
    levels={(v.side,Fraction(Decimal(v.price))):Fraction(Decimal(v.reported)) for v in item.before_levels}
    event=item.event
    if type(event) is PaperMarketEvent:
        if event.kind=='DEPTH_SNAPSHOT':levels={}
        for side,rows in (('BID',event.bids),('ASK',event.asks)):
            for row in rows:
                key=(side,Fraction(Decimal(row.price)));quantity=Fraction(Decimal(row.quantity))
                levels[key]=quantity if event.kind=='DEPTH_SNAPSHOT' else levels.get(key,Fraction(0))+quantity
    return levels


def check_order_semantics(scope,spec,order,inputs,fills,preventions,dispositions,accepted_at):
    by_id={i.event.event_id:i for i in inputs}
    # Peg/priceMatch fixes placement price once; later quotes cannot reprice it.
    if spec.price.kind in ('PEG','PRICE_MATCH'):
        same=spec.price.peg_type=='PRIMARY_PEG' if spec.price.kind=='PEG' else spec.price.price_match.startswith('QUEUE')
        offset=spec.price.offset_levels if spec.price.kind=='PEG' else (
            int(spec.price.price_match.split('_')[1])-1 if '_' in spec.price.price_match else 0)
        side='BID' if (spec.side=='BUY')==same else 'ASK';resolved=None
        active=spec.trigger.kind=='NONE'
        for item in inputs:
            if item.event.event_id==order.runtime.activation_event_id:active=True
            if not active:continue
            levels=reported_levels(item)
            prices=sorted((p for (s,p),q in levels.items() if s==side and q>0),reverse=side=='BID')
            if len(prices)>offset:resolved=prices[offset];break
        _check((resolved is None and order.runtime.effective_limit is None) or
            resolved is not None and order.runtime.effective_limit is not None and Fraction(order.runtime.effective_limit)==resolved,
            'PAPER_V2_PLACEMENT_PRICE_CHANGED')
        _check(not fills or resolved is not None,'PAPER_V2_PLACEMENT_DEPTH_REQUIRED')
        _check(all(Fraction(f.price)<=resolved if spec.side=='BUY' else Fraction(f.price)>=resolved for f in fills),
            'PAPER_V2_FILL_OUTSIDE_PLACEMENT_LIMIT')
    if spec.iceberg_quantity is not None:
        tranche=Fraction(Decimal(spec.iceberg_quantity));per_input=defaultdict(Fraction)
        for fill in fills:per_input[fill.market_event_id]+=Fraction(fill.quantity)
        _check(all(q<=tranche for q in per_input.values()),'PAPER_V2_ICEBERG_TRANCHE_EXCEEDED')
        _check(order.runtime.visible_remaining is not None and 0<=Fraction(order.runtime.visible_remaining)<=tranche,
            'PAPER_V2_ICEBERG_DISPLAY_CHANGED')
    if spec.tif.kind=='GTD':
        expiry=exact_time(spec.tif.expires_at)
        _check(all(exact_time(f.occurred_at)<expiry for f in fills),'PAPER_V2_GTD_FILL_AFTER_EXPIRY')
        for d in dispositions:
            if d.reason=='VENUE_GTD_EXPIRED':_check(exact_time(d.occurred_at)>=expiry,'PAPER_V2_GTD_EXPIRY_CLOCK_CHANGED')
    if scope.product==t.Product.US_EQUITY:
        calendars={i.event.event_id:i for i in inputs if type(i.event) is PaperCalendarEventV2}
        session=spec.trading_session or 'RTH'
        for fill in fills:
            raw=_raw_object(fill.raw_payload_json,fill.evidence);calendar=calendars.get(raw.get('calendar_event_id'))
            _check(calendar is not None,'PAPER_V2_FILL_CALENDAR_REQUIRED')
            at=exact_time(fill.occurred_at);source=by_id[fill.market_event_id]
            _check(calendar.event.sequence<=source.event.sequence and exact_time(calendar.observed_clock.utc_now)<=at
                and at<exact_time(calendar.event.valid_until) and any(w.session==session
                    and exact_time(w.opens_at)<=at<exact_time(w.closes_at) for w in calendar.event.windows),
                'PAPER_V2_FILL_OUTSIDE_SESSION')
        for d in dispositions:
            if d.reason!='VENUE_DAY_EXPIRED':continue
            raw=_raw_object(d.raw_payload_json,d.evidence);calendar=calendars.get(raw.get('calendar_event_id'))
            _check(calendar is not None,'PAPER_V2_DAY_CALENDAR_REQUIRED')
            closes=[exact_time(w.closes_at) for w in calendar.event.windows if w.session==session and exact_time(w.closes_at)>accepted_at]
            _check(closes and exact_time(raw['day_expires_at'])==min(closes)<=exact_time(d.occurred_at),
                'PAPER_V2_DAY_EXPIRY_CHANGED')
    own=tuple(p for p in preventions if p.scope==scope and p.client_id==order.client_id)
    _check(all(p.mode==spec.stp for p in own),'PAPER_V2_STP_MODE_CHANGED')
    _check(sum((Fraction(p.taker_quantity) for p in own),Fraction(0))==Fraction(order.runtime.prevented_quantity),
        'PAPER_V2_PREVENTED_CUMULATIVE_CHANGED')
    if spec.stp=='NONE':_check(not own,'PAPER_V2_UNEXPECTED_PREVENTION');return
    # Each prevention must have its actual disposition and same-account maker.
    disposition_map={d.event_id:d for d in dispositions}
    for p in own:
        d=disposition_map.get(p.prevention_id)
        _check(d is not None and d.reason=='STP_'+p.mode and p.venue_account_id==scope.venue_account_id,
            'PAPER_V2_STP_DISPOSITION_REQUIRED')
        raw=_raw_object(d.raw_payload_json,d.evidence)
        _check(Fraction(Decimal(raw['maker_quantity']))==Fraction(p.maker_quantity)
            and Fraction(Decimal(raw['taker_quantity']))==Fraction(p.taker_quantity)
            and raw['depth_event_id']==p.depth_event_id,'PAPER_V2_STP_DISPOSITION_QUANTITY_CHANGED')
        _check((p.mode!='EXPIRE_TAKER' or p.maker_quantity==0 and p.taker_quantity>0)
            and (p.mode!='EXPIRE_MAKER' or p.taker_quantity==0 and p.maker_quantity>0)
            and (p.mode!='EXPIRE_BOTH' or p.maker_quantity>0 and p.taker_quantity>0)
            and (p.mode!='DECREMENT' or p.maker_quantity==p.taker_quantity>0),'PAPER_V2_STP_MODE_QUANTITY_CHANGED')
    for fill in fills:
        source=by_id[fill.market_event_id];depth=None;owners={}
        for item in inputs:
            if item.event.sequence>source.event.sequence:break
            if type(item.event) is PaperMarketEvent:depth=item.event.event_id
            elif type(item.event) is PaperLiquidityOwnershipEventV2:
                for owner in item.event.owners:owners[(item.event.depth_event_id,owner.side,Fraction(Decimal(owner.price)))]=owner
        owner=owners.get((depth,'ASK' if spec.side=='BUY' else 'BID',Fraction(fill.price)))
        _check(owner is not None and owner.venue_account_id!=scope.venue_account_id,'PAPER_V2_STP_FILL_EXTERNAL_OWNER_REQUIRED')
