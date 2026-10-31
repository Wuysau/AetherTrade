from dataclasses import replace
import pytest
from trading_system.contracts import types as t
from trading_system.market.aggregate_requests import AggregateTradeRequest


def request(**changes):
    return AggregateTradeRequest(**{**dict(product=t.Product.SPOT,instrument_uid='BTCUSDT',venue_symbol='BTCUSDT',
        native_unit='BTC',start_ms=1000,end_ms=2000,from_id=None,limit=1000),**changes})

@pytest.mark.parametrize('product,origin,path',[(t.Product.SPOT,'https://data-api.binance.vision','/api/v3/aggTrades'),
    (t.Product.USD_M_PERP,'https://fapi.binance.com','/fapi/v1/aggTrades')])
def test_public_endpoint_and_half_open_window_are_explicit(product,origin,path):
    r=request(product=product)
    assert r.origin==origin and r.path==path and r.method=='GET'
    assert dict(r.parameters)==dict(symbol='BTCUSDT',startTime=1000,endTime=1999,limit=1000)
    assert not r.execution_authorized


def test_cursor_request_cannot_carry_a_silent_time_filter():
    r=request(start_ms=None,end_ms=None,from_id=7)
    assert dict(r.parameters)==dict(symbol='BTCUSDT',fromId=7,limit=1000)
    assert r.identity_hash!=request().identity_hash

@pytest.mark.parametrize('change',[{'from_id':7},{'start_ms':None},{'end_ms':1000},
    {'start_ms':True},{'limit':True},{'limit':1001},{'limit':0},{'from_id':-1,'start_ms':None,'end_ms':None},
    {'product':t.Product.US_EQUITY},{'end_ms':3601001}])
def test_ambiguous_or_unbounded_request_rejected(change):
    with pytest.raises(ValueError):request(**change)
