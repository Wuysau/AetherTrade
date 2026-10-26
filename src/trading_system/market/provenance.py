"""Untrusted market-intelligence provenance; recording grants no authority.

News text, revisions and retrieval times are recorded so research can reason
about what was visible at a decision point. Nothing in this module converts
external content into capabilities, scopes, credentials or order permission.
The archive is process-local and offline; durable archival belongs to the
intelligence/storage owners and is not claimed here.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, utc_timestamp

_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class ProvenanceError(ValueError):
    """Malformed provenance input; no partial record is retained."""


class NewsProvenanceError(ProvenanceError):
    """News metadata is inconsistent, future-dated or rewritten in place."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_digest(value: object) -> bool:
    """True only for a lowercase 64-character hexadecimal content digest."""
    return type(value) is str and _HASH.fullmatch(value) is not None


def require_utc(value: str, label: str) -> datetime:
    """Parse an approved UTC timestamp or raise a provenance error."""
    try:
        return utc_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ProvenanceError(f"{label} is not an approved UTC timestamp") from exc


def valid_evidence_ref(ref: t.EvidenceRef, environment: t.Environment, now: datetime) -> bool:
    """Structural evidence check; this is not a trust or freshness verdict."""
    try:
        return (bool(ref.id and ref.source)
                and is_digest(ref.hash)
                and ref.environment == environment
                and utc_timestamp(ref.visible_at) <= now)
    except (AttributeError, TypeError, ValueError):
        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class RawNewsItem:
    """Caller-supplied news fields; downloaded_at is supplied separately."""
    news_id: str
    source: str
    title: str
    body: str
    published_at: str
    revised_at: str | None
    revision: int


@dataclass(frozen=True, slots=True, kw_only=True)
class NewsRecord:
    """Append-only normalized record. Classification is data, not permission."""
    news_id: str
    source: str
    title: str
    body: str
    published_at: str
    revised_at: str | None
    downloaded_at: str
    revision: int
    content_hash: str
    recorded_at: str
    classification: Literal['UNTRUSTED_DATA'] = 'UNTRUSTED_DATA'

    def __post_init__(self):
        if self.classification != 'UNTRUSTED_DATA':
            raise ValueError('news records are always untrusted data')

    @property
    def untrusted(self) -> Literal[True]:
        return True


@dataclass(frozen=True, slots=True, kw_only=True)
class UntrustedNewsView:
    """Minimal model/research view. It deliberately has no authority fields."""
    news_id: str
    source: str
    title: str
    body: str
    published_at: str
    revised_at: str | None
    downloaded_at: str
    revision: int
    content_hash: str
    classification: Literal['UNTRUSTED_DATA'] = 'UNTRUSTED_DATA'

    @property
    def untrusted(self) -> Literal[True]:
        return True


def _content_hash(raw: RawNewsItem) -> str:
    payload = {
        'news_id': raw.news_id,
        'source': raw.source,
        'revision': raw.revision,
        'published_at': raw.published_at,
        'revised_at': raw.revised_at,
        'title': raw.title,
        'body': raw.body,
    }
    canonical_json(payload)
    return canonical_hash(payload)


class NewsProvenance:
    """Append-only local news provenance archive.

    The clock is injected for deterministic tests. Revisions are separate
    records: a later revision never mutates the earlier visible version.
    """

    def __init__(self, *, clock: Callable[[], datetime] = utc_now):
        self._clock = clock
        self._records: dict[tuple[str, int], NewsRecord] = {}

    def record(self, raw: RawNewsItem, downloaded_at: str) -> NewsRecord:
        if not isinstance(raw, RawNewsItem):
            raise TypeError('news input must be a typed RawNewsItem')
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise NewsProvenanceError('clock must return an aware datetime')
        now = now.astimezone(timezone.utc)

        if not all(isinstance(value, str) and value.strip()
                   for value in (raw.news_id, raw.source, raw.title)):
            raise NewsProvenanceError('news id, source and title are required')
        if type(raw.body) is not str:
            raise NewsProvenanceError('news body must be text')
        if type(raw.revision) is not int or raw.revision < 1:
            raise NewsProvenanceError('news revision must be a positive integer')

        published = require_utc(raw.published_at, 'published_at')
        downloaded = require_utc(downloaded_at, 'downloaded_at')
        revised = (None if raw.revised_at is None
                   else require_utc(raw.revised_at, 'revised_at'))
        if downloaded > now:
            raise NewsProvenanceError('download time cannot be in the future')
        if published > downloaded:
            raise NewsProvenanceError('news cannot be downloaded before publication')
        if revised is not None:
            if revised < published:
                raise NewsProvenanceError('revision precedes publication')
            if revised > downloaded:
                raise NewsProvenanceError('revision was not yet downloaded')
        if raw.revision > 1 and revised is None:
            raise NewsProvenanceError('later revisions require a revision time')

        digest = _content_hash(raw)
        record = NewsRecord(
            news_id=raw.news_id, source=raw.source, title=raw.title, body=raw.body,
            published_at=raw.published_at, revised_at=raw.revised_at,
            downloaded_at=downloaded_at, revision=raw.revision,
            content_hash=digest, recorded_at=now.isoformat())
        key = (raw.news_id, raw.revision)
        existing = self._records.get(key)
        if existing is not None:
            if existing.content_hash != digest:
                raise NewsProvenanceError('a stored news revision cannot be rewritten')
            return existing
        self._records[key] = record
        return record

    def records(self) -> tuple[NewsRecord, ...]:
        return tuple(self._records.values())

    def revisions(self, news_id: str) -> tuple[NewsRecord, ...]:
        return tuple(record for (stored_id, _), record in sorted(self._records.items())
                     if stored_id == news_id)

    def view(self, record: NewsRecord) -> UntrustedNewsView:
        if not isinstance(record, NewsRecord):
            raise TypeError('only stored news records can be viewed')
        return UntrustedNewsView(
            news_id=record.news_id, source=record.source, title=record.title,
            body=record.body, published_at=record.published_at,
            revised_at=record.revised_at, downloaded_at=record.downloaded_at,
            revision=record.revision, content_hash=record.content_hash)

    def model_view(self, news_id: str, revision: int | None = None) -> UntrustedNewsView:
        """Latest (or exact) revision for research; never an execution input."""
        available = self.revisions(news_id)
        if not available:
            raise NewsProvenanceError('unknown news id')
        if revision is None:
            return self.view(available[-1])
        for record in available:
            if record.revision == revision:
                return self.view(record)
        raise NewsProvenanceError('unknown news revision')
