"""Current explicit-band risk through shared policies and exposure calculation.

Scenario DeltaPlans below describe hypothetical fills, never a replacement
scalar goal or a durable intent. A successful mathematical assessment cannot
authenticate an execution bound, reserve money, or authorize an external write.
"""
from dataclasses import dataclass,replace
from decimal import Decimal,DecimalException
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp,decimal_string
from trading_system.contracts.quote_position_v1 import AbsolutePositionValueBandV1
from trading_system.intents.quote_target_allocation import QuoteTargetFactsV1,QuoteTargetAllocationV1,compile_quote_target
from trading_system.intents.quote_program_compilation import QuoteProgramCompilationV1
from trading_system.intents.semantic_validation import on_grid
from trading_system.market.freshness import FreshnessPolicy
from trading_system.market.snapshots import snapshot_content_hash
from .evaluator import RiskInputs
from .terms import RiskSource,RiskMetrics,_bound_metric_codes
from .bindings import RiskBinding,initial_binding_codes
from .program_assessment import WorkingRiskBound,PendingRiskBound,ProtectionCoverage,_valid_refs,_sum_assets,_money
from .program_exposure import FeeBound
from .exposure import ExposureCalculator,WorkingExposure,PendingExposure
from .policy_checks import (exact_risk_arithmetic,account_policy_codes,protection_policy_codes,
    portfolio_limit_codes,spendable_codes,capital_limit_exceeded)
from .valuation import Valuation,nonnegative

D=Decimal
def digest(value):return canonical_hash(domain_payload(value))

class _DataError(ValueError):pass
def need(ok,code):
    if not ok:raise _DataError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteRiskTermsV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_RISK_TERMS_V1']
    source:RiskSource
    decision_id:str
    scope:t.ScopeKey
    authorization_id:str
    owner_strategy_id:str
    ownership_epoch:int
    policy_revision:int
    collateral_group_id:str
    basis_fingerprint:str
    snapshot_id:str
    action:Literal['SET_TARGET']
    goal:AbsolutePositionValueBandV1
    quantity_unit:Literal['BASE','SHARE']
    requested_leverage:int
    maximum_planned_loss:t.Money
    protection_policy:str
    stop_price:Decimal|None
    stop_trigger:str
    maximum_holding_seconds:int
    review_due_at:str
    as_of:str
    created_at:str
    valid_until:str
    submission_deadline:str

    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteRiskFactsV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_RISK_FACTS_V1']
    inputs:RiskInputs
    market:t.SnapshotPacket
    authorization:t.AuthorizationDecision
    target_facts:QuoteTargetFactsV1
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
class QuoteRiskDecisionV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_RISK_DECISION_V1']
    risk_decision_id:str
    source:RiskSource
    terms_hash:str
    compilation_hash:str
    allocation_hash:str
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


def quote_binding(terms):
    need(type(terms) is QuoteRiskTermsV1 and terms.contract_version=='2026-10-30'
        and terms.record_kind=='QUOTE_RISK_TERMS_V1','QUOTE_RISK_TERMS_REQUIRED')
    return RiskBinding(**{name:getattr(terms,name) for name in RiskBinding.__dataclass_fields__})


def evaluate_quote_program(inputs,terms,plan,allocation,book,policy,facts):
    codes=[];metrics=None
    try:
        with exact_risk_arithmetic():
            binding=quote_binding(terms)
            need(type(facts) is QuoteRiskFactsV1 and facts.contract_version=='2026-10-30'
                and facts.record_kind=='QUOTE_RISK_FACTS_V1','QUOTE_RISK_FACTS_REQUIRED')
            codes.extend(initial_binding_codes(binding,inputs,book,facts.market,policy))
            if not codes:metrics=_derive(inputs,terms,plan,allocation,book,policy,facts,codes)
    except _DataError as error:codes.append(str(error))
    except DecimalException:codes.append('ARITHMETIC_PRECISION_UNPROVEN')
    except (ValueError,TypeError,AttributeError,ArithmeticError):codes.append('QUOTE_RISK_INPUT_INVALID')
    if codes:metrics=None
    verdict=('REPLAN_REQUIRED' if all(c.startswith('REPLAN:') for c in codes) else 'DENY') if codes else 'ALLOW'
    decision=QuoteRiskDecisionV1(contract_version='2026-10-30',record_kind='QUOTE_RISK_DECISION_V1',
        risk_decision_id='unassigned',source=terms.source,terms_hash=digest(terms),compilation_hash=digest(plan),
        allocation_hash=digest(allocation),facts_hash=digest(facts),policy_hash=digest(policy),
        evaluated_at=inputs.clock.utc_now,expires_at=min((terms.valid_until,facts.valid_until,inputs.expires_at),key=utc_timestamp),
        verdict=verdict,metrics=metrics,rejection_codes=tuple(dict.fromkeys(codes)),
        obligations=('MATHEMATICAL_RISK_ONLY','AUTHENTIC_CURRENT_FACT_READERS_REQUIRED',
            'SAME_FINANCIAL_TRANSACTION_AND_ATTACHMENT_REQUIRED','NO_COMMAND_OR_PERMIT_CREATED',
            'UNMET_GOAL_MANAGEMENT_REQUIRED','PRESERVE_RAW_OVERSHOOTS_AND_RESIDUAL_DUTIES'))
    payload=domain_payload(decision);del payload['risk_decision_id']
    return replace(decision,risk_decision_id=canonical_hash(payload))


