"""Server-scoped reads and fixed calculations; no network or write tool.

snapshot/history implement the internal IP-01 contracts. Their account-bearing
objects must stay server-side. Only model_data/call return provider projections.
References and candidate instruments use per-job aliases; neither raw reference
sources nor private references are sent to a provider.
"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from threading import RLock
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.wire import DecisionProposal
from trading_system.market.snapshots import snapshot_content_hash
from .providers import ModelBoundaryError, bounded_utf8, public_token


@dataclass(frozen=True, kw_only=True)
class SnapshotGrant:
    handle: str
    instrument_handle: str
    # The value expected by server-side IP-09 binding, never selected by a model.
    instrument_ref: str
    ref: t.SnapshotRef


@dataclass(frozen=True, kw_only=True)
class NewsItem:
    handle: str
    ref: t.EvidenceRef
    text: str


class ReadOnlyTools:
    def __init__(self, *, context: t.DecisionContext, as_of: str,
                 grants: tuple[SnapshotGrant, ...], load_snapshot: Callable,
                 load_history: Callable, news: tuple[NewsItem, ...] = (),
                 maximum_page_size: int = 100):
        if context.environment == t.Environment.LIVE or not context.scopes:
            raise ModelBoundaryError('LIVE_OR_EMPTY_SCOPE')
        if any(scope.environment != context.environment for scope in context.scopes):
            raise ModelBoundaryError('SCOPE_ENVIRONMENT_MISMATCH')
        if type(maximum_page_size) is not int or not 1 <= maximum_page_size <= 100:
            raise ValueError('INVALID_PAGE_LIMIT')
        self.context, self.as_of = context, as_of
        utc_timestamp(as_of)
        self._load_snapshot, self._load_history = load_snapshot, load_history
        self._maximum_page_size = maximum_page_size
        self._grants = {}
        self._news = {}
        self._cursors = {}
        self._lock = RLock()
        self._secrets = tuple(sorted({context.identity_id, context.deployment_id,
            context.authorization_id, context.decision_run_id,
            *(s.venue_account_id for s in context.scopes)}, key=len, reverse=True))
        instruments = {}
        refs = set()
        for grant in grants:
            public_token(grant.handle)
            public_token(grant.instrument_handle)
            self._check_ref(grant.ref)
            if grant.handle in self._grants or grant.ref.id in refs:
                raise ValueError('DUPLICATE_SNAPSHOT_GRANT')
            if (grant.instrument_handle in instruments
                    and instruments[grant.instrument_handle] != grant.instrument_ref):
                raise ValueError('AMBIGUOUS_INSTRUMENT_HANDLE')
            instruments[grant.instrument_handle] = grant.instrument_ref
            refs.add(grant.ref.id)
            self._grants[grant.handle] = grant
        if context.snapshot_ref not in tuple(g.ref for g in grants):
            raise ModelBoundaryError('CONTEXT_SNAPSHOT_NOT_GRANTED')
        for item in news:
            public_token(item.handle)
            self._check_ref(item.ref)
            if item.handle in self._grants or item.handle in self._news or item.ref.id in refs:
                raise ValueError('DUPLICATE_NEWS_GRANT')
            if type(item.text) is not str or len(item.text) > 16000:
                raise ModelBoundaryError('NEWS_INTEGRITY_FAILED')
            bounded_utf8(item.text)
            if item.ref.hash != canonical_hash({'text': item.text}):
                raise ModelBoundaryError('NEWS_INTEGRITY_FAILED')
            refs.add(item.ref.id)
            self._news[item.handle] = item

    @property
    def snapshot_refs(self) -> tuple[t.SnapshotRef, ...]:
        return tuple(grant.ref for grant in self._grants.values())

    def _check_ref(self, ref: t.EvidenceRef) -> None:
        if (not ref.id or not ref.source or len(ref.hash) != 64
                or any(char not in '0123456789abcdef' for char in ref.hash)
                or ref.environment != self.context.environment
                or utc_timestamp(ref.visible_at) > utc_timestamp(self.as_of)):
            raise ModelBoundaryError('REF_NOT_VISIBLE')

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, '[redacted]')
        return text

    def _packet(self, packet: t.SnapshotPacket, ref: t.SnapshotRef) -> None:
        if (packet.ref != ref or packet.scope not in self.context.scopes
                or packet.coverage.scope != packet.scope
                or snapshot_content_hash(packet) != ref.hash):
            raise ModelBoundaryError('SNAPSHOT_SCOPE_OR_INTEGRITY_FAILED')
        self._check_ref(ref)
        if any(utc_timestamp(value) > utc_timestamp(self.as_of) for value in (
                packet.published_at, packet.revision_visible_at,
                packet.quote.occurred_at, packet.quote.received_at)):
            raise ModelBoundaryError('FUTURE_SNAPSHOT')
        occurred, received, published, revision, visible = map(utc_timestamp, (
            packet.quote.occurred_at, packet.quote.received_at, packet.published_at,
            packet.revision_visible_at, ref.visible_at))
        if not (occurred <= received <= visible and occurred <= published <= revision <= visible):
            raise ModelBoundaryError('SNAPSHOT_TIME_ORDER_INVALID')
        for item in packet.private_refs + packet.economic_refs:
            self._check_ref(item)

    def snapshot(self, ref: t.SnapshotRef) -> t.SnapshotPacket:
        if ref not in self.snapshot_refs:
            raise ModelBoundaryError('SNAPSHOT_NOT_GRANTED')
        packet = self._load_snapshot(ref)
        self._packet(packet, ref)
        return packet

    def history(self, query: t.HistoryQuery) -> t.HistoryPage:
        if (query.scope not in self.context.scopes or type(query.limit) is not int
                or not 1 <= query.limit <= self._maximum_page_size
                or not utc_timestamp(query.start) <= utc_timestamp(query.end) <= utc_timestamp(self.as_of)):
            raise ModelBoundaryError('HISTORY_OUT_OF_SCOPE')
        identity = query.scope, query.start, query.end, query.limit
        backend_cursor = None
        if query.cursor is not None:
            if type(query.cursor) is not str or not query.cursor:
                raise ModelBoundaryError('HISTORY_CURSOR_SCOPE_MISMATCH')
            with self._lock:
                saved = self._cursors.get(query.cursor)
            if saved is None or saved[0] != identity:
                raise ModelBoundaryError('HISTORY_CURSOR_SCOPE_MISMATCH')
            backend_cursor = saved[1]
        page = self._load_history(replace(query, cursor=backend_cursor))
        if page.coverage.scope != query.scope or len(page.snapshots) > query.limit:
            raise ModelBoundaryError('INVALID_HISTORY_PAGE')
        for packet in page.snapshots:
            if packet.ref not in self.snapshot_refs or packet.scope != query.scope:
                raise ModelBoundaryError('HISTORY_SNAPSHOT_NOT_GRANTED')
            self._packet(packet, packet.ref)
            if not utc_timestamp(query.start) <= utc_timestamp(packet.quote.occurred_at) <= utc_timestamp(query.end):
                raise ModelBoundaryError('HISTORY_TIME_MISMATCH')
        allowed = self.snapshot_refs + tuple(item.ref for item in self._news.values())
        if any(ref not in allowed for ref in page.evidence):
            raise ModelBoundaryError('HISTORY_EVIDENCE_NOT_GRANTED')
        token = None
        if page.next_cursor is not None:
            if type(page.next_cursor) is not str or not page.next_cursor or page.coverage.pagination_complete:
                raise ModelBoundaryError('INVALID_HISTORY_CURSOR')
            with self._lock:
                if len(self._cursors) >= 1000:
                    raise ModelBoundaryError('HISTORY_CURSOR_LIMIT')
                token = uuid4().hex
                self._cursors[token] = identity, page.next_cursor
        elif page.coverage.query_succeeded and not page.coverage.pagination_complete:
            # Preserve the missing-data signal; an incomplete empty page is not a zero balance.
            pass
        return replace(page, next_cursor=token)

    @staticmethod
    def _coverage(value: t.Coverage) -> dict:
        return {'query_succeeded': value.query_succeeded, 'supported': value.supported,
                'pagination_complete': value.pagination_complete, 'has_missing_data': bool(value.missing)}

    def _view(self, packet: t.SnapshotPacket) -> dict:
        grant = next(g for g in self._grants.values() if g.ref == packet.ref)
        return {'ref': grant.handle, 'instrument_ref': grant.instrument_handle,
                'product': packet.scope.product.value, 'quote': domain_payload(packet.quote),
                'coverage': self._coverage(packet.coverage), 'trust': 'UNTRUSTED_DATA',
                'source': 'granted-snapshot', 'permission': 'READ_ONLY'}

    def model_data(self, refs: tuple[t.SnapshotRef, ...]) -> dict:
        if not refs or len(set(refs)) != len(refs):
            raise ModelBoundaryError('INVALID_VISIBLE_SNAPSHOTS')
        return {'snapshots': [self._view(self.snapshot(ref)) for ref in refs],
                'news': [{'ref': item.handle, 'text': self.redact(item.text),
                          'visible_at': item.ref.visible_at, 'trust': 'UNTRUSTED_DATA',
                          'source': 'granted-news', 'permission': 'READ_ONLY'}
                         for item in self._news.values()],
                'authority': 'READ_EVALUATE_PROPOSE_ONLY'}

    def call(self, name: str, arguments: Mapping) -> dict:
        """Explicit finite dispatch: never getattr, eval, URLs, headers or code.

        submit_proposal belongs to the completed-batch orchestrator boundary.
        This read facade cannot accept, execute, cancel or deploy anything.
        """
        if type(arguments) is not dict:
            raise ModelBoundaryError('INVALID_TOOL_ARGUMENTS')
        if name == 'read' and set(arguments) == {'kind', 'ref'}:
            handle = arguments['ref']
            if type(handle) is not str:
                raise ModelBoundaryError('INVALID_PUBLIC_HANDLE')
            if arguments['kind'] == 'snapshot' and handle in self._grants:
                return self._view(self.snapshot(self._grants[handle].ref))
            if arguments['kind'] == 'news' and handle in self._news:
                return {'ref': handle, 'text': self.redact(self._news[handle].text),
                        'trust': 'UNTRUSTED_DATA', 'source': 'granted-news', 'permission': 'READ_ONLY'}
        elif name == 'read' and set(arguments) == {'kind', 'ref', 'start', 'end', 'limit', 'cursor'}:
            handle = arguments['ref']
            if arguments['kind'] == 'history' and type(handle) is str and handle in self._grants:
                packet = self.snapshot(self._grants[handle].ref)
                page = self.history(t.HistoryQuery(scope=packet.scope, start=arguments['start'],
                    end=arguments['end'], limit=arguments['limit'], cursor=arguments['cursor']))
                return {'snapshots': [self._view(p) for p in page.snapshots],
                        'coverage': self._coverage(page.coverage), 'next_cursor': page.next_cursor,
                        'trust': 'UNTRUSTED_DATA', 'source': 'granted-history', 'permission': 'READ_ONLY'}
        elif name == 'evaluate' and set(arguments) == {'operation', 'ref'}:
            handle = arguments['ref']
            if arguments['operation'] == 'spread_bps' and type(handle) is str and handle in self._grants:
                packet = self.snapshot(self._grants[handle].ref)
                q, c = packet.quote, packet.coverage
                if (not c.query_succeeded or not c.supported or not c.pagination_complete or c.missing
                        or q.bid is None or q.ask is None):
                    raise ModelBoundaryError('EVALUATION_DATA_MISSING')
                if q.bid.currency != q.ask.currency or q.bid.value <= 0 or q.ask.value < q.bid.value:
                    raise ModelBoundaryError('EVALUATION_PRICE_INVALID')
                with localcontext() as ctx:
                    ctx.prec = 34
                    spread = (q.ask.value - q.bid.value) / q.bid.value * Decimal(10000)
                return {'value': format(spread, 'f'), 'unit': 'BPS', 'ref': handle,
                        'trust': 'UNTRUSTED_DATA', 'source': 'fixed-calculation', 'permission': 'READ_ONLY'}
        raise ModelBoundaryError('TOOL_NOT_ALLOWED')

    def bind_proposal(self, proposal: DecisionProposal,
                      visible_refs: tuple[t.SnapshotRef, ...]) -> DecisionProposal:
        """Resolve only aliases delivered to this job; this is not authorization."""
        grants = [g for g in self._grants.values() if g.ref in visible_refs]
        matches = {g.instrument_ref for g in grants if g.instrument_handle == proposal.instrument_ref}
        refs = {g.handle: g.ref.id for g in grants}
        refs.update({item.handle: item.ref.id for item in self._news.values()})
        if len(matches) != 1 or any(ref not in refs for ref in proposal.evidence_refs):
            raise ModelBoundaryError('PROPOSAL_REFERENCE_NOT_GRANTED')
        return replace(proposal, instrument_ref=matches.pop(),
                       evidence_refs=tuple(refs[ref] for ref in proposal.evidence_refs))
