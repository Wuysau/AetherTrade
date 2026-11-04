"""Ephemeral HMAC credentials. Never persisted or rendered in diagnostics."""
from dataclasses import dataclass, field
import hashlib
import hmac
import re
from threading import RLock, Timer
import time
from uuid import uuid4

from .contracts import DemoError


@dataclass(repr=False)
class _Credential:
    api_key: str = field(repr=False)
    secret: str = field(repr=False)
    expires_at: float = field(repr=False)


class DemoCredentialProvider:
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self._timers = {}
        self._values = {}
        self._lock = RLock()

    def put(self, api_key: str, secret: str, *, ttl_seconds=900) -> str:
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{16,256}', v) for v in (api_key, secret)):
            raise DemoError('DEMO_HMAC_CREDENTIAL_INVALID')
        if not isinstance(ttl_seconds, (int, float)) or not 0 < ttl_seconds <= 900:
            raise DemoError('DEMO_CREDENTIAL_TTL_INVALID')
        ref = 'demo-session:' + str(uuid4())
        with self._lock:
            self._values[ref] = _Credential(api_key, secret, self.clock() + ttl_seconds)
            timer = Timer(ttl_seconds, self.remove, args=(ref,))
            timer.daemon = True
            self._timers[ref] = timer
            timer.start()
        return ref

    def _get(self, ref):
        value = self._values.get(ref)
        if value is not None and value.expires_at <= self.clock():
            self.remove(ref)
            value = None
        if value is None:
            raise DemoError('DEMO_CREDENTIAL_UNAVAILABLE')
        return value

    def describe(self, ref):
        with self._lock:
            value = self._get(ref)
            return {'credential_ref': ref, 'fingerprint': hashlib.sha256(value.api_key.encode()).hexdigest()[:16], 'key_type': 'HMAC_SHA256'}

    def sign(self, ref, payload):
        with self._lock:
            return hmac.new(self._get(ref).secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def api_key(self, ref):
        with self._lock:
            return self._get(ref).api_key

    def remove(self, ref):
        with self._lock:
            self._values.pop(ref, None)
            timer = self._timers.pop(ref, None)
            if timer:
                timer.cancel()

    def clear(self):
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._values.clear()
