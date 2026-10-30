"""Native PAPER parent records. Financial authority stays at the real boundary.

An accepted parent is not an ordinary order or a fill. Native children will be
observations of its finite persisted pool, never application command exemptions.
"""
from dataclasses import dataclass,fields,replace
from decimal import Decimal,localcontext,Inexact,Rounded
from typing import Literal
from psycopg.types.json import Jsonb
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload,canonical_hash,canonical_json
from trading_system.contracts.order_lifecycle import AssetBudget
from trading_system.storage.models import decode,scope_id
from .paper_market import (PaperExecutionEnvelope,PaperCreationReceipt,number,require,digest,market_key,_evidence,
    _consumed_permit_clock,_current_consumed_owner,PaperAcceptedContext,PaperMarketFill,PaperMarketDisposition)
from .paper_market_v2 import (PaperMatchingModelV2,PaperNativeParentV2,PaperNativeMemberV2,PaperExecutionTermsV2,
    PaperMatchedOrderV2,PaperOrderRuntimeV2)
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.order_snapshots import OrderRuleSnapshot
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.intents.program_compilation import ProgramCompilation


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeParentTermsV2:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PAPER_NATIVE_PARENT_TERMS_V2']
    scope:t.ScopeKey
    parent_client_id:str
    node_id:str
    namespace:str
    template:str
    quantity:str
    quantity_unit:str
    quantity_step:str
    multiplier:str
    maximum_live_children:int
    accepted_clock:t.ClockReading


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeClockModelV2:
    model_kind:Literal['PAPER_NATIVE_TWAP_CLOCK_MODEL']='PAPER_NATIVE_TWAP_CLOCK_MODEL'
    model_revision:Literal[1]=1
    slice_seconds:int=60
    child_tif:Literal['IOC']='IOC'
    unused_policy:Literal['NO_AUTOMATIC_RECYCLE']='NO_AUTOMATIC_RECYCLE'
    def __post_init__(self):
        require(self.model_kind=='PAPER_NATIVE_TWAP_CLOCK_MODEL' and type(self.model_revision) is int and self.model_revision==1
            and type(self.slice_seconds) is int and self.slice_seconds==60 and self.child_tif=='IOC'
            and self.unused_policy=='NO_AUTOMATIC_RECYCLE','PAPER_NATIVE_CLOCK_MODEL_UNKNOWN')


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeExecutionTermsV2(PaperNativeParentTermsV2):
    record_kind:Literal['PAPER_NATIVE_EXECUTION_TERMS_V2']
    record_revision:Literal[2]
    execution_model:PaperNativeClockModelV2


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeChildTermsV2(PaperExecutionTermsV2):
    record_kind:Literal['PAPER_NATIVE_CHILD_TERMS_V2']
    parent_client_id:str
    allocation_id:str
    generation_event_id:str
    allocation_ordinal:int


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeChildAllocationV2:
    scope:t.ScopeKey
    parent_client_id:str
    child_client_id:str
    node_id:str
    allocation_id:str
    ordinal:int
    market_key:str
    market_event_id:str
    market_hash:str
    quantity:Decimal
    gross_ceiling:Decimal


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeChildContextV2(PaperAcceptedContext):
    model:PaperMatchingModelV2
    terms:PaperNativeChildTermsV2
    allocation:PaperNativeChildAllocationV2


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeAcceptedContextV2:
    original_write:ApplicationWriteV3
    request:VenueCreationRequest
    compilation:ProgramCompilation
    rules:OrderRuleSnapshot
    model:PaperMatchingModelV2
    terms:PaperNativeParentTermsV2
    execution_envelope:PaperExecutionEnvelope


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativePoolV2:
    original_quantity:Decimal
    allocated_quantity:Decimal
    executed_quantity:Decimal
    unused_quantity:Decimal
    remaining_pool_quantity:Decimal
    maximum_gross_notional:Decimal
    allocated_gross_notional:Decimal
    cumulative_gross_notional:Decimal
    fee_ceilings:tuple[AssetBudget,...]
    maximum_live_children:int


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeProgramReportV2:
    command_id:str
    request:VenueCreationRequest
    native_parent:PaperNativeParentV2
    pool:PaperNativePoolV2
    accepted_contexts:tuple[PaperNativeAcceptedContextV2,...]
    orders:tuple
    fills:tuple
    dispositions:tuple
    evidence:t.EvidenceRef
    contract_version:Literal['2026-10-30']='2026-10-30'
    record_kind:Literal['PAPER_NATIVE_PROGRAM_REPORT_V2']='PAPER_NATIVE_PROGRAM_REPORT_V2'
    @property
    def status(self):return self.native_parent.status


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperNativeExecutionReportV2(PaperNativeProgramReportV2):
    accepted_contexts:tuple[PaperNativeAcceptedContextV2|PaperNativeChildContextV2,...]
    orders:tuple[PaperMatchedOrderV2,...]
    fills:tuple[PaperMarketFill,...]
    dispositions:tuple[PaperMarketDisposition,...]
    record_kind:Literal['PAPER_NATIVE_EXECUTION_REPORT_V2']='PAPER_NATIVE_EXECUTION_REPORT_V2'
    record_revision:Literal[2]=2


