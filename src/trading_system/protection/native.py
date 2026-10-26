"""Offline native-protection evidence checks, never a venue client.

OrderProjection has no side/trigger fields. A trusted archive reader must supply
the additional typed proof below, bound to the entire report and plan. These
local types do not change the approved wire contracts or qualify an adapter.
"""
from dataclasses import dataclass
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.recovery.reconciler import utc_now, valid_ref


def digest(value) -> str:
    return canonical_hash(domain_payload(value))


def offline(scope: t.ScopeKey) -> None:
    if scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH):
        raise ValueError('ENVIRONMENT_NOT_ENABLED')


def closing_side(position: t.ManagedPosition) -> str:
    value = position.actual_quantity.value
    if value == 0:
        raise ValueError('NO_EXPOSURE')
    if position.scope.product != t.Product.USD_M_PERP and (
            value < 0 or position.scope.venue_leg != 'BOTH'):
        raise ValueError('CASH_POSITION_NOT_SELLABLE')
    if position.scope.venue_leg == 'LONG' and value < 0:
        raise ValueError('POSITION_DIRECTION_MISMATCH')
    if position.scope.venue_leg == 'SHORT':
        return 'BUY'
    return 'SELL' if value > 0 else 'BUY'


def position_reported(report: t.VenueReport, position: t.ManagedPosition) -> bool:
    matches = [p for p in report.positions if p.position_id == position.position_id]
    return (len(matches) == 1 and matches[0].scope == position.scope
            and matches[0].actual_quantity == position.actual_quantity
            and matches[0].owner_strategy_id == position.owner_strategy_id
            and matches[0].owner_book_id == position.owner_book_id
            and matches[0].ownership_epoch == position.ownership_epoch)


def owned(order: t.OwnedOrderRef, position: t.ManagedPosition) -> bool:
    return (order.identity.scope == position.scope and bool(position.owner_strategy_id)
            and order.owner_strategy_id == position.owner_strategy_id
            and order.ownership_epoch == position.ownership_epoch)


def same_order(left: t.OwnedOrderRef, right: t.OwnedOrderRef) -> bool:
    # The venue order ID can be learned after submission; every preassigned ID
    # must still match. A known venue ID may never silently change.
    a, b = left.identity, right.identity
    return (left.owner_strategy_id == right.owner_strategy_id
            and left.ownership_epoch == right.ownership_epoch and a.scope == b.scope
            and a.logical_client_order_id == b.logical_client_order_id
            and a.native_client_order_id == b.native_client_order_id
            and a.venue_client_order_id == b.venue_client_order_id
            and (a.venue_order_id is None or a.venue_order_id == b.venue_order_id))


def settled(order: t.OrderProjection) -> bool:
    return (order.lifecycle_state in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'}
            and order.command_certainty == 'CONFIRMED' and order.pending_operation == 'NONE'
            and order.outcome_provenance in {'VENUE_EVENT', 'VENUE_QUERY'}
            and order.leaves_quantity.value == 0)


def report_errors(report: t.VenueReport, scope: t.ScopeKey, now: str, age: int) -> tuple[str, ...]:
    if not isinstance(report, t.VenueReport):
        return ('REPORT_MISSING',)
    c = report.coverage
    if (c.scope != scope or c.query_succeeded is not True or c.pagination_complete is not True
            or c.supported is not True or c.missing or report.next_cursor is not None):
        return ('COVERAGE_INCOMPLETE',)
    try:
        fresh = 0 <= (utc_timestamp(now) - utc_timestamp(report.as_of)).total_seconds() <= age
    except (ValueError, TypeError):
        fresh = False
    if not fresh or not report.evidence or any(not valid_ref(ref, scope, now, age) for ref in report.evidence):
        return ('REPORT_STALE_OR_INVALID',)
    if (any(o.order.identity.scope != scope for o in report.orders)
            or any(p.scope != scope for p in report.positions)
            or any(f.scope != scope or f.order.scope != scope for f in report.fills)):
        return ('REPORT_SCOPE_MISMATCH',)
    ids = [o.order.identity.logical_client_order_id for o in report.orders]
    if len(ids) != len(set(ids)):
        return ('DUPLICATE_ORDER_EVIDENCE',)
    return ()


@dataclass(frozen=True, kw_only=True)
class NativeCapability:
    scope: t.ScopeKey
    status: t.CapabilityStatus
    minimum_quantity: t.Quantity
    quantity_step: t.Quantity
    order_types: tuple[str, ...]
    triggers: tuple[str, ...]
    atomic_amend: bool
    coexist: bool
    oco: bool
    valid_until: str
    evidence: t.EvidenceRef

    def errors(self, position, now, age=60):
        try:
            valid = (self.scope == position.scope and self.status == t.CapabilityStatus.VERIFIED
                     and utc_timestamp(now) < utc_timestamp(self.valid_until)
                     and valid_ref(self.evidence, self.scope, now, age)
                     and self.minimum_quantity.unit == position.actual_quantity.unit
                     and self.quantity_step.unit == position.actual_quantity.unit
                     and self.minimum_quantity.value > 0 and self.quantity_step.value > 0)
        except (ValueError, TypeError):
            valid = False
        return () if valid else ('CAPABILITY_UNVERIFIED',)

    def tradable(self, quantity: t.Quantity) -> bool:
        return (quantity.unit == self.minimum_quantity.unit == self.quantity_step.unit
                and self.quantity_step.value > 0 and quantity.value >= self.minimum_quantity.value
                and quantity.value % self.quantity_step.value == 0)


