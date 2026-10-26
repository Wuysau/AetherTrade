"""Deterministic target arithmetic; results are plans, never send permits.

Use compile_locked inside the reservation/command transaction in production.
The standalone compile method is a pure offline calculation against supplied
facts. Cash reservation amounts cannot be converted to working order quantity.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec, TradeIntent, validate_transition
from trading_system.risk.exposure import WorkingOrder
from .semantic_validation import ProposalRejected, complete, require, quantity_on_grid, time_key


@dataclass(frozen=True, kw_only=True)
class PendingQuantity:
    """Verified unsent/unknown reservation leaves not already in active_orders.

    Multiple asset reservations for one intent must map to one quantity record.
    The ledger reader supplies this mapping; currency amounts are not quantities.
    """
    intent_id: str
    signed_quantity: t.Quantity
    reservation_ids: tuple[str, ...]
    represented_order_ids: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class TargetInputs:
    instrument: t.InstrumentSpec
    expected_instrument: t.InstrumentSpec
    clock: t.ClockReading
    deadline_monotonic_ns: int
    session: t.SessionState
    order_sides: tuple[WorkingOrder, ...] = ()
    pending: tuple[PendingQuantity, ...] = ()
    previous_intent: TradeIntent | None = None
    allow_round_down: bool = False
    context: t.DecisionContext | None = None


class TargetCompiler:
    def __init__(self, inputs: TargetInputs):
        self.inputs = inputs

    def compile(self, intent: TradeIntent, book: t.BookSnapshot) -> t.DeltaPlan:
        ContractCodec().encode(intent)
        values = [Decimal(intent.target_quantity), self.inputs.instrument.quantity_step.value,
                  self.inputs.instrument.minimum_quantity.value,
                  *(p.actual_quantity.value for p in book.positions),
                  *(p.leaves_quantity.value for p in book.active_orders),
                  *(p.signed_quantity.value for p in self.inputs.pending)]
        with localcontext() as ctx:
            ctx.prec = max(80, max(v.adjusted() for v in values)
                           - min(v.as_tuple().exponent for v in values) + len(str(len(values))) + 8)
            return self._compile(intent, book)

    def _compile(self, intent, book):
        f, scope = self.inputs, book.scope
        require(scope.environment != t.Environment.LIVE, 'LIVE_DISABLED')
        require((intent.environment, intent.venue_account_id, intent.product, intent.instrument_uid,
                 intent.venue_leg) == (scope.environment, scope.venue_account_id, scope.product,
                                      scope.instrument_uid, scope.venue_leg), 'INTENT_SCOPE_MISMATCH')
        require(complete(book.coverage, scope) and bool(book.evidence), 'BOOK_INCOMPLETE')
        require(intent.basis_fingerprint == book.basis_fingerprint, 'STALE_BOOK')
        require(f.clock.clock_health == 'HEALTHY' and type(f.clock.monotonic_ns) is int
                and type(f.deadline_monotonic_ns) is int
                and 0 <= f.clock.monotonic_ns < f.deadline_monotonic_ns
                and time_key(f.clock.utc_now) < time_key(intent.valid_until), 'INTENT_EXPIRED')
        for evidence in book.evidence:
            require(evidence.environment == scope.environment
                    and time_key(evidence.visible_at) <= time_key(f.clock.utc_now), 'BOOK_EVIDENCE_INVALID')
        require(f.instrument == f.expected_instrument, 'INSTRUMENT_RULE_CHANGED')
        spec = f.instrument
        require(spec.instrument_uid == scope.instrument_uid and spec.product == scope.product
                and spec.venue == intent.venue and spec.rule_revision > 0, 'INSTRUMENT_BINDING')
        require(f.session.instrument_uid == scope.instrument_uid and f.session.state == 'OPEN'
                and f.session.evidence.environment == scope.environment
                and time_key(f.session.observed_at) <= time_key(f.clock.utc_now), 'SESSION_NOT_OPEN')
        unit = intent.quantity_unit
        require(unit == spec.quantity_step.unit == spec.minimum_quantity.unit
                and unit == {t.Product.SPOT: 'BASE', t.Product.USD_M_PERP: 'CONTRACT',
                             t.Product.US_EQUITY: 'SHARE'}[scope.product], 'QUANTITY_UNIT_MISMATCH')
        require(spec.minimum_quantity.value > 0, 'INVALID_INSTRUMENT_RULE')
        magnitude = quantity_on_grid(Decimal(intent.target_quantity), spec.quantity_step.value,
                                     f.allow_round_down and intent.action == 'SET_TARGET')
        require(scope.product == t.Product.USD_M_PERP or intent.target_side != 'SHORT', 'CASH_SHORT')
        require(scope.venue_leg == 'BOTH' or intent.target_side in (scope.venue_leg, 'FLAT'), 'LEG_MISMATCH')
        target = magnitude * (-1 if intent.target_side == 'SHORT' else 1)
        positions = [p for p in book.positions if p.scope == scope]
        require(len(positions) <= 1, 'AMBIGUOUS_POSITION')
        actual = Decimal(0)
        for p in positions:
            require(p.owner_strategy_id == intent.owner_strategy_id
                    and p.ownership_epoch == intent.ownership_epoch, 'POSITION_NOT_OWNED')
            require(p.actual_quantity.unit == unit and complete(p.cost_evidence, scope), 'POSITION_EVIDENCE')
            actual += p.actual_quantity.value
        require(scope.product == t.Product.USD_M_PERP or actual >= 0, 'CASH_SHORT')
        require(scope.venue_leg == 'BOTH' or (actual >= 0 if scope.venue_leg == 'LONG' else actual <= 0),
                'LEG_POSITION_MISMATCH')
        sides = {s.logical_client_order_id: s.side for s in f.order_sides}
        require(len(sides) == len(f.order_sides), 'DUPLICATE_ORDER_SIDE')
        working = Decimal(0)
        orders, signed_orders, seen = [], [], set()
        for projection in book.active_orders:
            order = projection.order
            if order.identity.scope != scope:
                continue
            identifier = order.identity.logical_client_order_id
            require(identifier not in seen, 'DUPLICATE_WORKING_ORDER')
            seen.add(identifier)
            require(order.owner_strategy_id == intent.owner_strategy_id
                    and order.ownership_epoch == intent.ownership_epoch, 'ORDER_NOT_OWNED')
            require(sides.get(identifier) in ('BUY', 'SELL'), 'WORKING_SIDE_REQUIRED')
            require(projection.leaves_quantity.unit == unit and projection.leaves_quantity.value >= 0
                    and projection.filled_quantity.unit == unit and projection.filled_quantity.value >= 0,
                    'WORKING_QUANTITY_INVALID')
            require(projection.command_certainty != 'UNKNOWN' or projection.leaves_quantity.value > 0,
                    'UNKNOWN_QUANTITY_REQUIRED')
            # Even a local terminal label does not erase possibly live leaves.
            signed = projection.leaves_quantity.value * (1 if sides[identifier] == 'BUY' else -1)
            if signed:
                working += signed
                orders.append(order)
                signed_orders.append(signed)
        require(len({p.reservation.reservation_id for p in book.reservations}) == len(book.reservations),
                'DUPLICATE_RESERVATION')
        for p in book.reservations:
            require(all(isinstance(v, Decimal) and v.is_finite() and v >= 0 for v in
                        (p.held, p.reflected, p.release_pending, p.reservation.amount))
                    and p.reflected <= p.held <= p.reservation.amount
                    and p.release_pending <= p.held, 'INVALID_RESERVATION')
        reservations = {p.reservation.reservation_id: p for p in book.reservations
                        if p.held > 0 or p.release_pending > 0 or p.reflected > 0}
        mapped, pending_ids = set(), set()
        for pending in f.pending:
            require(pending.intent_id not in pending_ids and bool(pending.reservation_ids), 'DUPLICATE_PENDING_INTENT')
            pending_ids.add(pending.intent_id)
            require(pending.signed_quantity.unit == unit, 'PENDING_UNIT_MISMATCH')
            for identifier in pending.reservation_ids:
                require(identifier in reservations and identifier not in mapped, 'RESERVATION_MAPPING_INVALID')
                r = reservations[identifier].reservation
                require(r.intent_id == pending.intent_id and r.owner_strategy_id == intent.owner_strategy_id
                        and r.environment == scope.environment and r.venue_account_id == scope.venue_account_id
                        and r.collateral_group_id == intent.collateral_group_id, 'RESERVATION_SCOPE_MISMATCH')
                mapped.add(identifier)
            if pending.represented_order_ids:
                require(set(pending.represented_order_ids) <= seen
                        and pending.signed_quantity.value == 0, 'RESERVATION_DOUBLE_COUNT')
            else:
                require(pending.signed_quantity.value != 0, 'PENDING_QUANTITY_REQUIRED')
                working += pending.signed_quantity.value
                if pending.signed_quantity.value:
                    signed_orders.append(pending.signed_quantity.value)
        require(mapped == set(reservations), 'RESERVATION_QUANTITY_REQUIRED')
        quantity = lambda value: t.Quantity(value=value, unit=unit)
        if intent.action != 'SET_TARGET':
            require(f.previous_intent is not None, 'PREVIOUS_RESPONSIBILITY_REQUIRED')
            try:
                validate_transition(f.previous_intent, intent)
            except ValueError as exc:
                raise ProposalRejected('RESPONSIBILITY_CHANGED') from exc
            return t.DeltaPlan(scope=scope, target=quantity(target), actual=quantity(actual),
                working=quantity(working), remaining=quantity(Decimal(0)), classification='NONE', cancel_first=())
        # Opposite exposure cannot be netted away by outstanding close orders.
        require(actual * target >= 0 and not (actual == 0 and any(x * target < 0 for x in signed_orders)),
                'REVERSAL_REQUIRES_FLAT')
        desired = target - actual
        remaining = desired - working
        conflict = any(x * desired <= 0 for x in signed_orders) or (
            bool(signed_orders) and abs(working) > abs(desired))
        if conflict:
            # Pending reservations without an order cannot be canceled by name.
            require(bool(orders), 'PENDING_RECONCILIATION_REQUIRED')
            return t.DeltaPlan(scope=scope, target=quantity(target), actual=quantity(actual),
                working=quantity(working), remaining=quantity(Decimal(0)), classification='NONE',
                cancel_first=tuple(orders))
        if remaining:
            require(time_key(f.clock.utc_now) < time_key(intent.execution.entry_deadline), 'ENTRY_DEADLINE')
            rounded = quantity_on_grid(abs(remaining), spec.quantity_step.value, f.allow_round_down)
            require(rounded >= spec.minimum_quantity.value, 'BELOW_MINIMUM_QUANTITY')
            remaining = rounded.copy_sign(remaining)
        classification = 'NONE' if not remaining else 'REDUCE' if actual and abs(target) < abs(actual) else 'INCREASE'
        return t.DeltaPlan(scope=scope, target=quantity(target), actual=quantity(actual), working=quantity(working),
                          remaining=quantity(remaining), classification=classification, cancel_first=())


def compile_locked(tx, intent: TradeIntent, load_inputs) -> t.DeltaPlan:
    """Re-read under IP-03 collateral lock plus management-owner row lock.

    load_inputs is local-only and must include all committed command/reservation
    quantities. The caller must retain this transaction through reservation and
    command persistence. This helper neither creates nor sends a command.
    """
    from trading_system.storage.models import account_key
    require(tx.active and tx.group_id == intent.collateral_group_id, 'TRANSACTION_REQUIRED')
    owner = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
        'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR UPDATE',
        (*account_key(tx.scope), tx.scope.instrument_uid, tx.scope.venue_leg)).fetchone()
    require(owner == (intent.owner_strategy_id, intent.ownership_epoch, intent.risk_policy_revision), 'STALE_OWNER')
    book = tx.projections.book(tx.scope)
    inputs = load_inputs(tx, intent, book)
    # The wire intent has no deployment/actor fields. Reuse its persisted
    # server context to locate the same durable strategy fence as intake.
    from .ingress import IngressRevision, revision_key
    context = inputs.context
    require(context is not None and tx.scope in context.scopes
            and (context.environment, context.authorization_id, context.strategy_revision,
                 context.policy_revision, context.ownership_epoch, context.decision_run_id)
            == (intent.environment, intent.authorization_id, intent.strategy_revision,
                intent.risk_policy_revision, intent.ownership_epoch, intent.decision_run_id),
            'INTENT_CONTEXT_REQUIRED')
    fence = tx._latest('audit_events', revision_key(context), IngressRevision)
    require(fence is not None and fence.enabled is True and fence.sequence >= 1
            and (fence.identity_id, fence.deployment_id, fence.authorization_id, fence.owner_strategy_id,
                 fence.strategy_revision, fence.risk_policy_revision, fence.ownership_epoch)
            == (context.identity_id, context.deployment_id, intent.authorization_id, intent.owner_strategy_id,
                intent.strategy_revision, intent.risk_policy_revision, intent.ownership_epoch), 'STALE_REVISION')
    return TargetCompiler(inputs).compile(intent, book)