def advance_native_programs(db,key):
    # V1 databases have no0014 function. A V1-configured publisher on an upgraded
    # shared venue still advances every actual native parent in that same book.
    if db.execute("SELECT to_regprocedure('public.paper_advance_native_algorithms_v2(text)') AS function").fetchone()['function'] is not None:
        db.execute('SELECT public.paper_advance_native_algorithms_v2(%s)',(key,))


def _restore_context(payload,request):
    cls=PaperNativeExecutionTermsV2 if payload.get('record_kind')=='PAPER_NATIVE_EXECUTION_TERMS_V2' else PaperNativeParentTermsV2
    names={f.name for f in fields(cls)}
    require(set(payload)==names|{'write','request','compilation','rules','model','execution_envelope'},'PAPER_NATIVE_CONTEXT_SHAPE_CHANGED')
    c=PaperNativeAcceptedContextV2(original_write=decode(ApplicationWriteV3,payload['write']),request=decode(VenueCreationRequest,payload['request']),
        compilation=decode(ProgramCompilation,payload['compilation']),rules=decode(OrderRuleSnapshot,payload['rules']),
        model=decode(PaperMatchingModelV2,payload['model']),terms=decode(cls,{k:payload[k] for k in names}),
        execution_envelope=decode(PaperExecutionEnvelope,payload['execution_envelope']))
    p=c.compilation;terms=c.terms;node=p.nodes[0];e=c.execution_envelope
    require(terms.contract_version=='2026-10-30' and terms.record_kind in ('PAPER_NATIVE_PARENT_TERMS_V2','PAPER_NATIVE_EXECUTION_TERMS_V2')
        and digest(c.request)==digest(request) and c.original_write.command_id==request.command_id
        and c.original_write.request_hash==digest(request) and c.original_write.compilation_hash==digest(p)
        and p.rules_hash==digest(c.rules) and terms.scope==request.scope==p.scope and len(p.nodes)==1
        and p.original_program.execution_mode=='VENUE_NATIVE' and terms.template==p.original_program.template
        and terms.template in ('TWAP','VP') and terms.parent_client_id==request.assigned_ids.parent_client_id
        and not request.assigned_ids.members and terms.node_id==node.original.node_id and terms.namespace==request.namespace.value
        and terms.quantity==node.quantity.value and node.quantity.kind=='BASE_QUANTITY'
        and terms.quantity_unit==node.quantity_unit and terms.multiplier==node.quantity_multiplier
        and terms.quantity_step==c.rules.quantity_step and e.scope==terms.scope and e.request_hash==digest(request)
        and e.command_id==request.command_id and e.node_id==terms.node_id,'PAPER_NATIVE_CONTEXT_CHANGED')
    return c


