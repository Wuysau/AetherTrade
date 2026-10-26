"""Order fact reconciliation, independent of command outcomes and send gates.

Quantities merge known facts, not complete account history. Without a coverage
watermark in OrderProjection, a cumulative report and partial fill history
cannot establish complete exposure. Consumers must retain reconciliation gaps;
this projection cannot release reservations or establish a ready risk book.
Adapters must normalize venue lifecycle revisions onto a comparable ordering
before submitting snapshots; source sequences from different streams are not
interchangeable. Order snapshots cannot resolve operation-specific UNKNOWN.
"""
from dataclasses import replace
from decimal import Decimal, localcontext
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.storage.models import MissingEvidence, ScopeMismatch, SequenceConflict


TERMINAL = frozenset((t.OrderLifecycle.CANCELED, t.OrderLifecycle.EXPIRED,
                      t.OrderLifecycle.REJECTED, t.OrderLifecycle.FILLED))
VENUE = frozenset(('VENUE_EVENT', 'VENUE_QUERY'))


def add_exact(a: Decimal, b: Decimal) -> Decimal:
    low = min(a.as_tuple().exponent, b.as_tuple().exponent)
    with localcontext() as ctx:
        ctx.prec = max(1, max(a.adjusted(), b.adjusted()) - low + 2)
        return a + b


def subtract_exact(a: Decimal, b: Decimal) -> Decimal:
    # Unary minus applies the ambient Decimal precision before add_exact runs.
    return add_exact(a, b.copy_negate())


