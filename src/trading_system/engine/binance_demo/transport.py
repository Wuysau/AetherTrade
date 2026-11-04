"""Single-attempt Demo HTTP transport, with no production or proxy fallback."""
import time
import logging
from contextvars import ContextVar
from urllib.parse import urlencode
import httpx

from .contracts import DemoEndpointRegistry, DemoError


_demo_in_flight = ContextVar('demo_http_in_flight', default=False)


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record):
        # HTTPX logs complete signed URLs; httpcore debug can include headers.
        # Suppress only this execution context, leaving unrelated HTTP visible.
        return not _demo_in_flight.get()


for _logger in ('httpx', 'httpcore.connection', 'httpcore.http11', 'httpcore.http2', 'httpcore.proxy', 'httpcore.socks'):
    logging.getLogger(_logger).addFilter(_PrivateRequestFilter())


class BinanceDemoTransport:
    def __init__(self, credentials, credential_ref, *, http_transport=None, clock=time.time):
        self.credentials, self.credential_ref = credentials, credential_ref
        self.clock = clock
        self.offset_ms = None
        self.synced_at = None
        self.blocked_until = 0
        self.banned = False
        self._client = httpx.Client(transport=http_transport, timeout=httpx.Timeout(10, connect=5),
                                   follow_redirects=False, verify=True, trust_env=False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._client.close()

    def _request(self, method, path, params=None, *, signed=False):
        url = DemoEndpointRegistry.url('SPOT', method, path)
        if self.banned:
            raise DemoError('DEMO_IP_BANNED')
        if self.clock() < self.blocked_until:
            raise DemoError('DEMO_RATE_LIMITED')
        params = dict(params or {})
        if any(k in params for k in ('signature', 'apiKey', 'timestamp', 'recvWindow')):
            raise DemoError('DEMO_RESERVED_PARAMETER')
        headers = {}
        if signed:
            if self.synced_at is None or not 0 <= self.clock() - self.synced_at <= 60:
                raise DemoError('DEMO_TIME_SYNC_REQUIRED')
            params.update(timestamp=int(self.clock() * 1000 + self.offset_ms), recvWindow=5000)
            headers['X-MBX-APIKEY'] = self.credentials.api_key(self.credential_ref)
        query = urlencode(sorted(params.items()))
        if signed:
            query += '&signature=' + self.credentials.sign(self.credential_ref, query)
        logging_token = _demo_in_flight.set(True)
        try:
            response = self._client.request(method, url, params=query, headers=headers)
        except httpx.HTTPError:
            raise DemoError('DEMO_TRANSPORT_UNKNOWN' if method != 'GET' else 'DEMO_UNAVAILABLE') from None
        finally:
            _demo_in_flight.reset(logging_token)
        if 300 <= response.status_code < 400:
            raise DemoError('DEMO_REDIRECT_BLOCKED')
        if response.status_code in {418, 429}:
            retry = response.headers.get('Retry-After', '60')
            retry = min(3600, max(1, int(retry))) if retry.isdigit() else 60
            self.blocked_until = self.clock() + retry
            self.banned = response.status_code == 418
            raise DemoError('DEMO_IP_BANNED' if self.banned else 'DEMO_RATE_LIMITED', retry_after=retry)
        if response.status_code >= 500:
            raise DemoError('DEMO_TRANSPORT_UNKNOWN' if method != 'GET' else 'DEMO_UNAVAILABLE')
        if len(response.content) > 2_000_000:
            raise DemoError('DEMO_RESPONSE_INVALID')
        try:
            data = response.json()
        except ValueError:
            raise DemoError('DEMO_RESPONSE_INVALID') from None
        if response.status_code >= 400:
            code = data.get('code') if isinstance(data, dict) else None
            # No error message or response URL is ever propagated.
            reason = {-1021: 'DEMO_CLOCK_DRIFT', -1022: 'DEMO_SIGNATURE_REJECTED', -2015: 'DEMO_CREDENTIAL_REJECTED', -2013: 'DEMO_ORDER_NOT_FOUND'}.get(code, 'DEMO_REQUEST_REJECTED')
            raise DemoError(reason)
        if not isinstance(data, (dict, list)):
            raise DemoError('DEMO_RESPONSE_INVALID')
        return data

    def sync_time(self):
        before = self.clock()
        data = self._request('GET', '/api/v3/time')
        after = self.clock()
        server = data.get('serverTime') if isinstance(data, dict) else None
        if type(server) is not int or after - before > 2 or after < before:
            raise DemoError('DEMO_CLOCK_UNCERTAIN')
        offset = server - (before + after) * 500
        if abs(offset) > 5000:
            raise DemoError('DEMO_CLOCK_DRIFT')
        self.offset_ms, self.synced_at = offset, after
        return {'offset_ms': round(offset), 'round_trip_ms': round((after - before) * 1000)}

    def account(self):
        return self._request('GET', '/api/v3/account', signed=True)

    def exchange_info(self, symbol='BTCUSDT'):
        return self._request('GET', '/api/v3/exchangeInfo', {'symbol': symbol})

    def open_orders(self, symbol='BTCUSDT'):
        return self._request('GET', '/api/v3/openOrders', {'symbol': symbol}, signed=True)

    def query_order(self, symbol, original_client_id):
        return self._request('GET', '/api/v3/order', {'symbol': symbol, 'origClientOrderId': original_client_id}, signed=True)

    def trades(self, symbol, *, from_id=None, limit=500):
        if type(limit) is not int or not 1 <= limit <= 1000 or (from_id is not None and (type(from_id) is not int or from_id < 0)):
            raise DemoError('DEMO_CURSOR_INVALID')
        params = {'symbol': symbol, 'limit': limit}
        if from_id is not None:
            params['fromId'] = from_id
        return self._request('GET', '/api/v3/myTrades', params, signed=True)

    def book_ticker(self, symbol='BTCUSDT'):
        return self._request('GET', '/api/v3/ticker/bookTicker', {'symbol': symbol})

    def send_order(self, params, *, permit):
        from trading_system.execution.demo_commands import DemoSendPermit
        if type(permit) is not DemoSendPermit:
            raise DemoError('DEMO_DURABLE_PERMIT_REQUIRED')
        permit.consume('POST', params, self.credential_ref)
        return self._request('POST', '/api/v3/order', params, signed=True)

    def cancel_order(self, params, *, permit):
        from trading_system.execution.demo_commands import DemoSendPermit
        if type(permit) is not DemoSendPermit:
            raise DemoError('DEMO_DURABLE_PERMIT_REQUIRED')
        permit.consume('DELETE', params, self.credential_ref)
        return self._request('DELETE', '/api/v3/order', params, signed=True)

    def subscription(self, request_id):
        if self.synced_at is None or not 0 <= self.clock() - self.synced_at <= 60:
            raise DemoError('DEMO_TIME_SYNC_REQUIRED')
        params = {'apiKey': self.credentials.api_key(self.credential_ref), 'timestamp': int(self.clock() * 1000 + self.offset_ms), 'recvWindow': 5000}
        params['signature'] = self.credentials.sign(self.credential_ref, urlencode(sorted(params.items())))
        return {'id': request_id, 'method': 'userDataStream.subscribe.signature', 'params': params}