def submit_native_program(engine,write,request,permit,*,boundary,lease,owner,original,consumption,assessment):
    p=assessment.compilation;program=p.original_program;node=p.nodes[0]
    require(type(engine._matching_model) is PaperMatchingModelV2 and program.template in ('TWAP','VP')
        and program.execution_mode=='VENUE_NATIVE' and len(p.nodes)==1 and node.quantity.kind=='BASE_QUANTITY'
        and request.operation.value=='SUBMIT_'+program.template and request.assigned_ids.parent_client_id is not None
        and not request.assigned_ids.members and request.namespace.value in ('BINANCE_SPOT_EXECUTION_ALGO','BINANCE_USD_M_EXECUTION_ALGO'),
        'PAPER_ACTUAL_NATIVE_ALGORITHM_REQUIRED')
    wire=tuple(f.value for f in request.fields if f.name=='quantity')
    require(len(wire)==1 and number(wire[0])==number(node.quantity.value),'PAPER_NATIVE_REQUEST_QUANTITY_CHANGED')
    bounds=tuple(b for b in assessment.risk_facts.node_bounds if b.node_id==node.original.node_id)
    require(len(bounds)==1 and bounds[0].maximum_price is not None and bounds[0].minimum_price is not None
        and bounds[0].execution_fees is not None and bounds[0].maximum_live_children>0,'PAPER_NATIVE_POOL_BOUND_REQUIRED')
    bound=bounds[0];model=engine._matching_model;fees={}
    for fee in bound.execution_fees:fees[fee.amount.currency]=fees.get(fee.amount.currency,Decimal(0))+fee.amount.amount
    gross=assessment.assessment.metrics.maximum_order_notional
    require(gross.currency==assessment.rules.quote_asset and gross.amount>0,'PAPER_NATIVE_POOL_GROSS_REQUIRED')
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        fee=number(node.quantity.value)*bound.maximum_price.value*number(node.quantity_multiplier)*number(model.quote_fee_rate)
    require(fee<=sum((f.amount.amount for f in bound.execution_fees if f.amount.currency==assessment.rules.quote_asset
        and f.debit=='EXTERNAL_BALANCE'),Decimal(0)),'PAPER_FEE_MODEL_EXCEEDS_ADMITTED_BOUND')
    envelope=PaperExecutionEnvelope(contract_version='2026-10-29',record_kind='PAPER_EXECUTION_ENVELOPE',scope=engine.scope,
        command_id=write.command_id,request_hash=digest(request),consumption_context_hash=canonical_hash(consumption),assessment_hash=digest(assessment),
        source_ref='ledger.program_permit_consumptions:'+scope_id(engine.scope)+':'+write.command_id,node_id=node.original.node_id,
        minimum_price=format(bound.minimum_price.value,'f'),maximum_price=format(bound.maximum_price.value,'f'),maximum_gross_notional=format(gross.amount,'f'),
        fee_ceilings=tuple(AssetBudget(asset=k,amount=format(v,'f')) for k,v in sorted(fees.items())))
    with owner.guard(lease):
        clock=_consumed_permit_clock(engine,permit,created_at=original.created_at)
        with engine._connect() as db:
            key=market_key(engine.scope.product,engine.scope.instrument_uid);parent=request.assigned_ids.parent_client_id
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
            with owner.guard(lease):
                _current_consumed_owner(boundary,original,lease,consumption,assessment,permit)
                clock=_consumed_permit_clock(engine,permit,after=clock,created_at=original.created_at)
            source_book=db.execute('SELECT occurred_at,observed_clock FROM public.paper_market_books WHERE market_key=%s',(key,)).fetchone()
            if source_book is not None:
                from trading_system.contracts.canonical import utc_timestamp
                observed=decode(t.ClockReading,source_book['observed_clock'])
                require(observed.clock_health=='HEALTHY' and utc_timestamp(observed.utc_now)<=utc_timestamp(clock.utc_now)
                    and source_book['occurred_at']<=utc_timestamp(clock.utc_now),'PAPER_MARKET_CLOCK_AHEAD_OF_ACCEPTANCE')
            term_class=PaperNativeExecutionTermsV2 if program.template=='TWAP' else PaperNativeParentTermsV2
            extra=dict(record_revision=2,execution_model=PaperNativeClockModelV2()) if program.template=='TWAP' else {}
            terms=term_class(contract_version='2026-10-30',record_kind='PAPER_NATIVE_EXECUTION_TERMS_V2' if program.template=='TWAP' else 'PAPER_NATIVE_PARENT_TERMS_V2',scope=engine.scope,
                parent_client_id=parent,node_id=node.original.node_id,namespace=request.namespace.value,template=program.template,quantity=node.quantity.value,
                quantity_unit=node.quantity_unit,quantity_step=assessment.rules.quantity_step,multiplier=node.quantity_multiplier,
                maximum_live_children=bound.maximum_live_children,accepted_clock=clock,**extra)
            payload=domain_payload(terms)|dict(write=domain_payload(original),request=domain_payload(request),compilation=domain_payload(p),
                rules=domain_payload(assessment.rules),model=domain_payload(model),execution_envelope=domain_payload(envelope))
            db.execute('INSERT INTO public.paper_engine_commands VALUES(%s,%s,%s,%s,NULL,%s,%s,%s)',
                ('PAPER',engine._scope_key,write.command_id,parent,'CREATE_REQUEST',digest(request),Jsonb(domain_payload(request))))
            db.execute('INSERT INTO public.paper_native_parent_terms_v2 VALUES(%s,%s,%s,%s,%s)',
                (engine._scope_key,parent,write.command_id,key,Jsonb(payload)))
            db.execute('SELECT public.paper_accept_native_algorithm_v2(%s,%s)',(engine._scope_key,write.command_id))
            with owner.guard(lease):
                _current_consumed_owner(boundary,original,lease,consumption,assessment,permit)
                _consumed_permit_clock(engine,permit,after=clock,created_at=original.created_at)
    report=engine.query_creation(write.command_id)
    return PaperCreationReceipt(command_id=write.command_id,request_hash=digest(request),scope=engine.scope,namespace=request.namespace.value,
        client_ids=(parent,),status=report.status,evidence=report.evidence)


