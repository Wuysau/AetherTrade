"""Bounded market reads with destination validation and no implicit credentials."""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import ipaddress
import json
import math
import socket
from urllib.parse import urlencode, urlsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .errors import ApiError


MAX_HTTP_BODY = 1024 * 1024
MAX_WS_MESSAGE = 256 * 1024


def invalid_url():
    return ApiError('MARKET_INVALID_URL', '远程行情需使用公网 HTTPS/WSS；本地模式仅允许显式回环地址。')


def _public_address(address):
    # ipaddress considers multicast globally scoped, but it isn't a public
    # unicast destination and must never be used for a provider connection.
    return (address.is_global and not address.is_multicast and not address.is_reserved
            and not address.is_unspecified)


def validate_url(value, local_mode, *, websocket=False):
    """Offline syntax checks; DNS is revalidated immediately before every connect."""
    try:
        if (type(local_mode) is not bool or not isinstance(value, str) or not 1 <= len(value) <= 2048
                or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value):
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.username is not None or parsed.password is not None or '?' in value or '#' in value
                or not parsed.hostname or parsed.port == 0):
            raise ValueError()
        secure, plain = ('wss', 'ws') if websocket else ('https', 'http')
        host = parsed.hostname.lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        loopback = host == 'localhost' or (address is not None and address.is_loopback)
        if local_mode:
            if not loopback or parsed.scheme not in (secure, plain):
                raise ValueError()
        elif (parsed.scheme != secure or loopback or (address is not None and not _public_address(address))
              or (address is None and ('.' not in host or host.endswith(('.localhost', '.local', '.internal', '.'))))):
            raise ValueError()
        if '%' in host:
            raise ValueError()
        return value.rstrip('/') if not websocket else value
    except (ValueError, TypeError, UnicodeError):
        raise invalid_url() from None


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError()
            result[key] = item
        return result

    def constant(_):
        raise ValueError()

    def floating(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError()
        return number

    result = json.loads(value, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
    if not isinstance(result, (dict, list)):
        raise ValueError()
    return result


def _failure():
    return ApiError('MARKET_REQUEST_FAILED', '行情请求失败或超时。', 502)


def _bad_response():
    return ApiError('MARKET_INVALID_RESPONSE', '行情响应格式或大小无效。', 502)


def _headers(config):
    headers = {}
    secret, scheme = config.get('api_key'), config.get('auth_scheme', 'none')
    if scheme not in ('none', 'bearer', 'x_api_key'):
        raise ApiError('MARKET_INVALID_CONFIG', '行情认证方式无效。')
    if secret:
        if not isinstance(secret, str) or not 1 <= len(secret) <= 4096 or any(not 33 <= ord(c) <= 126 for c in secret):
            raise ApiError('MARKET_INVALID_CONFIG', '行情密钥格式无效。')
        if scheme == 'bearer':
            headers['Authorization'] = 'Bearer ' + secret
        elif scheme == 'x_api_key':
            headers['X-API-Key'] = secret
    return headers


async def _destination(url, local_mode):
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme in ('https', 'wss') else 80)
    answers = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise invalid_url()
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise invalid_url() from None
        if not (address.is_loopback if local_mode else _public_address(address)):
            raise invalid_url()
    return addresses[0], port


def _timeout(config):
    value = config.get('timeout_seconds', 8)
    if type(value) is not int or not 2 <= value <= 20:
        raise ApiError('MARKET_INVALID_CONFIG', '行情请求超时设置无效。')
    return value


class _NoRedirectConnect(connect):
    def process_redirect(self, exc):
        # websockets otherwise follows same-origin redirects, including secrets.
        return exc


class MarketTransport:
    def __init__(self, client=None, ws_connect=None):
        self.client = client or httpx.AsyncClient(
            trust_env=False, follow_redirects=False, auth=None, http1=True, http2=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=0))
        self._owned_client = client is None
        self.ws_connect = ws_connect or _NoRedirectConnect

    async def get_json(self, config, path, params=None):
        try:
            base = validate_url(config['rest_url'], config['local_mode'])
            # Only caller-owned paths are appended; a path can never replace origin.
            if (not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
                    or any(c in path for c in ('?', '#', '\\')) or any(ord(c) <= 32 for c in path)):
                raise invalid_url()
            url = httpx.URL(base + path, params=params)
            timeout = _timeout(config)
            headers = dict(_headers(config), Accept='application/json', **{'Accept-Encoding': 'identity', 'Connection': 'close'})
            async with asyncio.timeout(timeout):
                address, _ = await _destination(str(url), config['local_mode'])
                headers['Host'] = url.netloc.decode()
                outgoing = httpx.Request('GET', url.copy_with(host=address), headers=headers,
                                         extensions={'sni_hostname': url.raw_host.decode('ascii'),
                                                     'timeout': httpx.Timeout(timeout).as_dict()})
                # A raw Request bypasses client headers/cookies; auth=None disables
                # injected client auth as well. Production has no proxy or pool reuse.
                response = await self.client.send(outgoing, auth=None, follow_redirects=False, stream=True)
                try:
                    if not 200 <= response.status_code < 300:
                        raise ApiError('MARKET_PROVIDER_ERROR', '行情服务请求失败。', 502)
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise _bad_response()
                    length = response.headers.get('content-length')
                    if length is not None and (not length.isdecimal() or int(length) > MAX_HTTP_BODY):
                        raise _bad_response()
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if len(body) + len(chunk) > MAX_HTTP_BODY:
                            raise _bad_response()
                        body.extend(chunk)
                    try:
                        return strict_json(bytes(body).decode('utf-8'))
                    except (ValueError, UnicodeError, RecursionError):
                        raise _bad_response() from None
                finally:
                    await response.aclose()
        except ApiError:
            raise
        except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError, UnicodeError, KeyError, RuntimeError):
            raise _failure() from None

    @asynccontextmanager
    async def stream(self, config, params=None):
        try:
            endpoint = validate_url(config['ws_url'], config['local_mode'], websocket=True)
            if params:
                endpoint += '?' + urlencode(params, doseq=True)
            timeout = _timeout(config)
            async with asyncio.timeout(timeout):
                address, port = await _destination(endpoint, config['local_mode'])
            kwargs = dict(host=address, port=port, proxy=None, compression=None,
                          additional_headers=_headers(config), user_agent_header=None,
                          open_timeout=timeout, close_timeout=2, ping_interval=20, ping_timeout=20,
                          max_size=MAX_WS_MESSAGE, max_queue=4, write_limit=32768)
            if urlsplit(endpoint).scheme == 'wss':
                kwargs['server_hostname'] = urlsplit(endpoint).hostname
            connector = self.ws_connect(endpoint, **kwargs)
            if isinstance(connector, connect):
                # Preserve redirect rejection if a real connect factory is injected.
                connector.process_redirect = lambda exc: exc
            async with AsyncExitStack() as stack:
                async with asyncio.timeout(timeout):
                    websocket = await stack.enter_async_context(connector)
                yield self._messages(websocket)
        except ApiError:
            raise
        except (WebSocketException, OSError, TimeoutError, ValueError, TypeError, UnicodeError, KeyError, RuntimeError):
            raise _failure() from None

    @staticmethod
    async def _messages(websocket):
        async for message in websocket:
            if not isinstance(message, (str, bytes)) or len(message.encode('utf-8') if isinstance(message, str) else message) > MAX_WS_MESSAGE:
                raise _bad_response()
            yield message

    async def close(self):
        if self._owned_client:
            await self.client.aclose()
