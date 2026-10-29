"""Synthetic source facts exercising real generic risk math, never venue proof."""
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
import pytest

from test_program_target_allocation import plan_for,scenario,NOW,END
from test_program_compilation import inputs,change,trigger,price as order_price
from test_program_exposure import facts_for
from unit.support_ip_06 import make_case,money,price
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.market.snapshots import snapshot_content_hash
from trading_system.intents.program_target_allocation import compile_program_target
from trading_system.risk.evaluator import RiskEvaluator,GroupLimit
from trading_system.risk.program_exposure import derive_program_exposure
from trading_system.risk.terms import RiskTerms,RiskSource
from trading_system.risk.valuation import FxRate
from trading_system.risk.program_assessment import ProgramRiskFacts,NodeRiskBound,ProtectionCoverage,WorkingRiskBound,PendingRiskBound
from trading_system.intents.program_compilation import compile_order_program,Namespace
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_program import QuantitySpec,TimeInForceSpec
from trading_system.intents.target_compiler import PendingQuantity
from trading_system.risk.program_exposure import FeeBound


def digest(value):return canonical_hash(domain_payload(value))


def risk_case(*,template='ATOMIC',mode='VENUE_NATIVE',product=t.Product.SPOT,actual='0',target='1',quantity='1',side='BUY',
              stop=None,target_side=None,conditional=False,close_all=False,working='0',pending=()):
    plan,rules=plan_for(template=template,mode=mode,product=product,quantity=quantity,target=target,side=side)
    _,_,caps,k=inputs(product);profile=k['profile'];p=plan.original_proposal
    if stop is not None:p=replace(p,exit_plan=replace(p.exit_plan,stop_price=stop))
    if target_side is not None:p=replace(p,target_side=target_side)
    if conditional or close_all:
        p=change(p,price=order_price(),tif=TimeInForceSpec(kind='NONE',expires_at=None),
            trigger=trigger(source='MARK_PRICE' if product==t.Product.USD_M_PERP else 'LAST',price='83000' if side=='BUY' else '81330'))
    if close_all:p=change(p,quantity=QuantitySpec(kind='CLOSE_ALL',value=None,source_node=None))
    if product==t.Product.US_EQUITY:
        scope=replace(plan.scope,instrument_uid='paper:equity:AAPL');rules=replace(rules,scope=scope,base_asset='AAPL',quote_asset='USD')
        caps=replace(caps,scope=scope);profile=replace(profile,scope=scope,venue_symbol='AAPL')
        p=replace(p,instrument_ref=scope.instrument_uid,protection_policy='HOSTED_ALLOWED_CASH_ONLY',
            program=replace(p.program,instrument_ref=scope.instrument_uid))
        k.update(scope=scope,profile=profile,expected_capabilities_hash=VersionedOrderCodec().hash('order-capability-snapshot',caps),
            expected_instrument_ref=scope.instrument_uid)
    plan=compile_order_program(p,rules,caps,**k)
    book,target_facts=scenario(plan,rules,actual=actual,working=working)
    target_facts=replace(target_facts,inputs=replace(target_facts.inputs,pending=pending))
    base=make_case(product);quote=rules.quote_asset;unit=target_facts.unit_binding.ledger_unit
    if product==t.Product.US_EQUITY:
        spec=replace(target_facts.inputs.instrument,quote_currency=quote,settlement_currency=quote,
            tick_size=price(str(target_facts.inputs.instrument.tick_size.value),quote))
        target_facts=replace(target_facts,inputs=replace(target_facts.inputs,instrument=spec,expected_instrument=spec))
    evidence=book.evidence[0]
    market=replace(base.market,scope=book.scope,ref=replace(target_facts.context.snapshot_ref,visible_at=NOW),
        coverage=book.coverage,quote=t.Quote(bid=price('81489',quote),ask=price('81490',quote),last=price('81490',quote),
            occurred_at=NOW,received_at=NOW),published_at=NOW,revision_visible_at=NOW)
    market=replace(market,ref=replace(market.ref,hash=snapshot_content_hash(market)))
    context=replace(target_facts.context,snapshot_ref=market.ref)
    target_facts=replace(target_facts,context=context,inputs=replace(target_facts.inputs,context=context))
    book=replace(book,balances=(money('1000000',quote),money('10',rules.base_asset)))
    target_facts=replace(target_facts,book_hash=digest(book))
    allocation=compile_program_target(plan,book,target_facts)
    budget=replace(facts_for(plan),collateral_group_id=target_facts.collateral_group_id,
        base_asset=rules.base_asset,quote_asset=quote,asset_limits=(money('10000000',quote),money('100',rules.base_asset)))
    rows=[];nodes=[]
    for n,old in zip(plan.nodes,budget.node_facts):
        is_pending=n.original.role=='PENDING'
        upper=D(n.original.order.price.value) if n.original.order.side=='BUY' and n.original.order.price.kind=='FIXED_LIMIT' else D('90000')
        lower=D(n.original.order.price.value) if n.original.order.price.kind=='FIXED_LIMIT' else D('81400')
        margin=(money(str(D(n.worst_case_quote_notional or '90000')),quote),) if product==t.Product.USD_M_PERP and not is_pending and side!='SELL' else ()
        old=replace(old,margin=margin if product==t.Product.USD_M_PERP else None)
        if close_all:old=replace(old,quantity_upper=t.Quantity(value=D(actual),unit=n.quantity_unit),price_upper=price('90000',quote))
        rows.append(old)
        nodes.append(NodeRiskBound(node_id=n.original.node_id,minimum_price=price(str(lower),quote),maximum_price=price(str(upper),quote),
            execution_fees=old.fees,funding_reserve=(),margin_reserve=old.margin,
            holding_horizon_seconds=plan.original_proposal.exit_plan.maximum_holding_seconds,
            maximum_live_children=2 if template in ('TWAP','VP') else 0,evidence_refs=(evidence.id,)))
    budget=replace(budget,node_facts=tuple(rows))
    exposure=derive_program_exposure(plan,budget,now=NOW)
    p=plan.original_proposal;clock=target_facts.inputs.clock;identifier='program-risk-fixture'
    terms=RiskTerms(source=RiskSource(kind='ORDER_PROPOSAL',version=p.contract_version,hash=plan.proposal_hash),
        decision_id=identifier,scope=plan.scope,authorization_id=context.authorization_id,
        owner_strategy_id=target_facts.owner.owner_strategy_id,ownership_epoch=target_facts.owner.ownership_epoch,
        policy_revision=target_facts.owner.policy_revision,collateral_group_id=target_facts.collateral_group_id,
        basis_fingerprint=book.basis_fingerprint,snapshot_id=market.ref.id,action=p.action,target_side=p.target_side,
        target=allocation.terms.target,requested_leverage=p.requested_leverage,maximum_planned_loss=money('50000',quote),
        protection_policy=p.protection_policy,stop_price=D(p.exit_plan.stop_price),stop_trigger=p.exit_plan.stop_trigger,
        maximum_holding_seconds=p.exit_plan.maximum_holding_seconds,review_due_at=p.exit_plan.review_due_at,
        as_of=NOW,created_at=NOW,valid_until=allocation.terms.valid_until,submission_deadline=p.program.submit_valid_until)
    current=replace(base.inputs,intent_id=identifier,authorization_id=context.authorization_id,
        owner_strategy_id=terms.owner_strategy_id,ownership_epoch=terms.ownership_epoch,policy_revision=terms.policy_revision,
        scope=book.scope,collateral_group_id=terms.collateral_group_id,basis_fingerprint=book.basis_fingerprint,
        book_sequence=book.sequence,snapshot_ref=market.ref,clock=clock,expires_at=END,
        instrument=target_facts.inputs.instrument,session=target_facts.inputs.session,multiplier=target_facts.unit_binding.ledger_multiplier,
        base_asset=rules.base_asset,fee_reserve=(),funding_reserve=(),existing_planned_loss=money('1000' if D(working) or pending else '0',quote),
        capital_in_use=money(str((D(actual).copy_abs()+D(working).copy_abs())*D(90000)),quote),
        spendable=book.balances,marks=(replace(base.inputs.marks[0],scope=book.scope,price=price('81490',quote),unit=unit),),
        order_sides=target_facts.inputs.order_sides,group_limits=(GroupLimit(group_id='related',maximum_gross=money('1000000',quote)),),
        fx_rates=(FxRate(source=rules.base_asset,target=quote,asset_rate=D('81489'),liability_rate=D('90000'),
            haircut=D(0),evidence_id=evidence.id),),
        allow_round_down=target_facts.inputs.allow_round_down,evidence=book.evidence)
    policy=replace(base.policy,collateral_group_id=terms.collateral_group_id,valuation_currency=quote,
        maximum_capital='1000000',maximum_order_notional='1000000',
        maximum_planned_loss_per_intent='50000',maximum_total_planned_loss='100000',maximum_gross_exposure='1000000',
        maximum_net_exposure='1000000',maximum_entry_slippage_bps='2000',maximum_active_orders=20)
    coverage=ProtectionCoverage(scope=book.scope,owner_strategy_id=terms.owner_strategy_id,ownership_epoch=terms.ownership_epoch,
        program_hash=plan.program_hash,allocation_hash=digest(allocation),book_hash=digest(book),
        origin='HOSTED_CASH_DUTY' if product==t.Product.US_EQUITY else 'NATIVE_PER_FILL_DUTY',
        phases=('PARTIAL_FILL','FULL_FILL','RECEIPT_NET','RETAINED_POSITION'),node_ids=tuple(n.original.node_id for n in plan.nodes),
        maximum_quantity=terms.target,stop_price=price(str(terms.stop_price),quote),stop_trigger=terms.stop_trigger,
        duty_refs=(evidence,),valid_until=END,maximum_live_orders=1)
    facts=ProgramRiskFacts(inputs=current,market=market,authorization=t.AuthorizationDecision(allowed=True,
        authorization_id=terms.authorization_id,rejection_codes=(),evidence=book.evidence),target_facts=target_facts,budget_facts=budget,
        rules=rules,capabilities=caps,profile=profile,expected_build_digest=plan.adapter_build_digest,
        expected_instrument_ref=p.instrument_ref,node_bounds=tuple(nodes),
        working_bounds=tuple(WorkingRiskBound(logical_client_order_id=o.order.identity.logical_client_order_id,
            scope=o.order.identity.scope,side='BUY',quantity=o.leaves_quantity,minimum_price=price('81400',quote),
            maximum_price=price('81490',quote),evidence_refs=(evidence.id,)) for o in book.active_orders),
        pending_bounds=tuple(PendingRiskBound(obligation_id=prior.intent_id,scope=book.scope,source_family='LEGACY_COMMAND',
            service_namespace='ORDINARY',canonical_command_refs=prior.command_refs,original_command_refs=prior.command_refs,
            minimum_price=price('81400',quote),maximum_price=price('81490',quote),evidence_refs=(evidence.id,)) for prior in pending),
        additional_exit_fees=(),
        protection=(coverage,),allowed_stop_triggers=('LAST','MARK_PRICE','CONTRACT_PRICE'),maximum_live_orders=len(plan.nodes)
            +sum(n.maximum_live_children for n in nodes)+1,
        holding_horizon_seconds=terms.maximum_holding_seconds,entry_slippage_bps=D(2000),evidence=book.evidence,as_of=NOW,valid_until=END)
    policy=replace(policy,protection_policy=p.protection_policy)
    return SimpleNamespace(plan=plan,book=book,allocation=allocation,exposure=exposure,terms=terms,facts=facts,policy=policy)


