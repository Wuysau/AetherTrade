"""Verification only. Signing keys belong to independent deployment/acceptance tools.

Filesystem ownership/isolation is an operator prerequisite, not established by
this module. A content hash measures bytes; it is not an OS attestation.
"""
import base64
import hashlib
import json
import re
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .contracts import DemoError


def canonical(payload):
    return b'AetherTrade/BINANCE_DEMO/proof/v1\x00' + json.dumps(
        payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class TrustStore:
    def __init__(self, issuers, *, revoked=(), test_only=False):
        self.test_only = test_only
        self.revoked = frozenset(revoked)
        self._issuers = {}
        try:
            if not issuers or type(test_only) is not bool: raise ValueError()
            for name, item in issuers.items():
                if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name) or
                        set(item) != {'key','purposes','test_only'} or type(item['test_only']) is not bool or
                        item['test_only'] != test_only or not isinstance(item['purposes'],list) or
                        not item['purposes'] or not set(item['purposes']) <= {'deployment','qualification','recovery','qualification_run'}):
                    raise ValueError()
                self._issuers[name] = (Ed25519PublicKey.from_public_bytes(bytes.fromhex(item['key'])), frozenset(item['purposes']))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise DemoError('DEMO_TRUST_CONFIGURATION_INVALID') from None

    def verify(self, envelope, *, purpose, bindings, now):
        try:
            if type(now) is not int or type(envelope) is not dict or set(envelope) != {'payload','signature'}: raise ValueError()
            payload = envelope['payload']
            if (type(payload) is not dict or set(payload) != {'version','issuer','purpose','nonce','issued_at','expires_at','bindings','claims'} or
                    type(payload['version']) is not int or payload['version'] != 1 or
                    type(payload['issued_at']) is not int or type(payload['expires_at']) is not int or
                    not 0 <= payload['issued_at'] <= now < payload['expires_at'] or
                    payload['expires_at'] - payload['issued_at'] > 86400 or
                    not isinstance(payload['nonce'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', payload['nonce']) or
                    payload['nonce'] in self.revoked or payload['purpose'] != purpose or
                    payload['bindings'] != bindings or type(payload['claims']) is not dict): raise ValueError()
            key, purposes = self._issuers[payload['issuer']]
            if purpose not in purposes: raise ValueError()
            encoded = canonical(payload)
            if len(encoded) > 65536: raise ValueError()
            key.verify(base64.b64decode(envelope['signature'],validate=True), encoded)
            return json.loads(json.dumps(payload))
        except (ValueError, TypeError, KeyError, InvalidSignature, RecursionError):
            raise DemoError('DEMO_ATTESTATION_INVALID') from None


def measure_build(root):
    root = Path(root).resolve()
    required = [root/'src/trading_system',root/'migrations',root/'pyproject.toml']
    if not all(p.exists() for p in required): raise DemoError('DEMO_BUILD_MEASUREMENT_REQUIRED')
    files = [required[-1]]
    files += [p for p in required[0].rglob('*') if p.is_file() and p.suffix in {'.py','.json','.yaml'}]
    files += list(required[1].glob('*.sql'))
    result = hashlib.sha256()
    for path in sorted(files):
        if path.is_symlink() or not path.resolve().is_relative_to(root): raise DemoError('DEMO_BUILD_PATH_REJECTED')
        name = path.relative_to(root).as_posix().encode()
        result.update(len(name).to_bytes(4,'big') + name + hashlib.sha256(path.read_bytes()).digest())
    return result.hexdigest()
