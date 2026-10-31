import asyncio
from dataclasses import replace
import httpx
import pytest
from server.test_market_transport import dns
from intents.test_aggregate_trade_request import request
from trading_system.contracts import types as t
from trading_system.market.aggregate_archive import AggregateTradeArchive
from trading_system.market.snapshots import LocalSnapshotBackend
from trading_system.server.market_transport import MarketTransport
from trading_system.server.aggregate_market import AggregateMarketReader

STAMP='2026-10-31T00:00:00Z'
BODY=b'[{"a":1,"p":"100","q":"0.1000","f":1,"l":1,"T":1000,"m":true}]'

@pytest.mark.parametrize('product',[t.Product.SPOT,t.Product.USD_M_PERP])
def test_reader_binds_fixed_request_and_archives_without_inherited_credentials(tmp_path,monkeypatch,product):
    async def run():
        dns(monkeypatch,['93.184.216.34']);original=request(product=product);calls=[]
        def handler(outgoing):
            calls.append(outgoing)
            assert outgoing.method=='GET' and outgoing.url.path==original.path
            assert outgoing.url.params['endTime']=='1999'
            assert 'authorization' not in outgoing.headers and 'cookie' not in outgoing.headers
            assert 'x-inherited' not in outgoing.headers
            return httpx.Response(200,content=BODY)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),auth=('fake','secret'),headers={'x-inherited':'secret'}) as client:
            archive=AggregateTradeArchive(LocalSnapshotBackend(tmp_path))
            reader=AggregateMarketReader(MarketTransport(client=client),archive,clock=lambda:STAMP)
            result=await reader.observe(original)
            assert result.status=='ARCHIVED_PAGE' and result.request==original
            assert archive.read_payload(original,result.receipt)==BODY
            assert not result.coverage_complete and not result.execution_authorized
        assert len(calls)==1
    asyncio.run(run())


def test_bad_page_retains_original_bytes_without_becoming_empty_volume(tmp_path,monkeypatch):
    async def run():
        dns(monkeypatch,['93.184.216.34'])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'bad-json'))) as client:
            archive=AggregateTradeArchive(LocalSnapshotBackend(tmp_path))
            result=await AggregateMarketReader(MarketTransport(client=client),archive,clock=lambda:STAMP).observe(request())
            assert result.status=='INVALID_PAGE' and result.page is None
            assert archive.read_payload(result.request,result.receipt)==b'bad-json'
    asyncio.run(run())


def test_archive_failure_propagates_without_retry(tmp_path,monkeypatch):
    async def run():
        dns(monkeypatch,['93.184.216.34']);calls=[]
        def handler(outgoing):calls.append(outgoing);return httpx.Response(200,content=BODY)
        backend=LocalSnapshotBackend(tmp_path)
        def fail(*args):raise OSError('archive failure')
        monkeypatch.setattr(backend,'stage',fail)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader=AggregateMarketReader(MarketTransport(client=client),AggregateTradeArchive(backend),clock=lambda:STAMP)
            with pytest.raises(OSError):await reader.observe(request())
        assert len(calls)==1
    asyncio.run(run())
