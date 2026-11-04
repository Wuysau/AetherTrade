"""Explicit, isolated USD-M Demo read-only HTTP client; never qualifies execution.

The official USD-M docs label this host Testnet. Its use here is limited to the
user's task-specific code exception. This client is not wired into the runtime,
and its existence does not authorize a connection or prove Demo compatibility.
Instances should not be shared across tasks or threads. Rate-limit state is
per instance, not shared across processes or an IP-wide qualification.
"""
import asyncio
import json
import math
import time
from types import MappingProxyType
from urllib.parse import urlencode

import httpx

from .contracts import DemoError
from .transport import _demo_in_flight


# A name is selected internally, not a URL supplied by a caller. There is no
# write, generic request, alternate host, or caller-defined query interface.
_ENDPOINTS = MappingProxyType({
    'time': ('https://demo-fapi.binance.com/fapi/v1/time', False, dict),
    'account': ('https://demo-fapi.binance.com/fapi/v3/account', True, dict),
    'position_mode': ('https://demo-fapi.binance.com/fapi/v1/positionSide/dual', True, dict),
    'symbol_configs': ('https://demo-fapi.binance.com/fapi/v1/symbolConfig', True, list),
    'position_risk': ('https://demo-fapi.binance.com/fapi/v3/positionRisk', True, list),
})
_MAX_RESPONSE_BYTES = 2_000_000
_TOTAL_DEADLINE_SECONDS = 10


def _reject_constant(_):
    raise ValueError('invalid JSON number')


class UsdmDemoReadOnly:
    def __init__(self, credentials, credential_ref, *, http_transport=None, clock=time.time, monotonic_clock=time.monotonic):
        self.credentials, self.credential_ref = credentials, credential_ref
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self.offset_ms = None
        self.synced_at = None
        self.synced_wall = None
        self.blocked_until = 0
        self.banned = False
        self._client = httpx.AsyncClient(transport=http_transport, timeout=httpx.Timeout(5, connect=5),
                                    follow_redirects=False, verify=True, trust_env=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        await self._client.aclose()

    def _now(self):
        value = self.clock()
        if type(value) not in (int, float) or not 0 <= value <= 2**53 or not math.isfinite(value):
            raise DemoError('DEMO_CLOCK_UNCERTAIN')
        return value

    def _steady(self):
        value = self.monotonic_clock()
        if type(value) not in (int, float) or not 0 <= value <= 2**53 or not math.isfinite(value):
            raise DemoError('DEMO_CLOCK_UNCERTAIN')
        return value

    async def _get(self, endpoint):
        if not isinstance(endpoint, str) or endpoint not in _ENDPOINTS:
            raise DemoError('DEMO_ENDPOINT_REJECTED')
        url, signed, expected_type = _ENDPOINTS[endpoint]
        if self.banned:
            raise DemoError('DEMO_IP_BANNED')
        now = self._now()
        started = self._steady()
        if started < self.blocked_until:
            raise DemoError('DEMO_RATE_LIMITED')
        headers, query = {'Accept-Encoding': 'identity'}, ''
        if signed:
            if (self.synced_at is None or not 0 <= started - self.synced_at <= 60
                    or abs((now - self.synced_wall) - (started - self.synced_at)) > 0.1):
                self.offset_ms = self.synced_at = self.synced_wall = None
                raise DemoError('DEMO_TIME_SYNC_REQUIRED')
            query = urlencode(sorted({'timestamp': int(now * 1000 + self.offset_ms), 'recvWindow': 5000}.items()))
            headers['X-MBX-APIKEY'] = self.credentials.api_key(self.credential_ref)
            query += '&signature=' + self.credentials.sign(self.credential_ref, query)
        token = _demo_in_flight.set(True)
        try:
            async with asyncio.timeout(_TOTAL_DEADLINE_SECONDS):
                return await self._read_response(url, query, headers, started, expected_type)
        except (httpx.HTTPError, TimeoutError):
            raise DemoError('DEMO_UNAVAILABLE') from None
        finally:
            _demo_in_flight.reset(token)

    async def _read_response(self, url, query, headers, started, expected_type):
        async with self._client.stream('GET', url, params=query, headers=headers) as response:
            status = response.status_code
            if 300 <= status < 400:
                raise DemoError('DEMO_REDIRECT_BLOCKED')
            if status in (418, 429):
                # Fail closed if the server delay cannot be safely represented.
                self.blocked_until = math.inf
                self.banned = status == 418
                retry = response.headers.get('Retry-After')
                if retry is not None and retry.isascii() and retry.isdigit() and len(retry) <= 10 and int(retry) <= 2**31:
                    retry = max(1, int(retry))
                    self.blocked_until = math.nextafter(self._steady() + retry, math.inf)
                else:
                    retry = None
                raise DemoError('DEMO_IP_BANNED' if self.banned else 'DEMO_RATE_LIMITED', retry_after=retry)
            if self._steady() - started > _TOTAL_DEADLINE_SECONDS:
                raise DemoError('DEMO_UNAVAILABLE')
            if status >= 500:
                raise DemoError('DEMO_UNAVAILABLE')
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise DemoError('DEMO_RESPONSE_INVALID')
            data = bytearray()
            async for chunk in response.aiter_raw():
                if self._steady() - started > _TOTAL_DEADLINE_SECONDS:
                    raise DemoError('DEMO_UNAVAILABLE')
                if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise DemoError('DEMO_RESPONSE_INVALID')
                data.extend(chunk)
            try:
                payload = json.loads(data, parse_constant=_reject_constant)
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise DemoError('DEMO_RESPONSE_INVALID') from None
            if status >= 400:
                code = payload.get('code') if isinstance(payload, dict) else None
                if type(code) is not int:
                    code = None
                reason = {-1021:'DEMO_CLOCK_DRIFT', -1022:'DEMO_SIGNATURE_REJECTED',
                          -2015:'DEMO_CREDENTIAL_REJECTED'}.get(code, 'DEMO_REQUEST_REJECTED')
                if code == -1021:
                    self.offset_ms = self.synced_at = self.synced_wall = None
                raise DemoError(reason)
            if not isinstance(payload, expected_type):
                raise DemoError('DEMO_RESPONSE_INVALID')
            return payload
    async def sync_time(self):
        # A failed re-sync cannot leave a previous offset usable.
        self.offset_ms = self.synced_at = self.synced_wall = None
        before = self._now()
        before_steady = self._steady()
        data = await self._get('time')
        after = self._now()
        after_steady = self._steady()
        elapsed = after_steady - before_steady
        server = data.get('serverTime')
        if (type(server) is not int or not 0 <= server < 2**63 or not 0 <= elapsed <= 2
                or abs((after - before) - elapsed) > 0.1):
            raise DemoError('DEMO_CLOCK_UNCERTAIN')
        offset = server - (before + after) * 500
        if not math.isfinite(offset) or abs(offset) > 5000:
            raise DemoError('DEMO_CLOCK_DRIFT')
        self.offset_ms, self.synced_at, self.synced_wall = offset, after_steady, after
        return {'offset_ms': round(offset), 'round_trip_ms': round(elapsed * 1000)}

    async def account(self):
        return await self._get('account')

    async def position_mode(self):
        return await self._get('position_mode')

    async def symbol_configs(self):
        return await self._get('symbol_configs')

    async def position_risk(self):
        return await self._get('position_risk')
