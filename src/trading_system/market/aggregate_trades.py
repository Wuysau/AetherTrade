"""Bounded public aggregate-trade candidates, never fills or complete volume.

Caller must archive original bytes and independently bind request, product,
instrument, native quantity unit, pagination and observation window. q and nq
are distinct quantities; no implicit RPI participation policy is selected.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.order_program import amount,identifier


def need(value):
    if not value:raise ValueError('PUBLIC_AGGREGATE_TRADE_INVALID')


def _pairs(pairs):
    result={}
    for key,value in pairs:
        need(key not in result);result[key]=value
    return result


def _constant(value):raise ValueError('PUBLIC_AGGREGATE_TRADE_NONFINITE')


@dataclass(frozen=True,slots=True,kw_only=True)
class AggregateTradeCandidate:
    aggregate_id:int
    first_trade_id:int
    last_trade_id:int
    timestamp_ms:int
    price:str
    quantity:str
    non_rpi_quantity:str|None
    buyer_is_maker:bool


@dataclass(frozen=True,slots=True,kw_only=True)
class AggregateTradePage:
    product:t.Product
    instrument_uid:str
    native_unit:str
    raw_hash:str
    rows:tuple[AggregateTradeCandidate,...]

    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def coverage_complete(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False


def parse_aggregate_trade_page(payload,*,product,instrument_uid,native_unit):
    need(type(product) is t.Product and product in (t.Product.SPOT,t.Product.USD_M_PERP))
    identifier(instrument_uid);identifier(native_unit)
    need(type(payload) is bytes and len(payload)<=1_048_576)
    body=json.loads(payload,object_pairs_hook=_pairs,parse_constant=_constant)
    need(type(body) is list and len(body)<=1000)
    rows=[];seen=set()
    for row in body:
        need(type(row) is dict and all(type(row.get(k)) is int and 0<=row[k]<2**63 for k in ('a','f','l','T')))
        need(row['a'] not in seen and row['f']<=row['l'] and type(row.get('m')) is bool)
        need('M' not in row or type(row['M']) is bool)
        amount(row.get('p'));quantity=amount(row.get('q'))
        non_rpi=row.get('nq')
        if 'nq' in row:
            need(product==t.Product.USD_M_PERP and amount(non_rpi,zero=True)<=quantity)
        rows.append(AggregateTradeCandidate(aggregate_id=row['a'],first_trade_id=row['f'],last_trade_id=row['l'],
            timestamp_ms=row['T'],price=row['p'],quantity=row['q'],non_rpi_quantity=non_rpi,buyer_is_maker=row['m']))
        seen.add(row['a'])
    return AggregateTradePage(product=product,instrument_uid=instrument_uid,native_unit=native_unit,
        raw_hash=sha256(payload).hexdigest(),rows=tuple(rows))


@dataclass(frozen=True,slots=True,kw_only=True)
class AggregateVolumeWindow:
    product:t.Product
    instrument_uid:str
    native_unit:str
    start_ms:int
    end_ms:int
    quantity_basis:Literal['TOTAL','NON_RPI']
    observed_quantity:str
    trade_count:int
    raw_hashes:tuple[str,...]

    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def coverage_complete(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False


def merge_aggregate_trade_pages(payloads,*,product,instrument_uid,native_unit,start_ms,end_ms,quantity_basis):
    """Merge observed aggregates in [start,end), without a completeness claim.

    Native aggregate IDs identify rows; f/l are not enumerated member sets.
    HTTP request/source authenticity, pagination and edge coverage remain host
    obligations. Missing or short pages never establish a complete volume scan.
    """
    from decimal import Decimal,localcontext,Inexact,Rounded
    from fractions import Fraction
    need(type(payloads) is tuple and 0<len(payloads)<=4096
        and all(type(p) is bytes for p in payloads) and sum(map(len,payloads))<=16_777_216)
    need(type(start_ms) is int and type(end_ms) is int and 0<=start_ms<end_ms<2**63)
    need(quantity_basis in ('TOTAL','NON_RPI') and (quantity_basis!='NON_RPI' or product==t.Product.USD_M_PERP))
    records={};hashes=[];count=0
    for payload in payloads:
        page=parse_aggregate_trade_page(payload,product=product,instrument_uid=instrument_uid,native_unit=native_unit)
        if page.raw_hash in hashes:continue
        hashes.append(page.raw_hash);count+=len(page.rows);need(count<=100000)
        for row in page.rows:
            old=records.get(row.aggregate_id)
            need(old is None or old==row)
            records[row.aggregate_id]=row
    selected=[row for row in records.values() if start_ms<=row.timestamp_ms<end_ms]
    total=Fraction(0)
    for row in selected:
        value=row.quantity if quantity_basis=='TOTAL' else row.non_rpi_quantity
        need(value is not None)
        total+=Fraction(amount(value,zero=True))
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        quantity=format(Decimal(total.numerator)/Decimal(total.denominator),'f')
    return AggregateVolumeWindow(product=product,instrument_uid=instrument_uid,native_unit=native_unit,
        start_ms=start_ms,end_ms=end_ms,quantity_basis=quantity_basis,observed_quantity=quantity,
        trade_count=len(selected),raw_hashes=tuple(hashes))
