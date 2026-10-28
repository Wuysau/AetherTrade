"""Memory-only live market terminal, with guarded source generations.

This service never submits venue orders or persists market ticks. Network and
credential policy belongs to MarketTransport; parsing is independent of it.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import threading
import time
import uuid
from contextlib import suppress
from decimal import Decimal
from functools import wraps

from . import market_parse as parse
from .errors import ApiError
from .instruments import ALL_SYMBOLS, BSTOCK_SYMBOLS, CRYPTO_SYMBOLS

_GROUPS = (("crypto", "加密货币", CRYPTO_SYMBOLS), ("bstocks", "Binance bStocks", BSTOCK_SYMBOLS))
_EXTRAS_REPAIR_SECONDS = 15


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._cache_lock:
            return method(self, *args, **kwargs)
    return call


class LiveMarketFeed:
    def __init__(self, config_service, max_age_seconds=90, transport=None):
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be finite and positive")
        self._service = config_service
        self._cache_lock = threading.RLock()
        self._transport = transport
        self._owns_transport = transport is None
        self.max_age_seconds = float(max_age_seconds)
        self._instance = str(uuid.uuid4())
        self._sequence = 0
        self._generation = 0
        self._config = None
        self._started = False
        self._closed = False
        self._tasks = set()
        self._retired = set()
        self._refresh_lock = asyncio.Lock()
        self._quotes = {}
        self._books = {}
        self._trades = {}
        self._candles = {}
        self._history = {}
        self._history_at = {}
        self._history_requests = {}
        self._history_limits = {}
        self._history_request_number = 0
        self._event_orders = {}
        self._deadlines = {}
        self._ws_live = False
        self._ws_event_mono = 0.0
        self._error = None
        self._state = {"mode": "websocket", "status": "CONNECTING", "last_event_at": None, "last_error": None, "reconnect_attempt": 0}
        self._snapshot_fingerprint = None

    def _source(self):
        return self._config["name"] if self._config else "Binance 官方行情"

    @_locked
    def _set_state(self, **fields):
        if any(self._state.get(key) != value for key, value in fields.items()):
            self._state.update(fields)
            self._sequence += 1

    def _valid(self, generation):
        return generation == self._generation and not self._closed

    def _fresh(self, as_of, deadline=None):
        age = parse.utc_now().timestamp() - parse.timestamp(as_of)
        return -parse.CLOCK_SKEW <= age <= self.max_age_seconds and (deadline is None or time.monotonic() <= deadline)

    @_locked
    def snapshot(self):
        quotes = []
        for symbol in ALL_SYMBOLS:
            if symbol in self._quotes:
                quote = dict(self._quotes[symbol])
                quote["stale"] = self._closed or not self._fresh(quote["as_of"], self._deadlines.get(("quote", symbol)))
                quotes.append(quote)
        books = copy.deepcopy(self._books)
        for symbol, book in books.items():
            book["stale"] = self._closed or not self._fresh(book["as_of"], self._deadlines.get(("book", symbol)))
        groups = []
        for identifier, label, symbols in _GROUPS:
            selected = [quote for quote in quotes if quote["symbol"] in symbols]
            fresh = sum(not quote["stale"] for quote in selected)
            status = "CONNECTED" if fresh == len(symbols) else "PARTIAL" if fresh else "STALE" if selected else "UNAVAILABLE"
            groups.append({"id": identifier, "label": label, "status": status,
                           "last_updated": min((quote["as_of"] for quote in selected), default=None),
                           "error": None if status == "CONNECTED" else "部分行情暂不可用" if fresh else "行情暂不可用或已过期"})
        fresh = sum(not quote["stale"] for quote in quotes)
        status = "CONNECTED" if fresh == len(ALL_SYMBOLS) else "PARTIAL" if fresh else "STALE" if quotes else "UNAVAILABLE"
        if self._ws_live and time.monotonic() - self._ws_event_mono > self.max_age_seconds:
            self._ws_live = False
            self._set_state(mode="polling", status="RECONNECTING", last_error="行情流数据已过期")
        fingerprint = (tuple((q["symbol"], q["stale"]) for q in quotes), tuple((s, b["stale"]) for s, b in books.items()))
        if self._snapshot_fingerprint is not None and fingerprint != self._snapshot_fingerprint:
            self._sequence += 1
        self._snapshot_fingerprint = fingerprint
        return {"source": self._source(), "status": status,
                "source_id": self._instance, "source_generation": self._generation,
                "venue": "BINANCE", "product": "SPOT",
                "last_updated": min((q["as_of"] for q in quotes), default=None),
                "error": self._error or ("行情已过期" if quotes and not fresh else None),
                "quotes": quotes, "groups": groups, "instance_id": self._instance,
                "source_revision": self._config["revision"] if self._config else 0,
                "sequence": self._sequence, "transport": dict(self._state),
                "books": books, "trades": copy.deepcopy(self._trades), "candles": copy.deepcopy(self._candles)}

    def _load(self):
        try:
            config = dict(self._service.runtime_config())
        except Exception:
            # A damaged/missing local vault must leave the settings UI usable.
            try:
                config = dict(self._service.get_config())
            except Exception:
                config = {"name": "Binance 官方行情", "revision": 0}
            config.update(enabled=False, timeout_seconds=8, poll_seconds=5, ws_url="", provider="binance")
            self._error = "行情源密钥不可用，请重新保存行情源设置"
        with self._cache_lock:
            self._config = config
            self._sequence += 1
        if self._transport is None and config["enabled"]:
            from .market_transport import MarketTransport
            self._transport = MarketTransport()
        if not self._config["enabled"]:
            self._set_state(mode="disabled", status="DISABLED", last_error=None)
        elif not self._config.get("ws_url"):
            self._set_state(mode="polling", status="POLLING")

    async def start(self):
        if self._closed or self._started:
            return
        self._started = True
        if self._config is None:
            self._load()
        self._launch()

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _launch(self):
        if self._closed or not self._started or not self._config["enabled"]:
            return
        generation, config = self._generation, dict(self._config)
        self._spawn(self._poll_loop(generation, config))
        if config.get("ws_url"):
            self._spawn(self._ws_loop(generation, config))

    @_locked
    def reconfigure(self):
        """Invalidate at call time, even before the returned coroutine is awaited."""
        self._generation += 1
        for task in tuple(self._tasks):
            task.cancel()
            self._retired.add(task)
            task.add_done_callback(self._retired.discard)
        self._tasks.clear()
        # An independently requested old-source refresh may still own its lock.
        # The new source must never wait for that request to finish.
        self._refresh_lock = asyncio.Lock()
        for cache in (self._quotes, self._books, self._trades, self._candles, self._history, self._history_at, self._history_requests, self._history_limits, self._event_orders, self._deadlines):
            cache.clear()
        self._config = None
        self._ws_live = False
        self._error = None
        self._snapshot_fingerprint = None
        self._state = {"mode": "websocket", "status": "CONNECTING", "last_event_at": None, "last_error": None, "reconnect_attempt": 0}
        self._sequence += 1
        self._load()
        async def restart():
            if not self._closed:
                self._launch()
        return restart()

    @_locked
    def _ordered(self, key, order):
        previous = self._event_orders.get(key)
        if previous is not None and order <= previous:
            return False
        self._event_orders[key] = order
        return True

    def _deadline(self, kind, symbol, as_of):
        age = max(0, parse.utc_now().timestamp() - parse.timestamp(as_of))
        self._deadlines[(kind, symbol)] = time.monotonic() + max(0, self.max_age_seconds - age)

    @_locked
    def _put_quote(self, item):
        symbol = item["symbol"]
        if not self._fresh(item["as_of"]):
            return False
        if not self._ordered(("quote", symbol), parse.timestamp(item["as_of"])):
            return False
        self._quotes[symbol] = {**item, "source": self._source(), "stale": False}
        self._deadline("quote", symbol, item["as_of"])
        self._sequence += 1
        return True

    @_locked
    def _put_book(self, symbol, item, order=None):
        if not self._fresh(item["as_of"]):
            return False
        if order is not None:
            old = self._books.get(symbol)
            if old and item["as_of"] < old["as_of"]:
                return False
            if not self._ordered(("depth", symbol), order):
                return False
        elif not self._ordered(("book", symbol), parse.timestamp(item["as_of"])):
            return False
        self._books[symbol] = item
        self._deadline("book", symbol, item["as_of"])
        self._sequence += 1
        return True

    @_locked
    def _put_trades(self, symbol, items):
        old = self._trades.get(symbol, [])
        by_id = {item["id"]: item for item in old}
        for item in items:
            # IDs are immutable: an upstream duplicate cannot alter a trade.
            by_id.setdefault(item["id"], item)
        result = sorted(by_id.values(), key=lambda item: (item["as_of"], item["id"]), reverse=True)[:40]
        if result == old:
            return False
        self._trades[symbol] = result
        self._sequence += 1
        return any(self._fresh(item["as_of"]) for item in items)

    @_locked
    def _put_candle(self, symbol, interval, item, event_order=None):
        old = self._candles.get(symbol, {}).get(interval)
        if old is not None and item["time"] < old["time"]:
            return False
        if old is not None and item["time"] == old["time"]:
            if Decimal(item["volume"]) < Decimal(old["volume"]) or Decimal(item["high"]) < Decimal(old["high"]) or Decimal(item["low"]) > Decimal(old["low"]):
                return False
        # Standard JSON has no intra-candle event timestamp: increasing volume
        # is its safe ordering key. Binance uses its explicit event timestamp.
        order = (item["time"], event_order if event_order is not None else Decimal(item["volume"]))
        if not self._ordered(("candle", symbol, interval), order):
            return False
        self._candles.setdefault(symbol, {})[interval] = item
        key = (symbol, interval)
        if key in self._history:
            rows = {row["time"]: row for row in self._history[key]}
            rows[item["time"]] = item
            self._history[key] = sorted(rows.values(), key=lambda row: row["time"])[-200:]
        self._sequence += 1
        return parse.utc_now().timestamp() * 1000 - item["time"] <= parse.INTERVAL_MS[interval] + self.max_age_seconds * 1000

    @_locked
    def _apply_standard(self, payload):
        if not isinstance(payload, dict):
            raise parse.InvalidMarketData()
        valid = False
        quotes = payload.get("quotes", [])
        if not isinstance(quotes, list) or len(quotes) > 100:
            raise parse.InvalidMarketData()
        for item in quotes:
            with suppress(parse.InvalidMarketData):
                valid = self._put_quote(parse.quote(item)) or valid
        for section in ("books", "trades", "candles"):
            values = payload.get(section, {})
            if not isinstance(values, dict) or len(values) > 100:
                continue
            for symbol, item in values.items():
                with suppress(parse.InvalidMarketData):
                    parse.symbol(symbol)
                    if section == "books":
                        valid = self._put_book(symbol, parse.book(item)) or valid
                    elif section == "trades":
                        if not isinstance(item, list) or len(item) > 40:
                            continue
                        rows = []
                        for row in item:
                            with suppress(parse.InvalidMarketData):
                                rows.append(parse.trade(row))
                        valid = self._put_trades(symbol, rows) or valid
                    elif isinstance(item, dict) and len(item) <= len(parse.INTERVALS):
                        for interval, row in item.items():
                            with suppress(parse.InvalidMarketData):
                                valid = self._put_candle(symbol, interval, parse.candle(row, interval)) or valid
        return valid

    async def _json(self, config, path, params=None):
        async with asyncio.timeout(config["timeout_seconds"]):
            return await self._transport.get_json(config, path, params)

    async def _ticker_group(self, generation, config, symbols):
        try:
            payload = await self._json(config, "/api/v3/ticker/24hr", {"symbols": json.dumps(symbols, separators=(",", ":")), "type": "FULL"})
        except Exception:
            # One delisted/unsupported bStock must not hide other selected pairs.
            if len(symbols) > 1 and self._valid(generation):
                results = await asyncio.gather(*(self._ticker_group(generation, config, (s,)) for s in symbols))
                return any(results)
            return False
        if not self._valid(generation):
            return False
        if isinstance(payload, dict) and "symbol" in payload:
            payload = [payload]
        if not isinstance(payload, list) or len(payload) > 100:
            return False
        valid = False
        for item in payload:
            with suppress(parse.InvalidMarketData):
                result = parse.quote(item, binance=True)
                if result["symbol"] in symbols:
                    valid = self._put_quote(result) or valid
        return valid

    async def _extras(self, generation, config, symbol):
        async def section(path, params):
            try:
                data = await self._json(config, path, params)
                if not self._valid(generation):
                    return
                if path.endswith("depth"):
                    order = data.get("lastUpdateId") if isinstance(data, dict) else None
                    if type(order) is not int or not 0 <= order <= 2**63 - 1:
                        return
                    self._put_book(symbol, parse.book(data, as_of=parse.utc_now().isoformat()), order)
                elif isinstance(data, list) and len(data) <= 40:
                    rows = []
                    for item in data:
                        with suppress(parse.InvalidMarketData):
                            rows.append(parse.trade(item, binance=True))
                    self._put_trades(symbol, rows)
            except Exception:
                return
        await asyncio.gather(section("/api/v3/depth", {"symbol": symbol, "limit": 5}), section("/api/v3/trades", {"symbol": symbol, "limit": 40}))

    async def refresh(self):
        if self._closed:
            return self.snapshot()
        if self._config is None:
            self._load()
        generation, config = self._generation, dict(self._config)
        if not config["enabled"]:
            return self.snapshot()
        try:
            async with asyncio.timeout(config["timeout_seconds"]):
                async with self._refresh_lock:
                    if not self._valid(generation):
                        return self.snapshot()
                    if config["provider"] == "standard_json":
                        payload = await self._json(config, "/snapshot", {"symbols": json.dumps(ALL_SYMBOLS, separators=(",", ":"))})
                        valid = self._apply_standard(payload) if self._valid(generation) else False
                    else:
                        results = await asyncio.gather(*(self._ticker_group(generation, config, symbols) for _, _, symbols in _GROUPS))
                        valid = any(results)
                    if self._valid(generation):
                        # Equal timestamps are valid cached data, but never extend expiry.
                        fresh = any(self._fresh(q["as_of"], self._deadlines.get(("quote", s))) for s, q in self._quotes.items())
                        if valid or fresh:
                            self._error = None
                            if not self._ws_live:
                                self._set_state(mode="polling", status="POLLING", last_error=None)
                            else:
                                self._set_state(last_error=None)
                        else:
                            self._error = "行情来源未返回有效的当前行情"
                            self._set_state(last_error=self._error)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._valid(generation):
                self._error = "无法获取行情，请检查行情源设置或稍后重试"
                self._set_state(last_error=self._error)
        return self.snapshot()

    @_locked
    def _needs_quote_repair(self, config):
        if not self._ws_live:
            return True
        # Coverage belongs to individual symbols. Repair before expiry so one
        # active pair cannot strand the remainder of a partially served stream.
        lead = min(config["poll_seconds"], self.max_age_seconds / 2)
        now, monotonic_now = parse.utc_now().timestamp(), time.monotonic()
        for symbol in ALL_SYMBOLS:
            quote = self._quotes.get(symbol)
            deadline = self._deadlines.get(("quote", symbol), 0)
            if quote is None or not self._fresh(quote["as_of"], deadline):
                return True
            if deadline <= monotonic_now + lead or now - parse.timestamp(quote["as_of"]) + lead >= self.max_age_seconds:
                return True
        return False

    @_locked
    def _extra_repair_symbols(self):
        result = []
        for symbol in self._quotes:
            book = self._books.get(symbol)
            trades = self._trades.get(symbol)
            if (book is None or not self._fresh(book["as_of"], self._deadlines.get(("book", symbol)))
                    or not trades or not self._fresh(trades[0]["as_of"])):
                result.append(symbol)
        return tuple(result)

    async def _poll_loop(self, generation, config):
        bootstrapped = False
        last_extras = 0.0
        while self._valid(generation):
            try:
                quote_repair = not bootstrapped or self._needs_quote_repair(config)
                if quote_repair:
                    await self.refresh()
                if (not bootstrapped or time.monotonic() - last_extras >= _EXTRAS_REPAIR_SECONDS) and self._valid(generation):
                    # Section coverage is independent of quote stream coverage.
                    # Standard JSON's optional sections share its snapshot route.
                    repair_symbols = self._extra_repair_symbols()
                    if config["provider"] == "standard_json":
                        if repair_symbols and not quote_repair:
                            await self.refresh()
                    else:
                        # Extras are best-effort and never block ticker publication.
                        await asyncio.gather(*(self._extras(generation, config, symbol) for symbol in repair_symbols))
                    last_extras = time.monotonic()
                bootstrapped = True
                await asyncio.sleep(config["poll_seconds"])
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._valid(generation):
                    self._set_state(last_error="行情轮询暂不可用")
                await asyncio.sleep(config["poll_seconds"])

    def _streams(self):
        suffixes = ("ticker", "depth5@1000ms", "aggTrade", *("kline_" + interval for interval in parse.INTERVALS))
        return "/".join(symbol.lower() + "@" + suffix for symbol in ALL_SYMBOLS for suffix in suffixes)

    @_locked
    def _apply_binance_ws(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("stream"), str) or not isinstance(payload.get("data"), dict):
            raise parse.InvalidMarketData()
        stream, data = payload["stream"], payload["data"]
        pair = stream.split("@", 1)
        if len(pair) != 2:
            raise parse.InvalidMarketData()
        symbol, kind = parse.symbol(pair[0].upper()), pair[1]
        if pair[0] != symbol.lower():
            raise parse.InvalidMarketData()
        if kind == "depth5@1000ms":
            order = data.get("lastUpdateId")
            if type(order) is not int or not 0 <= order <= 2**63 - 1:
                raise parse.InvalidMarketData()
            if "s" in data and data["s"] != symbol:
                raise parse.InvalidMarketData()
            return self._put_book(symbol, parse.book(data, as_of=parse.utc_now().isoformat()), order)
        if data.get("s") != symbol:
            raise parse.InvalidMarketData()
        event = parse.millis(data.get("E"))
        if not self._fresh(parse.to_iso(event)):
            return False
        if kind == "ticker" and data.get("e") == "24hrTicker":
            item = {"symbol": symbol, "lastPrice": data.get("c"), "priceChangePercent": data.get("P"), "closeTime": data.get("C"),
                    **{dest: data[key] for key, dest in (("o", "openPrice"), ("h", "highPrice"), ("l", "lowPrice"), ("v", "volume"), ("q", "quoteVolume")) if key in data}}
            return self._put_quote(parse.quote(item, binance=True))
        if kind == "aggTrade" and data.get("e") == "aggTrade":
            if type(data.get("a")) is not int or not 0 <= data["a"] <= 2**63 - 1:
                raise parse.InvalidMarketData()
            return self._put_trades(symbol, [parse.trade({"id": "agg:" + str(data.get("a")), "price": data.get("p"), "qty": data.get("q"), "time": data.get("T"), "isBuyerMaker": data.get("m")}, binance=True)])
        if kind.startswith("kline_") and data.get("e") == "kline":
            interval = kind[6:]
            item = data.get("k")
            if not isinstance(item, dict) or item.get("s") != symbol or item.get("i") != interval:
                raise parse.InvalidMarketData()
            metadata = {"closed": item.get("x"), "close_time": item.get("T")} if "x" in item or "T" in item else {}
            candle = parse.candle({"time": item.get("t"), **metadata, **{dest: item.get(key) for key, dest in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume"))}}, interval)
            return self._put_candle(symbol, interval, candle, event)
        raise parse.InvalidMarketData()

    async def _ws_loop(self, generation, config):
        attempt = 0
        while self._valid(generation):
            self._set_state(mode="websocket" if attempt == 0 else "polling", status="CONNECTING" if attempt == 0 else "RECONNECTING", reconnect_attempt=attempt)
            try:
                params = None if config["provider"] == "standard_json" else {"streams": self._streams()}
                async with self._transport.stream(config, params) as stream:
                    iterator = aiter(stream)
                    last_valid = time.monotonic()
                    while self._valid(generation):
                        # A connected but silent socket must eventually reconnect.
                        remaining = self.max_age_seconds - (time.monotonic() - last_valid)
                        if remaining <= 0:
                            raise TimeoutError
                        async with asyncio.timeout(remaining):
                            raw = await anext(iterator)
                        if not self._valid(generation):
                            return
                        try:
                            payload = parse.loads(raw)
                            valid = self._apply_standard(payload) if config["provider"] == "standard_json" else self._apply_binance_ws(payload)
                        except parse.InvalidMarketData:
                            continue
                        if valid:
                            last_valid = time.monotonic()
                            self._ws_live = True
                            self._ws_event_mono = time.monotonic()
                            self._error = None
                            self._set_state(mode="websocket", status="LIVE", last_event_at=parse.utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z"), last_error=None, reconnect_attempt=0)
                            attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if not self._valid(generation):
                return
            self._ws_live = False
            attempt += 1
            self._set_state(mode="polling", status="RECONNECTING", reconnect_attempt=attempt, last_error="行情流已断开，正在重连")
            await asyncio.sleep(min(30, 2 ** min(attempt - 1, 5)))

    async def candles(self, symbol, interval, limit=120):
        try:
            parse.symbol(symbol)
            if interval not in parse.INTERVALS or type(limit) is not int or not 1 <= limit <= 200:
                raise parse.InvalidMarketData()
        except (parse.InvalidMarketData, TypeError) as exc:
            raise ApiError("MARKET_INPUT", "交易对、周期或数量无效") from exc
        if self._config is None:
            self._load()
        generation, config = self._generation, dict(self._config)
        key = (symbol, interval)
        if self._closed or not config["enabled"]:
            return self._history_result(symbol, interval, limit)
        if key in self._history and time.monotonic() - self._history_at.get(key, 0) < config["poll_seconds"] and self._history_limits.get(key, 0) >= limit:
            return self._history_result(symbol, interval, limit)
        self._history_request_number += 1
        request_number = self._history_request_number
        request_started_ms = int(parse.utc_now().timestamp() * 1000)
        try:
            if config["provider"] == "standard_json":
                payload = await self._json(config, "/candles", {"symbol": symbol, "interval": interval, "limit": limit})
                if not isinstance(payload, dict) or payload.get("symbol") != symbol or payload.get("interval") != interval:
                    raise parse.InvalidMarketData()
                rows = payload.get("candles")
            else:
                rows = await self._json(config, "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
            if not self._valid(generation):
                return self._history_result(symbol, interval, limit)
            if request_number < self._history_requests.get(key, 0):
                return self._history_result(symbol, interval, limit)
            if not isinstance(rows, list) or len(rows) > limit:
                raise parse.InvalidMarketData()
            parsed = [parse.candle(row, interval) if config["provider"] == "standard_json" else parse.kline(row, interval) for row in rows]
            if any(a["time"] >= b["time"] for a, b in zip(parsed, parsed[1:])):
                raise parse.InvalidMarketData()
            # Never let an old in-flight REST history roll back newer WS candles.
            if parsed:
                self._put_candle(symbol, interval, parsed[-1], request_started_ms if config["provider"] != "standard_json" else None)
            latest = self._candles.get(symbol, {}).get(interval)
            merged = {row["time"]: row for row in parsed}
            if latest and (not parsed or latest["time"] >= parsed[0]["time"]):
                merged[latest["time"]] = latest
            self._history[key] = sorted(merged.values(), key=lambda row: row["time"])[-200:]
            self._history_at[key] = time.monotonic()
            self._history_requests[key] = request_number
            self._history_limits[key] = limit
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._valid(generation):
                return self._history_result(symbol, interval, limit)
            if key not in self._history:
                raise ApiError("MARKET_HISTORY", "暂时无法获取真实历史行情", 502) from exc
        return self._history_result(symbol, interval, limit)

    @_locked
    def _history_result(self, symbol, interval, limit):
        return {"symbol": symbol, "interval": interval, "source_revision": self._config["revision"] if self._config else 0,
                "source_id": self._instance, "source_generation": self._generation, "venue": "BINANCE", "product": "SPOT",
                "candles": copy.deepcopy(self._history.get((symbol, interval), [])[-limit:]), "source": self._source()}

    async def test_connection(self, expected_revision):
        config = dict(self._service.runtime_config())
        if type(expected_revision) is not int or expected_revision != config["revision"]:
            raise ApiError("MARKET_REVISION", "行情源设置已变化，请重新加载", 409)
        if not config["enabled"]:
            return {"ok": False, "revision": config["revision"], "message": "行情源已禁用", "quote_count": 0}
        if self._transport is None:
            from .market_transport import MarketTransport
            self._transport = MarketTransport()
        count = 0
        try:
            async with asyncio.timeout(config["timeout_seconds"]):
                standard = config["provider"] == "standard_json"
                if standard:
                    payload = await self._json(config, "/snapshot", {"symbols": json.dumps(ALL_SYMBOLS)})
                    rows = payload.get("quotes") if isinstance(payload, dict) else None
                else:
                    async def selected(symbols):
                        try:
                            value = await self._json(config, "/api/v3/ticker/24hr", {"symbols": json.dumps(symbols), "type": "FULL"})
                            if isinstance(value, list) and len(value) <= len(symbols):
                                return [item for item in value if isinstance(item, dict) and item.get("symbol") in symbols]
                            return []
                        except Exception:
                            if len(symbols) > 1:
                                groups = await asyncio.gather(*(selected((symbol,)) for symbol in symbols))
                                return [item for group in groups for item in group]
                            return []
                    batches = await asyncio.gather(*(selected(symbols) for _, _, symbols in _GROUPS))
                    rows = [item for batch in batches for item in batch]
                if isinstance(rows, list) and len(rows) <= 100:
                    symbols = set()
                    for item in rows:
                        with suppress(parse.InvalidMarketData):
                            quote = parse.quote(item, binance=not standard)
                            if self._fresh(quote["as_of"]):
                                symbols.add(quote["symbol"])
                    count = len(symbols)
        except Exception:
            pass
        if self._service.get_config()["revision"] != expected_revision:
            raise ApiError("MARKET_REVISION", "行情源设置已变化，请重新加载", 409)
        return {"ok": count > 0, "revision": config["revision"], "message": "行情源连接成功" if count else "未收到有效的当前行情，请检查设置", "quote_count": count}

    async def close(self):
        if self._closed:
            return
        self._closed = True
        self._started = False
        self._generation += 1
        tasks = tuple(self._tasks | self._retired)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._retired.clear()
        self._ws_live = False
        self._set_state(mode="disabled", status="DISABLED")
        if self._owns_transport and self._transport is not None:
            await self._transport.close()
