"""Market-driven producer for the existing isolated synthetic PAPER venue.

This module never reserves business cash or constructs a permit. Inputs are
depth, not requested fills. The SQL reducer alone computes visible-liquidity
execution; runtime cannot supply execution quantities or mutate market sources.
"""
from dataclasses import dataclass,replace,fields
from decimal import Decimal,localcontext,Inexact,Rounded
from pathlib import Path
from hashlib import sha256
from functools import wraps
from typing import Literal
import json
import re
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,utc_timestamp
from trading_system.contracts.order_lifecycle_v3 import FeeV3
from trading_system.contracts.order_lifecycle import AssetBudget
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.order_snapshots import OrderRuleSnapshot
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.intents.program_compilation import ProgramCompilation
from trading_system.storage.models import decode

SOURCE='PAPER_MARKET_REPLAY_SYNTHETIC'


def require(ok,reason):
    if not ok:raise ValueError(reason)
def digest(value):return canonical_hash(domain_payload(value))
def exact_market_arithmetic(function):
    @wraps(function)
    def call(*args,**kwargs):
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            return function(*args,**kwargs)
    return call
def number(value,*,signed=False):
    require(type(value) is str and 0<len(value)<=64,'PAPER_DECIMAL_REQUIRED')
    result=Decimal(value)
    require(result.is_finite() and (signed or result>=0),'PAPER_DECIMAL_INVALID')
    require(-64<=result.as_tuple().exponent<=64 and result.adjusted()<=64,'PAPER_DECIMAL_RANGE_EXCEEDED')
    return result


@dataclass(frozen=True,slots=True,kw_only=True)
class DepthLevel:
    price:str
    quantity:str
    def __post_init__(self):
        require(number(self.price)>0,'PAPER_DEPTH_PRICE_INVALID');number(self.quantity,signed=True)


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMarketEvent:
    contract_version:Literal['2026-10-29']
    event_id:str
    stream_id:str
    product:t.Product
    instrument_uid:str
    sequence:int
    kind:Literal['DEPTH_SNAPSHOT','DEPTH_DELTA']
    bids:tuple[DepthLevel,...]
    asks:tuple[DepthLevel,...]
    occurred_at:str
    source:Literal['PAPER_MARKET_REPLAY_SYNTHETIC']
    def __post_init__(self):
        require(self.contract_version=='2026-10-29' and self.source==SOURCE,'PAPER_MARKET_SOURCE_REQUIRED')
        require(type(self.sequence) is int and 0<self.sequence<2**63,'PAPER_MARKET_SEQUENCE_INVALID')
        require(self.kind in ('DEPTH_SNAPSHOT','DEPTH_DELTA'),'PAPER_MARKET_KIND_INVALID')
        require(type(self.product) is t.Product,'PAPER_MARKET_PRODUCT_REQUIRED')
        require(all(type(v) is str and 0<len(v)<=256 for v in (self.event_id,self.stream_id,self.instrument_uid)),
            'PAPER_MARKET_ID_INVALID')
        for side in (self.bids,self.asks):
            require(type(side) is tuple and len(side)<=256 and all(type(l) is DepthLevel for l in side),
                'PAPER_DEPTH_BOUND_REQUIRED')
            require(len({number(l.price) for l in side})==len(side),'PAPER_DEPTH_DUPLICATE_LEVEL')
            require(self.kind!='DEPTH_SNAPSHOT' or all(number(l.quantity,signed=True)>=0 for l in side),'PAPER_SNAPSHOT_NEGATIVE')
        if self.bids and self.asks:
            require(max(number(l.price) for l in self.bids)<min(number(l.price) for l in self.asks),'PAPER_CROSSED_SOURCE_BOOK')
        utc_timestamp(self.occurred_at)

    @property
    def market_key(self):
        return market_key(self.product,self.instrument_uid)


