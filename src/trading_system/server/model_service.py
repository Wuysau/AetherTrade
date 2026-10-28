"""Manual model configuration and advice; no trading or scheduling authority."""
import asyncio
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
from .store import stamp, strategies


DEFAULT_PROFILE_ID = repository.DEFAULT_PROFILE_ID
EDITABLE = {'name', 'protocol', 'base_url', 'model', 'enabled', 'local_mode', 'max_output_tokens',
            'timeout_seconds', 'anthropic_version'}


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

    def migrate(self):
        with self.store.transaction() as connection:
            repository.ensure_profiles(connection)

    @staticmethod
    def _profile(connection, profile_id):
        if not isinstance(profile_id, str) or not 1 <= len(profile_id) <= 64:
            raise ApiError('MODEL_PROFILE_NOT_FOUND', '模型配置不存在。', 404)
        config = repository.read_profile(connection, profile_id)
        if config is None:
            raise ApiError('MODEL_PROFILE_NOT_FOUND', '模型配置不存在。', 404)
        return config

    def get_config(self, profile_id=DEFAULT_PROFILE_ID):
        with self.store.transaction() as connection:
            return self._public(self._profile(connection, profile_id))

    def list_configs(self):
        with self.store.transaction() as connection:
            profiles = repository.read_profiles(connection)
            profiles.sort(key=lambda p: (p['id'] != DEFAULT_PROFILE_ID, p['name'].casefold(), p['id']))
            return {'profiles': [self._public(p) for p in profiles], 'default_profile_id': DEFAULT_PROFILE_ID}

    @staticmethod
    def _revision(config, revision):
        if type(revision) is not int or revision != config['revision']:
            raise ApiError('MODEL_REVISION_CONFLICT', '模型配置已更新，请重新加载后再操作。', 409)

    @staticmethod
    def _payload(payload, *, creating=False):
        allowed = EDITABLE | {'api_key', 'clear_api_key'} | (set() if creating else {'expected_revision'})
        if not isinstance(payload, dict) or set(payload) - allowed or (creating and 'name' not in payload):
            raise ApiError('INVALID_MODEL_CONFIG', '模型配置字段无效。')

    def create_config(self, payload):
        self._payload(payload, creating=True)
        with self.store.transaction() as connection:
            if len(repository.read_profiles(connection)) >= 20:
                raise ApiError('MODEL_PROFILE_LIMIT', '最多保存 20 个模型配置。', 409)
            identifier = str(uuid4())
            old = dict(repository.DEFAULT, id=identifier, name='')
            config = self._edited(connection, old, payload)
            repository.write_profile(connection, identifier, config)
            return self._public(config)

    def save_config(self, payload, profile_id=DEFAULT_PROFILE_ID):
        self._payload(payload)
        with self.store.transaction() as connection:
            old = self._profile(connection, profile_id)
            self._revision(old, payload.get('expected_revision'))
            config = self._edited(connection, old, payload)
            repository.write_profile(connection, profile_id, config)
            return self._public(config)

    def _edited(self, connection, old, payload):
        config = dict(old, **{k: v for k, v in payload.items() if k in EDITABLE})
        name = config['name']
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ApiError('INVALID_MODEL_CONFIG', '模型配置名称需为 1–80 个有效字符。')
        config['name'] = name.strip()
        if any(p['id'] != old['id'] and p['name'].casefold() == config['name'].casefold()
               for p in repository.read_profiles(connection)):
            raise ApiError('MODEL_PROFILE_NAME_CONFLICT', '模型配置名称已存在。', 409)
        if config['protocol'] not in ('openai_responses', 'openai_chat', 'anthropic'):
            raise ApiError('INVALID_MODEL_CONFIG', '请选择支持的模型协议。')
        for key in ('enabled', 'local_mode'):
            if type(config[key]) is not bool:
                raise ApiError('INVALID_MODEL_CONFIG', '模型开关需为布尔值。')
        for key, lower, upper in [('max_output_tokens', 128, 8192), ('timeout_seconds', 5, 120)]:
            if key == 'max_output_tokens' and config[key] is None and config['protocol'] != 'anthropic':
                continue
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
        return config

    def delete_config(self, profile_id, expected_revision):
        with self.store.transaction() as connection:
            config = self._profile(connection, profile_id)
            self._revision(config, expected_revision)
            if profile_id == DEFAULT_PROFILE_ID:
                raise ApiError('MODEL_PROFILE_RESERVED', '默认模型不能删除，可停用或编辑。', 409)
            if any(profile_id in s.get('model_profile_ids', [])
                   for s in connection.execute(select(strategies.c.data)).scalars()):
                raise ApiError('MODEL_PROFILE_REFERENCED', '策略仍引用此模型，请先修改策略选择。', 409)
            repository.delete_profile(connection, profile_id)
            return {'deleted': True, 'id': profile_id}

    def _snapshot(self, revision, enabled=False, profile_id=DEFAULT_PROFILE_ID):
        with self.store.transaction() as connection:
            config = self._profile(connection, profile_id)
            self._revision(config, revision)
            if not config['model'] or (enabled and not config['enabled']):
                raise ApiError('MODEL_NOT_CONFIGURED', '请配置并启用模型。', 409)
            secret = self.vault.decrypt(config['encrypted_key'])
            if not secret and not config['local_mode']:
                raise ApiError('MODEL_KEY_REQUIRED', '远程模型需要 API Key。', 409)
            return config, secret

    async def test_connection(self, expected_revision, profile_id=DEFAULT_PROFILE_ID):
        config, secret = self._snapshot(expected_revision, profile_id=profile_id)
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
            current = self._profile(connection, profile_id)
            self._revision(current, expected_revision)
            current.update(connection_status='UNAVAILABLE' if error else 'CONNECTED', last_test_at=stamp(), last_error=error.message if error else None)
            repository.write_profile(connection, profile_id, current)
            public = self._public(current)
        if error:
            raise error
        return {'config': public, 'models': models, 'message': '模型目录与凭证已验证；未执行付费推理。'}

    @staticmethod
    def _request_identity(symbol, key):
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9]{3,24}', symbol) or not isinstance(key, str) or not re.fullmatch(r'[\x21-\x7e]{1,128}', key):
            raise ApiError('INVALID_MODEL_REQUEST', '分析标的或幂等键无效。')

    @staticmethod
    def _hash(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _cached(previous, fingerprints):
        if previous:
            if previous['fingerprint'] not in fingerprints:
                raise ApiError('IDEMPOTENCY_CONFLICT', '此幂等键已用于其他分析请求。', 409)
            if previous['status'] == 'SUCCEEDED':
                return previous['result']
            raise ApiError('MODEL_RUN_NOT_RETRYABLE', '此请求已提交或失败，未重复调用模型。', 409)
        return None

    @staticmethod
    def _clean_context(symbol, context):
        if isinstance(context, dict) and 'analysis_snapshot' in context:
            from .analysis_snapshot import validate_snapshot
            snapshot = validate_snapshot(context['analysis_snapshot'])
            if snapshot['market']['quote'] is not None and snapshot['market']['quote']['symbol'] != symbol:
                raise ApiError('INVALID_MODEL_CONTEXT', 'Snapshot symbol mismatch.')
            cutoff = datetime.fromisoformat(snapshot['evidence_cutoff'])
            if not 0 <= (datetime.now(timezone.utc)-cutoff).total_seconds() <= context.get('quote_max_age_seconds', 90):
                raise ApiError('MODEL_QUOTE_UNAVAILABLE', '分析快照已过期。', 409)
            return {'symbol': symbol, 'analysis_snapshot': snapshot}
        try:
            quote = context['quote']
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(quote['as_of'])).total_seconds()
            price = Decimal(quote['price'])
            max_age = context['quote_max_age_seconds']
            if (context['market_status'] not in ('CONNECTED', 'PARTIAL') or quote['symbol'] != symbol
                    or quote.get('stale') is not False or not price.is_finite() or price <= 0
                    or type(max_age) not in (int, float) or not 0 < max_age <= 3600 or not 0 <= age <= max_age):
                raise ValueError()
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
            raise ApiError('MODEL_QUOTE_UNAVAILABLE', '所选标的行情缺失或已过期，未调用模型。', 409) from None
        clean = {'symbol': symbol}
        fields = {'quote': ('symbol', 'price', 'change_pct', 'as_of'),
                  'position': ('symbol', 'quantity', 'average_price', 'avg_price', 'cost_basis'),
                  'account': ('cash', 'equity', 'currency', 'paused'),
                  'risk': ('max_position_pct', 'max_daily_loss')}
        for section, allowed in fields.items():
            value = context.get(section)
            if isinstance(value, dict):
                clean[section] = {k: v for k, v in value.items() if k in allowed and (v is None or type(v) in (str, int, float, bool))}
        try:
            encoded = json.dumps(clean, allow_nan=False)
            if len(encoded) > 16000:
                raise ValueError()
        except (TypeError, ValueError):
            raise ApiError('INVALID_MODEL_CONTEXT', '分析上下文无效。') from None
        return clean

    @staticmethod
    def _identity(config):
        return {'profile_id': config['id'], 'profile_name': config['name'],
                'model': config['model'], 'config_revision': config['revision']}

    @staticmethod
    def _error(exc):
        # Never expose arbitrary transport exception text or a provider body.
        return ({'code': exc.code, 'error': exc.message} if isinstance(exc, ApiError) else
                {'code': 'MODEL_REQUEST_FAILED', 'error': '模型请求未完成；不会自动重试。'})

    async def generate(self, symbol, expected_revision, key, context, *, profile_id=DEFAULT_PROFILE_ID, component=False):
        if isinstance(context, dict) and 'analysis_snapshot' in context:
            return await self.analyze(symbol, 'single', [profile_id], {profile_id: expected_revision}, key, context)
        self._request_identity(symbol, key)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ApiError('INVALID_MODEL_REQUEST', '模型版本无效。')
        fingerprint = self._hash([symbol, 'single', profile_id, expected_revision, component])
        fingerprints = [fingerprint]
        if profile_id == DEFAULT_PROFILE_ID and not component:
            # Receipts created before profiles had this exact two-item digest.
            fingerprints.append(hashlib.sha256(json.dumps([symbol, expected_revision]).encode()).hexdigest())
        # Persist the attempt before network I/O, including on process crashes.
        with self.store.transaction() as connection:
            cached = self._cached(repository.read_run(connection, key), fingerprints)
            if cached is not None:
                return cached
            config = self._profile(connection, profile_id)
            self._revision(config, expected_revision)
            if not config['enabled'] or not config['model']:
                raise ApiError('MODEL_NOT_CONFIGURED', '请配置并启用模型。', 409)
            clean = self._clean_context(symbol, context)
            secret = self.vault.decrypt(config['encrypted_key'])
            if not secret and not config['local_mode']:
                raise ApiError('MODEL_KEY_REQUIRED', '远程模型需要 API Key。', 409)
            if secret and secret in json.dumps(clean, allow_nan=False):
                raise ApiError('INVALID_MODEL_CONTEXT', '分析上下文无效。')
            record = {'fingerprint': fingerprint, 'status': 'IN_FLIGHT', 'created_at': stamp(),
                      'component': component, 'analysis_mode': 'single', 'model_profile_ids': [profile_id],
                      'profile_revisions': {profile_id: expected_revision}, 'profile': self._identity(config)}
            repository.write_run(connection, key, record, new=True)
        try:
            path, body = provider.payload(config, clean)
            response = await provider.request(self.client, config, secret, 'POST', path, body)
            advice, usage = provider.parse(config, response, symbol)
            if secret and any(secret in value for value in [advice['summary'], advice['invalidation'], advice['symbol'], advice['action'], *advice['counter_evidence']]):
                raise provider.invalid()
            decision = dict(advice, id=str(uuid4()), created_at=stamp(), next_review_at=None,
                            risk_status='NOT_EVALUATED', source='AI · ' + config['protocol'], model=config['model'],
                            config_revision=config['revision'], analysis_mode='single', model_profile_id=profile_id,
                            profile_name=config['name'], model_profile_ids=[profile_id],
                            profile_revisions={profile_id: config['revision']}, model_profiles=[self._identity(config)],
                            data_as_of=clean['quote']['as_of'], snapshot_hash=self._hash(clean))
            result = {'decision': decision, 'usage': usage}
        except BaseException as exc:
            with self.store.transaction() as connection:
                repository.write_run(connection, key, dict(record, status='FAILED', **self._error(exc)))
            raise
        with self.store.transaction() as connection:
            repository.write_run(connection, key, dict(record, status='SUCCEEDED', result=result))
        return result

    async def analyze(self, symbol, analysis_mode, profile_ids, expected_revisions, key, context, *, decision_policy='unanimous'):
        self._request_identity(symbol, key)
        if (analysis_mode not in ('single', 'consensus') or not isinstance(profile_ids, list)
                or any(not isinstance(p, str) or not 1 <= len(p) <= 64 for p in profile_ids)
                or len(set(profile_ids)) != len(profile_ids)
                or (analysis_mode == 'single' and len(profile_ids) != 1)
                or (analysis_mode == 'consensus' and not 2 <= len(profile_ids) <= 3)
                or not isinstance(expected_revisions, dict) or set(expected_revisions) != set(profile_ids)
                or any(type(v) is not int or v < 0 for v in expected_revisions.values())):
            raise ApiError('INVALID_MODEL_REQUEST', '请选择一个模型，或选择 2–3 个不同模型交叉分析，并提供对应版本。')
        if decision_policy not in ('unanimous', 'evidence_review'):
            raise ApiError('INVALID_MODEL_REQUEST', 'Unknown decision policy.')
        if isinstance(context, dict) and 'analysis_snapshot' in context:
            return await self._assess(symbol, analysis_mode, list(profile_ids), dict(expected_revisions), key, context, decision_policy)
        if decision_policy != 'unanimous':
            raise ApiError('INVALID_MODEL_REQUEST', 'Evidence review requires a snapshot.')
        # Freeze the caller's mutable selection before the first await.
        profile_ids, expected_revisions = list(profile_ids), dict(expected_revisions)
        if analysis_mode == 'single':
            profile_id = profile_ids[0]
            args = (symbol, expected_revisions[profile_id], key, context)
            if profile_id == DEFAULT_PROFILE_ID:
                return await self.generate(*args)  # Legacy four-argument test doubles and receipts.
            return await self.generate(*args, profile_id=profile_id)

        fingerprint = self._hash([symbol, analysis_mode, [[p, expected_revisions[p]] for p in profile_ids]])
        with self.store.transaction() as connection:
            cached = self._cached(repository.read_run(connection, key), [fingerprint])
            if cached is not None:
                return cached
            configs = [self._profile(connection, p) for p in profile_ids]
            for config in configs:
                self._revision(config, expected_revisions[config['id']])
            clean = self._clean_context(symbol, context)
            encoded = json.dumps(clean, allow_nan=False)
            # Check every selected credential before sharing context with any provider.
            for config in configs:
                try:
                    secret = self.vault.decrypt(config['encrypted_key'])
                except ApiError:
                    continue  # This participant becomes an explicit failed result below.
                if secret and secret in encoded:
                    raise ApiError('INVALID_MODEL_CONTEXT', '分析上下文无效。')
            snapshot_hash = self._hash(clean)
            identities = [self._identity(c) for c in configs]
            record = {'fingerprint': fingerprint, 'status': 'IN_FLIGHT', 'created_at': stamp(),
                      'analysis_mode': 'consensus', 'model_profile_ids': profile_ids,
                      'profile_revisions': expected_revisions, 'model_profiles': identities,
                      'snapshot_hash': snapshot_hash, 'data_as_of': clean['quote']['as_of']}
            repository.write_run(connection, key, record, new=True)
            # This is the same primitive-only context for every participant. Caller
            # mutation while a provider awaits cannot change another request.
            shared_context = dict(clean, market_status=context['market_status'],
                                  quote_max_age_seconds=context['quote_max_age_seconds'])
            shared_context['quote'] = dict(clean['quote'], stale=False)

        async def participant(identity):
            profile_id = identity['profile_id']
            child_key = 'model-component:' + self._hash([key, profile_id])
            try:
                result = await self.generate(symbol, expected_revisions[profile_id], child_key, shared_context,
                                             profile_id=profile_id, component=True)
                return dict(identity, status='SUCCEEDED', decision=result['decision'], usage=result['usage'])
            except Exception as exc:
                failure = self._error(exc)
                # Even a pre-request failure (disabled/deleted/key unavailable)
                # receives a durable child outcome; it is never automatically retried.
                with self.store.transaction() as connection:
                    if repository.read_run(connection, child_key) is None:
                        repository.write_run(connection, child_key, {
                            'fingerprint': self._hash([symbol, 'single', profile_id, expected_revisions[profile_id], True]),
                            'status': 'FAILED', 'created_at': stamp(), 'component': True,
                            'profile': identity, **failure}, new=True)
                return dict(identity, status='FAILED', **failure)

        try:
            outcomes = await asyncio.gather(*(participant(identity) for identity in identities))
            successful = [r for r in outcomes if r['status'] == 'SUCCEEDED']
            actions = {r['decision']['action'] for r in successful}
            complete = len(successful) == len(outcomes)
            status = 'INCOMPLETE' if not complete else 'AGREED' if len(actions) == 1 else 'DISAGREED'
            action = next(iter(actions)) if status == 'AGREED' else 'HOLD'
            summary = (f'{len(outcomes)} 个模型方向一致：{action}。' if status == 'AGREED' else
                       '模型方向存在分歧，汇总为 HOLD。' if status == 'DISAGREED' else
                       f'{len(successful)}/{len(outcomes)} 个模型完成分析，结果不完整，汇总为 HOLD。')
            decision = {'id': str(uuid4()), 'symbol': symbol, 'action': action, 'summary': summary,
                        'counter_evidence': [], 'invalidation': '行情或任一模型配置变化后，应重新分析。',
                        'created_at': stamp(), 'next_review_at': None, 'risk_status': 'NOT_EVALUATED',
                        'source': 'AI · 交叉分析', 'model': ' + '.join(c['model'] for c in configs),
                        'analysis_mode': 'consensus', 'consensus_status': status, 'model_profile_ids': profile_ids,
                        'profile_revisions': expected_revisions, 'model_profiles': identities,
                        'data_as_of': clean['quote']['as_of'], 'snapshot_hash': snapshot_hash, 'model_results': outcomes}
            usage = None
            if complete and all(type(r['usage'].get(k)) is int for r in successful for k in ('input_tokens', 'output_tokens')):
                usage = {k: sum(r['usage'][k] for r in successful) for k in ('input_tokens', 'output_tokens')}
            result = {'decision': decision, 'usage': usage}
        except BaseException as exc:
            with self.store.transaction() as connection:
                repository.write_run(connection, key, dict(record, status='FAILED', **self._error(exc)))
            raise
        with self.store.transaction() as connection:
            repository.write_run(connection, key, dict(record, status='SUCCEEDED', result=result))
        return result

    def decisions(self):
        with self.store.transaction() as connection:
            records = connection.execute(select(repository.runs.c.data)).scalars()
            return sorted([r['result']['decision'] for r in records if r['status'] == 'SUCCEEDED' and not r.get('component')], key=lambda d: d['created_at'], reverse=True)[:200]

    def service_summary(self):
        config = self.get_config()
        return {'id': 'ai', 'label': 'AI 模型', 'status': config['connection_status'],
                'detail': '仅手动分析；模型建议未经过风控，不自动交易。', 'updated_at': config['last_test_at']}

    async def close(self):
        if self._owned_client is not None:
            await self._owned_client.aclose()

    async def _assess(self, symbol, mode, ids, revisions, key, context, policy):
        from .decision_assessment import validate_assessment, validate_review, PROMPT_REVISION
        clean = self._clean_context(symbol, context)
        snapshot = clean['analysis_snapshot']
        fingerprint = self._hash([symbol, mode, ids, revisions, policy, 'assessment_v1', snapshot['task_profile']])
        with self.store.transaction() as connection:
            cached = self._cached(repository.read_run(connection, key), [fingerprint])
            if cached is not None:
                return cached
            configs = {}
            for identifier in ids:
                config = self._profile(connection, identifier)
                self._revision(config, revisions[identifier])
                if not config['enabled'] or not config['model']:
                    raise ApiError('MODEL_NOT_CONFIGURED', 'Model is not enabled.', 409)
                secret = self.vault.decrypt(config['encrypted_key'])
                if not secret and not config['local_mode']:
                    raise ApiError('MODEL_KEY_REQUIRED', 'Model key required.', 409)
                configs[identifier] = (config, secret)
            record = dict(fingerprint=fingerprint, status='IN_FLIGHT', created_at=stamp(), schema_revision='assessment_v1',
                          snapshot=snapshot, requests=[], component=False, analysis_mode=mode, model_profile_ids=ids,
                          profile_revisions=revisions, prompt_revision=PROMPT_REVISION, decision_policy=policy)
            repository.write_run(connection, key, record, new=True)
        components, history = [], []
        usage = {'input_tokens': 0, 'output_tokens': 0}
        max_calls = 2 if mode == 'single' else len(ids) + 1
        deadline = asyncio.get_running_loop().time() + min(180, sum(c[0]['timeout_seconds'] for c in configs.values()) * 2)

        def persist():
            with self.store.transaction() as connection:
                repository.write_run(connection, key, record)

        async def call(identifier, role, extra=None):
            if len(record['requests']) >= max_calls or asyncio.get_running_loop().time() >= deadline:
                raise ApiError('MODEL_BUDGET_EXHAUSTED', 'Call or elapsed-time budget exhausted.', 409)
            config, secret = configs[identifier]
            with self.store.transaction() as connection:
                self._revision(self._profile(connection, identifier), revisions[identifier])
            request_context = dict(clean, role=role, **(extra or {}))
            path, body = provider.payload(config, request_context)
            serialized = json.dumps(body, ensure_ascii=False, allow_nan=False)
            if len(serialized.encode()) > 120000 or any(s and s in serialized for _, s in configs.values()):
                raise ApiError('INVALID_MODEL_CONTEXT', 'Unsafe request context.')
            request_record = dict(profile_id=identifier, provider=config['protocol'], model=config['model'],
                                  config_revision=config['revision'], role=role, path=path, body=body,
                                  input_hash=self._hash(body), prompt_revision=PROMPT_REVISION,
                                  snapshot_hash=snapshot['snapshot_hash'])
            record['requests'].append(request_record)
            persist()  # durable exact request before network I/O
            component = dict(profile_id=identifier, role=role, snapshot_hash=snapshot['snapshot_hash'],
                             config_revision=config['revision'], input_hash=request_record['input_hash'], status='FAILED')
            components.append(component)
            try:
                async with asyncio.timeout(max(0.001, deadline-asyncio.get_running_loop().time())):
                    raw = await provider.request(self.client, config, secret, 'POST', path, body)
                consumed = provider._usage(raw)
                component['usage'] = consumed
                for name in usage:
                    usage[name] += consumed.get(name) or 0
                value, _ = provider.parse(config, raw, symbol, schema='review_v1' if role == 'review' else 'assessment_v1')
                if any(s and s in json.dumps(value) for _, s in configs.values()):
                    raise provider.invalid()
                component.update(status='SUCCEEDED', response=value, usage=consumed)
                # Store parsed provider content only; never hidden reasoning or transport bodies.
                record['component_results'] = components
                persist()
                return value
            except Exception as exc:
                component.update(**self._error(exc))
                record['component_results'] = components
                persist()
                raise

        status, reason, selected, report = 'DECIDED', 'VALID_ASSESSMENT', None, None
        try:
            if snapshot['quality']['status'] != 'READY':
                status, reason = 'DATA_BLOCKED', 'REQUIRED_EVIDENCE_MISSING'
            else:
                raw = await call(ids[0], 'primary')
                try:
                    selected = validate_assessment(raw, snapshot)
                except ValueError as exc:
                    raw = await call(ids[0], 'repair', {'validation_errors': [str(exc)[:300]]})
                    selected = validate_assessment(raw, snapshot)
                if mode == 'consensus' and policy == 'evidence_review':
                    reports = []
                    for identifier in ids[1:]:
                        try:
                            review = validate_review(await call(identifier, 'review', {'candidate': selected}), snapshot)
                        except Exception:
                            status, reason = 'REVIEW_REQUIRED', 'REQUIRED_REVIEW_FAILED'
                            break
                        reports.append(review)
                    if status == 'DECIDED':
                        report = {'issues': [issue for item in reports for issue in item['issues']],
                                  'status': next((s for s in ('HARD', 'UNVERIFIED', 'SOFT') if any(r['status']==s for r in reports)), 'VERIFIED')}
                        if report['status'] == 'HARD':
                            status, reason = 'INVALID_PLAN', 'VERIFIED_REVIEW_DEFECT'
                        elif report['status'] == 'UNVERIFIED':
                            status, reason = 'REVIEW_REQUIRED', 'UNVERIFIED_CRITICAL_FACT'
                        elif report['status'] == 'SOFT':
                            raw = await call(ids[0], 'adjudication', {'candidate': selected, 'review_report': report})
                            history.append(dict(components[-1]))
                            revised = validate_assessment(raw, snapshot)
                            old_plan, new_plan = selected['proposal'], revised['proposal']
                            if new_plan is not None:
                                if old_plan is None:
                                    raise ValueError('Adjudication cannot introduce exposure')
                                old_target, new_target = old_plan['target'], new_plan['target']
                                if new_target['side'] != 'FLAT' and (new_target['side'] != old_target['side'] or new_target['sizing'] != old_target['sizing'] or Decimal(new_target['value']) > Decimal(old_target['value'])):
                                    raise ValueError('Adjudication cannot increase or reverse exposure')
                            selected = revised
                elif mode == 'consensus':
                    for identifier in ids[1:]:
                        try:
                            other = validate_assessment(await call(identifier, 'primary'), snapshot)
                        except Exception:
                            status, reason = 'REVIEW_REQUIRED', 'REQUIRED_REVIEW_FAILED'
                            break
                        # New plans require agreement on executable terms, not just direction.
                        def terms(value):
                            p = value['proposal']
                            return None if p is None else {k:v for k,v in p.items() if k not in ('proposal_id','rationale','evidence_refs')}
                        if terms(other) != terms(selected):
                            status, reason = 'REVIEW_REQUIRED', 'UNANIMOUS_PLAN_DISAGREEMENT'
        except ValueError:
            status, reason = 'INVALID_PLAN', 'INVALID_ASSESSMENT'
        except Exception as exc:
            status, reason = 'MODEL_ERROR', 'MODEL_REQUEST_FAILED'
            if isinstance(exc, ApiError) and exc.code == 'MODEL_BUDGET_EXHAUSTED':
                status, reason = 'REVIEW_REQUIRED', exc.code
        if selected is not None and status == 'DECIDED':
            try:
                validate_assessment(selected, snapshot)
            except ValueError:
                status, reason = 'EXPIRED', 'PLAN_EXPIRED_DURING_REVIEW'
        # A late response is never authority under a changed model revision.
        with self.store.transaction() as connection:
            if any(self._profile(connection, p)['revision'] != revisions[p] for p in ids):
                status, reason = 'EXPIRED', 'MODEL_REVISION_CHANGED'
        proposal = selected['proposal'] if selected and status == 'DECIDED' else None
        if selected and status == 'DECIDED' and proposal is None:
            reason = 'WAIT_CONDITION' if selected['watch_conditions'] else 'MAINTAIN_POSITION' if snapshot['portfolio'].get('position') else 'NO_EDGE'

        decision = dict(id=str(uuid4()), symbol=symbol, created_at=stamp(), schema_revision='assessment_v1',
                        assessment_status=status, reason_code=reason, proposal=proposal,
                        action=(proposal['action'] if proposal else 'HOLD') if status == 'DECIDED' else None,
                        summary=selected['summary'] if selected else reason, counter_evidence=[], invalidation='',
                        evidence_claims=selected['evidence_claims'] if selected else [],
                        watch_conditions=selected['watch_conditions'] if selected and status == 'DECIDED' else [],
                        next_review_at=selected['next_review_at'] if selected and status == 'DECIDED' else None,
                        review_report=report, component_results=components, adjudication_history=history,
                        snapshot_id=snapshot['snapshot_id'], snapshot_hash=snapshot['snapshot_hash'],
                        input_hash=self._hash(clean), strategy_revision=snapshot['task_profile']['strategy_revision'],
                        prompt_revision=PROMPT_REVISION, decision_policy=policy, decision_policy_revision='1',
                        analysis_mode=mode, required_profile_ids=ids, model_profile_ids=ids, profile_revisions=revisions,
                        model_profiles=[self._identity(configs[p][0]) for p in ids], data_as_of=snapshot['evidence_cutoff'],
                        risk_status='NOT_EVALUATED', source='AI', budget={'max_calls':max_calls,'calls':len(record['requests']), 'reserved_output_tokens': (None if any(configs[r['profile_id']][0]['max_output_tokens'] is None for r in record['requests']) else sum(configs[r['profile_id']][0]['max_output_tokens'] for r in record['requests'])), 'usage_complete': all(c.get('usage', {}).get('output_tokens') is not None for c in components)})
        if status == 'DATA_BLOCKED':
            from .analysis_snapshot import readiness_summary
            decision.update(summary=readiness_summary(snapshot), data_quality=snapshot['quality'],
                            missing_required=snapshot['quality']['missing_required'], source='数据就绪检查')
        result = {'decision': decision, 'usage': usage}
        record.update(status='SUCCEEDED', result=result)
        persist()
        return result
