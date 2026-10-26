"""Feed tests use only detached configuration and in-memory transports."""
import asyncio
import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from trading_system.server import market_live as live, market_parse as parse
from trading_system.server.errors import ApiError
from trading_system.server.instruments import ALL_SYMBOLS

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)


class Config:
    def __init__(self, **fields):
        self.data = {"revision": 0, "name": "Saved source", "provider": "standard_json", "enabled": True,
                     "ws_url": "wss://example.test/stream", "poll_seconds": 5, "timeout_seconds": 2, **fields}
        self.reads = 0

    def runtime_config(self):
        self.reads += 1
        return copy.deepcopy(self.data)

    def get_config(self):
        return copy.deepcopy(self.data)


def quote(symbol="BTCUSDT", **fields):
    return {"symbol": symbol, "price": "10", "change_pct": "1", "as_of": "2026-10-26T03:00:00Z", **fields}


def candle(**fields):
    return {"time": MS, "open": "10", "high": "12", "low": "9", "close": "11", "volume": "20", **fields}


class Transport:
    def __init__(self, handler=None):
        self.handler = handler or (lambda config, path, params: {"quotes": [quote()]})
        self.queue = asyncio.Queue()
        self.requests = []
        self.opened = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.streams = 0
        self.closed = False

    async def get_json(self, config, path, params=None):
        self.requests.append((copy.deepcopy(config), path, params))
        result = self.handler(config, path, params)
        return await result if hasattr(result, "__await__") else copy.deepcopy(result)

    @asynccontextmanager
    async def stream(self, config, params=None):
        self.streams += 1
        self.params = params
        self.opened.set()
        async def messages():
            try:
                while True:
                    item = await self.queue.get()
                    if isinstance(item, Exception):
                        raise item
                    yield item
            finally:
                self.cancelled.set()
        yield messages()

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(parse, "utc_now", lambda: NOW)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def test_construction_and_snapshot_offline():
    config = Config()
    feed = live.LiveMarketFeed(config)
    first = feed.snapshot()
    assert config.reads == 0
    assert first["status"] == "UNAVAILABLE"
    assert first["transport"]["status"] == "CONNECTING"
    assert first["books"] == first["trades"] == first["candles"] == {}
    assert first["instance_id"] == feed.snapshot()["instance_id"]


def test_partial_standard_validation_ordering_detachment_and_source_label():
    async def run():
        payload = {"source": "UNTRUSTED", "quotes": [quote(), quote("ETHUSDT", price="NaN"), quote("UNSUPPORTED")],
                   "books": {"BTCUSDT": {"bids": [["9", "2"]], "asks": [["11", "2"]], "as_of": quote()["as_of"]}},
                   "candles": {"BTCUSDT": {"1m": candle()}}}
        transport = Transport(lambda *args: payload)
        feed = live.LiveMarketFeed(Config(), transport=transport)
        first = await feed.refresh()
        assert first["source"] == "Saved source"
        assert first["status"] == "PARTIAL"
        assert len(first["quotes"]) == 1
        assert first["groups"][1]["status"] == "UNAVAILABLE"
        assert first["transport"]["status"] == "POLLING"
        first["quotes"][0]["price"] = "999"
        first["books"]["BTCUSDT"]["bids"][0][0] = "999"
        assert feed.snapshot()["quotes"][0]["price"] == "10"
        assert feed.snapshot()["books"]["BTCUSDT"]["bids"][0][0] == "9"
        transport.handler = lambda *args: {"quotes": [quote(price="8", as_of="2026-10-26T02:59:59Z")]}
        await feed.refresh()
        assert feed.snapshot()["quotes"][0]["price"] == "10"
        await feed.close()
        assert not transport.closed
    asyncio.run(run())


def test_expiry_checks_utc_and_monotonic_without_refresh(monkeypatch):
    async def run():
        mono = [100.0]
        monkeypatch.setattr(live.time, "monotonic", lambda: mono[0])
        feed = live.LiveMarketFeed(Config(), transport=Transport())
        first = await feed.refresh()
        mono[0] = 191
        monkeypatch.setattr(parse, "utc_now", lambda: NOW - timedelta(seconds=1))
        expired = feed.snapshot()
        assert expired["quotes"][0]["stale"]
        assert expired["sequence"] > first["sequence"]
        assert expired["status"] == "STALE"
        await feed.close()
    asyncio.run(run())


