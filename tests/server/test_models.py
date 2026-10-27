import asyncio
import json

import httpx
import pytest

from trading_system.server.errors import ApiError
from trading_system.server.model_service import ModelService
from trading_system.server.store import Store


@pytest.fixture
def service(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'models.sqlite'}")
    store.initialize()
    svc = ModelService(store, tmp_path / 'secret.key')
    yield svc
    asyncio.run(svc.close())
    store.close()


def save(svc, **changes):
    payload = dict(protocol='openai_chat', base_url='http://127.0.0.1:19876/v1', model='test-model', enabled=True,
                   local_mode=True, expected_revision=svc.get_config()['revision'], api_key='test-secret')
    payload.update(changes)
    return svc.save_config(payload)


def test_secret_cas_and_destination(service):
    assert service.get_config()['revision'] == 0
    cfg = save(service)
    assert cfg['has_api_key'] and cfg['key_available']
    assert 'test-secret' not in json.dumps(cfg)
    with pytest.raises(ApiError):
        service.save_config({'expected_revision': 0, 'model': 'other'})
    with pytest.raises(ApiError):
        service.save_config({'expected_revision': 1, 'base_url': 'https://api.example.com/v1'})
    assert service.get_config()['revision'] == 1


@pytest.mark.parametrize('url', ['http://api.example.com', 'https://127.0.0.1', 'https://169.254.169.254', 'https://user:pass@example.com', 'https://example.com?key=x', 'https://10.0.0.1'])
def test_bad_urls(service, url):
    with pytest.raises(ApiError):
        save(service, base_url=url, local_mode=False)


def test_three_protocols_and_idempotency(service):
    async def run():
        for protocol in ('openai_chat', 'openai_responses', 'anthropic'):
            calls = []
            advice = json.dumps(dict(symbol='BTCUSDT', action='HOLD', summary='Wait', counter_evidence=['Risk'], invalidation='Price changes'))
            def handle(request):
                calls.append(request)
                if request.method == 'GET':
                    return httpx.Response(200, json={'data': [{'id': 'test-model'}]})
                body = json.loads(request.content)
                assert 'test-secret' not in request.content.decode()
                assert body['model'] == 'test-model'
                if protocol == 'openai_chat':
                    result = {'choices': [{'finish_reason': 'stop', 'message': {'content': advice}}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 2}}
                elif protocol == 'openai_responses':
                    result = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': advice}]}]}
                else:
                    result = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': advice}]}
                return httpx.Response(200, json=result)
            service.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            cfg = save(service, protocol=protocol)
            assert (await service.test_connection(cfg['revision']))['config']['connection_status'] == 'CONNECTED'
            result = await service.generate('BTCUSDT', cfg['revision'], protocol, fresh_context())
            assert result['decision']['risk_status'] == 'NOT_EVALUATED'
            assert await service.generate('BTCUSDT', cfg['revision'], protocol, {'quote': {'price': '101'}}) == result
            assert len(calls) == 2
            with pytest.raises(ApiError):
                await service.generate('ETHUSDT', cfg['revision'], protocol, {})
            await service.client.aclose()
    asyncio.run(run())


def test_failed_request_not_retried_and_no_body_leak(service):
    async def run():
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(500, text='test-secret provider-sensitive-error')
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        cfg = save(service)
        for _ in range(2):
            with pytest.raises(ApiError) as error:
                await service.generate('BTCUSDT', cfg['revision'], 'failed', fresh_context())
            assert 'test-secret' not in error.value.message
        assert len(calls) == 1
    asyncio.run(run())


def fresh_context():
    from datetime import datetime, timezone
    return {'quote': {'symbol': 'BTCUSDT', 'price': '100', 'as_of': datetime.now(timezone.utc).isoformat(), 'stale': False},
            'market_status': 'CONNECTED', 'quote_max_age_seconds': 30}


