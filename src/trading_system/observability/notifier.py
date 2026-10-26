"""Durable, redacted notification delivery; never mutates risk state.

The small SQLite spool is independent of the business database. Sender callbacks
must use bounded timeouts and the supplied idempotency key. Delivery may repeat
after a crash: neither this module nor remote delivery promises exactly-once.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from .metrics import utc_now


CAUSES = frozenset({'STORAGE_UNAVAILABLE', 'PROTECTION_MISSING', 'INVALID_PROTECTION',
    'NATIVE_PROTECTION_UNAVAILABLE', 'MODEL_UNAVAILABLE', 'ACCOUNT_STATE_UNKNOWN',
    'EXECUTION_OWNER_UNCONFIRMED', 'ORDER_OUTCOME_UNKNOWN', 'DATA_STALE',
    'BOOK_DRIFT', 'MANUAL_INTERVENTION', 'BUDGET_EXCEEDED'})


@dataclass(frozen=True)
class Notice:
    dedup_key: str
    delivery_key: str
    scope_hash: str
    cause: str
    occurred_at: str
    occurrences: int


class Notifier:
    def __init__(self, path: Path, *, send=None, clock=utc_now,
                 repeat_seconds=60, pending_seconds=30):
        if any(type(v) is not int or v <= 0 for v in (repeat_seconds, pending_seconds)):
            raise ValueError('positive retry bounds required')
        self.path, self.send, self.clock = Path(path), send, clock
        self.repeat_seconds, self.pending_seconds = repeat_seconds, pending_seconds
        # Private provisioned parent; no arbitrary credential path is consulted.
        if self.path.is_symlink():
            raise ValueError('notification spool symlink rejected')
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            metadata = self.path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError('regular notification spool required')
            if os.name == 'posix' and (metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
                raise ValueError('private notification spool required')
        else:
            os.close(fd)
        with self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS notices (
                    key TEXT PRIMARY KEY, occurrences INTEGER NOT NULL,
                    attempt INTEGER NOT NULL, status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL, delivered_at TEXT);
                CREATE TABLE IF NOT EXISTS identities (
                    id_hash TEXT PRIMARY KEY, payload_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    key TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, PRIMARY KEY(key, attempt));
            ''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    def publish(self, alert: t.RiskAlert) -> t.NotificationReceipt:
        failed = t.NotificationReceipt(alert_id=alert.alert_id, status='FAILED', delivered_at=None)
        try:
            if (not alert.alert_id or not alert.cause or not isinstance(alert.scope, t.ScopeKey)
                    or alert.scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH)):
                return failed
            now = self.clock()
            instant = utc_timestamp(now)
            if utc_timestamp(alert.occurred_at) > instant:
                return failed
            scope_hash = canonical_hash(domain_payload(alert.scope))
            key = canonical_hash({'scope': scope_hash, 'cause': alert.cause})
            identity = hashlib.sha256(alert.alert_id.encode()).hexdigest()
            payload_hash = canonical_hash(domain_payload(alert))
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                old_identity = db.execute('SELECT payload_hash FROM identities WHERE id_hash=?', (identity,)).fetchone()
                if old_identity and old_identity[0] != payload_hash:
                    db.rollback()
                    return failed
                db.execute('INSERT OR IGNORE INTO identities VALUES (?,?)', (identity, payload_hash))
                row = db.execute('SELECT occurrences,attempt,status,attempted_at,delivered_at FROM notices WHERE key=?', (key,)).fetchone()
                count = row[0] + 1 if row else 1
                if row:
                    age = (instant - utc_timestamp(row[3])).total_seconds()
                    if age < 0:
                        db.rollback()
                        return failed
                    if ((row[2] == 'DELIVERED' and age < self.repeat_seconds)
                            or (row[2] == 'QUEUED' and age < self.pending_seconds)):
                        db.execute('UPDATE notices SET occurrences=? WHERE key=?', (count, key))
                        db.commit()
                        return t.NotificationReceipt(alert_id=alert.alert_id, status=row[2], delivered_at=row[4])
                attempt = row[1] + 1 if row else 1
                db.execute('INSERT OR REPLACE INTO notices VALUES (?,?,?,?,?,NULL)',
                           (key, count, attempt, 'QUEUED', now))
                db.execute('INSERT INTO attempts VALUES (?,?,?,?)', (key, attempt, 'QUEUED', now))
                db.commit()
            notice = Notice(key, key + ':' + str(attempt), scope_hash,
                            alert.cause if alert.cause in CAUSES else 'UNCLASSIFIED_RISK',
                            alert.occurred_at, count)
            status, delivered = 'FAILED', None
            try:
                # Only an explicit True acknowledgment is delivery evidence.
                if self.send is not None and self.send(notice) is True:
                    delivered = self.clock()
                    if utc_timestamp(delivered) < instant:
                        raise ValueError('clock reversed during delivery')
                    status = 'DELIVERED'
            except Exception:
                status, delivered = 'FAILED', None
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE attempts SET status=? WHERE key=? AND attempt=?', (status, key, attempt))
                updated = db.execute('UPDATE notices SET status=?,delivered_at=? WHERE key=? AND attempt=?',
                                     (status, delivered, key, attempt)).rowcount
                db.commit()
            # A newer retry owns the current result; do not claim its success.
            if not updated:
                return failed
            return t.NotificationReceipt(alert_id=alert.alert_id, status=status, delivered_at=delivered)
        except Exception:
            # Storage failure is visible, with no false delivery or risk release.
            # Exceptions and original evidence may contain secrets: never log them.
            return failed

    def history(self):
        """Redacted local attempt audit; durable across notifier restarts."""
        with self._connection() as db:
            return tuple(db.execute('SELECT key,attempt,status,occurred_at FROM attempts ORDER BY key,attempt'))
