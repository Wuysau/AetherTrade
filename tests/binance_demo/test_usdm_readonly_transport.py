"""USDM read-only contract tests. MockTransport only; never reaches a venue."""
import asyncio
import hashlib
import inspect
import hmac
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from trading_system.engine.binance_demo.contracts import DemoEndpointRegistry, DemoError
from trading_system.engine.binance_demo.credentials import DemoCredentialProvider
from trading_system.engine.binance_demo.usdm_readonly import UsdmDemoReadOnly


KEY = 'demo-key-1234567890'
SECRET = 'demo-secret-1234567890'


@pytest.fixture
def credentials():
    provider = DemoCredentialProvider()
    ref = provider.put(KEY, SECRET)
    yield provider, ref
    provider.clear()


def mock_transport(handler):
    def streamed(request):
        response = handler(request)
        if response.is_stream_consumed:
            return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))
        return response
    return httpx.MockTransport(streamed)


class SyncHarness:
    def __init__(self, transport):
        self.transport = transport
        self.loop = asyncio.new_event_loop()

    def __enter__(self):
        self.loop.run_until_complete(self.transport.__aenter__())
        return self

    def __exit__(self, *_):
        self.loop.run_until_complete(self.transport.close())
        self.loop.close()

    def __getattr__(self, name):
        attribute = getattr(self.transport, name)
        if inspect.iscoroutinefunction(attribute):
            return lambda *args, **kwargs: self.loop.run_until_complete(attribute(*args, **kwargs))
        return attribute


def client(credentials, handler, *, clock=lambda: 100.0):
    provider, ref = credentials
    return SyncHarness(UsdmDemoReadOnly(provider, ref, http_transport=mock_transport(handler), clock=clock, monotonic_clock=clock))


def test_only_documented_read_paths_are_signed_and_pinned(credentials):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == '/fapi/v1/time':
            return httpx.Response(200, json={'serverTime': 100000})
        return httpx.Response(200, json=[] if request.url.path in ('/fapi/v1/symbolConfig', '/fapi/v3/positionRisk') else {})
    with client(credentials, handler) as transport:
        transport.sync_time()
        transport.account()
        transport.position_mode()
        transport.symbol_configs()
        transport.position_risk()
    assert [r.url.path for r in requests] == ['/fapi/v1/time', '/fapi/v3/account', '/fapi/v1/positionSide/dual', '/fapi/v1/symbolConfig', '/fapi/v3/positionRisk']
    for request in requests:
        assert request.method == 'GET'
        assert str(request.url).startswith('https://demo-fapi.binance.com/')
    assert 'X-MBX-APIKEY' not in requests[0].headers
    for request in requests[1:]:
        assert request.headers['X-MBX-APIKEY'] == KEY
        query, signature = request.url.query.decode().rsplit('&signature=', 1)
        assert parse_qs(query) == {'recvWindow':['5000'], 'timestamp':['100000']}
        assert signature == hmac.new(SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()


def test_no_generic_or_write_endpoint_and_spot_gate_unchanged(credentials):
    with client(credentials, lambda r: pytest.fail('must not request')) as transport:
        for endpoint in ('/fapi/v1/order', 'https://fapi.binance.com', '//testnet.binancefuture.com', 'order', '../account'):
            with pytest.raises(DemoError): transport._get(endpoint)
        assert not hasattr(transport, 'send_order')
        assert not hasattr(transport, 'cancel_order')
    with pytest.raises(DemoError, match='DEMO_PRODUCT_NOT_QUALIFIED'):
        DemoEndpointRegistry.url('USDM','GET','/fapi/v3/account')


def test_signed_calls_require_fresh_clock_and_failed_sync_invalidates_it(credentials):
    now = [100.0]
    good = [True]
    def handler(request):
        return httpx.Response(200, json={'serverTime':100000 if good[0] else 200000})
    with client(credentials, handler, clock=lambda:now[0]) as transport:
        with pytest.raises(DemoError, match='TIME_SYNC_REQUIRED'): transport.account()
        transport.sync_time()
        good[0] = False
        with pytest.raises(DemoError, match='CLOCK_DRIFT'): transport.sync_time()
        with pytest.raises(DemoError, match='TIME_SYNC_REQUIRED'): transport.account()
        good[0] = True
        transport.sync_time()
        now[0] += 61
        with pytest.raises(DemoError, match='TIME_SYNC_REQUIRED'): transport.account()


@pytest.mark.parametrize('status,code', [(301, 'DEMO_REDIRECT_BLOCKED'), (418, 'DEMO_IP_BANNED'), (429, 'DEMO_RATE_LIMITED'), (500, 'DEMO_UNAVAILABLE'), (400, 'DEMO_REQUEST_REJECTED')])
def test_error_paths_are_single_attempt_and_redacted(credentials, status, code, caplog):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={'location':'https://fapi.binance.com','Retry-After':'5'}, json={'msg':KEY + SECRET})
    with caplog.at_level(logging.DEBUG):
        with client(credentials, handler) as transport:
            with pytest.raises(DemoError, match=code) as error: transport.sync_time()
    assert len(requests) == 1
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    assert KEY not in caplog.text and SECRET not in caplog.text and 'signature=' not in caplog.text


