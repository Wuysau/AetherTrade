"""Versioned Demo-only identities. Legacy PAPER scope hashes are unchanged."""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re


class DemoError(ValueError):
    """Only a fixed, non-secret reason code may cross the service boundary."""
    def __init__(self, code: str, *, retry_after: int | None = None):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def decimal(value, *, positive=False) -> Decimal:
    if not isinstance(value, (str, Decimal)):
        raise DemoError('DEMO_DECIMAL_REQUIRED')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise DemoError('DEMO_DECIMAL_INVALID') from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise DemoError('DEMO_DECIMAL_INVALID')
    return result


@dataclass(frozen=True, kw_only=True)
class DemoScope:
    account_id: str
    account_epoch: str
    strategy_id: str
    environment: str = 'BINANCE_DEMO'
    venue: str = 'BINANCE'
    product: str = 'SPOT'
    schema_version: str = 'demo-scope-v1'

    def __post_init__(self):
        if self.environment != 'BINANCE_DEMO' or self.venue != 'BINANCE' or self.product not in {'SPOT', 'USDM'} or self.schema_version != 'demo-scope-v1':
            raise DemoError('DEMO_SCOPE_REQUIRED')
        for value in (self.account_id, self.account_epoch, self.strategy_id):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', value):
                raise DemoError('DEMO_SCOPE_INVALID')

    @property
    def key(self):
        return digest(asdict(self))

    @property
    def account_key(self):
        return digest({k: v for k, v in asdict(self).items() if k != 'strategy_id'})


class DemoEndpointRegistry:
    SPOT_ORIGIN = 'https://demo-api.binance.com'
    SPOT_WS_API = 'wss://demo-ws-api.binance.com/ws-api/v3'
    SPOT_MARKET_WS = 'wss://demo-stream.binance.com/ws'
    USDM_ORIGIN = 'https://demo-fapi.binance.com'
    USDM_WS = 'wss://demo-fstream.binance.com'
    _SPOT = {'GET': frozenset({'/api/v3/time', '/api/v3/exchangeInfo', '/api/v3/account', '/api/v3/order', '/api/v3/openOrders', '/api/v3/allOrders', '/api/v3/myTrades', '/api/v3/ticker/bookTicker'}),
             'POST': frozenset({'/api/v3/order'}), 'DELETE': frozenset({'/api/v3/order'})}

    @classmethod
    def url(cls, product, method, path):
        # USDM, algo/list/batch/cancelReplace and order/test remain unqualified.
        if product != 'SPOT':
            raise DemoError('DEMO_PRODUCT_NOT_QUALIFIED')
        if path not in cls._SPOT.get(method, ()):
            raise DemoError('DEMO_ENDPOINT_REJECTED')
        return cls.SPOT_ORIGIN + path
