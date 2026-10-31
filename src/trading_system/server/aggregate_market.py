"""Explicit public aggregate-read composition; never auto-installed or scheduled.

Uses the existing guarded market transport and snapshot-backed archive. Caller
supplies a trusted clock and source metadata. One call reads one page without
retry; transport success is not complete-window or trading qualification.
"""
import asyncio
from dataclasses import dataclass,replace
from typing import Literal
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts import types as t
from trading_system.market.aggregate_requests import AggregateTradeRequest
from trading_system.market.aggregate_archive import AggregateTradeArchive,AggregateArchiveReceipt
from trading_system.market.aggregate_trades import AggregateTradePage,AggregateVolumeWindow,merge_aggregate_trade_pages
from .market_transport import MarketTransport


@dataclass(frozen=True,slots=True,kw_only=True)
class AggregatePageObservation:
    request:AggregateTradeRequest
    receipt:AggregateArchiveReceipt
    page:AggregateTradePage|None
    status:Literal['ARCHIVED_PAGE','INVALID_PAGE']

    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def coverage_complete(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class AggregateScanResult:
    status:Literal['PAGE_BOUNDARY_OBSERVED','BUDGET_EXHAUSTED','INVALID_PAGE']
    observations:tuple[AggregatePageObservation,...]
    window:AggregateVolumeWindow|None
    reason:str|None

    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def coverage_complete(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False


class AggregateMarketReader:
    def __init__(self,transport,archive,*,clock):
        if type(transport) is not MarketTransport or type(archive) is not AggregateTradeArchive or not callable(clock):
            raise ValueError('PUBLIC_AGGREGATE_READER_DEPENDENCIES_REQUIRED')
        self.transport,self.archive,self.clock=transport,archive,clock

    async def observe(self,request):
        if type(request) is not AggregateTradeRequest:raise ValueError('PUBLIC_AGGREGATE_REQUEST_REQUIRED')
        # Snapshot scalar request values before await; the receipt binds the
        # exact parameters sent even if the caller subsequently replaces its view.
        saved=replace(request)
        started=utc_timestamp(self.clock())
        config=dict(provider='binance',rest_url=saved.origin,local_mode=False,
            ws_url='wss://data-stream.binance.vision/stream' if saved.product==t.Product.SPOT else None,
            api_key=None,has_api_key=False,encrypted_key=None,auth_scheme='none',timeout_seconds=8)
        raw=await self.transport.get_bytes(config,saved.path,dict(saved.parameters))
        received_at=self.clock()
        if utc_timestamp(received_at)<started:raise ValueError('PUBLIC_AGGREGATE_CLOCK_REGRESSED')
        receipt=self.archive.record(saved,raw,received_at=received_at)
        try:page=self.archive.read_page(saved,receipt)
        except (ValueError,TypeError,UnicodeError,RecursionError):
            return AggregatePageObservation(request=saved,receipt=receipt,page=None,status='INVALID_PAGE')
        return AggregatePageObservation(request=saved,receipt=receipt,page=page,status='ARCHIVED_PAGE')


    async def scan(self,request,*,max_pages,timeout_seconds,quantity_basis):
        """Finite request chain; a short page is not authenticated completeness."""
        if (type(request) is not AggregateTradeRequest or request.from_id is not None
                or type(max_pages) is not int or not 1<=max_pages<=16
                or type(timeout_seconds) is not int or not 1<=timeout_seconds<=120
                or quantity_basis not in ('TOTAL','NON_RPI')
                or quantity_basis=='NON_RPI' and request.product!=t.Product.USD_M_PERP):
            raise ValueError('PUBLIC_AGGREGATE_SCAN_BUDGET_OR_WINDOW_INVALID')
        initial=replace(request);current=initial;observations=[];payloads=[];last_time=None
        deadline=asyncio.get_running_loop().time()+timeout_seconds
        def finish(status,reason=None):
            window=None
            if status!='INVALID_PAGE':
                try:
                    window=merge_aggregate_trade_pages(tuple(payloads),product=initial.product,
                        instrument_uid=initial.instrument_uid,native_unit=initial.native_unit,
                        start_ms=initial.start_ms,end_ms=initial.end_ms,quantity_basis=quantity_basis)
                except ValueError:
                    status,reason='INVALID_PAGE','PUBLIC_AGGREGATE_WINDOW_CONFLICT'
            if asyncio.get_running_loop().time()>=deadline:raise TimeoutError('aggregate scan deadline expired')
            return AggregateScanResult(status=status,observations=tuple(observations),window=window,reason=reason)
        async with asyncio.timeout(timeout_seconds):
            for _ in range(max_pages):
                if asyncio.get_running_loop().time()>=deadline:raise TimeoutError('aggregate scan deadline expired')
                observation=await self.observe(current);observations.append(observation)
                if asyncio.get_running_loop().time()>=deadline:raise TimeoutError('aggregate scan deadline expired')
                if observation.status!='ARCHIVED_PAGE':return finish('INVALID_PAGE','PUBLIC_AGGREGATE_PAGE_INVALID')
                rows=observation.page.rows
                if len(rows)>current.limit:return finish('INVALID_PAGE','PUBLIC_AGGREGATE_PAGE_LIMIT_CHANGED')
                if (any(a.aggregate_id>=b.aggregate_id or a.timestamp_ms>b.timestamp_ms for a,b in zip(rows,rows[1:]))
                        or rows and last_time is not None and rows[0].timestamp_ms<last_time):
                    return finish('INVALID_PAGE','PUBLIC_AGGREGATE_PAGE_ORDER_INVALID')
                if current.from_id is None:
                    if any(not initial.start_ms<=r.timestamp_ms<initial.end_ms for r in rows):
                        return finish('INVALID_PAGE','PUBLIC_AGGREGATE_TIME_WINDOW_CHANGED')
                elif rows and rows[0].aggregate_id<current.from_id:
                    return finish('INVALID_PAGE','PUBLIC_AGGREGATE_CURSOR_NOT_ADVANCED')
                payloads.append(self.archive.read_payload(observation.request,observation.receipt))
                if not rows or len(rows)<current.limit or rows[-1].timestamp_ms>=initial.end_ms:
                    return finish('PAGE_BOUNDARY_OBSERVED')
                last_time=rows[-1].timestamp_ms
                if rows[-1].aggregate_id==2**63-1:return finish('INVALID_PAGE','PUBLIC_AGGREGATE_CURSOR_EXHAUSTED')
                current=replace(initial,start_ms=None,end_ms=None,from_id=rows[-1].aggregate_id+1)
        return finish('BUDGET_EXHAUSTED','PUBLIC_AGGREGATE_PAGE_BUDGET_EXHAUSTED')