def _derive(i,terms,plan,a,book,policy,f,codes):
    need(type(plan) is QuoteProgramCompilationV1 and type(a) is QuoteTargetAllocationV1,'QUOTE_RISK_SOURCE_REQUIRED')
    p=plan.original_proposal;tf=f.target_facts;s=tf.scope_facts;b=tf.bounds;scope=terms.scope;now=utc_timestamp(i.clock.utc_now)
    need(scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),'ENVIRONMENT_DISABLED')
    need(type(tf) is QuoteTargetFactsV1 and digest(f.inputs)==digest(i),'QUOTE_RISK_INPUT_BINDING_MISMATCH')
    need(terms.authorization_id==s.context.authorization_id and terms.owner_strategy_id==s.owner.owner_strategy_id
        and terms.ownership_epoch==s.owner.ownership_epoch==s.context.ownership_epoch
        and terms.policy_revision==s.owner.policy_revision==s.context.policy_revision
        and terms.collateral_group_id==s.collateral_group_id,'QUOTE_SCOPE_AUTHORITY_MISMATCH')
    need(terms.source==RiskSource(kind='POSITION_VALUE_BAND_PROPOSAL',version=p.contract_version,hash=plan.proposal_hash)
        and terms.goal==p.goal and terms.action==p.action=='SET_TARGET'
        and terms.requested_leverage==p.requested_leverage and terms.protection_policy==p.protection_policy
        and terms.stop_price==(None if p.exit_plan.stop_price is None else decimal_string(p.exit_plan.stop_price))
        and terms.stop_trigger==p.exit_plan.stop_trigger and terms.review_due_at==p.exit_plan.review_due_at
        and terms.maximum_holding_seconds==p.exit_plan.maximum_holding_seconds
        and terms.submission_deadline==p.program.submit_valid_until,'QUOTE_RESPONSIBILITY_MISMATCH')
    need(digest(compile_quote_target(plan,book,tf))==digest(a),'QUOTE_ALLOCATION_CHANGED')
    need(not a.blockers,a.blockers[0] if a.blockers else 'QUOTE_ALLOCATION_BLOCKED')
    need(i.instrument==s.inputs.instrument and i.session==s.inputs.session and i.clock==s.inputs.clock
        and i.order_sides==s.inputs.order_sides and i.allow_round_down==s.inputs.allow_round_down
        and s.context.snapshot_ref==f.market.ref and i.multiplier==s.unit_binding.ledger_multiplier
        and i.base_asset==s.rules.base_asset and terms.quantity_unit==a.interval.maximum.unit,
        'QUOTE_TARGET_INPUT_BINDING_MISMATCH')
    need(_valid_refs(f.evidence,scope,now) and _valid_refs(book.evidence,scope,now),'QUOTE_RISK_EVIDENCE_REQUIRED')
    need(utc_timestamp(f.as_of)<=now<utc_timestamp(f.valid_until)
        and utc_timestamp(terms.valid_until)<=min(map(utc_timestamp,(a.valid_until,f.valid_until,i.expires_at))),
        'QUOTE_RISK_WINDOW_UNBOUND')
    need(f.market.ref.hash==snapshot_content_hash(f.market),'QUOTE_MARKET_HASH_MISMATCH')
    need(f.authorization.allowed is True and not f.authorization.rejection_codes
        and f.authorization.authorization_id==terms.authorization_id
        and _valid_refs(f.authorization.evidence,scope,now),'AUTHORIZATION_REQUIRED')
    codes.extend(FreshnessPolicy({scope.product:policy.maximum_quote_age_ms}).evaluate(f.market,i.clock,i.session).rejection_codes)
    need(type(f.entry_slippage_bps) is D and 0<=f.entry_slippage_bps<=decimal_string(policy.maximum_entry_slippage_bps),
        'QUOTE_SLIPPAGE_BOUND_REQUIRED')
    bid,ask=f.market.quote.bid,f.market.quote.ask
    need(bid is not None and ask is not None and bid.currency==ask.currency==i.instrument.quote_currency
        and 0<bid.value<=ask.value and (ask.value-bid.value)*10000<=bid.value*f.entry_slippage_bps,'QUOTE_SPREAD_LIMIT')
    need(type(f.holding_horizon_seconds) is int and f.holding_horizon_seconds>=terms.maximum_holding_seconds>0,
        'QUOTE_HOLDING_HORIZON_UNDERBOUND')
    reducing=a.position.classification=='REDUCE'
    if not reducing:need(i.new_risk_allowed is True,'NEW_RISK_DISABLED')
    codes.extend(account_policy_codes(product=scope.product,venue_leg=scope.venue_leg,
        target=a.position.projected_minimum.value,leverage=terms.requested_leverage,multiplier=i.multiplier,
        reducing=reducing,policy=policy,authorized_margin_mode=i.authorized_margin_mode,
        observed_margin_mode=i.observed_margin_mode,authorized_position_mode=i.authorized_position_mode,
        observed_position_mode=i.observed_position_mode))
    codes.extend(protection_policy_codes(product=scope.product,protection_policy=terms.protection_policy,policy=policy))
    valuation=Valuation(policy.valuation_currency,i.fx_rates)
    need(all(rate.evidence_id in {r.id for r in i.evidence} for rate in i.fx_rates),'FX_EVIDENCE_UNBOUND')
    report=_portfolio(i,terms,a,book,f,valuation)
    stop=terms.stop_price
    need(type(stop) is D and stop>0 and on_grid(stop,i.instrument.tick_size.value)
        and tf.profile.price.contains(stop),'INVALID_STOP')
    need(type(f.allowed_stop_triggers) is tuple and terms.stop_trigger in f.allowed_stop_triggers,'STOP_TRIGGER_UNVERIFIED')
    if terms.protection_policy=='NATIVE_REQUIRED':
        need(scope.product==t.Product.SPOT and terms.stop_trigger=='LAST'
            and 'SPOT-STOP_LOSS' in tf.capabilities.capability_ids and 'STOP_LOSS' in tf.profile.allowed_order_types,
            'NATIVE_STOP_TRIGGER_UNSUPPORTED')
    else:need(scope.product!=t.Product.USD_M_PERP and terms.stop_trigger in ('LAST','BID_ASK'),'HOSTED_STOP_TRIGGER_UNSUPPORTED')
    mark=next(m for m in i.marks if m.scope==scope)
    price=max(mark.price.value,b.maximum_price.value)
    need(stop<min(mark.price.value,b.minimum_price.value),'INVALID_STOP')
    retained=max(abs(v.value) for v in (a.position.baseline_minimum,a.position.baseline_maximum,
        a.position.projected_minimum,a.position.projected_maximum,a.scope_state.actual))
    protection_count=_protection(terms,plan,a,book,f,retained,now)
    need(type(f.maximum_live_orders) is int and f.maximum_live_orders>=1+protection_count,'QUOTE_ORDER_COUNT_UNDERBOUND')
    need(type(f.additional_exit_fees) is tuple,'QUOTE_EXIT_FEE_FACTS_REQUIRED')
    for fee in f.additional_exit_fees:
        need(type(fee) is FeeBound and fee.debit in ('FROM_RECEIPT','EXTERNAL_BALANCE')
            and (fee.debit!='FROM_RECEIPT' or fee.amount.currency==s.rules.quote_asset),'QUOTE_EXIT_FEE_BOUND_INVALID')
        nonnegative(fee.amount.amount)
    fees_by_asset=_sum_assets(a.cash.fee_liabilities,tuple(fee.amount for fee in f.additional_exit_fees))
    need(i.fee_reserve is not None and _sum_assets(i.fee_reserve)==fees_by_asset,'QUOTE_FEE_RESERVE_MISMATCH')
    need(type(i.funding_reserve) is tuple and not i.funding_reserve,'QUOTE_CASH_FUNDING_BOUND_REQUIRED')
    value=lambda assets:sum((valuation.convert(m,liability=True).amount for m in _money(assets)),D(0))
    fees=value(fees_by_asset);capital=valuation.convert(t.Money(currency=s.rules.quote_asset,
        amount=b.maximum_gross_quote.amount if a.cash.side=='BUY' else D(0)),liability=True).amount
    required=_sum_assets(a.cash.required_assets,tuple(fee.amount for fee in f.additional_exit_fees if fee.debit=='EXTERNAL_BALANCE'))
    loss_quote=retained*i.multiplier*(price-stop+price*f.entry_slippage_bps/10000)
    loss=valuation.convert(t.Money(currency=s.rules.quote_asset,amount=loss_quote),liability=True).amount+fees
    notional=valuation.convert(b.maximum_gross_quote,liability=True).amount
    need(terms.maximum_planned_loss.currency==valuation.currency,'PLANNED_LOSS_CURRENCY_MISMATCH')
    positions=sum(p.actual_quantity.value!=0 for p in book.positions)+int(a.scope_state.actual.value==0 and retained>0)
    count=len(book.active_orders)+len(s.inputs.pending)+f.maximum_live_orders
    codes.extend(portfolio_limit_codes(notional=notional,loss=loss,requested_loss=str(terms.maximum_planned_loss.amount),
        exposure=report,existing_loss=valuation.convert(i.existing_planned_loss,liability=True).amount,
        group_limits=i.group_limits,position_count=positions,active_order_count=count,policy=policy))
    if capital_limit_exceeded(capital=capital,fees=fees,funding=D(0),
        capital_in_use=valuation.convert(i.capital_in_use,liability=True).amount,policy=policy):codes.append('REPLAN:CAPITAL')
    codes.extend(spendable_codes(required_assets=required,spendable=i.spendable,book=book))
    money=lambda amount:t.Money(currency=valuation.currency,amount=amount)
    metrics=RiskMetrics(source=terms.source,allocation_hash=digest(a),facts_hash=digest(f),book_hash=digest(book),
        book_sequence=book.sequence,basis_fingerprint=book.basis_fingerprint,snapshot_ref=f.market.ref,
        evaluated_at=i.clock.utc_now,valid_until=min((terms.valid_until,f.valid_until),key=utc_timestamp),
        maximum_order_notional=money(notional),planned_loss=money(loss),fees=money(fees),funding=money(D(0)),
        capital=money(capital),required_assets=_money(required),exposure=report,related_groups=report.groups,
        protection_complete=True,holding_horizon_seconds=f.holding_horizon_seconds,
        open_position_count=positions,active_order_count=count,financial_coverage=book.coverage,evidence=book.evidence)
    codes.extend(_bound_metric_codes(terms,metrics,book,f.market.ref,i.clock))
    return metrics


