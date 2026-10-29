"""Versioned containment for an existing preauthorized emergency EXIT.

This is a mathematical proof boundary, not a lock resolver or send permission.
The outer financial/dispatch path must authenticate its archive and independently
apply the current LockedActionVerifier; no production reader is installed here.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from .terms import RiskMetrics
from .evaluator import EmergencyReduction
from .locks import ACTIONS,POLICIES
from trading_system.protection.native import report_errors
from .exposure import ExposureCalculator
from .valuation import Valuation


@dataclass(frozen=True,slots=True,kw_only=True)
class BoundedReductionProof:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['BOUNDED_REDUCTION_PROOF']
    scope:t.ScopeKey
    owner_strategy_id:str
    ownership_epoch:int
    authorization_id:str
    policy_revision:int
    source_hash:str
    terms_hash:str
    allocation_hash:str
    book_hash:str
    context_hash:str
    snapshot_ref:t.SnapshotRef
    collateral_group_id:str
    group_facts_hash:str
    position:t.ManagedPosition
    emergency:EmergencyReduction
    locks:tuple[t.RiskLock,...]
    unresolved_group_commands:tuple[str,...]
    report:t.VenueReport
    archive_ref:t.EvidenceRef
    management_duty_refs:tuple[t.EvidenceRef,...]
    valid_until:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ResidualRiskState:
    position_id:str
    starting_quantity:t.Quantity
    remaining_quantity:t.Quantity
    protection_state:Literal['UNPROTECTED','UNKNOWN']
    risk_permission:Literal[t.RiskPermission.EXIT_ONLY]
    intended_stop_loss:t.Money|None
    hard_loss_status:Literal['UNKNOWN','BOUNDED']
    hard_loss_bound:t.Money|None
    management_policy:Literal['MANAGE_UNTIL_FLAT']
    management_duty_refs:tuple[t.EvidenceRef,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class ReductionRiskMetrics(RiskMetrics):
    contract_version:Literal['2026-10-29']
    metric_kind:Literal['BOUNDED_REDUCTION_METRICS']
    reduction_proof_hash:str
    residual:ResidualRiskState


def reduction_metric_shape_valid(metrics):
    if type(metrics) is not ReductionRiskMetrics:return False
    r=metrics.residual
    if (metrics.contract_version!='2026-10-29' or metrics.metric_kind!='BOUNDED_REDUCTION_METRICS'
        or type(metrics.reduction_proof_hash) is not str or len(metrics.reduction_proof_hash)!=64
        or any(c not in '0123456789abcdef' for c in metrics.reduction_proof_hash)
        or metrics.protection_complete is not False or type(r) is not ResidualRiskState):return False
    quantities=(r.starting_quantity,r.remaining_quantity)
    if (any(type(q) is not t.Quantity or not q.value.is_finite() for q in quantities)
        or r.starting_quantity.unit!=r.remaining_quantity.unit
        or not 0<r.remaining_quantity.value.copy_abs()<r.starting_quantity.value.copy_abs()
        or r.starting_quantity.value.is_signed()!=r.remaining_quantity.value.is_signed() or not r.position_id
        or r.protection_state not in ('UNPROTECTED','UNKNOWN') or r.risk_permission!=t.RiskPermission.EXIT_ONLY
        or r.management_policy!='MANAGE_UNTIL_FLAT' or not r.management_duty_refs):return False
    if r.hard_loss_status=='UNKNOWN':
        if r.hard_loss_bound is not None:return False
    elif r.hard_loss_status=='BOUNDED':
        if type(r.hard_loss_bound) is not t.Money or not r.hard_loss_bound.amount.is_finite() or r.hard_loss_bound.amount<=0:return False
    else:return False
    if metrics.planned_loss!=r.intended_stop_loss:return False
    if metrics.planned_loss is not None and (type(metrics.planned_loss) is not t.Money
        or not metrics.planned_loss.amount.is_finite() or metrics.planned_loss.amount<=0):return False
    return True


def validate_bounded_reduction(proof,inputs,terms,plan,a,book,facts):
    """Return typed residual state only for the existing bounded EXIT policy."""
    def require(condition,reason):
        if not condition:raise ValueError(reason)
    digest=lambda record:canonical_hash(domain_payload(record))
    require(type(proof) is BoundedReductionProof and proof.contract_version=='2026-10-29'
        and proof.record_kind=='BOUNDED_REDUCTION_PROOF','BOUNDED_REDUCTION_PROOF_REQUIRED')
    delta=a.delta;now=utc_timestamp(inputs.clock.utc_now)
    require(plan.original_program.template=='ATOMIC' and len(a.nodes)==1 and not plan.original_program.links
        and not plan.original_program.exclusive_groups and not book.active_orders and not facts.target_facts.inputs.pending
        and not facts.target_facts.unknown_command_ids and not delta.cancel_first and delta.working.value==0
        and type(proof.unresolved_group_commands) is tuple and not proof.unresolved_group_commands
        and all(c.kind=='POSITION' for c in facts.budget_facts.consumers),'BOUNDED_REDUCTION_OBLIGATIONS_UNRESOLVED')
    require(delta.classification=='REDUCE' and delta.actual.value*delta.target.value>0
        and 0<delta.target.value.copy_abs()<delta.actual.value.copy_abs()
        and delta.target.value==delta.actual.value+delta.remaining.value,'BOUNDED_REDUCTION_MUST_STRICTLY_REDUCE')
    require(len(book.positions)==1 and proof.position==book.positions[0],'BOUNDED_REDUCTION_POSITION_AMBIGUOUS')
    position=proof.position
    require(position.scope==terms.scope==proof.scope and position.position_id and position.owner_book_id
        and position.owner_strategy_id==terms.owner_strategy_id==proof.owner_strategy_id
        and position.ownership_epoch==terms.ownership_epoch==proof.ownership_epoch
        and position.actual_quantity==delta.actual and position.actual_quantity.unit==terms.target.unit,
        'BOUNDED_REDUCTION_POSITION_NOT_OWNED')
    require(proof.authorization_id==terms.authorization_id and proof.policy_revision==terms.policy_revision
        and proof.collateral_group_id==terms.collateral_group_id and proof.source_hash==terms.source.hash
        and proof.terms_hash==digest(terms) and proof.allocation_hash==digest(a) and proof.book_hash==digest(book)
        and proof.context_hash==digest(facts.target_facts.context) and proof.snapshot_ref==facts.market.ref
        and proof.group_facts_hash==digest(facts.budget_facts),'BOUNDED_REDUCTION_SOURCE_BINDING_MISMATCH')
    emergency=inputs.emergency
    require(type(emergency) is EmergencyReduction and proof.emergency==emergency
        and emergency.maximum_quantity.unit==terms.target.unit
        and delta.remaining.value.copy_abs()<=emergency.maximum_quantity.value<=delta.actual.value.copy_abs()
        and emergency.maximum_slippage_bps.is_finite() and 0<=facts.entry_slippage_bps<=emergency.maximum_slippage_bps
        and emergency.maximum_quote_age_ms>0,'BOUNDED_REDUCTION_EMERGENCY_BINDING_MISMATCH')
    node=plan.nodes[0];order=node.original.order
    require(node.order_type in emergency.allowed_order_types and order.side==('SELL' if delta.actual.value>0 else 'BUY'),
        'BOUNDED_REDUCTION_ORDER_NOT_AUTHORIZED')
    if terms.scope.product==t.Product.USD_M_PERP:
        require(inputs.observed_position_mode=='ONE_WAY' and terms.scope.venue_leg=='BOTH'
            and order.reduce_only is True,'BOUNDED_REDUCTION_REDUCE_ONLY_REQUIRED')
    require(proof.locks and set(position.risk_locks)<=set(proof.locks),'BOUNDED_REDUCTION_LOCK_CONTEXT_REQUIRED')
    allowed=set(ACTIONS[t.RiskPermission.NORMAL])
    for lock in proof.locks:
        expected=POLICIES.get(lock.cause)
        require(expected is not None and lock.state=='ACTIVE' and lock.permission==expected[0]
            and lock.permission!=t.RiskPermission.RECOVERY_REQUIRED
            and lock.cause not in ('ORDER_OUTCOME_UNKNOWN','RESERVATION_COMMAND_UNKNOWN')
            and (lock.scope==terms.scope or lock.cause=='DAILY_LOSS'),'BOUNDED_REDUCTION_RECOVERY_OR_UNKNOWN_LOCK')
        allowed.intersection_update(ACTIONS[lock.permission])
    require('EXIT' in allowed,'BOUNDED_REDUCTION_EXIT_NOT_ALLOWED')
    require(not report_errors(proof.report,terms.scope,inputs.clock.utc_now,60)
        and proof.report.coverage==book.coverage and proof.report.positions==book.positions
        and proof.report.orders==book.active_orders and proof.report.balances==book.balances
        and all(e in proof.report.evidence for e in book.evidence),'BOUNDED_REDUCTION_REPORT_MISMATCH')
    require(proof.archive_ref.hash==digest(proof.report) and proof.archive_ref in facts.evidence
        and proof.management_duty_refs and set(proof.management_duty_refs)<=set(facts.evidence)
        and all(r.environment==terms.scope.environment and r.id and r.source and len(r.hash)==64
            and utc_timestamp(r.visible_at)<=now for r in (proof.archive_ref,*proof.management_duty_refs))
        and now<utc_timestamp(proof.valid_until),'BOUNDED_REDUCTION_ARCHIVE_OR_DUTY_UNVERIFIED')
    valuation=Valuation(inputs.instrument.quote_currency,inputs.fx_rates)
    before=replace_delta(delta,delta.actual.value,Decimal(0))
    old=ExposureCalculator(valuation,inputs.marks).project(book,before)
    after=ExposureCalculator(valuation,inputs.marks).project(book,delta)
    require(not old.missing and not after.missing and after.gross.amount<old.gross.amount
        and after.net.amount.copy_abs()<old.net.amount.copy_abs(),'BOUNDED_REDUCTION_EXPOSURE_NOT_DECREASED')
    return ResidualRiskState(position_id=position.position_id,starting_quantity=delta.actual,
        remaining_quantity=delta.target,protection_state='UNPROTECTED',risk_permission=t.RiskPermission.EXIT_ONLY,
        intended_stop_loss=None,hard_loss_status='UNKNOWN',hard_loss_bound=None,
        management_policy='MANAGE_UNTIL_FLAT',management_duty_refs=proof.management_duty_refs)


def replace_delta(delta,target,remaining):
    from dataclasses import replace
    return replace(delta,target=t.Quantity(value=target,unit=delta.target.unit),
        remaining=t.Quantity(value=remaining,unit=delta.remaining.unit),classification='NONE')
