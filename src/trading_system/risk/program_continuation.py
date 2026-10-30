"""Versioned continuing exposure facts, separate from post-full-fill metrics.

The record grants no action or credit. Source-bound stop estimates are not hard
loss guarantees; only actual reconciled positions can establish observed FLAT.
"""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.program_financial import require
from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
from trading_system.intents.quote_assessment_archive import QuoteAssessmentArchiveV1
from .program_assessment import ProtectionCoverage
from .program_assessment import _valid_refs
from trading_system.intents.semantic_validation import on_grid
from .policy_checks import exact_risk_arithmetic
from .valuation import Valuation
from trading_system.storage.models import atomic_write

CONTINUATION_PREFIX='program-continuation-risk:'

D=Decimal
def digest(value):return canonical_hash(domain_payload(value))


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramContinuationRiskV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_CONTINUATION_RISK_V1']
    scope:t.ScopeKey
    intent_id:str
    owner_strategy_id:str
    ownership_epoch:int
    archive_hash:str
    book_hash:str
    book_sequence:int
    actual_quantity:t.Quantity
    no_fill_minimum_quantity:t.Quantity
    no_fill_maximum_quantity:t.Quantity
    minimum_possible_quantity:t.Quantity
    maximum_possible_quantity:t.Quantity
    executable_current_quantity:t.Quantity
    current_lot_residual:t.Quantity
    quantity_envelope_status:Literal['EXPLICIT_QUOTE_ENVELOPE','GROSS_FILL_ENVELOPE','POSITION_FEE_EFFECTS_UNRESOLVED']
    fee_liabilities:tuple[t.Money,...]|None
    funding_liabilities:tuple[t.Money,...]|None
    goal_kind:Literal['SCALAR_TARGET','ABSOLUTE_POSITION_VALUE_BAND']
    protection_state:Literal['NO_CURRENT_EXPOSURE','BOUND_TO_ASSESSED_SOURCE','UNKNOWN']
    no_fill_stop_loss_estimate:t.Money|None
    hard_loss_status:Literal['UNKNOWN','NO_CURRENT_EXPOSURE']
    hard_loss_bound:t.Money|None
    required_duties:tuple[str,...]
    evidence:tuple[t.EvidenceRef,...]
    as_of:str
    review_due_at:str

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


def derive_position_lot_residual(quantity,step):
    require(type(quantity) is t.Quantity and type(step) is t.Quantity and quantity.unit==step.unit
        and quantity.value.is_finite() and step.value.is_finite() and step.value>0,'CONTINUATION_LOT_UNITS_REQUIRED')
    units=abs(Fraction(quantity.value))//Fraction(step.value)
    with exact_risk_arithmetic():
        executable=D(units)*step.value*(-1 if quantity.value<0 else 1)
        return t.Quantity(unit=quantity.unit,value=executable),t.Quantity(unit=quantity.unit,value=quantity.value-executable)


def derive_program_continuation(archive):
    require(type(archive) in (ProgramFinancialAssessmentArchive,QuoteAssessmentArchiveV1),
        'CONTINUATION_ARCHIVE_KIND_REQUIRED')
    with exact_risk_arithmetic():return _derive(archive)