def restore_native_child_context(engine,db,payload,request):
    require(engine is not None and db is not None,'PAPER_NATIVE_ALLOCATION_READER_REQUIRED')
    names={f.name for f in fields(PaperNativeChildTermsV2)}
    require(set(payload)==names|{'write','request','compilation','rules','model','execution_envelope'},'PAPER_NATIVE_CHILD_SHAPE_CHANGED')
    terms=decode(PaperNativeChildTermsV2,{k:payload[k] for k in names})
    source=db.execute('SELECT a.*,i.payload_hash,i.observed_clock FROM public.paper_native_child_allocations_v2 a '
        'JOIN public.paper_market_inputs i ON i.market_key=a.market_key AND i.event_id=a.market_event_id '
        'WHERE a.scope_key=%s AND a.child_client_id=%s AND a.allocation_id=%s',
        (engine._scope_key,terms.client_id,terms.allocation_id)).fetchone()
    parent=db.execute('SELECT terms FROM public.paper_native_parent_terms_v2 WHERE scope_key=%s AND parent_client_id=%s',
        (engine._scope_key,terms.parent_client_id)).fetchone()
    require(source is not None and parent is not None,'PAPER_NATIVE_CHILD_PARENT_SOURCE_REQUIRED')
    p=_restore_context(parent['terms'],request)
    require(type(p.terms) is PaperNativeExecutionTermsV2,'PAPER_NATIVE_CHILD_MODEL_REVISION_REQUIRED')
    node=p.compilation.nodes[0];order=node.original.order;quantity=format(source['quantity'],'f')
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        gross=source['quantity']*number(p.execution_envelope.maximum_price)*number(node.quantity_multiplier)
        fee=gross*number(p.model.quote_fee_rate)
    require(gross==source['gross_ceiling'],'PAPER_NATIVE_CHILD_ALLOCATION_GROSS_CHANGED')
    fees=() if fee==0 else (AssetBudget(asset=p.rules.quote_asset,amount=format(fee,'f')),)
    envelope=replace(p.execution_envelope,maximum_gross_notional=format(gross,'f'),fee_ceilings=fees)
    expected=PaperNativeChildTermsV2(scope=engine.scope,client_id=source['child_client_id'],node_id=node.original.node_id,
        namespace=node.namespace.value,quantity=quantity,quantity_step=p.rules.quantity_step,side=order.side,order_type=node.order_type,
        limit_price=order.price.value,post_only=False,tif=p.terms.execution_model.child_tif,multiplier=node.quantity_multiplier,
        quote_asset=p.rules.quote_asset,quote_fee_rate=p.model.quote_fee_rate,accepted_clock=decode(t.ClockReading,source['observed_clock']),
        contract_version='2026-10-30',record_kind='PAPER_NATIVE_CHILD_TERMS_V2',model_revision=2,spec=order,
        parent_client_id=p.terms.parent_client_id,allocation_id=source['allocation_id'],generation_event_id=source['market_event_id'],
        allocation_ordinal=source['ordinal'])
    require(digest(terms)==digest(expected) and canonical_hash(payload['write'])==digest(p.original_write)
        and canonical_hash(payload['request'])==digest(request) and canonical_hash(payload['compilation'])==digest(p.compilation)
        and canonical_hash(payload['rules'])==digest(p.rules) and canonical_hash(payload['model'])==digest(p.model)
        and canonical_hash(payload['execution_envelope'])==digest(envelope),'PAPER_NATIVE_CHILD_SOURCE_CHANGED')
    allocation=PaperNativeChildAllocationV2(scope=engine.scope,parent_client_id=source['parent_client_id'],child_client_id=source['child_client_id'],
        node_id=source['node_id'],allocation_id=source['allocation_id'],ordinal=source['ordinal'],market_key=source['market_key'],
        market_event_id=source['market_event_id'],market_hash=source['payload_hash'],quantity=source['quantity'],gross_ceiling=source['gross_ceiling'])
    return PaperNativeChildContextV2(original_write=p.original_write,request=request,compilation=p.compilation,rules=p.rules,model=p.model,
        terms=terms,execution_envelope=envelope,allocation=allocation)