def market_key(product,instrument_uid):
    return canonical_hash({'environment':'PAPER','venue':'BINANCE','product':str(product),'instrument_uid':instrument_uid})


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMatchingModel:
    model_id:str
    quote_fee_rate:str
    def __post_init__(self):
        require(type(self.model_id) is str and 0<len(self.model_id)<=128,'PAPER_MODEL_ID_REQUIRED')
        require(0<=number(self.quote_fee_rate)<=1,'PAPER_FEE_RATE_INVALID')


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMarketResult:
    market_key:str
    event_id:str
    applied:bool


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperCreationReceipt:
    command_id:str
    request_hash:str
    scope:t.ScopeKey
    namespace:str
    client_ids:tuple[str,...]
    status:str
    evidence:t.EvidenceRef


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMatchedOrder:
    client_id:str
    venue_order_id:str
    node_id:str
    namespace:str
    status:str
    quantity:Decimal
    filled_quantity:Decimal
    remaining_quantity:Decimal
    quantity_unit:str
    lifecycle_revision:int


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperExecutionTerms:
    scope:t.ScopeKey
    client_id:str
    node_id:str
    namespace:str
    quantity:str
    quantity_step:str
    side:Literal['BUY','SELL']
    order_type:str
    limit_price:str
    post_only:bool
    tif:str
    multiplier:str
    quote_asset:str
    quote_fee_rate:str
    accepted_clock:t.ClockReading


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperExecutionEnvelope:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['PAPER_EXECUTION_ENVELOPE']
    scope:t.ScopeKey
    command_id:str
    request_hash:str
    consumption_context_hash:str
    assessment_hash:str
    source_ref:str
    node_id:str
    minimum_price:str|None
    maximum_price:str|None
    maximum_gross_notional:str
    fee_ceilings:tuple[AssetBudget,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperAcceptedContext:
    original_write:ApplicationWriteV3
    request:VenueCreationRequest
    compilation:ProgramCompilation
    rules:OrderRuleSnapshot
    model:PaperMatchingModel
    terms:PaperExecutionTerms
    execution_envelope:PaperExecutionEnvelope


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperDepthState:
    side:Literal['BID','ASK']
    price:str
    reported:str
    available:str


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMarketInput:
    event:PaperMarketEvent
    event_hash:str
    raw_payload_json:str
    before_levels:tuple[PaperDepthState,...]
    observed_clock:t.ClockReading


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperLiquidityConsumption:
    market_key:str
    event_id:str
    scope:t.ScopeKey
    client_id:str
    fill_id:str
    side:Literal['BID','ASK']
    price:Decimal
    quantity:Decimal


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMarketEvidence:
    command_id:str
    request_hash:str
    inputs:tuple[PaperMarketInput,...]
    consumptions:tuple[PaperLiquidityConsumption,...]
    evidence:t.EvidenceRef


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMarketFill:
    fill_id:str
    client_id:str
    node_id:str
    namespace:str
    quantity:Decimal
    quantity_unit:str
    price:Decimal
    price_asset:str
    gross_notional:Decimal
    fees:tuple[FeeV3,...]
    market_event_id:str
    market_hash:str
    occurred_at:str
    evidence:t.EvidenceRef
    raw_payload_json:str


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMarketDisposition:
    event_id:str
    client_id:str
    status:str
    reason:str
    market_event_id:str
    market_hash:str
    occurred_at:str
    evidence:t.EvidenceRef
    raw_payload_json:str


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperCreationReport:
    command_id:str
    request:VenueCreationRequest
    orders:tuple[PaperMatchedOrder,...]
    fills:tuple[PaperMarketFill,...]
    evidence:t.EvidenceRef
    accepted_contexts:tuple[PaperAcceptedContext,...]=()
    dispositions:tuple[PaperMarketDisposition,...]=()
    @property
    def status(self):
        require(len(self.orders)==1,'PAPER_GROUP_REQUIRES_MEMBER_STATES');return self.orders[0].status
    @property
    def filled_quantity(self):
        require(len(self.orders)==1,'PAPER_GROUP_REQUIRES_MEMBER_STATES');return self.orders[0].filled_quantity
    @property
    def remaining_quantity(self):
        require(len(self.orders)==1,'PAPER_GROUP_REQUIRES_MEMBER_STATES');return self.orders[0].remaining_quantity


def provision_paper_market(admin_dsn,roles):
    """Administrator-only additive provisioning; never called by an engine."""
    from .paper_db_roles import PaperDatabaseRoles
    require(type(roles) is PaperDatabaseRoles,'PAPER_RESTRICTED_ROLES_REQUIRED')
    require(all(re.fullmatch(r'paper_engine_[a-z0-9_]{1,45}',r) for r in (roles.runtime_role,roles.event_role)),
        'PAPER_ROLE_NAME_INVALID')
    with psycopg.connect(admin_dsn) as db:
        require(db.execute('SELECT starts_with(current_database(),%s)',('paper_engine_',)).fetchone()==(True,),
            'PAPER_ISOLATED_DATABASE_REQUIRED')
        db.execute((Path(__file__).resolve().parents[3]/'migrations/0013_paper_market_matcher.sql').read_text(encoding='utf-8'))
        for role in (roles.runtime_role,roles.event_role):
            db.execute(sql.SQL('GRANT SELECT ON public.paper_market_inputs,public.paper_market_books,public.paper_market_levels,'
                'public.paper_order_terms,public.paper_market_consumptions TO {}').format(sql.Identifier(role)))
            db.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.paper_match_order(text,text) TO {}').format(sql.Identifier(role)))
        db.execute(sql.SQL('GRANT INSERT(scope_key,client_id,market_key,terms) ON public.paper_order_terms TO {}').format(sql.Identifier(roles.runtime_role)))
        db.execute(sql.SQL('GRANT USAGE ON SEQUENCE public.paper_order_terms_arrival_sequence_seq TO {}').format(sql.Identifier(roles.runtime_role)))
        db.execute(sql.SQL('GRANT INSERT ON public.paper_market_inputs TO {}').format(sql.Identifier(roles.event_role)))
        db.execute(sql.SQL('GRANT INSERT,UPDATE ON public.paper_market_books,public.paper_market_levels TO {}').format(sql.Identifier(roles.event_role)))


def _evidence(identifier,payload,now):
    return t.EvidenceRef(id=identifier,hash=canonical_hash(payload),environment=t.Environment.PAPER,source=SOURCE,visible_at=now)


def _consumed_permit_clock(engine,permit,*,after=None,created_at=None):
    clock=engine._clock()
    require(type(clock) is t.ClockReading and clock.clock_health=='HEALTHY'
        and type(clock.monotonic_ns) is int and clock.monotonic_ns>=0
        and utc_timestamp(clock.utc_now)<utc_timestamp(permit.expires_at)
        and clock.monotonic_ns<permit.deadline_monotonic_ns
        and (created_at is None or utc_timestamp(clock.utc_now)>=utc_timestamp(created_at))
        and (after is None or utc_timestamp(clock.utc_now)>=utc_timestamp(after.utc_now)
             and clock.monotonic_ns>=after.monotonic_ns),'PAPER_CONSUMED_PERMIT_EXPIRED')
    return clock


def _current_consumed_owner(boundary,write,lease,consumption,assessment,permit):
    """Fresh owner/fence/book check; never consume twice or renew authority.

    The short business read commits before returning to each SIM write barrier.
    The host additionally serializes revocations with its side-effect guard.
    """
    current=boundary.recheck_consumed(permit,lease)
    require(digest(current.write)==digest(write) and current.request.command_id==write.command_id
        and current.consumption_context_hash==canonical_hash(consumption),'PAPER_CURRENT_CONSUMED_CONTEXT_CHANGED')


def _accepted_context(payload,request,*,db=None,engine=None):
    if payload.get('record_kind')=='PAPER_NATIVE_CHILD_TERMS_V2':
        from .paper_native_programs import restore_native_child_context
        return restore_native_child_context(engine,db,payload,request)
    if payload.get('model_revision')==2:
        from .paper_market_v2 import restore_accepted_context_v2
        return restore_accepted_context_v2(payload,request)
    names={f.name for f in fields(PaperExecutionTerms)}
    require(set(payload)==names|{'write','request','compilation','rules','model','execution_envelope'},'PAPER_ACCEPTED_CONTEXT_SHAPE_INVALID')
    context=PaperAcceptedContext(original_write=decode(ApplicationWriteV3,payload['write']),
        request=decode(VenueCreationRequest,payload['request']),compilation=decode(ProgramCompilation,payload['compilation']),
        rules=decode(OrderRuleSnapshot,payload['rules']),model=decode(PaperMatchingModel,payload['model']),
        terms=decode(PaperExecutionTerms,{key:payload[key] for key in names}),
        execution_envelope=decode(PaperExecutionEnvelope,payload['execution_envelope']))
    p=context.compilation;w=context.original_write;n=p.nodes[0];o=n.original.order
    require(digest(context.request)==digest(request) and w.command_id==request.command_id
        and w.request_hash==digest(request) and w.compilation_hash==request.compilation_hash==digest(p)
        and p.rules_hash==digest(context.rules) and context.terms.scope==request.scope,'PAPER_ACCEPTED_SOURCE_MISMATCH')
    expected=PaperExecutionTerms(scope=request.scope,client_id=request.assigned_ids.members[0].client_id,node_id=n.original.node_id,
        namespace=request.namespace.value,quantity=n.quantity.value,quantity_step=context.rules.quantity_step,side=o.side,
        order_type=n.order_type,limit_price=o.price.value,post_only=o.post_only,tif='GTC' if n.order_type=='LIMIT_MAKER' else o.tif.kind,
        multiplier=n.quantity_multiplier,quote_asset=context.rules.quote_asset,quote_fee_rate=context.model.quote_fee_rate,
        accepted_clock=context.terms.accepted_clock)
    require(digest(context.terms)==digest(expected),'PAPER_ACCEPTED_EXECUTION_TERMS_CHANGED')
    require(context.terms.accepted_clock.clock_health=='HEALTHY'
        and utc_timestamp(context.terms.accepted_clock.utc_now)>=utc_timestamp(w.created_at),
        'PAPER_ACCEPTED_CLOCK_INVALID')
    e=context.execution_envelope
    require(e.scope==request.scope and e.command_id==request.command_id and e.request_hash==digest(request)
        and e.node_id==n.original.node_id and number(e.maximum_gross_notional)>0,'PAPER_EXECUTION_ENVELOPE_CHANGED')
    return context


@exact_market_arithmetic
def submit_creation(engine,write,request,permit,*,boundary,lease,owner):
    from trading_system.contracts.application_write_v3 import ApplicationWriteV3
    from trading_system.execution.creation_requests import VenueCreationRequest
    from trading_system.execution.program_commands import ProgramCommandBoundary,ProgramWritePermit,ProgramCommandRepository
    from trading_system.runtime.execution_owner import ExecutionOwner
    require(type(write) is ApplicationWriteV3 and type(request) is VenueCreationRequest and type(permit) is ProgramWritePermit,
        'PAPER_ACTUAL_V3_COMMAND_PERMIT_REQUIRED')
    require(type(boundary) is ProgramCommandBoundary and type(owner) is ExecutionOwner and owner.engine is engine,
        'PAPER_ACTUAL_BOUNDARY_OWNER_REQUIRED')
    require(write.scope==engine.scope==boundary.scope and write.command_id==request.command_id==permit.command_id
        and write.request_hash==permit.request_hash==digest(request),'PAPER_ORIGINAL_REQUEST_REQUIRED')
    model=engine._matching_model
    from .paper_market_v2 import PaperMatchingModelV2,validate_atomic_v2,execution_tif
    v2=type(model) is PaperMatchingModelV2
    require(engine.scope.product in (t.Product.SPOT,t.Product.USD_M_PERP) or v2 and engine.scope.product==t.Product.US_EQUITY,
        'PAPER_EQUITY_SESSION_MODEL_REQUIRED' if engine.scope.product==t.Product.US_EQUITY else 'PAPER_PRODUCT_MODEL_REQUIRED')
    require(type(model) is PaperMatchingModel or v2,'PAPER_EXPLICIT_MATCHING_MODEL_REQUIRED')
    with owner.guard(lease):
        consumed=boundary.consume(permit,lease)
        require(type(consumed) is VenueCreationRequest and digest(consumed)==digest(request),'PAPER_CONSUMED_REQUEST_CHANGED')
        # Read exact archived execution economics through the real repository.
        # No open business transaction crosses into the simulated venue.
        with boundary.uow.transaction(engine.scope) as tx:
            repo=ProgramCommandRepository(tx,**boundary.readers)
            bundle=repo._bundle(write.intent_id)
            original=tx._latest('order_writes_v2',write.command_id,ApplicationWriteV3)
            require(original is not None and original.state=='MAY_HAVE_SENT' and original.request_hash==digest(request),
                'PAPER_COMMITTED_MAY_REQUIRED')
            require(digest(replace(write,state=original.state))==digest(original)
                and original.intent_id==bundle.intent.intent_id,'PAPER_ORIGINAL_WRITE_CHANGED')
            from trading_system.storage.models import scope_id
            from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
            source=tx.execute('SELECT context FROM ledger.program_permit_consumptions WHERE scope_id=%s AND command_id=%s',
                (scope_id(engine.scope),write.command_id)).fetchone()
            require(source is not None,'PAPER_PROTECTED_CONSUMPTION_REQUIRED')
            consumption=source[0]
            from trading_system.intents.quote_assessment_archive import QuoteAssessmentArchiveCodec
            is_quote=consumption['assessment'].get('record_kind')=='QUOTE_ASSESSMENT_ARCHIVE_V1'
            assessment=(QuoteAssessmentArchiveCodec().decode(consumption['assessment']) if is_quote
                else decode(ProgramFinancialAssessmentArchive,consumption['assessment']))
            require(canonical_hash(consumption['write'])==digest(original) and consumption['request_hash']==digest(request)
                and consumption['assessment_hash']==digest(assessment),'PAPER_PROTECTED_CONTEXT_CHANGED')
        if is_quote:
            from .paper_market_quote import _submit_consumed_quote
            return _submit_consumed_quote(engine,original,request,permit,boundary=boundary,lease=lease,owner=owner,
                consumption=consumption,assessment=assessment)
        plan=assessment.compilation
        require(digest(plan)==original.compilation_hash,'PAPER_CURRENT_COMPILATION_CHANGED')
        if v2 and plan.original_program.template in ('TWAP','VP'):
            from .paper_native_programs import submit_native_program
            return submit_native_program(engine,write,request,permit,boundary=boundary,lease=lease,owner=owner,
                original=original,consumption=consumption,assessment=assessment)
        native_list=v2 and engine.scope.product==t.Product.SPOT and plan.original_program.template in ('OCO','OTO','OTOCO') and plan.original_program.execution_mode=='VENUE_NATIVE'
        require((plan.original_program.template=='ATOMIC' and len(plan.nodes)==1 or native_list)
            and tuple(m.node_id for m in request.assigned_ids.members)==tuple(n.original.node_id for n in plan.nodes),
            'PAPER_PROGRAM_MATCHER_STAGE_REQUIRED')
        if native_list:
            require(request.namespace.value=='BINANCE_SPOT_ORDER_LIST' and request.operation.value=='SUBMIT_'+plan.original_program.template
                and request.assigned_ids.parent_client_id is not None,'PAPER_ACTUAL_NATIVE_PARENT_REQUIRED')
            require(all(n.original.order.stp=='NONE' for n in plan.nodes),'PAPER_NATIVE_LIST_STP_STAGE_REQUIRED')
        execution_rows=[]
        for node in plan.nodes:
            order=node.original.order
            if v2:validate_atomic_v2(engine.scope,node)
            else:
                require(node.order_type in ('LIMIT','LIMIT_MAKER') and order.price.kind=='FIXED_LIMIT' and order.trigger.kind=='NONE'
                    and order.iceberg_quantity is None and order.stp=='NONE' and order.routing=='STANDARD'
                    and order.trading_session is None
                    and (order.tif.kind in ('GTC','IOC','FOK','GTX') or node.order_type=='LIMIT_MAKER' and order.tif.kind=='NONE'),
                    'PAPER_EXECUTION_SEMANTICS_STAGE_REQUIRED')
            require(node.quantity.kind=='BASE_QUANTITY','PAPER_NATIVE_QUANTITY_REQUIRED')
            quantity_name='quantity'
            if native_list and plan.original_program.template!='OCO':
                quantity_name='workingQuantity' if node.original.role=='WORKING' else 'pendingQuantity'
            quantity_fields=tuple(f for f in request.fields if f.name==quantity_name)
            require(len(quantity_fields)==1 and type(quantity_fields[0].value) is str
                and number(quantity_fields[0].value)==number(node.quantity.value),'PAPER_NATIVE_REQUEST_QUANTITY_CHANGED')
            qty=number(node.quantity.value);client=next(m.client_id for m in request.assigned_ids.members if m.node_id==node.original.node_id)
            bounds=tuple(b for b in assessment.risk_facts.node_bounds if b.node_id==node.original.node_id)
            require(len(bounds)==1 and bounds[0].execution_fees is not None,'PAPER_CURRENT_EXECUTION_BOUND_REQUIRED')
            bound=bounds[0];ceilings={}
            for fee in bound.execution_fees:
                ceilings[fee.amount.currency]=ceilings.get(fee.amount.currency,Decimal(0))+fee.amount.amount
            require(assessment.assessment.metrics.maximum_order_notional.currency==assessment.rules.quote_asset,
                'PAPER_GROSS_BOUND_CURRENCY_CHANGED')
            gross_limit=assessment.assessment.metrics.maximum_order_notional.amount
            if native_list:
                # The scalar metric limits the initial target order. Pending
                # native exits have their own already admitted node budgets;
                # using the initial BUY cap would erase legitimate SELL upside.
                require(bound.maximum_price is not None and node.worst_case_quote_notional is not None
                    and assessment.exposure.gross_program is not None
                    and assessment.exposure.gross_program.currency==assessment.rules.quote_asset,
                    'PAPER_NATIVE_NODE_GROSS_SOURCE_REQUIRED')
                with localcontext() as arithmetic:
                    arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
                    gross_limit=min(qty*bound.maximum_price.value*number(node.quantity_multiplier),number(node.worst_case_quote_notional))
                require(0<gross_limit<=assessment.exposure.gross_program.amount,'PAPER_NATIVE_NODE_GROSS_EXCEEDS_PROGRAM')
                from trading_system.risk.valuation import Valuation
                valued=Valuation(assessment.risk_policy.valuation_currency,assessment.risk_facts.inputs.fx_rates).convert(
                    t.Money(currency=assessment.rules.quote_asset,amount=gross_limit),liability=True)
                require(valued.amount<=number(assessment.risk_policy.maximum_order_notional),'PAPER_NATIVE_NODE_POLICY_EXCEEDED')
            envelope=PaperExecutionEnvelope(contract_version='2026-10-29',record_kind='PAPER_EXECUTION_ENVELOPE',scope=engine.scope,
                command_id=write.command_id,request_hash=digest(request),consumption_context_hash=canonical_hash(consumption),
                assessment_hash=digest(assessment),source_ref='ledger.program_permit_consumptions:'+scope_id(engine.scope)+':'+write.command_id,
                node_id=node.original.node_id,minimum_price=None if bound.minimum_price is None else format(bound.minimum_price.value,'f'),
                maximum_price=None if bound.maximum_price is None else format(bound.maximum_price.value,'f'),
                maximum_gross_notional=format(gross_limit,'f'),
                fee_ceilings=tuple(AssetBudget(asset=asset,amount=format(value,'f')) for asset,value in sorted(ceilings.items())))
            if v2 and order.price.kind=='MARKET':
                require(envelope.minimum_price is not None and envelope.maximum_price is not None,'PAPER_MARKET_EXECUTION_ENVELOPE_REQUIRED')
            with localcontext() as arithmetic:
                arithmetic.prec=256;arithmetic.traps[Inexact]=True;arithmetic.traps[Rounded]=True
                fee_price=envelope.maximum_price if envelope.maximum_price is not None else order.price.value if order.side=='BUY' else None
                require(number(model.quote_fee_rate)==0 or fee_price is not None,'PAPER_SELL_FEE_UPPER_BOUND_REQUIRED')
                maximum_fee=Decimal(0) if number(model.quote_fee_rate)==0 else qty*number(fee_price)*number(node.quantity_multiplier)*number(model.quote_fee_rate)
                reserved_fee=sum((f.amount.amount for f in bound.execution_fees
                    if f.amount.currency==assessment.rules.quote_asset and f.debit=='EXTERNAL_BALANCE'),Decimal(0))
                require(maximum_fee<=reserved_fee,'PAPER_FEE_MODEL_EXCEEDS_ADMITTED_BOUND')
            terms={'scope':domain_payload(engine.scope),'client_id':client,'node_id':node.original.node_id,
                'namespace':node.namespace.value,'quantity':node.quantity.value,'quantity_step':assessment.rules.quantity_step,
                'side':order.side,'order_type':node.order_type,'limit_price':order.price.value,'post_only':order.post_only,
                'tif':execution_tif(node) if v2 else 'GTC' if node.order_type=='LIMIT_MAKER' else order.tif.kind,
                'multiplier':node.quantity_multiplier,'quote_asset':assessment.rules.quote_asset,
                'quote_fee_rate':model.quote_fee_rate,'model':domain_payload(model),'request':domain_payload(request),
                'write':domain_payload(original),'rules':domain_payload(assessment.rules),'compilation':domain_payload(plan),
                'execution_envelope':domain_payload(envelope)}
            if v2:
                terms.update(contract_version='2026-10-30',record_kind='PAPER_EXECUTION_TERMS_V2',model_revision=2,spec=domain_payload(order))
            execution_rows.append((terms,node,qty,client))
        with owner.guard(lease):
            clock=_consumed_permit_clock(engine,permit,created_at=original.created_at)
            with engine._connect() as db:
                key=market_key(engine.scope.product,engine.scope.instrument_uid)
                db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
                with owner.guard(lease):
                    _current_consumed_owner(boundary,original,lease,consumption,assessment,permit)
                    clock=_consumed_permit_clock(engine,permit,after=clock,created_at=original.created_at)
                source_book=db.execute('SELECT occurred_at,observed_clock FROM public.paper_market_books WHERE market_key=%s',(key,)).fetchone()
                if source_book is not None:
                    observed=decode(t.ClockReading,source_book['observed_clock'])
                    require(observed.clock_health=='HEALTHY' and utc_timestamp(observed.utc_now)<=utc_timestamp(clock.utc_now)
                        and source_book['occurred_at']<=utc_timestamp(clock.utc_now),'PAPER_MARKET_CLOCK_AHEAD_OF_ACCEPTANCE')
                require(db.execute('SELECT 1 FROM public.paper_engine_commands WHERE scope_key=%s AND command_id=%s',
                    (engine._scope_key,write.command_id)).fetchone() is None,'PAPER_ORIGINAL_ID_ALREADY_OBSERVED')
                for terms,node,qty,client in execution_rows:
                    terms['accepted_clock']=domain_payload(clock)
                    db.execute('INSERT INTO public.paper_engine_orders(environment,scope_key,client_id,order_id,logical_id,'
                        'owner_strategy_id,ownership_epoch,quantity_unit,price_currency,quantity) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        ('PAPER',engine._scope_key,client,'paper-v3:'+client,client,original.owner_strategy_id,
                            original.ownership_epoch,node.quantity_unit,assessment.rules.quote_asset,qty))
                    db.execute('INSERT INTO public.paper_order_terms(scope_key,client_id,market_key,terms) VALUES(%s,%s,%s,%s)',
                        (engine._scope_key,client,key,Jsonb(terms)))
                anchor=execution_rows[0][3]
                db.execute('INSERT INTO public.paper_engine_commands VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                    ('PAPER',engine._scope_key,write.command_id,anchor,anchor,'CREATE_REQUEST',digest(request),Jsonb(domain_payload(request))))
                if native_list:db.execute('SELECT public.paper_accept_native_program_v2(%s,%s)',(engine._scope_key,write.command_id))
                db.execute('SELECT public.paper_match_order(%s,%s)',(engine._scope_key,anchor))
                # Owner clock/lease remains held and current through SIM commit.
                with owner.guard(lease):
                    _current_consumed_owner(boundary,original,lease,consumption,assessment,permit)
                    _consumed_permit_clock(engine,permit,after=clock,created_at=original.created_at)
        report=query_creation(engine,write.command_id)
        return PaperCreationReceipt(command_id=write.command_id,request_hash=digest(request),scope=engine.scope,
            namespace=request.namespace.value,client_ids=tuple(row[3] for row in execution_rows),status=report.status,evidence=report.evidence)


def _read_creation_members(engine,db,command_id,request,client_ids,*,decorate=True,maximum_members=32,initial_bytes=0):
    require(len(client_ids)<=maximum_members,'PAPER_NATIVE_MEMBER_BOUND_EXCEEDED')
    rows=db.cursor(name='paper_creation_member_rows');rows.itersize=1
    rows.execute('SELECT o.*,t.terms FROM public.paper_engine_orders o JOIN public.paper_order_terms t '
        'USING(scope_key,client_id) WHERE o.scope_key=%s AND o.client_id=ANY(%s) ORDER BY t.arrival_sequence',
        (engine._scope_key,client_ids))
    orders=[];fills=[];contexts=[];dispositions=[];raw_total=initial_bytes+len(canonical_json(domain_payload(request)).encode('utf-8'))
    for row in rows:
        terms=row['terms']
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            remaining=Decimal(0) if row['lifecycle'] in ('FILLED','CANCELED','EXPIRED','REJECTED') else row['quantity']-row['filled']
        context=_accepted_context(terms,request,db=db,engine=engine)
        raw_total+=len(canonical_json(domain_payload(context)).encode('utf-8'))+1024
        require(raw_total<=16777216,'PAPER_RAW_EVIDENCE_BOUND_EXCEEDED');contexts.append(context)
        orders.append(PaperMatchedOrder(client_id=row['client_id'],venue_order_id=row['order_id'],node_id=terms['node_id'],namespace=terms['namespace'],
            status=row['lifecycle'],quantity=row['quantity'],filled_quantity=row['filled'],remaining_quantity=remaining,
            quantity_unit=row['quantity_unit'],lifecycle_revision=row['revision']))
        with db.cursor(name='paper_creation_source_events') as events:
            events.itersize=1
            events.execute('SELECT *,payload::text AS raw_payload_json FROM public.paper_engine_events WHERE scope_key=%s AND client_id=%s '
                'ORDER BY occurred_at,event_id LIMIT 4097',(engine._scope_key,row['client_id']))
            count=0
            for event in events:
                count+=1;require(count<=4096,'PAPER_FILL_EVIDENCE_BOUND_EXCEEDED')
                # SQL-created events persist SHA256(payload::text), a distinct
                # domain from app-canonical market input hashes. Keep the
                # original bytes/hash; JSON NUMERIC must never round via float.
                raw=event['raw_payload_json']
                require(len(raw.encode('utf-8'))<=1048576,'PAPER_RAW_EVIDENCE_BOUND_EXCEEDED')
                require(sha256(raw.encode('utf-8')).hexdigest()==event['payload_hash'],'PAPER_EVENT_STORED_HASH_CHANGED')
                payload=json.loads(raw,parse_float=Decimal)
                event_ref=t.EvidenceRef(id=event['event_id'],hash=event['payload_hash'],environment=t.Environment.PAPER,
                    source=SOURCE,visible_at=event['occurred_at'].isoformat())
                if payload.get('record_kind')=='PAPER_MARKET_DISPOSITION':
                    source=db.execute('SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=%s AND event_id=%s',
                        (payload['market_key'],payload['market_event_id'])).fetchone()
                    require(source is not None and source['payload_hash']==payload['market_hash'] and event['fill_delta']==0,
                        'PAPER_DISPOSITION_SOURCE_REQUIRED')
                    item=PaperMarketDisposition(event_id=event['event_id'],client_id=row['client_id'],status=payload['status'],
                        reason=payload['reason'],market_event_id=payload['market_event_id'],market_hash=payload['market_hash'],
                        occurred_at=event['occurred_at'].isoformat(),evidence=event_ref,
                        raw_payload_json=raw)
                    raw_total+=len(canonical_json(domain_payload(item)).encode('utf-8'))
                    require(raw_total<=16777216,'PAPER_RAW_EVIDENCE_BOUND_EXCEEDED');dispositions.append(item)
                    continue
                require(payload.get('record_kind')=='PAPER_MARKET_FILL','PAPER_MARKET_SOURCE_REQUIRED')
                source=db.execute('SELECT c.quantity,c.price,i.payload_hash FROM public.paper_market_consumptions c '
                    'JOIN public.paper_market_inputs i USING(market_key,event_id) WHERE c.scope_key=%s AND c.client_id=%s AND c.fill_id=%s',
                    (engine._scope_key,row['client_id'],event['event_id'])).fetchone()
                require(source is not None and source['quantity']==event['fill_delta'] and source['price']==event['price']
                    and source['payload_hash']==payload['market_hash'],'PAPER_FILL_LIQUIDITY_SOURCE_REQUIRED')
                item=PaperMarketFill(fill_id=event['event_id'],client_id=row['client_id'],node_id=terms['node_id'],
                    namespace=terms['namespace'],quantity=event['fill_delta'],quantity_unit=row['quantity_unit'],
                    price=event['price'],price_asset=row['price_currency'],gross_notional=Decimal(payload['gross_notional']),
                    fees=tuple(decode(FeeV3,f) for f in payload['fees']),market_event_id=payload['market_event_id'],market_hash=payload['market_hash'],
                    occurred_at=event['occurred_at'].isoformat(),evidence=event_ref,
                    raw_payload_json=raw)
                raw_total+=len(canonical_json(domain_payload(item)).encode('utf-8'))
                require(raw_total<=16777216,'PAPER_RAW_EVIDENCE_BOUND_EXCEEDED');fills.append(item)
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            require(sum((f.quantity for f in fills if f.client_id==row['client_id']),Decimal(0))==row['filled'],
                'PAPER_FILL_SOURCE_INCOMPLETE')
    rows.close()
    require(len(orders)==len(client_ids),'PAPER_COMPLETE_NATIVE_MEMBER_INVENTORY_REQUIRED')
    payload={'request':domain_payload(request),'orders':[domain_payload(o) for o in orders],'fills':[domain_payload(f) for f in fills],
        'accepted_contexts':[domain_payload(c) for c in contexts],'dispositions':[domain_payload(d) for d in dispositions]}
    report=PaperCreationReport(command_id=command_id,request=request,orders=tuple(orders),fills=tuple(fills),
        accepted_contexts=tuple(contexts),dispositions=tuple(dispositions),evidence=_evidence('paper-market-query:'+command_id,payload,engine._clock().utc_now))
    if decorate and any(getattr(context.terms,'model_revision',None)==2 for context in contexts):
        from .paper_market_v2 import decorate_report_v2
        from trading_system.execution.program_effective_orders import decorate_managed_report
        return decorate_managed_report(engine,db,decorate_report_v2(engine,db,report))
    return report


def query_creation(engine,command_id):
    from .paper_market_quote import query_quote_creation
    quote=query_quote_creation(engine,command_id)
    if quote is not None:return quote
    from trading_system.execution.creation_requests import VenueCreationRequest
    with engine._connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        command=db.execute('SELECT * FROM public.paper_engine_commands WHERE scope_key=%s AND command_id=%s AND operation=%s',
            (engine._scope_key,command_id,'CREATE_REQUEST')).fetchone()
        if command is None:return None
        request=decode(VenueCreationRequest,command['request'])
        require(request.command_id==command_id and request.scope==engine.scope and digest(request)==command['request_hash'],
            'PAPER_REQUEST_QUERY_BINDING_CHANGED')
        if command['target_client_id'] is None:
            from .paper_native_programs import query_native_program
            return query_native_program(engine,db,command)
        return _read_creation_members(engine,db,command_id,request,[m.client_id for m in request.assigned_ids.members])


def query_market_evidence(engine,command_id):
    """Server-only bounded source archive, including competing allocations."""
    with engine._connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        command=db.execute('SELECT * FROM public.paper_engine_commands WHERE scope_key=%s AND command_id=%s AND operation=%s',
            (engine._scope_key,command_id,'CREATE_REQUEST')).fetchone()
        if command is None:return None
        request=decode(VenueCreationRequest,command['request'])
        require(request.scope==engine.scope and request.command_id==command_id and digest(request)==command['request_hash'],
            'PAPER_MARKET_QUERY_BINDING_CHANGED')
        native_parent=command['target_client_id'] is None
        if native_parent:
            from .paper_market_v2 import PaperMatchingModelV2
            require(type(engine._matching_model) is PaperMatchingModelV2,'PAPER_NATIVE_PARENT_READER_REQUIRED')
            v2=True
        else:
            v2=db.execute("SELECT terms->>'model_revision' AS revision FROM public.paper_order_terms WHERE scope_key=%s AND client_id=%s",
                (engine._scope_key,command['target_client_id'])).fetchone()['revision']=='2'
        inputs=[];consumptions=[];used_bytes=512
        # Named cursors fetch one source/allocation at a time. Bounds apply
        # globally before append, not per event after loading an entire history.
        with db.cursor(name='paper_market_evidence_sources') as source_cursor:
            source_cursor.itersize=1
            source_query=('SELECT DISTINCT i.market_key,i.sequence,i.event_id,i.payload_hash,i.payload,i.before_levels,i.observed_clock '
                "FROM public.paper_market_inputs i JOIN public.paper_engine_events e ON e.payload->>'market_key'=i.market_key "
                "AND (e.payload->>'market_event_id'=i.event_id OR e.payload->>'calendar_event_id'=i.event_id "
                "OR e.payload->>'depth_event_id'=i.event_id) WHERE e.scope_key=%s AND e.client_id=%s "
                'ORDER BY i.market_key,i.sequence,i.event_id LIMIT 4097')
            if v2:
                source_query=('SELECT DISTINCT i.market_key,i.sequence,i.event_id,i.payload_hash,i.payload,i.before_levels,i.observed_clock '
                    'FROM public.paper_market_inputs i JOIN public.paper_order_input_dependencies_v2 d USING(market_key,event_id) '
                    'WHERE d.scope_key=%s AND d.client_id=ANY(%s) ORDER BY i.market_key,i.sequence,i.event_id LIMIT 4097')
            source_parameters=(engine._scope_key,[m.client_id for m in request.assigned_ids.members] if v2 else command['target_client_id'])
            if native_parent:
                source_query=('SELECT i.market_key,i.sequence,i.event_id,i.payload_hash,i.payload,i.before_levels,i.observed_clock '
                    'FROM public.paper_market_inputs i JOIN public.paper_native_parent_input_dependencies_v2 d USING(market_key,event_id) '
                    'WHERE d.scope_key=%s AND d.parent_client_id=%s ORDER BY i.market_key,i.sequence,i.event_id LIMIT 4097')
                source_parameters=(engine._scope_key,request.assigned_ids.parent_client_id)
            source_cursor.execute(source_query,source_parameters)
            for row in source_cursor:
                require(len(inputs)<4096,'PAPER_MARKET_EVIDENCE_BOUND_EXCEEDED')
                from .paper_market_v2 import decode_source_event
                event=decode_source_event(row['payload']);require(digest(event)==row['payload_hash'],'PAPER_MARKET_ARCHIVE_HASH_CHANGED')
                raw=canonical_json(row['payload'])
                require(len(raw.encode('utf-8'))<=1048576 and len(row['before_levels'])<=8192,'PAPER_MARKET_RAW_EVIDENCE_BOUND_EXCEEDED')
                item=PaperMarketInput(event=event,event_hash=row['payload_hash'],raw_payload_json=raw,before_levels=tuple(
                    decode(PaperDepthState,level) for level in row['before_levels']),observed_clock=decode(t.ClockReading,row['observed_clock']))
                used_bytes+=len(canonical_json(domain_payload(item)).encode('utf-8'))
                require(used_bytes<=16777216,'PAPER_MARKET_TOTAL_EVIDENCE_BOUND_EXCEEDED');inputs.append(item)
                with db.cursor(name='paper_market_evidence_allocations') as allocations:
                    allocations.itersize=1
                    allocation_sql=('SELECT market_key,event_id,scope_key,client_id,fill_id,side,price,quantity '
                        'FROM public.paper_market_consumptions WHERE market_key=%s AND event_id=%s')
                    allocation_args=(row['market_key'],row['event_id'])
                    if db.execute("SELECT to_regclass('public.paper_quote_allocations_v1') AS relation").fetchone()['relation'] is not None:
                        allocation_sql+=(' UNION ALL SELECT a.market_key,a.event_id,a.scope_key,t.client_id,a.fill_id,a.side,a.price,a.quantity '
                            'FROM public.paper_quote_allocations_v1 a JOIN public.paper_quote_terms_v1 t USING(scope_key,command_id) '
                            'WHERE a.market_key=%s AND a.event_id=%s')
                        allocation_args+=allocation_args
                    allocations.execute(allocation_sql+' ORDER BY fill_id LIMIT %s',allocation_args+(100001-len(consumptions),))
                    for allocated in allocations:
                        require(len(consumptions)<100000,'PAPER_MARKET_ALLOCATION_BOUND_EXCEEDED')
                        item=PaperLiquidityConsumption(market_key=allocated['market_key'],event_id=allocated['event_id'],
                            scope=decode(t.ScopeKey,allocated['scope_key']),client_id=allocated['client_id'],fill_id=allocated['fill_id'],
                            side=allocated['side'],price=allocated['price'],quantity=allocated['quantity'])
                        used_bytes+=len(canonical_json(domain_payload(item)).encode('utf-8'))
                        require(used_bytes<=16777216,'PAPER_MARKET_TOTAL_EVIDENCE_BOUND_EXCEEDED');consumptions.append(item)
        if v2:
            from .paper_market_v2 import decorate_market_evidence_v2
            return decorate_market_evidence_v2(engine,db,command_id,request,inputs,consumptions,used_bytes)
        payload={'command_id':command_id,'request_hash':digest(request),'inputs':[domain_payload(i) for i in inputs],
            'consumptions':[domain_payload(c) for c in consumptions]}
        return PaperMarketEvidence(command_id=command_id,request_hash=digest(request),inputs=tuple(inputs),consumptions=tuple(consumptions),
            evidence=_evidence('paper-market-source:'+command_id,payload,engine._clock().utc_now))


class PaperMatcher:
    def __init__(self,engine):
        from .paper import PaperEngine
        require(type(engine) is PaperEngine and engine.scope.environment==t.Environment.PAPER,'PAPER_ENGINE_REQUIRED')
        self.engine=engine

    @exact_market_arithmetic
    def apply_market_event(self,event):
        from .paper_market_v2 import PaperReferenceEventV2,PaperClockEventV2,PaperCalendarEventV2,PaperLiquidityOwnershipEventV2,PaperPositionEventV2,apply_reference_event
        if type(event) in (PaperReferenceEventV2,PaperClockEventV2,PaperCalendarEventV2,PaperLiquidityOwnershipEventV2,PaperPositionEventV2):return apply_reference_event(self.engine,event)
        require(type(event) is PaperMarketEvent and event.product==self.engine.scope.product
            and event.instrument_uid==self.engine.scope.instrument_uid,'PAPER_MARKET_SCOPE_MISMATCH')
        now=self.engine._clock()
        require(type(now) is t.ClockReading and now.clock_health=='HEALTHY' and type(now.monotonic_ns) is int
            and now.monotonic_ns>=0 and utc_timestamp(event.occurred_at)<=utc_timestamp(now.utc_now),'PAPER_MARKET_CLOCK_REQUIRED')
        with self.engine._event_connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(event.market_key,))
            previous=db.execute('SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=%s AND event_id=%s',
                (event.market_key,event.event_id)).fetchone()
            if previous:
                require(previous['payload_hash']==digest(event),'PAPER_MARKET_EVENT_CONFLICT')
                return PaperMarketResult(market_key=event.market_key,event_id=event.event_id,applied=False)
            book=db.execute('SELECT * FROM public.paper_market_books WHERE market_key=%s FOR UPDATE',(event.market_key,)).fetchone()
            require(book is None or (book['stream_id']==event.stream_id and event.sequence>book['sequence']
                and utc_timestamp(event.occurred_at)>=book['occurred_at']
                and utc_timestamp(now.utc_now)>=utc_timestamp(book['observed_clock']['utc_now'])
                and now.monotonic_ns>=book['observed_clock']['monotonic_ns']),'PAPER_MARKET_SEQUENCE_CONFLICT')
            require(event.kind=='DEPTH_SNAPSHOT' or (book is not None and event.sequence==book['sequence']+1),
                'PAPER_DELTA_GAP_REQUIRES_SNAPSHOT')
            before=db.execute('SELECT side,price,reported,available FROM public.paper_market_levels WHERE market_key=%s ORDER BY side,price LIMIT 8193',
                (event.market_key,)).fetchall()
            require(len(before)<=8192,'PAPER_MARKET_STATE_BOUND_EXCEEDED')
            before_payload=[dict(side=r['side'],price=format(r['price'],'f'),reported=format(r['reported'],'f'),available=format(r['available'],'f')) for r in before]
            db.execute('INSERT INTO public.paper_market_inputs VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (event.market_key,event.event_id,event.sequence,digest(event),Jsonb(domain_payload(event)),Jsonb(before_payload),Jsonb(domain_payload(now))))
            db.execute('INSERT INTO public.paper_market_books VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(market_key) DO UPDATE '
                'SET sequence=excluded.sequence,event_id=excluded.event_id,occurred_at=excluded.occurred_at,observed_clock=excluded.observed_clock',
                (event.market_key,str(event.product),event.instrument_uid,event.stream_id,event.sequence,event.event_id,event.occurred_at,Jsonb(domain_payload(now))))
            for side,levels in (('BID',event.bids),('ASK',event.asks)):
                present=[]
                for level in levels:
                    price=number(level.price);quantity=number(level.quantity,signed=True);present.append(price)
                    prior=db.execute('SELECT reported,available FROM public.paper_market_levels WHERE market_key=%s AND side=%s AND price=%s FOR UPDATE',
                        (event.market_key,side,price)).fetchone()
                    reported=quantity if event.kind=='DEPTH_SNAPSHOT' else (prior['reported'] if prior else Decimal(0))+quantity
                    available=(quantity if prior is None else min(prior['available'],quantity)) if event.kind=='DEPTH_SNAPSHOT' else max(
                        Decimal(0),(prior['available'] if prior else Decimal(0))+quantity)
                    require(reported>=0,'PAPER_DEPTH_DELTA_NEGATIVE_TOTAL')
                    db.execute('INSERT INTO public.paper_market_levels VALUES(%s,%s,%s,%s,%s) ON CONFLICT(market_key,side,price) '
                        'DO UPDATE SET reported=excluded.reported,available=excluded.available',(event.market_key,side,price,reported,available))
                if event.kind=='DEPTH_SNAPSHOT':
                    db.execute('UPDATE public.paper_market_levels SET reported=0,available=0 WHERE market_key=%s AND side=%s '
                        'AND NOT(price=ANY(%s))',(event.market_key,side,present))
            bounds=db.execute("SELECT max(price) FILTER(WHERE side='BID') AS bid,min(price) FILTER(WHERE side='ASK') AS ask "
                'FROM public.paper_market_levels WHERE market_key=%s AND reported>0',(event.market_key,)).fetchone()
            require(db.execute('SELECT count(*) AS count FROM public.paper_market_levels WHERE market_key=%s',
                (event.market_key,)).fetchone()['count']<=8192,'PAPER_MARKET_STATE_BOUND_EXCEEDED')
            require(bounds['bid'] is None or bounds['ask'] is None or bounds['bid']<bounds['ask'],'PAPER_CROSSED_SOURCE_BOOK')
            from .paper_native_programs import advance_native_programs
            advance_native_programs(db,event.market_key)
            first=db.execute('SELECT scope_key,client_id FROM public.paper_order_terms WHERE market_key=%s ORDER BY arrival_sequence LIMIT 1',
                (event.market_key,)).fetchone()
            if first:db.execute('SELECT public.paper_match_order(%s,%s)',(first['scope_key'],first['client_id']))
        return PaperMarketResult(market_key=event.market_key,event_id=event.event_id,applied=True)
