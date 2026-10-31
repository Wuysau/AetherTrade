import json
from decimal import Decimal as D,localcontext
import pytest
from trading_system.contracts import types as t
from trading_system.market.aggregate_trades import merge_aggregate_trade_pages


def row(i=1,first=10,last=10,time=1000,q='1.25',nq='1.00'):
    return dict(a=i,p='100',q=q,nq=nq,f=first,l=last,T=time,m=True)

def merge(pages,**changes):
    return merge_aggregate_trade_pages(tuple(json.dumps(p).encode() for p in pages),
        product=t.Product.USD_M_PERP,instrument_uid='BTCUSDT',native_unit='BTC',
        start_ms=1000,end_ms=2000,**{'quantity_basis':'TOTAL',**changes})


def test_overlapping_pages_and_identical_replays_do_not_double_count():
    a=row();b=row(2,11,11,q='2.25',nq='2.00')
    with localcontext() as ctx:
        ctx.prec=1
        result=merge([[a],[a,b],[a]])
    assert D(result.observed_quantity)==D('3.50') and result.trade_count==2
    assert len(result.raw_hashes)==2 and not result.coverage_complete and not result.source_verified
    assert not result.execution_authorized
    assert D(merge([[a,b]],quantity_basis='NON_RPI').observed_quantity)==3


def test_half_open_window_keeps_adjacent_windows_disjoint():
    result=merge([[row(time=999),row(2,11,11,time=1000),row(3,12,12,time=2000)]])
    assert result.trade_count==1 and D(result.observed_quantity)==D('1.25')

@pytest.mark.parametrize('fault',['changed_id','overlapping_trade_range','missing_nq','unknown_basis'])
def test_conflicting_or_undefined_volume_is_rejected(fault):
    a=row();b=row(2,11,11)
    if fault=='changed_id':b={**a,'q':'2'}
    if fault=='overlapping_trade_range':b=row(2,10,11)
    if fault=='missing_nq':b.pop('nq')
    with pytest.raises(ValueError):merge([[a],[b]],quantity_basis='bad' if fault=='unknown_basis' else 'NON_RPI')


def test_empty_page_is_observed_zero_without_proving_complete_scan():
    result=merge([[]])
    assert D(result.observed_quantity)==0 and not result.coverage_complete
    with pytest.raises(ValueError):merge([])
