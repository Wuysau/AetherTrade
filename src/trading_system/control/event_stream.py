"""Scoped event catch-up with explicit gaps. No venue connection is opened.

The feed port supplies a durable, per-scope monotonic position and retained
records. This module binds a cursor to the exact scope and stream before reading,
keeps aggregate revisions in append order, and never converts a lost range into
a silently "latest" stream. A gap always requires a fresh snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_system.contracts import types as t
from trading_system.contracts.canonical import (
    canonical_hash,
    domain_payload,
    utc_timestamp,
)
from trading_system.storage.models import SequenceConflict
from .auth import ControlError


MAX_EVENT_PAGE = 1000
DEFAULT_EVENT_PAGE = 256


@dataclass(frozen=True, kw_only=True)
class EventRecord:
    """One durably appended event and its per-scope stream position."""

    position: int
    event: t.DomainEvent


class EventFeed(Protocol):
    def watermark(self, scope: t.ScopeKey) -> int: ...

    def retained_from(self, scope: t.ScopeKey) -> int: ...

    def read_after(self, scope: t.ScopeKey, after: int, limit: int
                   ) -> tuple[EventRecord, ...]: ...


def event_stream_id(scope: t.ScopeKey) -> str:
    """Bind a cursor to one exact environment/account/product/instrument/leg."""
    return 'events:v1:' + canonical_hash(domain_payload(scope))


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _validate_scope(scope: t.ScopeKey) -> None:
    if not isinstance(scope, t.ScopeKey):
        raise ControlError(400, 'INVALID_SCOPE')
    if scope.environment == t.Environment.LIVE:
        raise ControlError(403, 'LIVE_NOT_QUALIFIED')


def _nonnegative_int(value: object, code: str) -> int:
    if _is_bool(value) or type(value) is not int or value < 0:
        raise ControlError(503, code)
    return value


class EventStream:
    """Implementation of the frozen EventStream port."""

    def __init__(self, feed: EventFeed, *, page_limit: int = DEFAULT_EVENT_PAGE):
        if (_is_bool(page_limit) or type(page_limit) is not int
                or not 1 <= page_limit <= MAX_EVENT_PAGE):
            raise ValueError('invalid event page limit')
        self.feed = feed
        self.page_limit = page_limit

    def read(self, cursor: t.StreamCursor | None,
             scope: t.ScopeKey) -> t.EventPage:
        _validate_scope(scope)
        stream_id = event_stream_id(scope)
        watermark = _nonnegative_int(self.feed.watermark(scope),
                                     'EVENT_FEED_INCONSISTENT')
        retained_from = _nonnegative_int(self.feed.retained_from(scope),
                                         'EVENT_FEED_INCONSISTENT')
        # An empty stream retains position 1 as the next append position.
        if watermark == 0:
            retained_from = 1
        if retained_from < 1 or retained_from > watermark + 1:
            raise ControlError(503, 'EVENT_FEED_INCONSISTENT')

        after = retained_from - 1
        if cursor is not None:
            if not isinstance(cursor, t.StreamCursor):
                raise ControlError(400, 'INVALID_CURSOR')
            if (_is_bool(cursor.sequence) or type(cursor.sequence) is not int
                    or cursor.sequence < 0):
                raise ControlError(400, 'INVALID_CURSOR')
            if cursor.scope != scope:
                # Do not consult the feed. The caller may not probe another
                # account's or environment's cursor position.
                raise ControlError(403, 'CURSOR_SCOPE_MISMATCH')
            if cursor.stream_id != stream_id:
                raise ControlError(409, 'CURSOR_STREAM_MISMATCH')
            if cursor.sequence > watermark:
                raise ControlError(409, 'CURSOR_AHEAD')
            if cursor.sequence < retained_from - 1:
                # The range cannot be replayed. Return an explicit gap and make
                # the client fetch a snapshot; do not skip ahead silently.
                return t.EventPage(events=(),
                    next_cursor=t.StreamCursor(scope=scope,
                                               sequence=watermark,
                                               stream_id=stream_id),
                    gap=True, requires_snapshot=True)
            after = cursor.sequence

        if cursor is None and retained_from > 1:
            # An initial connection cannot reconstruct discarded history. Do not
            # present retained records as a continuous stream; request a snapshot.
            return t.EventPage(events=(),
                next_cursor=t.StreamCursor(scope=scope, sequence=watermark,
                                           stream_id=stream_id),
                gap=True, requires_snapshot=True)

        records = tuple(self.feed.read_after(scope, after, self.page_limit))
        checked = self._validate_records(records, scope, after, watermark)

        if checked:
            last = checked[-1].position
            contiguous = (checked[0].position == after + 1
                          and all(record.position == after + index + 1
                                  for index, record in enumerate(checked)))
            if not contiguous:
                return t.EventPage(events=(),
                    next_cursor=t.StreamCursor(scope=scope,
                                               sequence=watermark,
                                               stream_id=stream_id),
                    gap=True, requires_snapshot=True)
            page_full = len(checked) == self.page_limit and last < watermark
            tail_gap = not page_full and last < watermark
        else:
            last = after
            page_full = False
            tail_gap = after < watermark

        events = tuple(t.ProjectionEvent(
            cursor=t.StreamCursor(scope=scope, sequence=record.position,
                                  stream_id=stream_id),
            event=record.event) for record in checked)
        next_sequence = last if checked else watermark
        return t.EventPage(events=events,
            next_cursor=t.StreamCursor(scope=scope, sequence=next_sequence,
                                       stream_id=stream_id),
            gap=tail_gap, requires_snapshot=tail_gap)

    @staticmethod
    def _validate_records(records: tuple[EventRecord, ...],
                          scope: t.ScopeKey, after: int,
                          watermark: int) -> tuple[EventRecord, ...]:
        checked = []
        previous_position = after
        revisions: dict[str, int] = {}
        for record in records:
            if not isinstance(record, EventRecord):
                raise ControlError(503, 'EVENT_FEED_INCONSISTENT')
            if (_is_bool(record.position) or type(record.position) is not int
                    or record.position <= previous_position
                    or record.position > watermark):
                # Missing/reordered records must not be merged into a cumulative
                # count. Require the adapter to return strictly increasing positions.
                raise ControlError(503, 'EVENT_FEED_INCONSISTENT')
            event = record.event
            if not isinstance(event, t.DomainEvent):
                raise ControlError(503, 'EVENT_FEED_INCONSISTENT')
            if event.scope != scope:
                raise ControlError(503, 'EVENT_SCOPE_VIOLATION')
            if (_is_bool(event.revision) or type(event.revision) is not int
                    or event.revision < 1):
                raise ControlError(503, 'EVENT_FEED_INCONSISTENT')
            try:
                utc_timestamp(event.occurred_at)
            except ValueError as exc:
                raise ControlError(503, 'EVENT_FEED_INCONSISTENT') from exc
            current = revisions.get(event.aggregate_id)
            if current is not None and event.revision <= current:
                # Aggregate sequence is authoritative for replay. Never reorder
                # or add out-of-order revisions into an incorrect cumulative view.
                raise SequenceConflict(
                    'event replay requires increasing aggregate revisions')
            revisions[event.aggregate_id] = event.revision
            previous_position = record.position
            checked.append(record)
        return tuple(checked)
