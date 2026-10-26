"""Append-only domain events and transactional outbox; no venue delivery."""
from datetime import datetime, timezone
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from .models import encode, scope_id, account_key, SequenceConflict, ScopeMismatch, atomic_write


class EventStore:
    def __init__(self, received_at=None):
        # The ingress may supply the actual receipt time, distinct from processing.
        self.received_at = received_at

    @atomic_write
    def append(self, tx, event: t.DomainEvent, expected_sequence: int) -> t.EventRef:
        tx.require_scope(event.scope)
        payload = event.payload
        if isinstance(payload, (t.Fill, t.RiskLock, t.ManagedPosition)):
            tx.require_scope(payload.scope)
        elif isinstance(payload, t.OrderProjection):
            tx.require_scope(payload.order.identity.scope)
        elif isinstance(payload, t.ReservationProjection):
            reservation = payload.reservation
            if (reservation.environment != tx.scope.environment
                    or reservation.venue_account_id != tx.scope.venue_account_id
                    or reservation.collateral_group_id != tx.group_id):
                raise ScopeMismatch('event reservation binding mismatch')
            if not any(item.reservation_id == reservation.reservation_id
                       for item in tx.reservations.for_intent(reservation.intent_id)):
                raise ScopeMismatch('event reservation is not in transaction scope')
        else:
            raise TypeError('unsupported domain event payload')
        if any(ref.environment != tx.scope.environment for ref in event.evidence):
            raise ScopeMismatch('event evidence environment mismatch')
        if event.revision != expected_sequence + 1:
            tx.failed = True
            raise SequenceConflict('event revision must equal next aggregate sequence')
        utc_timestamp(event.occurred_at)
        processed = datetime.now(timezone.utc).isoformat()
        received = self.received_at or processed
        if utc_timestamp(received) > utc_timestamp(processed):
            tx.failed = True
            raise ValueError('receipt is later than processing')
        digest = canonical_hash(domain_payload(event))
        sequence = tx.advance('events:' + event.aggregate_id, expected_sequence)
        tx.execute('INSERT INTO ledger.domain_events '
            '(event_id,scope_id,aggregate_id,sequence,payload,payload_hash,occurred_at,received_at,processed_at,transaction_id) '
            'VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)',
            (event.event_id, scope_id(event.scope), event.aggregate_id, sequence,
             encode(event), digest, event.occurred_at, received, processed, tx.transaction_id))
        if isinstance(event.payload, t.Fill):
            fill = event.payload
            if utc_timestamp(fill.occurred_at) != utc_timestamp(event.occurred_at):
                raise ValueError('fill and event occurrence times differ')
            tx.require_scope(fill.scope)
            tx.require_scope(fill.order.scope)
            if fill.evidence.environment != event.scope.environment:
                tx.failed = True
                raise ValueError('fill evidence environment mismatch')
            if not fill.native_trade_id:
                tx.failed = True
                raise ValueError('fill without native identity needs reconciliation coverage')
            tx.execute('INSERT INTO ledger.fills '
                '(scope_id,record_id,sequence,payload,transaction_id,venue,environment,account_id,product,'
                'instrument_uid,native_trade_id,correction_of,quantity,unit,price,currency,event_id) '
                'VALUES (%s,%s,1,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (scope_id(fill.scope), fill.fill_id, encode(fill), tx.transaction_id,
                 fill.venue, *account_key(fill.scope), fill.scope.instrument_uid,
                 fill.native_trade_id, fill.correction_of, fill.quantity.value,
                 fill.quantity.unit, fill.price.value, fill.price.currency, event.event_id))
            for index, fee in enumerate(fill.fees):
                tx.execute('INSERT INTO ledger.fill_fees(fill_id,fee_index,amount,currency) VALUES (%s,%s,%s,%s)',
                           (fill.fill_id, index, fee.amount, fee.currency))
        tx.execute('INSERT INTO ledger.outbox '
            '(message_id,scope_id,aggregate_id,sequence,kind,event_id,transaction_id) '
            'VALUES (%s,%s,%s,%s,\'DOMAIN_EVENT\',%s,%s)',
            (str(uuid4()), scope_id(event.scope), event.aggregate_id, sequence,
             event.event_id, tx.transaction_id))
        return t.EventRef(id=event.event_id, hash=digest, environment=event.scope.environment,
                          source='postgres-ledger', visible_at=processed)
