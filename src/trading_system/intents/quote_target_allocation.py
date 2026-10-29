"""Versioned quote-band allocation through shared scope/cash/position engines.

Evidence/hash identities are not authority. The financial host must independently
resolve the current enforcement artifact and physical quantity-reservation map
inside its existing transaction before assessing or persisting admission.
"""
from dataclasses import dataclass,replace
from decimal import Decimal,localcontext,DecimalException,Inexact,Rounded
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.program_financial import require,digest as require_digest
from trading_system.contracts.order_snapshots import OrderCapabilitySnapshot
from trading_system.risk.program_exposure import FeeBound
from trading_system.risk.quote_cash_requirements import QuoteCashRequirementsV1,derive_quote_cash_requirements
from .quote_fill_interval import QuoteFillIntervalV1,compile_quote_fill_interval
from .quote_program_compilation import QuoteProgramCompilationV1,compile_quote_program
from .position_value_band import PositionValueBandAssessmentV1,assess_position_value_band
from .program_target_allocation import ProgramTargetFacts,_validate_program_scope,_evidence
from .product_rules import TrustedProductRules
from .program_compilation import RequestPlan
from .target_compiler import TargetCompiler,TargetScopeTerms,TargetScopeState


def digest(value):return canonical_hash(domain_payload(value))


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteExecutionBoundsV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_EXECUTION_BOUNDS_V1']
    scope:t.ScopeKey
    compilation_hash:str
    rules_hash:str
    profile_hash:str
    node_id:str
    enforcement_kind:Literal['PAPER_INTERNAL_ENVELOPE','VENUE_NATIVE_GUARANTEE']
    model_hash:str
    minimum_price:t.Price
    maximum_price:t.Price
    maximum_gross_quote:t.Money
    fees:tuple[FeeBound,...]|None
    evidence:t.EvidenceRef
    valid_until:str

    def __post_init__(self):
        require(self.contract_version=='2026-10-30' and self.record_kind=='QUOTE_EXECUTION_BOUNDS_V1',
            'QUOTE_EXECUTION_BOUNDS_VERSION_REQUIRED')
        for value in (self.compilation_hash,self.rules_hash,self.profile_hash,self.model_hash):require_digest(value)
        require(self.enforcement_kind in ('PAPER_INTERNAL_ENVELOPE','VENUE_NATIVE_GUARANTEE'),
            'QUOTE_EXECUTION_ENFORCEMENT_REQUIRED')
        utc_timestamp(self.valid_until)

    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteTargetFactsV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_TARGET_FACTS_V1']
    scope_facts:ProgramTargetFacts
    profile:TrustedProductRules
    capabilities:OrderCapabilitySnapshot
    expected_build_digest:str
    expected_instrument_ref:str
    quantity_reservation_ids:tuple[str,...]
    bounds:QuoteExecutionBoundsV1


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteTargetAllocationV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_TARGET_ALLOCATION_V1']
    transaction_id:str
    scope:t.ScopeKey
    compilation_hash:str
    proposal_hash:str
    program_hash:str
    book_hash:str
    facts_hash:str
    context_hash:str
    native_quote_amount:t.Money
    scope_state:TargetScopeState
    interval:QuoteFillIntervalV1
    cash:QuoteCashRequirementsV1
    position:PositionValueBandAssessmentV1
    requests:tuple[RequestPlan,...]
    blockers:tuple[str,...]
    valid_until:str

    @property
    def execution_authorized(self)->Literal[False]:return False


def compile_quote_target(plan,book,facts):
    try:
        with localcontext() as arithmetic:
            arithmetic.prec=256
            arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            return _compile_quote_target(plan,book,facts)
    except DecimalException as error:
        raise ValueError('QUOTE_TARGET_ARITHMETIC_UNPROVEN') from error


