"""Explicit public aggregate-read composition; never auto-installed or scheduled.

Uses the existing guarded market transport and snapshot-backed archive. Caller
supplies a trusted clock and source metadata. One call reads one page without
retry; transport success is not complete-window or trading qualification.
"""
from dataclasses import dataclass,replace
from typing import Literal
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts import types as t
from trading_system.market.aggregate_requests import AggregateTradeRequest
from trading_system.market.aggregate_archive import AggregateTradeArchive,AggregateArchiveReceipt
from trading_system.market.aggregate_trades import AggregateTradePage
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