def assess(c):return RiskEvaluator(c.facts.inputs).evaluate_program(c.terms,c.plan,c.allocation,c.exposure,c.book,c.policy,c.facts)


def rebind(c,*,book=None,market=None,target_facts=None):
    c.book=book or c.book;market=market or c.facts.market;tf=target_facts or c.facts.target_facts
    market=replace(market,ref=replace(market.ref,hash=snapshot_content_hash(market)))
    context=replace(tf.context,snapshot_ref=market.ref)
    tf=replace(tf,context=context,book_hash=digest(c.book),book_sequence=c.book.sequence,
        inputs=replace(tf.inputs,context=context))
    c.allocation=compile_program_target(c.plan,c.book,tf)
    current=replace(c.facts.inputs,book_sequence=c.book.sequence,snapshot_ref=market.ref,
        basis_fingerprint=c.book.basis_fingerprint,order_sides=tf.inputs.order_sides)
    protection=tuple(replace(p,allocation_hash=digest(c.allocation),book_hash=digest(c.book)) for p in (c.facts.protection or ()))
    c.facts=replace(c.facts,target_facts=tf,market=market,inputs=current,protection=protection)
    return c


def test_actual_program_risk_derives_signed_portfolio_and_declared_buy_cost_without_legacy_intent(monkeypatch):
    from trading_system.contracts.wire import ContractCodec,TradeIntent
    c=risk_case();original=ContractCodec.encode
    def no_intent(codec,record):
        assert type(record) is not TradeIntent,'fabricated legacy intent'
        return original(codec,record)
    monkeypatch.setattr(ContractCodec,'encode',no_intent)
    result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.required_assets==(money('81490'),)
    assert result.metrics.exposure.gross.amount==result.metrics.exposure.net.amount==81490
    assert result.metrics.related_groups and result.metrics.facts_hash==digest(c.facts)
    assert not result.execution_authorized


