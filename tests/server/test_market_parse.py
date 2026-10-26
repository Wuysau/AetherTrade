from datetime import datetime, timezone

import pytest

from trading_system.server import market_parse as parse

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(parse, "utc_now", lambda: NOW)


def candle(**values):
    return {"time": MS, "open": "10", "high": "12", "low": "9", "close": "11", "volume": "20", **values}


@pytest.mark.parametrize("value", [True, 1, None, "NaN", "Infinity", "1e6", " 1", "9" * 65, "1" + "0" * 31])
def test_decimal_bounds(value):
    with pytest.raises(parse.InvalidMarketData):
        parse.decimal(value)


@pytest.mark.parametrize("values", [{"open": "13"}, {"close": "8"}, {"low": "13"}, {"volume": "-1"}, {"time": MS + 1}, {"time": MS + 60000}])
def test_candle_invariants(values):
    with pytest.raises(parse.InvalidMarketData):
        parse.candle(candle(**values), "1m")


@pytest.mark.parametrize("value", ["2026-10-26T03:00:00", "2026-10-26T03:00:00+08:00", "2026-10-26T03:00:06Z", "secret"])
def test_requires_utc_bounded_clock(value):
    with pytest.raises(parse.InvalidMarketData):
        parse.iso(value)
    assert parse.iso("2026-10-26T03:00:05Z").endswith("Z")


@pytest.mark.parametrize("raw", [b'\xff', '{"a":1,"a":2}', '{"a":NaN}', '[]', ' ' * 262145, '[' * 2000], ids=["encoding", "duplicate", "nonfinite", "array", "oversized", "nested"])
def test_wire_json_is_strict_and_bounded(raw):
    with pytest.raises(parse.InvalidMarketData):
        parse.loads(raw)


def test_book_sorted_bounded_and_crossed_rejected():
    result = parse.book({"bids": [[str(i), "1"] for i in range(1, 8)], "asks": [[str(i), "1"] for i in range(12, 8, -1)], "as_of": "2026-10-26T03:00:00Z"})
    assert result["bids"] == [[str(i), "1"] for i in range(7, 2, -1)]
    assert result["asks"][0][0] == "9"
    with pytest.raises(parse.InvalidMarketData):
        parse.book({"bids": [["10", "1"]], "asks": [["9", "1"]], "as_of": "2026-10-26T03:00:00Z"})


def test_candle_and_trade_have_no_invented_fields():
    assert parse.candle(candle(), "1m") == candle()
    assert parse.trade({"id": 1, "price": "10", "qty": "2", "time": MS, "isBuyerMaker": True}, binance=True) == {
        "id": "1", "price": "10", "quantity": "2", "as_of": "2026-10-26T03:00:00.000Z", "side": "SELL"}