def _portfolio(i,terms,a,book,f,valuation):
    need(type(f.working_bounds) is tuple,'QUOTE_WORKING_COVERAGE_INCOMPLETE')
    need(type(f.pending_bounds) is tuple,'QUOTE_PENDING_COVERAGE_INCOMPLETE')
    scope=terms.scope;marks={m.scope:m for m in i.marks};bound=f.target_facts.bounds
    need(scope in marks and marks[scope].unit==terms.quantity_unit and marks[scope].multiplier==i.multiplier,
        'QUOTE_VALUATION_MARK_MISSING')
    def mark_for(key,upper):
        need(key in marks and upper is not None and upper.currency==marks[key].price.currency,'QUOTE_PORTFOLIO_BOUND_MISSING')
        return replace(marks[key],price=upper)
    current={(o.order.identity.scope,o.order.identity.logical_client_order_id):o for o in book.active_orders}
    working={(b.scope,b.logical_client_order_id):b for b in f.working_bounds}
    need(len(working)==len(f.working_bounds) and set(working)==set(current),'QUOTE_WORKING_COVERAGE_INCOMPLETE')
    ws=[]
    for key,o in current.items():
        b=working[key]
        need(o.command_certainty!='UNKNOWN','UNKNOWN_ORDER_RECONCILIATION_REQUIRED')
        need(b.quantity==o.leaves_quantity and b.side in ('BUY','SELL') and b.evidence_refs
            and set(b.evidence_refs)<={r.id for r in f.evidence},'QUOTE_WORKING_BOUND_MISMATCH')
        ws.append(WorkingExposure(scope=b.scope,logical_client_order_id=b.logical_client_order_id,side=b.side,
            mark=mark_for(b.scope,b.maximum_price)))
    pending=f.target_facts.scope_facts.inputs.pending;indexed={b.obligation_id:b for b in f.pending_bounds}
    need(len(indexed)==len(f.pending_bounds) and set(indexed)=={p.intent_id for p in pending},'QUOTE_PENDING_COVERAGE_INCOMPLETE')
    ps=[]
    for p in pending:
        b=indexed[p.intent_id]
        need(b.scope==scope and b.canonical_command_refs==p.command_refs and b.original_command_refs
            and len(b.original_command_refs)==len(b.canonical_command_refs) and b.evidence_refs
            and set(b.evidence_refs)<={r.id for r in f.evidence},'QUOTE_PENDING_BOUND_MISMATCH')
        ps.append(PendingExposure(obligation_id=p.intent_id,scope=scope,signed_quantity=p.signed_quantity,
            command_refs=b.original_command_refs,represented_order_ids=p.represented_order_ids,
            source_family=b.source_family,service_namespace=b.service_namespace,mark=mark_for(scope,b.maximum_price)))
    marked=tuple(replace(m,price=t.Price(currency=m.price.currency,value=max(m.price.value,bound.maximum_price.value)))
        if m.scope==scope else m for m in i.marks)
    actual=a.scope_state.actual;working=a.scope_state.working;reports=[]
    for endpoint in (D(0),a.cash.position_delta_minimum.value,a.cash.position_delta_maximum.value):
        quantity=lambda v:t.Quantity(unit=terms.quantity_unit,value=v)
        # This is a hypothetical fill scenario, never the user's goal.
        delta=t.DeltaPlan(scope=scope,target=quantity(actual.value+working.value+endpoint),actual=actual,working=working,
            remaining=quantity(endpoint),classification='NONE' if endpoint==0 else a.position.classification,
            cancel_first=())
        reports.append(ExposureCalculator(valuation,marked,i.order_sides,pending=tuple(ps),working=tuple(ws)).project(book,delta))
    need(all(not r.missing for r in reports),next((code for r in reports for code in r.missing),'QUOTE_PORTFOLIO_INCOMPLETE'))
    worst=max(reports,key=lambda r:abs(r.net.amount));ids=sorted({g.group_id for r in reports for g in r.groups})
    groups=tuple(t.ExposureGroup(group_id=key,
        gross=t.Money(currency=valuation.currency,amount=max(g.gross.amount for r in reports for g in r.groups if g.group_id==key)),
        net=t.Money(currency=valuation.currency,amount=next((g.net.amount for g in worst.groups if g.group_id==key),D(0)))) for key in ids)
    return t.ExposureReport(gross=t.Money(currency=valuation.currency,amount=sum((g.gross.amount for g in groups),D(0))),
        net=worst.net,groups=groups,missing=())


