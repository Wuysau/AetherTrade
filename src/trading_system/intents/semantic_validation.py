"""Server-side proposal semantics. None of these checks grants a send permit."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, localcontext

from trading_system.accounts.authorization import AuthorizationBinding, AuthorizationService
from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.wire import (
    ABSENT, ContractCodec, DecisionProposal, TradeIntent, validate_transition,
)
from trading_system.market.snapshots import snapshot_content_hash


class ProposalRejected(ValueError):
    """Stable local reason code; not a venue rejection."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ProposalRejected(reason)


def time_key(value: str):
    utc_timestamp(value)
    seconds, _, fraction = value.removesuffix('Z').removesuffix('+00:00').partition('.')
    return seconds, Decimal('0.' + (fraction or '0'))


def complete(coverage: t.Coverage, scope: t.ScopeKey) -> bool:
    return (coverage.scope == scope and coverage.query_succeeded is True
            and coverage.pagination_complete is True and coverage.supported is True
            and not coverage.missing)


def quantity_on_grid(value: Decimal, step: Decimal, allow_round_down: bool) -> Decimal:
    require(value.is_finite() and value >= 0 and step.is_finite() and step > 0,
            'INVALID_QUANTITY_RULE')
    with localcontext() as ctx:
        ctx.prec = max(80, len(value.as_tuple().digits) + abs(value.as_tuple().exponent)
                       + len(step.as_tuple().digits) + abs(step.as_tuple().exponent) + 4)
        rounded = (value / step).to_integral_value(rounding=ROUND_DOWN) * step
        require(rounded == value or allow_round_down, 'QUANTITY_OFF_GRID')
        require(value == 0 or rounded > 0, 'QUANTITY_BELOW_STEP')
        return rounded


def quantity_from_notional(value: Decimal, price: Decimal, step: Decimal,
                           allow_round_down: bool) -> Decimal:
    """Divide and floor on the grid with integer ratios, without rounding up."""
    require(value >= 0 and price > 0 and step > 0, 'INVALID_QUANTITY_RULE')
    vn, vd = value.as_integer_ratio()
    pn, pd = price.as_integer_ratio()
    sn, sd = step.as_integer_ratio()
    count, remainder = divmod(vn * pd * sd, vd * pn * sn)
    require(not remainder or allow_round_down, 'QUANTITY_OFF_GRID')
    require(value == 0 or count > 0, 'QUANTITY_BELOW_STEP')
    with localcontext() as ctx:
        ctx.prec = max(80, len(str(count)) + len(step.as_tuple().digits) + 2)
        return Decimal(count) * step


@dataclass(frozen=True, kw_only=True)
class BindingInputs:
    """Trusted deployment inputs loaded under the ledger transaction.

    Evidence is the allowlist delivered to this decision run, not a global list
    of references. The loader must resolve these from this deployment's store.
    No field in this record is accepted from the model's proposal.
    """
    authorization: AuthorizationBinding
    instrument_ref: str
    instrument: t.InstrumentSpec
    collateral_group_id: str
    snapshot: t.SnapshotPacket
    freshness: t.DataVerdict
    evidence: tuple[t.EvidenceRef, ...]
    clock: t.ClockReading
    deadline_monotonic_ns: int
    maximum_valid_until: str
    max_planned_loss: t.Money
    protection_policy: str
    allowed_stop_triggers: tuple[str, ...]
    allow_round_down: bool = False
    previous_proposal: DecisionProposal | None = None
    previous_intent: TradeIntent | None = None


