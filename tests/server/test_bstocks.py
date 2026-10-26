"""Independent Binance crypto/bStock batches, with no live network."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from trading_system.server import market
from trading_system.server.market import MarketFeed


NOW = datetime(2026, 10, 26, 3, tzinfo=timezone.utc)
CRYPTO = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
STOCKS = ("TSLABUSDT", "NVDABUSDT", "AAPLBUSDT")


def payload(symbols, age=1):
    return [{"symbol": s, "lastPrice": "123.45", "priceChangePercent": "1.2",
             "closeTime": int((NOW - timedelta(seconds=age)).timestamp() * 1000)} for s in symbols]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(market, "_utc_now", lambda: NOW)


def test_explicit_detached_catalog():
    from trading_system.server.instruments import ALL_SYMBOLS, instruments
    catalog = instruments()
    assert ALL_SYMBOLS == CRYPTO + STOCKS
    assert [i["asset_class"] for i in catalog] == ["CRYPTO"] * 3 + ["BSTOCK"] * 3
    assert [i["underlying_symbol"] for i in catalog] == [None] * 3 + ["TSLA", "NVDA", "AAPL"]
    assert [i["name"] for i in catalog[3:]] == ["Tesla", "NVIDIA", "Apple"]
    assert all(i["quote_asset"] == "USDT" for i in catalog)
    catalog[0]["name"] = "changed"
    assert instruments()[0]["name"] != "changed"


def test_six_fresh_quotes_and_fixed_separate_requests():
    seen = []
    def handler(request):
        symbols = tuple(json.loads(request.url.params["symbols"]))
        seen.append(symbols)
        assert request.url.host == "data-api.binance.vision"
        assert request.url.path == "/api/v3/ticker/24hr"
        return httpx.Response(200, json=payload(symbols))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            feed = MarketFeed(client=client, include_bstocks=True)
            assert feed.snapshot()["status"] == "UNAVAILABLE"
            result = await feed.refresh()
            assert result["status"] == "CONNECTED"
            assert tuple(q["symbol"] for q in result["quotes"]) == CRYPTO + STOCKS
            assert all(not q["stale"] for q in result["quotes"])
            assert [g["id"] for g in result["groups"]] == ["crypto", "bstocks"]
            assert all(g["status"] == "CONNECTED" for g in result["groups"])
    asyncio.run(run())
    assert set(seen) == {CRYPTO, STOCKS}


@pytest.mark.parametrize("bad_group", [CRYPTO, STOCKS])
@pytest.mark.parametrize("failure", ["missing", "invalid", "http", "timeout"])
def test_bad_group_does_not_invalidate_other_group(bad_group, failure):
    failing = False
    def handler(request):
        symbols = tuple(json.loads(request.url.params["symbols"]))
        data = payload(symbols)
        if symbols == bad_group and failing:
            if failure == "missing":
                data.pop()
            elif failure == "invalid":
                data[0]["lastPrice"] = "NaN"
            elif failure == "http":
                return httpx.Response(503, text="secret")
            else:
                raise httpx.ReadTimeout("secret", request=request)
        return httpx.Response(200, json=data)
    async def run():
        nonlocal failing
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            feed = MarketFeed(client=client, include_bstocks=True)
            assert (await feed.refresh())["status"] == "CONNECTED"
            failing = True
            result = await feed.refresh()
            assert result["status"] == "PARTIAL"
            assert len(result["quotes"]) == 6
            assert all(q["stale"] == (q["symbol"] in bad_group) for q in result["quotes"])
            assert "secret" not in str(result)
            fresh_feed = MarketFeed(client=client, include_bstocks=True)
            initial = await fresh_feed.refresh()
            assert initial["status"] == "PARTIAL"
            assert len(initial["quotes"]) == 3
            failing = False
            assert (await feed.refresh())["status"] == "CONNECTED"
    asyncio.run(run())


def test_group_expiry_is_independent(monkeypatch):
    def handler(request):
        symbols = tuple(json.loads(request.url.params["symbols"]))
        return httpx.Response(200, json=payload(symbols, age=80 if symbols == STOCKS else 1))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            feed = MarketFeed(client=client, include_bstocks=True)
            await feed.refresh()
            monkeypatch.setattr(market, "_utc_now", lambda: NOW + timedelta(seconds=20))
            result = feed.snapshot()
            assert result["status"] == "PARTIAL"
            assert all(q["stale"] == (q["symbol"] in STOCKS) for q in result["quotes"])
            monkeypatch.setattr(market, "_utc_now", lambda: NOW + timedelta(seconds=100))
            assert feed.snapshot()["status"] == "STALE"
    asyncio.run(run())


def test_both_owned_clients_close(monkeypatch):
    clients = []
    client_class = httpx.AsyncClient
    def factory(**kwargs):
        client = client_class(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json=payload(json.loads(r.url.params["symbols"])))), **kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    async def run():
        feed = MarketFeed(include_bstocks=True)
        await feed.refresh()
        await feed.close()
        await feed.close()
        assert clients and all(c.is_closed for c in clients)
        assert (await feed.refresh())["status"] == "STALE"
    asyncio.run(run())


def test_slow_stock_stream_closes_without_invalidating_crypto():
    class Slow(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"["
            await asyncio.sleep(10)

        async def aclose(self):
            self.closed = True

    stream = Slow()
    def handler(request):
        symbols = tuple(json.loads(request.url.params["symbols"]))
        if symbols == STOCKS:
            return httpx.Response(200, stream=stream)
        return httpx.Response(200, json=payload(symbols))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            feed = MarketFeed(client=client, include_bstocks=True, timeout=0.02)
            result = await feed.refresh()
            assert result["status"] == "PARTIAL"
            assert all(not q["stale"] for q in result["quotes"])
            assert result["groups"][1]["status"] == "UNAVAILABLE"
            assert "超时" in result["groups"][1]["error"]
            assert stream.closed
            await feed.close()
            assert not client.is_closed
    asyncio.run(run())


def test_both_failed_groups_without_cache_are_unavailable():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(503)
        )) as client:
            result = await MarketFeed(client=client, include_bstocks=True).refresh()
            assert result["status"] == "UNAVAILABLE"
            assert result["quotes"] == []
            assert all(g["status"] == "UNAVAILABLE" for g in result["groups"])
    asyncio.run(run())
