"""Independent, default-deny review of terminal native-program funding.

The verifier is a trusted LOCAL archive/ledger reader, never a model callback.
It must independently substantiate every obligation, including balances and
ongoing protection; metadata labels alone are not evidence. Only a separately
provisioned database resolver can mint same-transaction release approvals.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal, localcontext, Inexact, Rounded

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.order_lifecycle import ParentAuthority, ProgramState, TERMINAL
from trading_system.storage.models import MissingEvidence, decode, encode, scope_id, account_key
from .reservation_repository import ReservationRepository, balance_watermark

REQUIREMENTS = frozenset({'PARENT_TERMINAL', 'COMPLETE_CHILD_IDENTITIES', 'FILLS_AND_FEES',
                         'BALANCES', 'OWNERSHIP', 'RISK_RECONCILIATION', 'PROTECTION_RESPONSIBILITY'})


@dataclass(frozen=True, kw_only=True)
class ReviewedObligation:
    requirement: str
    evidence: tuple[t.EvidenceRef, ...]


@dataclass(frozen=True, kw_only=True)
class ClosureReview:
    context_hash: str
    obligations: tuple[ReviewedObligation, ...]
    protection_disposition: str  # FLAT_RECONCILED or RESPONSIBILITY_RETAINED
    reviewed_at: str
    expires_at: str


def validate_review(review, context, now):
    if type(review) is not ClosureReview:
        raise ValueError('INDEPENDENT_STRUCTURED_REVIEW_REQUIRED')
    if review.context_hash != canonical_hash(context):
        raise ValueError('STALE_CLOSURE_REVIEW')
    reviewed, expiry, instant = map(utc_timestamp, (review.reviewed_at, review.expires_at, now))
    if not reviewed <= instant < expiry <= reviewed + timedelta(seconds=60):
        raise ValueError('STALE_CLOSURE_REVIEW')
    if len(review.obligations) != len(REQUIREMENTS) or {o.requirement for o in review.obligations} != REQUIREMENTS:
        raise ValueError('ALL_CLOSURE_OBLIGATIONS_REQUIRED')
    floor = utc_timestamp(context['state']['parent_observed_at'])
    if any(utc_timestamp(e['visible_at']) > reviewed for e in context['book']['evidence']):
        raise ValueError('FUTURE_BALANCE_EVIDENCE')
    for obligation in review.obligations:
        if not obligation.evidence:
            raise ValueError('CLOSURE_EVIDENCE_REQUIRED')
        for ref in obligation.evidence:
            if (str(ref.environment) != context['binding']['scope']['environment'] or not ref.id or not ref.source
                    or len(ref.hash) != 64 or any(c not in '0123456789abcdef' for c in ref.hash)
                    or not floor <= utc_timestamp(ref.visible_at) <= reviewed):
                raise ValueError('INVALID_CLOSURE_EVIDENCE')
    expected = 'RESPONSIBILITY_RETAINED' if Decimal(context['state']['protection_quantity']) else 'FLAT_RECONCILED'
    if review.protection_disposition != expected:
        raise ValueError('EXPLICIT_PROTECTION_RESPONSIBILITY_REQUIRED')


def closure_context(tx, program_id, remaining_holds):
    """Reload exact ledger facts under its existing collateral-group lock."""
    with localcontext() as arithmetic:
        arithmetic.prec = 256
        arithmetic.traps[Inexact] = arithmetic.traps[Rounded] = True
        return _closure_context(tx, program_id, remaining_holds)


def _closure_context(tx, program_id, remaining_holds):
    if tx.scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH):
        raise MissingEvidence('ENVIRONMENT_DISABLED')
    binding = tx._latest('order_program_bindings', program_id, ParentAuthority)
    state = tx._latest('order_program_states', program_id, ProgramState)
    if binding is None or state is None or binding.scope != tx.scope or state.program_id != program_id:
        raise MissingEvidence('OWNED_PARENT_BINDING_REQUIRED')
    if (state.status not in ('STOPPED', 'COMPLETED') or Decimal(state.unallocated_quantity) != 0
            or not state.parent_observed_at or any(c.status not in TERMINAL for c in state.children)):
        raise MissingEvidence('PARENT_AND_ALL_CHILDREN_TERMINAL_REQUIRED')
    owner = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
        'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR SHARE',
        (*account_key(tx.scope), tx.scope.instrument_uid, tx.scope.venue_leg)).fetchone()
    if owner is None or owner[:2] != (binding.owner_strategy_id, binding.ownership_epoch):
        raise MissingEvidence('CURRENT_OWNER_REQUIRED')
    if tx.risk_locks.applicable(tx.scope):
        raise MissingEvidence('ACTIVE_RISK_REQUIRES_SEPARATE_RESOLUTION')
    book = tx.projections.book(tx.scope)
    coverage = book.coverage
    if (coverage.scope != tx.scope or not coverage.query_succeeded or not coverage.pagination_complete
            or not coverage.supported or coverage.missing or not book.evidence
            or any(e.environment != tx.scope.environment or utc_timestamp(e.visible_at) < utc_timestamp(state.parent_observed_at)
                   for e in book.evidence)):
        raise MissingEvidence('COMPLETE_POST_TERMINAL_BALANCES_REQUIRED')
    # The trusted review additionally proves completeness of venue child/fill
    # enumeration; the local checks cannot invent events which were never seen.
    ledger_fills = []
    native_ids = {binding.parent_venue_id, *(c.child_order_id for c in state.children)}
    if any(o.order.identity.venue_order_id in native_ids and
           (o.lifecycle_state not in TERMINAL or o.pending_operation != 'NONE'
            or o.command_certainty != 'CONFIRMED' or o.leaves_quantity.value != 0)
           for o in book.active_orders):
        raise MissingEvidence('BOOK_HAS_UNSETTLED_PROGRAM_ORDERS')
    for child in state.children:
        key = canonical_hash(dict(venue=binding.venue, namespace='ORDINARY', child_id=child.child_order_id))
        link = tx.execute('SELECT payload FROM ledger.order_child_links WHERE scope_id=%s AND record_id=%s',
                          (scope_id(tx.scope), key)).fetchone()
        if link is None or link[0]['program_id'] != program_id or link[0]['parent_venue_id'] != binding.parent_venue_id:
            raise MissingEvidence('OWNED_CHILD_LINK_REQUIRED')
        if sum((Decimal(f.quantity) for f in child.fills), Decimal(0)) != Decimal(child.cumulative_quantity):
            raise MissingEvidence('COMPLETE_FILL_DETAILS_REQUIRED')
        for observed in child.fills:
            rows = tx.execute('SELECT payload FROM ledger.fills WHERE scope_id=%s AND native_trade_id=%s',
                              (scope_id(tx.scope), observed.native_trade_id)).fetchall()
            if len(rows) != 1:
                raise MissingEvidence('LEDGER_FILL_RECONCILIATION_REQUIRED')
            fill = decode(t.Fill, rows[0][0])
            if (fill.correction_of is not None or fill.scope != tx.scope or fill.venue != binding.venue
                    or fill.order.venue_order_id != child.child_order_id or fill.order.scope != tx.scope
                    or fill.quantity.value != Decimal(observed.quantity) or fill.price.value != Decimal(observed.price)
                    or fill.price.currency != binding.quote_asset
                    or sorted((f.currency, f.amount) for f in fill.fees) != sorted((f.asset, Decimal(f.amount)) for f in observed.fees)):
                raise MissingEvidence('LEDGER_FILL_RECONCILIATION_REQUIRED')
            if tx.execute('SELECT 1 FROM ledger.fills WHERE correction_of=%s', (fill.fill_id,)).fetchone():
                raise MissingEvidence('CORRECTED_FILL_REVIEW_REQUIRED')
            ledger_fills.append(domain_payload(fill))
    if sum((p.actual_quantity.value for p in book.positions), Decimal(0)).copy_abs() != Decimal(state.protection_quantity):
        raise MissingEvidence('POSITION_RECONCILIATION_REQUIRED')
    plans = []
    for position in book.positions:
        if (position.scope != tx.scope or position.owner_strategy_id != binding.owner_strategy_id
                or position.ownership_epoch != binding.ownership_epoch or position.risk_locks):
            raise MissingEvidence('OWNED_POSITION_RECONCILIATION_REQUIRED')
        if not position.actual_quantity.value:
            continue
        plan = tx._latest('protection_plans', position.active_exit_plan_id, t.ProtectionPlan) if position.active_exit_plan_id else None
        if (plan is None or plan.scope != tx.scope or plan.coverage_quantity.unit != position.actual_quantity.unit
                or plan.coverage_quantity.value < abs(position.actual_quantity.value)
                or plan.state != {'NATIVE':t.ProtectionState.VERIFIED_NATIVE,
                                  'HOSTED':t.ProtectionState.VERIFIED_HOSTED}.get(plan.mode)
                or not plan.last_verified_at or utc_timestamp(plan.last_verified_at) < utc_timestamp(state.parent_observed_at)
                or plan.coverage_gap_since is not None):
            raise MissingEvidence('RETAINED_PROTECTION_REQUIRED')
        if plan.mode == 'NATIVE' and (not plan.native_order_ids or any(
                o.identity.scope != tx.scope or o.owner_strategy_id != binding.owner_strategy_id
                or o.ownership_epoch != binding.ownership_epoch for o in plan.native_order_ids)):
            raise MissingEvidence('OWNED_PROTECTION_REQUIRED')
        if plan.mode == 'HOSTED' and (tx.scope.product not in (t.Product.SPOT, t.Product.US_EQUITY) or not plan.monitor_id):
            raise MissingEvidence('RETAINED_PROTECTION_REQUIRED')
        plans.append(domain_payload(plan))
    if set(remaining_holds) != set(binding.reservation_ids):
        raise MissingEvidence('ALL_SHARED_RESERVATIONS_REQUIRED')
    targets = []
    for reservation_id in binding.reservation_ids:
        link = tx.execute('SELECT payload FROM ledger.order_funding_links WHERE scope_id=%s AND record_id=%s',
                          (scope_id(tx.scope), reservation_id)).fetchone()
        current = ReservationRepository(tx).get(reservation_id)
        r = current.reservation
        if (link is None or link[0]['program_id'] != program_id or r.intent_id != binding.funding_intent_id
                or r.owner_strategy_id != binding.owner_strategy_id or r.collateral_group_id != tx.group_id):
            raise MissingEvidence('SHARED_FUNDING_IDENTITY_REQUIRED')
        target = remaining_holds[reservation_id]
        if (type(target) is not Decimal or not target.is_finite() or not 0 <= target <= current.held
                or current.held > r.amount or not 0 <= current.reflected <= current.held
                or not 0 <= current.release_pending <= current.held):
            raise ValueError('INVALID_REVIEWED_REDUCTION')
        # No balance credit is invented. The reviewed book and archive prove the
        # venue no longer freezes these funds; remaining protection is separate.
        after = current if target == current.held else replace(current, sequence=current.sequence + 1, held=target, reflected=Decimal(0),
            release_pending=Decimal(0), reservation=replace(r, reflected_amount=Decimal(0),
                state=t.ReservationState.RELEASED if target == 0 else t.ReservationState.HELD,
                evidence_watermark=balance_watermark(book,book.evidence[0])))
        targets.append({'reservation_id': reservation_id, 'before': domain_payload(current), 'after': domain_payload(after)})
    if not any(target['before'] != target['after'] for target in targets):
        raise ValueError('NO_REVIEWED_REDUCTION')
    return {'program_id': program_id, 'binding': domain_payload(binding), 'state': domain_payload(state),
            'owner': list(owner), 'book': domain_payload(book), 'fills': ledger_fills, 'protection_plans': plans,
            'targets': targets}


class OrderProgramClosureService:
    def __init__(self, *, resolver_uow=None, verify=None, clock=None):
        self.resolver_uow, self.verify = resolver_uow, verify
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def close(self, scope, program_id, remaining_holds):
        if self.resolver_uow is None or self.verify is None:
            raise MissingEvidence('INDEPENDENT_CLOSURE_RESOLVER_REQUIRED')
        with self.resolver_uow.transaction(scope) as tx:
            context = closure_context(tx, program_id, remaining_holds)
            review = self.verify(tx, deepcopy(context))
            validate_review(review, context, self.clock())
            if closure_context(tx, program_id, remaining_holds) != context:
                raise MissingEvidence('CLOSURE_CONTEXT_CHANGED_DURING_REVIEW')
            document = {**context, 'review': domain_payload(review)}
            from trading_system.contracts.canonical import canonical_json
            tx.execute('SELECT ledger.approve_order_program_closure(%s,%s,%s::jsonb,%s)',
                       (scope_id(scope), program_id, canonical_json(document), tx.transaction_id))
            released = []
            for target in context['targets']:
                projection = decode(t.ReservationProjection, target['after'])
                if target['before'] != target['after']:
                    ReservationRepository(tx).save(projection)
                released.append(projection)
            return tuple(released)