def validate_proposal(proposal: DecisionProposal, context: t.DecisionContext,
                      book: t.BookSnapshot, facts: BindingInputs) -> Decimal:
    codec = ContractCodec()
    codec.encode(proposal)
    now = facts.clock
    require(now.clock_health == 'HEALTHY' and type(now.monotonic_ns) is int
            and type(facts.deadline_monotonic_ns) is int
            and 0 <= now.monotonic_ns < facts.deadline_monotonic_ns, 'DECISION_DEADLINE')
    require(context.environment != t.Environment.LIVE, 'LIVE_DISABLED')
    authorization = AuthorizationService(lambda _: facts.authorization,
        clock=lambda: utc_timestamp(now.utc_now)).validate(context)
    require(authorization.allowed and not authorization.rejection_codes, 'AUTHORIZATION_REQUIRED')
    grant, spec, snapshot = facts.authorization.authorization, facts.instrument, facts.snapshot
    require(book.scope in context.scopes and book.scope in grant.scopes, 'SCOPE_NOT_AUTHORIZED')
    require(proposal.instrument_ref == facts.instrument_ref
            and spec.instrument_uid == book.scope.instrument_uid
            and spec.product == book.scope.product and spec.venue == 'BINANCE', 'INSTRUMENT_BINDING')
    require(spec.quantity_step.value > 0 and spec.minimum_quantity.value > 0
            and spec.minimum_quantity.unit == spec.quantity_step.unit
            and spec.tick_size.currency == spec.quote_currency and spec.rule_revision > 0,
            'INVALID_INSTRUMENT_RULE')
    require(complete(book.coverage, book.scope) and bool(book.evidence), 'BOOK_INCOMPLETE')
    require(snapshot.ref == context.snapshot_ref and snapshot.scope == book.scope
            and snapshot_content_hash(snapshot) == snapshot.ref.hash
            and complete(snapshot.coverage, book.scope), 'SNAPSHOT_BINDING')
    require(facts.freshness.usable is True and not facts.freshness.missing
            and not facts.freshness.rejection_codes, 'SNAPSHOT_UNUSABLE')
    as_of = snapshot.quote.occurred_at
    require(time_key(as_of) <= time_key(snapshot.quote.received_at) <= time_key(now.utc_now)
            and time_key(as_of) <= time_key(snapshot.published_at)
            <= time_key(snapshot.revision_visible_at) <= time_key(snapshot.ref.visible_at)
            <= time_key(now.utc_now), 'FUTURE_EVIDENCE')
    refs = {e.id: e for e in facts.evidence}
    require(len(refs) == len(facts.evidence)
            and len(set(proposal.evidence_refs)) == len(proposal.evidence_refs)
            and snapshot.ref.id in proposal.evidence_refs
            and refs.get(snapshot.ref.id) == snapshot.ref, 'EVIDENCE_BINDING')
    for identifier in proposal.evidence_refs:
        ref = refs.get(identifier)
        require(ref is not None and ref.environment == context.environment
                and bool(ref.source) and len(ref.hash) == 64
                and all(c in '0123456789abcdef' for c in ref.hash)
                and time_key(ref.visible_at) <= time_key(snapshot.ref.visible_at), 'EVIDENCE_NOT_VISIBLE')
    require(time_key(now.utc_now) < time_key(proposal.valid_until)
            <= time_key(facts.maximum_valid_until)
            and time_key(proposal.valid_until) <= time_key(grant.expires_at), 'PROPOSAL_EXPIRED_OR_TOO_LONG')
    require(time_key(now.utc_now) < time_key(proposal.entry.entry_deadline)
            <= time_key(proposal.valid_until), 'ENTRY_DEADLINE')
    require(time_key(now.utc_now) < time_key(proposal.exit_plan.review_due_at), 'REVIEW_DEADLINE')
    leverage = 1 if proposal.requested_leverage is ABSENT else proposal.requested_leverage
    require(spec.product == t.Product.USD_M_PERP or leverage == 1, 'CASH_LEVERAGE')
    require(spec.product == t.Product.USD_M_PERP or proposal.target.side != 'SHORT', 'CASH_SHORT')
    require(facts.protection_policy in grant.protection_options
            and (spec.product != t.Product.USD_M_PERP or facts.protection_policy == 'NATIVE_REQUIRED'),
            'PROTECTION_NOT_AUTHORIZED')
    require(proposal.exit_plan.stop_trigger in facts.allowed_stop_triggers, 'STOP_TRIGGER_UNVERIFIED')
    require(facts.max_planned_loss.amount > 0 and bool(facts.collateral_group_id)
            and facts.max_planned_loss.currency == spec.settlement_currency, 'BINDING_POLICY_MISSING')
    if proposal.action in ('HOLD', 'UPDATE_EXIT'):
        require(facts.previous_proposal is not None and facts.previous_intent is not None,
                'PREVIOUS_RESPONSIBILITY_REQUIRED')
        require(codec.hash(facts.previous_proposal) == facts.previous_intent.proposal_hash,
                'PREVIOUS_PROPOSAL_MISMATCH')
        try:
            validate_transition(facts.previous_proposal, proposal)
        except ValueError as exc:
            raise ProposalRejected('RESPONSIBILITY_CHANGED') from exc
        # Exit updates must not silently replace execution or leverage authority.
        require(proposal.entry == facts.previous_proposal.entry
                and proposal.requested_leverage == facts.previous_proposal.requested_leverage,
                'UPDATE_EXIT_EXECUTION_CHANGED')
    value = (Decimal(facts.previous_intent.target_quantity) if proposal.action != 'SET_TARGET'
             else Decimal(proposal.target.value))
    if proposal.action == 'SET_TARGET' and proposal.target.sizing == 'QUOTE_NOTIONAL' and value:
        price = snapshot.quote.ask if proposal.target.side == 'LONG' else snapshot.quote.bid
        require(price is not None and price.currency == spec.quote_currency, 'CONVERSION_PRICE_REQUIRED')
        # Perpetual multiplier is absent from this contract; never assume one.
        require(spec.product != t.Product.USD_M_PERP, 'CONTRACT_MULTIPLIER_REQUIRED')
        value = quantity_from_notional(value, price.value, spec.quantity_step.value,
                                       facts.allow_round_down)
    quantity = (value if proposal.action != 'SET_TARGET' else
                quantity_on_grid(value, spec.quantity_step.value, facts.allow_round_down))
    require(quantity == 0 or quantity >= spec.minimum_quantity.value, 'BELOW_MINIMUM_QUANTITY')
    if proposal.entry.limit_price is not None:
        price = Decimal(proposal.entry.limit_price)
        require(on_grid(price, spec.tick_size.value), 'PRICE_OFF_TICK')
    stop = proposal.exit_plan.stop_price
    if proposal.target.side != 'FLAT':
        reference = proposal.entry.limit_price
        quote = snapshot.quote.ask if proposal.target.side == 'LONG' else snapshot.quote.bid
        require(reference is not None or quote is not None, 'REFERENCE_PRICE_REQUIRED')
        mark = Decimal(reference) if reference is not None else quote.value
        require(stop is not None, 'STOP_REQUIRED')
        require(on_grid(Decimal(stop), spec.tick_size.value), 'STOP_OFF_TICK')
        require(Decimal(stop) < mark if proposal.target.side == 'LONG' else Decimal(stop) > mark,
                'STOP_DIRECTION')
    return quantity


def on_grid(value: Decimal, step: Decimal) -> bool:
    """Exact divisibility independent of the caller's Decimal context."""
    numerator, denominator = value.as_integer_ratio()
    step_numerator, step_denominator = step.as_integer_ratio()
    return step_numerator > 0 and (numerator * step_denominator) % (denominator * step_numerator) == 0
