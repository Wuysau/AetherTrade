"""Additive venue model/source revision. No financial admission or send grants."""
from dataclasses import dataclass,fields,replace
from decimal import Decimal
from pathlib import Path
from typing import Literal
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.order_program import AtomicOrderSpec
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.storage.models import decode
from .paper_market import (SOURCE,PaperMatchingModel,PaperExecutionTerms,PaperAcceptedContext,PaperMatchedOrder,
    PaperCreationReport,PaperMarketEvent,PaperMarketResult,PaperExecutionEnvelope,number,require,digest,market_key,_evidence)
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.order_snapshots import OrderRuleSnapshot
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.intents.program_compilation import ProgramCompilation


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMatchingModelV2(PaperMatchingModel):
    contract_version:Literal['2026-10-30']='2026-10-30'
    model_revision:Literal[2]=2
    iceberg_reveal_policy:Literal['ONE_TRANCHE_PER_INPUT_FIFO']='ONE_TRANCHE_PER_INPUT_FIFO'
    def __post_init__(self):
        PaperMatchingModel.__post_init__(self)
        require(self.contract_version=='2026-10-30' and type(self.model_revision) is int and self.model_revision==2,
            'PAPER_MODEL_REVISION_INVALID')
        require(self.iceberg_reveal_policy=='ONE_TRANCHE_PER_INPUT_FIFO','PAPER_ICEBERG_MODEL_UNKNOWN')


@dataclass(frozen=True,slots=True,kw_only=True)
class ReferencePrice:
    source:Literal['LAST','CONTRACT_PRICE','MARK_PRICE','BID','ASK']
    price:str
    def __post_init__(self):
        require(self.source in ('LAST','CONTRACT_PRICE','MARK_PRICE','BID','ASK') and number(self.price)>0,
            'PAPER_REFERENCE_PRICE_INVALID')


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperReferenceEventV2:
    contract_version:Literal['2026-10-30']
    event_id:str
    stream_id:str
    product:t.Product
    instrument_uid:str
    sequence:int
    kind:Literal['REFERENCE_PRICES']
    prices:tuple[ReferencePrice,...]
    occurred_at:str
    source:Literal['PAPER_MARKET_REPLAY_SYNTHETIC']
    def __post_init__(self):
        require(self.contract_version=='2026-10-30' and self.kind=='REFERENCE_PRICES' and self.source==SOURCE,
            'PAPER_REFERENCE_VERSION_INVALID')
        require(type(self.sequence) is int and 0<self.sequence<2**63,'PAPER_REFERENCE_SEQUENCE_INVALID')
        require(type(self.product) is t.Product and all(type(v) is str and 0<len(v)<=256
            for v in (self.event_id,self.stream_id,self.instrument_uid)),'PAPER_REFERENCE_SCOPE_REQUIRED')
        require(type(self.prices) is tuple and 1<=len(self.prices)<=5 and all(type(p) is ReferencePrice for p in self.prices)
            and len({p.source for p in self.prices})==len(self.prices),'PAPER_REFERENCE_SOURCES_INVALID')
        utc_timestamp(self.occurred_at)
    @property
    def market_key(self):return market_key(self.product,self.instrument_uid)


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperExecutionTermsV2(PaperExecutionTerms):
    limit_price:str|None
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PAPER_EXECUTION_TERMS_V2']
    model_revision:Literal[2]
    spec:AtomicOrderSpec


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperAcceptedContextV2(PaperAcceptedContext):
    model:PaperMatchingModelV2
    terms:PaperExecutionTermsV2


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperOrderRuntimeV2:
    active:bool
    phase:str
    activation_event_id:str|None
    high_water:Decimal|None
    low_water:Decimal|None
    effective_limit:Decimal|None
    visible_remaining:Decimal|None
    queue_priority:int


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperMatchedOrderV2(PaperMatchedOrder):
    runtime:PaperOrderRuntimeV2


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperCreationReportV2(PaperCreationReport):
    orders:tuple[PaperMatchedOrderV2,...]
    accepted_contexts:tuple[PaperAcceptedContextV2,...]=()
    contract_version:Literal['2026-10-30']='2026-10-30'
    record_kind:Literal['PAPER_CREATION_REPORT_V2']='PAPER_CREATION_REPORT_V2'


