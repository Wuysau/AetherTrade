"""Narrow server-held authorization for removing a healthy stop before reduction.

The stop must be explicitly canceled and queried terminal before the separately
authorized reduction can be submitted. No order or protection is synthesized.
"""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from trading_system.contracts import types as t
from trading_system.storage.models import decode, scope_id


@dataclass(frozen=True)
class ProtectionAdjustment:
    parent: str
    cancel: str
    actual: str
    quantity: str


@dataclass(frozen=True)
class AdjustmentInvalidation:
    cancel: str
    reason: str
    actual: str
    book_sequence: int
    book_fingerprint: str
    reviewed_at: str


def invalidation(tx, cancel_id):
    return tx._latest('audit_events', 'paper:adjustment-invalidated:' + cancel_id, AdjustmentInvalidation)


def invalidate_obsolete(tx, record, *, owner, now):
    """Retire only a proved obsolete authorization; grant no close/unlock authority."""
    if invalidation(tx, record['cancel']):
        return True
    from trading_system.execution.commands import CommandRepository
    from trading_system.recovery.reconciler import valid_ref
    cancel = CommandRepository(tx).get(record['cancel'])
    parent = CommandRepository(tx).get(record['parent'])
    if not cancel or not parent:
        return False
    stop = tx._latest('order_projections', parent.logical_client_order_id, t.OrderProjection)
    book = tx.projections.book(tx.scope)
    coverage = book.coverage
    actual = sum((p.actual_quantity.value for p in book.positions), Decimal(0))
    if (actual == Decimal(record['actual']) or actual < 0 or book.active_orders or len(book.positions) > 1
            or cancel.operation != 'CANCEL' or cancel.state != 'VENUE_CONFIRMED'
            or parent.operation != 'SET_PROTECTION' or cancel.parent_command_id != parent.command_id
            or not stop or stop.lifecycle_state != 'CANCELED' or stop.command_certainty != 'CONFIRMED'
            or stop.pending_operation != 'NONE' or stop.order.owner_strategy_id != owner
            or stop.order.identity.scope != tx.scope
            or stop.order.ownership_epoch != cancel.ownership_epoch or parent.ownership_epoch != cancel.ownership_epoch
            or cancel.target_order_id != stop.order.identity.venue_order_id
            or book.scope != tx.scope or coverage.scope != tx.scope
            or not coverage.query_succeeded or not coverage.pagination_complete or not coverage.supported or coverage.missing
            or not book.evidence or any(not valid_ref(e, tx.scope, now, 60) for e in book.evidence)
            or any(p.scope != tx.scope or p.owner_strategy_id != owner or p.ownership_epoch != cancel.ownership_epoch
                or p.actual_quantity.unit != stop.filled_quantity.unit
                for p in book.positions)
            or any(lock.permission == t.RiskPermission.RECOVERY_REQUIRED for lock in tx.risk_locks.applicable(tx.scope))):
        return False
    ids = tx.execute("SELECT DISTINCT record_id FROM ledger.commands WHERE scope_id=%s",
        (scope_id(tx.scope),)).fetchall()
    # Outcomes live in receipts; raw command snapshots remain MAY_HAVE_SENT.
    if any(CommandRepository(tx).get(identifier).state not in ('VENUE_CONFIRMED', 'VENUE_REJECTED', 'ABORTED_UNSENT')
            for identifier, in ids):
        return False
    # A changed projection alone cannot abandon responsibility. Reprove exact
    # remaining quantity from every durable fill and its original order side.
    fills = tx.execute("SELECT f.payload,c.payload->'canonical_payload'->>'side' FROM ledger.fills f "
        "JOIN ledger.commands c ON c.scope_id=f.scope_id AND c.sequence=1 "
        "AND c.payload->>'logical_client_order_id'=f.payload->'order'->>'logical_client_order_id' "
        "WHERE f.scope_id=%s", (scope_id(tx.scope),)).fetchall()
    if any(side not in ('BUY', 'SELL') for _, side in fills):
        return False
    net = sum((Fraction(decode(t.Fill, raw).quantity.value) * (1 if side == 'BUY' else -1)
        for raw, side in fills), Fraction(0))
    if net != Fraction(actual):
        return False
    tx._append('audit_events', 'paper:adjustment-invalidated:' + record['cancel'],
        AdjustmentInvalidation(cancel=record['cancel'], reason='QUANTITY_CHANGED_AFTER_CONFIRMED_CANCEL',
            actual=str(actual), book_sequence=book.sequence, book_fingerprint=book.basis_fingerprint, reviewed_at=now), 0)
    return True


def adjustment(tx, cancel_id):
    row = tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s '
        'ORDER BY sequence DESC LIMIT 1', (scope_id(tx.scope), 'paper:adjustment:' + cancel_id)).fetchone()
    return row[0] if row else None


