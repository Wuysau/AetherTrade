"""Source-backed quote facts, with no registry or posting authority."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from trading_system.contracts.canonical import canonical_hash
from trading_system.storage.models import scope_id
from trading_system.intents.quote_assessment_archive import QuoteAssessmentArchiveCodec
from trading_system.engine.paper_market import require,digest,number,exact_market_arithmetic
from trading_system.engine.paper_market_quote import PaperQuoteReportV1,query_quote_creation,build_quote_terms


@dataclass(frozen=True,slots=True,kw_only=True)
class VerifiedPaperQuoteReportV1:
    record_kind:Literal['VERIFIED_PAPER_QUOTE_REPORT_V1']
    report:PaperQuoteReportV1
    consumption_context_hash:str
    discrepancies:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False


@exact_market_arithmetic
def verify_quote_report(engine,report,*,boundary):
    from trading_system.engine.paper import PaperEngine
    from trading_system.execution.program_commands import ProgramCommandBoundary
    require(type(engine) is PaperEngine and type(boundary) is ProgramCommandBoundary
        and type(report) is PaperQuoteReportV1 and engine.scope==boundary.scope,'PAPER_QUOTE_VERIFIER_COMPOSITION_REQUIRED')
    actual=query_quote_creation(engine,report.command_id)
    require(actual is not None and digest(actual)==digest(report),'PAPER_QUOTE_REPORT_SOURCE_CHANGED')
    with boundary.uow.transaction(engine.scope) as tx:
        row=tx.execute('SELECT context FROM ledger.program_permit_consumptions WHERE scope_id=%s AND command_id=%s',
            (scope_id(engine.scope),report.command_id)).fetchone()
        require(row is not None,'PAPER_PROTECTED_CONSUMPTION_REQUIRED')
        context=row[0]
    archive=QuoteAssessmentArchiveCodec().decode(context['assessment']);terms=report.terms
    require(canonical_hash(context)==terms.consumption_context_hash
        and canonical_hash(context['write'])==digest(terms.write)
        and context['request_hash']==digest(report.request)
        and context['assessment_hash']==digest(archive),'PAPER_QUOTE_PROTECTED_SOURCE_CHANGED')
    expected=build_quote_terms(engine,terms.write,report.request,context,archive,terms.accepted_clock)
    require(digest(expected)==digest(terms),'PAPER_QUOTE_IMMUTABLE_TERMS_CHANGED')
    base=sum((f.quantity for f in report.fills),Decimal(0));gross=sum((f.quantity*f.price for f in report.fills),Decimal(0))
    require(base==report.actual_base and gross==report.gross_quote
        and number(terms.requested_quote)-gross==report.remaining_quote,'PAPER_QUOTE_RUNTIME_CHANGED')
    require(report.status==('FILLED' if report.remaining_quote==0 else 'EXPIRED'),'PAPER_QUOTE_TERMINAL_CHANGED')
    problems=[]
    for fill in report.fills:
        require(fill.quantity>0 and fill.quantity%number(terms.quantity_step)==0
            and fill.fee_debit=='EXTERNAL_BALANCE' and fill.quantity_unit=='BASE' and fill.quote_asset==terms.quote_asset
            and fill.gross_quote==fill.quantity*fill.price
            and fill.quote_fee==fill.gross_quote*number(terms.quote_fee_rate),'PAPER_QUOTE_FILL_ECONOMICS_CHANGED')
        if not number(terms.minimum_price)<=fill.price<=number(terms.maximum_price):problems.append('EXECUTION_PRICE_OUTSIDE_ENVELOPE')
    if gross>number(terms.requested_quote):problems.append('GROSS_QUOTE_EXCEEDED_REQUEST')
    # Re-read immutable market sources and aggregate both allocator families.
    # A quote fill must consume real opposing capacity, never a supplied DTO.
    with engine._connect() as db:
        for event_id in {f.market_event_id for f in report.fills}:
            from trading_system.engine.paper_market import market_key
            key=market_key(engine.scope.product,engine.scope.instrument_uid)
            source=db.execute('SELECT * FROM public.paper_market_inputs WHERE market_key=%s AND event_id=%s',
                (key,event_id)).fetchone()
            require(source is not None and canonical_hash(source['payload'])==source['payload_hash'],
                'PAPER_QUOTE_MARKET_SOURCE_CHANGED')
            event=source['payload'];opposite='ASK' if terms.side=='BUY' else 'BID'
            levels=event['asks' if opposite=='ASK' else 'bids']
            before={number(v['price']):number(v['available']) for v in source['before_levels'] if v['side']==opposite}
            changes={number(v['price']):number(v['quantity'],signed=True) for v in levels}
            for fill in (f for f in report.fills if f.market_event_id==event_id):
                require(fill.market_hash==source['payload_hash'],'PAPER_QUOTE_FILL_SOURCE_HASH_CHANGED')
                previous=before.get(fill.price,Decimal(0));change=changes.get(fill.price,Decimal(0))
                capacity=(min(previous,change) if fill.price in before else change) if event['kind']=='DEPTH_SNAPSHOT' else max(Decimal(0),previous+change)
                allocated=db.execute('SELECT coalesce(sum(quantity),0) AS quantity FROM ('
                    'SELECT quantity FROM public.paper_market_consumptions WHERE market_key=%s AND event_id=%s AND side=%s AND price=%s '
                    'UNION ALL SELECT quantity FROM public.paper_quote_allocations_v1 WHERE market_key=%s AND event_id=%s AND side=%s AND price=%s) a',
                    (key,event_id,opposite,fill.price,key,event_id,opposite,fill.price)).fetchone()['quantity']
                require(allocated<=capacity,'PAPER_QUOTE_SHARED_LIQUIDITY_EXCEEDED')
    # Do not cap reported fills to make an envelope pass.
    return VerifiedPaperQuoteReportV1(record_kind='VERIFIED_PAPER_QUOTE_REPORT_V1',report=report,
        consumption_context_hash=canonical_hash(context),discrepancies=tuple(sorted(set(problems))))
