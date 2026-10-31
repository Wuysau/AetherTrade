"""Fixed public aggregate-trade GET identities. Construction performs no I/O.

Time windows are half-open locally; Binance endTime is inclusive. Each time
request is bounded to at most one hour as local workload policy. Longer scans
must use explicit requests; cursor and time filters are never silently mixed.
No key, account identity, arbitrary URL or trading endpoint is accepted.
"""
from dataclasses import dataclass
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_program import identifier


def need(condition):
    if not condition:raise ValueError('PUBLIC_AGGREGATE_REQUEST_INVALID')


def integer(value):return type(value) is int and 0<=value<2**63


@dataclass(frozen=True,slots=True,kw_only=True)
class AggregateTradeRequest:
    product:t.Product
    instrument_uid:str
    venue_symbol:str
    native_unit:str
    start_ms:int|None
    end_ms:int|None
    from_id:int|None
    limit:int
    contract_version:Literal['2026-10-31']='2026-10-31'
    record_kind:Literal['PUBLIC_AGGREGATE_REQUEST_V1']='PUBLIC_AGGREGATE_REQUEST_V1'

    def __post_init__(self):
        need(self.contract_version=='2026-10-31' and self.record_kind=='PUBLIC_AGGREGATE_REQUEST_V1')
        need(type(self.product) is t.Product and self.product in (t.Product.SPOT,t.Product.USD_M_PERP))
        for value in (self.instrument_uid,self.venue_symbol,self.native_unit):identifier(value)
        need(type(self.limit) is int and 1<=self.limit<=1000)
        if self.from_id is None:
            need(integer(self.start_ms) and integer(self.end_ms)
                and 0<self.end_ms-self.start_ms<=3_600_000)
        else:
            need(integer(self.from_id) and self.start_ms is None and self.end_ms is None)

    @property
    def origin(self):
        return 'https://data-api.binance.vision' if self.product==t.Product.SPOT else 'https://fapi.binance.com'

    @property
    def path(self):return '/api/v3/aggTrades' if self.product==t.Product.SPOT else '/fapi/v1/aggTrades'

    @property
    def method(self)->Literal['GET']:return 'GET'

    @property
    def parameters(self):
        query=(('symbol',self.venue_symbol),('limit',self.limit))
        if self.from_id is not None:return (*query,('fromId',self.from_id))
        return (*query,('startTime',self.start_ms),('endTime',self.end_ms-1))

    @property
    def identity_hash(self):
        self.__post_init__()
        return canonical_hash(dict(request=domain_payload(self),origin=self.origin,path=self.path,
            method=self.method,parameters=dict(self.parameters)))

    @property
    def execution_authorized(self)->Literal[False]:return False
