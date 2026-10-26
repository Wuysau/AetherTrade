"""Offline Stocks semantics behind IP-11. No transport or native qualification.

The public documentation is a request-shape reference, not proof of an account,
instrument, executable native binding, balances, fills or permission to trade.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.wire import ABSENT, ExecutionCommand
from .bridge import BridgeDenied, NautilusEngine, OfflineBridge
from .id_codec import VenueIdCodec
from .stocks_models import StocksOrderPreview, StocksRequestObservation


class StocksEngineAdapter(NautilusEngine):
    def __init__(self, compatibility: t.CompatibilityMap, *, offline: OfflineBridge | None = None):
        if offline is not None and offline.scope.product != t.Product.US_EQUITY:
            raise BridgeDenied('REAL_EQUITY_SCOPE_REQUIRED')
        super().__init__(compatibility, offline=offline)

    @staticmethod
    def preview(command: ExecutionCommand, instrument: t.InstrumentSpec, *,
                trading_session: str | None = None) -> StocksOrderPreview:
        """Pure inspection; session is NOT added to the signed canonical payload.

        The missing canonical session/wallet binding needs a reviewed contract
        extension before a native LIMIT mapping can be installed.
        """
        if (command.product != t.Product.US_EQUITY or instrument.product != t.Product.US_EQUITY
                or instrument.venue != 'BINANCE' or command.instrument_uid != instrument.instrument_uid):
            raise BridgeDenied('REAL_EQUITY_INSTRUMENT_REQUIRED')
        if command.operation not in ('SUBMIT', 'CLOSE'):
            raise BridgeDenied('UNSUPPORTED_STOCKS_OPERATION')
        p = command.canonical_payload
        if p.tokenize is not False:
            raise BridgeDenied('EXPLICIT_TOKENIZE_FALSE_REQUIRED')
        if (p.trigger_price is not ABSENT or p.position_side not in (ABSENT, 'BOTH')
                or p.reduce_only is not ABSENT or p.full_position_exit is not ABSENT):
            raise BridgeDenied('UNMAPPED_EQUITY_FIELDS')
        if command.operation == 'CLOSE' and p.side != 'SELL':
            raise BridgeDenied('CLOSE_REQUIRES_SELL')
        def optional(value):
            return None if value is ABSENT else value
        return StocksOrderPreview(symbol=instrument.native_symbol, side=p.side,
            order_type=p.order_type, quantity=optional(p.quantity), notional=optional(p.quote_notional),
            price=optional(p.price), time_in_force='DAY' if p.time_in_force is ABSENT else p.time_in_force,
            trading_session=trading_session, tokenize=p.tokenize)

    def _write(self, command, permit, operations):
        if not isinstance(command, ExecutionCommand) or command.product != t.Product.US_EQUITY:
            raise BridgeDenied('REAL_EQUITY_COMMAND_REQUIRED')
        if command.operation in ('AMEND', 'SET_PROTECTION'):
            raise BridgeDenied('STOCKS_OPERATION_NOT_QUALIFIED')
        if command.operation in ('SUBMIT', 'CLOSE'):
            p = command.canonical_payload
            if p.tokenize is not False:
                raise BridgeDenied('EXPLICIT_TOKENIZE_FALSE_REQUIRED')
            if p.order_type not in ('MARKET', 'LIMIT'):
                raise BridgeDenied('NATIVE_STOP_UNVERIFIED')
            if p.order_type == 'LIMIT':
                raise BridgeDenied('LIMIT_SESSION_NOT_IN_CANONICAL_CONTRACT')
            if (p.trigger_price is not ABSENT or p.position_side not in (ABSENT, 'BOTH')
                    or p.reduce_only is not ABSENT or p.full_position_exit is not ABSENT):
                raise BridgeDenied('UNMAPPED_EQUITY_FIELDS')
            if command.operation == 'CLOSE' and p.side != 'SELL':
                raise BridgeDenied('CLOSE_REQUIRES_SELL')
            def optional(value):
                return None if value is ABSENT else value
            StocksOrderPreview(symbol=command.instrument_uid, side=p.side, order_type=p.order_type,
                quantity=optional(p.quantity), notional=optional(p.quote_notional),
                price=optional(p.price), trading_session=None,
                time_in_force='DAY' if p.time_in_force is ABSENT else p.time_in_force)
        return super()._write(command, permit, operations)

    def query(self, query: t.ReconciliationCase) -> t.VenueReport:
        if query.scope.product != t.Product.US_EQUITY:
            raise BridgeDenied('REAL_EQUITY_SCOPE_REQUIRED')
        report = super().query(query)
        return replace(report, coverage=replace(report.coverage, missing=(
            'STOCKS_NATIVE_QUERY_NOT_QUALIFIED', 'BALANCES', 'SHARES', 'ORDER_HISTORY', 'TRADE_HISTORY')))


class StocksCapabilityProbe:
    def verify(self, input: t.StocksProbeInput) -> t.CapabilityProfile:
        """Inspect inputs without promoting supplied assertions to qualification."""
        e, instrument = input.evidence, input.instrument
        reasons = ['NATIVE_STOCKS_BOUNDARY_NOT_QUALIFIED', 'NATIVE_STOP_UNVERIFIED',
                   'ACCOUNT_BALANCES_SHARES_HISTORY_NOT_VERIFIED']
        if (e.scope.product != t.Product.US_EQUITY or instrument.product != t.Product.US_EQUITY
                or instrument.instrument_uid != e.scope.instrument_uid or instrument.venue != 'BINANCE'):
            reasons.append('REAL_EQUITY_SCOPE_MISMATCH')
        c = e.coverage
        if (c.scope != e.scope or not c.supported or not c.query_succeeded
                or not c.pagination_complete or c.missing or not e.evidence):
            reasons.append('ACCOUNT_READS_MISSING_OR_INCOMPLETE')
        if any(ref.environment != e.scope.environment for ref in e.evidence):
            reasons.append('EVIDENCE_ENVIRONMENT_MISMATCH')
        # A non-valid interval intentionally grants no cached capability lifetime.
        timestamp = '1970-01-01T00:00:00Z'
        return t.CapabilityProfile(scope=e.scope, build_digest=e.build_digest,
            status=t.CapabilityStatus.UNVERIFIED, capabilities=(), limitations=tuple(reasons),
            evidence=e.evidence, valid_from=timestamp, valid_until=timestamp)


class StocksEvidenceNormalizer:
    """Hash-bound request ACK envelopes only; never produces a fill/domain delta.

    Reader resolves an already archived reference (no path/network access here).
    Lifecycle decoders remain unqualified. received_at is the observation time,
    not an invented venue execution time. Sources distinguish place/cancel ACKs.
    """
    def __init__(self, codec: VenueIdCodec, reader: Callable[[t.EvidenceRef], bytes]):
        if codec.scope.product != t.Product.US_EQUITY:
            raise ValueError('REAL_EQUITY_SCOPE_REQUIRED')
        self.codec, self.reader = codec, reader

    def _decode(self, raw):
        if (raw.scope != self.codec.scope or raw.source not in ('STOCKS_PLACE_ACK', 'STOCKS_CANCEL_ACK')
                or raw.raw_payload_ref.source != raw.source
                or raw.raw_payload_ref.environment != raw.scope.environment
                or raw.payload_hash != raw.raw_payload_ref.hash):
            raise ValueError('ACK_SCOPE_OR_PROVENANCE_MISMATCH')
        utc_timestamp(raw.received_at)
        if utc_timestamp(raw.raw_payload_ref.visible_at) > utc_timestamp(raw.received_at):
            raise ValueError('RAW_EVIDENCE_NOT_VISIBLE')
        payload = self.reader(raw.raw_payload_ref)
        if not isinstance(payload, bytes) or sha256(payload).hexdigest() != raw.payload_hash:
            raise ValueError('RAW_HASH_MISMATCH')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('DUPLICATE_ACK_FIELD')
                result[key] = value
            return result
        data = json.loads(payload, object_pairs_hook=unique)
        if not isinstance(data, dict) or data.get('status') not in ('S', 'F'):
            raise ValueError('NOT_A_REQUEST_ACK')
        if set(data) - {'status', 'orderId', 'clientOrderId'}:
            raise ValueError('UNREVIEWED_ACK_SHAPE')
        for field in ('orderId', 'clientOrderId'):
            if field in data and (not isinstance(data[field], str) or not data[field].strip()):
                raise ValueError('INVALID_ACK_ID')
        if raw.source == 'STOCKS_PLACE_ACK':
            identity = self.codec.decode(data.get('clientOrderId'))
            order_id = data.get('orderId')
            if not isinstance(order_id, str) or not order_id.strip():
                raise ValueError('ORDER_ID_REQUIRED')
            if identity.venue_order_id is not None and identity.venue_order_id != order_id:
                raise ValueError('ORDER_ID_MISMATCH')
        else:
            identity = self.codec.decode_order(data.get('orderId'))
            if 'clientOrderId' in data and self.codec.decode(data['clientOrderId']) != identity:
                raise ValueError('ORDER_ID_MISMATCH')
        return data, identity

    def _event(self, raw, identity):
        key = canonical_hash({'scope': domain_payload(raw.scope), 'source': raw.source,
                              'hash': raw.payload_hash})
        return t.VenueEvent(event_id='stocks:ack:' + key, source=raw.source,
            source_event_id=None, source_sequence=None, account_id=raw.scope.venue_account_id,
            instrument_uid=raw.scope.instrument_uid, order_refs=(identity,), occurred_at=raw.received_at,
            received_at=raw.received_at, processed_at=raw.received_at,
            payload_hash=raw.payload_hash, raw_payload_ref=raw.raw_payload_ref)

    def normalize(self, raw: t.RawVenueMessage) -> t.VenueEvent:
        _, identity = self._decode(raw)
        return self._event(raw, identity)

    def observe_ack(self, raw: t.RawVenueMessage, *, actual_shares: str) -> StocksRequestObservation:
        data, identity = self._decode(raw)
        return StocksRequestObservation(event=self._event(raw, identity),
            acknowledgement=data['status'], actual_shares=actual_shares)