def test_signed_logging_and_network_failure_do_not_leak_credentials(credentials, caplog):
    def handler(request):
        if request.url.path.endswith('/time'): return httpx.Response(200, json={'serverTime':100000})
        raise httpx.ReadTimeout(f'{KEY} {SECRET} {request.url}')
    with caplog.at_level(logging.DEBUG):
        with client(credentials, handler) as transport:
            transport.sync_time()
            with pytest.raises(DemoError, match='DEMO_UNAVAILABLE') as error: transport.account()
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    assert KEY not in caplog.text and SECRET not in caplog.text and 'signature=' not in caplog.text


def test_invalid_json_wrong_shapes_and_oversize_rejected(credentials):
    for response in (httpx.Response(200, content=b'invalid'), httpx.Response(200, json=[]), httpx.Response(200, content=b' ' * 2_000_001)):
        with client(credentials, lambda r: response) as transport:
            with pytest.raises(DemoError, match='DEMO_RESPONSE_INVALID'): transport.sync_time()


def test_malformed_error_code_is_rejected_without_escaping_exception(credentials):
    with client(credentials, lambda r: httpx.Response(400, json={'code': ['bad'], 'msg': SECRET})) as transport:
        with pytest.raises(DemoError, match='DEMO_REQUEST_REJECTED'):
            transport.sync_time()


def test_invalid_clock_is_a_controlled_failure(credentials):
    with client(credentials, lambda r: pytest.fail('must not request'), clock=lambda: 10**1000) as transport:
        with pytest.raises(DemoError, match='DEMO_CLOCK_UNCERTAIN'): transport.sync_time()


def test_rate_limit_and_ban_block_further_requests(credentials):
    for status, expected in [(429, 'DEMO_RATE_LIMITED'), (418, 'DEMO_IP_BANNED')]:
        requests = []
        now = [100.0]
        def handler(request):
            requests.append(request)
            return httpx.Response(status, headers={'Retry-After':'5'}, json={})
        with client(credentials, handler, clock=lambda:now[0]) as transport:
            with pytest.raises(DemoError, match=expected): transport.sync_time()
            with pytest.raises(DemoError, match=expected): transport.sync_time()
            assert len(requests) == 1
            now[0] = 106
            if status == 418:
                with pytest.raises(DemoError, match=expected): transport.sync_time()
                assert len(requests) == 1
            else:
                with pytest.raises(DemoError, match=expected): transport.sync_time()
                assert len(requests) == 2


def test_revoked_credentials_fail_before_signed_http_request(credentials):
    provider, ref = credentials
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'serverTime':100000})
    with client(credentials, handler) as transport:
        transport.sync_time()
        provider.remove(ref)
        with pytest.raises(DemoError, match='DEMO_CREDENTIAL_UNAVAILABLE'): transport.account()
    assert len(seen) == 1


def test_rejects_compressed_response_to_bound_decoded_size(credentials):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={'Content-Encoding':'gzip'}, stream=httpx.ByteStream(b'not-decompressed'))
    with client(credentials, handler) as transport:
        with pytest.raises(DemoError, match='DEMO_RESPONSE_INVALID'): transport.sync_time()
    assert seen[0].headers['accept-encoding'] == 'identity'


def test_retry_after_uses_monotonic_response_receipt_without_shortening(credentials):
    wall, steady = [100.0], [10.0]
    requests = []
    def handler(request):
        requests.append(request)
        steady[0] = 130.0
        return httpx.Response(429, headers={'Retry-After':'7200'}, json={})
    provider, ref = credentials
    with SyncHarness(UsdmDemoReadOnly(provider, ref, http_transport=mock_transport(handler),
                          clock=lambda:wall[0], monotonic_clock=lambda:steady[0])) as transport:
        with pytest.raises(DemoError) as first: transport.sync_time()
        assert first.value.retry_after == 7200
        wall[0] = 10000.0
        steady[0] = 7210.0
        with pytest.raises(DemoError, match='DEMO_RATE_LIMITED'): transport.sync_time()
        assert len(requests) == 1
        steady[0] = 7331.0
        with pytest.raises(DemoError, match='DEMO_RATE_LIMITED'): transport.sync_time()
        assert len(requests) == 2