def _protection(terms,plan,a,book,f,quantity,now):
    need(type(f.protection) is tuple,'QUOTE_PARTIAL_OR_RETAINED_PROTECTION_MISSING')
    required={'PARTIAL_FILL','FULL_FILL','RECEIPT_NET'}
    if a.scope_state.actual.value:required.add('RETAINED_POSITION')
    covered=set();seen=set();orders=0
    for proof in f.protection:
        need(type(proof) is ProtectionCoverage and proof.scope==terms.scope
            and proof.owner_strategy_id==terms.owner_strategy_id and proof.ownership_epoch==terms.ownership_epoch
            and proof.program_hash==plan.program_hash and proof.allocation_hash==digest(a) and proof.book_hash==digest(book)
            and proof.maximum_quantity.unit==terms.quantity_unit and proof.maximum_quantity.value>=quantity
            and proof.stop_price.value==terms.stop_price and proof.stop_price.currency==f.inputs.instrument.quote_currency
            and proof.stop_trigger==terms.stop_trigger and set(proof.node_ids)=={n.original.node_id for n in plan.nodes}
            and _valid_refs(proof.duty_refs,terms.scope,now) and set(proof.duty_refs)<=set(f.evidence)
            and now<utc_timestamp(proof.valid_until),'QUOTE_PROTECTION_BINDING_MISMATCH')
        need(proof.origin in ('NATIVE_PER_FILL_DUTY','HOSTED_CASH_DUTY'),'QUOTE_PARTIAL_OR_RETAINED_PROTECTION_MISSING')
        if proof.origin=='HOSTED_CASH_DUTY':need(terms.protection_policy=='HOSTED_ALLOWED_CASH_ONLY','HOSTED_PROTECTION_NOT_AUTHORIZED')
        refs={(r.id,r.hash) for r in proof.duty_refs}
        need(not refs.intersection(seen) and type(proof.maximum_live_orders) is int and proof.maximum_live_orders>=1,
            'QUOTE_PROTECTION_REFERENCE_OR_COUNT_INVALID')
        seen.update(refs);orders+=proof.maximum_live_orders;covered.update(proof.phases)
    need(required<=covered,'QUOTE_PARTIAL_OR_RETAINED_PROTECTION_MISSING')
    return orders
