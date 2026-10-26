"""One additive, revisioned market source; credentials never leave runtime_config."""
from copy import deepcopy

from sqlalchemy import Column, Integer, JSON, Table, insert, select, update

from .errors import ApiError
from .market_transport import validate_url
from .model_secrets import ModelSecrets
from .store import metadata


configs = Table('console_market_config', metadata,
                Column('id', Integer, primary_key=True), Column('data', JSON, nullable=False))
DEFAULT = dict(provider='binance', name='Binance 官方行情',
               rest_url='https://data-api.binance.vision',
               ws_url='wss://data-stream.binance.vision/stream',
               auth_scheme='none', local_mode=False, enabled=True,
               poll_seconds=5, timeout_seconds=8, revision=0, encrypted_key=None)
EDITABLE = set(DEFAULT) - {'revision', 'encrypted_key'}
DESTINATION = ('provider', 'rest_url', 'ws_url', 'auth_scheme', 'local_mode')


def invalid():
    return ApiError('MARKET_INVALID_CONFIG', '行情源配置字段或取值无效。')


class MarketConfigService:
    def __init__(self, store, key_path):
        self.store = store
        self.vault = ModelSecrets(key_path)

    @staticmethod
    def _read(connection):
        data = connection.execute(select(configs.c.data).where(configs.c.id == 1)).scalar_one_or_none()
        if data is None:
            data = dict(DEFAULT)
            connection.execute(insert(configs).values(id=1, data=data))
        return deepcopy(data)

    def migrate(self):
        # Also supports an already initialized Store; never mutates model tables.
        configs.create(self.store.engine, checkfirst=True)
        with self.store.transaction() as connection:
            self._read(connection)

    def _decrypt(self, encrypted):
        try:
            return self.vault.decrypt(encrypted)
        except ApiError:
            raise ApiError('MARKET_KEY_UNAVAILABLE', '行情密钥无法读取，请恢复主密钥或重新输入 API Key。', 503) from None

    def _public(self, data):
        public = {key: deepcopy(data[key]) for key in EDITABLE | {'revision'}}
        public['has_api_key'] = bool(data['encrypted_key'])
        try:
            public['key_available'] = bool(self._decrypt(data['encrypted_key']))
        except ApiError:
            public['key_available'] = False
        return public

    def get_config(self):
        with self.store.transaction() as connection:
            return self._public(self._read(connection))

    def runtime_config(self):
        with self.store.transaction() as connection:
            data = self._read(connection)
            secret = self._decrypt(data['encrypted_key'])
            return dict({key: deepcopy(data[key]) for key in EDITABLE | {'revision'}},
                        has_api_key=bool(data['encrypted_key']), key_available=bool(secret), api_key=secret)

    @staticmethod
    def _validate(data):
        for key, choices in [('provider', ('binance', 'binance_compatible', 'standard_json')),
                             ('auth_scheme', ('none', 'bearer', 'x_api_key'))]:
            if not isinstance(data[key], str) or data[key] not in choices:
                raise invalid()
        name = data['name']
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise invalid()
        data['name'] = name.strip()
        if any(type(data[key]) is not bool for key in ('enabled', 'local_mode')):
            raise invalid()
        for key, low, high in [('poll_seconds', 2, 60), ('timeout_seconds', 2, 20)]:
            if type(data[key]) is not int or not low <= data[key] <= high:
                raise invalid()
        data['rest_url'] = validate_url(data['rest_url'], data['local_mode'])
        if data['ws_url'] != '':
            data['ws_url'] = validate_url(data['ws_url'], data['local_mode'], websocket=True)

    def save_config(self, payload):
        if (not isinstance(payload, dict) or set(payload) - (EDITABLE | {'expected_revision', 'api_key', 'clear_api_key'})
                or 'expected_revision' not in payload):
            raise invalid()
        if type(payload['expected_revision']) is not int or payload['expected_revision'] < 0:
            raise invalid()
        if 'api_key' in payload and 'clear_api_key' in payload:
            raise invalid()
        if 'clear_api_key' in payload and type(payload['clear_api_key']) is not bool:
            raise invalid()
        secret = payload.get('api_key')
        if 'api_key' in payload and (not isinstance(secret, str) or not 1 <= len(secret) <= 4096
                                     or any(not 33 <= ord(c) <= 126 for c in secret)):
            raise invalid()
        with self.store.transaction() as connection:
            old = self._read(connection)
            if old['revision'] != payload['expected_revision']:
                raise ApiError('MARKET_REVISION_CONFLICT', '行情源配置已更新，请重新加载后再操作。', 409)
            data = dict(old, **{key: value for key, value in payload.items() if key in EDITABLE})
            # Validate supplied values before applying the explicit official preset.
            self._validate(data)
            preset = payload.get('provider') == 'binance'
            if preset:
                if secret:
                    raise invalid()
                data.update({key: DEFAULT[key] for key in ('name', 'rest_url', 'ws_url', 'auth_scheme', 'local_mode')})
            clear = preset or payload.get('clear_api_key', False)
            if (old['encrypted_key'] and any(data[key] != old[key] for key in DESTINATION)
                    and not (clear or secret)):
                raise ApiError('MARKET_KEY_REENTRY_REQUIRED', '更改行情地址、协议或认证方式时，请重新输入或清除 API Key。')
            if secret:
                try:
                    data['encrypted_key'] = self.vault.encrypt(secret)
                except ApiError:
                    raise ApiError('MARKET_KEY_UNAVAILABLE', '行情密钥存储不可用。', 503) from None
            elif clear:
                data['encrypted_key'] = None
            data['revision'] = old['revision'] + 1
            connection.execute(update(configs).where(configs.c.id == 1).values(data=data))
            return self._public(data)
