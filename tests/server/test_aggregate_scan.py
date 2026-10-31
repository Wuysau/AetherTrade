import asyncio,json
import httpx
import pytest
from server.test_market_transport import dns
from server.test_aggregate_market import STAMP
from intents.test_aggregate_trade_request import request
from trading_system.market.aggregate_archive import AggregateTradeArchive
from trading_system.market.snapshots import LocalSnapshotBackend
from trading_system.server.market_transport import MarketTransport
from trading_system.server.aggregate_market import AggregateMarketReader


def row(i,time):return dict(a=i,p='100',q='1',f=i,l=i,T=time,m=True)

@pytest.mark.parametrize('mode,expected,count',[
    ('normal','PAGE_BOUNDARY_OBSERVED',2),('budget','BUDGET_EXHAUSTED',1),
    ('repeat','INVALID_PAGE',2),('foreign_time','INVALID_PAGE',1),('invalid_json','INVALID_PAGE',1)])
def test_bounded_scan_preserves_request_chain_and_never_claims_qualification(tmp_path,monkeypatch,mode,expected,count):
    async def run():
        dns(monkeypatch,['93.184.216.34']);calls=[]
        def handler(outgoing):
            calls.append(dict(outgoing.url.params))
            if mode=='invalid_json':return httpx.Response(200,content=b'bad')
            if len(calls)==1:
                assert 'startTime' in calls[-1] and 'fromId' not in calls[-1]
                rows=[row(1,999 if mode=='foreign_time' else 1000),row(2,1100)]
            else:
                assert calls[-1]['fromId']=='3' and 'startTime' not in calls[-1]
                rows=[row(1,1000),row(2,1100)] if mode=='repeat' else [row(3,1200)]
            return httpx.Response(200,content=json.dumps(rows).encode())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader=AggregateMarketReader(MarketTransport(client=client),AggregateTradeArchive(LocalSnapshotBackend(tmp_path)),clock=lambda:STAMP)
            result=await reader.scan(request(limit=2),max_pages=1 if mode=='budget' else 3,timeout_seconds=5,quantity_basis='TOTAL')
            assert result.status==expected and len(result.observations)==count
            assert not result.source_verified and not result.coverage_complete and not result.execution_authorized
            if mode=='normal':assert result.window.observed_quantity=='3' and result.window.trade_count==3
            if expected=='INVALID_PAGE':assert result.window is None
        assert len(calls)==count
    asyncio.run(run())


def test_scan_budget_is_validated_before_io(tmp_path):
    async def run():
        def forbidden(_):raise AssertionError('invalid budget reached network')
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            reader=AggregateMarketReader(MarketTransport(client=client),AggregateTradeArchive(LocalSnapshotBackend(tmp_path)),clock=lambda:STAMP)
            for pages in (0,True,17):
                with pytest.raises(ValueError):await reader.scan(request(),max_pages=pages,timeout_seconds=5,quantity_basis='TOTAL')
    asyncio.run(run())



def test_scan_deadline_cancels_wait_without_retry_or_false_boundary(tmp_path,monkeypatch):
    from time import monotonic
    async def run():
        dns(monkeypatch,['93.184.216.34']);calls=[]
        async def handler(outgoing):
            calls.append(outgoing)
            await asyncio.Future()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader=AggregateMarketReader(MarketTransport(client=client),AggregateTradeArchive(LocalSnapshotBackend(tmp_path)),clock=lambda:STAMP)
            start=monotonic()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(reader.scan(request(),max_pages=2,timeout_seconds=1,quantity_basis='TOTAL'),timeout=3)
            assert monotonic()-start<2 and len(calls)==1
            assert list(tmp_path.iterdir())==[]
    asyncio.run(run())