def query_native_program(engine,db,command):
    require(type(engine._matching_model) is PaperMatchingModelV2,'PAPER_NATIVE_PARENT_READER_REQUIRED')
    request=decode(VenueCreationRequest,command['request'])
    row=db.execute('SELECT t.terms,p.*,a.* FROM public.paper_native_parent_terms_v2 t JOIN public.paper_native_parents_v2 p '
        'USING(scope_key,parent_client_id,command_id) JOIN public.paper_native_algorithm_progress_v2 a USING(scope_key,parent_client_id) '
        'WHERE t.scope_key=%s AND t.command_id=%s',(engine._scope_key,command['command_id'])).fetchone()
    require(row is not None and row['request_hash']==command['request_hash']==digest(request),'PAPER_NATIVE_PARENT_SOURCE_REQUIRED')
    context=_restore_context(row['terms'],request)
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        remaining=row['original_quantity']-row['allocated_quantity']
    pool=PaperNativePoolV2(original_quantity=row['original_quantity'],allocated_quantity=row['allocated_quantity'],executed_quantity=row['executed_quantity'],
        unused_quantity=row['unused_quantity'],remaining_pool_quantity=remaining,maximum_gross_notional=row['maximum_gross_notional'],
        allocated_gross_notional=row['allocated_gross_notional'],cumulative_gross_notional=row['cumulative_gross_notional'],
        fee_ceilings=tuple(decode(AssetBudget,f) for f in row['fee_ceilings']),maximum_live_children=row['maximum_live_children'])
    require(pool.maximum_gross_notional==number(context.execution_envelope.maximum_gross_notional)
        and canonical_hash({'fees':[domain_payload(f) for f in pool.fee_ceilings]})==
            canonical_hash({'fees':[domain_payload(f) for f in context.execution_envelope.fee_ceilings]})
        and pool.maximum_live_children==context.terms.maximum_live_children
        and row['parent_client_id']==request.assigned_ids.parent_client_id and row['namespace']==request.namespace.value
        and row['template']==context.terms.template,'PAPER_NATIVE_POOL_SOURCE_CHANGED')
    require(pool.original_quantity==number(context.terms.quantity) and pool.executed_quantity+pool.unused_quantity<=pool.allocated_quantity<=pool.original_quantity,
        'PAPER_NATIVE_POOL_CHANGED')
    members=db.execute('SELECT m.node_id,m.client_id,o.order_id AS venue_order_id FROM public.paper_native_members_v2 m '
        'JOIN public.paper_engine_orders o USING(scope_key,client_id) WHERE m.scope_key=%s AND m.parent_client_id=%s ORDER BY ordinal LIMIT 4097',
        (engine._scope_key,row['parent_client_id'])).fetchall()
    require(len(members)<=4096,'PAPER_NATIVE_MEMBER_BOUND_EXCEEDED')
    if type(context.terms) is PaperNativeParentTermsV2:
        require(not members and pool.allocated_quantity==pool.executed_quantity==pool.unused_quantity==pool.allocated_gross_notional==pool.cumulative_gross_notional==0,
            'PAPER_NATIVE_CHILD_READER_STAGE_REQUIRED')
    parent=PaperNativeParentV2(parent_client_id=row['parent_client_id'],venue_parent_id=row['venue_parent_id'],namespace=row['namespace'],
        template=row['template'],status=row['status'],lifecycle_revision=row['revision'],members=tuple(PaperNativeMemberV2(**m) for m in members))
    if type(context.terms) is PaperNativeExecutionTermsV2:
        from .paper_market import _read_creation_members
        initial=len(canonical_json({'context':domain_payload(context),'pool':domain_payload(pool),'parent':domain_payload(parent)}).encode('utf-8'))
        report=_read_creation_members(engine,db,request.command_id,request,[m['client_id'] for m in members],decorate=False,
            maximum_members=4096,initial_bytes=initial)
        orders=[]
        for order in report.orders:
            runtime=db.execute('SELECT active,phase,activation_event_id,high_water,low_water,effective_limit,visible_remaining,queue_priority,'
                'prevented_quantity,parent_activation_fill_id FROM public.paper_order_runtime_v2 WHERE scope_key=%s AND client_id=%s',
                (engine._scope_key,order.client_id)).fetchone()
            require(runtime is not None,'PAPER_NATIVE_CHILD_RUNTIME_REQUIRED')
            orders.append(PaperMatchedOrderV2(**{f.name:getattr(order,f.name) for f in fields(type(order))},runtime=PaperOrderRuntimeV2(**runtime)))
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            require(sum((o.quantity for o in orders),Decimal(0))==pool.allocated_quantity
                and sum((f.quantity for f in report.fills),Decimal(0))==pool.executed_quantity
                and sum((f.gross_notional for f in report.fills),Decimal(0))==pool.cumulative_gross_notional,'PAPER_NATIVE_POOL_FILL_INVENTORY_CHANGED')
        body=dict(request=domain_payload(request),native_parent=domain_payload(parent),pool=domain_payload(pool),
            accepted_contexts=[domain_payload(context),*[domain_payload(c) for c in report.accepted_contexts]],orders=[domain_payload(o) for o in orders],
            fills=[domain_payload(f) for f in report.fills],dispositions=[domain_payload(d) for d in report.dispositions],
            contract_version='2026-10-30',record_kind='PAPER_NATIVE_EXECUTION_REPORT_V2',record_revision=2)
        return PaperNativeExecutionReportV2(command_id=request.command_id,request=request,native_parent=parent,pool=pool,
            accepted_contexts=(context,*report.accepted_contexts),orders=tuple(orders),fills=report.fills,dispositions=report.dispositions,
            evidence=_evidence('paper-native-execution:'+request.command_id,body,engine._clock().utc_now))
    payload=dict(request=domain_payload(request),native_parent=domain_payload(parent),pool=domain_payload(pool),accepted_contexts=[domain_payload(context)],
        orders=[],fills=[],dispositions=[],contract_version='2026-10-30',record_kind='PAPER_NATIVE_PROGRAM_REPORT_V2')
    return PaperNativeProgramReportV2(command_id=request.command_id,request=request,native_parent=parent,pool=pool,accepted_contexts=(context,),
        orders=(),fills=(),dispositions=(),evidence=_evidence('paper-native-program:'+request.command_id,payload,engine._clock().utc_now))