def validate_atomic_v2(scope,node):
    o=node.original.order
    require(scope.product in (t.Product.SPOT,t.Product.USD_M_PERP),'PAPER_EQUITY_SESSION_MODEL_REQUIRED')
    require(o.price.kind in ('FIXED_LIMIT','MARKET','PEG','PRICE_MATCH') and o.trigger.kind in ('NONE','STATIC','TRAILING')
        and o.stp=='NONE' and o.routing=='STANDARD' and o.trading_session is None
        and o.tif.kind in ('NONE','GTC','IOC','FOK','GTX'),'PAPER_V2_EXECUTION_STAGE_REQUIRED')
    # Conditional USD-M has a separate native parent/ordinary-child identity.
    # Its model is added with native parent membership; never alias the algo ID.
    require(scope.product!=t.Product.USD_M_PERP or o.trigger.kind=='NONE','PAPER_USDM_CONDITIONAL_MEMBERSHIP_REQUIRED')


def execution_tif(node):
    if node.original.order.price.kind=='MARKET':return 'IOC'
    return 'GTC' if node.order_type=='LIMIT_MAKER' else node.original.order.tif.kind


def restore_accepted_context_v2(payload,request):
    names={f.name for f in fields(PaperExecutionTermsV2)}
    require(set(payload)==names|{'write','request','compilation','rules','model','execution_envelope'},'PAPER_V2_CONTEXT_SHAPE_INVALID')
    c=PaperAcceptedContextV2(original_write=decode(ApplicationWriteV3,payload['write']),request=decode(VenueCreationRequest,payload['request']),
        compilation=decode(ProgramCompilation,payload['compilation']),rules=decode(OrderRuleSnapshot,payload['rules']),
        model=decode(PaperMatchingModelV2,payload['model']),terms=decode(PaperExecutionTermsV2,{k:payload[k] for k in names}),
        execution_envelope=decode(PaperExecutionEnvelope,payload['execution_envelope']))
    p=c.compilation;n=p.nodes[0];o=n.original.order;w=c.original_write;e=c.execution_envelope
    require(digest(c.request)==digest(request) and w.command_id==request.command_id and w.request_hash==digest(request)
        and w.compilation_hash==request.compilation_hash==digest(p) and p.rules_hash==digest(c.rules)
        and c.terms.scope==request.scope,'PAPER_V2_ACCEPTED_SOURCE_CHANGED')
    expected=PaperExecutionTermsV2(scope=request.scope,client_id=request.assigned_ids.members[0].client_id,node_id=n.original.node_id,
        namespace=request.namespace.value,quantity=n.quantity.value,quantity_step=c.rules.quantity_step,side=o.side,
        order_type=n.order_type,limit_price=o.price.value,post_only=o.post_only,tif=execution_tif(n),multiplier=n.quantity_multiplier,
        quote_asset=c.rules.quote_asset,quote_fee_rate=c.model.quote_fee_rate,accepted_clock=c.terms.accepted_clock,
        contract_version='2026-10-30',record_kind='PAPER_EXECUTION_TERMS_V2',model_revision=2,spec=o)
    require(digest(c.terms)==digest(expected),'PAPER_V2_EXECUTION_TERMS_CHANGED')
    require(c.terms.accepted_clock.clock_health=='HEALTHY' and utc_timestamp(c.terms.accepted_clock.utc_now)>=utc_timestamp(w.created_at)
        and e.scope==request.scope and e.command_id==request.command_id and e.request_hash==digest(request)
        and e.node_id==n.original.node_id and number(e.maximum_gross_notional)>0,'PAPER_V2_ACCEPTED_ENVELOPE_CHANGED')
    return c


def decorate_report_v2(engine,db,report):
    orders=[]
    for order in report.orders:
        row=db.execute('SELECT active,phase,activation_event_id,high_water,low_water,effective_limit,visible_remaining,queue_priority FROM public.paper_order_runtime_v2 '
            'WHERE scope_key=%s AND client_id=%s',(engine._scope_key,order.client_id)).fetchone()
        require(row is not None,'PAPER_V2_RUNTIME_SOURCE_REQUIRED')
        runtime=PaperOrderRuntimeV2(**row)
        orders.append(PaperMatchedOrderV2(**{f.name:getattr(order,f.name) for f in fields(PaperMatchedOrder)},runtime=runtime))
    payload={'request':domain_payload(report.request),'orders':[domain_payload(o) for o in orders],
        'fills':[domain_payload(f) for f in report.fills],'accepted_contexts':[domain_payload(c) for c in report.accepted_contexts],
        'dispositions':[domain_payload(d) for d in report.dispositions],'contract_version':'2026-10-30','record_kind':'PAPER_CREATION_REPORT_V2'}
    return PaperCreationReportV2(command_id=report.command_id,request=report.request,orders=tuple(orders),fills=report.fills,
        accepted_contexts=report.accepted_contexts,dispositions=report.dispositions,
        evidence=_evidence('paper-market-v2-query:'+report.command_id,payload,engine._clock().utc_now))