def _derive(a):
    quote=type(a) is QuoteAssessmentArchiveV1;f=a.risk_facts;i=f.inputs;p=a.compilation
    sf=f.target_facts.scope_facts if quote else f.target_facts
    require(a.book.scope==i.scope==p.scope and digest(a.book)==sf.book_hash==a.allocation.book_hash,
        'CONTINUATION_CURRENT_BOOK_CHANGED')
    unit=sf.unit_binding.ledger_unit;scope=p.scope;actual=D(0)
    for position in a.book.positions:
        if position.scope!=scope:continue
        require(position.actual_quantity.unit==unit and position.owner_strategy_id==sf.owner.owner_strategy_id
            and position.ownership_epoch==sf.owner.ownership_epoch,'CONTINUATION_POSITION_NOT_OWNED')
        actual+=position.actual_quantity.value
    expected=a.allocation.scope_state.actual if quote else a.allocation.delta.actual
    require(expected==t.Quantity(unit=unit,value=actual),'CONTINUATION_CURRENT_BOOK_CHANGED')
    if quote:
        b=a.allocation.position
        low,high=b.baseline_minimum.value,b.baseline_maximum.value
        minimum,maximum=b.projected_minimum.value,b.projected_maximum.value
        goal='ABSOLUTE_POSITION_VALUE_BAND';reducing=b.classification=='REDUCE'
    else:
        low=high=actual;sides={s.logical_client_order_id:s.side for s in i.order_sides}
        for order in a.book.active_orders:
            if order.order.identity.scope!=scope:continue
            side=sides.get(order.order.identity.logical_client_order_id)
            require(side in ('BUY','SELL') and order.leaves_quantity.unit==unit,'CONTINUATION_WORKING_SIDE_REQUIRED')
            q=order.leaves_quantity.value*(1 if side=='BUY' else -1);low+=min(q,D(0));high+=max(q,D(0))
        for pending in sf.inputs.pending:
            require(pending.signed_quantity.unit==unit,'CONTINUATION_PENDING_UNIT_CHANGED')
            q=pending.signed_quantity.value;low+=min(q,D(0));high+=max(q,D(0))
        delta=a.allocation.delta.remaining.value
        minimum=low+min(delta,D(0));maximum=high+max(delta,D(0))
        goal='SCALAR_TARGET';reducing=a.allocation.delta.classification=='REDUCE'
    quantity=lambda value:t.Quantity(unit=unit,value=value)
    executable,residual=derive_position_lot_residual(quantity(actual),i.instrument.quantity_step)
    duties=['CURRENT_POSITION_NOT_ESTABLISHED_BY_TARGET','RETAIN_ORIGINAL_UNKNOWN_AND_FUNDING_OBLIGATIONS',
        'CURRENT_PROTECTION_RECHECK_REQUIRED','REVIEW_AT_DECLARED_DEADLINE']
    if reducing or residual.value:duties.append('MANAGE_UNTIL_OBSERVED_FLAT_OR_AUTHORIZED_TRANSFER')
    if residual.value:duties.append('EXACT_NONZERO_LOT_RESIDUAL_REQUIRES_MANAGEMENT')
    if quote and a.allocation.position.unmet_goal_possible:duties.append('UNMET_GOAL_REVIEW_REQUIRED')
    base_fees=any(m.currency==i.base_asset and m.amount for m in (i.fee_reserve or ()))
    if base_fees:
        duties.append('RECONCILE_EXACT_POSITION_FEE_EFFECTS')
    duties.append('FEES_AND_FUNDING_LIABILITIES_REMAIN_SEPARATE_FROM_STOP_ESTIMATE')
    protection,estimate=_stop_estimate(a,low,high)
    if protection=='UNKNOWN':duties.append('CURRENT_PROTECTION_OR_BOUNDED_EXIT_REVIEW_REQUIRED')
    no_current=max(abs(low),abs(high))==0
    return ProgramContinuationRiskV1(contract_version='2026-10-30',record_kind='PROGRAM_CONTINUATION_RISK_V1',
        scope=scope,intent_id=a.intent_id,owner_strategy_id=sf.owner.owner_strategy_id,ownership_epoch=sf.owner.ownership_epoch,
        archive_hash=digest(a),book_hash=digest(a.book),book_sequence=a.book.sequence,actual_quantity=quantity(actual),
        no_fill_minimum_quantity=quantity(low),no_fill_maximum_quantity=quantity(high),minimum_possible_quantity=quantity(minimum),
        maximum_possible_quantity=quantity(maximum),executable_current_quantity=executable,current_lot_residual=residual,
        quantity_envelope_status='EXPLICIT_QUOTE_ENVELOPE' if quote else 'POSITION_FEE_EFFECTS_UNRESOLVED' if base_fees else 'GROSS_FILL_ENVELOPE',
        fee_liabilities=i.fee_reserve,funding_liabilities=i.funding_reserve,
        goal_kind=goal,protection_state=protection,no_fill_stop_loss_estimate=estimate,
        hard_loss_status='NO_CURRENT_EXPOSURE' if no_current else 'UNKNOWN',hard_loss_bound=None,
        required_duties=tuple(duties),evidence=a.book.evidence,as_of=i.clock.utc_now,review_due_at=a.risk_terms.review_due_at)


