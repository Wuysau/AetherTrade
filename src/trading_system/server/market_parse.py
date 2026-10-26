"""Strict, bounded market DTO parsing. All failures contain static text only.

Market event clocks may lead UTC by at most five seconds. Historic candles and
trades are genuine source data; their age never refreshes a quote's lifetime.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from .instruments import ALL_SYMBOLS

INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
INTERVAL_MS = dict(zip(INTERVALS, (60000, 300000, 900000, 3600000, 14400000, 86400000)))
CLOCK_SKEW = 5
_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z")


class InvalidMarketData(ValueError):
    def __init__(self):
        super().__init__("行情来源数据格式无效")


def utc_now():
    return datetime.now(timezone.utc)


def decimal(value, *, positive=False, nonnegative=False):
    if not isinstance(value, str) or len(value) > 64 or not _DECIMAL.fullmatch(value):
        raise InvalidMarketData()
    number = Decimal(value)
    if abs(number) > Decimal("1e30") or (positive and number <= 0) or (nonnegative and number < 0):
        raise InvalidMarketData()
    return value


def millis(value, *, now=None):
    now = now or utc_now()
    if type(value) is not int or value <= 0 or value > int((now.timestamp() + CLOCK_SKEW) * 1000):
        raise InvalidMarketData()
    return value


def iso(value, *, now=None):
    if not isinstance(value, str) or len(value) > 40 or not _ISO.fullmatch(value):
        raise InvalidMarketData()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        millis(int(stamp.timestamp() * 1000), now=now)
    except (ValueError, OverflowError) as exc:
        raise InvalidMarketData() from exc
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_iso(value, *, now=None):
    millis(value, now=now)
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def symbol(value):
    if not isinstance(value, str) or value not in ALL_SYMBOLS:
        raise InvalidMarketData()
    return value


def quote(item, *, binance=False, now=None):
    if not isinstance(item, dict):
        raise InvalidMarketData()
    result = {
        "symbol": symbol(item.get("symbol")),
        "price": decimal(item.get("lastPrice" if binance else "price"), positive=True),
        "change_pct": decimal(item.get("priceChangePercent" if binance else "change_pct")),
        "as_of": to_iso(item.get("closeTime"), now=now) if binance else iso(item.get("as_of"), now=now),
    }
    fields = {"open": "openPrice", "high": "highPrice", "low": "lowPrice", "volume": "volume", "quote_volume": "quoteVolume"}
    for key, upstream in fields.items():
        source = upstream if binance else key
        if source in item:
            result[key] = decimal(item[source], positive=key in ("open", "high", "low"), nonnegative=key in ("volume", "quote_volume"))
    if "high" in result and "low" in result:
        low, high = Decimal(result["low"]), Decimal(result["high"])
        if low > high or not low <= Decimal(result["price"]) <= high:
            raise InvalidMarketData()
        if "open" in result and not low <= Decimal(result["open"]) <= high:
            raise InvalidMarketData()
    return result


def book(item, *, as_of=None, now=None):
    if not isinstance(item, dict):
        raise InvalidMarketData()
    result = {"as_of": iso(as_of if as_of is not None else item.get("as_of"), now=now), "stale": False}
    for side, reverse in (("bids", True), ("asks", False)):
        rows = item.get(side)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise InvalidMarketData()
        levels = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                raise InvalidMarketData()
            levels.append([decimal(row[0], positive=True), decimal(row[1], nonnegative=True)])
        if len({Decimal(row[0]) for row in levels}) != len(levels):
            raise InvalidMarketData()
        result[side] = sorted(levels, key=lambda row: Decimal(row[0]), reverse=reverse)[:5]
    if Decimal(result["bids"][0][0]) >= Decimal(result["asks"][0][0]):
        raise InvalidMarketData()
    return result


def trade(item, *, binance=False, now=None):
    if not isinstance(item, dict):
        raise InvalidMarketData()
    identifier = item.get("id")
    if type(identifier) is int and 0 <= identifier <= 2**63 - 1:
        identifier = str(identifier)
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 80 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", identifier):
        raise InvalidMarketData()
    if binance:
        if type(item.get("isBuyerMaker")) is not bool:
            raise InvalidMarketData()
        side = "SELL" if item["isBuyerMaker"] else "BUY"
    else:
        side = item.get("side")
        if side not in ("BUY", "SELL"):
            raise InvalidMarketData()
    return {"id": identifier, "price": decimal(item.get("price"), positive=True),
            "quantity": decimal(item.get("qty" if binance else "quantity"), positive=True), "side": side,
            "as_of": to_iso(item.get("time"), now=now) if binance else iso(item.get("as_of"), now=now)}


def candle(item, interval, *, now=None):
    if interval not in INTERVALS or not isinstance(item, dict):
        raise InvalidMarketData()
    opened = millis(item.get("time"), now=now)
    if opened % INTERVAL_MS[interval]:
        raise InvalidMarketData()
    result = {"time": opened, **{key: decimal(item.get(key), positive=key != "volume", nonnegative=key == "volume") for key in ("open", "high", "low", "close", "volume")}}
    low, high = Decimal(result["low"]), Decimal(result["high"])
    if low > high or any(not low <= Decimal(result[key]) <= high for key in ("open", "close")):
        raise InvalidMarketData()
    return result


def kline(row, interval, *, now=None):
    if not isinstance(row, list) or not 6 <= len(row) <= 12:
        raise InvalidMarketData()
    return candle(dict(zip(("time", "open", "high", "low", "close", "volume"), row)), interval, now=now)


def loads(raw):
    if not isinstance(raw, (str, bytes)) or len(raw) > 262144 or (isinstance(raw, str) and len(raw.encode("utf-8")) > 262144):
        raise InvalidMarketData()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InvalidMarketData()
            result[key] = value
        return result
    def constant(value):
        raise InvalidMarketData()
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidMarketData() from exc
    if not isinstance(result, dict):
        raise InvalidMarketData()
    return result