@pytest.mark.parametrize('retry', ['9999999999999999999999999999999999', 'Wed, 21 Oct 2099 07:28:00 GMT'])
def test_unrepresentable_or_unparseable_retry_after_fails_closed(credentials, retry):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(429, headers={'Retry-After':retry}, json={})
    with client(credentials, handler) as transport:
        with pytest.raises(DemoError, match='DEMO_RATE_LIMITED'): transport.sync_time()
        with pytest.raises(DemoError, match='DEMO_RATE_LIMITED'): transport.sync_time()
    assert len(seen) == 1


def test_total_deadline_stops_trickling_body(credentials):
    wall, steady = [100.0], [0.0]
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                steady[0] += 1.0
                yield b' '
    provider, ref = credentials
    with SyncHarness(UsdmDemoReadOnly(provider, ref, http_transport=httpx.MockTransport(
            lambda r: httpx.Response(200, stream=Trickle())),
            clock=lambda:wall[0], monotonic_clock=lambda:steady[0])) as transport:
        with pytest.raises(DemoError, match='DEMO_UNAVAILABLE'): transport.sync_time()
    assert steady[0] < 100


def test_exchange_clock_rejection_invalidates_previous_sync(credentials):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/time'):
            return httpx.Response(200, json={'serverTime':100000})
        return httpx.Response(400, json={'code':-1021})
    with client(credentials, handler) as transport:
        transport.sync_time()
        with pytest.raises(DemoError, match='DEMO_CLOCK_DRIFT'): transport.account()
        with pytest.raises(DemoError, match='DEMO_TIME_SYNC_REQUIRED'): transport.account()
    assert len(requests) == 2


@pytest.mark.parametrize('stall_at', ['headers', 'body'])
def test_absolute_deadline_cancels_stalled_io(credentials, monkeypatch, stall_at):
    from trading_system.engine.binance_demo import usdm_readonly
    monkeypatch.setattr(usdm_readonly, '_TOTAL_DEADLINE_SECONDS', 0.01)
    cancelled = []
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                await asyncio.sleep(1)
                yield b'{}'
            finally:
                cancelled.append('body')
    async def handler(request):
        if stall_at == 'headers':
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.append('headers')
        return httpx.Response(200, stream=SlowBody())
    provider, ref = credentials
    with SyncHarness(UsdmDemoReadOnly(provider, ref, http_transport=httpx.MockTransport(handler),
                                      clock=lambda:100.0)) as transport:
        with pytest.raises(DemoError, match='DEMO_UNAVAILABLE'): transport.sync_time()
    assert cancelled == [stall_at]


@pytest.mark.parametrize('wall_elapsed,steady_elapsed', [(0, 61), (-20, 1), (20, 1)])
def test_wall_clock_jump_cannot_extend_or_reuse_signed_sync(credentials, wall_elapsed, steady_elapsed):
    wall, steady, seen = [100.0], [10.0], []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'serverTime':100000})
    provider, ref = credentials
    with SyncHarness(UsdmDemoReadOnly(provider, ref, http_transport=mock_transport(handler),
                                      clock=lambda:wall[0], monotonic_clock=lambda:steady[0])) as transport:
        transport.sync_time()
        wall[0] += wall_elapsed
        steady[0] += steady_elapsed
        with pytest.raises(DemoError, match='DEMO_TIME_SYNC_REQUIRED'): transport.account()
    assert len(seen) == 1


def test_sync_rejects_wall_clock_manipulation_that_hides_elapsed_time(credentials):
    wall, steady = [100.0], [10.0]
    def handler(request):
        steady[0] += 3
        return httpx.Response(200, json={'serverTime':100000})
    provider, ref = credentials
    with SyncHarness(UsdmDemoReadOnly(provider, ref, http_transport=mock_transport(handler),
                                      clock=lambda:wall[0], monotonic_clock=lambda:steady[0])) as transport:
        with pytest.raises(DemoError, match='DEMO_CLOCK_UNCERTAIN'): transport.sync_time()


def test_missing_retry_after_fails_closed(credentials):
    now, seen = [100.0], []
    def handler(request):
        seen.append(request)
        return httpx.Response(429, json={})
    with client(credentials, handler, clock=lambda:now[0]) as transport:
        with pytest.raises(DemoError, match='DEMO_RATE_LIMITED'): transport.sync_time()
        now[0] += 1000
        with pytest.raises(DemoError, match='DEMO_RATE_LIMITED'): transport.sync_time()
    assert len(seen) == 1
