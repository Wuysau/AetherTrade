"""Append-only offline memory index; text is untrusted data, never policy.

Process-local reference implementation. Production persistence and authenticated
evidence resolution are explicit integration dependencies, not durability claims.
"""
from dataclasses import replace
from threading import RLock
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.rules.sandbox import digest


class MemoryStore:
    def __init__(self, *, environment: t.Environment, now: Callable[[], str],
                 verify_evidence: Callable[[t.EvidenceRef, t.MemoryEntry], bool]):
        if environment == t.Environment.LIVE:
            raise ValueError('LIVE_DISABLED')
        if type(environment) is not t.Environment:
            raise ValueError('INVALID_ENVIRONMENT')
        self._environment, self._now, self._verify = environment, now, verify_evidence
        self._entries = {}
        self._lock = RLock()

    @property
    def environment(self) -> t.Environment:
        return self._environment

    def append(self, entry: t.MemoryEntry) -> t.MemoryRef:
        # Frozen dataclasses do not enforce annotated tuple/string types. Reject
        # mutable containers before hashing so the stored value cannot change
        # after append through a caller-held list.
        if (type(entry) is not t.MemoryEntry or type(entry.id) is not str
                or not entry.id.strip() or len(entry.id) > 256
                or type(entry.text) is not str or type(entry.evidence) is not tuple
                or len(entry.evidence) > 32):
            raise ValueError('INVALID_MEMORY')
        visible, now = utc_timestamp(entry.visible_at), utc_timestamp(self._now())
        if (entry.environment != self.environment or not entry.id
                or entry.classification not in ('FACT', 'INFERENCE', 'EXPIRED')
                or not entry.text or len(entry.text.encode('utf-8')) > 16000 or visible > now):
            raise ValueError('INVALID_MEMORY')
        if entry.expires_at is not None and utc_timestamp(entry.expires_at) <= visible:
            raise ValueError('INVALID_MEMORY_EXPIRY')
        if entry.classification == 'FACT' and not entry.evidence:
            raise ValueError('FACT_REQUIRES_EVIDENCE')
        for ref in entry.evidence:
            if type(ref) not in (t.EvidenceRef, t.SnapshotRef, t.MemoryRef, t.EventRef):
                raise ValueError('MEMORY_EVIDENCE_INVALID')
            digest(ref.hash)
            if (type(ref.id) is not str or not ref.id or type(ref.source) is not str
                    or not ref.source or ref.environment != self.environment
                    or utc_timestamp(ref.visible_at) > visible
                    or self._verify(ref, entry) is not True):
                raise ValueError('MEMORY_EVIDENCE_INVALID')
        ref = t.MemoryRef(id=entry.id, hash=canonical_hash(domain_payload(entry)),
            environment=entry.environment, source='offline-memory', visible_at=entry.visible_at)
        with self._lock:
            existing = self._entries.get(entry.id)
            if existing is not None and existing != entry:
                raise ValueError('MEMORY_ID_CONFLICT')
            self._entries[entry.id] = entry
        return ref

    def entries(self, classification: str, *, as_of: str) -> tuple[t.MemoryEntry, ...]:
        if classification not in ('FACT', 'INFERENCE', 'EXPIRED'):
            raise ValueError('INVALID_CLASSIFICATION')
        at = utc_timestamp(as_of)
        if at > utc_timestamp(self._now()):
            raise ValueError('FUTURE_MEMORY_READ')
        with self._lock:
            entries = tuple(self._entries.values())
        result = []
        for entry in entries:
            if utc_timestamp(entry.visible_at) > at:
                continue
            expired = entry.expires_at is not None and utc_timestamp(entry.expires_at) <= at
            view = replace(entry, classification='EXPIRED') if expired else entry
            if view.classification == classification:
                result.append(view)
        return tuple(sorted(result, key=lambda entry: (utc_timestamp(entry.visible_at), entry.id)))
