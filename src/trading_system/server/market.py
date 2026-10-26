"""Bounded, credential-free public spot quotes for the local PAPER console.

Binance documents this host as market-data-only (no account or order APIs):
https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from .instruments import BSTOCK_SYMBOLS, CRYPTO_SYMBOLS

_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"
_SOURCE = "Binance Spot (data-api.binance.vision)"
_MAX_BODY_BYTES = 32768
_DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    price: str
    change_pct: str
    as_of: str
    source: str
    stale: bool


class _InvalidData(ValueError):
    """Only static, safe messages from this module may reach the UI."""


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidData("行情响应包含重复字段")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise _InvalidData("行情响应包含无效数字")


def _decimal_string(value: object, *, positive: bool) -> str:
    # Bound both the input and its arithmetic cost. Binance uses plain decimal
    # strings, so exponent notation, whitespace and JSON floats are unnecessary.
    if not isinstance(value, str) or len(value) > 64 or not _DECIMAL.fullmatch(value):
        raise _InvalidData("行情价格或涨跌幅格式无效")
    number = Decimal(value)
    if not number.is_finite() or (positive and number <= 0):
        raise _InvalidData("行情价格必须为正数，涨跌幅必须为有限数字")
    return value


class _QuoteGroup:
    """Offline construction; only refresh performs a fixed public GET.

    Failed refreshes invalidate the entire previous batch. An old quote remains
    visible for reference, but its ``stale`` flag cannot be cleared until a new
    complete batch passes validation. ``last_updated`` is the oldest source
    timestamp in that batch, never a fabricated local observation timestamp.
    """

    def __init__(
        self, timeout: float = 6.0, max_age_seconds: float = 90,
        client: httpx.AsyncClient | None = None,
        *, symbols: tuple[str, ...] = CRYPTO_SYMBOLS,
    ) -> None:
        for name, value in (("timeout", timeout), ("max_age_seconds", max_age_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        self.timeout = float(timeout)
        self._symbols = symbols
        self.max_age_seconds = float(max_age_seconds)
        self._client = client
        self._owns_client = client is None
        self._closed = False
        self._lock = asyncio.Lock()
        self._quotes: tuple[Quote, ...] = ()
        self._deadlines: dict[str, float] = {}
        self._error: str | None = "尚未获取公开行情，请刷新"

    def snapshot(self) -> dict:
        """Return detached JSON values and recheck expiry without any I/O."""
        now, monotonic_now = _utc_now(), time.monotonic()
        quotes = []
        for quote in self._quotes:
            age = (now - datetime.fromisoformat(quote.as_of)).total_seconds()
            stale = (
                self._error is not None or not 0 <= age <= self.max_age_seconds
                or monotonic_now > self._deadlines[quote.symbol]
            )
            quotes.append(asdict(replace(quote, stale=stale)))
        stale = any(quote["stale"] for quote in quotes)
        return {
            "source": _SOURCE,
            "status": "UNAVAILABLE" if not quotes else "STALE" if stale else "CONNECTED",
            "last_updated": min((quote.as_of for quote in self._quotes), default=None),
            "error": self._error or ("行情已过期，请刷新后再执行模拟交易" if stale else None),
            "quotes": quotes,
        }

    async def refresh(self) -> dict:
        """Fetch atomically; bound connection, streaming and lock wait time."""
        try:
            async with asyncio.timeout(self.timeout):
                async with self._lock:
                    if self._closed:
                        raise _InvalidData("行情服务已关闭")
                    if self._client is None:
                        # Ignore proxy/auth/certificate environment configuration.
                        # No credential lookup or exchange trading client exists.
                        self._client = httpx.AsyncClient(
                            timeout=self.timeout, follow_redirects=False, trust_env=False,
                            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                        )
                    quotes = await self._fetch()
                    now, monotonic_now = _utc_now(), time.monotonic()
                    self._deadlines = {
                        quote.symbol: monotonic_now + self.max_age_seconds
                        - (now - datetime.fromisoformat(quote.as_of)).total_seconds()
                        for quote in quotes
                    }
                    self._quotes = quotes
                    self._error = None
        except (TimeoutError, httpx.TimeoutException):
            self._error = "公开行情请求超时，请稍后重试"
        except _InvalidData as exc:
            self._error = str(exc)
        except (httpx.HTTPError, RuntimeError):
            # Never expose exception strings: proxy URLs and server bodies may
            # contain credentials, arbitrary markup or misleading instructions.
            self._error = "无法连接公开行情服务，请检查网络后重试"
        return self.snapshot()

    async def _fetch(self) -> tuple[Quote, ...]:
        assert self._client is not None
        # Building the Request directly avoids inheriting injected client auth
        # headers, cookies, arbitrary query parameters or a different base URL.
        request = httpx.Request(
            "GET", _URL,
            params={"symbols": json.dumps(self._symbols, separators=(",", ":")), "type": "FULL"},
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            extensions={"timeout": httpx.Timeout(self.timeout).as_dict()},
        )
        response = await self._client.send(request, stream=True, auth=None, follow_redirects=False)
        try:
            if response.status_code != 200:
                raise _InvalidData(f"公开行情服务返回 HTTP {response.status_code}，请稍后重试")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise _InvalidData("公开行情服务返回不支持的压缩响应")
            length = response.headers.get("content-length")
            if length is not None and (not length.isascii() or not length.isdecimal() or len(length) > 10 or int(length) > _MAX_BODY_BYTES):
                raise _InvalidData("公开行情响应大小无效或超过上限")
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if len(body) + len(chunk) > _MAX_BODY_BYTES:
                    raise _InvalidData("公开行情响应超过大小上限")
                body.extend(chunk)
            try:
                payload = json.loads(
                    body, object_pairs_hook=_json_object, parse_constant=_invalid_constant,
                )
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise _InvalidData("公开行情响应不是有效 JSON 数据") from exc
            return self._parse(payload)
        finally:
            await response.aclose()

    def _parse(self, payload: object) -> tuple[Quote, ...]:
        if not isinstance(payload, list) or len(payload) != len(self._symbols):
            raise _InvalidData("公开行情响应缺少完整的指定交易对")
        now = _utc_now()
        quotes = {}
        for item in payload:
            if not isinstance(item, dict):
                raise _InvalidData("公开行情响应结构无效")
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or symbol not in self._symbols or symbol in quotes:
                raise _InvalidData("公开行情响应包含错误或重复的交易对")
            price = _decimal_string(item.get("lastPrice"), positive=True)
            change = _decimal_string(item.get("priceChangePercent"), positive=False)
            timestamp = item.get("closeTime")
            if type(timestamp) is not int or not 0 < timestamp < 253402300800000:
                raise _InvalidData("公开行情来源时间戳无效")
            seconds, milliseconds = divmod(timestamp, 1000)
            as_of = datetime.fromtimestamp(seconds, timezone.utc) + timedelta(milliseconds=milliseconds)
            age = (now - as_of).total_seconds()
            if age < 0:
                raise _InvalidData("公开行情来源时间在未来，请检查系统时钟")
            if age > self.max_age_seconds:
                raise _InvalidData("公开行情来源数据已过期，请稍后刷新")
            quotes[symbol] = Quote(
                symbol, price, change, as_of.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                _SOURCE, False,
            )
        return tuple(quotes[symbol] for symbol in self._symbols)

    async def close(self) -> None:
        """Close only resources owned by this feed; injected clients stay open."""
        async with self._lock:
            self._closed = True
            self._error = "行情服务已关闭"
            if self._owns_client and self._client is not None:
                await self._client.aclose()


class MarketFeed:
    """Independent bounded batches; default retains the crypto-only protocol.

    Each group owns its cache, request deadline, lock and (unless injected)
    HTTP client. A slow or invalid group cannot invalidate another group's
    fresh quotes. No network I/O occurs until an explicit refresh.
    """

    def __init__(
        self, timeout: float = 6.0, max_age_seconds: float = 90,
        client: httpx.AsyncClient | None = None, *, include_bstocks: bool = False,
    ) -> None:
        self._groups = [("crypto", "加密货币", _QuoteGroup(timeout, max_age_seconds, client))]
        if include_bstocks:
            self._groups.append(("bstocks", "Binance bStocks", _QuoteGroup(
                timeout, max_age_seconds, client, symbols=BSTOCK_SYMBOLS,
            )))

    def snapshot(self) -> dict:
        snapshots = [group.snapshot() for _, _, group in self._groups]
        if len(snapshots) == 1:
            return snapshots[0]
        quotes = [quote for snapshot in snapshots for quote in snapshot["quotes"]]
        statuses = [snapshot["status"] for snapshot in snapshots]
        if all(status == "CONNECTED" for status in statuses):
            status = "CONNECTED"
        elif "CONNECTED" in statuses:
            status = "PARTIAL"
        else:
            status = "STALE" if quotes else "UNAVAILABLE"
        groups = [
            {"id": group_id, "label": label, **{
                key: snapshot[key] for key in ("status", "last_updated", "error")
            }}
            for (group_id, label, _), snapshot in zip(self._groups, snapshots)
        ]
        return {
            "source": _SOURCE, "status": status,
            "last_updated": min((q["as_of"] for q in quotes), default=None),
            "error": "; ".join(f"{g['label']}: {g['error']}" for g in groups if g["error"]) or None,
            "quotes": quotes, "groups": groups,
        }

    async def refresh(self) -> dict:
        await asyncio.gather(*(group.refresh() for _, _, group in self._groups))
        return self.snapshot()

    async def close(self) -> None:
        await asyncio.gather(*(group.close() for _, _, group in self._groups))
