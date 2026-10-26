"""Deterministic public quote adapter tests; no live network is used here."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from trading_system.server import market
from trading_system.server.market import MarketFeed, Quote


NOW = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(market, "_utc_now", lambda: NOW)


def ticker_payload():
    return [
        {
            "symbol": symbol,
            "lastPrice": price,
            "priceChangePercent": change,
            "closeTime": int((NOW - timedelta(seconds=1)).timestamp() * 1000),
        }
        for symbol, price, change in zip(
            SYMBOLS, ["68000.12000000", "3500.50", "140.01"], ["1.125", "-2.100", "0.000"]
        )
    ]


def run_refresh(handler, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await MarketFeed(client=client, **kwargs).refresh()

    return asyncio.run(run())


def test_constructor_and_snapshot_are_offline(monkeypatch):
    def forbidden_client(*args, **kwargs):
        pytest.fail("constructing/snapshotting the feed must not create a network client")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden_client)
    feed = MarketFeed()
    result = feed.snapshot()
    assert set(result) == {"source", "status", "last_updated", "error", "quotes"}
    assert result["status"] == "UNAVAILABLE"
    assert result["last_updated"] is None
    assert result["quotes"] == []
    assert result["error"]


def test_quote_fields():
    quote = Quote("BTCUSDT", "1.00", "0", "2026-10-26T03:00:00Z", "test", False)
    assert quote.price == "1.00"
    assert quote.stale is False


def test_fixed_public_request_and_exact_quote_shape():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "data-api.binance.vision"
        assert request.url.path == "/api/v3/ticker/24hr"
        assert json.loads(request.url.params["symbols"]) == SYMBOLS
        assert request.url.params["type"] == "FULL"
        assert request.headers["accept-encoding"] == "identity"
        assert all(value == 6.0 for value in request.extensions["timeout"].values())
        return httpx.Response(200, json=list(reversed(ticker_payload())))

    result = run_refresh(handler)
    assert len(requests) == 1
    assert result["status"] == "CONNECTED"
    assert "Binance" in result["source"]
    assert "data-api.binance.vision" in result["source"]
    assert result["error"] is None
    assert result["last_updated"] == "2026-10-26T02:59:59.000Z"
    assert [quote["symbol"] for quote in result["quotes"]] == SYMBOLS
    assert result["quotes"][0] == {
        "symbol": "BTCUSDT", "price": "68000.12000000", "change_pct": "1.125",
        "as_of": "2026-10-26T02:59:59.000Z", "source": result["source"], "stale": False,
    }


def test_injected_client_cannot_add_credentials_or_arbitrary_query():
    def handler(request):
        assert "authorization" not in request.headers
        assert "x-mbx-apikey" not in request.headers
        assert "cookie" not in request.headers
        assert set(request.url.params) == {"symbols", "type"}
        return httpx.Response(200, json=ticker_payload())

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), auth=("user", "secret"),
            headers={"X-MBX-APIKEY": "secret"}, cookies={"session": "secret"},
            params={"arbitrary": "secret"}, follow_redirects=True,
        ) as client:
            assert (await MarketFeed(client=client).refresh())["status"] == "CONNECTED"

    asyncio.run(run())


@pytest.mark.parametrize("field,value", [
    ("lastPrice", "0"), ("lastPrice", "-1"), ("lastPrice", "NaN"),
    ("lastPrice", "Infinity"), ("lastPrice", "1e999999"), ("lastPrice", 123),
    ("lastPrice", True), ("lastPrice", None), ("lastPrice", "9" * 1000),
    ("lastPrice", " 1.0"), ("priceChangePercent", "NaN"),
    ("priceChangePercent", "-Infinity"), ("priceChangePercent", False),
    ("priceChangePercent", "1e999999"), ("closeTime", True),
    ("closeTime", "1792983599000"), ("closeTime", -1),
    ("closeTime", 10**30), ("closeTime", int(NOW.timestamp() * 1000) + 1),
    ("closeTime", int((NOW - timedelta(seconds=91)).timestamp() * 1000)),
])
def test_rejects_invalid_values_and_timestamps(field, value):
    payload = ticker_payload()
    payload[0][field] = value
    result = run_refresh(lambda request: httpx.Response(200, json=payload))
    assert result["status"] == "UNAVAILABLE"
    assert result["quotes"] == []
    assert result["error"]


@pytest.mark.parametrize("payload", [
    {}, [], [None, None, None], ticker_payload()[:2], ticker_payload() * 2,
    [ticker_payload()[0]] * 3,
    [{**ticker_payload()[0], "symbol": "BTCUSD"}, *ticker_payload()[1:]],
    [{"symbol": "BTCUSDT"}, *ticker_payload()[1:]],
])
def test_rejects_partial_or_unexpected_schema(payload):
    result = run_refresh(lambda request: httpx.Response(200, json=payload))
    assert result["status"] == "UNAVAILABLE"
    assert not result["quotes"]


@pytest.mark.parametrize("content", [b"not json <secret>", b"[", b"\xff", b"[" * 2000])
def test_malformed_json_is_unavailable_and_does_not_echo_response(content):
    result = run_refresh(lambda request: httpx.Response(200, content=content))
    assert result["status"] == "UNAVAILABLE"
    assert "secret" not in result["error"]


def test_duplicate_json_properties_are_rejected():
    content = json.dumps(ticker_payload()).replace('"lastPrice":', '"lastPrice":"0", "lastPrice":', 1)
    assert run_refresh(lambda request: httpx.Response(200, text=content))["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500])
def test_http_errors_are_sanitized_and_redirects_are_not_followed(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/secret"}, text="api-key=secret")

    result = run_refresh(handler)
    assert result["status"] == "UNAVAILABLE"
    assert str(status) in result["error"]
    assert "secret" not in result["error"]
    assert len(requests) == 1


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
def test_transport_errors_are_sanitized(exception):
    def handler(request):
        raise exception("proxy password=secret", request=request)

    result = run_refresh(handler)
    assert result["status"] == "UNAVAILABLE"
    assert result["error"]
    assert "secret" not in result["error"]


def test_entire_operation_has_a_deadline():
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json=ticker_payload())

    result = run_refresh(handler, timeout=0.01)
    assert result["status"] == "UNAVAILABLE"
    assert "超时" in result["error"]


def test_slow_response_body_times_out_and_closes():
    class Slow(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"["
            await asyncio.sleep(10)

        async def aclose(self):
            self.closed = True

    stream = Slow()
    result = run_refresh(lambda request: httpx.Response(200, stream=stream), timeout=0.01)
    assert result["status"] == "UNAVAILABLE"
    assert "超时" in result["error"]
    assert stream.closed


def test_streamed_body_is_bounded_and_closed():
    class TooLarge(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b" " * 32768
            yield b" " * 32768
            pytest.fail("oversized response must stop being consumed")

        async def aclose(self):
            self.closed = True

    stream = TooLarge()
    result = run_refresh(lambda request: httpx.Response(200, stream=stream))
    assert result["status"] == "UNAVAILABLE"
    assert stream.closed


@pytest.mark.parametrize("headers", [
    {"content-length": "999999"}, {"content-length": "-1"},
    {"content-length": "invalid"}, {"content-encoding": "gzip"},
])
def test_invalid_or_compressed_response_metadata_is_rejected(headers):
    class Empty(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("invalid metadata must be rejected before reading the body")
            yield b""

    result = run_refresh(lambda request: httpx.Response(200, headers=headers, stream=Empty()))
    assert result["status"] == "UNAVAILABLE"


def test_failure_preserves_cache_marks_stale_and_success_recovers():
    responses = [httpx.Response(200, json=ticker_payload()), httpx.Response(503),
                 httpx.Response(200, json=ticker_payload())]

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: responses.pop(0))) as client:
            feed = MarketFeed(client=client)
            first = await feed.refresh()
            failed = await feed.refresh()
            assert failed["status"] == "STALE"
            assert failed["last_updated"] == first["last_updated"]
            assert failed["error"]
            for old, stale in zip(first["quotes"], failed["quotes"]):
                assert stale == {**old, "stale": True}
            assert feed.snapshot() == failed
            assert (await feed.refresh())["status"] == "CONNECTED"
            assert feed.snapshot()["error"] is None

    asyncio.run(run())


def test_snapshot_rechecks_age_and_returns_independent_data(monkeypatch):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ticker_payload()))) as client:
            feed = MarketFeed(client=client)
            first = await feed.refresh()
            first["quotes"][0]["price"] = "999999"
            assert feed.snapshot()["quotes"][0]["price"] == "68000.12000000"
            monkeypatch.setattr(market, "_utc_now", lambda: NOW + timedelta(seconds=90))
            stale = feed.snapshot()
            assert stale["status"] == "STALE"
            assert all(quote["stale"] for quote in stale["quotes"])
            assert stale["error"]

    asyncio.run(run())


def test_clock_rollback_cannot_extend_quote_lifetime(monkeypatch):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ticker_payload()))) as client:
            feed = MarketFeed(client=client)
            with monkeypatch.context() as clock:
                clock.setattr(market.time, "monotonic", lambda: 100.0)
                assert (await feed.refresh())["status"] == "CONNECTED"
                clock.setattr(market.time, "monotonic", lambda: 191.0)
                clock.setattr(market, "_utc_now", lambda: NOW - timedelta(milliseconds=500))
                assert feed.snapshot()["status"] == "STALE"

    asyncio.run(run())


def test_close_respects_client_ownership(monkeypatch):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ticker_payload()))) as client:
            injected = MarketFeed(client=client)
            await injected.close()
            assert not client.is_closed
        created = []
        client_class = httpx.AsyncClient

        def factory(**kwargs):
            assert kwargs["trust_env"] is False
            owned = client_class(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ticker_payload())), **kwargs)
            created.append(owned)
            return owned

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        feed = MarketFeed()
        await feed.refresh()
        await feed.close()
        await feed.close()
        assert len(created) == 1 and created[0].is_closed
        assert (await feed.refresh())["status"] == "STALE"

    asyncio.run(run())


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
@pytest.mark.parametrize("argument", ["timeout", "max_age_seconds"])
def test_configuration_requires_finite_positive_bounds(argument, value):
    with pytest.raises(ValueError):
        MarketFeed(**{argument: value})
