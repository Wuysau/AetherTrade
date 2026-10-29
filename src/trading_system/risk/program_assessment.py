"""Typed generic risk evidence and pure assessment, never order authority.

Host readers must authenticate every input under the existing owner/collateral
lock. Dataclass construction, labels and hashes are not evidence of a venue order
or protection. No production reader/permit/command is registered by this module.
"""
from dataclasses import dataclass,replace
from decimal import Decimal,Inexact,Rounded
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.order_snapshots import OrderRuleSnapshot,OrderCapabilitySnapshot
from trading_system.intents.product_rules import TrustedProductRules
from trading_system.intents.program_target_allocation import ProgramTargetFacts
from .evaluator import RiskInputs
from .program_exposure import ProgramBudgetFacts,FeeBound,derive_program_exposure
from .terms import RiskMetrics,RiskSource,RiskTerms,metric_binding_codes
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp,decimal_string
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.intents.program_compilation import compile_order_program
from trading_system.intents.program_target_allocation import compile_program_target
from trading_system.market.snapshots import snapshot_content_hash
from trading_system.market.freshness import FreshnessPolicy
from .bindings import terms_binding,initial_binding_codes
from .policy_checks import (exact_risk_arithmetic,account_policy_codes,protection_policy_codes,
    portfolio_limit_codes,spendable_codes,capital_limit_exceeded,legacy_margin_upper_bound)
from .valuation import Valuation,nonnegative
from .asset_capacity import amounts
from .exposure import ExposureCalculator,ExposureMark,PendingExposure,ProposedExposure,WorkingExposure
from trading_system.intents.semantic_validation import on_grid

D=Decimal


def digest(record):return canonical_hash(domain_payload(record))


class _RiskDataError(ValueError):pass


def require(condition,reason):
    if not condition:raise _RiskDataError(reason)


@dataclass(frozen=True,slots=True,kw_only=True)
class NodeRiskBound:
    node_id:str
    minimum_price:t.Price|None
    maximum_price:t.Price|None
    execution_fees:tuple[FeeBound,...]|None
    funding_reserve:tuple[t.Money,...]|None
    margin_reserve:tuple[t.Money,...]|None
    holding_horizon_seconds:int|None
    maximum_live_children:int|None
    evidence_refs:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class WorkingRiskBound:
    logical_client_order_id:str
    scope:t.ScopeKey
    side:Literal['BUY','SELL']
    quantity:t.Quantity
    minimum_price:t.Price|None
    maximum_price:t.Price|None
    evidence_refs:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class PendingRiskBound:
    obligation_id:str
    scope:t.ScopeKey
    source_family:Literal['LEGACY_COMMAND','APPLICATION_WRITE']
    service_namespace:Literal['ORDINARY','ORDER_LIST','CONDITIONAL_ALGO','EXECUTION_ALGO','EQUITY']
    canonical_command_refs:tuple[str,...]
    original_command_refs:tuple[str,...]
    minimum_price:t.Price|None
    maximum_price:t.Price|None
    evidence_refs:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class ProtectionCoverage:
    """Concrete phase/quantity duty reference, not a model-authored True flag.

NATIVE_FULL_FILL_ONLY can never claim PARTIAL_FILL, even if a payload lists it.
An authentic server-side archive reader must prove each referenced duty and its
activation/race handling before supplying this fact; no such reader is installed.
"""
    scope:t.ScopeKey
    owner_strategy_id:str
    ownership_epoch:int
    program_hash:str
    allocation_hash:str
    book_hash:str
    origin:Literal['NATIVE_PER_FILL_DUTY','HOSTED_CASH_DUTY','NATIVE_FULL_FILL_ONLY']
    phases:tuple[Literal['PARTIAL_FILL','FULL_FILL','RECEIPT_NET','RETAINED_POSITION'],...]
    node_ids:tuple[str,...]
    maximum_quantity:t.Quantity
    stop_price:t.Price
    stop_trigger:str
    duty_refs:tuple[t.EvidenceRef,...]
    valid_until:str
    maximum_live_orders:int|None=None


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramRiskFacts:
    inputs:RiskInputs
    market:t.SnapshotPacket
    authorization:t.AuthorizationDecision
    target_facts:ProgramTargetFacts
    budget_facts:ProgramBudgetFacts
    rules:OrderRuleSnapshot
    capabilities:OrderCapabilitySnapshot
    profile:TrustedProductRules
    expected_build_digest:str
    expected_instrument_ref:str
    node_bounds:tuple[NodeRiskBound,...]|None
    working_bounds:tuple[WorkingRiskBound,...]|None
    pending_bounds:tuple[PendingRiskBound,...]|None
    additional_exit_fees:tuple[FeeBound,...]|None
    protection:tuple[ProtectionCoverage,...]|None
    allowed_stop_triggers:tuple[str,...]|None
    maximum_live_orders:int|None
    holding_horizon_seconds:int|None
    entry_slippage_bps:Decimal|None
    evidence:tuple[t.EvidenceRef,...]
    as_of:str
    valid_until:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramRiskDecision:
    risk_decision_id:str
    source:RiskSource
    terms_hash:str
    compilation_hash:str
    allocation_hash:str
    exposure_hash:str
    facts_hash:str
    policy_hash:str
    evaluated_at:str
    expires_at:str
    verdict:Literal['ALLOW','DENY','REPLAN_REQUIRED']
    metrics:RiskMetrics|None
    rejection_codes:tuple[str,...]
    obligations:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False


