"""User-entered, ephemeral Demo connection. No order/enable/credential-save API."""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import re
import secrets
from threading import RLock
import time

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, SecretStr, Field

from trading_system.engine.binance_demo.contracts import DemoError, DemoEndpointRegistry, decimal, digest
from trading_system.engine.binance_demo.credentials import DemoCredentialProvider
from trading_system.engine.binance_demo.transport import BinanceDemoTransport
from .errors import ApiError


class DemoDisconnect(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_epoch: str = Field(min_length=16, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


class DemoConnect(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr
    api_secret: SecretStr
    acknowledge_demo_only: bool = Field(strict=True)
    connection_epoch: str | None = Field(default=None, min_length=16, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


@dataclass
class _Connection:
    credential_ref: str = field(repr=False)
    expires_at: float
    generation: int
    snapshot: dict | None = None


class _ConnectionLease:
    """Internal capability object; never accepted from or serialized to HTTP."""
    def __init__(self, service, session, entry):
        self._service, self._session, self._entry = service, session, entry
        self.credential_ref, self.expires_at = entry.credential_ref, entry.expires_at

    def validate(self):
        with self._service._lock:
            self._service._expire()
            if self._service._entries.get(self._session) is not self._entry:
                raise DemoError('DEMO_SESSION_LEASE_REVOKED')
            self._service.provider.describe(self.credential_ref)
            return True

    def transport(self):
        self.validate()
        return self._service._transport_factory(self._service.provider, self.credential_ref)

    @contextmanager
    def registration_guard(self):
        # Prevent same-process logout/disconnect while registering this lease.
        # Process failure and expiry still require durable fencing/recovery.
        with self._service._lock:
            self.validate()
            yield
            self.validate()


class DemoConnectionService:
    """One connection per authenticated console session, expiring in 15 minutes.

    The application never writes either credential to disk. Python cannot promise
    cryptographic memory erasure; process memory must be protected operationally.
    """
    def __init__(self, transport_factory=None, *, clock=time.time):
        self.provider = DemoCredentialProvider(clock=clock)
        self._transport_factory = transport_factory or BinanceDemoTransport
        self.clock = clock
        self._entries = {}
        self._pending_connect = {}
        self._connection_epochs = {}
        self._generation = 0
        self._closed = False
        self._lock = RLock()
        self.runtime = None

    def connection_lease(self, session_key):
        with self._lock:
            self._expire()
            entry = self._entries.get(session_key)
            if entry is None or entry.snapshot is None:
                raise DemoError('DEMO_CONNECTED_SESSION_REQUIRED')
            return _ConnectionLease(self, session_key, entry)

    @property
    def connection_count(self):
        with self._lock:
            self._expire()
            return len(self._entries)

    def _expire(self):
        for key, (_, expires_at) in list(self._connection_epochs.items()):
            if expires_at <= self.clock():
                del self._connection_epochs[key]
        for key, (_, expires_at) in list(self._pending_connect.items()):
            if expires_at <= self.clock():
                del self._pending_connect[key]
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= self.clock():
                self.provider.remove(entry.credential_ref)
                del self._entries[key]

    def authorize_connect(self, session_key, authenticate, *, connection_epoch=None):
        """Atomically authenticate and issue one in-process connect ticket.

        Logout uses the same lock around session revocation; disconnect cancels
        any ticket issued before it. Tickets contain no credentials.
        """
        from datetime import datetime
        with self._lock:
            if self._closed: raise DemoError('DEMO_SERVICE_CLOSED')
            self._expire()
            session = authenticate()
            self._check_epoch(session_key, connection_epoch)
            if session_key in self._pending_connect:
                raise DemoError('DEMO_CONNECTION_BUSY')
            expires_at = datetime.fromisoformat(session['expires_at'].replace('Z', '+00:00')).timestamp()
            ticket = object()
            self._pending_connect[session_key] = (ticket, min(expires_at, self.clock() + 60))
            return {'session': session, 'ticket': ticket}

    def _check_epoch(self, session_key, supplied):
        epoch = self._connection_epochs.get(session_key)
        if (epoch is None or not isinstance(supplied, str) or
                not secrets.compare_digest(epoch[0], supplied)):
            raise DemoError('DEMO_CONNECTION_EPOCH_STALE')

    def _disconnect_locked(self, session_key):
        self._pending_connect.pop(session_key, None)
        self._connection_epochs.pop(session_key, None)
        entry = self._entries.pop(session_key, None)
        if entry:
            self.provider.remove(entry.credential_ref)

    def revoke_session(self, session_key, revoke):
        # Serialize durable session revocation with connect authentication.
        with self._lock:
            revoke()
            self._disconnect_locked(session_key)
        if self.runtime is not None:
            self.runtime.close_session(session_key)

    def _base(self):
        return {'environment': 'BINANCE_DEMO', 'product': 'SPOT', 'state': 'DISABLED', 'execution_authorized': False,
            'credential_storage': 'MEMORY_ONLY', 'credential_ttl_seconds': 900, 'identity_verified_for_execution': False,
            'endpoint': DemoEndpointRegistry.SPOT_ORIGIN, 'budget_cap_usdt': '10000', 'balances': [],
            'capabilities': [{'product': 'SPOT', 'order_type': kind, 'state': 'COMPILED', 'enabled': False} for kind in ('LIMIT', 'MARKET')],
            'blockers': ['POSTGRESQL_QUALIFICATION_REQUIRED', 'DEMO_ACCOUNT_QUALIFICATION_REQUIRED', 'PRIVATE_STREAM_RECONCILIATION_REQUIRED', 'STRATEGY_AUTHORIZATION_REQUIRED'],
            'usdm_state': 'DISABLED', 'notice': 'Read-only Demo connection. PAPER funds are independent. No trading capability is enabled.'}

    def status(self, session_key):
        with self._lock:
            if self._closed: raise DemoError('DEMO_SERVICE_CLOSED')
            self._expire()
            entry = self._entries.get(session_key)
            result = self._base()
            if session_key not in self._connection_epochs:
                self._connection_epochs[session_key] = (secrets.token_urlsafe(24), self.clock() + 900)
            result['connection_epoch'] = self._connection_epochs[session_key][0]
            if session_key in self._pending_connect:
                result['state'] = 'CONNECTING'
            elif entry:
                result.update(deepcopy(entry.snapshot or {'state': 'CONNECTING'}))
                result.update(self.provider.describe(entry.credential_ref))
                result['expires_at'] = int(entry.expires_at)
                if result.get('checked_at') and self.clock() - result['checked_at'] > 15:
                    result['snapshot_stale'] = True
            return result

    def connect(self, session_key, payload, *, session_expires_at, ticket=None):
        with self._lock:
            if self._closed: raise DemoError('DEMO_SERVICE_CLOSED')
            self._expire()
            if ticket is not None:
                pending = self._pending_connect.get(session_key)
                if pending is None or pending[0] is not ticket:
                    raise DemoError('DEMO_CONNECTION_CANCELED')
                del self._pending_connect[session_key]
                self._check_epoch(session_key, payload.connection_epoch)
                del self._connection_epochs[session_key]
            if not payload.acknowledge_demo_only:
                raise DemoError('DEMO_EXPLICIT_ACK_REQUIRED')
            previous = self._entries.get(session_key)
            if previous and previous.snapshot is None:
                raise DemoError('DEMO_CONNECTION_BUSY')
            if previous:
                self.provider.remove(previous.credential_ref)
                del self._entries[session_key]
            if len(self._entries) >= 4:
                raise DemoError('DEMO_CONNECTION_LIMIT')
            ref = self.provider.put(payload.api_key.get_secret_value(), payload.api_secret.get_secret_value(), ttl_seconds=min(900, session_expires_at - self.clock()))
            self._generation += 1
            entry = _Connection(ref, min(self.clock() + 900, session_expires_at), self._generation)
            self._entries[session_key] = entry
        try:
            with self._transport_factory(self.provider, ref) as transport:
                timing = transport.sync_time()
                account = transport.account()
                rules = transport.exchange_info('BTCUSDT')
                orders = transport.open_orders('BTCUSDT')
            if not isinstance(account, dict) or account.get('accountType') != 'SPOT' or not isinstance(account.get('balances'), list) or len(account['balances']) > 1000:
                raise DemoError('DEMO_ACCOUNT_RESPONSE_INVALID')
            balances = []
            for item in account['balances']:
                asset = item.get('asset')
                if not isinstance(asset, str) or not re.fullmatch(r'[A-Z0-9]{1,20}', asset):
                    raise DemoError('DEMO_BALANCE_RESPONSE_INVALID')
                free, locked = decimal(item.get('free')), decimal(item.get('locked'))
                if free or locked:
                    balances.append({'asset': asset, 'free': str(free), 'locked': str(locked)})
            if not isinstance(rules, dict) or not isinstance(rules.get('symbols'), list) or not any(s.get('symbol') == 'BTCUSDT' for s in rules['symbols']) or not isinstance(orders, list):
                raise DemoError('DEMO_BASELINE_RESPONSE_INVALID')
            snapshot = {'state': 'READ_ONLY', 'balances': balances, 'checked_at': int(self.clock()), 'snapshot_stale': False,
                'server_time': timing, 'symbol': 'BTCUSDT', 'rules_hash': digest(rules), 'open_order_count': len(orders),
                'exchange_can_trade': account.get('canTrade') is True}
            with self._lock:
                self._expire()
                if self._entries.get(session_key) is not entry:
                    raise DemoError('DEMO_CONNECTION_CANCELED')
                entry.snapshot = snapshot
            return self.status(session_key)
        except BaseException:
            with self._lock:
                if self._entries.get(session_key) is entry:
                    del self._entries[session_key]
                self.provider.remove(ref)
            raise

    def disconnect(self, session_key, *, connection_epoch=None, require_epoch=False):
        with self._lock:
            self._expire()
            if require_epoch: self._check_epoch(session_key, connection_epoch)
            self._disconnect_locked(session_key)
        if self.runtime is not None:
            self.runtime.close_session(session_key)
        return self.status(session_key)

    async def aclose(self):
        with self._lock:
            self._closed = True
            self._entries.clear()
            self._pending_connect.clear()
            self._connection_epochs.clear()
            self.provider.clear()
        if self.runtime is not None:
            close_async = getattr(self.runtime, 'aclose', None)
            if close_async is not None:
                await close_async()
            else:
                self.runtime.close()

    def close(self):
        with self._lock:
            self._closed = True
            self._pending_connect.clear()
            self._connection_epochs.clear()
        try:
            if self.runtime is not None:
                self.runtime.close()
        finally:
            with self._lock:
                self._entries.clear()
                self._pending_connect.clear()
                self._connection_epochs.clear()
                self.provider.clear()


def session_key(request, settings):
    return hashlib.sha256((request.cookies.get(settings.cookie_name) or '').encode()).hexdigest()


def install_demo_routes(app, service, authenticated, mutation, settings, connect_mutation):
    @app.get('/api/demo/status')
    def status(request: Request, _=Depends(authenticated)):
        result = service.status(session_key(request, settings))
        if service.runtime is not None:
            result['runtime'] = service.runtime.status(session_key(request, settings))
        return result

    @app.post('/api/demo/connect')
    def connect(payload: DemoConnect, request: Request, authorization=Depends(connect_mutation)):
        from datetime import datetime
        try:
            session = authorization['session']
            return service.connect(session_key(request, settings), payload, ticket=authorization['ticket'],
                session_expires_at=datetime.fromisoformat(session['expires_at'].replace('Z', '+00:00')).timestamp())
        except DemoError as exc:
            raise ApiError(exc.code, 'Demo 只读连接未通过，请检查专用 HMAC API Key、网络和服务器时间。', 422) from None
        except Exception:
            raise ApiError('DEMO_CONNECTION_UNAVAILABLE', 'Demo 连接暂不可用，未启用交易。', 503) from None

    @app.post('/api/demo/disconnect')
    def disconnect(payload: DemoDisconnect, request: Request, _=Depends(mutation)):
        try:
            return service.disconnect(session_key(request, settings), connection_epoch=payload.connection_epoch, require_epoch=True)
        except DemoError as exc:
            raise ApiError(exc.code, 'Demo 凭据移除未通过，请刷新状态后重试。', 422) from None
