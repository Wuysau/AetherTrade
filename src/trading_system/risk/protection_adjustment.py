"""Narrow server-held authorization for removing a healthy stop before reduction.

The stop must be explicitly canceled and queried terminal before the separately
authorized reduction can be submitted. No order or protection is synthesized.
"""
from dataclasses import dataclass
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.storage.models import scope_id


@dataclass(frozen=True)
class ProtectionAdjustment:
    parent: str
    cancel: str
    actual: str
    quantity: str


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
        phase = ('REDUCTION_ACCEPTED' if close else 'REVIEW_REQUIRED' if needs_review
            else 'READY_TO_CONTINUE' if stop and stop.lifecycle_state == 'CANCELED'
            and stop.command_certainty == 'CONFIRMED' and stop.pending_operation == 'NONE'
            else 'CANCEL_CONFIRM_REQUIRED')
        result.append(dict(cancel_command_id=record['cancel'], stop_command_id=record['parent'],
            reduction_command_id=close_id if close else None, quantity=record['quantity'],
            phase=phase, protection_responsibility='RETAINED_UNTIL_EXIT_OR_VERIFIED_REPLACEMENT'))
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