class OrderProjector:
    def __init__(self, *, quantity_at: Callable[[t.DomainEvent], t.Quantity] | None = None,
                 require_evidence: Callable[[t.DomainEvent], None] | None = None):
        # FillLedger.quantity_at reads deduplicated, committed fills through the
        # event's sequence. A raw increment must never be added to a REST total.
        self.quantity_at = quantity_at
        self.require_evidence = require_evidence

    def apply(self, event: t.DomainEvent, previous: t.OrderProjection) -> t.ProjectionUpdate:
        scope = previous.order.identity.scope
        if scope.environment == t.Environment.LIVE:
            raise ValueError('LIVE is not enabled in this stage')
        if event.scope != scope:
            raise ScopeMismatch('projection scope mismatch')
        if event.aggregate_id != 'order:' + previous.order.identity.logical_client_order_id:
            raise ScopeMismatch('projection aggregate mismatch')
        if event.revision != previous.sequence + 1:
            raise SequenceConflict('projection requires contiguous aggregate sequence')
        utc_timestamp(event.occurred_at)
        if any(ref.environment != scope.environment for ref in event.evidence):
            raise ScopeMismatch('projection evidence environment mismatch')
        for q in (previous.filled_quantity, previous.leaves_quantity):
            if q.value < 0 or q.unit != previous.filled_quantity.unit:
                raise ValueError('invalid previous quantities')
        payload = event.payload
        if isinstance(payload, t.Fill):
            if payload.order != previous.order.identity or payload.scope != scope:
                raise ScopeMismatch('fill order mismatch')
            if payload.evidence not in event.evidence:
                raise MissingEvidence('fill raw evidence missing')
            if self.quantity_at is None:
                raise MissingEvidence('persisted fill history required; cannot add to cumulative snapshot')
            total = self.quantity_at(event)
            if total.unit != previous.filled_quantity.unit or total.value < 0:
                raise ValueError('invalid accounted fill total')
            # Incomplete historical fills cannot reduce a reported cumulative
            # total. An explicit correction is the sole downward quantity path.
            value = max(total.value, previous.filled_quantity.value)
            if payload.correction_of:
                # Preserve snapshot-only history: correcting one known fill
                # cannot erase the uncollected portion of a cumulative report.
                value = max(total.value, add_exact(previous.filled_quantity.value,
                                                  payload.quantity.value))
            delta = subtract_exact(value, previous.filled_quantity.value)
            leaves = max(Decimal(0), subtract_exact(previous.leaves_quantity.value, delta))
            if previous.lifecycle_state in TERMINAL and previous.outcome_provenance in VENUE:
                leaves = Decimal(0)
            lifecycle = previous.lifecycle_state
            provenance = previous.outcome_provenance
            if (lifecycle not in TERMINAL or provenance not in VENUE) and value > 0:
                # A fill total alone cannot prove the original order quantity
                # or that an amendment/cancel completed.
                lifecycle = t.OrderLifecycle.PARTIALLY_FILLED
                provenance = 'VENUE_EVENT'
            projection = replace(previous, lifecycle_state=lifecycle,
                outcome_provenance=provenance,
                lifecycle_revision=(0 if previous.outcome_provenance not in VENUE
                                    and provenance in VENUE else previous.lifecycle_revision),
                filled_quantity=t.Quantity(value=value, unit=total.unit),
                leaves_quantity=t.Quantity(value=leaves, unit=total.unit),
                sequence=event.revision)
        elif isinstance(payload, t.OrderProjection):
            if payload.order != previous.order:
                raise ScopeMismatch('order identity or ownership mismatch')
            for q in (payload.filled_quantity, payload.leaves_quantity):
                if q.value < 0 or q.unit != previous.filled_quantity.unit:
                    raise ValueError('invalid snapshot quantities')
            if payload.outcome_provenance in VENUE and not event.evidence:
                raise MissingEvidence('venue projection requires raw evidence')
            is_venue = payload.outcome_provenance in VENUE
            if is_venue:
                if self.require_evidence is None:
                    raise MissingEvidence('venue projection requires an evidence verifier')
                self.require_evidence(event)
            if type(payload.lifecycle_revision) is not int or payload.lifecycle_revision < 0:
                raise ValueError('invalid lifecycle revision')
            fresh = (payload.lifecycle_revision > previous.lifecycle_revision
                     or (is_venue and previous.outcome_provenance not in VENUE))
            # Local/inferred events may show a local lifecycle, but cannot
            # replace venue quantities or resolve ANY pending command.
            filled = max(previous.filled_quantity.value, payload.filled_quantity.value) if is_venue else previous.filled_quantity.value
            lifecycle = previous.lifecycle_state
            accept_lifecycle = fresh and (
                (is_venue and previous.outcome_provenance not in VENUE)
                or ((is_venue or previous.outcome_provenance not in VENUE)
                    and (lifecycle not in TERMINAL or payload.lifecycle_state == lifecycle)))
            if accept_lifecycle:
                lifecycle = payload.lifecycle_state
            leaves = previous.leaves_quantity.value
            if is_venue and accept_lifecycle:
                leaves = payload.leaves_quantity.value
                baseline = payload.filled_quantity.value
            else:
                baseline = previous.filled_quantity.value
            if is_venue:
                leaves = max(Decimal(0), subtract_exact(leaves,
                    max(Decimal(0), subtract_exact(filled, baseline))))
            # A local terminal is not evidence that the remainder is gone.
            if is_venue and accept_lifecycle and lifecycle in TERMINAL:
                leaves = Decimal(0)
            projection = replace(previous, lifecycle_state=lifecycle,
                outcome_provenance=payload.outcome_provenance if accept_lifecycle else previous.outcome_provenance,
                filled_quantity=t.Quantity(value=filled, unit=previous.filled_quantity.unit),
                leaves_quantity=t.Quantity(value=leaves, unit=previous.leaves_quantity.unit),
                lifecycle_revision=payload.lifecycle_revision if accept_lifecycle else previous.lifecycle_revision,
                sequence=event.revision)
        else:
            raise TypeError('unsupported order event payload')
        # Inputs are already ledger events. Returning them in events would make
        # ProjectionRepository append the same event twice. No commands emitted.
        return t.ProjectionUpdate(projection=projection, events=())
