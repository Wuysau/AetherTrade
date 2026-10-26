"""Deterministic DNS/HTTP/WebSocket mocks; no external network or real keys."""
import asyncio
from contextlib import asynccontextmanager
import socket
from types import SimpleNamespace

import httpx
import httpcore
import pytest
from websockets.asyncio.client import connect
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from trading_system.server.errors import ApiError
from trading_system.server.market_config import DEFAULT
from trading_system.server.market_transport import MAX_HTTP_BODY, MAX_WS_MESSAGE, MarketTransport


def config(**changes):
    return dict(DEFAULT, api_key='fixture-key', auth_scheme='bearer', **changes)


def dns(monkeypatch, addresses, calls=None):
    async def lookup(host, port, **kwargs):
        if calls is not None:
            calls.append((host, port, kwargs))
        return [(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM, 6, '',
                 (address, port, 0, 0) if ':' in address else (address, port)) for address in addresses]
    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', lookup)


def test_http_pins_ip_preserves_host_sni_and_has_no_inherited_auth_cookies(monkeypatch):
    async def run():
        dns_calls, requests = [], []
        dns(monkeypatch, ['93.184.216.34'], dns_calls)
        def handle(request):
            requests.append(request)
            assert request.url.host == '93.184.216.34'
            assert request.headers['host'] == 'data-api.binance.vision'
            assert request.extensions['sni_hostname'] == 'data-api.binance.vision'
            assert request.headers['authorization'] == 'Bearer fixture-key'
            assert 'cookie' not in request.headers and 'x-inherited' not in request.headers
            assert request.url.path == '/api/v3/ticker/24hr'
            assert request.url.params['symbols'] == '["BTCUSDT"]'
            return httpx.Response(200, json=[{'symbol': 'BTCUSDT'}], headers={'Set-Cookie': 'upstream=ignored'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle),
                                     auth=('inherited', 'secret'), cookies={'inherited': 'secret'},
                                     headers={'X-Inherited': 'secret'}) as client:
            transport = MarketTransport(client=client)
            for _ in range(2):
                assert await transport.get_json(config(), '/api/v3/ticker/24hr', {'symbols': '["BTCUSDT"]'}) == [{'symbol': 'BTCUSDT'}]
            await transport.close()
            assert not client.is_closed
        assert len(requests) == len(dns_calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize('hostname', ['data-api.binance.vision', 'xn--bcher-kva.example'])
def test_real_httpcore_tls_backend_receives_hostname_string_and_pinned_tcp_ip(monkeypatch, hostname):
    """Exercise HTTPX's actual HTTP transport; MockTransport skips TLS entirely."""
    async def run():
        dns(monkeypatch, ['93.184.216.34'])
        connections, handshakes, writes = [], [], []

        class Stream(httpcore.AsyncNetworkStream):
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                handshakes.append(server_hostname)
                # anyio's IDNA resolver requires str and calls .encode() here.
                assert isinstance(server_hostname, str)
                assert server_hostname.encode('ascii') == hostname.encode('ascii')
                return self

            async def write(self, buffer, timeout=None):
                writes.append(buffer)

            async def read(self, max_bytes, timeout=None):
                return b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}'

            async def aclose(self):
                pass

            def get_extra_info(self, info):
                return None

        class Backend(httpcore.AsyncNetworkBackend):
            async def connect_tcp(self, host, port, **kwargs):
                connections.append((host, port))
                return Stream()

        http_transport = httpx.AsyncHTTPTransport(trust_env=False)
        # Only replace the socket boundary. HTTPX/h11/httpcore execute normally.
        http_transport._pool = httpcore.AsyncConnectionPool(network_backend=Backend(),
                                                          max_keepalive_connections=0)
        async with httpx.AsyncClient(transport=http_transport) as client:
            source = config()
            source['rest_url'] = 'https://' + hostname
            assert await MarketTransport(client=client).get_json(source, '/snapshot') == {}
        assert connections == [('93.184.216.34', 443)]
        assert handshakes == [hostname]
        assert b'Host: ' + hostname.encode('ascii') + b'\r\n' in b''.join(writes)
    asyncio.run(run())


@pytest.mark.parametrize('local,addresses', [(False, ['127.0.0.1']), (False, ['93.184.216.34', '10.0.0.1']),
                                           (False, ['224.0.0.1']), (False, ['ff02::1']),
                                           (False, []), (True, ['93.184.216.34']), (True, ['127.0.0.1', '10.0.0.1'])])
