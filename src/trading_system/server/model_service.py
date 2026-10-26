"""Manual model configuration and advice; no trading or scheduling authority."""
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

import httpx
from sqlalchemy import select

from .errors import ApiError
from . import model_client as provider
from . import model_repository as repository
from .model_secrets import ModelSecrets
from .store import stamp


class ModelService:
    def __init__(self, store, key_path, *, client=None):
        self.store = store
        self.vault = ModelSecrets(key_path)
        # Requests pin an approved IP while TLS authenticates the original host.
        # A pool keyed by the pinned IP must never reuse TLS across hostnames.
        self.client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False, auth=None,
                                                http1=True, http2=False,
                                                limits=httpx.Limits(max_connections=4, max_keepalive_connections=0))
        self._owned_client = self.client if client is None else None

    def _public(self, config):
        public = {k: v for k, v in config.items() if k != 'encrypted_key'}
        public['has_api_key'] = bool(config['encrypted_key'])
        try:
            public['key_available'] = bool(self.vault.decrypt(config['encrypted_key']))
        except ApiError:
            public['key_available'] = False
        return public

    def get_config(self):
        with self.store.transaction() as connection:
            return self._public(repository.read_config(connection))

    @staticmethod
    def _revision(config, revision):
        if type(revision) is not int or revision != config['revision']:
            raise ApiError('MODEL_REVISION_CONFLICT', '模型配置已更新，请重新加载后再操作。', 409)

    def save_config(self, payload):
        editable = {'protocol', 'base_url', 'model', 'enabled', 'local_mode', 'max_output_tokens', 'timeout_seconds', 'anthropic_version'}
        if not isinstance(payload, dict) or set(payload) - editable - {'expected_revision', 'api_key', 'clear_api_key'}:
            raise ApiError('INVALID_MODEL_CONFIG', '模型配置字段无效。')
        with self.store.transaction() as connection:
            old = repository.read_config(connection)
            self._revision(old, payload.get('expected_revision'))
            config = dict(old, **{k: v for k, v in payload.items() if k in editable})
            if config['protocol'] not in ('openai_responses', 'openai_chat', 'anthropic'):
                raise ApiError('INVALID_MODEL_CONFIG', '请选择支持的模型协议。')
            for key in ('enabled', 'local_mode'):
                if type(config[key]) is not bool:
                    raise ApiError('INVALID_MODEL_CONFIG', '模型开关需为布尔值。')
            for key, lower, upper in [('max_output_tokens', 128, 8192), ('timeout_seconds', 5, 120)]:
                if type(config[key]) is not int or not lower <= config[key] <= upper:
                    raise ApiError('INVALID_MODEL_CONFIG', '模型请求限制不在允许范围内。')
            if not isinstance(config['model'], str) or not 1 <= len(config['model'].strip()) <= 200 or any(ord(c) < 32 for c in config['model']):
                raise ApiError('INVALID_MODEL_CONFIG', '请填写有效模型名称（最多 200 字符）。')
            if not isinstance(config['anthropic_version'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', config['anthropic_version']):
                raise ApiError('INVALID_MODEL_CONFIG', 'Claude API 版本需使用 YYYY-MM-DD。')
            config['base_url'] = provider.validate_url(config['base_url'], config['local_mode'])
            clear = payload.get('clear_api_key', False)
            secret = payload.get('api_key')
            if type(clear) is not bool or (secret is not None and (not isinstance(secret, str) or len(secret) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in secret))):
                raise ApiError('INVALID_MODEL_CONFIG', 'API Key 格式无效。')
            if clear and secret:
                raise ApiError('INVALID_MODEL_CONFIG', '清除和设置 API Key 不能同时使用。')
            if old['encrypted_key'] and any(old[k] != config[k] for k in ('base_url', 'protocol')) and not (clear or secret):
                raise ApiError('MODEL_KEY_REENTRY_REQUIRED', '更改模型地址或协议时，请重新输入或清除 API Key。')
            config['encrypted_key'] = None if clear else self.vault.encrypt(secret) if secret else old['encrypted_key']
            config.update(revision=old['revision'] + 1, connection_status='CONFIGURED', last_test_at=None, last_error=None)
            repository.write_config(connection, config)
            return self._public(config)

    def _snapshot(self, revision, enabled=False):
        with self.store.transaction() as connection:
            config = repository.read_config(connection)
            self._revision(config, revision)
            if not config['model'] or (enabled and not config['enabled']):
                raise ApiError('MODEL_NOT_CONFIGURED', '请配置并启用模型。', 409)
            secret = self.vault.decrypt(config['encrypted_key'])
            if not secret and not config['local_mode']:
                raise ApiError('MODEL_KEY_REQUIRED', '远程模型需要 API Key。', 409)
            return config, secret

    async def test_connection(self, expected_revision):
        config, secret = self._snapshot(expected_revision)
        error = None
        models = []
        try:
            body = await provider.request(self.client, config, secret, 'GET', '/models')
            data = body.get('data')
            if not isinstance(data, list) or len(data) > 2000:
                raise provider.invalid()
            for item in data:
                name = item.get('id') if isinstance(item, dict) else None
                if not isinstance(name, str) or not 1 <= len(name) <= 200 or any(ord(c) < 32 for c in name):
                    raise provider.invalid()
                if secret and secret in name:
                    raise provider.invalid()
                models.append(name)
            if config['model'] not in models:
                raise ApiError('MODEL_NOT_LISTED', '目录未包含所选模型；尚未验证连接。', 422)
        except ApiError as exc:
            error = exc
        with self.store.transaction() as connection:
            current = repository.read_config(connection)
            self._revision(current, expected_revision)
            current.update(connection_status='UNAVAILABLE' if error else 'CONNECTED', last_test_at=stamp(), last_error=error.message if error else None)
            repository.write_config(connection, current)
            public = self._public(current)
        if error:
            raise error
        return {'config': public, 'models': models, 'message': '模型目录与凭证已验证；未执行付费推理。'}

    async def generate(self, symbol, expected_revision, key, context):
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9]{3,24}', symbol) or not isinstance(key, str) or not re.fullmatch(r'[\x21-\x7e]{1,128}', key):
            raise ApiError('INVALID_MODEL_REQUEST', '分析标的或幂等键无效。')
        if type(expected_revision) is not int:
            raise ApiError('INVALID_MODEL_REQUEST', '模型版本无效。')
        fingerprint = hashlib.sha256(json.dumps([symbol, expected_revision]).encode()).hexdigest()
        # Persist the attempt before network I/O, including on process crashes.
        with self.store.transaction() as connection:
            previous = repository.read_run(connection, key)
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise ApiError('IDEMPOTENCY_CONFLICT', '此幂等键已用于其他分析请求。', 409)
                if previous['status'] == 'SUCCEEDED':
                    return previous['result']
                raise ApiError('MODEL_RUN_NOT_RETRYABLE', '此请求已提交或失败，未重复调用模型。', 409)
            config = repository.read_config(connection)
            self._revision(config, expected_revision)
            if not config['enabled'] or not config['model']:
                raise ApiError('MODEL_NOT_CONFIGURED', '请配置并启用模型。', 409)
            try:
                quote = context['quote']
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(quote['as_of'])).total_seconds()
                price = Decimal(quote['price'])
                max_age = context['quote_max_age_seconds']
                if (context['market_status'] not in ('CONNECTED', 'PARTIAL') or quote['symbol'] != symbol
                        or quote.get('stale') is not False or not price.is_finite() or price <= 0
                        or type(max_age) not in (int, float) or not 0 < max_age <= 3600 or not 0 <= age <= max_age):
                    raise ValueError()
            except (KeyError, TypeError, ValueError, InvalidOperation):
                raise ApiError('MODEL_QUOTE_UNAVAILABLE', '所选标的行情缺失或已过期，未调用模型。', 409) from None
            secret = self.vault.decrypt(config['encrypted_key'])
            if not secret and not config['local_mode']:
                raise ApiError('MODEL_KEY_REQUIRED', '远程模型需要 API Key。', 409)
            record = {'fingerprint': fingerprint, 'status': 'IN_FLIGHT', 'created_at': stamp()}
            repository.write_run(connection, key, record, new=True)
        try:
            clean = {'symbol': symbol}
            fields = {'quote': ('symbol', 'price', 'change_pct', 'as_of'),
                      'position': ('symbol', 'quantity', 'average_price', 'avg_price', 'cost_basis'),
                      'account': ('cash', 'equity', 'currency', 'paused'),
                      'risk': ('max_position_pct', 'max_daily_loss')}
            for section, allowed in fields.items():
                value = context.get(section)
                if isinstance(value, dict):
                    clean[section] = {k: v for k, v in value.items() if k in allowed and (v is None or type(v) in (str, int, float, bool))}
            encoded = json.dumps(clean, allow_nan=False)
            if len(encoded) > 16000 or (secret and secret in encoded):
                raise ApiError('INVALID_MODEL_CONTEXT', '分析上下文无效。')
            path, body = provider.payload(config, clean)
            response = await provider.request(self.client, config, secret, 'POST', path, body)
            advice, usage = provider.parse(config, response, symbol)
            if secret and any(secret in value for value in [advice['summary'], advice['invalidation'], advice['symbol'], advice['action'], *advice['counter_evidence']]):
                raise provider.invalid()
            decision = dict(advice, id=str(uuid4()), created_at=stamp(), next_review_at=None,
                            risk_status='NOT_EVALUATED', source='AI · ' + config['protocol'], model=config['model'], config_revision=config['revision'])
            result = {'decision': decision, 'usage': usage}
        except BaseException:
            with self.store.transaction() as connection:
                repository.write_run(connection, key, dict(record, status='FAILED'))
            raise
        with self.store.transaction() as connection:
            repository.write_run(connection, key, dict(record, status='SUCCEEDED', result=result))
        return result

    def decisions(self):
        with self.store.transaction() as connection:
            records = connection.execute(select(repository.runs.c.data)).scalars()
            return sorted([r['result']['decision'] for r in records if r['status'] == 'SUCCEEDED'], key=lambda d: d['created_at'], reverse=True)[:200]

    def service_summary(self):
        config = self.get_config()
        return {'id': 'ai', 'label': 'AI 模型', 'status': config['connection_status'],
                'detail': '仅手动分析；模型建议未经过风控，不自动交易。', 'updated_at': config['last_test_at']}

    async def close(self):
        if self._owned_client is not None:
            await self._owned_client.aclose()