def evaluate_program(inputs,terms,plan,allocation,exposure,book,policy,facts):
    """Derive real metrics; local ALLOW is never a send/reservation authority."""
    codes=[];metrics=None
    try:
        with exact_risk_arithmetic():
            require(type(terms) is RiskTerms and type(facts) is ProgramRiskFacts,'PROGRAM_RISK_FACTS_REQUIRED')
            codes.extend(initial_binding_codes(terms_binding(terms),inputs,book,facts.market,policy))
            if not codes:metrics=_derive(inputs,terms,plan,allocation,exposure,book,policy,facts,codes)
    except _RiskDataError as error:codes.append(str(error))
    except (Inexact,Rounded):codes.append('ARITHMETIC_PRECISION_UNPROVEN')
    except (ValueError,TypeError,ArithmeticError,AttributeError):codes.append('PROGRAM_RISK_INPUT_INVALID')
    if codes:metrics=None
    verdict=('REPLAN_REQUIRED' if all(c.startswith('REPLAN:') for c in codes) else 'DENY') if codes else 'ALLOW'
    obligations=('MATHEMATICAL_RISK_ONLY','AUTHENTIC_CURRENT_FACT_READERS_REQUIRED',
        'LOCKED_FINANCIAL_BUNDLE_AND_CURRENT_OWNER_REQUIRED','NO_COMMAND_OR_PERMIT_CREATED',
        'UNFILLED_ORDERS_RETAIN_PROTECTION_AND_RECONCILIATION_DUTY')
    values=dict(source=terms.source,terms_hash=digest(terms),compilation_hash=digest(plan),allocation_hash=digest(allocation),
        exposure_hash=digest(exposure),facts_hash=digest(facts),policy_hash=digest(policy),evaluated_at=inputs.clock.utc_now,
        expires_at=min((terms.valid_until,facts.valid_until,inputs.expires_at),key=utc_timestamp),verdict=verdict,
        metrics=metrics,rejection_codes=tuple(dict.fromkeys(codes)),obligations=obligations)
    decision=ProgramRiskDecision(risk_decision_id='unassigned',**values)
    body=domain_payload(decision);del body['risk_decision_id']
    return replace(decision,risk_decision_id=canonical_hash(body))


def _valid_refs(refs,scope,now):
    return bool(refs) and all(e.environment==scope.environment and e.id and e.source and len(e.hash)==64
        and all(c in '0123456789abcdef' for c in e.hash) and utc_timestamp(e.visible_at)<=now for e in refs)


def _sum_assets(*values):
    result={}
    for collection in values:
        for money in collection:
            nonnegative(money.amount);require(money.currency,'RISK_COST_CURRENCY_MISSING')
            result[money.currency]=result.get(money.currency,D(0))+money.amount
    return result


def _money(values):return tuple(t.Money(currency=k,amount=v) for k,v in sorted(values.items()) if v)


def _aggregate_nodes(plan,bounds,extract,budget):
    """Use the same evidenced exclusivity membership as the program budget."""
    total={};used=set()
    proofs={frozenset(g.node_ids) for g in budget.atomic_groups}
    for group in plan.original_program.exclusive_groups:
        if plan.original_program.execution_mode!='VENUE_NATIVE' or frozenset(group) not in proofs:continue
        values=[_sum_assets(extract(bounds[n])) for n in group]
        for asset in {a for value in values for a in value}:
            total[asset]=total.get(asset,D(0))+max(value.get(asset,D(0)) for value in values)
        used.update(group)
    for node in plan.nodes:
        if node.original.node_id in used:continue
        for asset,value in _sum_assets(extract(bounds[node.original.node_id])).items():
            total[asset]=total.get(asset,D(0))+value
    return total