def decode_source_event(payload):
    if payload.get('contract_version')=='2026-10-30':return decode(PaperReferenceEventV2,payload)
    return decode(PaperMarketEvent,payload)


def provision_paper_market_v2(admin_dsn,roles):
    from .paper_db_roles import PaperDatabaseRoles
    require(type(roles) is PaperDatabaseRoles,'PAPER_RESTRICTED_ROLES_REQUIRED')
    with psycopg.connect(admin_dsn) as db:
        require(db.execute('SELECT starts_with(current_database(),%s)',('paper_engine_',)).fetchone()==(True,),
            'PAPER_ISOLATED_DATABASE_REQUIRED')
        db.execute((Path(__file__).resolve().parents[3]/'migrations/0014_paper_market_matcher_v2.sql').read_text(encoding='utf-8'))
        for role in (roles.runtime_role,roles.event_role):
            db.execute(sql.SQL('GRANT SELECT ON public.paper_reference_prices_v2,public.paper_order_runtime_v2,'
                'public.paper_market_state_v2 TO {}').format(sql.Identifier(role)))
        db.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.paper_match_order(text,text) TO {}').format(sql.Identifier(roles.runtime_role)))
        db.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.paper_match_order(text,text) TO {}').format(sql.Identifier(roles.event_role)))


def apply_reference_event(engine,event):
    require(type(event) is PaperReferenceEventV2 and event.product==engine.scope.product and event.instrument_uid==engine.scope.instrument_uid,
        'PAPER_REFERENCE_SCOPE_MISMATCH')
    now=engine._clock()
    require(type(now) is t.ClockReading and now.clock_health=='HEALTHY' and type(now.monotonic_ns) is int
        and now.monotonic_ns>=0 and utc_timestamp(event.occurred_at)<=utc_timestamp(now.utc_now),'PAPER_REFERENCE_CLOCK_REQUIRED')
    with engine._event_connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(event.market_key,))
        prior=db.execute('SELECT payload_hash FROM public.paper_market_inputs WHERE market_key=%s AND event_id=%s',
            (event.market_key,event.event_id)).fetchone()
        if prior:
            require(prior['payload_hash']==digest(event),'PAPER_REFERENCE_EVENT_CONFLICT')
            return PaperMarketResult(market_key=event.market_key,event_id=event.event_id,applied=False)
        book=db.execute('SELECT * FROM public.paper_market_books WHERE market_key=%s FOR UPDATE',(event.market_key,)).fetchone()
        require(book is None or (book['stream_id']==event.stream_id and event.sequence==book['sequence']+1
            and utc_timestamp(event.occurred_at)>=book['occurred_at']
            and utc_timestamp(now.utc_now)>=utc_timestamp(book['observed_clock']['utc_now'])
            and now.monotonic_ns>=book['observed_clock']['monotonic_ns']),'PAPER_REFERENCE_SEQUENCE_CONFLICT')
        before=db.execute('SELECT side,price,reported,available FROM public.paper_market_levels WHERE market_key=%s '
            'ORDER BY side,price LIMIT 8193',(event.market_key,)).fetchall()
        require(len(before)<=8192,'PAPER_MARKET_STATE_BOUND_EXCEEDED')
        levels=[dict(side=r['side'],price=format(r['price'],'f'),reported=format(r['reported'],'f'),available=format(r['available'],'f')) for r in before]
        db.execute('INSERT INTO public.paper_market_inputs VALUES(%s,%s,%s,%s,%s,%s,%s)',
            (event.market_key,event.event_id,event.sequence,digest(event),Jsonb(domain_payload(event)),Jsonb(levels),Jsonb(domain_payload(now))))
        db.execute('INSERT INTO public.paper_market_books VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(market_key) DO UPDATE '
            'SET sequence=excluded.sequence,event_id=excluded.event_id,occurred_at=excluded.occurred_at,observed_clock=excluded.observed_clock',
            (event.market_key,str(event.product),event.instrument_uid,event.stream_id,event.sequence,event.event_id,event.occurred_at,Jsonb(domain_payload(now))))
        first=db.execute('SELECT scope_key,client_id FROM public.paper_order_terms WHERE market_key=%s ORDER BY arrival_sequence LIMIT 1',
            (event.market_key,)).fetchone()
        if first:db.execute('SELECT public.paper_match_order(%s,%s)',(first['scope_key'],first['client_id']))
    return PaperMarketResult(market_key=event.market_key,event_id=event.event_id,applied=True)