@pytest.mark.parametrize('field',['node_bounds','working_bounds','pending_bounds','additional_exit_fees','protection',
    'allowed_stop_triggers','maximum_live_orders','holding_horizon_seconds','entry_slippage_bps'])
def test_missing_risk_facts_cannot_become_zero_metrics(field):
    c=risk_case();c.facts=replace(c.facts,**{field:None})
    result=assess(c)
    assert result.verdict=='DENY' and result.metrics is None and result.rejection_codes


@pytest.mark.parametrize('template,mode,product,target',[
    ('ATOMIC','VENUE_NATIVE',t.Product.SPOT,'1'),('ATOMIC','VENUE_NATIVE',t.Product.USD_M_PERP,'1'),
    ('TWAP','VENUE_NATIVE',t.Product.SPOT,'1'),('TWAP','LOCAL_SYNTHETIC',t.Product.SPOT,'1'),
    ('VP','VENUE_NATIVE',t.Product.SPOT,'1'),('VP','LOCAL_SYNTHETIC',t.Product.SPOT,'1'),
    ('BATCH','VENUE_NATIVE',t.Product.USD_M_PERP,'2'),('BATCH','LOCAL_SYNTHETIC',t.Product.USD_M_PERP,'2'),
    ('OTO','VENUE_NATIVE',t.Product.SPOT,'1'),('OTOCO','VENUE_NATIVE',t.Product.SPOT,'1'),
    ('OPO','VENUE_NATIVE',t.Product.SPOT,'1'),('OPOCO','VENUE_NATIVE',t.Product.SPOT,'1'),
    ('OTO','LOCAL_SYNTHETIC',t.Product.SPOT,'1'),('OPO','LOCAL_SYNTHETIC',t.Product.SPOT,'1'),
])
def test_real_allocation_patterns_have_derived_metrics_not_cumulative_budget_net(template,mode,product,target):
    c=risk_case(template=template,mode=mode,product=product,target=target)
    result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.exposure.groups and not result.metrics.exposure.missing
    assert sum(g.gross.amount for g in result.metrics.related_groups)==result.metrics.exposure.gross.amount
    assert sum(g.net.amount for g in result.metrics.related_groups)==result.metrics.exposure.net.amount