def _derive(inputs,terms,plan,a,e,book,policy,facts,codes):
    now=utc_timestamp(inputs.clock.utc_now);scope=terms.scope;p=plan.original_proposal;codec=VersionedOrderCodec()
    require(scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),'ENVIRONMENT_DISABLED')
    for name in ('node_bounds','working_bounds','pending_bounds','additional_exit_fees','protection','allowed_stop_triggers',
                 'maximum_live_orders','holding_horizon_seconds','entry_slippage_bps'):
        require(getattr(facts,name) is not None,'PROGRAM_RISK_FACT_MISSING:'+name)
    require(facts.inputs==inputs,'PROGRAM_RISK_INPUT_BINDING_MISMATCH')
    require(terms.source==RiskSource(kind='ORDER_PROPOSAL',version=p.contract_version,
        hash=codec.hash('order-proposal',p)) and terms.source.hash==plan.proposal_hash,'PROGRAM_RISK_SOURCE_MISMATCH')
    require(terms.action==p.action=='SET_TARGET' and terms.requested_leverage==p.requested_leverage
        and terms.target==a.terms.target and terms.target_side==p.target_side==a.terms.target_side
        and terms.protection_policy==p.protection_policy and terms.stop_trigger==p.exit_plan.stop_trigger
        and terms.stop_price==(None if p.exit_plan.stop_price is None else decimal_string(p.exit_plan.stop_price))
        and terms.maximum_holding_seconds==p.exit_plan.maximum_holding_seconds
        and terms.review_due_at==p.exit_plan.review_due_at and terms.submission_deadline==p.program.submit_valid_until,
        'PROGRAM_RISK_RESPONSIBILITY_MISMATCH')
    require(_valid_refs(facts.evidence,scope,now) and _valid_refs(book.evidence,scope,now),'PROGRAM_RISK_EVIDENCE_MISSING')
    require(utc_timestamp(facts.as_of)<=now<utc_timestamp(facts.valid_until),'PROGRAM_RISK_FACTS_EXPIRED')
    require(utc_timestamp(terms.valid_until)<=min(map(utc_timestamp,(a.terms.valid_until,facts.valid_until,
        facts.rules.valid_until,facts.capabilities.valid_until,facts.profile.valid_until,
        facts.budget_facts.valid_until,inputs.expires_at))),'PROGRAM_RISK_WINDOW_EXTENDED')
    require(facts.market.ref.hash==snapshot_content_hash(facts.market),'PROGRAM_RISK_MARKET_HASH_MISMATCH')
    require(facts.authorization.allowed is True and not facts.authorization.rejection_codes
        and facts.authorization.authorization_id==terms.authorization_id
        and _valid_refs(facts.authorization.evidence,scope,now),'AUTHORIZATION_REQUIRED')
    target=facts.target_facts
    require(target.inputs.instrument==inputs.instrument and target.inputs.session==inputs.session
        and target.inputs.order_sides==inputs.order_sides and target.inputs.clock==inputs.clock
        and target.inputs.allow_round_down==inputs.allow_round_down and target.context.snapshot_ref==facts.market.ref,
        'PROGRAM_RISK_TARGET_INPUT_BINDING_MISMATCH')
    require(inputs.multiplier==target.unit_binding.ledger_multiplier and inputs.base_asset==facts.rules.base_asset,
        'PROGRAM_RISK_UNIT_BINDING_MISMATCH')
    expected=compile_order_program(p,facts.rules,facts.capabilities,profile=facts.profile,scope=scope,
        now=inputs.clock.utc_now,expected_build_digest=facts.expected_build_digest,
        expected_rule_revision=inputs.instrument.rule_revision,expected_capabilities_hash=codec.hash('order-capability-snapshot',facts.capabilities),
        expected_instrument_ref=facts.expected_instrument_ref,allow_round_down=inputs.allow_round_down)
    require(expected==plan,'PROGRAM_RISK_COMPILATION_CHANGED')
    require(compile_program_target(plan,book,target)==a,'PROGRAM_RISK_ALLOCATION_CHANGED')
    require(not a.blockers,a.blockers[0] if a.blockers else 'PROGRAM_RISK_ALLOCATION_BLOCKED')
    valuation=Valuation(policy.valuation_currency,inputs.fx_rates)
    require(all(rate.evidence_id in {ref.id for ref in inputs.evidence} for rate in inputs.fx_rates),'FX_EVIDENCE_UNBOUND')
    require(derive_program_exposure(plan,facts.budget_facts,now=inputs.clock.utc_now,valuation=valuation)==e,'PROGRAM_RISK_BUDGET_CHANGED')
    require(not e.blockers,e.blockers[0] if e.blockers else 'PROGRAM_RISK_BUDGET_BLOCKED')
    require('EXISTING_PROGRAM_NO_RESUBMISSION' not in e.obligations,'PROGRAM_ALREADY_ALLOCATED_NO_RESUBMISSION')
    emergency=inputs.emergency if a.delta.classification=='REDUCE' else None
    age=emergency.maximum_quote_age_ms if emergency is not None else policy.maximum_quote_age_ms
    codes.extend(FreshnessPolicy({scope.product:age}).evaluate(facts.market,inputs.clock,inputs.session).rejection_codes)
    if codes:return None
    require(type(facts.holding_horizon_seconds) is int and facts.holding_horizon_seconds>=terms.maximum_holding_seconds>0,
        'PROGRAM_RISK_HOLDING_HORIZON_UNDERBOUND')
    require(isinstance(facts.entry_slippage_bps,D) and facts.entry_slippage_bps.is_finite()
        and 0<=facts.entry_slippage_bps<=decimal_string(policy.maximum_entry_slippage_bps),'PROGRAM_RISK_SLIPPAGE_INVALID')
    bid,ask=facts.market.quote.bid,facts.market.quote.ask
    require(bid is not None and ask is not None,'PRICE_MISSING')
    require(bid.currency==ask.currency==inputs.instrument.quote_currency and bid.value<=ask.value,'INVALID_QUOTE')
    if emergency is None:
        require((ask.value-bid.value)*10000<=bid.value*facts.entry_slippage_bps,'ENTRY_SPREAD_LIMIT')
    else:
        require(emergency.maximum_quantity.unit==terms.target.unit
            and abs(a.delta.remaining.value)<=nonnegative(emergency.maximum_quantity.value)
            and emergency.reference_price.currency==inputs.instrument.quote_currency,'EMERGENCY_SCOPE_INVALID')
        reference=emergency.reference_price.value;price=ask.value if a.delta.remaining.value>0 else bid.value
        adverse=price-reference if a.delta.remaining.value>0 else reference-price
        require(adverse*10000<=reference*nonnegative(emergency.maximum_slippage_bps),'EMERGENCY_PRICE_TOLERANCE')
    require(terms.stop_trigger in facts.allowed_stop_triggers,'STOP_TRIGGER_UNVERIFIED')
    bounds={b.node_id:b for b in facts.node_bounds}
    require(len(bounds)==len(facts.node_bounds) and set(bounds)=={n.original.node_id for n in plan.nodes},'PROGRAM_RISK_NODE_BOUNDS_INCOMPLETE')
    budget_nodes={b.node_id:b for b in facts.budget_facts.node_facts}
    evidence_ids={ref.id for ref in facts.evidence}
    for n in plan.nodes:
        b=bounds[n.original.node_id];old=budget_nodes[n.original.node_id]
        require(type(b) is NodeRiskBound and b.minimum_price is not None and b.maximum_price is not None,'PROGRAM_RISK_PRICE_BOUND_MISSING')
        require(b.minimum_price.currency==b.maximum_price.currency==facts.rules.quote_asset
            and b.minimum_price.value<=b.maximum_price.value,'PROGRAM_RISK_PRICE_BOUND_INVALID')
        require(b.evidence_refs and set(b.evidence_refs)<=evidence_ids,'PROGRAM_RISK_NODE_EVIDENCE_MISSING')
        require(b.execution_fees is not None and b.funding_reserve is not None,'FEE_OR_FUNDING_EVIDENCE_MISSING')
        require(b.execution_fees==old.fees and b.margin_reserve==old.margin,'PROGRAM_RISK_COST_BUDGET_MISMATCH')
        require(type(b.holding_horizon_seconds) is int and b.holding_horizon_seconds>=terms.maximum_holding_seconds,'PROGRAM_RISK_NODE_HORIZON_UNDERBOUND')
        require(type(b.maximum_live_children) is int and b.maximum_live_children>=0
            and (b.maximum_live_children>0 if n.original.role=='ALGORITHM_PARENT' else b.maximum_live_children==0),'PROGRAM_RISK_CHILD_COUNT_MISSING')
        o=n.original.order
        if emergency is not None and n.original.node_id in {x.node_id for x in a.nodes if x.phase in ('INITIAL','ALGORITHM_ENVELOPE')}:
            require(n.order_type in emergency.allowed_order_types,'EMERGENCY_SCOPE_INVALID')
        if o.price.kind=='FIXED_LIMIT':
            limit=D(o.price.value)
            require(b.maximum_price.value==limit if o.side=='BUY' else b.minimum_price.value==limit,'PROGRAM_RISK_DECLARED_LIMIT_CHANGED')
            reference=(emergency.reference_price.value if emergency is not None else ask.value if o.side=='BUY' else bid.value)
            tolerance=emergency.maximum_slippage_bps if emergency is not None else facts.entry_slippage_bps
            adverse=limit-reference if o.side=='BUY' else reference-limit
            require(adverse*10000<=reference*tolerance,'LIMIT_PRICE_TOLERANCE')
        else:
            envelope=facts.profile.execution_price_envelope
            require(envelope is not None and D(envelope.minimum)<=b.minimum_price.value<=b.maximum_price.value<=D(envelope.maximum),
                'PROGRAM_RISK_EXECUTION_ENVELOPE_UNBOUND')
    require(type(facts.maximum_live_orders) is int and facts.maximum_live_orders>=len(plan.nodes)
        +sum(b.maximum_live_children for b in bounds.values()),'PROGRAM_RISK_ORDER_COUNT_UNDERBOUND')
    delta=a.delta;actual=delta.actual.value;target_qty=delta.target.value
    reducing=delta.classification=='REDUCE'
    codes.extend(account_policy_codes(product=scope.product,venue_leg=scope.venue_leg,target=target_qty,
        leverage=terms.requested_leverage,multiplier=inputs.multiplier,reducing=reducing,policy=policy,
        authorized_margin_mode=inputs.authorized_margin_mode,observed_margin_mode=inputs.observed_margin_mode,
        authorized_position_mode=inputs.authorized_position_mode,observed_position_mode=inputs.observed_position_mode))
    if not reducing and (not inputs.new_risk_allowed or any(pos.risk_locks for pos in book.positions)):codes.append('NEW_RISK_LOCKED')
    if codes:return None
    return _metrics(inputs,terms,plan,a,e,book,policy,facts,bounds,valuation,reducing,codes)


