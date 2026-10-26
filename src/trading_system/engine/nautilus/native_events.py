"""Archive raw input before translating an explicitly injected event decoder.

No upstream event shape is invented here. IP-02 must supply a reviewed exact
build decoder. Observations do not establish fills, ownership or final outcomes;
deduplication, projections and reconciliation belong to IP-12/13.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from typing import Callable, Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from .id_codec import UnknownIdentity, VenueIdCodec


@dataclass(frozen=True, kw_only=True)
class DecodedNativeEvent:
    """Application adapter output, not a Nautilus class/signature."""
    source_event_id: str | None
    source_sequence: str | None
    occurred_at: str
    native_client_order_id: str | None
    venue_client_order_id: str | None
    venue_order_id: str | None


@dataclass(frozen=True, kw_only=True)
class EventObservation:
    raw: t.RawVenueMessage
    event: t.VenueEvent | None
    status: Literal['OBSERVED', 'UNKNOWN', 'UNATTRIBUTED']
    reason: str | None


class NativeEventBridge:
    def __init__(self, codec: VenueIdCodec, archive: Path, *,
                 decoder: Callable[[bytes], DecodedNativeEvent] | None = None):
        self.codec, self.archive, self.decoder = codec, Path(archive), decoder

    def ingest(self, payload: bytes, *, source: str, received_at: str,
               processed_at: str) -> EventObservation:
        if not isinstance(payload, bytes) or not isinstance(source, str) or not source.strip():
            raise ValueError('raw bytes and explicit source required')
        # Store first, including unparseable events and invalid event timestamps.
        digest = sha256(payload).hexdigest()
        self.archive.mkdir(parents=True, exist_ok=True)
        target = self.archive / (digest + '.raw')
        try:
            with target.open('xb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            # An earlier fsync may have failed after writing these bytes. Reuse
            # requires another successful flush, not just matching file content.
            with target.open('r+b') as stream:
                if stream.read() != payload:
                    raise ValueError('raw archive hash/content mismatch')
                os.fsync(stream.fileno())
        # Archive failures propagate: never return a successful evidence receipt.
        ref = t.EvidenceRef(id=str(target.resolve()), hash=digest,
            environment=self.codec.scope.environment, source=source, visible_at=received_at)
        raw = t.RawVenueMessage(scope=self.codec.scope, source=source, payload_hash=digest,
                                raw_payload_ref=ref, received_at=received_at)
        if self.decoder is None:
            return EventObservation(raw=raw, event=None, status='UNKNOWN',
                                    reason='NATIVE_DECODER_NOT_QUALIFIED')
        try:
            if utc_timestamp(processed_at) < utc_timestamp(received_at):
                raise ValueError('processing precedes receipt')
            decoded = self.decoder(payload)
            if not isinstance(decoded, DecodedNativeEvent):
                raise ValueError('typed decoded event required')
            utc_timestamp(decoded.occurred_at)
            for field in (decoded.source_event_id, decoded.source_sequence,
                          decoded.native_client_order_id, decoded.venue_client_order_id,
                          decoded.venue_order_id):
                if field is not None and (not isinstance(field, str) or not field.strip()):
                    raise ValueError('invalid event identifier')
            found, unknown = [], 0
            for value, lookup in (
                    (decoded.native_client_order_id, self.codec.decode_native),
                    (decoded.venue_client_order_id, self.codec.decode),
                    (decoded.venue_order_id, self.codec.decode_order)):
                if value is not None:
                    try:
                        found.append(lookup(value))
                    except UnknownIdentity:
                        unknown += 1
            if not found:
                return EventObservation(raw=raw, event=None, status='UNATTRIBUTED',
                                        reason='NO_REGISTERED_ORDER_IDENTITY')
            if unknown or any(identity != found[0] for identity in found):
                raise ValueError('contradictory or unbound event identities')
            event_key = canonical_hash({'scope': domain_payload(self.codec.scope), 'source': source,
                'source_event_id': decoded.source_event_id, 'payload_hash': digest})
            event = t.VenueEvent(event_id='ip11:' + event_key, source=source,
                source_event_id=decoded.source_event_id, source_sequence=decoded.source_sequence,
                account_id=self.codec.scope.venue_account_id,
                instrument_uid=self.codec.scope.instrument_uid, order_refs=(found[0],),
                occurred_at=decoded.occurred_at, received_at=received_at, processed_at=processed_at,
                payload_hash=digest, raw_payload_ref=ref)
            return EventObservation(raw=raw, event=event, status='OBSERVED', reason=None)
        except Exception:
            # Do not leak decoder exception text or treat bad responses as NOT_SENT.
            return EventObservation(raw=raw, event=None, status='UNKNOWN',
                                    reason='DECODE_OR_IDENTITY_UNRESOLVED')
