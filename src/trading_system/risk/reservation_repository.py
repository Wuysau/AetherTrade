"""IP-07 append-only views over IP-03's existing ledger, under its group lock.

The original reservation rows/NUMERIC index are immutable creation facts.
Transitions live in namespaced audit_events; consumers must use this projection,
not treat the creation index as a current balance. No schema changes are needed.
"""
from dataclasses import replace
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.wire import TradeIntent, ExecutionCommand
from trading_system.storage.models import decode, atomic_write, MissingEvidence, scope_id


class ReservationRepository:
    def __init__(self, tx):
        self.tx = tx

    def get(self, reservation_id):
        initial = self.tx._latest('reservations', reservation_id, t.CapitalReservation)
        if initial is None:
            raise MissingEvidence('reservation not in this scope')
        return self.tx._latest('audit_events', 'ip07:reservation:' + reservation_id,
                               t.ReservationProjection) or self.initial(initial)

    @staticmethod
    def initial(reservation):
        return t.ReservationProjection(reservation=reservation, held=reservation.amount,
            reflected=Decimal(0), release_pending=Decimal(0), sequence=0)

    def group(self):
        # Deliberately includes every account/product/instrument in the pool.
        rows = self.tx.execute("SELECT r.payload, p.payload FROM ledger.reservations r "
            "LEFT JOIN LATERAL (SELECT payload FROM ledger.audit_events a "
            "WHERE a.scope_id=r.scope_id AND a.record_id='ip07:reservation:' || r.record_id "
            "ORDER BY sequence DESC LIMIT 1) p ON true "
            "WHERE r.payload->>'environment'=%s AND r.payload->>'collateral_group_id'=%s",
            (str(self.tx.scope.environment), self.tx.group_id)).fetchall()
        return tuple(decode(t.ReservationProjection, projection) if projection is not None
                     else self.initial(decode(t.CapitalReservation, initial))
                     for initial, projection in rows)

    def existing(self, intent):
        stored = self.tx._latest('intents', intent.intent_id, TradeIntent)
        if stored is None:
            return None
        if stored != intent:
            raise ValueError('intent identity reused with different content')
        return tuple(self.get(r.reservation_id) for r in self.tx.reservations.for_intent(intent.intent_id))

    def first_commands(self, intent_id):
        """Read roots even when an evaluated reduction needs no reserved assets.

        Reservation references alone cannot establish an atomic bundle in that
        case. Read creation rows so later execution transitions do not hide the
        command that was committed with the intent.
        """
        rows = self.tx.execute(
            "SELECT payload FROM ledger.commands WHERE scope_id=%s AND sequence=1 "
            "AND payload->>'intent_id'=%s AND payload->>'parent_command_id' IS NULL",
            (scope_id(self.tx.scope), intent_id)).fetchall()
        return tuple(decode(ExecutionCommand, row[0]) for row in rows)

    @atomic_write
    def save(self, projection):
        self.tx._append('audit_events', 'ip07:reservation:' + projection.reservation.reservation_id,
                        projection, projection.sequence - 1)

    @atomic_write
    def risk(self, decision):
        self.tx._append('audit_events', 'ip07:risk:' + decision.intent_id, decision, 0)

    @atomic_write
    def uncertain(self, reservation, at):
        key = 'ip07:unknown:' + reservation.reservation_id
        if self.tx._latest('risk_locks', key, t.RiskLock) is None:
            self.tx.risk_locks.append(t.RiskLock(lock_id=key, scope=self.tx.scope,
                cause='RESERVATION_COMMAND_UNKNOWN', state='ACTIVE',
                permission=t.RiskPermission.NO_NEW_RISK,
                required_evidence=('command_terminal', 'fills_and_balance_reconciled'), raised_at=at), 0)
        if self.tx._latest('reconciliation_cases', key, t.ReconciliationCase) is None:
            self.tx._append('reconciliation_cases', key, t.ReconciliationCase(
                case_id=key, scope=self.tx.scope, command_ids=reservation.command_refs,
                position_ids=(), missing=('command_terminal', 'fills_and_balance_reconciled'),
                query_budget=t.QueryBudget(maximum_requests=1, window_seconds=60,
                    minimum_interval_ms=1000, retry_after=None),
                attempts=0, next_query_at=at, state='WAITING'), 0)


def balance_watermark(book, evidence):
    """Bind credit to the exact balance snapshot, including scope and sequence."""
    return evidence.id + ':' + evidence.hash + ':' + canonical_hash(domain_payload(
        replace(book, reservations=())))


def for_balance(projection, book):
    """Reflection is creditable only against the exact reconciled balance proof.

    A late/other shard's balance cannot inherit reflection credit. This can
    conservatively overhold until that shard receives the same evidence.
    """
    watermark = projection.reservation.evidence_watermark
    proven = any(watermark == balance_watermark(book, e) for e in book.evidence)
    if proven:
        return projection
    if projection.reservation.state == t.ReservationState.RELEASED:
        return replace(projection, held=projection.reservation.amount, reflected=Decimal(0),
            release_pending=projection.reservation.amount,
            reservation=replace(projection.reservation, state=t.ReservationState.RELEASE_PENDING))
    return replace(projection, held=projection.reservation.amount, reflected=Decimal(0))


def merge_holds(group, book):
    """Keep unknown snapshot obligations; a ledger reload must not erase them.

    A local ID is replaced only when its immutable creation facts match. An
    external hold receives no reflection/release credit without our mapping.
    """
    for p in (*group, *book.reservations):
        values = (p.reservation.amount, p.held, p.reflected, p.release_pending)
        if (any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in values)
                or p.held > p.reservation.amount or p.reflected > p.held or p.release_pending > p.held):
            raise ValueError('invalid reservation amounts')
    by_id = {p.reservation.reservation_id: p for p in group}
    if len(by_id) != len(group):
        raise ValueError('duplicate ledger reservation')
    seen = set()
    for projection in book.reservations:
        r = projection.reservation
        if r.reservation_id in seen:
            raise ValueError('duplicate book reservation')
        seen.add(r.reservation_id)
        local = by_id.get(r.reservation_id)
        if local is not None:
            creation = lambda item: replace(item, state=t.ReservationState.HELD,
                reflected_amount=Decimal(0), evidence_watermark='')
            if creation(local.reservation) != creation(r):
                raise ValueError('reservation identity collision')
        else:
            if r.amount == 0:
                raise ValueError('unknown reservation needs a positive amount bound')
            # Even a claimed RELEASED external entry remains held until bound.
            by_id[r.reservation_id] = replace(projection, held=r.amount,
                reflected=Decimal(0), release_pending=r.amount,
                reservation=replace(r, state=t.ReservationState.RELEASE_PENDING,
                    reflected_amount=Decimal(0), evidence_watermark=''))
    return tuple(for_balance(p, book) for p in by_id.values())
