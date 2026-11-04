"""Offline protocol contracts; MockTransport never contacts an exchange."""
from decimal import Decimal
import hashlib
import hmac
from urllib.parse import parse_qs

import httpx
import pytest

from trading_system.engine.binance_demo.contracts import DemoEndpointRegistry, DemoError, DemoScope
from trading_system.engine.binance_demo.credentials import DemoCredentialProvider
from trading_system.engine.binance_demo.transport import BinanceDemoTransport
from trading_system.engine.binance_demo.orders import compile_spot_order


def credentials():
    provider = DemoCredentialProvider()
    ref = provider.put('demo-key-1234567890', 'demo-secret-1234567890')
    return provider, ref


def test_demo_scope_cannot_alias_paper_live_or_testnet():
    for environment in ['PAPER', 'LIVE', 'TESTNET']:
        with pytest.raises(DemoError):
            DemoScope(environment=environment, account_id='a', account_epoch='e', strategy_id='s')
    a = DemoScope(account_id='a', account_epoch='1', strategy_id='s')
    b = DemoScope(account_id='a', account_epoch='2', strategy_id='s')
    assert a.key != b.key


@pytest.mark.parametrize('path', ['https://api.binance.com/api/v3/order', '//api.binance.com', '/api/api/v3/order', '/api/v3/../v3/order', '/api/v3/order?symbol=BTCUSDT'])
def test_arbitrary_destinations_rejected(path):
    with pytest.raises(DemoError):
        DemoEndpointRegistry.url('SPOT', 'GET', path)


def test_credentials_are_session_only_redacted_and_revocable():
    provider, ref = credentials()
    assert 'demo-key' not in repr(provider)
    assert 'demo-secret' not in repr(provider)
    assert set(provider.describe(ref)) == {'credential_ref', 'fingerprint', 'key_type'}
    provider.remove(ref)
    with pytest.raises(DemoError, match='CREDENTIAL_UNAVAILABLE'):
        provider.sign(ref, 'x')


def test_signed_account_request_is_fixed_host_and_canonical_hmac():
    provider, ref = credentials()
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path.endswith('/time'):
            return httpx.Response(200, json={'serverTime': 100000})
        return httpx.Response(200, json={'accountType': 'SPOT', 'canTrade': True, 'balances': []})
    with BinanceDemoTransport(provider, ref, http_transport=httpx.MockTransport(handle), clock=lambda: 100.0) as transport:
        transport.sync_time()
        assert transport.account()['accountType'] == 'SPOT'
    request = requests[-1]
    assert str(request.url).startswith('https://demo-api.binance.com/api/v3/account?')
    assert request.headers['X-MBX-APIKEY'] == 'demo-key-1234567890'
    raw = request.url.query.decode()
    query, signature = raw.rsplit('&signature=', 1)
    assert query == 'recvWindow=5000&timestamp=100000'
    assert signature == hmac.new(b'demo-secret-1234567890', query.encode(), hashlib.sha256).hexdigest()


@pytest.mark.parametrize('status,code', [(302, 'DEMO_REDIRECT_BLOCKED'), (418, 'DEMO_IP_BANNED'), (429, 'DEMO_RATE_LIMITED'), (503, 'DEMO_UNAVAILABLE')])
def test_http_errors_never_echo_upstream_or_follow_redirects(status, code):
    provider, ref = credentials()
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(status, headers={'location': 'https://api.binance.com', 'Retry-After': '3'}, json={'msg': 'demo-secret-1234567890'})
    with BinanceDemoTransport(provider, ref, http_transport=httpx.MockTransport(handle)) as transport:
        with pytest.raises(DemoError, match=code) as error:
            transport.sync_time()
    assert len(seen) == 1
    assert 'demo-secret' not in str(error.value)


def test_clock_sync_required_and_bounded():
    provider, ref = credentials()
    with BinanceDemoTransport(provider, ref, http_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'serverTime': 900000})), clock=lambda: 100.0) as transport:
        with pytest.raises(DemoError, match='TIME_SYNC_REQUIRED'):
            transport.account()
        with pytest.raises(DemoError, match='CLOCK_DRIFT'):
            transport.sync_time()


def test_no_public_write_without_durable_permit():
    provider, ref = credentials()
    with BinanceDemoTransport(provider, ref, http_transport=httpx.MockTransport(lambda r: pytest.fail('network must not run'))) as transport:
        with pytest.raises(DemoError, match='PERMIT_REQUIRED'):
            transport.send_order({'symbol': 'BTCUSDT'}, permit=None)


RULES = {'symbol': 'BTCUSDT', 'status': 'TRADING', 'baseAsset': 'BTC', 'quoteAsset': 'USDT', 'orderTypes': ['LIMIT', 'MARKET'],
         'filters': [{'filterType': 'LOT_SIZE', 'minQty': '0.00001', 'maxQty': '100', 'stepSize': '0.00001'},
                     {'filterType': 'PRICE_FILTER', 'minPrice': '0.01', 'maxPrice': '1000000', 'tickSize': '0.01'},
                     {'filterType': 'MIN_NOTIONAL', 'minNotional': '5', 'applyToMarket': True}]}


def test_spot_order_uses_decimal_filters_and_original_client_id():
    result = compile_spot_order(RULES, side='BUY', order_type='LIMIT', quantity='0.001', price='50000.00', client_id='demo-original', reference_price='50000')
    assert result == {'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'LIMIT', 'quantity': '0.001', 'price': '50000.00', 'timeInForce': 'GTC', 'newClientOrderId': 'demo-original'}


@pytest.mark.parametrize('quantity', ['NaN', 'Infinity', '-1', '0', '0.000011', '100.1'])
def test_bad_quantities_do_not_compile(quantity):
    with pytest.raises(DemoError):
        compile_spot_order(RULES, side='BUY', order_type='LIMIT', quantity=quantity, price='50000.00', client_id='original', reference_price='50000')


def test_http_diagnostics_redact_signature_and_api_key(caplog):
    import logging
    provider, ref = credentials()
    with caplog.at_level(logging.DEBUG):
        with BinanceDemoTransport(provider, ref, http_transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'serverTime': 100000}) if r.url.path.endswith('/time') else httpx.Response(200, json={})), clock=lambda: 100.0) as transport:
            transport.sync_time()
            transport.account()
    assert 'demo-key-1234567890' not in caplog.text
    assert 'signature=' not in caplog.text


def test_expired_key_cannot_sign_even_without_status_polling():
    now = [100.0]
    provider = DemoCredentialProvider(clock=lambda: now[0])
    ref = provider.put('demo-key-1234567890', 'demo-secret-1234567890', ttl_seconds=1)
    now[0] = 102.0
    with pytest.raises(DemoError, match='CREDENTIAL_UNAVAILABLE'):
        provider.sign(ref, 'anything')
