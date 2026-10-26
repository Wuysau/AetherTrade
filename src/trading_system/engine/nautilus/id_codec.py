"""Persistent, scoped application ID registry; no venue format qualification.

The SQLite registry is an offline mapping store, not the business ledger. Live
composition must persist these identities with the IP-10 command transaction.
Hash collisions and unknown IDs fail closed; prefixes never establish ownership.
"""
from __future__ import annotations

from base64 import b32encode
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3
from threading import RLock

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json, domain_payload


ROLES = frozenset({'ORDER', 'ALGORITHM', 'CHILD', 'PROTECTION', 'CLOSE', 'CANCEL', 'AMEND'})


class IdentityConflict(ValueError):
    pass


class UnknownIdentity(LookupError):
    pass


class VenueIdCodec:
    def __init__(self, scope: t.ScopeKey, *, database: str | Path = ':memory:'):
        if (not isinstance(scope, t.ScopeKey) or not scope.venue_account_id
                or not scope.instrument_uid or scope.venue_leg not in ('BOTH', 'LONG', 'SHORT')):
            raise ValueError('explicit account/product/instrument/leg scope required')
        t.Environment(scope.environment)
        t.Product(scope.product)
        self.scope = scope
        self._scope_key = canonical_json(domain_payload(scope))
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.execute('''CREATE TABLE IF NOT EXISTS ip11_identities (
            scope_key TEXT NOT NULL, environment TEXT NOT NULL,
            account TEXT NOT NULL, product TEXT NOT NULL,
            role TEXT NOT NULL, logical_id TEXT NOT NULL,
            native_id TEXT NOT NULL, client_id TEXT NOT NULL, order_id TEXT,
            PRIMARY KEY (scope_key, role, logical_id),
            UNIQUE (environment, account, product, native_id),
            UNIQUE (environment, account, product, client_id),
            UNIQUE (environment, account, product, order_id))''')
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _token(self, logical_id: str, role: str) -> str:
        body = canonical_json({'version': 1, 'scope': domain_payload(self.scope),
                               'logical_id': logical_id, 'role': role})
        return b32encode(sha256(body.encode('utf-8')).digest()[:16]).decode('ascii').rstrip('=')

    def encode(self, logical_id: str, product: t.Product, role: str) -> str:
        if not isinstance(logical_id, str) or not logical_id or len(logical_id) > 4096:
            raise ValueError('nonempty bounded logical ID required')
        if t.Product(product) != self.scope.product or role not in ROLES:
            raise ValueError('product mismatch or unsupported ID role')
        token = self._token(logical_id, role)
        native, client = 'N1' + token, 'V1' + token
        with self._lock, self._db:
            row = self._db.execute(
                'SELECT native_id,client_id FROM ip11_identities '
                'WHERE scope_key=? AND role=? AND logical_id=?',
                (self._scope_key, role, logical_id)).fetchone()
            if row:
                if row != (native, client):
                    raise IdentityConflict('stored ID algorithm/version mismatch')
                return row[1]
            try:
                self._db.execute('INSERT INTO ip11_identities VALUES (?,?,?,?,?,?,?,?,?)',
                    (self._scope_key, str(self.scope.environment), self.scope.venue_account_id,
                     str(self.scope.product), role, logical_id, native, client, None))
            except sqlite3.IntegrityError as exc:
                raise IdentityConflict('ID collision; mapping was not overwritten') from exc
        return client

    def _lookup(self, column: str, value: str) -> tuple[t.OrderIdentity, str]:
        # Column is chosen only by the methods below, never by external input.
        with self._lock:
            row = self._db.execute(
                f'SELECT logical_id,native_id,client_id,order_id,role FROM ip11_identities '
                f'WHERE scope_key=? AND {column}=?', (self._scope_key, value)).fetchone()
        if row is None:
            raise UnknownIdentity('ID is not registered in this exact scope')
        return t.OrderIdentity(scope=self.scope, logical_client_order_id=row[0],
            native_client_order_id=row[1], venue_client_order_id=row[2], venue_order_id=row[3]), row[4]

    def decode(self, venue_id: str) -> t.OrderIdentity:
        """Resolve a venue *client* ID; order IDs have a separate namespace."""
        return self._lookup('client_id', venue_id)[0]

    def decode_native(self, native_id: str) -> t.OrderIdentity:
        return self._lookup('native_id', native_id)[0]

    def decode_order(self, order_id: str) -> t.OrderIdentity:
        return self._lookup('order_id', order_id)[0]

    def role(self, venue_client_id: str) -> str:
        return self._lookup('client_id', venue_client_id)[1]

    def bind_venue_order(self, venue_client_id: str, order_id: str) -> t.OrderIdentity:
        """Trusted reconciliation only: associate an observed ID, never infer one."""
        if not isinstance(order_id, str) or not order_id or len(order_id) > 4096:
            raise ValueError('nonempty bounded observed venue order ID required')
        with self._lock, self._db:
            identity = self.decode(venue_client_id)
            if identity.venue_order_id is not None and identity.venue_order_id != order_id:
                raise IdentityConflict('cannot rebind an existing venue order')
            try:
                self._db.execute('UPDATE ip11_identities SET order_id=? '
                    'WHERE scope_key=? AND client_id=? AND (order_id IS NULL OR order_id=?)',
                    (order_id, self._scope_key, venue_client_id, order_id))
                # Re-read also protects against concurrent connections rebinding.
                updated = self.decode(venue_client_id)
                if updated.venue_order_id != order_id:
                    raise IdentityConflict('concurrent venue order binding conflict')
            except sqlite3.IntegrityError as exc:
                raise IdentityConflict('venue order already belongs to another identity') from exc
        return replace(identity, venue_order_id=order_id)