@dataclass(frozen=True, kw_only=True)
class NativeOrderProof:
    order: t.OwnedOrderRef
    side: str
    order_type: str
    stop_price: t.Price
    trigger: str
    reduce_only: bool
    full_position_exit: bool
    evidence: t.EvidenceRef


@dataclass(frozen=True, kw_only=True)
class NativeProof:
    plan_hash: str
    report_hash: str
    position: t.ManagedPosition
    positions_verified: bool
    balances_and_allocations_verified: bool
    orders: tuple[NativeOrderProof, ...]


class NativeVerifier:
    def __init__(self, *, read_proof=None, clock=utc_now, maximum_age_seconds=60):
        if type(maximum_age_seconds) is not int or maximum_age_seconds <= 0:
            raise ValueError('positive freshness bound required')
        self.read_proof, self.clock, self.maximum_age_seconds = read_proof, clock, maximum_age_seconds

    def verify(self, plan: t.ProtectionPlan, report: t.VenueReport,
               position: t.ManagedPosition) -> t.ProtectionVerdict:
        zero = t.Quantity(value=Decimal(0), unit=position.actual_quantity.unit)

        def gap(*missing):
            return t.ProtectionVerdict(state=t.ProtectionState.GAP, covered_quantity=zero,
                                       missing=tuple(missing), evidence=())

        offline(position.scope)
        if (plan.scope != position.scope or plan.mode != 'NATIVE' or plan.monitor_id is not None
                or position.active_exit_plan_id not in (None, plan.plan_id)
                or plan.coverage_quantity.unit != zero.unit
                or plan.coverage_quantity.value != abs(position.actual_quantity.value)
                or plan.coverage_quantity.value <= 0):
            return gap('PLAN_OR_EXPOSURE_MISMATCH')
        now = self.clock()
        errors = report_errors(report, plan.scope, now, self.maximum_age_seconds)
        if errors:
            return gap(*errors)
        if not position_reported(report, position):
            return gap('CURRENT_POSITION_UNVERIFIED')
        if plan.coverage_gap_since and utc_timestamp(report.as_of) < utc_timestamp(plan.coverage_gap_since):
            return gap('REPORT_PREDATES_GAP')
        try:
            proof = self.read_proof(plan, report) if self.read_proof else None
        except Exception:
            return gap('NATIVE_PROOF_UNAVAILABLE')
        if (not isinstance(proof, NativeProof) or proof.plan_hash != digest(plan)
                or proof.report_hash != digest(report) or proof.position != position
                or proof.positions_verified is not True
                or (plan.scope.product != t.Product.USD_M_PERP
                    and proof.balances_and_allocations_verified is not True)):
            return gap('NATIVE_PROOF_MISSING_OR_MISMATCH')
        # One explicitly quantity-bounded stop. Combined/OCO writes require an
        # independently reviewed compiler; never sum two full-size sell legs.
        if len(plan.native_order_ids) != 1 or len(proof.orders) != 1:
            return gap('SINGLE_STOP_OR_VERIFIED_COMBINATION_REQUIRED')
        ref, detail = plan.native_order_ids[0], proof.orders[0]
        matches = [o for o in report.orders if same_order(ref, o.order)]
        if len(matches) != 1 or not owned(ref, position) or not same_order(ref, detail.order):
            return gap('ORIGINAL_ORDER_OR_OWNER_MISMATCH')
        order = matches[0]
        if (order.lifecycle_state not in {'ACCEPTED', 'PARTIALLY_FILLED'}
                or not order.order.identity.venue_order_id
                or order.command_certainty != 'CONFIRMED' or order.pending_operation != 'NONE'
                or order.outcome_provenance not in {'VENUE_QUERY', 'VENUE_EVENT'}
                or order.leaves_quantity != plan.coverage_quantity):
            return gap('STOP_NOT_ACTIVE_OR_QUANTITY_MISMATCH')
        try:
            side = closing_side(position)
        except ValueError:
            return gap('POSITION_DIRECTION_MISMATCH')
        if (detail.evidence not in report.evidence or detail.side != side
                or detail.order_type != 'STOP_MARKET' or detail.stop_price != plan.stop_price
                or detail.trigger != plan.stop_trigger or detail.full_position_exit is not False
                or (position.scope.product == t.Product.USD_M_PERP and detail.reduce_only is not True)):
            return gap('STOP_PARAMETERS_UNVERIFIED')
        return t.ProtectionVerdict(state=t.ProtectionState.VERIFIED_NATIVE,
            covered_quantity=plan.coverage_quantity, missing=(), evidence=report.evidence)
