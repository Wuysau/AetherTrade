"""Independent native-parent observation source; never authorizes writes/posting.

Exact tagged source is intentionally not registered as ordinary V1/V2 evidence.
"""
from dataclasses import dataclass,replace
from decimal import Decimal,localcontext,Inexact,Rounded
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.execution.paper_program_reports import (PaperProgramReportVerifier,
    PaperProgramVerification,NormalizedPaperFill,normalize_native_economics,_check,_SourceError,_raw_object,digest)
from trading_system.storage.models import scope_id


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeParentVerification(PaperProgramVerification):
    record_kind:Literal['PAPER_NATIVE_PARENT_VERIFICATION_V1']='PAPER_NATIVE_PARENT_VERIFICATION_V1'
    contract_version:Literal['2026-10-30']='2026-10-30'


class PaperNativeParentReportVerifier(PaperProgramReportVerifier):
    def _result(self,*args,**kwargs):
        from dataclasses import fields
        result=super()._result(*args,**kwargs)
        return PaperNativeParentVerification(**{f.name:getattr(result,f.name) for f in fields(result)})

    def verify(self,command_id):
        from trading_system.engine.paper import PaperEngine
        from trading_system.engine.nautilus.native_events import NativeEventBridge
        from trading_system.engine.paper_native_programs import PaperNativeExecutionReportV2
        from trading_system.engine.paper_market_v2 import PaperMarketEvidenceV2
        _check(type(command_id) is str and 0<len(command_id)<=256,'ORIGINAL_COMMAND_ID_REQUIRED')
        if (type(self.engine) is not PaperEngine or self.engine.scope!=self.scope
            or type(self.archive_bridge) is not NativeEventBridge or self.archive_bridge.codec.scope!=self.scope):
            return self._result(command_id,'BLOCKED',('PAPER_TRUSTED_READERS_REQUIRED',))
        report=market=None;refs=[]
        try:
            original,bundle,consumed,assessment=self._load(command_id)
            clock=self.engine._clock()
            report=self.engine.query_creation(command_id)
            if report is None:return self._result(command_id,'NOT_OBSERVED')
            for item in (report,*getattr(report,'fills',()),*getattr(report,'dispositions',())):
                raw=getattr(item,'raw_payload_json',None)
                refs.append(self._archive(raw.encode() if type(raw) is str else domain_payload(item),clock))
            market=self.engine.query_market_evidence(command_id)
            refs.append(self._archive(domain_payload(market),clock))
            _check(type(report) is PaperNativeExecutionReportV2 and type(market) is PaperMarketEvidenceV2,
                'PAPER_NATIVE_EXACT_SOURCE_TYPE_REQUIRED')
            _check(clock.clock_health=='HEALTHY','PAPER_NATIVE_SOURCE_CLOCK_REQUIRED')
            with localcontext() as arithmetic:
                arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
                fills,codes=self._authenticate(original,bundle,consumed,assessment,report,market,clock,refs)
            return self._result(command_id,'DISCREPANCY' if codes else 'VERIFIED',codes,report=report,market=market,
                refs=tuple(refs),fills=tuple(fills),verified=True)
        except _SourceError as exc:
            return self._result(command_id,'BLOCKED',(str(exc),),report=report,market=market,refs=tuple(refs))
        except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError,StopIteration):
            return self._result(command_id,'BLOCKED',('PAPER_NATIVE_SOURCE_SHAPE_INVALID',),report=report,market=market,refs=tuple(refs))

    def _authenticate(self,original,bundle,consumed,a,report,market,clock,refs):
        write=original.initial;request=original.request;parent_id=request.assigned_ids.parent_client_id
        _check(not request.assigned_ids.members and parent_id is not None and report.command_id==market.command_id==write.command_id
            and digest(report.request)==write.request_hash==market.request_hash,'PAPER_NATIVE_ORIGINAL_CHANGED')
        _check(report.record_kind=='PAPER_NATIVE_EXECUTION_REPORT_V2' and report.record_revision==2,'PAPER_NATIVE_TAG_CHANGED')
        with self.engine._connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            params=(scope_id(self.scope),parent_id)
            parent=db.execute('SELECT p.*,t.terms,g.* FROM public.paper_native_parents_v2 p '
                'JOIN public.paper_native_parent_terms_v2 t USING(scope_key,parent_client_id,command_id) '
                'JOIN public.paper_native_algorithm_progress_v2 g USING(scope_key,parent_client_id) '
                'WHERE p.scope_key=%s AND p.parent_client_id=%s',params).fetchone()
            _check(parent is not None,'PAPER_NATIVE_PARENT_SOURCE_REQUIRED')
            members=db.execute('SELECT m.*,o.order_id,o.quantity,o.filled,o.lifecycle,o.revision,o.quantity_unit,t.terms '
                'FROM public.paper_native_members_v2 m JOIN public.paper_engine_orders o USING(scope_key,client_id) '
                'JOIN public.paper_order_terms t USING(scope_key,client_id) WHERE m.scope_key=%s AND m.parent_client_id=%s '
                'ORDER BY ordinal LIMIT 4097',params).fetchall()
            allocations=db.execute('SELECT * FROM public.paper_native_child_allocations_v2 WHERE scope_key=%s AND parent_client_id=%s '
                'ORDER BY ordinal LIMIT 4097',params).fetchall()
            clients=[m['client_id'] for m in members]
            events=db.execute('SELECT event_id,client_id,payload::text AS raw,payload_hash,fill_delta,price '
                'FROM public.paper_engine_events WHERE scope_key=%s AND client_id=ANY(%s) ORDER BY event_id LIMIT 4097',
                (params[0],clients)).fetchall()
            sources=db.execute('SELECT i.* FROM public.paper_native_parent_input_dependencies_v2 d JOIN public.paper_market_inputs i '
                'USING(market_key,event_id) WHERE d.scope_key=%s AND d.parent_client_id=%s ORDER BY i.sequence LIMIT 4097',params).fetchall()
            consumptions=db.execute('SELECT c.* FROM public.paper_market_consumptions c JOIN public.paper_native_parent_input_dependencies_v2 d '
                'USING(market_key,event_id) WHERE d.scope_key=%s AND d.parent_client_id=%s ORDER BY c.fill_id LIMIT 100001',params).fetchall()
        _check(len(members)<=4096 and len(events)<=4096 and len(sources)<=4096 and len(consumptions)<=100000,'PAPER_NATIVE_SOURCE_BOUND')
        # Archive source rows as explicit canonical strings before comparing DTOs.
        def serial(value):
            if isinstance(value,Decimal):return format(value,'f')
            if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
            if isinstance(value,(list,tuple)):return [serial(v) for v in value]
            if hasattr(value,'isoformat'):return value.isoformat()
            return value
        refs.append(self._archive(serial(dict(parent=dict(parent),members=members,allocations=allocations,
            events=events,sources=sources,consumptions=consumptions)),clock))
        p=report.native_parent;pool=report.pool;ctx=report.accepted_contexts[0];terms=parent['terms']
        _check(parent['command_id']==write.command_id and parent['request_hash']==write.request_hash
            and p.parent_client_id==parent_id and p.venue_parent_id==parent['venue_parent_id']
            and p.namespace==request.namespace.value==parent['namespace'] and p.template==parent['template']=='TWAP'
            and p.status==parent['status'] and p.lifecycle_revision==parent['revision'],'PAPER_NATIVE_PARENT_CHANGED')
        _check(digest(ctx.original_write)==canonical_hash(consumed['write']) and digest(ctx.request)==write.request_hash
            and digest(ctx.compilation)==digest(a.compilation) and digest(ctx.rules)==digest(a.rules)
            and ctx.execution_envelope.consumption_context_hash==canonical_hash(consumed)
            and ctx.execution_envelope.assessment_hash==digest(a),'PAPER_NATIVE_AUTHORIZATION_CHANGED')
        expected=domain_payload(ctx.terms)|dict(write=domain_payload(ctx.original_write),request=domain_payload(ctx.request),
            compilation=domain_payload(ctx.compilation),rules=domain_payload(ctx.rules),model=domain_payload(ctx.model),
            execution_envelope=domain_payload(ctx.execution_envelope))
        _check(expected==terms,'PAPER_NATIVE_ACCEPTED_CONTEXT_CHANGED')
        for key in ('original_quantity','allocated_quantity','executed_quantity','unused_quantity','maximum_gross_notional',
                    'allocated_gross_notional','cumulative_gross_notional','maximum_live_children'):
            _check(getattr(pool,key)==parent[key],'PAPER_NATIVE_POOL_CHANGED')
        _check(pool.maximum_gross_notional==a.assessment.metrics.maximum_order_notional.amount
            and pool.original_quantity==Decimal(a.compilation.nodes[0].quantity.value)
            and pool.remaining_pool_quantity==pool.original_quantity-pool.allocated_quantity
            and [domain_payload(f) for f in pool.fee_ceilings]==parent['fee_ceilings'],'PAPER_NATIVE_BUDGET_CHANGED')
        _check(len(members)==len(allocations)==len(report.orders)==len(p.members)
            and len(report.accepted_contexts)==len(members)+1,'PAPER_NATIVE_CHILD_INVENTORY_CHANGED')
        child_map={c.terms.client_id:c for c in report.accepted_contexts[1:]}
        for row,allocation,order,member in zip(members,allocations,report.orders,p.members):
            client=row['client_id'];c=child_map[client]
            _check(row['member_kind']=='ALGORITHM_CHILD' and allocation['child_client_id']==order.client_id==member.client_id==client
                and allocation['parent_client_id']==c.allocation.parent_client_id==parent_id
                and allocation['ordinal']==c.allocation.ordinal==row['ordinal']
                and allocation['allocation_id']==c.allocation.allocation_id==c.terms.allocation_id
                and order.venue_order_id==member.venue_order_id==row['order_id']
                and order.quantity==allocation['quantity']==row['quantity'] and order.filled_quantity==row['filled']
                and order.status==row['lifecycle'] and order.lifecycle_revision==row['revision']
                and order.quantity_unit==row['quantity_unit'] and order.node_id==row['node_id']
                and order.namespace==c.terms.namespace==a.compilation.nodes[0].namespace.value,
                'PAPER_NATIVE_CHILD_IDENTITY_CHANGED')
            expected=domain_payload(c.terms)|dict(write=domain_payload(c.original_write),request=domain_payload(c.request),
                compilation=domain_payload(c.compilation),rules=domain_payload(c.rules),model=domain_payload(c.model),
                execution_envelope=domain_payload(c.execution_envelope))
            _check(expected==row['terms'] and digest(c.original_write)==digest(ctx.original_write)
                and digest(c.compilation)==digest(a.compilation) and digest(c.request)==write.request_hash,
                'PAPER_NATIVE_CHILD_CONTEXT_CHANGED')
        source_map={(s['market_key'],s['event_id']):s for s in sources}
        _check(len(market.inputs)==len(source_map),'PAPER_NATIVE_INPUT_INVENTORY_CHANGED')
        for i in market.inputs:
            s=source_map[(i.event.market_key,i.event.event_id)]
            _check(domain_payload(i.event)==s['payload'] and i.event_hash==s['payload_hash']==digest(i.event)
                and domain_payload(i.observed_clock)==s['observed_clock']
                and [domain_payload(v) for v in i.before_levels]==s['before_levels'],'PAPER_NATIVE_INPUT_CHANGED')
        def key(c):return (c['market_key'],c['event_id'],c['scope_key'],c['client_id'],c['fill_id'],c['side'],c['price'],c['quantity'])
        observed=[dict(market_key=c.market_key,event_id=c.event_id,scope_key=scope_id(c.scope),client_id=c.client_id,
            fill_id=c.fill_id,side=c.side,price=c.price,quantity=c.quantity) for c in market.consumptions]
        _check(sorted(map(key,consumptions))==sorted(map(key,observed)),'PAPER_NATIVE_LIQUIDITY_CHANGED')
        events={e['event_id']:e for e in events};fills=[];codes=[]
        _check(set(events)=={f.fill_id for f in report.fills}|{d.event_id for d in report.dispositions}
            and len(events)==len(report.fills)+len(report.dispositions),'PAPER_NATIVE_EVENT_INVENTORY_CHANGED')
        for f in report.fills:
            e=events[f.fill_id];raw=_raw_object(f.raw_payload_json,f.evidence);c=child_map[f.client_id]
            _check(e['raw']==f.raw_payload_json and e['payload_hash']==f.evidence.hash and e['client_id']==f.client_id
                and e['fill_delta']==f.quantity and e['price']==f.price and Decimal(raw['gross_notional'])==f.gross_notional
                and raw['fees']==[domain_payload(v) for v in f.fees] and f.namespace==c.terms.namespace
                and f.node_id==c.terms.node_id and f.quantity_unit==bundle.intent.unit.native_unit,
                'PAPER_NATIVE_FILL_CHANGED')
            _check(any(r['fill_id']==f.fill_id and r['scope_key']==scope_id(self.scope) and r['client_id']==f.client_id
                and r['quantity']==f.quantity and r['price']==f.price for r in consumptions),'PAPER_NATIVE_FILL_ALLOCATION_REQUIRED')
            economics=normalize_native_economics(quantity=t.Quantity(value=f.quantity,unit=f.quantity_unit),
                price=t.Price(value=f.price,currency=f.price_asset),fees=tuple(t.Money(currency=v.asset,amount=Decimal(v.amount)) for v in f.fees),
                reported_gross=t.Money(currency=f.price_asset,amount=f.gross_notional),unit=bundle.intent.unit)
            codes.extend(economics.discrepancies)
            identity=t.OrderIdentity(scope=self.scope,logical_client_order_id=f.client_id,native_client_order_id=f.client_id,
                venue_client_order_id=f.client_id,venue_order_id=next(o.venue_order_id for o in report.orders if o.client_id==f.client_id))
            fact=t.Fill(fill_id='paper-native:'+canonical_hash(dict(scope=domain_payload(self.scope),native_trade_id=f.fill_id)),
                venue='BINANCE',scope=self.scope,native_trade_id=f.fill_id,order=identity,quantity=economics.native_quantity,
                price=economics.venue_price,fees=economics.fees or (t.Money(currency=f.price_asset,amount=Decimal(0)),),
                occurred_at=f.occurred_at,correction_of=None,evidence=next(r for r in refs if r.hash==f.evidence.hash))
            fills.append(NormalizedPaperFill(fill=fact,side=c.terms.side,economics=economics,raw=f,fees_known=True))
        for d in report.dispositions:
            e=events[d.event_id];raw=_raw_object(d.raw_payload_json,d.evidence)
            _check(e['raw']==d.raw_payload_json and e['payload_hash']==d.evidence.hash and e['client_id']==d.client_id
                and raw['status']==d.status and raw['reason']==d.reason,'PAPER_NATIVE_DISPOSITION_CHANGED')
        if sum((f.quantity for f in report.fills),Decimal(0))>pool.original_quantity:codes.append('NATIVE_PARENT_OVERFILLED')
        if sum((o.remaining_quantity for o in report.orders),Decimal(0))+pool.executed_quantity+pool.remaining_pool_quantity>pool.original_quantity:
            codes.append('NATIVE_PARENT_REMAINING_EXPOSURE_EXCEEDED')
        return fills,codes
