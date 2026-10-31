import json
from hashlib import sha256
import pytest
from trading_system.contracts import types as t
from trading_system.market.aggregate_trades import parse_aggregate_trade_page


def raw(**changes):
    return json.dumps([dict(a=7,p='100',q='2.50',f=10,l=12,T=1793404800000,m=True,**changes)]).encode()

def test_futures_total_and_non_rpi_quantities_remain_separate():
    payload=raw(nq='2.00')
    page=parse_aggregate_trade_page(payload,product=t.Product.USD_M_PERP,instrument_uid='BTCUSDT',native_unit='BTC')
    assert page.raw_hash==sha256(payload).hexdigest()
    assert page.rows[0].quantity=='2.50' and page.rows[0].non_rpi_quantity=='2.00'
    assert not page.source_verified and not page.coverage_complete and not page.execution_authorized

def test_spot_does_not_invent_a_non_rpi_quantity():
    page=parse_aggregate_trade_page(raw(M=True),product=t.Product.SPOT,instrument_uid='BTCUSDT',native_unit='BTC')
    assert page.rows[0].non_rpi_quantity is None and page.rows[0].first_trade_id==10

@pytest.mark.parametrize('fault',['duplicate_key','boolean_id','negative_quantity','nonfinite','reversed_ids','duplicate_trade','rpi_over_total'])
def test_ambiguous_public_trade_pages_are_rejected(fault):
    row=json.loads(raw())[0]
    if fault=='boolean_id':row['a']=True
    if fault=='negative_quantity':row['q']='-1'
    if fault=='nonfinite':row['q']='NaN'
    if fault=='reversed_ids':row['f']=13
    if fault=='rpi_over_total':row['nq']='3'
    payload=json.dumps([row,row] if fault=='duplicate_trade' else [row]).encode()
    if fault=='duplicate_key':payload=b'[{"a":7,"a":8}]'
    with pytest.raises(ValueError):parse_aggregate_trade_page(payload,product=t.Product.USD_M_PERP,instrument_uid='BTCUSDT',native_unit='BTC')