def _portfolio(inputs,terms,plan,a,e,book,facts,bounds,valuation):
    scope=terms.scope;bridge=facts.target_facts.unit_binding
    marks={mark.scope:mark for mark in inputs.marks}
    require(scope in marks and marks[scope].unit==terms.target.unit
        and marks[scope].multiplier==inputs.multiplier,'VALUATION_MARK_MISSING:'+scope.instrument_uid)
    def mark_for(scope,upper):
        require(scope in marks and upper is not None,'PROGRAM_RISK_PORTFOLIO_BOUND_MISSING')
        require(upper.currency==marks[scope].price.currency,'PROGRAM_RISK_PORTFOLIO_CURRENCY_MISMATCH')
        return replace(marks[scope],price=upper)
    current={(o.order.identity.scope,o.order.identity.logical_client_order_id):o for o in book.active_orders}
    ordered={(b.scope,b.logical_client_order_id):b for b in facts.working_bounds}
    require(len(ordered)==len(facts.working_bounds) and set(ordered)==set(current),'PROGRAM_RISK_WORKING_COVERAGE_INCOMPLETE')
    working=[]
    for key,order in current.items():
        b=ordered[key]
        require(order.command_certainty!='UNKNOWN','UNKNOWN_ORDER_RECONCILIATION_REQUIRED')
        require(b.side in ('BUY','SELL') and b.quantity==order.leaves_quantity
            and b.evidence_refs and set(b.evidence_refs)<={r.id for r in facts.evidence},'PROGRAM_RISK_WORKING_BOUND_MISMATCH')
        working.append(WorkingExposure(scope=b.scope,logical_client_order_id=b.logical_client_order_id,
            side=b.side,mark=mark_for(b.scope,b.maximum_price)))
    pending=[];pending_bounds={b.obligation_id:b for b in facts.pending_bounds}
    source_pending=facts.target_facts.inputs.pending
    require(len(pending_bounds)==len(facts.pending_bounds) and set(pending_bounds)=={p.intent_id for p in source_pending},
        'PROGRAM_RISK_PENDING_COVERAGE_INCOMPLETE')
    for p in source_pending:
        b=pending_bounds[p.intent_id]
        require(b.scope==scope and b.canonical_command_refs==p.command_refs and b.original_command_refs
            and len(b.original_command_refs)==len(b.canonical_command_refs)
            and b.evidence_refs and set(b.evidence_refs)<={r.id for r in facts.evidence},'PROGRAM_RISK_PENDING_SOURCE_MISMATCH')
        pending.append(PendingExposure(obligation_id=p.intent_id,scope=scope,signed_quantity=p.signed_quantity,
            command_refs=b.original_command_refs,represented_order_ids=p.represented_order_ids,
            source_family=b.source_family,service_namespace=b.service_namespace,mark=mark_for(scope,b.maximum_price)))
    initial=[n for n in a.nodes if n.phase in ('INITIAL','ALGORITHM_ENVELOPE')]
    proposed=tuple(ProposedExposure(node_id=n.node_id,scope=scope,
        signed_quantity=t.Quantity(value=n.ledger_quantity.value*(1 if n.side=='BUY' else -1),unit=terms.target.unit),
        mark=mark_for(scope,bounds[n.node_id].maximum_price)) for n in initial)
    calculator=lambda items:ExposureCalculator(valuation,inputs.marks,inputs.order_sides,pending=tuple(pending),
        working=tuple(working),proposed=items)
    reports=[]
    no_fill=replace(a.delta,target=t.Quantity(value=a.delta.actual.value+a.delta.working.value,unit=terms.target.unit),
        remaining=t.Quantity(value=D(0),unit=terms.target.unit),classification='NONE')
    reports.append(calculator(()).project(book,no_fill))
    reports.append(calculator(proposed).project(book,a.delta))
    deferred=[n for n in a.nodes if n.phase in ('AFTER_FULL_FILL','AFTER_NET_RECEIPT')]
    if deferred:
        by_id={n.original.node_id:n for n in plan.nodes};quantities={}
        for n in deferred:
            if n.ledger_quantity is not None:quantities[n.node_id]=n.ledger_quantity.value
            else:
                receipt=next((r for r in e.receipts if n.node_id in r.target_node_ids),None)
                require(receipt is not None,'PROGRAM_RISK_RECEIPT_QUANTITY_UNBOUND')
                quantities[n.node_id]=receipt.maximum_net.value*bridge.ledger_units_per_native_unit
        proof={frozenset(g.node_ids) for g in facts.budget_facts.atomic_groups}
        close=D(0);used=set()
        for group in plan.original_program.exclusive_groups:
            if plan.original_program.execution_mode=='VENUE_NATIVE' and frozenset(group) in proof:
                close+=max(quantities.get(n,D(0)) for n in group);used.update(group)
        close+=sum((q for key,q in quantities.items() if key not in used),D(0))
        owned_at_activation=abs(a.delta.actual.value+a.delta.remaining.value)
        # Other still-working entry orders need not fill before this working
        # leg activates its pending exits. Never borrow their possible inventory.
        if close>owned_at_activation:
            bounded=all(by_id[n.node_id].original.order.reduce_only for n in deferred)
            require(scope.product==t.Product.USD_M_PERP and bounded,'CONTINGENT_QUANTITY_NOT_OWNED_AT_ACTIVATION')
            close=owned_at_activation
        sign=D(1) if a.delta.target.value>0 else D(-1)
        remaining=a.delta.remaining.value-sign*close
        target=a.delta.actual.value+a.delta.working.value+remaining
        require(a.delta.actual.value*target>=0 and (scope.product==t.Product.USD_M_PERP or target>=0),
            'CONTINGENT_REVERSAL_NOT_AUTHORIZED')
        scenario=replace(a.delta,target=t.Quantity(value=target,unit=terms.target.unit),
            remaining=t.Quantity(value=remaining,unit=terms.target.unit),classification='REDUCE' if a.delta.actual.value
                and abs(target)<abs(a.delta.actual.value) else 'INCREASE' if remaining else 'NONE')
        # A post-exit valuation envelope is not a new execution request. Physical
        # remaining units use the conservative current/fill mark, never sale P&L.
        envelope=max([marks[scope].price.value]+[b.maximum_price.value for b in bounds.values()])
        scenario_marks=tuple(replace(m,price=t.Price(value=envelope,currency=m.price.currency)) if m.scope==scope
            and a.delta.classification!='REDUCE' else m for m in inputs.marks)
        # Before an automatic pending exit, the acquired inventory can still be
        # held at its declared scenario valuation ceiling. This changes portfolio
        # risk, never the BUY principal or the recorded execution/position book.
        reports.append(ExposureCalculator(valuation,scenario_marks,inputs.order_sides,
            pending=tuple(pending),working=tuple(working)).project(book,a.delta))
        reports.append(ExposureCalculator(valuation,scenario_marks,inputs.order_sides,pending=tuple(pending),working=tuple(working)).project(book,scenario))
    require(all(not report.missing for report in reports),next((code for r in reports for code in r.missing),'PORTFOLIO_INCOMPLETE'))
    # Keep a real worst-net scenario's signed groups; gross is the componentwise
    # envelope. Their sums remain exact, rather than inventing net=0/groups=().
    worst=max(reports,key=lambda r:abs(r.net.amount));worst_groups={g.group_id:g for g in worst.groups}
    ids={g.group_id for r in reports for g in r.groups};groups=[]
    for key in sorted(ids):
        gross=max((g.gross.amount for r in reports for g in r.groups if g.group_id==key),default=D(0))
        net=worst_groups[key].net.amount if key in worst_groups else D(0)
        groups.append(t.ExposureGroup(group_id=key,gross=t.Money(amount=gross,currency=valuation.currency),
            net=t.Money(amount=net,currency=valuation.currency)))
    report=t.ExposureReport(gross=t.Money(amount=sum(g.gross.amount for g in groups),currency=valuation.currency),
        net=worst.net,groups=tuple(groups),missing=())
    return report,initial


