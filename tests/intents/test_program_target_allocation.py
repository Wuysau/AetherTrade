"""Generic program target allocation, sharing production TargetCompiler math."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_program_compilation import inputs,change,price,trigger
from test_program_exposure import make_plan
from unit.support_ip_09 import make_case,position,order
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_program import QuantitySpec,TimeInForceSpec
from trading_system.intents.program_compilation import compile_order_program
from trading_system.intents.semantic_validation import ProposalRejected
from trading_system.intents.program_target_allocation import (
    ProgramTargetFacts,TargetOwnerState,NativeUnitBinding,TargetNormalizationPrice,compile_program_target,
)
from trading_system.risk.exposure import WorkingOrder

NOW='2026-10-28T10:00:00Z'
END='2026-10-28T11:00:00Z'


def plan_for(*,target='1',quantity='1',side='BUY',product=t.Product.SPOT,template='ATOMIC',mode='VENUE_NATIVE'):
    target=format(D(target),'f');quantity=format(D(quantity),'f')
    if template!='ATOMIC':
        old=make_plan(template,mode,product);p=replace(old.original_proposal,target_sizing='BASE_QUANTITY',target_value=target,target_side='FLAT' if D(target)==0 else 'LONG')
        if template in ('TWAP','VP'):
            root=p.program.nodes[0]
            p=replace(p,program=replace(p.program,nodes=(replace(root,order=replace(root.order,
                side=side,quantity=QuantitySpec(kind='BASE_QUANTITY',value=quantity,source_node=None))),)))
        _,r,c,k=inputs(product)
    else:
        p,r,c,k=inputs(product);p=change(p,side=side,quantity=QuantitySpec(kind='BASE_QUANTITY',value=quantity,source_node=None))
        if product==t.Product.US_EQUITY:
            p=change(p,tokenize=False,trading_session='24H')
        p=replace(p,target_sizing='BASE_QUANTITY',target_value=target,target_side='FLAT' if D(target)==0 else 'LONG',program=replace(p.program,execution_mode=mode))
    return compile_order_program(p,r,c,**k),r


def scenario(plan,rules,*,actual='0',working='0',working_side='BUY',certainty='CONFIRMED'):
    case=make_case(plan.scope.product);scope=plan.scope;unit=case.target.instrument.quantity_step.unit
    proof=t.EvidenceRef(id='trusted-target-source',hash='a'*64,environment=scope.environment,source='ISOLATED_TARGET_FACTS',visible_at=NOW)
    cov=t.Coverage(scope=scope,query_succeeded=True,pagination_complete=True,supported=True,missing=())
    spec=replace(case.target.instrument,instrument_uid=scope.instrument_uid,native_symbol='BTCUSDT',
        quantity_step=t.Quantity(value=D(rules.quantity_step),unit=unit),minimum_quantity=t.Quantity(value=D(rules.minimum_quantity),unit=unit),
        minimum_notional=t.Money(amount=D(rules.minimum_notional),currency=rules.quote_asset))
    book=replace(case.book,scope=scope,coverage=cov,evidence=(proof,),positions=(position(scope,actual,unit),) if D(actual) else (),
        active_orders=(order(scope,working,unit=unit,certainty=certainty),) if D(working) else ())
    context=replace(case.context,scopes=(scope,),snapshot_ref=replace(case.context.snapshot_ref,visible_at=NOW))
    target=replace(case.target,instrument=spec,expected_instrument=spec,clock=replace(case.target.clock,utc_now=NOW),context=context,
        allow_round_down=plan.original_proposal.target_sizing=='QUOTE_NOTIONAL',
        session=replace(case.target.session,instrument_uid=scope.instrument_uid,observed_at=NOW,evidence=proof),
        order_sides=(WorkingOrder(logical_client_order_id='order',side=working_side),) if D(working) else ())
    digest=canonical_hash(domain_payload(plan));first=plan.nodes[0]
    bridge=NativeUnitBinding(scope=scope,compilation_hash=digest,rule_revision=rules.revision,native_unit=first.quantity_unit,ledger_unit=unit,
        ledger_units_per_native_unit=D(1),native_multiplier=D(first.quantity_multiplier),ledger_multiplier=D(first.quantity_multiplier),evidence=proof,valid_until=END)
    normalization=None
    if plan.original_proposal.target_sizing=='QUOTE_NOTIONAL':
        normalization=TargetNormalizationPrice(scope=scope,compilation_hash=digest,price=t.Price(value=D(first.original.order.price.value or '50000'),currency=rules.quote_asset),
            basis='DECLARED_LIMIT' if first.original.order.price.kind=='FIXED_LIMIT' else 'TRUSTED_MARK',evidence=proof,valid_until=END)
    facts=ProgramTargetFacts(transaction_id='fixture-tx',compilation_hash=digest,rules=rules,context=context,
        owner=TargetOwnerState(owner_strategy_id='demo-ai-strategy',ownership_epoch=1,policy_revision=1),collateral_group_id='paper-group-demo',
        book_sequence=book.sequence,book_hash=canonical_hash(domain_payload(book)),inputs=target,unit_binding=bridge,
        normalization_price=normalization,unknown_command_ids=(),evidence=proof,valid_until=END)
    return book,facts


def test_generic_atomic_increase_uses_real_common_target_math_without_legacy_projection(monkeypatch):
    plan,rules=plan_for();book,facts=scenario(plan,rules)
    from trading_system.contracts.wire import ContractCodec
    monkeypatch.setattr(ContractCodec,'encode',lambda *a: (_ for _ in ()).throw(AssertionError('fabricated legacy wire projection')))
    result=compile_program_target(plan,book,facts)
    assert result.delta.remaining.value==1 and result.nodes[0].ledger_quantity.value==1
    assert len(result.requests)==1 and not result.execution_authorized


def test_partial_reduce_requires_original_sell_quantity_equal_real_remaining_delta():
    plan,rules=plan_for(target='.6',quantity='.4',side='SELL');book,facts=scenario(plan,rules,actual='1')
    result=compile_program_target(plan,book,facts)
    assert result.delta.remaining.value==D('-.4') and result.delta.classification=='REDUCE'
    assert result.nodes[0].side=='SELL' and result.nodes[0].native_quantity.value==D('.4')


def test_immutable_node_quantity_is_not_silently_resized_for_a_smaller_delta():
    plan,rules=plan_for();book,facts=scenario(plan,rules,actual='.4')
    with pytest.raises(ProposalRejected,match='PROGRAM_NODE_QUANTITY_REPLAN_REQUIRED'):
        compile_program_target(plan,book,facts)


def test_flat_exit_has_real_sell_allocation_without_fabricated_legacy_entry():
    plan,rules=plan_for(target='0',quantity='1',side='SELL');book,facts=scenario(plan,rules,actual='1')
    result=compile_program_target(plan,book,facts)
    assert result.terms.target_side=='FLAT' and result.delta.target.value==0
    assert result.delta.remaining.value==-1 and result.nodes[0].native_quantity.value==1


def test_partial_reducer_never_silently_flips_original_buy_request():
    plan,rules=plan_for(target='.6',quantity='.4',side='BUY');book,facts=scenario(plan,rules,actual='1')
    with pytest.raises(ProposalRejected,match='PROGRAM_NODE_SIDE_REPLAN_REQUIRED'):
        compile_program_target(plan,book,facts)


def test_current_fills_and_working_leave_only_the_true_remaining_quantity():
    plan,rules=plan_for(quantity='.3');book,facts=scenario(plan,rules,actual='.4',working='.3')
    result=compile_program_target(plan,book,facts)
    assert result.delta.actual.value==D('.4') and result.delta.working.value==D('.3')
    assert result.nodes[0].ledger_quantity.value==D('.3') and not result.blockers


def test_unknown_working_is_counted_but_does_not_create_a_new_request():
    plan,rules=plan_for(quantity='.3');book,facts=scenario(plan,rules,actual='.4',working='.3',certainty='UNKNOWN')
    result=compile_program_target(plan,book,facts)
    assert result.delta.remaining.value==D('.3') and result.delta.working.value==D('.3')
    assert result.blockers==('UNKNOWN_ORDER_RECONCILIATION_REQUIRED',) and not result.requests


def test_zero_asset_pending_command_still_occupies_quantity():
    from trading_system.intents.target_compiler import PendingQuantity
    plan,rules=plan_for(quantity='.4');book,facts=scenario(plan,rules,actual='.4')
    pending=PendingQuantity(intent_id='earlier',signed_quantity=t.Quantity(value=D('.2'),unit='BASE'),reservation_ids=(),command_refs=('durable-parent',))
    result=compile_program_target(plan,book,replace(facts,inputs=replace(facts.inputs,pending=(pending,))))
    assert result.delta.working.value==D('.2') and result.nodes[0].ledger_quantity.value==D('.4')
    blocked=compile_program_target(plan,book,replace(facts,inputs=replace(facts.inputs,pending=(pending,)),unknown_command_ids=('durable-parent',)))
    assert blocked.blockers==('UNKNOWN_ORDER_RECONCILIATION_REQUIRED',) and not blocked.requests


def test_satisfied_target_and_conflicting_old_work_never_duplicate_submission():
    plan,rules=plan_for();book,facts=scenario(plan,rules,actual='.4',working='.6')
    result=compile_program_target(plan,book,facts)
    assert result.delta.remaining.value==0 and result.blockers==('TARGET_ALREADY_SATISFIED',) and not result.requests
    plan,rules=plan_for(target='.5',quantity='.1');book,facts=scenario(plan,rules,actual='.4',working='.3')
    result=compile_program_target(plan,book,facts)
    assert result.delta.cancel_first and 'TARGET_CANCEL_AND_RECONCILE_REQUIRED' in result.blockers and not result.requests


@pytest.mark.parametrize('template',['TWAP','VP'])
@pytest.mark.parametrize('mode',['VENUE_NATIVE','LOCAL_SYNTHETIC'])
def test_native_and_local_algorithm_envelope_is_allocated_once(template,mode):
    plan,rules=plan_for(template=template,mode=mode);book,facts=scenario(plan,rules)
    result=compile_program_target(plan,book,facts)
    assert len(result.nodes)==1 and result.nodes[0].phase=='ALGORITHM_ENVELOPE'
    assert result.nodes[0].ledger_quantity.value==1 and len(result.requests)==1
    assert result.requests[0].phase==('INITIAL' if mode=='VENUE_NATIVE' else 'ALGORITHM_ENVELOPE')
    assert result.requests[0].original==plan.requests[0] and not result.execution_authorized


@pytest.mark.parametrize('template',['OTO','OTOCO','OPO','OPOCO'])
@pytest.mark.parametrize('mode',['VENUE_NATIVE','LOCAL_SYNTHETIC'])
def test_contingent_legs_are_never_charged_as_immediate_entry_delta(template,mode):
    plan,rules=plan_for(template=template,mode=mode);book,facts=scenario(plan,rules)
    result=compile_program_target(plan,book,facts)
    assert result.delta.remaining.value==1 and result.nodes[0].ledger_quantity.value==1
    for pending in result.nodes[1:]:
        assert pending.phase==('AFTER_NET_RECEIPT' if template.startswith('OPO') else 'AFTER_FULL_FILL')
        assert 'CONTINGENT_FINANCIAL_AND_TARGET_REVALIDATION' in pending.obligations
        assert (pending.native_quantity is None)==template.startswith('OPO')
    assert 'CONTINGENT_LEGS_NOT_INITIAL_DELTA' in result.obligations
    if mode=='VENUE_NATIVE':
        assert len(result.requests)==1 and result.requests[0].deferred_node_ids
    else:
        assert all(r.phase!='INITIAL' for r in result.requests[1:])


@pytest.mark.parametrize('mode',['VENUE_NATIVE','LOCAL_SYNTHETIC'])
def test_same_direction_batch_uses_sum_of_immutable_node_quantities(mode):
    plan,rules=plan_for(target='2',template='BATCH',mode=mode,product=t.Product.USD_M_PERP);book,facts=scenario(plan,rules)
    result=compile_program_target(plan,book,facts)
    assert result.delta.remaining.value==2 and sum(n.ledger_quantity.value for n in result.nodes)==2
    book,facts=scenario(plan,rules,actual='1')
    with pytest.raises(ProposalRejected,match='PROGRAM_NODE_QUANTITY_REPLAN_REQUIRED'):
        compile_program_target(plan,book,facts)


def test_local_oco_still_requires_group_race_quantity_proof_instead_of_native_guarantees():
    plan,rules=plan_for(template='OCO',mode='LOCAL_SYNTHETIC');book,facts=scenario(plan,rules,actual='2')
    result=compile_program_target(plan,book,facts)
    assert result.delta.remaining.value==-1
    assert result.blockers==('EXCLUSIVE_TARGET_ALLOCATION_PROOF_REQUIRED',) and not result.requests


def test_quote_limit_target_uses_exact_declared_limit_and_current_native_bridge():
    p,r,c,k=inputs();plan=compile_order_program(p,r,c,**k);book,facts=scenario(plan,r)
    result=compile_program_target(plan,book,facts)
    assert result.terms.target.value==D('0.049085') and result.nodes[0].native_quantity.value==D('0.049085')
    bad=replace(facts.normalization_price,price=t.Price(value=D('50000'),currency='USDT'))
    with pytest.raises(ProposalRejected,match='DECLARED_TARGET_LIMIT_REQUIRED'):
        compile_program_target(plan,book,replace(facts,normalization_price=bad))
    with pytest.raises(ProposalRejected,match='TARGET_NORMALIZATION_PRICE_REQUIRED'):
        compile_program_target(plan,book,replace(facts,normalization_price=None))


def test_native_quote_market_request_stays_unresolved_instead_of_converted_to_quantity():
    p,r,c,k=inputs();p=change(p,price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None));plan=compile_order_program(p,r,c,**k)
    book,facts=scenario(plan,r);result=compile_program_target(plan,book,facts)
    assert result.delta.target.value==D('0.08') and result.blockers==('QUOTE_REQUEST_TARGET_ALLOCATION_UNRESOLVED',)
    assert not result.requests and plan.nodes[0].quantity.kind=='QUOTE_NOTIONAL'


def test_close_all_records_a_bound_without_inserting_quantity_into_wire():
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    p=change(p,side='SELL',price=price(),quantity=QuantitySpec(kind='CLOSE_ALL',value=None,source_node=None),
        trigger=trigger(source='MARK_PRICE'),tif=TimeInForceSpec(kind='NONE',expires_at=None))
    p=replace(p,target_side='FLAT',target_value='0',target_sizing='BASE_QUANTITY')
    plan=compile_order_program(p,r,c,**k);book,facts=scenario(plan,r,actual='2')
    result=compile_program_target(plan,book,facts)
    assert result.nodes[0].native_quantity is None and result.nodes[0].ledger_quantity.value==2
    assert 'FULL_POSITION_QUANTITY_RECHECK_AT_DISPATCH' in result.nodes[0].obligations
    assert all(p.name!='quantity' for p in plan.requests[0].parameters)


@pytest.mark.parametrize('mutation,code',[
    ('context','PROGRAM_TARGET_CONTEXT_MISMATCH'),('owner','PROGRAM_TARGET_OWNER_MISMATCH'),
    ('book','TARGET_BOOK_OR_COMPILATION_CHANGED'),('rule','TARGET_RULE_SNAPSHOT_MISMATCH'),
    ('unit','COMPILED_NATIVE_UNIT_MISMATCH'),('expired','TARGET_FACTS_EXPIRED'),
    ('multiplier','NATIVE_UNIT_MULTIPLIER_MISMATCH'),('unknown_coverage','UNKNOWN_COMMAND_COVERAGE_REQUIRED'),
])
def test_stale_or_mismatching_trusted_sources_fail_closed(mutation,code):
    plan,rules=plan_for();book,facts=scenario(plan,rules)
    if mutation=='context':facts=replace(facts,context=replace(facts.context,decision_run_id='different'))
    if mutation=='owner':facts=replace(facts,owner=replace(facts.owner,ownership_epoch=2))
    if mutation=='book':facts=replace(facts,book_sequence=2)
    if mutation=='rule':facts=replace(facts,rules=replace(rules,revision=2))
    if mutation=='unit':facts=replace(facts,unit_binding=replace(facts.unit_binding,native_unit='ETH'))
    if mutation=='expired':facts=replace(facts,valid_until=NOW)
    if mutation=='multiplier':facts=replace(facts,unit_binding=replace(facts.unit_binding,ledger_multiplier=D(2)))
    if mutation=='unknown_coverage':facts=replace(facts,unknown_command_ids=None)
    with pytest.raises(ProposalRejected,match=code):compile_program_target(plan,book,facts)


def test_native_lot_to_legacy_contract_conversion_is_explicit_and_exact():
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    k['profile']=replace(k['profile'],usd_m=replace(k['profile'].usd_m,quantity_unit='LOT',contract_multiplier='0.1'))
    p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None));p=replace(p,target_sizing='BASE_QUANTITY',target_value='1')
    plan=compile_order_program(p,r,c,**k);book,facts=scenario(plan,r)
    spec=replace(facts.inputs.instrument,quantity_step=t.Quantity(value=D(r.quantity_step)*100,unit='CONTRACT'),minimum_quantity=t.Quantity(value=D(r.minimum_quantity)*100,unit='CONTRACT'))
    bridge=replace(facts.unit_binding,ledger_units_per_native_unit=D(100),ledger_multiplier=D('.001'))
    facts=replace(facts,unit_binding=bridge,inputs=replace(facts.inputs,instrument=spec,expected_instrument=spec))
    result=compile_program_target(plan,book,facts)
    assert result.delta.target.value==100 and result.nodes[0].native_quantity.value==1 and result.nodes[0].ledger_quantity.value==100
    assert result.terms.target.unit=='CONTRACT' and result.nodes[0].native_quantity.unit=='LOT'


def test_identical_native_and_ledger_unit_cannot_hide_a_quantity_scale_change():
    plan,rules=plan_for(product=t.Product.USD_M_PERP);book,facts=scenario(plan,rules)
    spec=replace(facts.inputs.instrument,quantity_step=t.Quantity(value=D(rules.quantity_step)*2,unit='CONTRACT'),minimum_quantity=t.Quantity(value=D(rules.minimum_quantity)*2,unit='CONTRACT'))
    bridge=replace(facts.unit_binding,ledger_units_per_native_unit=D(2),ledger_multiplier=D('.5'))
    with pytest.raises(ProposalRejected,match='IDENTICAL_UNIT_SCALE_MISMATCH'):
        compile_program_target(plan,book,replace(facts,unit_binding=bridge,inputs=replace(facts.inputs,instrument=spec,expected_instrument=spec)))


def test_future_context_snapshot_is_not_a_genuine_current_basis():
    plan,rules=plan_for();book,facts=scenario(plan,rules)
    context=replace(facts.context,snapshot_ref=replace(facts.context.snapshot_ref,visible_at=END))
    with pytest.raises(ProposalRejected,match='TARGET_SOURCE_EVIDENCE_REQUIRED'):
        compile_program_target(plan,book,replace(facts,context=context,inputs=replace(facts.inputs,context=context)))


def test_two_requests_cannot_spend_one_initial_node_allocation():
    plan,rules=plan_for();plan=replace(plan,requests=plan.requests*2);book,facts=scenario(plan,rules)
    with pytest.raises(ProposalRejected,match='REQUEST_NODE_COVERAGE_AMBIGUOUS'):
        compile_program_target(plan,book,facts)


@pytest.mark.parametrize('product',list(t.Product))
def test_generic_partial_reducer_keeps_each_legacy_ledger_unit(product):
    plan,rules=plan_for(target='.6',quantity='.4',side='SELL',product=product);book,facts=scenario(plan,rules,actual='1')
    result=compile_program_target(plan,book,facts)
    assert result.delta.remaining.value==D('-.4')
    assert result.terms.target.unit=={t.Product.SPOT:'BASE',t.Product.USD_M_PERP:'CONTRACT',t.Product.US_EQUITY:'SHARE'}[product]
    assert result.requests[0].original==plan.requests[0]


def test_submitted_plan_and_price_proofs_have_real_expiry_not_placeholder_deadlines():
    plan,rules=plan_for();book,facts=scenario(plan,rules)
    late=replace(facts.inputs,clock=replace(facts.inputs.clock,utc_now=plan.original_proposal.valid_until))
    with pytest.raises(ProposalRejected,match='INTENT_EXPIRED'):
        compile_program_target(plan,book,replace(facts,inputs=late))
    p,r,c,k=inputs();plan=compile_order_program(p,r,c,**k);book,facts=scenario(plan,r)
    expired=replace(facts.normalization_price,valid_until=NOW)
    with pytest.raises(ProposalRejected,match='TARGET_NORMALIZATION_PRICE_EXPIRED'):
        compile_program_target(plan,book,replace(facts,normalization_price=expired))


def test_program_source_hash_mismatch_never_yields_allocations():
    plan,rules=plan_for();plan=replace(plan,program_hash='0'*64);book,facts=scenario(plan,rules)
    with pytest.raises(ProposalRejected,match='PROGRAM_SOURCE_HASH_MISMATCH'):
        compile_program_target(plan,book,facts)


def test_new_target_math_cannot_cross_through_flat_to_reverse_a_position():
    plan,rules=plan_for(side='SELL',product=t.Product.USD_M_PERP)
    p=replace(plan.original_proposal,target_side='SHORT');_,r,c,k=inputs(t.Product.USD_M_PERP)
    plan=compile_order_program(p,r,c,**k);book,facts=scenario(plan,r,actual='1')
    with pytest.raises(ProposalRejected,match='REVERSAL_REQUIRES_FLAT'):
        compile_program_target(plan,book,facts)


@pytest.mark.parametrize('side,quantity,code',[
    ('BUY','1','CONTINGENT_NONPROTECTIVE_TARGET_UNSUPPORTED'),
    ('SELL','2','CONTINGENT_QUANTITY_EXCEEDS_TARGET'),
])
def test_pending_leg_cannot_silently_define_a_larger_or_different_future_target(side,quantity,code):
    old,_=plan_for(template='OTO');pending=old.original_program.nodes[1]
    pending=replace(pending,order=replace(pending.order,side=side,quantity=QuantitySpec(kind='BASE_QUANTITY',value=quantity,source_node=None)))
    proposal=replace(old.original_proposal,program=replace(old.original_program,nodes=(old.original_program.nodes[0],pending)))
    _,r,c,k=inputs();plan=compile_order_program(proposal,r,c,**k);book,facts=scenario(plan,r)
    result=compile_program_target(plan,book,facts)
    assert code in result.blockers and not result.requests


def test_quote_batch_equal_numeric_limits_preserve_different_wire_representations():
    old,_=plan_for(target='2',template='BATCH',product=t.Product.USD_M_PERP)
    nodes=tuple(replace(node,order=replace(node.order,price=replace(node.order.price,value=value)))
        for node,value in zip(old.original_program.nodes,('81490.0','81490.00')))
    proposal=replace(old.original_proposal,target_sizing='QUOTE_NOTIONAL',target_value='162980',
        program=replace(old.original_program,nodes=nodes))
    _,rules,caps,kwargs=inputs(t.Product.USD_M_PERP)
    plan=compile_order_program(proposal,rules,caps,**kwargs)
    before=canonical_hash(domain_payload(plan));book,facts=scenario(plan,rules)
    result=compile_program_target(plan,book,facts)
    assert not result.blockers and result.delta.target.value==D(2)
    assert sum(node.ledger_quantity.value for node in result.nodes)==D(2)
    assert tuple(node.original.order.price.value for node in plan.nodes)==('81490.0','81490.00')
    assert tuple(next(p.value for p in node.parameters if p.name=='price') for node in plan.nodes)==('81490.0','81490.00')
    assert canonical_hash(domain_payload(plan))==before
    assert result.compilation_hash==before and result.requests[0].original==plan.requests[0]
    different=replace(nodes[1],order=replace(nodes[1].order,price=replace(nodes[1].order.price,value='81491.00')))
    different_proposal=replace(proposal,program=replace(proposal.program,nodes=(nodes[0],different)))
    different_plan=compile_order_program(different_proposal,rules,caps,**kwargs)
    book,facts=scenario(different_plan,rules)
    with pytest.raises(ProposalRejected,match='MULTI_PRICE_TARGET_NORMALIZATION_UNSUPPORTED'):
        compile_program_target(different_plan,book,facts)


def test_rounded_remaining_dust_does_not_appear_fully_allocatable():
    plan,rules=plan_for(target='1',quantity='0.999999');book,facts=scenario(plan,rules,actual='0.0000005')
    facts=replace(facts,inputs=replace(facts.inputs,allow_round_down=True))
    original=canonical_hash(domain_payload(plan))
    result=compile_program_target(plan,book,facts)
    assert result.blockers==('TARGET_PRECISION_RESIDUAL_UNRESOLVED',)
    assert not result.nodes and not result.requests
    assert result.delta.target.value==D(1) and result.delta.actual.value==D('0.0000005')
    assert result.delta.remaining.value==D('0.999999')
    assert result.target_residual==t.Quantity(value=D('0.0000005'),unit='BASE')
    assert 'EXPLICIT_RESIDUAL_AND_PROTECTION_ACCOUNTING_REQUIRED' in result.obligations
    assert canonical_hash(domain_payload(plan))==original and plan.original_proposal.target_value=='1'
    from trading_system.risk.exposure import ExposureCalculator,ExposureMark
    from trading_system.risk.valuation import Valuation
    mark=ExposureMark(scope=plan.scope,price=t.Price(value=D(100),currency='USDT'),multiplier=D(1),unit='BASE',related_group='btc')
    assert 'DELTA_INCONSISTENT' in ExposureCalculator(Valuation('USDT'),(mark,)).project(book,result.delta).missing


def test_exact_grid_remaining_still_allocates_without_residual():
    plan,rules=plan_for(target='1',quantity='0.999999');book,facts=scenario(plan,rules,actual='0.000001')
    result=compile_program_target(plan,book,replace(facts,inputs=replace(facts.inputs,allow_round_down=True)))
    assert not result.blockers and len(result.requests)==1
    assert result.target_residual==t.Quantity(value=D(0),unit='BASE')
