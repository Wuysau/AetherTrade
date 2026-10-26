"""Bounded, no-retry provider requests and protocol envelope validation."""
import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit

import httpx
from .errors import ApiError

MAX_BODY = 262144


def strict_json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = item
        return result

    def constant(_):
        raise ValueError('Nonfinite JSON constant')

    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


def invalid():
    return ApiError('INVALID_MODEL_RESPONSE', '模型响应不是完整有效的分析 JSON。', 502)


def validate_url(value, local_mode):
    try:
        if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 for c in value) or '\\' in value:
            raise ValueError()
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname or parsed.port == 0:
            raise ValueError()
        host = parsed.hostname.lower()
        loopback = host in ('localhost', '127.0.0.1', '::1')
        if local_mode:
            if not loopback or parsed.scheme not in ('http', 'https'):
                raise ValueError()
        elif parsed.scheme != 'https' or loopback:
            raise ValueError()
        else:
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
                    raise ValueError()
            else:
                if not address.is_global:
                    raise ValueError()
        return value.rstrip('/')
    except (ValueError, TypeError):
        raise ApiError('INVALID_MODEL_URL', '远程模型需使用公网 HTTPS；本地模式仅允许回环地址。') from None


async def request(client, config, secret, method, path, payload=None):
    url = httpx.URL(config['base_url'] + path)
    headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity', 'Connection': 'close'}
    if config['protocol'] == 'anthropic':
        headers['anthropic-version'] = config['anthropic_version']
        if secret:
            headers['x-api-key'] = secret
    elif secret:
        headers['Authorization'] = 'Bearer ' + secret
    try:
        async with asyncio.timeout(config['timeout_seconds']):
            # Resolve once, validate all answers, and pin the actual request to an
            # approved address. TLS SNI and Host preserve the original hostname.
            if not config['local_mode']:
                answers = await asyncio.get_running_loop().getaddrinfo(url.host, url.port or 443, type=socket.SOCK_STREAM)
                addresses = [answer[4][0] for answer in answers]
                if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
                    raise ApiError('INVALID_MODEL_URL', '模型地址未解析到允许的公网地址。')
                original_host = url.host
                headers['Host'] = url.netloc.decode()
                url = url.copy_with(host=addresses[0])
                extensions = {'sni_hostname': original_host.encode()}
            else:
                extensions = {}
            extensions['timeout'] = httpx.Timeout(config['timeout_seconds']).as_dict()
            outgoing = httpx.Request(method, url, headers=headers, json=payload, extensions=extensions)
            response = await client.send(outgoing, auth=None, follow_redirects=False, stream=True)
            try:
                if not 200 <= response.status_code < 300:
                    raise ApiError('MODEL_PROVIDER_ERROR', f'模型服务请求失败（HTTP {response.status_code}）。', 502)
                if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                    raise invalid()
                length = response.headers.get('content-length')
                if length is not None and (not length.isdecimal() or int(length) > MAX_BODY):
                    raise invalid()
                chunks = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    if len(chunks) + len(chunk) > MAX_BODY:
                        raise invalid()
                    chunks.extend(chunk)
                result = strict_json(chunks)
                if not isinstance(result, dict):
                    raise invalid()
                return result
            finally:
                await response.aclose()
    except ApiError:
        raise
    except (httpx.HTTPError, OSError, TimeoutError, ValueError, UnicodeError, RecursionError):
        raise ApiError('MODEL_REQUEST_FAILED', '模型请求失败或超时；不会自动重试。', 502) from None


INSTRUCTIONS = ('Return only a JSON object with symbol, action (HOLD/BUY/SELL), summary, counter_evidence (array of strings), '
                'and invalidation (string). Analyze only the supplied public quote and PAPER context. '
                'This is advice only, not an order or risk approval. Do not invent missing data.')


def payload(config, context):
    text = json.dumps(context, ensure_ascii=False, allow_nan=False)
    common = {'model': config['model']}
    if config['protocol'] == 'openai_responses':
        return '/responses', dict(common, input=text, instructions=INSTRUCTIONS, max_output_tokens=config['max_output_tokens'], store=False)
    messages = [{'role': 'user', 'content': text}]
    if config['protocol'] == 'anthropic':
        return '/messages', dict(common, system=INSTRUCTIONS, messages=messages, max_tokens=config['max_output_tokens'], stream=False)
    token_field = 'max_completion_tokens' if urlsplit(config['base_url']).hostname == 'api.openai.com' else 'max_tokens'
    return '/chat/completions', dict(common, messages=[{'role': 'system', 'content': INSTRUCTIONS}] + messages,
                                     stream=False, **{token_field: config['max_output_tokens']})


def parse(config, body, symbol):
    try:
        protocol = config['protocol']
        if protocol == 'openai_chat':
            choices = body['choices']
            if len(choices) != 1 or choices[0]['finish_reason'] != 'stop':
                raise ValueError()
            message = choices[0]['message']
            if message.get('refusal') or message.get('tool_calls') or message.get('function_call'):
                raise ValueError()
            text = message['content']
        elif protocol == 'anthropic':
            if body['stop_reason'] != 'end_turn' or any(block['type'] != 'text' for block in body['content']):
                raise ValueError()
            text = ''.join(block['text'] for block in body['content'])
        else:
            if body.get('status') != 'completed':
                raise ValueError()
            parts = []
            for item in body['output']:
                if item['type'] == 'reasoning':
                    continue
                if item['type'] != 'message' or item.get('status', 'completed') != 'completed':
                    raise ValueError()
                for block in item['content']:
                    if block['type'] != 'output_text':
                        raise ValueError()
                    parts.append(block['text'])
            text = ''.join(parts)
        if not isinstance(text, str) or len(text) > 16000:
            raise ValueError()
        advice = strict_json(text)
        if set(advice) != {'symbol', 'action', 'summary', 'counter_evidence', 'invalidation'} or advice['symbol'] != symbol or advice['action'] not in ('HOLD', 'BUY', 'SELL'):
            raise ValueError()
        for field in ('summary', 'invalidation'):
            if not isinstance(advice[field], str) or not advice[field].strip() or not 1 <= len(advice[field]) <= 4000:
                raise ValueError()
        evidence = advice['counter_evidence']
        if not isinstance(evidence, list) or len(evidence) > 20 or any(not isinstance(s, str) or not 1 <= len(s) <= 1000 for s in evidence):
            raise ValueError()
        raw_usage = body.get('usage') or {}
        usage = {k: raw_usage.get(k, raw_usage.get(old)) for k, old in [('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens')]}
        if any(v is not None and (type(v) is not int or v < 0) for v in usage.values()):
            raise ValueError()
        return advice, usage
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise invalid() from None
