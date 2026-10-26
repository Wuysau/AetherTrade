"""Transactional, append-only fill accounting with durable protection notices.

No venue I/O, inferred fills, position ownership assignment or protection orders.
The caller must supply the adapter's reviewed native trade-ID scope. Native ID
scope is not inferred from a symbol or a credential alias.
"""
from dataclasses import dataclass, replace
from typing import Literal
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.storage.events import EventStore
from trading_system.storage.models import (decode, scope_id, account_key,
    MissingEvidence, ScopeMismatch)
from .evidence import EvidenceRecord, next_account_sequence


@dataclass(frozen=True, kw_only=True)
class FillNotice:
    fill: t.Fill
    aggregate_sequence: int
    status: str
    protection_recheck_required: bool
    event_ref: t.EventRef | None
    # Empty fees mean unknown, not a zero-fee assertion. No history coverage is
    # present in Fill; even a valid fill cannot claim complete account history.
    fees_known: bool
    history_complete: bool = False


class FillLedger:
    def __init__(self, uow, scope: t.ScopeKey, *,
                 trade_id_scope: Literal['ACCOUNT_PRODUCT', 'INSTRUMENT']):
        if scope.environment == t.Environment.LIVE:
            raise ValueError('LIVE is not enabled in this stage')
        if trade_id_scope not in ('ACCOUNT_PRODUCT', 'INSTRUMENT'):
            raise ValueError('unsupported/unverified native trade ID scope')
        self.uow, self.scope, self.trade_id_scope = uow, scope, trade_id_scope

    def _validate(self, fill):
        if fill.scope != self.scope or fill.order.scope != self.scope:
            raise ScopeMismatch('fill scope mismatch')
        if fill.evidence.environment != self.scope.environment:
            raise ScopeMismatch('fill evidence environment mismatch')
        if not fill.fill_id.strip() or not fill.venue.strip():
            raise ValueError('fill and venue identities required')
        if fill.native_trade_id is not None and not fill.native_trade_id.strip():
            raise ValueError('empty native identity')
        if fill.correction_of is not None and (not fill.correction_of.strip()
                                              or fill.correction_of == fill.fill_id):
            raise ValueError('invalid correction target')
        if fill.quantity.value == 0 or (fill.correction_of is None and fill.quantity.value < 0):
            raise ValueError('quantity requires positive fill or nonzero explicit correction')
        utc_timestamp(fill.occurred_at)
        utc_timestamp(fill.evidence.visible_at)

    def _evidence(self, tx, fill):
        rows = tx.execute('SELECT payload FROM ledger.venue_events WHERE scope_id=%s '
            "AND payload->>'status'='OBSERVED' AND payload->'raw'->>'payload_hash'=%s",
            (scope_id(self.scope), fill.evidence.hash)).fetchall()
        for row in rows:
            record = decode(EvidenceRecord, row[0])
            if (record.event is not None and record.raw.raw_payload_ref == fill.evidence
                    and fill.order in record.event.order_refs):
                return record
        raise MissingEvidence('fill requires ingested raw evidence bound to this order')

    def _duplicate(self, tx, fill):
        params = (fill.venue, *account_key(fill.scope), fill.native_trade_id)
        sql = ('SELECT payload FROM ledger.fills WHERE venue=%s AND environment=%s '
               'AND account_id=%s AND product=%s AND native_trade_id=%s')
        if self.trade_id_scope == 'INSTRUMENT':
            sql += ' AND instrument_uid=%s'
            params += (fill.scope.instrument_uid,)
        rows = tx.execute(sql, params).fetchall()
        if len(rows) > 1:
            raise ValueError('stored trade identities conflict with adapter scope')
        if not rows:
            return None
        existing = decode(t.Fill, rows[0][0])
        a, b = domain_payload(existing), domain_payload(fill)
        for field in ('fill_id', 'evidence'):
            a.pop(field)
            b.pop(field)
        if a != b:
            raise ValueError('native trade identity conflict; explicit correction required')
        return existing

    def record(self, fill: t.Fill, correction_of: str | None = None) -> t.FillReceipt:
        if correction_of is not None:
            if fill.correction_of not in (None, correction_of):
                raise ValueError('conflicting correction targets')
            fill = replace(fill, correction_of=correction_of)
        self._validate(fill)
        with self.uow.transaction(self.scope) as tx:
            evidence = self._evidence(tx, fill)
            if fill.native_trade_id is None:
                # Fingerprint is only a reconciliation candidate; it is never
                # used to collapse two possibly distinct economic executions.
                key = 'ip12:unidentified:' + canonical_hash(domain_payload(fill))
                if tx._latest('audit_events', key, FillNotice) is None:
                    tx._append('audit_events', key, FillNotice(fill=fill,
                        aggregate_sequence=next_account_sequence(tx),
                        status='UNKNOWN:NATIVE_ID_AND_COVERAGE_REQUIRED',
                        protection_recheck_required=True, event_ref=None,
                        fees_known=bool(fill.fees)), 0)
                receipt = t.FillReceipt(fill_id=fill.fill_id, recorded=False,
                    duplicate_of=None, event_ref=None)
            else:
                duplicate = self._duplicate(tx, fill)
                if duplicate is not None:
                    return t.FillReceipt(fill_id=fill.fill_id, recorded=False,
                        duplicate_of=duplicate.fill_id, event_ref=None)
                if tx.execute('SELECT 1 FROM ledger.fills WHERE record_id=%s',
                              (fill.fill_id,)).fetchone() is not None:
                    raise ValueError('fill_id already belongs to another native identity')
                if fill.correction_of is not None:
                    row = tx.execute('SELECT payload FROM ledger.fills WHERE record_id=%s',
                                     (fill.correction_of,)).fetchone()
                    if row is None:
                        raise MissingEvidence('correction target is missing')
                    original = decode(t.Fill, row[0])
                    if original.correction_of is not None:
                        raise ValueError('corrections must reference the original execution')
                    if (original.order != fill.order or original.scope != fill.scope
                            or original.venue != fill.venue
                            or original.quantity.unit != fill.quantity.unit
                            or original.price.currency != fill.price.currency):
                        raise ScopeMismatch('correction target identity/units mismatch')
                    adjusted = tx.execute('SELECT COALESCE(sum(quantity),0) FROM ledger.fills '
                        'WHERE record_id=%s OR correction_of=%s',
                        (fill.correction_of, fill.correction_of)).fetchone()[0]
                    from .projections import add_exact
                    if add_exact(adjusted, fill.quantity.value) < 0:
                        raise ValueError('correction exceeds original execution quantity')
                # Do not let a conflicting unit/settlement convention corrupt
                # totals, even if this execution has a new native ID.
                rows = tx.execute('SELECT payload FROM ledger.fills WHERE scope_id=%s '
                    "AND payload->'order'->>'logical_client_order_id'=%s",
                    (scope_id(self.scope), fill.order.logical_client_order_id)).fetchall()
                for row in rows:
                    prior = decode(t.Fill, row[0])
                    if (prior.order != fill.order or prior.quantity.unit != fill.quantity.unit
                            or prior.price.currency != fill.price.currency or prior.venue != fill.venue):
                        raise ScopeMismatch('fill identity or units changed within order')
                total = tx.execute('SELECT COALESCE(sum(quantity),0) FROM ledger.fills '
                    "WHERE scope_id=%s AND payload->'order'->>'logical_client_order_id'=%s",
                    (scope_id(self.scope), fill.order.logical_client_order_id)).fetchone()[0]
                from .projections import add_exact
                if add_exact(total, fill.quantity.value) < 0:
                    raise ValueError('correction exceeds accounted quantity')
                aggregate = 'order:' + fill.order.logical_client_order_id
                head = tx.execute('SELECT sequence FROM ledger.aggregate_heads '
                    'WHERE scope_id=%s AND aggregate_id=%s',
                    (scope_id(self.scope), 'events:' + aggregate)).fetchone()
                sequence = 0 if head is None else head[0]
                event = t.DomainEvent(event_id='ip12:fill:' + uuid4().hex,
                    scope=self.scope, aggregate_id=aggregate, revision=sequence + 1,
                    occurred_at=fill.occurred_at, evidence=(fill.evidence,), payload=fill)
                ref = EventStore(received_at=evidence.raw.received_at).append(tx, event, sequence)
                tx._append('audit_events', 'ip12:fill-notice:' + fill.fill_id,
                    FillNotice(fill=fill, aggregate_sequence=next_account_sequence(tx),
                        status='FILL_RECORDED', protection_recheck_required=True,
                        event_ref=ref, fees_known=bool(fill.fees)), 0)
                # EventStore's DOMAIN_EVENT outbox entry is the durable wake-up
                # for protection/reconciliation. Consumer acknowledgement is a
                # separate task; a notice is not evidence of protected exposure.
                receipt = t.FillReceipt(fill_id=fill.fill_id, recorded=True,
                    duplicate_of=None, event_ref=ref)
        # Return success only after UnitOfWork commits all three records/outbox.
        return receipt

    def quantity_at(self, event: t.DomainEvent) -> t.Quantity:
        """Known executions through this event, not a complete account position."""
        if event.scope != self.scope or not isinstance(event.payload, t.Fill):
            raise ScopeMismatch('fill history scope mismatch')
        with self.uow.transaction(self.scope) as tx:
            row = tx.execute('SELECT payload_hash FROM ledger.domain_events WHERE event_id=%s',
                             (event.event_id,)).fetchone()
            if row is None or row[0] != canonical_hash(domain_payload(event)):
                raise MissingEvidence('projection requires the exact committed fill event')
            quantity = tx.execute('SELECT COALESCE(sum(f.quantity),0) FROM ledger.fills f '
                'JOIN ledger.domain_events e ON e.event_id=f.event_id '
                'WHERE e.scope_id=%s AND e.aggregate_id=%s AND e.sequence<=%s',
                (scope_id(self.scope), event.aggregate_id, event.revision)).fetchone()[0]
        return t.Quantity(value=quantity, unit=event.payload.quantity.unit)