def _stop_estimate(a,low,high):
    f=a.risk_facts;i=f.inputs;scope=i.scope;worst=max(abs(low),abs(high))
    if worst==0:return 'NO_CURRENT_EXPOSURE',t.Money(currency=a.risk_policy.valuation_currency,amount=D(0))
    if low<0<high:return 'UNKNOWN',None
    mark=next((m for m in i.marks if m.scope==scope),None)
    if mark is None or f.protection is None or f.entry_slippage_bps is None:return 'UNKNOWN',None
    now=utc_timestamp(i.clock.utc_now);estimates=[]
    profile=f.target_facts.profile if type(a) is QuoteAssessmentArchiveV1 else f.profile
    capabilities=f.target_facts.capabilities if type(a) is QuoteAssessmentArchiveV1 else f.capabilities
    try:
        for proof in f.protection:
            if (type(proof) is not ProtectionCoverage or proof.scope!=scope
                or proof.owner_strategy_id!=a.risk_terms.owner_strategy_id or proof.ownership_epoch!=a.risk_terms.ownership_epoch
                or proof.program_hash!=a.compilation.program_hash or proof.allocation_hash!=digest(a.allocation)
                or proof.book_hash!=digest(a.book) or proof.maximum_quantity.unit!=mark.unit
                or proof.maximum_quantity.value<worst or proof.origin not in ('NATIVE_PER_FILL_DUTY','HOSTED_CASH_DUTY')
                or not {'RETAINED_POSITION','PARTIAL_FILL'}<=set(proof.phases) or not proof.duty_refs
                or not set(proof.duty_refs)<=set(f.evidence) or not _valid_refs(proof.duty_refs,scope,now)
                or now>=utc_timestamp(proof.valid_until) or proof.stop_price.currency!=mark.price.currency
                or proof.stop_price.value<=0 or not on_grid(proof.stop_price.value,i.instrument.tick_size.value)
                or not profile.price.contains(proof.stop_price.value)
                or f.allowed_stop_triggers is None or proof.stop_trigger not in f.allowed_stop_triggers):continue
            if proof.origin=='HOSTED_CASH_DUTY':
                if (scope.product==t.Product.USD_M_PERP or a.risk_terms.protection_policy!='HOSTED_ALLOWED_CASH_ONLY'
                    or proof.stop_trigger not in ('LAST','BID_ASK')):continue
            elif scope.product==t.Product.SPOT:
                if proof.stop_trigger!='LAST' or 'SPOT-STOP_LOSS' not in capabilities.capability_ids or 'STOP_LOSS' not in profile.allowed_order_types:continue
            elif scope.product==t.Product.USD_M_PERP:
                if proof.stop_trigger not in ('LAST','MARK') or 'USDM-STOP_MARKET' not in capabilities.capability_ids or 'STOP_MARKET' not in profile.allowed_order_types:continue
            else:continue
            adverse=mark.price.value-proof.stop_price.value if high>0 else proof.stop_price.value-mark.price.value
            if adverse<=0:continue
            valuation=Valuation(a.risk_policy.valuation_currency,i.fx_rates)
            cost=worst*mark.multiplier*(adverse+mark.price.value*f.entry_slippage_bps/10000)
            estimates.append(valuation.convert(t.Money(currency=mark.price.currency,amount=cost),liability=True))
        if not estimates:return 'UNKNOWN',None
        return 'BOUND_TO_ASSESSED_SOURCE',max(estimates,key=lambda m:m.amount)
    except (ValueError,ArithmeticError):return 'UNKNOWN',None


class _ContinuationWriter:
    def __init__(self,tx):self.tx=tx

    @atomic_write
    def persist(self,bundle):
        from .program_financial_admission import current_program_bundle_witness
        tx=self.tx
        current_program_bundle_witness(tx,bundle)
        require(bundle.intent.scope==tx.scope,'CONTINUATION_SCOPE_CHANGED')
        record=derive_program_continuation(bundle.archive)
        require(record.intent_id==bundle.intent.intent_id and record.archive_hash==bundle.intent.hashes.archive,
            'CONTINUATION_FINANCIAL_SOURCE_CHANGED')
        key=CONTINUATION_PREFIX+record.intent_id
        previous=tx._latest('audit_events',key,ProgramContinuationRiskV1)
        require(previous is None or digest(previous)==digest(record),'CONTINUATION_SOURCE_CONFLICT')
        if previous is None:tx._append('audit_events',key,record,0)
        return previous or record


def persist_program_continuation(tx,bundle):
    """Private successful-kernel attachment, never an owner-authored grant."""
    return _ContinuationWriter(tx).persist(bundle)