def intentional_cancel(tx, parent, order):
    """Never reclassify a rejection/expiry or an unconfirmed cancellation."""
    if order.lifecycle_state != 'CANCELED' or order.command_certainty != 'CONFIRMED' or order.pending_operation != 'NONE':
        return False
    from trading_system.execution.commands import CommandRepository
    rows = tx.execute("SELECT payload->>'cancel' FROM ledger.audit_events WHERE scope_id=%s "
        "AND record_id LIKE 'paper:adjustment:%%' AND payload->>'parent'=%s",
        (scope_id(tx.scope), parent.command_id)).fetchall()
    for identifier, in rows:
        cancel = CommandRepository(tx).get(identifier)
        if (cancel and cancel.operation == 'CANCEL' and cancel.state == 'VENUE_CONFIRMED'
                and cancel.parent_command_id == parent.command_id
                and cancel.target_order_id == order.order.identity.venue_order_id):
            return True
    return False


def adjustment_views(tx, account):
    from trading_system.contracts.canonical import canonical_hash
    from trading_system.execution.commands import CommandRepository
    records = tx.execute("SELECT payload FROM ledger.audit_events WHERE scope_id=%s "
        "AND record_id LIKE 'paper:adjustment:%%' ORDER BY processed_at", (scope_id(tx.scope),)).fetchall()
    result = []
    for record, in records:
        cancel = CommandRepository(tx).get(record['cancel'])
        parent = CommandRepository(tx).get(record['parent'])
        stop = tx._latest('order_projections', parent.logical_client_order_id, t.OrderProjection)
        close_id = 'console-command:' + canonical_hash({'key': 'adjustment-close:' + record['cancel'], 'account': account})
        close = CommandRepository(tx).get(close_id)
        needs_review = any(lock.permission == t.RiskPermission.RECOVERY_REQUIRED
            for lock in tx.risk_locks.applicable(tx.scope))
        book = tx.projections.book(tx.scope)
        changed = sum((p.actual_quantity.value for p in book.positions), Decimal(0)) != Decimal(record['actual'])
        invalidated = invalidation(tx, record['cancel'])
        phase = ('INVALIDATED_QUANTITY_CHANGED' if invalidated
            else 'REDUCTION_ACCEPTED' if close else 'REVIEW_REQUIRED' if needs_review or changed
            else 'READY_TO_CONTINUE' if stop and stop.lifecycle_state == 'CANCELED'
            and stop.command_certainty == 'CONFIRMED' and stop.pending_operation == 'NONE'
            else 'CANCEL_CONFIRM_REQUIRED')
        result.append(dict(cancel_command_id=record['cancel'], stop_command_id=record['parent'],
            reduction_command_id=close_id if close else None, quantity=record['quantity'],
            phase=phase, invalidation_reason=invalidated.reason if invalidated else None,
            invalidated_actual_quantity=invalidated.actual if invalidated else None,
            protection_responsibility='RETAINED_UNTIL_EXIT_OR_VERIFIED_REPLACEMENT'))
    return result


def verified_adjustment(tx, command, parent, target, book):
    if parent is None or parent.operation != 'SET_PROTECTION':
        return False
    record = adjustment(tx, command.command_id)
    plan = tx._latest('protection_plans', 'protection:' + scope_id(tx.scope), t.ProtectionPlan)
    actual = sum((p.actual_quantity.value for p in book.positions), Decimal(0))
    return bool(record and parent and parent.operation == 'SET_PROTECTION'
        and parent.canonical_payload.side == 'SELL' and parent.canonical_payload.order_type == 'STOP_MARKET'
        and record['parent'] == parent.command_id and record['cancel'] == command.command_id
        and Decimal(record['actual']) == actual and 0 < Decimal(record['quantity']) <= actual
        and len(book.positions) == 1 and len(book.active_orders) == 1
        and book.positions[0].owner_strategy_id == target.order.owner_strategy_id
        and book.positions[0].ownership_epoch == command.ownership_epoch == parent.ownership_epoch
        and plan and plan.state == 'VERIFIED_NATIVE' and plan.coverage_quantity.value == actual
        and plan.native_order_ids == (target.order,) and target.leaves_quantity.value == actual
        and target.command_certainty == 'CONFIRMED' and target.pending_operation == 'NONE'
        and target.lifecycle_state in ('SUBMITTED', 'ACCEPTED')
        and not any(lock.permission == t.RiskPermission.RECOVERY_REQUIRED
            or (lock.permission == t.RiskPermission.EXIT_ONLY and Decimal(record['quantity']) != actual)
            for lock in tx.risk_locks.applicable(tx.scope)))