def _compile_quote_target(plan,book,facts):
    require(type(plan) is QuoteProgramCompilationV1 and type(facts) is QuoteTargetFactsV1
        and facts.contract_version=='2026-10-30' and facts.record_kind=='QUOTE_TARGET_FACTS_V1',
        'QUOTE_TARGET_TYPED_FACTS_REQUIRED')
    s=facts.scope_facts
    require(type(s) is ProgramTargetFacts and s.normalization_price is None,
        'QUOTE_TARGET_SCOPE_FACTS_REQUIRED')
    scope,now,proposal,program,codec,phash,bridge,spec,ratio,rules=_validate_program_scope(plan,book,s)
    require(type(facts.profile) is TrustedProductRules and digest(facts.profile)==plan.profile_hash
        and codec.hash('order-capability-snapshot',facts.capabilities)==plan.capabilities_hash,
        'QUOTE_TARGET_PROFILE_OR_CAPABILITIES_CHANGED')
    replay=compile_quote_program(proposal,rules,facts.capabilities,profile=facts.profile,scope=scope,now=now,
        expected_build_digest=facts.expected_build_digest,expected_rule_revision=rules.revision,
        expected_capabilities_hash=plan.capabilities_hash,expected_instrument_ref=facts.expected_instrument_ref,
        allow_round_down=s.inputs.allow_round_down)
    require(digest(replay)==phash,'QUOTE_TARGET_COMPILATION_CHANGED')
    b=facts.bounds;node=plan.nodes[0]
    require(type(b) is QuoteExecutionBoundsV1 and b.scope==scope and b.compilation_hash==phash
        and b.rules_hash==plan.rules_hash and b.profile_hash==plan.profile_hash and b.node_id==node.original.node_id,
        'QUOTE_EXECUTION_ENVELOPE_CHANGED')
    require((b.enforcement_kind=='PAPER_INTERNAL_ENVELOPE')==(facts.capabilities.transport=='PAPER'),
        'QUOTE_EXECUTION_ENVIRONMENT_UNBOUND')
    _evidence(b.evidence,scope,now)
    require(utc_timestamp(now)<utc_timestamp(b.valid_until),'QUOTE_EXECUTION_ENVELOPE_EXPIRED')
    requested=t.Money(currency=rules.quote_asset,amount=Decimal(node.quantity.value))
    require(b.maximum_gross_quote.currency==rules.quote_asset
        and b.maximum_gross_quote.amount>=requested.amount,'QUOTE_GROSS_EXECUTION_UPPER_UNDERSTATED')
    envelope=facts.profile.execution_price_envelope
    require(envelope is not None and envelope.contains(b.minimum_price.value)
        and envelope.contains(b.maximum_price.value),'QUOTE_EXECUTION_PROFILE_RANGE_UNBOUND')
    interval=compile_quote_fill_interval(maximum_gross_quote=b.maximum_gross_quote,minimum_price=b.minimum_price,
        maximum_price=b.maximum_price,quantity_step=t.Quantity(unit=bridge.ledger_unit,
            value=Decimal(facts.profile.market_quantity_step)*ratio),notional_multiplier=bridge.ledger_multiplier)
    require(type(facts.quantity_reservation_ids) is tuple
        and len(set(facts.quantity_reservation_ids))==len(facts.quantity_reservation_ids),
        'QUOTE_QUANTITY_RESERVATION_MAP_REQUIRED')
    originals={p.reservation.reservation_id:p for p in book.reservations}
    require(len(originals)==len(book.reservations) and set(facts.quantity_reservation_ids)<=set(originals),
        'QUOTE_QUANTITY_RESERVATION_MAP_CHANGED')
    quantity_book=replace(book,reservations=tuple(originals[i] for i in facts.quantity_reservation_ids))
    until=min((proposal.valid_until,program.submit_valid_until,s.valid_until,b.valid_until,
        bridge.valid_until,rules.valid_until,facts.profile.valid_until,facts.capabilities.valid_until),key=utc_timestamp)
    terms=TargetScopeTerms(scope=scope,venue=rules.venue,owner_strategy_id=s.owner.owner_strategy_id,
        ownership_epoch=s.owner.ownership_epoch,collateral_group_id=s.collateral_group_id,
        basis_fingerprint=book.basis_fingerprint,target_side=proposal.goal.side,unit=bridge.ledger_unit,
        valid_until=until,submission_deadline=program.submit_valid_until)
    state=TargetCompiler(s.inputs).read_scope_terms(terms,quantity_book)
    cash=derive_quote_cash_requirements(interval,side=node.original.order.side,base_asset=rules.base_asset,fees=b.fees)
    position=assess_position_value_band(proposal.goal,state,scope=scope,request_side=node.original.order.side,
        delta_minimum=cash.position_delta_minimum,delta_maximum=cash.position_delta_maximum)
    blockers=list(position.blockers)
    if s.unknown_command_ids:blockers.append('TARGET_COMMAND_RECONCILIATION_REQUIRED')
    return QuoteTargetAllocationV1(contract_version='2026-10-30',record_kind='QUOTE_TARGET_ALLOCATION_V1',
        transaction_id=s.transaction_id,scope=scope,compilation_hash=phash,proposal_hash=plan.proposal_hash,
        program_hash=plan.program_hash,book_hash=digest(book),facts_hash=digest(facts),context_hash=digest(s.context),
        native_quote_amount=requested,scope_state=state,interval=interval,cash=cash,position=position,
        requests=() if blockers else plan.requests,blockers=tuple(blockers),valid_until=until)
