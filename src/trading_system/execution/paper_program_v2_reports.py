"""Trusted command-ID-only PAPER V2 observation verifier; no ledger posting."""
from dataclasses import dataclass,replace
from decimal import Decimal,localcontext,Inexact,Rounded
from fractions import Fraction
from hashlib import sha256
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.contracts.order_lifecycle_v3 import exact_time
from trading_system.contracts.order_lifecycle import AssetBudget
from trading_system.engine.paper_market import SOURCE,PaperMarketFill,PaperExecutionEnvelope,PaperMarketEvent
from trading_system.engine.paper_market_v2 import (PaperCreationReportV2,PaperMarketEvidenceV2,PaperAcceptedContextV2,
    PaperExecutionTermsV2,PaperReferenceEventV2,PaperMatchingModelV2,execution_tif)
from .paper_program_reports import (NativeFillEconomics,normalize_native_economics,PaperProgramReportVerifier,
    _SourceError,_check,_raw_object,_raw_decimal)
from .paper_program_v2_liquidity import v2_market_evidence_codes


def digest(value):return canonical_hash(domain_payload(value))


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperFillSourceV2:
    node_id:str
    client_id:str
    accepted_context_hash:str
    consumption_context_hash:str
    causal_input_ids:tuple[tuple[str,str],...]
    parent_client_id:str|None
    contract_version:Literal['2026-10-30']='2026-10-30'
    record_kind:Literal['PAPER_FILL_SOURCE_V2']='PAPER_FILL_SOURCE_V2'


@dataclass(frozen=True,slots=True,kw_only=True)
class NormalizedPaperFillV2:
    fill:t.Fill
    side:Literal['BUY','SELL']
    economics:NativeFillEconomics
    raw:PaperMarketFill
    fees_known:bool
    source_v2:PaperFillSourceV2
    contract_version:Literal['2026-10-30']='2026-10-30'
    record_kind:Literal['NORMALIZED_PAPER_FILL_V2']='NORMALIZED_PAPER_FILL_V2'


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperProgramVerificationV2:
    command_id:str
    status:Literal['BLOCKED','NOT_OBSERVED','VERIFIED','DISCREPANCY']
    source_verified:bool
    fills:tuple[NormalizedPaperFillV2,...]
    orders:tuple[t.OrderProjection,...]
    raw_refs:tuple[t.EvidenceRef,...]
    discrepancies:tuple[str,...]
    retained_duties:tuple[str,...]
    raw_report:object|None=None
    market_evidence:object|None=None
    contract_version:Literal['2026-10-30']='2026-10-30'
    record_kind:Literal['PAPER_PROGRAM_VERIFICATION_V2']='PAPER_PROGRAM_VERIFICATION_V2'
    synthetic:Literal[True]=True
    execution_authorized:Literal[False]=False
    funds_release_authorized:Literal[False]=False