def _protection(inputs,terms,plan,a,book,facts):
    if a.delta.target.value==0:return 0
    require(terms.stop_price is not None and terms.stop_price>0,'STOP_REQUIRED')
    required={'PARTIAL_FILL','FULL_FILL'}
    if a.delta.actual.value:required.add('RETAINED_POSITION')
    if any(n.phase=='AFTER_NET_RECEIPT' for n in a.nodes):required.add('RECEIPT_NET')
    covered=set();now=utc_timestamp(inputs.clock.utc_now);orders=0;seen=set()
    for proof in facts.protection:
        require(type(proof) is ProtectionCoverage,'PROGRAM_RISK_PROTECTION_PROOF_REQUIRED')
        require(proof.scope==terms.scope and proof.owner_strategy_id==terms.owner_strategy_id
            and proof.ownership_epoch==terms.ownership_epoch and proof.program_hash==plan.program_hash
            and proof.allocation_hash==digest(a) and proof.book_hash==digest(book)
            and proof.maximum_quantity.unit==terms.target.unit
            and proof.maximum_quantity.value>=max(terms.target.value,a.delta.actual.value.copy_abs())
            and proof.stop_price.value==terms.stop_price and proof.stop_price.currency==inputs.instrument.quote_currency
            and proof.stop_trigger==terms.stop_trigger and set(proof.node_ids)=={n.original.node_id for n in plan.nodes}
            and _valid_refs(proof.duty_refs,terms.scope,now) and set(proof.duty_refs)<=set(facts.evidence)
            and now<utc_timestamp(proof.valid_until),'PROGRAM_RISK_PROTECTION_BINDING_MISMATCH')
        require(proof.origin in ('NATIVE_PER_FILL_DUTY','HOSTED_CASH_DUTY','NATIVE_FULL_FILL_ONLY'),'PROGRAM_RISK_PROTECTION_ORIGIN_UNKNOWN')
        require(type(proof.maximum_live_orders) is int and proof.maximum_live_orders>=1,'PROGRAM_RISK_PROTECTION_COUNT_UNBOUND')
        refs=tuple((r.id,r.hash) for r in proof.duty_refs)
        require(not seen.intersection(refs),'PROGRAM_RISK_PROTECTION_REFERENCE_REUSED')
        seen.update(refs);orders+=proof.maximum_live_orders
        if proof.origin=='NATIVE_FULL_FILL_ONLY':
            require('PARTIAL_FILL' not in proof.phases,'NATIVE_FULL_FILL_PROTECTION_GAP')
        if proof.origin=='HOSTED_CASH_DUTY':
            require(terms.scope.product!=t.Product.USD_M_PERP and terms.protection_policy=='HOSTED_ALLOWED_CASH_ONLY',
                'HOSTED_PROTECTION_NOT_AUTHORIZED')
        covered.update(proof.phases)
    require(required<=covered,'PROGRAM_RISK_PARTIAL_OR_RETAINED_PROTECTION_MISSING')
    return orders


