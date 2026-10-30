"""Native PAPER parent records. Financial authority stays at the real boundary.

An accepted parent is not an ordinary order or a fill. Native children will be
observations of its finite persisted pool, never application command exemptions.
"""
from dataclasses import dataclass,fields
from decimal import Decimal,localcontext,Inexact,Rounded
from typing import Literal
from psycopg.types.json import Jsonb
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload,canonical_hash
from trading_system.contracts.order_lifecycle import AssetBudget
from trading_system.storage.models import decode,scope_id
from .paper_market import (PaperExecutionEnvelope,PaperCreationReceipt,number,require,digest,market_key,_evidence,
    _consumed_permit_clock,_current_consumed_owner)
from .paper_market_v2 import PaperMatchingModelV2,PaperNativeParentV2
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


def _restore_context(payload,request):
    names={f.name for f in fields(PaperNativeParentTermsV2)}
    require(set(payload)==names|{'write','request','compilation','rules','model','execution_envelope'},'PAPER_NATIVE_CONTEXT_SHAPE_CHANGED')
    c=PaperNativeAcceptedContextV2(original_write=decode(ApplicationWriteV3,payload['write']),request=decode(VenueCreationRequest,payload['request']),
        compilation=decode(ProgramCompilation,payload['compilation']),rules=decode(OrderRuleSnapshot,payload['rules']),
        model=decode(PaperMatchingModelV2,payload['model']),terms=decode(PaperNativeParentTermsV2,{k:payload[k] for k in names}),
        execution_envelope=decode(PaperExecutionEnvelope,payload['execution_envelope']))
    p=c.compilation;terms=c.terms;node=p.nodes[0];e=c.execution_envelope
    require(terms.contract_version=='2026-10-30' and terms.record_kind=='PAPER_NATIVE_PARENT_TERMS_V2'
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
                _current_consumed_owner(boundary,original,lease,consumption,assessment)
                clock=_consumed_permit_clock(engine,permit,after=clock,created_at=original.created_at)
            source_book=db.execute('SELECT occurred_at,observed_clock FROM public.paper_market_books WHERE market_key=%s',(key,)).fetchone()
            if source_book is not None:
                from trading_system.contracts.canonical import utc_timestamp
                observed=decode(t.ClockReading,source_book['observed_clock'])
                require(observed.clock_health=='HEALTHY' and utc_timestamp(observed.utc_now)<=utc_timestamp(clock.utc_now)
                    and source_book['occurred_at']<=utc_timestamp(clock.utc_now),'PAPER_MARKET_CLOCK_AHEAD_OF_ACCEPTANCE')
            terms=PaperNativeParentTermsV2(contract_version='2026-10-30',record_kind='PAPER_NATIVE_PARENT_TERMS_V2',scope=engine.scope,
                parent_client_id=parent,node_id=node.original.node_id,namespace=request.namespace.value,template=program.template,quantity=node.quantity.value,
                quantity_unit=node.quantity_unit,quantity_step=assessment.rules.quantity_step,multiplier=node.quantity_multiplier,
                maximum_live_children=bound.maximum_live_children,accepted_clock=clock)
            payload=domain_payload(terms)|dict(write=domain_payload(original),request=domain_payload(request),compilation=domain_payload(p),
                rules=domain_payload(assessment.rules),model=domain_payload(model),execution_envelope=domain_payload(envelope))
            db.execute('INSERT INTO public.paper_engine_commands VALUES(%s,%s,%s,%s,NULL,%s,%s,%s)',
                ('PAPER',engine._scope_key,write.command_id,parent,'CREATE_REQUEST',digest(request),Jsonb(domain_payload(request))))
            db.execute('INSERT INTO public.paper_native_parent_terms_v2 VALUES(%s,%s,%s,%s,%s)',
                (engine._scope_key,parent,write.command_id,key,Jsonb(payload)))
            db.execute('SELECT public.paper_accept_native_algorithm_v2(%s,%s)',(engine._scope_key,write.command_id))
            with owner.guard(lease):
                _current_consumed_owner(boundary,original,lease,consumption,assessment)
                _consumed_permit_clock(engine,permit,after=clock,created_at=original.created_at)
    report=engine.query_creation(write.command_id)
    return PaperCreationReceipt(command_id=write.command_id,request_hash=digest(request),scope=engine.scope,namespace=request.namespace.value,
        client_ids=(parent,),status=report.status,evidence=report.evidence)


def query_native_program(engine,db,command):
    require(type(engine._matching_model) is PaperMatchingModelV2,'PAPER_NATIVE_PARENT_READER_REQUIRED')
    request=decode(VenueCreationRequest,command['request'])
    row=db.execute('SELECT t.terms,p.*,a.* FROM public.paper_native_parent_terms_v2 t JOIN public.paper_native_parents_v2 p '
        'USING(scope_key,parent_client_id,command_id) JOIN public.paper_native_algorithm_progress_v2 a USING(scope_key,parent_client_id) '
        'WHERE t.scope_key=%s AND t.command_id=%s',(engine._scope_key,command['command_id'])).fetchone()
    require(row is not None and row['request_hash']==command['request_hash']==digest(request),'PAPER_NATIVE_PARENT_SOURCE_REQUIRED')
    context=_restore_context(row['terms'],request)
    require(db.execute('SELECT count(*) AS n FROM public.paper_native_members_v2 WHERE scope_key=%s AND parent_client_id=%s',
        (engine._scope_key,row['parent_client_id'])).fetchone()['n']==0,'PAPER_NATIVE_CHILD_READER_STAGE_REQUIRED')
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
    require(pool.allocated_quantity==pool.executed_quantity==pool.unused_quantity==pool.allocated_gross_notional==pool.cumulative_gross_notional==0,
        'PAPER_NATIVE_CHILD_READER_STAGE_REQUIRED')
    parent=PaperNativeParentV2(parent_client_id=row['parent_client_id'],venue_parent_id=row['venue_parent_id'],namespace=row['namespace'],
        template=row['template'],status=row['status'],lifecycle_revision=row['revision'],members=())
    payload=dict(request=domain_payload(request),native_parent=domain_payload(parent),pool=domain_payload(pool),accepted_contexts=[domain_payload(context)],
        orders=[],fills=[],dispositions=[],contract_version='2026-10-30',record_kind='PAPER_NATIVE_PROGRAM_REPORT_V2')
    return PaperNativeProgramReportV2(command_id=request.command_id,request=request,native_parent=parent,pool=pool,accepted_contexts=(context,),
        orders=(),fills=(),dispositions=(),evidence=_evidence('paper-native-program:'+request.command_id,payload,engine._clock().utc_now))