class PaperProgramV2ReportVerifier:
    def __init__(self,uow,scope,*,engine=None,archive_bridge=None):
        _check(type(scope) is t.ScopeKey and scope.environment==t.Environment.PAPER,'PAPER_ONLY')
        self.uow,self.scope,self.engine,self.archive_bridge=uow,scope,engine,archive_bridge

    def _result(self,command,status,codes=(),*,report=None,market=None,refs=(),fills=(),orders=(),verified=False):
        duties=['QUERY_ORIGINAL_IDS','RESERVATION_RECONCILIATION_REQUIRED','ACCOUNT_HISTORY_NOT_VERIFIED']
        if fills:duties+=['PROTECTION_RECHECK_REQUIRED','OWNERSHIP_ALLOCATION_RECONCILIATION_REQUIRED']
        if any(not f.fees_known for f in fills):duties.append('FEE_RECONCILIATION_REQUIRED')
        if codes:duties.append('SOURCE_OR_ECONOMIC_RECONCILIATION_REQUIRED')
        return PaperProgramVerificationV2(command_id=command,status=status,source_verified=verified,fills=fills,orders=orders,
            raw_refs=tuple(refs),discrepancies=tuple(dict.fromkeys(codes)),retained_duties=tuple(duties),raw_report=report,market_evidence=market)

    def _archive(self,value,clock):
        raw=value if type(value) is bytes else canonical_json(value).encode('utf-8')
        return self.archive_bridge.ingest(raw,source=SOURCE,received_at=clock.utc_now,processed_at=clock.utc_now).raw.raw_payload_ref

    def _manifest(self,original,clock,refs):
        members=original.request.assigned_ids.members;clients=[m.client_id for m in members]
        with self.engine._connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            deps=db.execute('SELECT d.client_id,d.market_key,d.event_id,i.payload_hash FROM public.paper_order_input_dependencies_v2 d '
                'JOIN public.paper_market_inputs i USING(market_key,event_id) WHERE d.scope_key=%s AND d.client_id=ANY(%s) '
                'ORDER BY d.client_id,i.sequence,i.event_id LIMIT 16385',(self.engine._scope_key,clients)).fetchall()
            events=db.execute('SELECT client_id,event_id,payload::text AS raw,payload_hash FROM public.paper_engine_events '
                'WHERE scope_key=%s AND client_id=ANY(%s) ORDER BY occurred_at,event_id LIMIT 4097',
                (self.engine._scope_key,clients)).fetchall()
            orders=db.execute('SELECT client_id,order_id,revision FROM public.paper_engine_orders '
                'WHERE scope_key=%s AND client_id=ANY(%s) ORDER BY client_id LIMIT 513',(self.engine._scope_key,clients)).fetchall()
        snapshot=dict(record_kind='PAPER_V2_RESTRICTED_SOURCE_MANIFEST',dependencies=[dict(d) for d in deps],
            events=[dict(e) for e in events],orders=[dict(o) for o in orders])
        refs.append(self._archive(snapshot,clock))
        _check(len(deps)<=16384 and len(events)<=4096 and len(orders)<=512,'PAPER_V2_SOURCE_MANIFEST_BOUND')
        return deps,{e['event_id']:e for e in events},{o['client_id']:o for o in orders}

    def verify(self,command_id):
        from trading_system.engine.paper import PaperEngine
        from trading_system.engine.nautilus.native_events import NativeEventBridge
        _check(type(command_id) is str and 0<len(command_id)<=256,'ORIGINAL_COMMAND_ID_REQUIRED')
        if (self.uow is None or type(self.engine) is not PaperEngine or self.engine.scope!=self.scope
                or type(self.archive_bridge) is not NativeEventBridge or self.archive_bridge.codec.scope!=self.scope):
            return self._result(command_id,'BLOCKED',('PAPER_TRUSTED_READERS_REQUIRED',))
        report=market=None;refs=[]
        try:
            original,bundle,consumed,assessment=PaperProgramReportVerifier._load(self,command_id)
            report=self.engine.query_creation(command_id)
            if report is None:return self._result(command_id,'NOT_OBSERVED',('PAPER_QUERY_ORIGINAL_IDS_REQUIRED',))
            market=self.engine.query_market_evidence(command_id);clock=self.engine._clock()
            _check(type(clock) is t.ClockReading,'PAPER_V2_SOURCE_CLOCK_REQUIRED')
            # Full snapshots/raw blobs are durable before model/shape/hash claims.
            for observed in (report,market):
                for item in (*getattr(observed,'fills',()),*getattr(observed,'dispositions',()),*getattr(observed,'inputs',())):
                    raw=getattr(item,'raw_payload_json',None)
                    if type(raw) is str:refs.append(self._archive(raw.encode('utf-8'),clock))
                refs.append(self._archive(domain_payload(observed),clock))
            _check(type(report) is PaperCreationReportV2 and type(market) is PaperMarketEvidenceV2,'PAPER_V2_SOURCE_INTERFACE_UNSUPPORTED')
            _check(clock.clock_health=='HEALTHY','PAPER_V2_SOURCE_CLOCK_REQUIRED')
            manifest=self._manifest(original,clock,refs)
            fills,orders,codes=self._normalize(original,bundle,consumed,assessment,report,market,clock,refs,manifest)
            return self._result(command_id,'DISCREPANCY' if codes else 'VERIFIED',codes,report=report,market=market,
                refs=refs,fills=fills,orders=orders,verified=True)
        except _SourceError as exc:return self._result(command_id,'BLOCKED',(str(exc),),report=report,market=market,refs=refs)
        except (ValueError,TypeError,AttributeError,KeyError,ArithmeticError,StopIteration):
            return self._result(command_id,'BLOCKED',('PAPER_V2_SOURCE_SHAPE_OR_ECONOMICS_INVALID',),report=report,market=market,refs=refs)

    def _normalize(self,original,bundle,consumed,a,report,market,clock,refs,manifest):
        from trading_system.storage.models import scope_id
        write=original.initial;request=original.request;plan=a.compilation;deps,events,source_orders=manifest
        _check(report.command_id==market.command_id==write.command_id and digest(report.request)==write.request_hash
            and market.request_hash==write.request_hash,'PAPER_V2_ORIGINAL_REQUEST_CHANGED')
        expected_inputs={(d['market_key'],d['event_id']) for d in deps}
        _check(expected_inputs=={(i.event.market_key,i.event.event_id) for i in market.inputs},'PAPER_V2_CAUSAL_INPUT_SET_CHANGED')
        _check(all(any(i.event_hash==d['payload_hash'] for i in market.inputs
            if (i.event.market_key,i.event.event_id)==(d['market_key'],d['event_id'])) for d in deps),'PAPER_V2_CAUSAL_INPUT_HASH_CHANGED')
        issues=v2_market_evidence_codes(market,scope=self.scope,command_id=write.command_id,request_hash=write.request_hash,now=clock.utc_now)
        _check(not issues,issues[0] if issues else 'PAPER_V2_MARKET_SOURCE_INVALID')
        body=dict(request=domain_payload(report.request),orders=[domain_payload(o) for o in report.orders],
            fills=[domain_payload(f) for f in report.fills],accepted_contexts=[domain_payload(c) for c in report.accepted_contexts],
            dispositions=[domain_payload(d) for d in report.dispositions],contract_version=report.contract_version,
            record_kind=report.record_kind,native_parent=domain_payload(report.native_parent) if report.native_parent is not None else None)
        _check(report.contract_version=='2026-10-30' and report.record_kind=='PAPER_CREATION_REPORT_V2'
            and report.evidence.id=='paper-market-v2-query:'+write.command_id and report.evidence.hash==canonical_hash(body)
            and report.evidence.source==SOURCE and report.evidence.environment==t.Environment.PAPER,'PAPER_V2_REPORT_HASH_CHANGED')
        nodes={n.original.node_id:n for n in plan.nodes};assigned={m.node_id:m.client_id for m in request.assigned_ids.members}
        _check(plan.original_program.template in ('ATOMIC','OCO'),'PAPER_V2_NATIVE_LINEAGE_INTERFACE_UNSUPPORTED')
        _check(set(nodes)==set(assigned) and len(report.orders)==len(report.accepted_contexts)==len(nodes),'PAPER_V2_NODE_INVENTORY_CHANGED')
        if request.assigned_ids.parent_client_id is not None:
            parent=report.native_parent
            _check(parent is not None and parent.parent_client_id==request.assigned_ids.parent_client_id
                and parent.namespace==request.namespace.value and parent.template==plan.original_program.template
                and tuple((m.node_id,m.client_id) for m in parent.members)==tuple((m.node_id,m.client_id) for m in request.assigned_ids.members)
                and all(source_orders[m.client_id]['order_id']==m.venue_order_id for m in parent.members),
                'PAPER_V2_NATIVE_PARENT_MEMBERSHIP_CHANGED')
        else:_check(report.native_parent is None,'PAPER_V2_UNEXPECTED_PARENT')
        _check({f.fill_id for f in report.fills}|{d.event_id for d in report.dispositions}==set(events),
            'PAPER_V2_COMPLETE_EVENT_SET_CHANGED')
        input_map={(i.event.market_key,i.event.event_id):i for i in market.inputs};normalized=[];projections=[];economics=[]
        for order,accepted in zip(report.orders,report.accepted_contexts,strict=True):
            _check(order.node_id in nodes and order.client_id==assigned[order.node_id],'PAPER_V2_ORDER_NODE_CHANGED')
            n=nodes[order.node_id];spec=n.original.order;client=order.client_id
            # Initial safe supported semantic families; unknown ones stay raw and
            # blocked until independently pinned by the next actual test cohort.
            _check(spec.price.kind in ('MARKET','FIXED_LIMIT') and spec.trigger.kind in ('NONE','STATIC','TRAILING')
                and spec.stp=='NONE' and spec.iceberg_quantity is None and self.scope.product!=t.Product.US_EQUITY,
                'PAPER_V2_SEMANTIC_INTERFACE_UNSUPPORTED')
            _check(type(accepted) is PaperAcceptedContextV2 and digest(accepted.original_write)==digest(replace(write,state='MAY_HAVE_SENT'))
                and digest(accepted.request)==write.request_hash and digest(accepted.compilation)==write.compilation_hash
                and digest(accepted.rules)==digest(a.rules),'PAPER_V2_ACCEPTED_SOURCE_CHANGED')
            _check(type(accepted.model) is PaperMatchingModelV2,'PAPER_V2_MODEL_INTERFACE_UNSUPPORTED')
            accepted.model.__post_init__()
            terms=PaperExecutionTermsV2(scope=self.scope,client_id=client,node_id=n.original.node_id,namespace=n.namespace.value,
                quantity=n.quantity.value,quantity_step=a.rules.quantity_step,side=spec.side,order_type=n.order_type,
                limit_price=spec.price.value,post_only=spec.post_only,tif=execution_tif(n),multiplier=n.quantity_multiplier,
                quote_asset=a.rules.quote_asset,quote_fee_rate=accepted.model.quote_fee_rate,accepted_clock=accepted.terms.accepted_clock,
                contract_version='2026-10-30',record_kind='PAPER_EXECUTION_TERMS_V2',model_revision=2,spec=spec)
            _check(digest(terms)==digest(accepted.terms),'PAPER_V2_ACCEPTED_TERMS_CHANGED')
            bound=next(b for b in a.risk_facts.node_bounds if b.node_id==order.node_id);ceilings={}
            with localcontext() as ctx:
                ctx.prec=256;ctx.traps[Inexact]=ctx.traps[Rounded]=True
                for fee in bound.execution_fees:ceilings[fee.amount.currency]=ceilings.get(fee.amount.currency,Decimal(0))+fee.amount.amount
                gross=a.assessment.metrics.maximum_order_notional.amount
                if report.native_parent is not None:
                    _check(bound.maximum_price is not None and n.worst_case_quote_notional is not None,'PAPER_V2_NODE_GROSS_BOUND_REQUIRED')
                    gross=min(Decimal(n.quantity.value)*bound.maximum_price.value*Decimal(n.quantity_multiplier),Decimal(n.worst_case_quote_notional))
                    _check(0<gross<=a.exposure.gross_program.amount,'PAPER_V2_NODE_GROSS_PROGRAM_CHANGED')
                    from trading_system.risk.valuation import Valuation
                    value=Valuation(a.risk_policy.valuation_currency,a.risk_facts.inputs.fx_rates).convert(t.Money(amount=gross,currency=a.rules.quote_asset),liability=True)
                    _check(value.amount<=Decimal(a.risk_policy.maximum_order_notional),'PAPER_V2_NODE_POLICY_CHANGED')
            envelope=PaperExecutionEnvelope(contract_version='2026-10-29',record_kind='PAPER_EXECUTION_ENVELOPE',scope=self.scope,
                command_id=write.command_id,request_hash=write.request_hash,consumption_context_hash=canonical_hash(consumed),assessment_hash=digest(a),
                source_ref='ledger.program_permit_consumptions:'+scope_id(self.scope)+':'+write.command_id,node_id=order.node_id,
                minimum_price=None if bound.minimum_price is None else format(bound.minimum_price.value,'f'),
                maximum_price=None if bound.maximum_price is None else format(bound.maximum_price.value,'f'),maximum_gross_notional=format(gross,'f'),
                fee_ceilings=tuple(AssetBudget(asset=k,amount=format(v,'f')) for k,v in sorted(ceilings.items())))
            _check(digest(envelope)==digest(accepted.execution_envelope),'PAPER_V2_ACCEPTED_ENVELOPE_CHANGED')
            accepted_at=exact_time(terms.accepted_clock.utc_now)
            _check(terms.accepted_clock.clock_health=='HEALTHY' and exact_time(consumed['clock']['utc_now'])<=accepted_at<exact_time(consumed['valid_until'])
                and accepted_at<=exact_time(clock.utc_now),'PAPER_V2_ACCEPTED_CLOCK_CHANGED')
            persisted=source_orders[client]
            _check(order.venue_order_id==persisted['order_id'] and order.lifecycle_revision==persisted['revision']
                and order.namespace==n.namespace.value and order.quantity_unit==bundle.intent.unit.native_unit,'PAPER_V2_ACTUAL_ORDER_ID_CHANGED')
            own_fills=tuple(f for f in report.fills if f.client_id==client)
            own_inputs=tuple(i for i in market.inputs if (i.event.market_key,i.event.event_id) in
                {(d['market_key'],d['event_id']) for d in deps if d['client_id']==client})
            self._trigger(spec,order,own_inputs,own_fills,accepted_at)
            identity=t.OrderIdentity(scope=self.scope,logical_client_order_id=client,native_client_order_id=client,venue_client_order_id=client,venue_order_id=order.venue_order_id)
            total=Decimal(0);gross_total=Decimal(0);fee_totals={}
            for f in own_fills:
                stored=events[f.fill_id];raw=_raw_object(f.raw_payload_json,f.evidence)
                _check(stored['client_id']==client and stored['raw']==f.raw_payload_json and stored['payload_hash']==f.evidence.hash
                    and f.node_id==order.node_id and f.namespace==n.namespace.value and raw.get('client_id')==client
                    and raw.get('record_kind')=='PAPER_MARKET_FILL' and raw.get('event_id')==f.fill_id,'PAPER_V2_ACTUAL_FILL_SOURCE_CHANGED')
                source=input_map.get((raw.get('market_key'),f.market_event_id))
                allocations=tuple(c for c in market.consumptions if c.scope==self.scope and c.client_id==client and c.fill_id==f.fill_id)
                _check(source is not None and len(allocations)==1 and allocations[0].quantity==f.quantity and allocations[0].price==f.price
                    and allocations[0].side==('ASK' if spec.side=='BUY' else 'BID') and source.event_hash==f.market_hash==raw.get('market_hash'),
                    'PAPER_V2_FILL_ALLOCATION_CHANGED')
                _check(_raw_decimal(raw['quantity'])==f.quantity and _raw_decimal(raw['price'])==f.price and _raw_decimal(raw['gross_notional'])==f.gross_notional
                    and raw['fees']==[domain_payload(v) for v in f.fees] and canonical_hash(raw['model'])==digest(accepted.model)
                    and canonical_hash(raw['execution_envelope'])==digest(envelope),'PAPER_V2_FILL_RAW_FIELDS_CHANGED')
                _check(exact_time(raw['execution_at'])==exact_time(f.occurred_at)
                    and max(accepted_at,exact_time(source.event.occurred_at),exact_time(source.observed_clock.utc_now))<=exact_time(f.occurred_at)<=exact_time(clock.utc_now),
                    'PAPER_V2_FILL_CLOCK_CHANGED')
                e=normalize_native_economics(quantity=t.Quantity(value=f.quantity,unit=f.quantity_unit),price=t.Price(value=f.price,currency=f.price_asset),
                    fees=tuple(t.Money(amount=Decimal(v.amount),currency=v.asset) for v in f.fees),reported_gross=t.Money(amount=f.gross_notional,currency=f.price_asset),unit=bundle.intent.unit)
                economics.extend(e.discrepancies)
                with localcontext() as ctx:
                    ctx.prec=256;ctx.traps[Inexact]=ctx.traps[Rounded]=True
                    total+=f.quantity;gross_total+=f.gross_notional
                    for fee in e.fees:fee_totals[fee.currency]=fee_totals.get(fee.currency,Decimal(0))+fee.amount
                    expected_fee=e.expected_gross.amount*Decimal(accepted.model.quote_fee_rate)
                    actual_fee=sum((v.amount for v in e.fees if v.currency==a.rules.quote_asset),Decimal(0))
                known=bool(e.fees) or Decimal(accepted.model.quote_fee_rate)==0
                if not known:economics.append('FILL_FEES_UNKNOWN')
                if actual_fee!=expected_fee or any(v.currency!=a.rules.quote_asset for v in e.fees):economics.append('FILL_FEE_MODEL_DISCREPANCY')
                if f.price_asset!=a.rules.quote_asset:economics.append('FILL_QUOTE_ASSET_MISMATCH')
                if spec.price.kind=='FIXED_LIMIT' and (spec.side=='BUY' and f.price>Decimal(spec.price.value) or spec.side=='SELL' and f.price<Decimal(spec.price.value)):
                    economics.append('FILL_OUTSIDE_ORIGINAL_LIMIT')
                if bound.minimum_price is not None and f.price<bound.minimum_price.value or bound.maximum_price is not None and f.price>bound.maximum_price.value:
                    economics.append('FILL_OUTSIDE_EXECUTION_ENVELOPE')
                ledger_fees=e.fees or ((t.Money(amount=Decimal(0),currency=a.rules.quote_asset),) if known else ())
                ref=next(r for r in refs if r.hash==f.evidence.hash)
                fact=t.Fill(fill_id='paper-program:'+canonical_hash(dict(scope=domain_payload(self.scope),native_trade_id=f.fill_id)),venue='BINANCE',scope=self.scope,
                    native_trade_id=f.fill_id,order=identity,quantity=e.native_quantity,price=e.venue_price,fees=ledger_fees,occurred_at=f.occurred_at,correction_of=None,evidence=ref)
                provenance=PaperFillSourceV2(node_id=order.node_id,client_id=client,accepted_context_hash=digest(accepted),consumption_context_hash=canonical_hash(consumed),
                    causal_input_ids=tuple((i.event.market_key,i.event.event_id) for i in own_inputs),parent_client_id=request.assigned_ids.parent_client_id)
                normalized.append(NormalizedPaperFillV2(fill=fact,side=spec.side,economics=e,raw=f,fees_known=known,source_v2=provenance))
            if total!=order.filled_quantity:economics.append('ORDER_CUMULATIVE_FILL_MISMATCH')
            if order.quantity!=Decimal(n.quantity.value):economics.append('ORDER_ORIGINAL_QUANTITY_MISMATCH')
            if total>order.quantity:economics.append('ORDER_OVERFILLED')
            if gross_total>gross:economics.append('PROGRAM_GROSS_ENVELOPE_EXCEEDED')
            if any(v>ceilings.get(k,Decimal(0)) for k,v in fee_totals.items()):economics.append('PROGRAM_FEE_ENVELOPE_EXCEEDED')
            terminal=order.status in ('FILLED','CANCELED','EXPIRED','REJECTED')
            with localcontext() as ctx:
                ctx.prec=256;expected=Decimal(0) if terminal else order.quantity-order.filled_quantity-order.runtime.prevented_quantity
            if expected!=order.remaining_quantity or expected<0:economics.append('ORDER_REMAINING_QUANTITY_MISMATCH')
            projections.append(t.OrderProjection(order=t.OwnedOrderRef(identity=identity,owner_strategy_id=write.owner_strategy_id,ownership_epoch=write.ownership_epoch),
                lifecycle_state=t.OrderLifecycle(order.status),pending_operation='NONE',command_certainty='CONFIRMED',outcome_provenance='VENUE_QUERY',
                filled_quantity=t.Quantity(value=order.filled_quantity,unit=order.quantity_unit),leaves_quantity=t.Quantity(value=order.remaining_quantity,unit=order.quantity_unit),
                lifecycle_revision=order.lifecycle_revision,sequence=0))
        # Every disposition remains an actual stored raw event, not a fill.
        for d in report.dispositions:
            raw=_raw_object(d.raw_payload_json,d.evidence);stored=events[d.event_id]
            _check(stored['raw']==d.raw_payload_json and stored['payload_hash']==d.evidence.hash and stored['client_id']==d.client_id
                and raw.get('record_kind')=='PAPER_MARKET_DISPOSITION' and raw.get('status')==d.status and raw.get('reason')==d.reason,
                'PAPER_V2_DISPOSITION_SOURCE_CHANGED')
        return tuple(normalized),tuple(projections),tuple(dict.fromkeys(economics))

    def _trigger(self,spec,order,inputs,fills,accepted_at):
        if spec.trigger.kind=='NONE':return
        source=spec.trigger.source
        _check(source!='BID_ASK','PAPER_V2_TRIGGER_INTERFACE_UNSUPPORTED')
        active=False;started=False;high=low=None;activation=None
        for item in inputs:
            if type(item.event) is not PaperReferenceEventV2:continue
            prices=[Fraction(Decimal(p.price)) for p in item.event.prices if p.source==source]
            if not prices:continue
            price=prices[0];trigger=spec.trigger
            if trigger.kind=='STATIC':
                threshold=Fraction(Decimal(trigger.price));active=price>=threshold if (spec.side=='BUY')==(trigger.purpose=='STOP_LOSS') else price<=threshold
            else:
                if not started:
                    threshold=None if trigger.activation_price is None else Fraction(Decimal(trigger.activation_price))
                    ready=threshold is None or (price>=threshold if (spec.side=='BUY')==(trigger.purpose=='STOP_LOSS') else price<=threshold)
                    if not ready:continue
                    high=low=price;started=True;continue
                high=max(high,price);low=min(low,price);offset=Fraction(Decimal(trigger.offset))
                if trigger.offset_unit=='BIPS':offset/=10000
                elif trigger.offset_unit=='PERCENT':offset/=100
                elif trigger.offset_unit!='PRICE':raise _SourceError('PAPER_V2_TRAILING_UNIT_UNSUPPORTED')
                active=price<=high-(offset if trigger.offset_unit=='PRICE' else high*offset) if spec.side=='SELL' else price>=low+(offset if trigger.offset_unit=='PRICE' else low*offset)
            if active:activation=item;break
        if fills:_check(activation is not None,'PAPER_V2_TRIGGER_NOT_PROVEN')
        if activation is not None:
            _check(order.runtime.activation_event_id==activation.event.event_id,'PAPER_V2_TRIGGER_EVENT_CHANGED')
            _check(all(exact_time(f.occurred_at)>=max(accepted_at,exact_time(activation.observed_clock.utc_now)) for f in fills),'PAPER_V2_TRIGGER_FILL_CLOCK_CHANGED')
        if spec.trigger.kind=='TRAILING' and started:
            _check(order.runtime.high_water is not None and Fraction(order.runtime.high_water)==high
                and order.runtime.low_water is not None and Fraction(order.runtime.low_water)==low,'PAPER_V2_TRAILING_EXTREMA_CHANGED')