@pytest.mark.parametrize('target,amount',[('.6','.4'),('0','1')])
def test_actual_partial_and_flat_reductions_use_owned_quantities(target,amount):
    c=risk_case(actual='1',target=target,quantity=amount,side='SELL')
    result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.required_assets==(money(amount,'BTC'),)
    assert not result.execution_authorized


def test_full_fill_only_native_list_is_not_partial_fill_protection():
    c=risk_case(template='OTOCO')
    proof=replace(c.facts.protection[0],origin='NATIVE_FULL_FILL_ONLY',phases=('FULL_FILL',))
    c.facts=replace(c.facts,protection=(proof,))
    result=assess(c)
    assert result.verdict=='DENY' and 'PROGRAM_RISK_PARTIAL_OR_RETAINED_PROTECTION_MISSING' in result.rejection_codes


@pytest.mark.parametrize('template',['OTOCO','OPOCO'])
def test_software_pending_overlap_does_not_borrow_unowned_future_inventory(template):
    c=risk_case(template=template,mode='LOCAL_SYNTHETIC')
    result=assess(c)
    assert result.verdict=='DENY' and 'CONTINGENT_QUANTITY_NOT_OWNED_AT_ACTIVATION' in result.rejection_codes


def test_resting_buy_ask_above_limit_does_not_inflate_principal_or_invent_current_ask_fill():
    c=risk_case();market=replace(c.facts.market,quote=replace(c.facts.market.quote,bid=price('89999'),ask=price('90000')))
    rebind(c,market=market)
    result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.required_assets==(money('81490'),)
    assert result.metrics.exposure.gross.amount==81490


