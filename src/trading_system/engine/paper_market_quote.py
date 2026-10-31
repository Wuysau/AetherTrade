"""Immediate native quote budget execution, sharing the existing PAPER depth.

No financial authorization is created here. The public adapter consumes the
existing permit once before entering this module; quote reports are facts only.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Literal
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from trading_system.contracts import types as t
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.canonical import domain_payload, canonical_hash, utc_timestamp
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.intents.quote_assessment_archive import QuoteAssessmentArchiveV1
from trading_system.storage.models import decode
from .paper_market import (require,number,digest,market_key,_evidence,exact_market_arithmetic,
    _current_consumed_owner,_consumed_permit_clock,PaperCreationReceipt,PaperMatchingModel)


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperQuoteTermsV1:
    record_kind:Literal['PAPER_QUOTE_TERMS_V1']
    scope:t.ScopeKey
    request:VenueCreationRequest
    write:ApplicationWriteV3
    archive:QuoteAssessmentArchiveV1
    consumption_context_hash:str
    client_id:str
    requested_quote:str
    quantity_step:str
    side:Literal['BUY','SELL']
    minimum_price:str
    maximum_price:str
    quote_asset:str
    quote_fee_rate:str
    model:PaperMatchingModel
    accepted_clock:t.ClockReading


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperQuoteFillV1:
    fill_id:str
    quantity:Decimal
    price:Decimal
    gross_quote:Decimal
    quote_fee:Decimal
    fee_debit:Literal['EXTERNAL_BALANCE']
    quantity_unit:Literal['BASE']
    quote_asset:str
    market_event_id:str
    market_hash:str


@dataclass(frozen=True,slots=True,kw_only=True)
class PaperQuoteReportV1:
    record_kind:Literal['PAPER_QUOTE_REPORT_V1']
    command_id:str
    request:VenueCreationRequest
    terms:PaperQuoteTermsV1
    status:str
    reason:str
    actual_base:Decimal
    gross_quote:Decimal
    remaining_quote:Decimal
    fills:tuple[PaperQuoteFillV1,...]
    evidence:t.EvidenceRef

    @property
    def execution_authorized(self)->Literal[False]:return False


def provision_paper_quote(admin_dsn,roles):
    """Administrator-only, additive installation in a provisioned SIM database."""
    from .paper_db_roles import PaperDatabaseRoles
    require(type(roles) is PaperDatabaseRoles,'PAPER_RESTRICTED_ROLES_REQUIRED')
    with psycopg.connect(admin_dsn) as db:
        require(db.execute("SELECT starts_with(current_database(),'paper_engine_')").fetchone()==(True,),
            'PAPER_ISOLATED_DATABASE_REQUIRED')
        db.execute((Path(__file__).resolve().parents[3]/'migrations/0025_paper_quote_budget.sql').read_text(encoding='utf-8'))
        for role in (roles.runtime_role,roles.event_role):
            db.execute(sql.SQL('GRANT SELECT ON public.paper_quote_terms_v1,public.paper_quote_runtime_v1,'
                'public.paper_quote_allocations_v1 TO {}').format(sql.Identifier(role)))
        db.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.paper_accept_quote_v1(text,text,text,text,jsonb) TO {}')
            .format(sql.Identifier(roles.runtime_role)))


@exact_market_arithmetic
def build_quote_terms(engine,original,request,consumption,assessment,clock):
    plan=assessment.compilation
    require(engine.scope.product==t.Product.SPOT and plan.original_program.template=='ATOMIC'
        and len(plan.nodes)==1 and len(request.assigned_ids.members)==1,'PAPER_QUOTE_ATOMIC_SPOT_REQUIRED')
    node=plan.nodes[0];order=node.original.order;bounds=assessment.risk_facts.target_facts.bounds
    require(digest(plan)==original.compilation_hash and node.quantity.kind=='QUOTE_NOTIONAL'
        and order.price.kind=='MARKET' and order.routing=='STANDARD' and order.stp=='NONE'
        and order.trigger.kind=='NONE' and order.iceberg_quantity is None,'PAPER_QUOTE_SEMANTICS_REQUIRED')
    params=dict(request.form_parameters())
    require(params.get('quoteOrderQty')==node.quantity.value and 'quantity' not in params
        and number(node.quantity_multiplier)==1,'PAPER_ORIGINAL_QUOTE_REQUEST_REQUIRED')
    require(bounds.enforcement_kind=='PAPER_INTERNAL_ENVELOPE' and bounds.fees is not None
        and bounds.minimum_price.currency==bounds.maximum_price.currency==assessment.rules.quote_asset
        and 0<bounds.minimum_price.value<=bounds.maximum_price.value
        and number(node.quantity.value)<=bounds.maximum_gross_quote.amount,'PAPER_QUOTE_BOUNDS_REQUIRED')
    model=engine._matching_model
    require(type(model) is PaperMatchingModel,'PAPER_QUOTE_MODEL_REQUIRED')
    require(all(f.amount.currency==assessment.rules.quote_asset and f.debit=='EXTERNAL_BALANCE' for f in bounds.fees),
        'PAPER_QUOTE_FEE_MODEL_UNSUPPORTED')
    ceiling=sum((f.amount.amount for f in bounds.fees),Decimal(0))
    require(number(node.quantity.value)*number(model.quote_fee_rate)<=ceiling,'PAPER_FEE_MODEL_EXCEEDS_ADMITTED_BOUND')
    step=assessment.profile.market_quantity_step
    require(number(step)>0,'PAPER_QUOTE_LOT_REQUIRED')
    return PaperQuoteTermsV1(record_kind='PAPER_QUOTE_TERMS_V1',scope=engine.scope,request=request,write=original,
        archive=assessment,consumption_context_hash=canonical_hash(consumption),client_id=request.assigned_ids.members[0].client_id,
        requested_quote=node.quantity.value,quantity_step=step,side=order.side,
        minimum_price=format(bounds.minimum_price.value,'f'),maximum_price=format(bounds.maximum_price.value,'f'),
        quote_asset=assessment.rules.quote_asset,quote_fee_rate=model.quote_fee_rate,model=model,accepted_clock=clock)


def submit_quote_creation(engine,write,request,permit,*,boundary,lease,owner):
    """Compatibility helper; authority remains the public single-consume adapter."""
    require('quoteOrderQty' in dict(request.form_parameters()),'PAPER_QUOTE_REQUEST_REQUIRED')
    return engine.submit_creation(write,request,permit,boundary=boundary,lease=lease,owner=owner)


def _submit_consumed_quote(engine,original,request,permit,*,boundary,lease,owner,consumption,assessment):
    with owner.guard(lease):
        clock=_consumed_permit_clock(engine,permit,created_at=original.created_at)
        terms=build_quote_terms(engine,original,request,consumption,assessment,clock)
        with engine._connect() as db:
            key=market_key(engine.scope.product,engine.scope.instrument_uid)
            db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
            with owner.guard(lease):
                _current_consumed_owner(boundary,original,lease,consumption,assessment,permit)
                clock=_consumed_permit_clock(engine,permit,after=clock,created_at=original.created_at)
            terms=replace(terms,accepted_clock=clock)
            book=db.execute('SELECT occurred_at,observed_clock FROM public.paper_market_books WHERE market_key=%s',(key,)).fetchone()
            require(book is not None,'PAPER_CURRENT_DEPTH_REQUIRED')
            observed=decode(t.ClockReading,book['observed_clock'])
            require(observed.clock_health=='HEALTHY' and utc_timestamp(observed.utc_now)<=utc_timestamp(clock.utc_now)
                and book['occurred_at']<=utc_timestamp(clock.utc_now),'PAPER_MARKET_CLOCK_AHEAD_OF_ACCEPTANCE')
            db.execute('SELECT public.paper_accept_quote_v1(%s,%s,%s,%s,%s)',
                (engine._scope_key,original.command_id,terms.client_id,key,Jsonb(domain_payload(terms))))
            with owner.guard(lease):
                _current_consumed_owner(boundary,original,lease,consumption,assessment,permit)
                _consumed_permit_clock(engine,permit,after=clock,created_at=original.created_at)
    report=query_quote_creation(engine,original.command_id)
    return PaperCreationReceipt(command_id=original.command_id,request_hash=digest(request),scope=engine.scope,
        namespace=request.namespace.value,client_ids=(terms.client_id,),status=report.status,evidence=report.evidence)


@exact_market_arithmetic
def query_quote_creation(engine,command_id):
    with engine._connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        # Additive optional schema: historical BASE deployments need no migration.
        if db.execute("SELECT to_regclass('public.paper_quote_terms_v1') AS relation").fetchone()['relation'] is None:return None
        row=db.execute('SELECT t.terms,t.client_id AS source_client_id,t.market_key AS source_market_key,r.* FROM public.paper_quote_terms_v1 t JOIN public.paper_quote_runtime_v1 r '
            'USING(scope_key,command_id) WHERE scope_key=%s AND command_id=%s',(engine._scope_key,command_id)).fetchone()
        if row is None:return None
        terms=decode(PaperQuoteTermsV1,row['terms'])
        require(terms.scope==engine.scope and terms.write.command_id==command_id
            and terms.client_id==row['source_client_id']
            and row['source_market_key']==market_key(engine.scope.product,engine.scope.instrument_uid)
            and digest(terms.request)==terms.write.request_hash,'PAPER_QUOTE_SOURCE_BINDING_CHANGED')
        rows=db.execute('SELECT a.*,i.payload_hash FROM public.paper_quote_allocations_v1 a JOIN public.paper_market_inputs i '
            'USING(market_key,event_id) WHERE a.scope_key=%s AND a.command_id=%s ORDER BY a.fill_id LIMIT 8193',
            (engine._scope_key,command_id)).fetchall()
        require(len(rows)<=8192,'PAPER_QUOTE_FILL_BOUND_EXCEEDED')
        fills=tuple(PaperQuoteFillV1(fill_id=r['fill_id'],quantity=r['quantity'],price=r['price'],gross_quote=r['quantity']*r['price'],
            quote_fee=r['fee'],fee_debit='EXTERNAL_BALANCE',quantity_unit='BASE',quote_asset=terms.quote_asset,market_event_id=r['event_id'],market_hash=r['payload_hash']) for r in rows)
        payload=dict(record_kind='PAPER_QUOTE_REPORT_V1',command_id=command_id,request=terms.request,terms=terms,status=row['status'],
            reason=row['reason'],actual_base=row['actual_base'],gross_quote=row['gross_quote'],remaining_quote=row['remaining_quote'],fills=fills)
        return PaperQuoteReportV1(**payload,evidence=_evidence('paper-quote:'+command_id,
            {k:domain_payload(v) if hasattr(v,'__dataclass_fields__') else [domain_payload(f) for f in v] if k=='fills' else str(v) if isinstance(v,Decimal) else v for k,v in payload.items()},terms.accepted_clock.utc_now))
