"""Fail-closed offline exceptions to cause locks, never execution authority.

Book/order projections omit side and protection parameters. A trusted LOCAL
archive reader must establish current position, balances/ownership allocations,
and complete working-order coverage under the ledger mutex, then return the
typed proof below. Hashes and evidence labels alone do not establish provenance.
No production reader is installed here; public/user/model payloads must never
be used as this callback. Authorization, reservations and owner fences remain
the callers' responsibility on both sides of the durable send boundary.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import localcontext
from fractions import Fraction

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, decimal_string, domain_payload, utc_timestamp
from trading_system.contracts.wire import ABSENT, ContractCodec
from trading_system.execution.commands import command_hash
from trading_system.protection.native import owned, report_errors
from trading_system.recovery.reconciler import valid_ref
from trading_system.storage.models import scope_id
from .locks import ACTIONS


def digest(record):
    return canonical_hash(domain_payload(record))


@dataclass(frozen=True, kw_only=True)
class WorkingTerms:
    order: t.OwnedOrderRef
    side: str
    evidence: t.EvidenceRef


@dataclass(frozen=True, kw_only=True)
class ActionProof:
    """Server-held attestation bound to this transaction's exact inputs.

    The reader verifies archived source semantics and complete current economic
    state; this type is not an API contract and its construction grants nothing.
    Existing working quantities are all counted, including protective orders.
    No OCO/coexistence or unbounded full-position-exit assumption is made.
    """
    transaction_id: str
    request_hash: str
    intent_hash: str
    book_hash: str
    report: t.VenueReport
    instrument: t.InstrumentSpec
    orders: tuple[WorkingTerms, ...]
    valid_until: str


class LockedActionVerifier:
    def __init__(self, read_proof=None, *, maximum_age_seconds=60):
        if type(maximum_age_seconds) is not int or maximum_age_seconds <= 0:
            raise ValueError('positive evidence age required')
        self.read_proof, self.maximum_age_seconds = read_proof, maximum_age_seconds

    def verify(self, tx, command, intent, now, *, book=None) -> str | None:
        """Return verified validity limit, or None. Callers must recheck at I/O."""
        try:
            with localcontext() as ctx:
                ctx.prec = 80
                # Even when the cause-lock repository is empty, the current
                # owned-position projection may impose a stricter permission.
                # Missing/stale book evidence is never proof of no locks.
                if book is None:
                    book = tx.projections.book(tx.scope)
                coverage = book.coverage
                if (book.scope != tx.scope or coverage.scope != tx.scope
                        or coverage.query_succeeded is not True
                        or coverage.pagination_complete is not True
                        or coverage.supported is not True or coverage.missing
                        or type(book.sequence) is not int or book.sequence < 1
                        or not book.evidence or any(not valid_ref(e, tx.scope, now,
                            self.maximum_age_seconds) for e in book.evidence)):
                    return None
                ledger_locks = tuple(tx.risk_locks.applicable(tx.scope))
                locks = ledger_locks + tuple(
                    lock for p in book.positions for lock in p.risk_locks)
                if not locks:
                    return min([utc_timestamp(intent.valid_until)] + [utc_timestamp(e.visible_at)
                        + timedelta(seconds=self.maximum_age_seconds) for e in book.evidence]).isoformat()
                return self._verify(tx, command, intent, now, locks, book, ledger_locks)
        except Exception:
            # Unavailable/malformed local proof cannot relax a lock.
            return None

    def _verify(self, tx, command, intent, now, locks, book, ledger_locks):
        scope = tx.scope
        if scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH):
            return None
        allowed = ACTIONS[t.RiskPermission.NORMAL]
        for lock in locks:
            # Cross-instrument DAILY_LOSS applies only when returned by the
            # ledger repository after source identity/history/binding checks.
            # A position or caller cannot assert its own foreign-scope alias.
            if ((lock.scope != scope and not (lock.cause == 'DAILY_LOSS' and lock in ledger_locks))
                    or lock.state != 'ACTIVE'
                    or lock.cause in {'ORDER_OUTCOME_UNKNOWN', 'RESERVATION_COMMAND_UNKNOWN'}):
                return None
            allowed &= ACTIONS.get(lock.permission, frozenset())
        if not allowed & {'EXIT', 'MANAGE'} or self.read_proof is None:
            return None
        # A second queued reduction could otherwise spend the same reducible
        # position before either command has produced a working-order event.
        queued = tx.execute(
            'SELECT c.record_id FROM (SELECT DISTINCT ON (record_id) record_id,payload '
            'FROM ledger.commands WHERE scope_id=%s ORDER BY record_id,sequence DESC) c '
            "WHERE c.record_id<>%s AND c.payload->>'state'='DURABLE_READY'",
            (scope_id(scope), command.command_id)).fetchall()
        if queued:
            return None
        proof = self.read_proof(tx, command, intent, book)
        if (type(proof) is not ActionProof or not tx.transaction_id
                or proof.transaction_id != tx.transaction_id
                or proof.request_hash != command_hash(command)
                or proof.intent_hash != digest(intent) or proof.book_hash != digest(book)
                or utc_timestamp(now) >= utc_timestamp(proof.valid_until)):
            return None
        encoded = ContractCodec().encode(command)
        if (canonical_hash(encoded['canonical_payload']) != command.payload_hash
                or command.intent_id != intent.intent_id
                or command.environment != scope.environment or intent.environment != scope.environment
                or command.venue_account_id != scope.venue_account_id
                or intent.venue_account_id != scope.venue_account_id
                or command.product != scope.product or intent.product != scope.product
                or command.instrument_uid != scope.instrument_uid or intent.instrument_uid != scope.instrument_uid
                or intent.venue_leg != scope.venue_leg or intent.collateral_group_id != tx.group_id
                or command.ownership_epoch != intent.ownership_epoch
                or command.policy_revision != intent.risk_policy_revision
                or type(command.execution_epoch) is not int or command.execution_epoch < 1):
            return None
        report = proof.report
        if (book.scope != scope or type(book.sequence) is not int or book.sequence < 1
                or report_errors(report, scope, now, self.maximum_age_seconds)
                or report.coverage != book.coverage or report.positions != book.positions
                or report.orders != book.active_orders or report.balances != book.balances
                or not book.evidence or any(e not in report.evidence for e in book.evidence)):
            return None
        if command.operation == 'CANCEL':
            # Cancel only a proved owned OPENING root. Cancelling protection or
            # an exit can increase retained risk and is deliberately excluded.
            matches = [order for order in book.active_orders if order.order.identity.venue_order_id == command.target_order_id]
            if len(matches) != 1 or command.parent_command_id is None:
                return None
            target = matches[0]
            from trading_system.execution.commands import CommandRepository
            parent = CommandRepository(tx).get(command.parent_command_id)
            details = [term for term in proof.orders if term.order == target.order]
            from .protection_adjustment import verified_adjustment
            adjusting = verified_adjustment(tx, command, parent, target, book)
            if (parent is None or (not adjusting and (parent.operation != 'SUBMIT' or parent.parent_command_id is not None
                    or parent.canonical_payload.side != 'BUY')) or scope.product != t.Product.SPOT
                    or parent.logical_client_order_id != target.order.identity.logical_client_order_id
                    or target.order.owner_strategy_id != intent.owner_strategy_id
                    or target.order.ownership_epoch != intent.ownership_epoch
                    or target.command_certainty != 'CONFIRMED' or target.pending_operation != 'NONE'
                    or target.lifecycle_state not in ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED')
                    or len(details) != 1 or details[0].side != ('SELL' if adjusting else 'BUY') or details[0].evidence not in report.evidence
                    or target.leaves_quantity.unit != intent.quantity_unit or target.leaves_quantity.value <= 0
                    or not ('EXIT' in allowed or 'MANAGE' in allowed)):
                return None
            return min(utc_timestamp(proof.valid_until), *(utc_timestamp(e.visible_at)
                + timedelta(seconds=self.maximum_age_seconds) for e in report.evidence)).isoformat()
        # A single signed position is required. Hedge SHORT legs use negative
        # quantities consistently with RiskEvaluator; ambiguity fails closed.
        if len(book.positions) != 1:
            return None
        position = book.positions[0]
        actual = position.actual_quantity.value
        if (position.scope != scope or not position.owner_book_id
                or position.owner_strategy_id != intent.owner_strategy_id
                or position.ownership_epoch != intent.ownership_epoch
                or position.actual_quantity.unit != intent.quantity_unit or actual == 0
                or (scope.venue_leg == 'LONG' and actual < 0)
                or (scope.venue_leg == 'SHORT' and actual > 0)
                or (scope.product != t.Product.USD_M_PERP and (actual < 0 or scope.venue_leg != 'BOTH'))):
            return None
        side = 'SELL' if actual > 0 else 'BUY'
        spec, payload = proof.instrument, command.canonical_payload
        unit = position.actual_quantity.unit
        quantity = decimal_string(payload.quantity)
        if (spec.instrument_uid != scope.instrument_uid or spec.product != scope.product
                or spec.quantity_step.unit != unit or spec.minimum_quantity.unit != unit
                or spec.quantity_step.value <= 0 or spec.minimum_quantity.value <= 0
                or quantity < spec.minimum_quantity.value or quantity % spec.quantity_step.value
                or payload.side != side or payload.quote_notional is not ABSENT
                or payload.full_position_exit not in (ABSENT, False)
                or payload.position_side not in (ABSENT, scope.venue_leg)
                or (scope.venue_leg != 'BOTH' and payload.position_side != scope.venue_leg)
                or (scope.product == t.Product.USD_M_PERP and payload.reduce_only is not True)
                or command.target_order_id is not None or command.venue_order_id is not None):
            return None
        terms = proof.orders
        if (len(terms) != len(book.active_orders)
                or len({d.order.identity.logical_client_order_id for d in terms}) != len(terms)):
            return None
        # Exact rational sums keep even a tiny residual from disappearing under
        # Decimal context rounding and overstating available close quantity.
        working = Fraction(0)
        for order in book.active_orders:
            details = [d for d in terms if type(d) is WorkingTerms and d.order == order.order]
            if (command.operation == 'SET_PROTECTION' and len(details) == 1 and details[0].side != side
                    and owned(order.order, position) and order.command_certainty == 'CONFIRMED'
                    and order.pending_operation == 'NONE' and details[0].evidence in report.evidence):
                # A partially-filled opening order does not consume closing
                # quantity. Verify its exact durable root instead of allowing
                # an arbitrary opposite-side working order to be ignored.
                row = tx.execute("SELECT payload FROM ledger.commands WHERE scope_id=%s AND sequence=1 "
                    "AND payload->>'logical_client_order_id'=%s", (scope_id(scope),
                        order.order.identity.logical_client_order_id)).fetchone()
                from trading_system.contracts.wire import ExecutionCommand
                from trading_system.storage.models import decode
                original = decode(ExecutionCommand, row[0]) if row else None
                if (original is not None and original.operation == 'SUBMIT' and original.parent_command_id is None
                        and original.canonical_payload.side == details[0].side
                        and original.ownership_epoch == position.ownership_epoch
                        and order.lifecycle_state in ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED')):
                    continue
                return None
            if (len(details) != 1 or not owned(order.order, position)
                    or details[0].side != side or details[0].evidence not in report.evidence
                    or order.command_certainty != 'CONFIRMED' or order.pending_operation != 'NONE'
                    or order.outcome_provenance not in ('VENUE_EVENT', 'VENUE_QUERY')
                    or order.lifecycle_state not in ('ACCEPTED', 'PARTIALLY_FILLED')
                    or order.leaves_quantity.unit != unit or order.leaves_quantity.value <= 0):
                return None
            working += Fraction(order.leaves_quantity.value)
        if Fraction(quantity) + working > abs(Fraction(actual)):
            return None
        # A report just inside the freshness bound must not become stale while
        # the second dispatch transaction closes. Carry every evidence expiry
        # through to the final pre-I/O clock check, alongside the authority TTL.
        validity = min([utc_timestamp(proof.valid_until)] + [utc_timestamp(at)
            + timedelta(seconds=self.maximum_age_seconds)
            for at in (report.as_of, *(e.visible_at for e in report.evidence))]).isoformat()
        if command.operation == 'SET_PROTECTION':
            if 'MANAGE' not in allowed or not position.active_exit_plan_id:
                return None
            plan = tx._latest('protection_plans', position.active_exit_plan_id, t.ProtectionPlan)
            # Repair only a durable gap with an explicitly bounded stop matching
            # the approved plan. Never cancel/replace working protection here.
            valid = (plan is not None and plan.plan_id == position.active_exit_plan_id
                and plan.scope == scope and plan.mode == 'NATIVE' and plan.monitor_id is None
                and plan.state in ('GAP', 'REPAIRING') and plan.coverage_gap_since is not None
                and utc_timestamp(plan.coverage_gap_since) <= utc_timestamp(report.as_of)
                and plan.coverage_quantity.unit == unit
                and quantity <= plan.coverage_quantity.value <= actual.copy_abs()
                and payload.order_type == 'STOP_MARKET' and payload.price is ABSENT
                and payload.trigger_price == format(plan.stop_price.value, 'f')
                and plan.stop_price.currency == spec.quote_currency
                and spec.tick_size.currency == spec.quote_currency and spec.tick_size.value > 0
                and plan.stop_price.value % spec.tick_size.value == 0
                and intent.exit_plan.stop_price == payload.trigger_price
                # The current command schema cannot encode MARK/BID_ASK.
                # Keep the existing protection compiler's LAST-only boundary.
                and intent.exit_plan.stop_trigger == plan.stop_trigger == 'LAST')
            return validity if valid else None
        valid = ('EXIT' in allowed and command.operation in ('SUBMIT', 'CLOSE')
            and payload.order_type in ('MARKET', 'LIMIT') and payload.trigger_price is ABSENT
            and ((payload.order_type == 'MARKET' and payload.price is ABSENT)
                 or (payload.order_type == 'LIMIT' and decimal_string(payload.price) > 0
                     and spec.tick_size.currency == spec.quote_currency
                     and spec.tick_size.value > 0
                     and decimal_string(payload.price) % spec.tick_size.value == 0)))
        return validity if valid else None