def test_staged_portfolio_is_not_cumulative_buy_plus_sell_turnover_or_fake_zero_net():
    c=risk_case(template='OTOCO');result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.exposure.gross.amount==result.metrics.exposure.net.amount==90000
    assert result.metrics.exposure.gross.amount<c.exposure.gross_after.amount


@pytest.mark.parametrize('fault',['leverage','action','side','submission','window','policy','owner','book','allocation','budget'])
def test_exact_source_and_current_fact_bindings_cannot_be_relabelled(fault):
    c=risk_case()
    if fault=='leverage':c.terms=replace(c.terms,requested_leverage=4)
    if fault=='action':c.terms=replace(c.terms,action='HOLD')
    if fault=='side':c.terms=replace(c.terms,target_side='SHORT')
    if fault=='submission':c.terms=replace(c.terms,submission_deadline=END)
    if fault=='window':c.terms=replace(c.terms,valid_until=END)
    if fault=='policy':c.policy=replace(c.policy,revision=2)
    if fault=='owner':c.facts=replace(c.facts,inputs=replace(c.facts.inputs,ownership_epoch=2))
    if fault=='book':c.book=replace(c.book,sequence=2)
    if fault=='allocation':c.allocation=replace(c.allocation,compilation_hash='f'*64)
    if fault=='budget':c.exposure=replace(c.exposure,program_requirements=())
    result=assess(c)
    assert result.verdict=='DENY' and result.metrics is None and result.rejection_codes


def test_shorter_qualified_target_window_is_legal_and_cannot_be_extended():
    c=risk_case();early='2026-10-28T10:00:05Z'
    tf=replace(c.facts.target_facts,unit_binding=replace(c.facts.target_facts.unit_binding,valid_until=early))
    rebind(c,target_facts=tf)
    assert assess(c).verdict=='DENY'
    c.terms=replace(c.terms,valid_until=early)
    result=assess(c)
    assert result.verdict=='ALLOW' and result.expires_at==early
    assert c.plan.original_proposal.valid_until!=early and c.terms.submission_deadline==c.plan.original_program.submit_valid_until


@pytest.mark.parametrize('field,value',[('maximum_live_orders',1),('holding_horizon_seconds',1),('entry_slippage_bps',D('2001'))])
def test_node_and_protection_counts_horizon_and_slippage_are_real_limits(field,value):
    c=risk_case();c.facts=replace(c.facts,**{field:value})
    result=assess(c)
    assert result.verdict=='DENY' and result.metrics is None


def test_boolean_protection_hint_cannot_approve_risk():
    c=risk_case();c.facts=replace(c.facts,protection=(True,))
    result=assess(c)
    assert result.verdict=='DENY' and result.metrics is None


def test_invalid_actual_stop_and_missing_horizon_fee_bounds_are_not_zero_loss():
    c=risk_case(stop='82000');result=assess(c)
    assert result.verdict=='DENY' and 'INVALID_STOP' in result.rejection_codes
    c=risk_case();node=replace(c.facts.node_bounds[0],funding_reserve=None)
    c.facts=replace(c.facts,node_bounds=(node,))
    assert assess(c).verdict=='DENY'


def test_coherent_zero_derivative_margin_budget_cannot_approve_notional_risk():
    c=risk_case(product=t.Product.USD_M_PERP)
    budget=replace(c.facts.budget_facts,node_facts=(replace(c.facts.budget_facts.node_facts[0],margin=()),))
    c.exposure=derive_program_exposure(c.plan,budget,now=NOW)
    c.facts=replace(c.facts,budget_facts=budget,node_bounds=(replace(c.facts.node_bounds[0],margin_reserve=()),))
    result=assess(c)
    assert result.verdict=='DENY' and 'PROGRAM_RISK_MARGIN_UNDERBOUND' in result.rejection_codes


@pytest.mark.parametrize('product',[t.Product.SPOT,t.Product.USD_M_PERP])
def test_atomic_conditional_requests_keep_their_real_type_in_risk(product):
    c=risk_case(product=product,conditional=True);result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert c.plan.nodes[0].order_type in ('STOP_LOSS','STOP_MARKET')
    assert c.plan.original_program.nodes[0].order.trigger.kind=='STATIC'


