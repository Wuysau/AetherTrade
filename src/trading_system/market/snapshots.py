"""Content-addressed snapshot storage with a commit barrier.

A `SnapshotRef` is returned only after the object backend reports a durable
commit and the reference index accepts it. A failed commit or failed reference
publication leaves no loadable reference, so research can never replay a
torn snapshot. This module performs no venue access, holds no credentials and
mints no execution authority.

The in-memory backend is an offline test/local adapter and is not durable.
Deployment owners must supply an object-store backend whose `commit` is a
real fsync/atomic-rename barrier.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.storage.models import decode as decode_record
from trading_system.storage.models import encode as encode_record

from .provenance import is_digest, valid_evidence_ref


class SnapshotError(Exception):
    """Base error for snapshot storage operations."""


class SnapshotPersistenceError(SnapshotError, RuntimeError):
    """The object or reference was not durably published."""


class SnapshotIntegrityError(SnapshotError, ValueError):
    """The packet or reference violates the snapshot contract."""


class SnapshotNotFoundError(SnapshotError, LookupError):
    """No loadable published snapshot exists for the supplied reference."""


class StagedSnapshot(Protocol):
    def commit(self) -> None: ...
    def abort(self) -> None: ...


class SnapshotBackend(Protocol):
    def stage(self, key: str, data: bytes) -> StagedSnapshot: ...
    def read(self, key: str) -> bytes: ...


def snapshot_content_hash(packet: t.SnapshotPacket) -> str:
    """Hash the replayable content, excluding the reference that names it."""
    payload = domain_payload(packet)
    payload.pop('ref', None)
    return canonical_hash(payload)


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('snapshot clock must return an aware datetime')
    return value.astimezone(timezone.utc)


def _timestamp(value: str, label: str) -> datetime:
    try:
        return utc_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotIntegrityError(f'{label} is not an approved UTC timestamp') from exc


def _valid_scope(scope: object) -> bool:
    return (isinstance(scope, t.ScopeKey)
            and isinstance(scope.environment, t.Environment)
            and isinstance(scope.product, t.Product)
            and isinstance(scope.venue_account_id, str) and bool(scope.venue_account_id)
            and isinstance(scope.instrument_uid, str) and bool(scope.instrument_uid)
            and scope.venue_leg in ('BOTH', 'LONG', 'SHORT'))


def _validate_packet(packet: t.SnapshotPacket, now: datetime, *, published: bool) -> str:
    if not isinstance(packet, t.SnapshotPacket):
        raise SnapshotIntegrityError('expected a typed SnapshotPacket')
    scope = packet.scope
    if not _valid_scope(scope):
        raise SnapshotIntegrityError('snapshot scope is invalid')
    if not isinstance(packet.quote, t.Quote):
        raise SnapshotIntegrityError('snapshot quote is invalid')

    quote = packet.quote
    prices = [price for price in (quote.bid, quote.ask, quote.last) if price is not None]
    if any(not isinstance(price, t.Price) for price in prices):
        raise SnapshotIntegrityError('snapshot quote prices must be typed Price values')
    if not prices:
        raise SnapshotIntegrityError('snapshot quote contains no price')
    if len({price.currency for price in prices}) != 1:
        raise SnapshotIntegrityError('snapshot quote mixes currencies')

    occurred = _timestamp(quote.occurred_at, 'quote.occurred_at')
    received = _timestamp(quote.received_at, 'quote.received_at')
    if occurred > received:
        raise SnapshotIntegrityError('quote cannot be received before it occurred')
    published_at = _timestamp(packet.published_at, 'published_at')
    revision_visible = _timestamp(packet.revision_visible_at, 'revision_visible_at')
    if occurred > published_at:
        raise SnapshotIntegrityError('snapshot cannot be published before the quote occurred')
    if published_at > revision_visible:
        raise SnapshotIntegrityError('revision visibility cannot precede publication')
    if any(moment > now for moment in (occurred, received, published_at, revision_visible)):
        raise SnapshotIntegrityError('snapshot content cannot come from the future')

    coverage = packet.coverage
    if not isinstance(coverage, t.Coverage):
        raise SnapshotIntegrityError('snapshot coverage is invalid')
    if coverage.scope != scope:
        raise SnapshotIntegrityError('coverage scope does not match the snapshot scope')
    if any(type(value) is not bool for value in
           (coverage.query_succeeded, coverage.pagination_complete, coverage.supported)):
        raise SnapshotIntegrityError('coverage flags must be booleans')
    if not isinstance(coverage.missing, tuple) or any(
            not isinstance(item, str) or not item for item in coverage.missing):
        raise SnapshotIntegrityError('coverage missing entries must be non-empty names')

    for label, refs in (('private', packet.private_refs), ('economic', packet.economic_refs)):
        if not isinstance(refs, tuple):
            raise SnapshotIntegrityError(f'{label} references must be a tuple')
        if any(not isinstance(ref, t.EvidenceRef)
               or not valid_evidence_ref(ref, scope.environment, now) for ref in refs):
            raise SnapshotIntegrityError(f'{label} evidence reference is invalid')

    ref = packet.ref
    if not isinstance(ref, t.SnapshotRef):
        raise SnapshotIntegrityError('snapshot reference is missing')
    if ref.environment != scope.environment:
        raise SnapshotIntegrityError('snapshot reference environment does not match scope')
    if not isinstance(ref.source, str) or not ref.source:
        raise SnapshotIntegrityError('snapshot reference source is required')

    try:
        digest = snapshot_content_hash(packet)
    except (TypeError, ValueError, AttributeError) as exc:
        raise SnapshotIntegrityError('snapshot content is not losslessly serializable') from exc
    if published:
        if (not is_digest(ref.hash) or not isinstance(ref.id, str) or not ref.id):
            raise SnapshotIntegrityError('published references need an id and content digest')
        if not valid_evidence_ref(ref, scope.environment, now):
            raise SnapshotIntegrityError('published snapshot reference is not yet visible')
    else:
        if ref.hash not in ('', digest):
            raise SnapshotIntegrityError('declared snapshot hash does not match content')
        if not isinstance(ref.id, str):
            raise SnapshotIntegrityError('snapshot reference id must be text')
        if ref.visible_at:
            visible = _timestamp(ref.visible_at, 'reference.visible_at')
            if visible > now:
                raise SnapshotIntegrityError('snapshot reference cannot be visible in the future')
    return digest


class SnapshotStore:
    """Publish a reference only after durable content storage succeeds."""

    def __init__(self, backend: SnapshotBackend, *,
                 clock: Callable[[], datetime] | None = None,
                 publish: Callable[[t.SnapshotRef], None] | None = None):
        if not hasattr(backend, 'stage') or not hasattr(backend, 'read'):
            raise TypeError('snapshot backend must stage and read objects')
        self._backend = backend
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._publish = publish
        self._refs: dict[str, t.SnapshotRef] = {}

    @property
    def published_refs(self) -> list[t.SnapshotRef]:
        """References accepted by this process; a copy, never a mutable index."""
        return list(self._refs.values())

    def put(self, packet: t.SnapshotPacket) -> t.SnapshotRef:
        now = _aware_utc(self._clock())
        digest = _validate_packet(packet, now, published=False)
        existing = self._refs.get(digest)
        if existing is not None:
            return existing

        ref = t.SnapshotRef(id=packet.ref.id or digest, hash=digest,
                            environment=packet.scope.environment,
                            source=packet.ref.source, visible_at=now.isoformat())
        stored = replace(packet, ref=ref)
        blob = encode_record(stored).encode('utf-8')

        try:
            staged = self._backend.stage(digest, blob)
        except Exception as exc:
            raise SnapshotPersistenceError(
                'snapshot content could not be staged') from exc
        try:
            staged.commit()
        except Exception as exc:
            try:
                staged.abort()
            except Exception:
                pass
            raise SnapshotPersistenceError(
                'snapshot content was not durably committed') from exc

        if self._publish is not None:
            try:
                self._publish(ref)
            except Exception as exc:
                # The object may be an orphan; it is not a loadable reference.
                raise SnapshotPersistenceError(
                    'snapshot reference publication failed') from exc
        self._refs[digest] = ref
        return ref

    def load(self, ref: t.SnapshotRef) -> t.SnapshotPacket:
        if (not isinstance(ref, t.SnapshotRef) or not is_digest(ref.hash)
                or not isinstance(ref.id, str) or not ref.id
                or not isinstance(ref.source, str) or not ref.source
                or not isinstance(ref.environment, t.Environment)):
            raise SnapshotIntegrityError('malformed snapshot reference')
        try:
            utc_timestamp(ref.visible_at)
        except (TypeError, ValueError) as exc:
            raise SnapshotIntegrityError('snapshot reference visibility is not a UTC time') from exc
        stored_ref = self._refs.get(ref.hash)
        if stored_ref is None:
            raise SnapshotNotFoundError('snapshot reference is not published')
        if stored_ref != ref:
            raise SnapshotIntegrityError(
                'reference metadata does not match the published snapshot')

        try:
            blob = self._backend.read(ref.hash)
        except (KeyError, FileNotFoundError, OSError) as exc:
            raise SnapshotNotFoundError('snapshot object is unavailable') from exc
        try:
            packet = decode_record(t.SnapshotPacket, json.loads(blob.decode('utf-8')))
        except (TypeError, ValueError, KeyError, AttributeError, UnicodeDecodeError) as exc:
            raise SnapshotIntegrityError('stored snapshot is not a valid packet') from exc
        if not isinstance(packet, t.SnapshotPacket) or packet.ref != stored_ref:
            raise SnapshotIntegrityError('stored snapshot reference was altered')
        if snapshot_content_hash(packet) != ref.hash:
            raise SnapshotIntegrityError('stored snapshot content hash does not match')
        _validate_packet(packet, _aware_utc(self._clock()), published=True)
        return packet


class _MemoryStage:
    __slots__ = ('_backend', '_key', '_data', '_closed')

    def __init__(self, backend: 'InMemorySnapshotBackend', key: str, data: bytes):
        self._backend = backend
        self._key = key
        self._data = data
        self._closed = False

    def commit(self) -> None:
        if self._closed:
            raise RuntimeError('staged snapshot is already closed')
        self._backend._commit(self._key, self._data)
        self._closed = True

    def abort(self) -> None:
        self._closed = True


class InMemorySnapshotBackend:
    """Offline adapter. `objects` is intentionally inspectable for tests."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def stage(self, key: str, data: bytes) -> StagedSnapshot:
        if not is_digest(key):
            raise ValueError('snapshot object key must be a content digest')
        if type(data) is not bytes:
            raise ValueError('snapshot object data must be bytes')
        return _MemoryStage(self, key, data)

    def read(self, key: str) -> bytes:
        return self.objects[key]

    def _commit(self, key: str, data: bytes) -> None:
        self.objects[key] = data