def test_start_nonblocking_socket_open_not_live_then_valid_event_and_disconnect():
    async def run():
        config, transport = Config(), Transport(lambda *args: {"quotes": []})
        feed = live.LiveMarketFeed(config, transport=transport)
        await asyncio.wait_for(feed.start(), .2)
        await transport.opened.wait()
        assert feed.snapshot()["transport"]["status"] != "LIVE"
        await transport.queue.put('{"quotes":[{"symbol":"BTCUSDT","price":"NaN"}]}')
        await transport.queue.put(json.dumps({"quotes": [quote()]}))
        await until(lambda: feed.snapshot()["transport"]["status"] == "LIVE")
        assert config.reads == 1  # no DB/config access per event
        await transport.queue.put(RuntimeError("credentials-secret"))
        await until(lambda: feed.snapshot()["transport"]["status"] == "RECONNECTING")
        assert "secret" not in json.dumps(feed.snapshot())
        await asyncio.wait_for(feed.close(), .2)
        assert not feed._tasks and transport.cancelled.is_set()
    asyncio.run(run())


def test_disabled_never_connects():
    async def run():
        transport = Transport()
        feed = live.LiveMarketFeed(Config(enabled=False), transport=transport)
        await feed.start()
        await feed.refresh()
        assert not transport.requests and not transport.streams
        assert feed.snapshot()["transport"]["status"] == "DISABLED"
        await feed.close()
    asyncio.run(run())


def test_source_generation_invalidates_synchronously_and_late_rest_cannot_return():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(config, path, params):
            entered.set()
            await release.wait()
            return {"quotes": [quote()]}
        config, transport = Config(), Transport(delayed)
        feed = live.LiveMarketFeed(config, transport=transport)
        task = asyncio.create_task(feed.refresh())
        await entered.wait()
        config.data.update(revision=1, name="New source", enabled=False)
        pending = feed.reconfigure()
        assert feed.snapshot()["source_revision"] == 1
        assert not feed.snapshot()["quotes"]
        release.set()
        await task
        await pending
        assert not feed.snapshot()["quotes"]
        await feed.close()
    asyncio.run(run())


def test_reconfigure_cancels_ws_and_old_event_cannot_merge():
    async def run():
        config, transport = Config(), Transport()
        feed = live.LiveMarketFeed(config, transport=transport)
        await feed.start()
        await transport.opened.wait()
        await until(lambda: bool(feed.snapshot()["quotes"]))
        config.data.update(revision=1, enabled=False)
        await feed.reconfigure()
        await transport.queue.put(json.dumps({"quotes": [quote(price="999")]}))
        await asyncio.sleep(0)
        assert feed.snapshot()["quotes"] == []
        await feed.close()
    asyncio.run(run())


def test_binance_partial_group_fallback_and_independent_bootstrap_failure():
    async def run():
        def handler(config, path, params):
            if path != "/api/v3/ticker/24hr":
                raise ValueError("upstream body secret")
            symbols = json.loads(params["symbols"])
            if any(symbol.endswith("BUSDT") for symbol in symbols):
                raise ValueError("unsupported")
            return [{"symbol": s, "lastPrice": "10", "priceChangePercent": "1", "closeTime": MS} for s in symbols]
        transport = Transport(handler)
        feed = live.LiveMarketFeed(Config(provider="binance"), transport=transport)
        result = await feed.refresh()
        assert len(result["quotes"]) == 3
        assert result["groups"][0]["status"] == "CONNECTED"
        assert result["groups"][1]["status"] == "UNAVAILABLE"
        assert len(transport.requests) == 5
        tested = await feed.test_connection(0)
        assert tested["ok"] and tested["quote_count"] == 3
        await feed.close()
    asyncio.run(run())


def test_binance_stream_identity_depth_order_and_trade_dedup():
    feed = live.LiveMarketFeed(Config(), transport=Transport())
    feed._load()
    data = {"e": "24hrTicker", "E": MS, "s": "BTCUSDT", "c": "10", "P": "1", "C": MS}
    assert feed._apply_binance_ws({"stream": "btcusdt@ticker", "data": data})
    with pytest.raises(parse.InvalidMarketData):
        feed._apply_binance_ws({"stream": "ethusdt@ticker", "data": data})
    depth = {"lastUpdateId": 100, "bids": [["9", "1"]], "asks": [["11", "1"]]}
    assert feed._apply_binance_ws({"stream": "btcusdt@depth5@1000ms", "data": depth})
    assert not feed._apply_binance_ws({"stream": "btcusdt@depth5@1000ms", "data": {**depth, "lastUpdateId": 99}})
    trade = {"e": "aggTrade", "E": MS, "s": "BTCUSDT", "a": 1, "p": "10", "q": "2", "T": MS, "m": True}
    assert feed._apply_binance_ws({"stream": "btcusdt@aggTrade", "data": trade})
    assert not feed._apply_binance_ws({"stream": "btcusdt@aggTrade", "data": trade})
    assert feed.snapshot()["trades"]["BTCUSDT"][0]["side"] == "SELL"
    assert feed.snapshot()["quotes"][0]["as_of"] == "2026-10-26T03:00:00.000Z"