@pytest.mark.parametrize('template',['TWAP','VP'])
@pytest.mark.parametrize('mode',['VENUE_NATIVE','LOCAL_SYNTHETIC'])
def test_futures_algorithm_mother_uses_one_capital_envelope_and_explicit_children(template,mode):
    c=risk_case(product=t.Product.USD_M_PERP,template=template,mode=mode);result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.capital.amount==90000
    assert result.metrics.active_order_count>=3


def test_real_equity_namespace_cash_and_share_units_do_not_become_spot_or_usdt():
    c=risk_case(product=t.Product.US_EQUITY);result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.required_assets==(money('81490','USD'),)
    assert c.terms.target.unit=='SHARE' and c.plan.requests[0].namespace==Namespace.EQUITY
    assert c.plan.original_program.nodes[0].order.tokenize is False


def test_close_all_records_real_owned_quantity_without_inserting_wire_quantity():
    c=risk_case(product=t.Product.USD_M_PERP,side='SELL',actual='2',target='0',quantity='2',close_all=True)
    result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert c.allocation.nodes[0].native_quantity is None and c.allocation.nodes[0].ledger_quantity.value==2
    assert result.metrics.required_assets==()


def test_actual_working_orders_are_counted_with_price_bounds_and_no_dummy_orders():
    c=risk_case(actual='.4',working='.3',quantity='.3');result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.exposure.gross.amount==result.metrics.exposure.net.amount==81490
    assert result.metrics.required_assets==(money('24447'),)
    assert c.allocation.delta.working.value==D('.3')
    c.facts=replace(c.facts,working_bounds=())
    assert 'PROGRAM_RISK_WORKING_COVERAGE_INCOMPLETE' in assess(c).rejection_codes


def test_unsent_pending_quantities_use_same_portfolio_math_without_fabricated_orders():
    prior=PendingQuantity(intent_id='old-held-target',signed_quantity=t.Quantity(value=D('.2'),unit='BASE'),
        reservation_ids=(),command_refs=('durable-old-root',))
    c=risk_case(actual='.4',quantity='.4',pending=(prior,));result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert not c.book.active_orders and c.allocation.delta.working.value==D('.2')
    assert result.metrics.exposure.gross.amount==result.metrics.exposure.net.amount==81490
    assert result.metrics.required_assets==(money('32596'),)


def test_base_receipt_fees_reduce_receipt_bound_without_inventing_existing_base_cash_need():
    c=risk_case(template='OPO');fee=FeeBound(amount=money('.01','BTC'),debit='FROM_RECEIPT')
    budget=replace(c.facts.budget_facts,node_facts=(replace(c.facts.budget_facts.node_facts[0],fees=(fee,)),
        *c.facts.budget_facts.node_facts[1:]))
    c.exposure=derive_program_exposure(c.plan,budget,now=NOW)
    c.facts=replace(c.facts,budget_facts=budget,node_bounds=(replace(c.facts.node_bounds[0],execution_fees=(fee,)),
        *c.facts.node_bounds[1:]),inputs=replace(c.facts.inputs,fee_reserve=(fee.amount,)))
    result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.required_assets==(money('81490'),) and result.metrics.fees.amount==900
    assert c.exposure.receipts[0].minimum_net.value==D('.99')


def test_quote_funding_and_exit_fee_costs_are_reserved_once_and_bound_to_horizon():
    c=risk_case();entry_fee=FeeBound(amount=money('2'),debit='EXTERNAL_BALANCE');exit_fee=FeeBound(amount=money('3'),debit='EXTERNAL_BALANCE')
    budget=replace(c.facts.budget_facts,node_facts=(replace(c.facts.budget_facts.node_facts[0],fees=(entry_fee,)),))
    c.exposure=derive_program_exposure(c.plan,budget,now=NOW)
    c.facts=replace(c.facts,budget_facts=budget,node_bounds=(replace(c.facts.node_bounds[0],execution_fees=(entry_fee,),funding_reserve=(money('4'),)),),
        additional_exit_fees=(exit_fee,),inputs=replace(c.facts.inputs,fee_reserve=(money('5'),),funding_reserve=(money('4'),)))
    result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.required_assets==(money('81499'),)
    assert result.metrics.capital.amount==81490 and result.metrics.fees.amount==5 and result.metrics.funding.amount==4
    c.facts=replace(c.facts,inputs=replace(c.facts.inputs,funding_reserve=()))
    assert 'PROGRAM_RISK_FUNDING_BOUND_MISMATCH' in assess(c).rejection_codes
