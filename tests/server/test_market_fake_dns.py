"""Official public feeds remain usable through a TUN Fake-IP DNS mapping."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from trading_system.server.errors import ApiError
from trading_system.server.market_config import DEFAULT
from trading_system.server.market_transport import MarketTransport
from server.test_market_transport import dns


def official(**changes):
    return dict(DEFAULT, api_key=None, **changes)


@pytest.mark.parametrize('address', ['198.18.0.7', '198.19.255.254'])
def test_official_credential_free_http_and_ws_keep_pinning_and_hostname(monkeypatch, address):
    async def run():
        dns(monkeypatch, [address])
        requests, connections = [], []

        def handler(request):
            requests.append(request)
            assert request.url.host == address
            assert request.headers['host'] == 'data-api.binance.vision'
            assert request.extensions['sni_hostname'] == 'data-api.binance.vision'
            assert 'authorization' not in request.headers and 'x-api-key' not in request.headers
            return httpx.Response(200, json={'symbol': 'BTCUSDT'})

        @asynccontextmanager
        async def ws(endpoint, **options):
            connections.append(endpoint)
            assert endpoint.startswith('wss://data-stream.binance.vision/stream?')
            assert options['host'] == address and options['port'] == 443
            assert options['server_hostname'] == 'data-stream.binance.vision'
            assert options['additional_headers'] == {} and options['proxy'] is None
            assert options.get('ssl') is not False

            async def messages():
                yield '{"stream":"btcusdt@ticker","data":{}}'
            yield messages()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     headers={'Authorization': 'must-not-inherit'}) as client:
            transport = MarketTransport(client=client, ws_connect=ws)
            await transport.get_json(official(), '/api/v3/ticker/24hr', {'symbol': 'BTCUSDT'})
            async with transport.stream(official(), {'streams': 'btcusdt@ticker'}) as messages:
                assert len([message async for message in messages]) == 1
        assert len(requests) == len(connections) == 1

    asyncio.run(run())


@pytest.mark.parametrize('changes', [
    {'provider': 'binance_compatible'}, {'provider': 'standard_json'},
    {'api_key': 'do-not-send'}, {'auth_scheme': 'bearer'}, {'auth_scheme': 'x_api_key'},
    {'has_api_key': True}, {'encrypted_key': 'unavailable-key'},
    {'rest_url': 'https://data-api.binance.vision.attacker.example'},
    {'rest_url': 'https://data-api.binance.vision:8443'},
    {'rest_url': 'https://data-api.binance.vision/other'},
    {'ws_url': 'wss://other.example/stream'},
])
def test_fake_dns_exception_does_not_apply_to_custom_or_authenticated_sources(monkeypatch, changes):
    async def run():
        dns(monkeypatch, ['198.18.0.7'])
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(200, json={}))) as client:
            transport = MarketTransport(client=client)
            source = official()
            source.update(changes)
            with pytest.raises(ApiError) as error:
                await transport.get_json(source, '/api/v3/ticker/24hr')
            assert error.value.code == 'MARKET_INVALID_URL'
            with pytest.raises(ApiError) as error:
                async with transport.stream(source):
                    pytest.fail('Blocked DNS must not open a socket')
            assert error.value.code == 'MARKET_INVALID_URL'
        assert calls == []

    asyncio.run(run())


@pytest.mark.parametrize('addresses', [
    ['127.0.0.1'], ['10.0.0.1'], ['169.254.169.254'], ['192.168.1.1'],
    ['100.64.0.1'], ['198.51.100.1'], ['::1'], ['fc00::1'],
    ['198.18.0.7', '10.0.0.1'], ['93.184.216.34', '127.0.0.1'],
])
def test_official_preset_still_rejects_other_private_and_mixed_answers(monkeypatch, addresses):
    async def run():
        dns(monkeypatch, addresses)
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: calls.append(request) or httpx.Response(200, json={}))) as client:
            transport = MarketTransport(client=client)
            with pytest.raises(ApiError):
                await transport.get_json(official(), '/api/v3/ticker/24hr')
            with pytest.raises(ApiError):
                async with transport.stream(official()):
                    pytest.fail('Blocked DNS must not open a socket')
        assert calls == []

    asyncio.run(run())