def test_history_all_intervals_cached_and_ws_candle_preserved():
    async def run():
        transport = Transport(lambda config, path, params: {"symbol": params["symbol"], "interval": params["interval"], "candles": [candle(time=MS // parse.INTERVAL_MS[params["interval"]] * parse.INTERVAL_MS[params["interval"]]) ]})
        feed = live.LiveMarketFeed(Config(), transport=transport)
        for interval in parse.INTERVALS:
            result = await feed.candles("BTCUSDT", interval, 1)
            assert result["candles"] == [candle(time=MS // parse.INTERVAL_MS[interval] * parse.INTERVAL_MS[interval])]
            assert (await feed.candles("BTCUSDT", interval, 1)) == result
        assert len(transport.requests) == 6
        newer = candle(close="12", volume="21")
        assert feed._put_candle("BTCUSDT", "1m", newer)
        feed._history_at.clear()
        assert (await feed.candles("BTCUSDT", "1m", 1))["candles"] == [newer]
        assert not feed._put_candle("BTCUSDT", "1m", candle())
        await feed.close()
    asyncio.run(run())


def test_late_history_from_previous_source_is_empty_current_revision():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(config, path, params):
            entered.set()
            await release.wait()
            return {"symbol": "BTCUSDT", "interval": "1m", "candles": [candle()]}
        config = Config()
        feed = live.LiveMarketFeed(config, transport=Transport(delayed))
        task = asyncio.create_task(feed.candles("BTCUSDT", "1m"))
        await entered.wait()
        config.data.update(revision=1, enabled=False)
        await feed.reconfigure()
        release.set()
        result = await task
        assert result["source_revision"] == 1 and result["candles"] == []
        await feed.close()
    asyncio.run(run())


def test_rest_only_current_candle_updates_without_replacing_newer_data():
    async def run():
        payload = {"symbol": "BTCUSDT", "interval": "1m", "candles": [candle()]}
        feed = live.LiveMarketFeed(Config(ws_url=""), transport=Transport(lambda *args: payload))
        await feed.candles("BTCUSDT", "1m", 1)
        feed._history_at.clear()
        payload["candles"] = [candle(close="12", volume="21")]
        updated = await feed.candles("BTCUSDT", "1m", 1)
        assert updated["candles"][0]["close"] == "12"
        assert feed.snapshot()["candles"]["BTCUSDT"]["1m"]["close"] == "12"
        await feed.close()
    asyncio.run(run())


def test_history_out_of_order_response_cannot_replace_later_history():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def handler(*args):
            nonlocal count
            count += 1
            if count == 1:
                entered.set()
                await release.wait()
                return {"symbol": "BTCUSDT", "interval": "1m", "candles": [candle(time=MS - 120000), candle()]}
            return {"symbol": "BTCUSDT", "interval": "1m", "candles": [candle(time=MS - 60000), candle(volume="21")]}
        feed = live.LiveMarketFeed(Config(), transport=Transport(handler))
        first = asyncio.create_task(feed.candles("BTCUSDT", "1m", 2))
        await entered.wait()
        second = await feed.candles("BTCUSDT", "1m", 2)
        release.set()
        assert (await first) == second
        assert [row["time"] for row in second["candles"]] == [MS - 60000, MS]
        await feed.close()
    asyncio.run(run())


@pytest.mark.parametrize("symbol,interval,limit", [("UNKNOWN", "1m", 120), ("BTCUSDT", "2m", 120), ("BTCUSDT", "1m", True), ("BTCUSDT", "1m", 201)])
def test_history_input_validation(symbol, interval, limit):
    async def run():
        with pytest.raises(ApiError) as error:
            await live.LiveMarketFeed(Config(), transport=Transport()).candles(symbol, interval, limit)
        assert error.value.code == "MARKET_INPUT"
    asyncio.run(run())


def test_connection_does_not_activate_and_revision_guard():
    async def run():
        config = Config()
        feed = live.LiveMarketFeed(config, transport=Transport())
        assert (await feed.test_connection(0))["quote_count"] == 1
        assert not feed.snapshot()["quotes"]
        with pytest.raises(ApiError) as error:
            await feed.test_connection(1)
        assert error.value.status == 409
        await feed.close()
    asyncio.run(run())


def test_broken_key_vault_does_not_crash_start_or_connect():
    class Broken(Config):
        def runtime_config(self):
            raise ApiError("MARKET_KEY_UNAVAILABLE", "secret credential")
    async def run():
        transport = Transport()
        feed = live.LiveMarketFeed(Broken(revision=4), transport=transport)
        await feed.start()
        assert feed.snapshot()["source_revision"] == 4
        assert feed.snapshot()["error"]
        assert "secret" not in json.dumps(feed.snapshot())
        assert not transport.requests and not transport.streams
        await feed.close()
    asyncio.run(run())


def test_refresh_is_bounded_and_cannot_leak_upstream_exception():
    async def run():
        async def hangs(*args):
            await asyncio.Event().wait()
        feed = live.LiveMarketFeed(Config(timeout_seconds=.01), transport=Transport(hangs))
        result = await asyncio.wait_for(feed.refresh(), .2)
        assert result["error"] and not result["quotes"]
        await feed.close()
    asyncio.run(run())


def test_silent_or_invalid_stream_reconnects_and_rest_remains_truthful():
    async def run():
        transport = Transport()
        feed = live.LiveMarketFeed(Config(), max_age_seconds=.03, transport=transport)
        await feed.start()
        await transport.opened.wait()
        for _ in range(30):
            await transport.queue.put("not-json")
        await until(lambda: feed.snapshot()["transport"]["reconnect_attempt"] == 1)
        assert feed.snapshot()["transport"]["status"] == "RECONNECTING"
        assert feed.snapshot()["transport"]["mode"] == "polling"
        transport.handler = lambda *args: {"quotes": [quote(as_of="2026-10-26T03:00:01Z")]}
        await feed.refresh()
        assert feed.snapshot()["transport"]["status"] == "POLLING"
        await asyncio.wait_for(feed.close(), .2)
    asyncio.run(run())


def test_worker_thread_snapshot_is_detached_during_async_updates():
    async def run():
        feed = live.LiveMarketFeed(Config(), transport=Transport())
        await feed.refresh()
        def snapshots():
            for _ in range(500):
                value = feed.snapshot()
                assert len(value["quotes"]) == 1
                value["quotes"].clear()
        reader = asyncio.create_task(asyncio.to_thread(snapshots))
        for index in range(100):
            feed._put_trades("BTCUSDT", [{"id": str(index), "price": "10", "quantity": "1", "side": "BUY", "as_of": quote()["as_of"]}])
            await asyncio.sleep(0)
        await reader
        assert len(feed.snapshot()["trades"]["BTCUSDT"]) == 40
        await feed.close()
    asyncio.run(run())


@pytest.mark.parametrize("rest_symbols", [("BTCUSDT", "ETHUSDT"), ALL_SYMBOLS], ids=["partial-rest", "complete-rest"])
def test_partial_websocket_keeps_other_symbols_fresh_via_rest(monkeypatch, rest_symbols):
    async def run():
        origin = live.time.monotonic()
        monkeypatch.setattr(parse, "utc_now", lambda: NOW + timedelta(seconds=live.time.monotonic() - origin))
        def current(symbol):
            return quote(symbol, as_of=parse.utc_now().isoformat())
        transport = Transport(lambda *args: {"quotes": [current(symbol) for symbol in rest_symbols]})
        feed = live.LiveMarketFeed(Config(poll_seconds=.02), max_age_seconds=.12, transport=transport)
        await feed.start()
        await transport.opened.wait()
        async def btc_stream():
            while True:
                await transport.queue.put(json.dumps({"quotes": [current("BTCUSDT")]}))
                await asyncio.sleep(.01)
        producer = asyncio.create_task(btc_stream())
        try:
            await until(lambda: feed.snapshot()["transport"]["status"] == "LIVE")
            initial_requests = len(transport.requests)
            await asyncio.sleep(.3)
            selected = {item["symbol"]: item for item in feed.snapshot()["quotes"]}
            assert not selected["ETHUSDT"]["stale"]
            assert len(transport.requests) > initial_requests
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            await feed.close()
    asyncio.run(run())


def test_complete_fresh_websocket_quotes_suppress_extra_rest(monkeypatch):
    async def run():
        origin = live.time.monotonic()
        monkeypatch.setattr(parse, "utc_now", lambda: NOW + timedelta(seconds=live.time.monotonic() - origin))
        def current():
            return {"quotes": [quote(symbol, as_of=parse.utc_now().isoformat()) for symbol in ALL_SYMBOLS]}
        transport = Transport(lambda *args: current())
        feed = live.LiveMarketFeed(Config(poll_seconds=.01), max_age_seconds=.1, transport=transport)
        await feed.start()
        await transport.opened.wait()
        async def complete_stream():
            while True:
                await transport.queue.put(json.dumps(current()))
                await asyncio.sleep(.005)
        producer = asyncio.create_task(complete_stream())
        try:
            await until(lambda: feed.snapshot()["transport"]["status"] == "LIVE")
            initial_requests = len(transport.requests)
            await asyncio.sleep(.2)
            assert len(transport.requests) == initial_requests
            assert feed.snapshot()["status"] == "CONNECTED"
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            await feed.close()
    asyncio.run(run())


def test_stale_book_repair_continues_during_complete_quote_stream(monkeypatch):
    async def run():
        origin = live.time.monotonic()
        monkeypatch.setattr(parse, "utc_now", lambda: NOW + timedelta(seconds=live.time.monotonic() - origin))
        monkeypatch.setattr(live, "_EXTRAS_REPAIR_SECONDS", .04, raising=False)
        depth_updates = 0
        def handler(config, path, params):
            nonlocal depth_updates
            if path.endswith("24hr"):
                return [{"symbol": symbol, "lastPrice": "10", "priceChangePercent": "1", "closeTime": int(parse.utc_now().timestamp() * 1000)} for symbol in json.loads(params["symbols"])]
            if path.endswith("depth"):
                depth_updates += 1
                return {"lastUpdateId": depth_updates, "bids": [["9", "1"]], "asks": [["11", "1"]]}
            return []
        transport = Transport(handler)
        feed = live.LiveMarketFeed(Config(provider="binance", poll_seconds=.005), max_age_seconds=.025, transport=transport)
        await feed.start()
        await transport.opened.wait()
        async def quote_stream():
            while True:
                stamp = int(parse.utc_now().timestamp() * 1000)
                for symbol in ALL_SYMBOLS:
                    await transport.queue.put(json.dumps({"stream": symbol.lower() + "@ticker", "data": {
                        "e": "24hrTicker", "E": stamp, "s": symbol, "c": "10", "P": "1", "C": stamp}}))
                await asyncio.sleep(.003)
        producer = asyncio.create_task(quote_stream())
        try:
            await until(lambda: depth_updates == 6)
            await until(lambda: feed.snapshot()["transport"]["status"] == "LIVE")
            await until(lambda: depth_updates >= 12)
            assert all(not item["stale"] for item in feed.snapshot()["books"].values())
            ticker_requests = [request for request in transport.requests if request[1].endswith("24hr")]
            assert len(ticker_requests) == 2
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            await feed.close()
    asyncio.run(run())


def test_standard_optional_books_repair_on_bounded_cadence_with_live_quotes(monkeypatch):
    async def run():
        origin = live.time.monotonic()
        monkeypatch.setattr(parse, "utc_now", lambda: NOW + timedelta(seconds=live.time.monotonic() - origin))
        monkeypatch.setattr(live, "_EXTRAS_REPAIR_SECONDS", .04)
        def current():
            return {"quotes": [quote(symbol, as_of=parse.utc_now().isoformat()) for symbol in ALL_SYMBOLS]}
        def rest(*args):
            return {**current(), "books": {"BTCUSDT": {"bids": [["9", "1"]], "asks": [["11", "1"]], "as_of": parse.utc_now().isoformat()}}}
        transport = Transport(rest)
        feed = live.LiveMarketFeed(Config(poll_seconds=.005), max_age_seconds=.025, transport=transport)
        await feed.start()
        await transport.opened.wait()
        async def producer_loop():
            while True:
                await transport.queue.put(json.dumps(current()))
                await asyncio.sleep(.003)
        producer = asyncio.create_task(producer_loop())
        try:
            await until(lambda: feed.snapshot()["transport"]["status"] == "LIVE")
            initial = len(transport.requests)
            await until(lambda: len(transport.requests) > initial)
            assert not feed.snapshot()["books"]["BTCUSDT"]["stale"]
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            await feed.close()
    asyncio.run(run())