def _metrics(inputs,terms,plan,a,e,book,policy,facts,bounds,valuation,reducing,codes):
    require(inputs.fee_reserve is not None and inputs.funding_reserve is not None,'FEE_OR_FUNDING_EVIDENCE_MISSING')
    fee_total=_aggregate_nodes(plan,bounds,lambda b:tuple(f.amount for f in b.execution_fees),facts.budget_facts)
    external=_aggregate_nodes(plan,bounds,lambda b:tuple(f.amount for f in b.execution_fees if f.debit=='EXTERNAL_BALANCE'),facts.budget_facts)
    extra_external=[]
    for fee in facts.additional_exit_fees:
        require(type(fee) is FeeBound and fee.debit in ('EXTERNAL_BALANCE','FROM_RECEIPT'),'PROGRAM_RISK_EXIT_FEE_INVALID')
        nonnegative(fee.amount.amount);fee_total[fee.amount.currency]=fee_total.get(fee.amount.currency,D(0))+fee.amount.amount
        if fee.debit=='EXTERNAL_BALANCE':extra_external.append(fee.amount)
        else:require(terms.scope.product!=t.Product.USD_M_PERP and fee.amount.currency==inputs.instrument.quote_currency,'PROGRAM_RISK_EXIT_RECEIPT_FEE_INVALID')
    funding_assets=_aggregate_nodes(plan,bounds,lambda b:b.funding_reserve,facts.budget_facts)
    require(_sum_assets(inputs.fee_reserve)==fee_total,'PROGRAM_RISK_FEE_BOUND_MISMATCH')
    require(_sum_assets(inputs.funding_reserve)==funding_assets,'PROGRAM_RISK_FUNDING_BOUND_MISMATCH')
    value=lambda assets:sum((valuation.convert(m,liability=True).amount for m in _money(assets)),D(0))
    fees=value(fee_total);funding=value(funding_assets)
    required=_sum_assets(e.additional_requirements,_money(funding_assets),tuple(extra_external))
    capital_assets=amounts(e.additional_requirements)
    for asset,fee in external.items():
        capital_assets[asset]=capital_assets.get(asset,D(0))-fee
        require(capital_assets[asset]>=0,'PROGRAM_RISK_FEE_RESERVATION_UNBOUND')
    capital=value(capital_assets)
    _stop_validity(inputs,terms,plan,a,facts)
    report,initial=_portfolio(inputs,terms,plan,a,e,book,facts,bounds,valuation)
    protection_orders=_protection(inputs,terms,plan,a,book,facts)
    require(facts.maximum_live_orders>=len(plan.nodes)+sum(b.maximum_live_children for b in bounds.values())+protection_orders,
        'PROGRAM_RISK_ORDER_COUNT_UNDERBOUND')
    quote=inputs.instrument.quote_currency;mult=inputs.multiplier
    per_order=[];loss_quote=D(0)
    current_mark=next(m for m in inputs.marks if m.scope==terms.scope)
    if terms.target.value and reducing:
        # A reduction leaves physical inventory at risk. Fees-only applies to a
        # genuinely flat target, never to a still-open long/short residual.
        adverse=current_mark.price.value-terms.stop_price if a.delta.target.value>0 else terms.stop_price-current_mark.price.value
        loss_quote=terms.target.value*mult*(adverse+current_mark.price.value*facts.entry_slippage_bps/10000)
    elif not reducing and a.delta.actual.value:
        require(terms.stop_price is not None and (terms.stop_price<current_mark.price.value
            if a.delta.actual.value>0 else terms.stop_price>current_mark.price.value),'INVALID_STOP')
        adverse=current_mark.price.value-terms.stop_price if a.delta.actual.value>0 else terms.stop_price-current_mark.price.value
        loss_quote+=abs(a.delta.actual.value)*mult*(max(adverse,D(0))+current_mark.price.value*facts.entry_slippage_bps/10000)
    for node in initial:
        bound=bounds[node.node_id];quantity=node.ledger_quantity.value
        notional=quantity*mult*bound.maximum_price.value;per_order.append(notional)
        if terms.scope.product==t.Product.USD_M_PERP and not reducing:
            margin=amounts(bound.margin_reserve or ())
            require(margin.get(quote,D(0))>=legacy_margin_upper_bound(notional,terms.requested_leverage),'PROGRAM_RISK_MARGIN_UNDERBOUND')
        if not reducing:
            require(terms.stop_price is not None,'STOP_REQUIRED')
            entry=bound.maximum_price.value if node.side=='BUY' else bound.minimum_price.value
            require(terms.stop_price<bound.minimum_price.value if node.side=='BUY' else terms.stop_price>bound.maximum_price.value,'INVALID_STOP')
            adverse=entry-terms.stop_price if node.side=='BUY' else terms.stop_price-entry
            loss_quote+=quantity*mult*(adverse+entry*facts.entry_slippage_bps/10000)
    # Existing working/pending loss is separately supplied as a complete bound
    # for prior obligations; it is not manufactured from reservation currency.
    loss=valuation.convert(t.Money(amount=loss_quote,currency=quote),liability=True).amount+fees+funding
    notional=valuation.convert(t.Money(amount=max(per_order,default=D(0)),currency=quote),liability=True).amount
    if not reducing:
        codes.extend(protection_policy_codes(product=terms.scope.product,protection_policy=terms.protection_policy,policy=policy))
        require(terms.maximum_planned_loss.currency==valuation.currency,'PLANNED_LOSS_CURRENCY_MISMATCH')
        codes.extend(portfolio_limit_codes(notional=notional,loss=loss,requested_loss=str(terms.maximum_planned_loss.amount),
            exposure=report,existing_loss=valuation.convert(inputs.existing_planned_loss,liability=True).amount,
            group_limits=inputs.group_limits,position_count=sum(p.actual_quantity.value!=0 for p in book.positions)
                +int(not any(p.scope==terms.scope and p.actual_quantity.value!=0 for p in book.positions) and terms.target.value!=0),
            active_order_count=len(book.active_orders)+len(facts.target_facts.inputs.pending)+facts.maximum_live_orders,policy=policy))
        if capital_limit_exceeded(capital=capital,fees=fees,funding=funding,
            capital_in_use=valuation.convert(inputs.capital_in_use,liability=True).amount,policy=policy):codes.append('REPLAN:CAPITAL')
    codes.extend(spendable_codes(required_assets=required,spendable=inputs.spendable,book=book))
    metrics=RiskMetrics(source=terms.source,allocation_hash=digest(a),facts_hash=digest(facts),book_hash=digest(book),
        book_sequence=book.sequence,basis_fingerprint=book.basis_fingerprint,snapshot_ref=facts.market.ref,
        evaluated_at=inputs.clock.utc_now,valid_until=min((terms.valid_until,facts.valid_until),key=utc_timestamp),
        maximum_order_notional=t.Money(amount=notional,currency=valuation.currency),planned_loss=t.Money(amount=loss,currency=valuation.currency),
        fees=t.Money(amount=fees,currency=valuation.currency),funding=t.Money(amount=funding,currency=valuation.currency),
        capital=t.Money(amount=capital,currency=valuation.currency),required_assets=_money(required),exposure=report,related_groups=report.groups,
        protection_complete=True,holding_horizon_seconds=facts.holding_horizon_seconds,
        open_position_count=sum(p.actual_quantity.value!=0 for p in book.positions)+int(a.delta.actual.value==0 and terms.target.value!=0),
        active_order_count=len(book.active_orders)+len(facts.target_facts.inputs.pending)+facts.maximum_live_orders,
        financial_coverage=book.coverage,evidence=book.evidence)
    codes.extend(metric_binding_codes(terms,metrics,book,facts.market.ref,inputs.clock))
    return metrics