def test_stale_quote_rejected_without_claim(service):
    cfg = save(service)
    async def run():
        with pytest.raises(ApiError) as error:
            await service.generate('BTCUSDT', cfg['revision'], 'stale', {})
        assert error.value.code == 'MODEL_QUOTE_UNAVAILABLE'
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['redirect', 'timeout', 'bad-json', 'truncated', 'wrong-symbol', 'oversize'])
def test_invalid_provider_results(service, kind):
    async def run():
        def handle(request):
            if kind == 'redirect':
                return httpx.Response(302, headers={'Location': 'http://localhost:9/stolen'})
            if kind == 'timeout':
                raise httpx.ReadTimeout('test-secret', request=request)
            if kind == 'oversize':
                return httpx.Response(200, content=b'x' * 262145)
            text = 'not JSON' if kind == 'bad-json' else json.dumps(dict(symbol='ETHUSDT' if kind == 'wrong-symbol' else 'BTCUSDT', action='HOLD', summary='Wait', counter_evidence=[], invalidation='Change'))
            return httpx.Response(200, json={'choices': [{'finish_reason': 'length' if kind == 'truncated' else 'stop', 'message': {'content': text}}]})
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        cfg = save(service)
        with pytest.raises(ApiError):
            await service.generate('BTCUSDT', cfg['revision'], kind, fresh_context())
        assert service.decisions() == []
    asyncio.run(run())


def test_concurrent_save_cannot_receive_stale_proof(service):
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        async def handle(request):
            started.set()
            await release.wait()
            return httpx.Response(200, json={'data': [{'id': 'test-model'}]})
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        cfg = save(service)
        task = asyncio.create_task(service.test_connection(cfg['revision']))
        await started.wait()
        updated = save(service, model='new-model')
        release.set()
        with pytest.raises(ApiError) as error:
            await task
        assert error.value.status == 409
        assert service.get_config()['revision'] == updated['revision']
        assert service.get_config()['connection_status'] == 'CONFIGURED'
    asyncio.run(run())


def test_missing_master_key_and_ciphertext(service):
    cfg = save(service)
    from trading_system.server.model_repository import read_config
    with service.store.transaction() as connection:
        assert 'test-secret' not in json.dumps(read_config(connection))
    service.vault.path.unlink()
    public = service.get_config()
    assert public['has_api_key'] and not public['key_available']
    assert not service.vault.path.exists()



def test_inflight_claim_and_restart_replay(service, tmp_path):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handle(request):
            calls.append(request)
            started.set()
            await release.wait()
            advice = dict(symbol='BTCUSDT', action='HOLD', summary='Wait', counter_evidence=[], invalidation='Change')
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(advice)}}]})
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        cfg = save(service)
        pending = asyncio.create_task(service.generate('BTCUSDT', cfg['revision'], 'persistent', fresh_context()))
        await started.wait()
        with pytest.raises(ApiError) as error:
            await service.generate('BTCUSDT', cfg['revision'], 'persistent', fresh_context())
        assert error.value.code == 'MODEL_RUN_NOT_RETRYABLE'
        save(service, model='changed')  # No transaction held while provider awaits.
        release.set()
        result = await pending
        assert result['decision']['config_revision'] == cfg['revision']
        other_store = Store(service.store.engine.url.render_as_string(hide_password=False))
        other_store.initialize()
        restarted = ModelService(other_store, tmp_path / 'secret.key')
        try:
            assert await restarted.generate('BTCUSDT', cfg['revision'], 'persistent', {}) == result
        finally:
            await restarted.close()
            other_store.close()
        assert len(calls) == 1
    asyncio.run(run())


def test_remote_dns_private_target_blocked(service, monkeypatch):
    async def run():
        calls = []
        async def resolve(*args, **kwargs):
            import socket
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request)))
        cfg = save(service, base_url='https://models.example.com/v1', local_mode=False)
        with pytest.raises(ApiError) as error:
            await service.test_connection(cfg['revision'])
        assert error.value.code == 'INVALID_MODEL_URL'
        assert calls == []
    asyncio.run(run())


def test_remote_dns_pins_ip_and_preserves_tls_host(service, monkeypatch):
    async def run():
        async def resolve(*args, **kwargs):
            import socket
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        def handle(request):
            assert request.url.host == '8.8.8.8'
            assert request.headers['host'] == 'models.example.com'
            assert request.extensions['sni_hostname'] == 'models.example.com'
            assert request.headers['authorization'] == 'Bearer test-secret'
            return httpx.Response(200, json={'data': [{'id': 'test-model'}]})
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        cfg = save(service, base_url='https://models.example.com/v1', local_mode=False)
        assert (await service.test_connection(cfg['revision']))['config']['connection_status'] == 'CONNECTED'
    asyncio.run(run())


def test_responses_explicitly_disables_storage():
    from trading_system.server.model_client import payload
    from trading_system.server.model_repository import DEFAULT
    _, body = payload(dict(DEFAULT, model='test'), {})
    assert body['store'] is False


