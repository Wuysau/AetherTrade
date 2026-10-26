"""Offline evidence intake. Accepted means archived metadata, never order success.

The archive reader is injected and runs before acquiring a ledger transaction.
It must read an already durable, access-controlled archive (e.g. IP-11), not a
venue endpoint. A decoder is an adapter dependency, not a trusted source of
authority. This module does not change commands, reservations or risk gates.
"""
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.storage.models import decode, scope_id, ScopeMismatch, MissingEvidence


@dataclass(frozen=True, kw_only=True)
class EvidenceRecord:
    raw: t.RawVenueMessage
    event: t.VenueEvent | None
    aggregate_sequence: int
    status: str
    reason: str | None


def next_account_sequence(tx) -> int:
    """Account/product order across instruments; collateral mutex serializes it."""
    key = canonical_hash({'environment': str(tx.scope.environment),
        'account': tx.scope.venue_account_id, 'product': str(tx.scope.product)})
    tx.execute('INSERT INTO ledger.aggregate_heads(scope_id,aggregate_id,sequence) '
               "VALUES (%s,'ip12:account-evidence',0) ON CONFLICT DO NOTHING", (key,))
    return tx.execute('UPDATE ledger.aggregate_heads SET sequence=sequence+1 '
        "WHERE scope_id=%s AND aggregate_id='ip12:account-evidence' RETURNING sequence",
        (key,)).fetchone()[0]


class EvidenceIngestor:
    def __init__(self, uow, scope: t.ScopeKey, *, read_raw: Callable[[t.EvidenceRef], bytes]):
        if scope.environment == t.Environment.LIVE:
            raise ValueError('LIVE is not enabled in this stage')
        self.uow, self.scope, self.read_raw = uow, scope, read_raw

    def require_evidence(self, event: t.DomainEvent) -> None:
        """Bind a normalized order observation to previously ingested metadata."""
        if event.scope != self.scope or not isinstance(event.payload, t.OrderProjection):
            raise ScopeMismatch('order evidence scope mismatch')
        with self.uow.transaction(self.scope) as tx:
            for ref in event.evidence:
                rows = tx.execute('SELECT payload FROM ledger.venue_events WHERE scope_id=%s '
                    "AND payload->>'status'='OBSERVED' AND payload->'raw'->>'payload_hash'=%s",
                    (scope_id(self.scope), ref.hash)).fetchall()
                for row in rows:
                    record = decode(EvidenceRecord, row[0])
                    if (record.event is not None and record.raw.raw_payload_ref == ref
                            and event.payload.order.identity in record.event.order_refs):
                        return
        raise MissingEvidence('order projection requires ingested evidence for this identity')

    def _check_raw(self, raw):
        if raw.scope != self.scope or raw.raw_payload_ref.environment != self.scope.environment:
            raise ScopeMismatch('raw evidence scope mismatch')
        if not raw.source.strip() or raw.raw_payload_ref.source != raw.source:
            raise ValueError('raw source mismatch')
        utc_timestamp(raw.received_at)
        utc_timestamp(raw.raw_payload_ref.visible_at)
        payload = self.read_raw(raw.raw_payload_ref)
        if not isinstance(payload, bytes):
            raise ValueError('archive must supply exact bytes')
        digest = sha256(payload).hexdigest()
        if digest != raw.payload_hash or digest != raw.raw_payload_ref.hash:
            raise ValueError('raw archive hash mismatch')
        return payload

    def _check_event(self, event, raw):
        if not isinstance(event, t.VenueEvent) or not event.event_id.strip():
            raise ValueError('typed event identity required')
        if (event.account_id != self.scope.venue_account_id
                or event.instrument_uid != self.scope.instrument_uid
                or not event.order_refs
                or any(order.scope != self.scope for order in event.order_refs)):
            raise ScopeMismatch('event identity scope mismatch')
        if (event.source != raw.source or event.raw_payload_ref != raw.raw_payload_ref
                or event.payload_hash != raw.payload_hash or event.received_at != raw.received_at):
            raise ValueError('event is not bound to archived input')
        for value in (event.source_event_id, event.source_sequence):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError('invalid source identity')
        utc_timestamp(event.occurred_at)
        if utc_timestamp(event.processed_at) < utc_timestamp(event.received_at):
            raise ValueError('processing precedes receipt')

    def _persist(self, raw, event, reason):
        # Delivery duplicates may have different local timestamps. The raw
        # hash identifies the archived observation; source IDs are not assumed
        # globally unique and source_sequence is never a local ordering clock.
        key = 'ip12:raw:' + canonical_hash({'scope': domain_payload(self.scope),
            'source': raw.source, 'hash': raw.payload_hash,
            'event_id': None if event is None else event.event_id})
        with self.uow.transaction(self.scope) as tx:
            existing = tx._latest('venue_events', key, EvidenceRecord)
            if existing is None:
                record = EvidenceRecord(raw=raw, event=event,
                    aggregate_sequence=next_account_sequence(tx),
                    status='UNKNOWN' if event is None else 'OBSERVED', reason=reason)
                tx._append('venue_events', key, record, 0)
            else:
                # Preserve the original receipt, without overwriting history.
                if existing.event is not None and event is not None:
                    a, b = domain_payload(existing.event), domain_payload(event)
                    for item in ('received_at', 'processed_at', 'raw_payload_ref'):
                        a.pop(item)
                        b.pop(item)
                    if a != b:
                        raise ValueError('same archived identity has conflicting decoded facts')
                raw, event, reason = existing.raw, existing.event, existing.reason
        return t.EvidenceReceipt(event_id=key if event is None else event.event_id,
            accepted=event is not None, reason=reason, raw_ref=raw.raw_payload_ref)

    def ingest(self, event: t.VenueEvent) -> t.EvidenceReceipt:
        raw = t.RawVenueMessage(scope=self.scope, source=event.source,
            payload_hash=event.payload_hash, raw_payload_ref=event.raw_payload_ref,
            received_at=event.received_at)
        self._check_raw(raw)
        try:
            self._check_event(event, raw)
        except (ValueError, TypeError, AttributeError):
            return self._persist(raw, None, 'UNKNOWN:EVENT_BINDING_INVALID')
        return self._persist(raw, event, None)

    def ingest_raw(self, raw: t.RawVenueMessage, *,
                   decoder: Callable[[bytes], t.VenueEvent]) -> t.EvidenceReceipt:
        """Persist failed parsing as UNKNOWN; never invent an occurrence time."""
        payload = self._check_raw(raw)
        try:
            event = decoder(payload)
            self._check_event(event, raw)
        except Exception:
            # Decoder messages may contain private payloads. Keep a stable reason
            # and the original content-addressed reference, not exception text.
            return self._persist(raw, None, 'UNKNOWN:PARSE_OR_IDENTITY_ERROR')
        return self._persist(raw, event, None)