def _stop_validity(inputs,terms,plan,a,facts):
    if terms.target.value==0:return
    stop=terms.stop_price;spec=inputs.instrument
    require(isinstance(stop,D) and stop.is_finite() and stop>0 and on_grid(stop,spec.tick_size.value)
        and facts.profile.price.contains(stop),'INVALID_STOP')
    require(terms.stop_trigger in facts.allowed_stop_triggers,'STOP_TRIGGER_UNVERIFIED')
    if terms.protection_policy=='NATIVE_REQUIRED':
        if terms.scope.product==t.Product.SPOT:
            native={'LAST':'LAST'}.get(terms.stop_trigger);capability='SPOT-STOP_LOSS';order_type='STOP_LOSS'
        elif terms.scope.product==t.Product.USD_M_PERP:
            native={'LAST':'CONTRACT_PRICE','MARK':'MARK_PRICE'}.get(terms.stop_trigger)
            capability='USDM-STOP_MARKET';order_type='STOP_MARKET'
        else:native=None;capability=None;order_type=None
        require(native is not None and capability in facts.capabilities.capability_ids
            and order_type in facts.profile.allowed_order_types,
            'NATIVE_STOP_TRIGGER_UNSUPPORTED')
    else:
        require(terms.scope.product!=t.Product.USD_M_PERP and terms.stop_trigger in ('LAST','BID_ASK'),
            'HOSTED_STOP_TRIGGER_UNSUPPORTED')
    if a.delta.actual.value:
        mark=next((m for m in inputs.marks if m.scope==terms.scope),None)
        require(mark is not None and (stop<mark.price.value if a.delta.target.value>0 else stop>mark.price.value),'INVALID_STOP')
