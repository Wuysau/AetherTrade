"""Quantity obligations read from durable roots while the collateral lock is held.

Cash holds are never converted to order quantities. A sent command remains a
reconciliation obligation even after an ACK. TargetReportService accepts only
an exact archived, complete report from the host's trusted local archive reader;
it does not query a venue, infer fills, or authorize an order.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from hashlib import sha256
import json

from trading_system.accounts.authorization import valid_evidence
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.wire import TradeIntent
from trading_system.execution.commands import CommandRepository
from trading_system.execution.projections import TERMINAL, VENUE
from trading_system.intents.semantic_validation import require
from trading_system.intents.target_compiler import PendingQuantity
from trading_system.storage.models import account_key, decode, scope_id


@dataclass(frozen=True, kw_only=True)
class TargetBasis:
    command_id: str
    book_sequence: int
    actual: t.Quantity
    instrument: t.InstrumentSpec


@dataclass(frozen=True, kw_only=True)
class TargetReport:
    book_hash: str
    report: t.VenueReport
    archive_ref: t.EvidenceRef
    command_ids: tuple[str, ...]


def digest(value):
    return canonical_hash(domain_payload(value))


def roots(tx):
    rows = tx.execute("SELECT record_id FROM ledger.commands WHERE scope_id=%s AND sequence=1 "
        "AND ((payload->>'operation' IN ('SUBMIT','CLOSE') AND payload->>'parent_command_id' IS NULL) "
        "OR payload->>'operation'='SET_PROTECTION') "
        "ORDER BY record_id", (scope_id(tx.scope),)).fetchall()
    commands = CommandRepository(tx)
    return tuple(commands.get(row[0]) for row in rows)


def scope_bound_intent(tx, command):
    previous = tx._latest('intents', command.intent_id, TradeIntent)
    require(previous is not None, 'PRIOR_COMMAND_NOT_OWNED')
    require((previous.environment, previous.venue_account_id, previous.product, previous.instrument_uid,
        previous.venue_leg, previous.collateral_group_id) == (tx.scope.environment,
        tx.scope.venue_account_id, tx.scope.product, tx.scope.instrument_uid,
        tx.scope.venue_leg, tx.group_id), 'PRIOR_COMMAND_SCOPE_MISMATCH')
    return previous


def owned_intent(tx, command, owner):
    previous = scope_bound_intent(tx, command)
    require((previous.owner_strategy_id, previous.ownership_epoch,
        previous.risk_policy_revision) == owner, 'PRIOR_COMMAND_NOT_OWNED')
    return previous


def validate_report(tx, book, report, commands, now):
    """Validate terminal/fill/position reconciliation, not merely an ACK flag."""
    require(isinstance(report, t.VenueReport) and report.coverage == book.coverage
        and report.coverage.scope == tx.scope and report.coverage.query_succeeded
        and report.coverage.pagination_complete and report.coverage.supported
        and not report.coverage.missing and report.next_cursor is None,
        'TARGET_REPORT_INCOMPLETE')
    require(report.positions == book.positions and report.balances == book.balances
        and report.evidence == book.evidence and not book.active_orders
        and bool(report.evidence) and all(valid_evidence(e, tx.scope.environment, now)
            for e in report.evidence) and utc_timestamp(report.as_of) <= now,
        'TARGET_REPORT_BOOK_MISMATCH')
    owner = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
        'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR UPDATE',
        (*account_key(tx.scope), tx.scope.instrument_uid, tx.scope.venue_leg)).fetchone()
    orders = {p.order.identity.logical_client_order_id: p for p in report.orders}
    require(len(orders) == len(report.orders), 'TARGET_REPORT_DUPLICATE_ORDER')
    expected_orders, expected_fills, bases = set(), [], []
    signed_fills = Decimal(0)
    for command in commands:
        require(command.state in ('VENUE_CONFIRMED', 'VENUE_REJECTED'), 'TARGET_COMMAND_UNRESOLVED')
        # A completed handoff changes current ownership, never historical order
        # ownership. Retain every terminal root and its exact original fills;
        # the full baseline/fill equation below still binds the current position.
        # READY commands separately require current ownership before occupying
        # a new target, and reserve/dispatch retain their current-owner guards.
        previous = scope_bound_intent(tx, command)
        basis = tx._latest('audit_events', 'ip07:target-basis:' + command.command_id, TargetBasis)
        require(basis is not None and basis.command_id == command.command_id
            and basis.book_sequence < book.sequence, 'TARGET_BASIS_REQUIRED')
        bases.append(basis)
        expected_orders.add(command.logical_client_order_id)
        order = orders.get(command.logical_client_order_id)
        require(order is not None and order.order.owner_strategy_id == previous.owner_strategy_id
            and order.order.ownership_epoch == previous.ownership_epoch
            and order.order.identity.scope == tx.scope
            and order.order.identity.venue_client_order_id == command.venue_client_order_id
            and (command.venue_order_id is None or order.order.identity.venue_order_id == command.venue_order_id)
            and order.lifecycle_state in TERMINAL and order.outcome_provenance in VENUE
            and order.command_certainty == 'CONFIRMED' and order.pending_operation == 'NONE'
            and order.leaves_quantity.unit == previous.quantity_unit and order.leaves_quantity.value == 0
            and order.filled_quantity.unit == previous.quantity_unit
            and 0 <= order.filled_quantity.value <= Decimal(command.canonical_payload.quantity)
            and utc_timestamp(command.created_at) <= utc_timestamp(report.as_of),
            'TARGET_TERMINAL_ORDER_REQUIRED')
        require((order.lifecycle_state != t.OrderLifecycle.FILLED
            or order.filled_quantity.value == Decimal(command.canonical_payload.quantity))
            and (order.lifecycle_state != t.OrderLifecycle.REJECTED or order.filled_quantity.value == 0),
            'TARGET_TERMINAL_QUANTITY_MISMATCH')
        rows = tx.execute("SELECT payload FROM ledger.fills WHERE scope_id=%s "
            "AND payload->'order'->>'logical_client_order_id'=%s ORDER BY record_id",
            (scope_id(tx.scope), command.logical_client_order_id)).fetchall()
        fills = tuple(decode(t.Fill, row[0]) for row in rows)
        require(all(f.order == order.order.identity and f.scope == tx.scope
            and f.quantity.unit == previous.quantity_unit and f.native_trade_id is not None
            and valid_evidence(f.evidence, tx.scope.environment, now)
            and utc_timestamp(f.occurred_at) <= utc_timestamp(report.as_of) for f in fills),
            'TARGET_FILL_BINDING_REQUIRED')
        require(sum((f.quantity.value for f in fills), Decimal(0)) == order.filled_quantity.value,
            'TARGET_FILLS_INCOMPLETE')
        expected_fills.extend(fills)
        signed_fills += order.filled_quantity.value * (1 if command.canonical_payload.side == 'BUY' else -1)
    require(set(orders) == expected_orders and sorted(report.fills, key=lambda f: f.fill_id)
        == sorted(expected_fills, key=lambda f: f.fill_id), 'TARGET_REPORT_HISTORY_MISMATCH')
    require(bool(bases), 'TARGET_BASIS_REQUIRED')
    baseline = min(bases, key=lambda b: b.book_sequence)
    actual = sum((p.actual_quantity.value for p in book.positions), Decimal(0))
    require(len(book.positions) <= 1 and all(p.scope == tx.scope and
        (p.owner_strategy_id, p.ownership_epoch) == owner[:2] and
        p.actual_quantity.unit == baseline.actual.unit and p.cost_evidence == book.coverage
        for p in book.positions) and actual == baseline.actual.value + signed_fills,
        'TARGET_POSITION_RECONCILIATION_REQUIRED')


class TargetReportService:
    """Persist a verified report from an access-controlled local archive.

    read_archive(ref) must return exact bytes from the server's durable archive,
    never bytes submitted by the trading caller. The report is a normalized
    authoritative full scope query, not a single websocket event or ACK.
    """
    def __init__(self, uow, scope, *, read_archive, clock):
        require(scope.environment != t.Environment.LIVE, 'LIVE_DISABLED')
        self.uow, self.scope, self.read_archive, self.clock = uow, scope, read_archive, clock

    def record(self, ref):
        with localcontext() as ctx:
            ctx.prec = 80
            return self._record(ref)

    def _record(self, ref):
        now = utc_timestamp(self.clock())
        require(valid_evidence(ref, self.scope.environment, now), 'TARGET_ARCHIVE_EVIDENCE_REQUIRED')
        raw = self.read_archive(ref)
        require(isinstance(raw, bytes) and sha256(raw).hexdigest() == ref.hash,
            'TARGET_ARCHIVE_HASH_MISMATCH')
        report = decode(t.VenueReport, json.loads(raw))
        with self.uow.transaction(self.scope) as tx:
            book = tx.projections.book(self.scope)
            commands = tuple(c for c in roots(tx) if c.state not in ('DURABLE_READY', 'ABORTED_UNSENT', 'NOT_SENT'))
            validate_report(tx, book, report, commands, now)
            record = TargetReport(book_hash=digest(book), report=report, archive_ref=ref,
                command_ids=tuple(c.command_id for c in commands))
            key = 'ip07:target-report:' + str(book.sequence)
            prior = tx._latest('audit_events', key, TargetReport)
            require(prior is None or prior == record, 'TARGET_REPORT_CONFLICT')
            if prior is None:
                tx._append('audit_events', key, record, 0)
        return record


def target_obligations(tx, intent, book, scope_holds, now):
    """Return verified unsent quantity; any sent uncertainty requires a report."""
    commands = roots(tx)
    by_id = {c.command_id: c for c in commands}
    for projection in scope_holds:
        reservation = projection.reservation
        if not (projection.held or projection.reflected or projection.release_pending):
            continue
        require(bool(reservation.command_refs) and all(identifier in by_id
            and by_id[identifier].intent_id == reservation.intent_id for identifier in reservation.command_refs),
            'RESERVATION_QUANTITY_REQUIRED')
    sent = tuple(c for c in commands if c.state not in ('DURABLE_READY', 'ABORTED_UNSENT', 'NOT_SENT'))
    if sent:
        record = tx._latest('audit_events', 'ip07:target-report:' + str(book.sequence), TargetReport)
        require(record is not None and record.book_hash == digest(book)
            and record.command_ids == tuple(c.command_id for c in sent)
            and valid_evidence(record.archive_ref, tx.scope.environment, now),
            'TARGET_RECONCILIATION_REQUIRED')
        validate_report(tx, book, record.report, sent, now)
    pending = []
    for command in commands:
        if command.state != 'DURABLE_READY':
            continue
        previous = owned_intent(tx, command,
            (intent.owner_strategy_id, intent.ownership_epoch, intent.risk_policy_revision))
        # An externally observed order/fill contradicts the unsent mapping.
        require(not any(p.order.identity.logical_client_order_id == command.logical_client_order_id
            for p in book.active_orders) and tx.execute("SELECT 1 FROM ledger.fills WHERE scope_id=%s "
            "AND payload->'order'->>'logical_client_order_id'=%s LIMIT 1",
            (scope_id(tx.scope), command.logical_client_order_id)).fetchone() is None,
            'TARGET_RECONCILIATION_REQUIRED')
        quantity = Decimal(command.canonical_payload.quantity)
        require(quantity.is_finite() and quantity > 0 and command.canonical_payload.side in ('BUY', 'SELL'),
            'TARGET_COMMAND_QUANTITY_REQUIRED')
        refs = tuple(p.reservation.reservation_id for p in scope_holds
            if p.reservation.intent_id == previous.intent_id and
            (p.held > 0 or p.reflected > 0 or p.release_pending > 0))
        pending.append(PendingQuantity(intent_id=previous.intent_id,
            signed_quantity=t.Quantity(value=quantity * (1 if command.canonical_payload.side == 'BUY' else -1),
                unit=previous.quantity_unit), reservation_ids=refs, command_refs=(command.command_id,)))
    return tuple(pending)