class _LocalStage:
    __slots__ = ('_backend', '_temporary', '_final', '_closed')

    def __init__(self, backend: 'LocalSnapshotBackend', temporary: Path, final: Path):
        self._backend = backend
        self._temporary = temporary
        self._final = final
        self._closed = False

    def commit(self) -> None:
        if self._closed:
            raise RuntimeError('staged snapshot is already closed')
        try:
            os.replace(self._temporary, self._final)
            self._backend._sync_directory()
        except Exception:
            self.abort()
            raise
        self._closed = True

    def abort(self) -> None:
        if not self._closed:
            try:
                self._temporary.unlink()
            except OSError:
                pass
        self._closed = True


class LocalSnapshotBackend:
    """Filesystem adapter: fsync a temporary file, then atomically rename it."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def stage(self, key: str, data: bytes) -> StagedSnapshot:
        if not is_digest(key):
            raise ValueError('snapshot object key must be a content digest')
        if type(data) is not bytes:
            raise ValueError('snapshot object data must be bytes')
        temporary = self.root / f'.{key}.{uuid4().hex}.tmp'
        with open(temporary, 'xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return _LocalStage(self, temporary, self.root / key)

    def read(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def _sync_directory(self) -> None:
        # Directory fsync is unavailable on some platforms; the rename still
        # provides atomic publication. Callers must not treat this as a
        # platform qualification result.
        try:
            descriptor = os.open(self.root, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