def test_injected_client_state_never_inherited(service):
    async def run():
        def handle(request):
            assert request.url.query == b''
            assert 'cookie' not in request.headers
            assert 'x-inherited' not in request.headers
            assert 'authorization' not in request.headers
            assert request.headers['accept-encoding'] == 'identity'
            return httpx.Response(200, json={'data': [{'id': 'test-model'}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle), cookies={'old-key': 'sensitive'},
                                  headers={'X-Inherited': 'sensitive'}, params={'old-key': 'sensitive'}, auth=('user', 'secret'))
        svc = ModelService(service.store, service.vault.path, client=client)
        try:
            cfg = save(svc, clear_api_key=True, api_key=None)
            await svc.test_connection(cfg['revision'])
            await svc.close()
            assert not client.is_closed
        finally:
            await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('raw', [b'{"data": [], "data": [{"id":"test-model"}]}', b'{"data": [{"id":"test-model"}], "extra":NaN}', b'{"data": [{"id":"test-model"}], "extra":Infinity}'])
def test_outer_json_rejects_ambiguous_values(service, raw):
    async def run():
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw)))
        cfg = save(service)
        with pytest.raises(ApiError):
            await service.test_connection(cfg['revision'])
    asyncio.run(run())


@pytest.mark.parametrize('text', [
    '{"symbol":"BTCUSDT","action":"BUY","action":"HOLD","summary":"Wait","counter_evidence":[],"invalidation":"Change"}',
    '{"symbol":"BTCUSDT","action":"HOLD","summary":"Wait","counter_evidence":[],"invalidation":"Change","extra":NaN}',
    '{"symbol":"BTCUSDT","action":"HOLD","summary":" ","counter_evidence":[],"invalidation":"Change"}',
    '{"symbol":"BTCUSDT","action":"HOLD","summary":"Wait","counter_evidence":[],"invalidation":" "}',
])
def test_inner_json_strict(text):
    from trading_system.server.model_client import parse
    with pytest.raises(ApiError):
        parse({'protocol': 'openai_chat'}, {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}, 'BTCUSDT')


@pytest.mark.parametrize('headers', [{'Content-Encoding': 'gzip'}, {'Content-Length': '262145'}])
def test_reject_compressed_or_advertised_oversize(service, headers):
    async def run():
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, content=b'')))
        cfg = save(service)
        with pytest.raises(ApiError):
            await service.test_connection(cfg['revision'])
    asyncio.run(run())


def test_reject_headers_before_reading_stream(service):
    class Unreadable(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError('Rejected response body must not be read')
            yield b''
    async def run():
        for headers in ({'Content-Encoding': 'gzip'}, {'Content-Length': '262145'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=Unreadable()))) as client:
                svc = ModelService(service.store, service.vault.path, client=client)
                cfg = save(svc)
                with pytest.raises(ApiError) as error:
                    await svc.test_connection(cfg['revision'])
                assert error.value.code == 'INVALID_MODEL_RESPONSE'
    asyncio.run(run())


def test_same_ip_origins_require_separate_tls_handshakes(service, monkeypatch):
    import httpcore
    import socket
    import ssl
    from trading_system.server.model_client import request

    class Stream(httpcore.AsyncNetworkStream):
        def __init__(self):
            self.sni, self.writes = [], []
            self.closed = False

        async def read(self, max_bytes, timeout=None):
            return b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{"data":[]}'

        async def write(self, buffer, timeout=None):
            self.writes.append(buffer)

        async def aclose(self):
            self.closed = True

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert ssl_context.check_hostname
            self.sni.append(server_hostname)
            return self

        def get_extra_info(self, info):
            return None

    class Backend(httpcore.AsyncNetworkBackend):
        def __init__(self):
            self.streams = []

        async def connect_tcp(self, *args, **kwargs):
            assert kwargs['host'] == '8.8.8.8'
            stream = Stream()
            self.streams.append(stream)
            return stream

    async def run():
        async def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
        backend = Backend()
        # Keep the real production HTTPX/httpcore pool; replace only socket IO.
        service.client._transport._pool._network_backend = backend
        for host in ('first.example', 'second.example'):
            config = dict(base_url='https://' + host + '/v1', protocol='openai_chat', local_mode=False, timeout_seconds=5)
            await request(service.client, config, 'fixture-' + host, 'GET', '/models')
        handshakes = [sni for stream in backend.streams for sni in stream.sni]
        assert handshakes == ['first.example', 'second.example']
        assert len(backend.streams) == 2
        assert b'fixture-second.example' not in b''.join(backend.streams[0].writes)
        assert all(stream.closed for stream in backend.streams)
    asyncio.run(run())