def test_every_dns_answer_is_validated_before_http_and_ws(monkeypatch, local, addresses):
    async def run():
        dns(monkeypatch, addresses)
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(200, json={})
        def ws(*args, **kwargs):
            calls.append(args)
            raise AssertionError('DNS rejection must happen before connection')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            transport = MarketTransport(client=client, ws_connect=ws)
            source = config()
            if local:
                source.update(local_mode=True, rest_url='http://localhost:19876', ws_url='ws://localhost:19876/feed')
            with pytest.raises(ApiError) as error:
                await transport.get_json(source, '/snapshot')
            assert error.value.code == 'MARKET_INVALID_URL'
            with pytest.raises(ApiError) as error:
                async with transport.stream(source):
                    pass
            assert error.value.code == 'MARKET_INVALID_URL'
        assert calls == []
    asyncio.run(run())


def test_dns_rebinding_is_rechecked_for_every_request(monkeypatch):
    async def run():
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(200, json={}))) as client:
            transport = MarketTransport(client=client)
            dns(monkeypatch, ['93.184.216.34'])
            await transport.get_json(config(), '/snapshot')
            dns(monkeypatch, ['169.254.169.254'])
            with pytest.raises(ApiError):
                await transport.get_json(config(), '/snapshot')
        assert len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize('status', [301, 302, 307, 308, 401, 500])
def test_http_never_follows_redirect_or_exposes_provider_body(monkeypatch, status):
    async def run():
        dns(monkeypatch, ['93.184.216.34'])
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(status, text='fixture-key private-provider-body', headers={'Location': 'https://other.example.com/secret'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
            with pytest.raises(ApiError) as error:
                await MarketTransport(client=client).get_json(config(), '/snapshot')
        assert error.value.code == 'MARKET_PROVIDER_ERROR' and len(calls) == 1
        assert 'fixture-key' not in error.value.message and 'private-provider-body' not in error.value.message
    asyncio.run(run())


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.parametrize('body,headers', [
    (b'{"a":1,"a":2}', {}), (b'{"a":NaN}', {}), (b'{"a":1e999}', {}),
    (b'42', {}), (b'null', {}), (b'{"a":"\xff"}', {}),
    (b'{}', {'Content-Encoding': 'gzip'}), (b'{}', {'Content-Length': str(MAX_HTTP_BODY + 1)}),
    (b'{}', {'Content-Length': '-1'}), (b'[' + b' ' * MAX_HTTP_BODY + b']', {}),
], ids=['duplicate-key', 'nan', 'overflow', 'number', 'null', 'invalid-utf8', 'compressed',
        'declared-oversized', 'invalid-length', 'streamed-oversized'])
def test_http_body_is_bounded_strict_utf8_json(monkeypatch, body, headers):
    async def run():
        dns(monkeypatch, ['93.184.216.34'])
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=Chunks([body[:10], body[10:]]), headers=headers))) as client:
            with pytest.raises(ApiError) as error:
                await MarketTransport(client=client).get_json(config(), '/snapshot')
        assert error.value.code == 'MARKET_INVALID_RESPONSE'
    asyncio.run(run())


