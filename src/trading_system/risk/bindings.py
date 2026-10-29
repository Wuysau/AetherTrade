"""Shared ordered source/basis checks; metadata projections grant no authority."""
from dataclasses import dataclass
from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from .valuation import nonnegative


@dataclass(frozen=True,slots=True,kw_only=True)
class RiskBinding:
    decision_id:str
    scope:t.ScopeKey
    authorization_id:str
    owner_strategy_id:str
    ownership_epoch:int
    policy_revision:int
    collateral_group_id:str
    basis_fingerprint:str
    snapshot_id:str
    action:str
    quantity_unit:str
    valid_until:str
    submission_deadline:str
    created_at:str
    as_of:str


def legacy_binding(intent):
    # Raw diagnostic metadata only. RiskEvaluator separately requires the exact
    # unchanged legacy codec before any ALLOW; no intent is fabricated here.
    return RiskBinding(decision_id=intent.intent_id,scope=t.ScopeKey(environment=intent.environment,
        venue_account_id=intent.venue_account_id,product=intent.product,instrument_uid=intent.instrument_uid,venue_leg=intent.venue_leg),
        authorization_id=intent.authorization_id,owner_strategy_id=intent.owner_strategy_id,
        ownership_epoch=intent.ownership_epoch,policy_revision=intent.risk_policy_revision,
        collateral_group_id=intent.collateral_group_id,basis_fingerprint=intent.basis_fingerprint,
        snapshot_id=intent.snapshot_id,action=intent.action,quantity_unit=intent.quantity_unit,
        valid_until=intent.valid_until,submission_deadline=intent.execution.entry_deadline,
        created_at=intent.created_at,as_of=intent.as_of)


def terms_binding(terms):
    return RiskBinding(decision_id=terms.decision_id,scope=terms.scope,authorization_id=terms.authorization_id,
        owner_strategy_id=terms.owner_strategy_id,ownership_epoch=terms.ownership_epoch,policy_revision=terms.policy_revision,
        collateral_group_id=terms.collateral_group_id,basis_fingerprint=terms.basis_fingerprint,snapshot_id=terms.snapshot_id,
        action=terms.action,quantity_unit=terms.target.unit,valid_until=terms.valid_until,
        submission_deadline=terms.submission_deadline,created_at=terms.created_at,as_of=terms.as_of)


def initial_binding_codes(binding,facts,book,market,policy):
    """Generator preserves legacy diagnostic order even if later input raises."""
    now=utc_timestamp(facts.clock.utc_now);scope=binding.scope
    if scope.environment=='LIVE' or policy.environment=='LIVE':yield 'LIVE_DISABLED'
    if (facts.scope!=book.scope or facts.scope!=market.scope or facts.scope!=scope
        or policy.environment!=scope.environment):yield 'SCOPE_MISMATCH'
    if (facts.intent_id!=binding.decision_id or facts.authorization_id!=binding.authorization_id
        or facts.owner_strategy_id!=binding.owner_strategy_id or facts.ownership_epoch!=binding.ownership_epoch
        or facts.policy_revision!=binding.policy_revision or facts.policy_revision!=policy.revision
        or facts.collateral_group_id!=binding.collateral_group_id or facts.collateral_group_id!=policy.collateral_group_id):
        yield 'AUTHORITY_MISMATCH'
    if (facts.basis_fingerprint!=book.basis_fingerprint or binding.basis_fingerprint!=book.basis_fingerprint
        or facts.book_sequence!=book.sequence or facts.snapshot_ref!=market.ref or binding.snapshot_id!=market.ref.id):yield 'STALE_BASIS'
    if (now>=utc_timestamp(binding.valid_until) or now>=utc_timestamp(facts.expires_at)
        or now>=utc_timestamp(binding.submission_deadline) or utc_timestamp(binding.created_at)>now
        or utc_timestamp(binding.as_of)>utc_timestamp(binding.created_at)):yield 'EXPIRED_OR_FUTURE_INTENT'
    if (not facts.evidence or any(not e.id or not e.hash or e.environment!=facts.scope.environment
        or utc_timestamp(e.visible_at)>now for e in facts.evidence)):yield 'RISK_EVIDENCE_MISSING'
    if not (book.coverage.query_succeeded and book.coverage.pagination_complete and book.coverage.supported
        and not book.coverage.missing and book.coverage.scope==book.scope):yield 'BOOK_INCOMPLETE'
    if binding.action!='SET_TARGET':yield 'ACTION_REQUIRES_INGRESS_VALIDATION'
    spec=facts.instrument
    if (spec.instrument_uid!=scope.instrument_uid or spec.product!=scope.product
        or spec.quantity_step.unit!=binding.quantity_unit or spec.minimum_quantity.unit!=binding.quantity_unit
        or spec.tick_size.currency!=spec.quote_currency or spec.minimum_notional.currency!=spec.quote_currency
        or spec.settlement_currency!=spec.quote_currency or nonnegative(facts.multiplier)==0):yield 'INSTRUMENT_RULES_UNSUPPORTED'