@pytest.mark.parametrize('scheme,header', [('none', None), ('bearer', 'authorization'), ('x_api_key', 'x-api-key')])
def test_auth_is_sent_only_in_selected_scheme_to_selected_destination(monkeypatch, scheme, header):
    async def run():
        dns(monkeypatch, ['127.0.0.1'])
        def handle(request):
            assert request.url.host == '127.0.0.1' and request.headers['host'] == 'localhost:19876'
            assert ('authorization' in request.headers) == (header == 'authorization')
            assert ('x-api-key' in request.headers) == (header == 'x-api-key')
            return httpx.Response(200, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            source = config()
            source.update(rest_url='http://localhost:19876', local_mode=True, auth_scheme=scheme)
            await MarketTransport(client=client).get_json(source, '/snapshot')
    asyncio.run(run())


def test_websocket_pins_destination_keeps_endpoint_and_bounds_options(monkeypatch):
    async def run():
        dns(monkeypatch, ['93.184.216.34'])
        calls, closed = [], []
        @asynccontextmanager
        async def ws(endpoint, **kwargs):
            calls.append((endpoint, kwargs))
            async def messages():
                yield '{"quotes":[]}'
                yield b'{"quotes":[]}'
            try:
                yield messages()
            finally:
                closed.append(True)
        transport = MarketTransport(ws_connect=ws)
        try:
            async with transport.stream(config(), {'streams': 'btcusdt@ticker/ethusdt@ticker'}) as messages:
                assert [value async for value in messages] == ['{"quotes":[]}', b'{"quotes":[]}']
        finally:
            await transport.close()
        endpoint, options = calls[0]
        assert endpoint.startswith('wss://data-stream.binance.vision/stream?streams=')
        assert options['host'] == '93.184.216.34' and options['port'] == 443
        assert options['server_hostname'] == 'data-stream.binance.vision'
        assert options['proxy'] is None and options['compression'] is None
        assert options['max_size'] == MAX_WS_MESSAGE and options['max_queue'] == 4
        assert options['ping_interval'] == 20 and options['ping_timeout'] == 20
        assert options['additional_headers'] == {'Authorization': 'Bearer fixture-key'}
        assert closed == [True]
    asyncio.run(run())


@pytest.mark.parametrize('location', ['/redirected', 'wss://other.example.com/feed'])
@pytest.mark.parametrize('inject_real_connect', [False, True])
def test_websocket_rejects_even_same_host_redirects_before_second_handshake(monkeypatch, location, inject_real_connect):
    async def run():
        dns(monkeypatch, ['93.184.216.34'])
        calls, aborted = [], []
        class Connection:
            transport = SimpleNamespace(abort=lambda: aborted.append(True))

            async def handshake(self, headers, user_agent):
                assert headers == {'Authorization': 'Bearer fixture-key'}
                raise InvalidStatus(Response(302, 'Found', Headers({'Location': location})))
        async def open_tcp(connector):
            calls.append(connector.uri)
            assert connector.create_connection_kwargs['host'] == '93.184.216.34'
            return Connection()
        monkeypatch.setattr(connect, 'open_tcp_connection', open_tcp)
        transport = MarketTransport(ws_connect=connect if inject_real_connect else None)
        try:
            with pytest.raises(ApiError) as error:
                async with transport.stream(config()):
                    pass
            assert error.value.code == 'MARKET_REQUEST_FAILED'
            assert 'fixture-key' not in error.value.message and location not in error.value.message
            assert calls == ['wss://data-stream.binance.vision/stream'] and aborted == [True]
        finally:
            await transport.close()
    asyncio.run(run())


@pytest.mark.parametrize('message', ['字' * (MAX_WS_MESSAGE // 3 + 1), b'x' * (MAX_WS_MESSAGE + 1), {}],
                         ids=['oversized-utf8', 'oversized-bytes', 'wrong-type'])
def test_websocket_rechecks_raw_message_type_and_utf8_size(monkeypatch, message):
    async def run():
        dns(monkeypatch, ['127.0.0.1'])
        closed = []
        @asynccontextmanager
        async def ws(endpoint, **kwargs):
            assert kwargs['host'] == '127.0.0.1' and 'server_hostname' not in kwargs
            async def messages():
                yield message
            try:
                yield messages()
            finally:
                closed.append(True)
        transport = MarketTransport(ws_connect=ws)
        source = config()
        source.update(ws_url='ws://localhost:19876/feed', local_mode=True)
        try:
            with pytest.raises(ApiError) as error:
                async with transport.stream(source) as messages:
                    async for _ in messages:
                        pass
            assert error.value.code == 'MARKET_INVALID_RESPONSE'
            assert closed == [True]
        finally:
            await transport.close()
    asyncio.run(run())


def test_network_errors_are_sanitized_and_cancellation_is_not_swallowed(monkeypatch):
    async def run():
        dns(monkeypatch, ['93.184.216.34'])
        async def fail(request):
            raise httpx.ConnectError('fixture-key provider-internals')
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            with pytest.raises(ApiError) as error:
                await MarketTransport(client=client).get_json(config(), '/snapshot')
            assert 'fixture-key' not in error.value.message
        entered, closed = asyncio.Event(), []
        @asynccontextmanager
        async def ws(endpoint, **kwargs):
            async def messages():
                entered.set()
                await asyncio.Future()
                yield 'unreachable'
            try:
                yield messages()
            finally:
                closed.append(True)
        transport = MarketTransport(ws_connect=ws)
        async def listen():
            async with transport.stream(config()) as messages:
                async for _ in messages:
                    pass
        task = asyncio.create_task(listen())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [True]
        await transport.close()
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['http', 'ws'])
def test_connect_and_http_response_waits_have_absolute_timeout(monkeypatch, kind):
    async def run():
        dns(monkeypatch, ['93.184.216.34'])
        finished = []
        async def handle(request):
            try:
                await asyncio.Future()
            finally:
                finished.append(True)
        @asynccontextmanager
        async def ws(endpoint, **kwargs):
            try:
                await asyncio.Future()
                yield None
            finally:
                finished.append(True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            transport = MarketTransport(client=client, ws_connect=ws)
            source = config()
            source['timeout_seconds'] = 2
            with pytest.raises(ApiError) as error:
                async with asyncio.timeout(4):
                    if kind == 'http':
                        await transport.get_json(source, '/snapshot')
                    else:
                        async with transport.stream(source):
                            pytest.fail('Timed-out opening handshake must not yield')
            assert error.value.code == 'MARKET_REQUEST_FAILED'
            assert finished == [True]
    asyncio.run(run())
