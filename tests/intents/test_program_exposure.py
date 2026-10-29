"""Offline budgets from real compiler outputs; never financial admission."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_program_compilation import inputs,change,price,trigger
from trading_system.intents.program_compilation import compile_order_program
from trading_system.contracts import types as t
from trading_system.risk.program_exposure import ProgramBudgetFacts,NodeBudgetFacts,FeeBound,derive_program_exposure,compilation_hash
from trading_system.risk.program_exposure import AtomicGroupProof,LockedReceipt,ExistingConsumer,AllocationEnvelope
from trading_system.contracts.order_program import QuantitySpec,ActivationLink,AlgorithmSpec,TimeInForceSpec
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.canonical import domain_payload
from trading_system.risk.valuation import Valuation,FxRate

NOW='2026-10-28T10:00:00Z'


def make_plan(template='ATOMIC',mode='VENUE_NATIVE',product=t.Product.SPOT):
    p,r,c,k=inputs(product)
    p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None))
    root=p.program.nodes[0]
    program=replace(p.program,execution_mode=mode)
    if template=='OCO' or template.endswith('CO'):
        q=QuantitySpec(kind='DERIVED_RECEIPT',value=None,source_node=root.node_id) if template.startswith('OPO') else root.order.quantity
        above=replace(root,node_id='above',role='LEG' if template=='OCO' else 'PENDING',order=replace(root.order,side='SELL',quantity=q,
            price=price('FIXED_LIMIT',value='83000'),trigger=trigger(purpose='TAKE_PROFIT',price='83000')))
        below=replace(above,node_id='below',order=replace(above.order,price=price('FIXED_LIMIT',value='81300'),trigger=trigger(price='81330')))
        if template=='OCO':
            program=replace(program,template=template,nodes=(above,below),exclusive_groups=(('above','below'),))
        else:
            working=replace(root,role='WORKING')
            program=replace(program,template=template,nodes=(working,above,below),exclusive_groups=(('above','below'),),
                links=tuple(ActivationLink(source=root.node_id,target=n.node_id,condition='NET_RECEIPT' if template.startswith('OPO') else 'FULL_FILL') for n in (above,below)))
    elif template in ('OTO','OPO'):
        working=replace(root,role='WORKING')
        pending=replace(root,node_id='exit',role='PENDING',order=replace(root.order,side='SELL',
            quantity=QuantitySpec(kind='DERIVED_RECEIPT',value=None,source_node=root.node_id) if template=='OPO' else root.order.quantity))
        program=replace(program,template=template,nodes=(working,pending),
            links=(ActivationLink(source=root.node_id,target='exit',condition='NET_RECEIPT' if template=='OPO' else 'FULL_FILL'),))
    elif template=='BATCH':
        program=replace(program,template=template,nodes=(root,replace(root,node_id='second')))
    elif template in ('TWAP','VP'):
        parent=replace(root,role='ALGORITHM_PARENT',order=replace(root.order,price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None)))
        if template=='TWAP' or mode!='VENUE_NATIVE':
            program=replace(program,template=template,nodes=(parent,),algorithm=AlgorithmSpec(kind=template,duration_seconds=300,
                participation_bps='100' if template=='VP' else None,urgency='HIGH' if template=='VP' else None))
        else:
            payload=domain_payload(replace(p,program=replace(program,nodes=(parent,))))
            payload['contract_version']='2026-10-29'
            payload['program'].update(contract_version='2026-10-29',template='VP',algorithm=dict(kind='VP',duration_seconds=None,urgency='HIGH'))
            p=VersionedOrderCodec().decode('order-proposal',payload)
            program=p.program
    return compile_order_program(replace(p,program=program),r,c,**k)


def facts_for(plan):
    source=next((n for n in plan.nodes if n.original.role=='WORKING'),None)
    return ProgramBudgetFacts(scope=plan.scope,collateral_group_id='group',compilation_hash=compilation_hash(plan),base_asset='BTC',quote_asset='USDT',
        node_facts=tuple(NodeBudgetFacts(node_id=n.original.node_id,fees=(),margin=(t.Money(amount=D('10'),currency='USDT'),) if plan.scope.product==t.Product.USD_M_PERP else None,
            price_upper=t.Price(value=D('90000'),currency='USDT') if n.quantity.kind=='DERIVED_RECEIPT' else None) for n in plan.nodes),
        asset_limits=(t.Money(amount=D('10000000'),currency='USDT'),t.Money(amount=D('100'),currency='BTC')),
        consumers=(),reservations=(),allocations=(),atomic_groups=tuple(AtomicGroupProof(node_ids=g,evidence_id='native-atomic') for g in plan.original_program.exclusive_groups) if plan.original_program.execution_mode=='VENUE_NATIVE' else (),
        receipts=(LockedReceipt(source_node_id=source.original.node_id,target_node_ids=tuple(n.original.node_id for n in plan.nodes if n.quantity.kind=='DERIVED_RECEIPT'),evidence_id='locked-net'),) if plan.original_program.template.startswith('OPO') else (),
        evidence_id='complete-ledger-fixture',as_of=NOW,valid_until='2026-10-28T11:00:00Z',partial_fill_policy='FULL_FILL_ONLY_ACCEPTED')


def values(requirements):return {m.currency:m.amount for m in requirements}


def consumer(plan,key,kind='WORKING',amount='100',asset='USDT'):
    return ExistingConsumer(consumer_id=key,scope=plan.scope,collateral_group_id='group',kind=kind,
        requirements=(t.Money(amount=D(amount),currency=asset),),gross_exposure=t.Money(amount=D(amount),currency=asset),evidence_id='archived:'+key)


def reservation(plan,amount='100',reflected='0',pending='0',key='reserved'):
    r=t.CapitalReservation(reservation_id=key,environment=plan.scope.environment,collateral_group_id='group',venue_account_id=plan.scope.venue_account_id,
        owner_strategy_id='owner',intent_id='intent',asset='USDT',amount=D(amount),reflected_amount=D(reflected),state=t.ReservationState.HELD,
        created_at=NOW,expires_at='2026-10-28T11:00:00Z',command_refs=('parent',),evidence_watermark='archive')
    return t.ReservationProjection(reservation=r,held=D(amount),reflected=D(reflected),release_pending=D(pending),sequence=1)


@pytest.mark.parametrize('template,mode,quote,base',[
    ('ATOMIC','VENUE_NATIVE','81490','0'),('OCO','VENUE_NATIVE','0','1'),('OCO','LOCAL_SYNTHETIC','0','2'),
    ('OTO','VENUE_NATIVE','81490','1'),('OTO','LOCAL_SYNTHETIC','81490','1'),
    ('OTOCO','VENUE_NATIVE','81490','1'),('OTOCO','LOCAL_SYNTHETIC','81490','2'),
    ('OPO','VENUE_NATIVE','81490','0'),('OPO','LOCAL_SYNTHETIC','81490','0'),
    ('OPOCO','VENUE_NATIVE','81490','0'),('OPOCO','LOCAL_SYNTHETIC','81490','1'),
    ('TWAP','VENUE_NATIVE','90000','0'),('TWAP','LOCAL_SYNTHETIC','90000','0'),
    ('VP','VENUE_NATIVE','90000','0'),('VP','LOCAL_SYNTHETIC','90000','0')])
def test_all_allocation_topologies_use_compiler_outputs(template,mode,quote,base):
    plan=make_plan(template,mode);result=derive_program_exposure(plan,facts_for(plan),now=NOW)
    assert not result.blockers,result.blockers
    assert values(result.program_requirements)=={k:D(v) for k,v in [('USDT',quote),('BTC',base)] if D(v)}
    assert not result.execution_authorized


def test_native_exclusivity_without_proof_falls_back_to_overlapping_sum():
    plan=make_plan('OCO');facts=replace(facts_for(plan),atomic_groups=())
    result=derive_program_exposure(plan,facts,now=NOW)
    assert not result.blockers and values(result.program_requirements)=={'BTC':D(2)}


def test_software_exclusivity_cannot_claim_native_atomicity():
    plan=make_plan('OCO','LOCAL_SYNTHETIC');facts=replace(facts_for(plan),atomic_groups=(AtomicGroupProof(node_ids=('above','below'),evidence_id='claim'),))
    assert derive_program_exposure(plan,facts,now=NOW).blockers==('ATOMICITY_NOT_PROVEN',)


def test_base_fee_deducted_from_receipt_is_not_external_balance_and_never_authorizes_gross_exit():
    plan=make_plan('OPOCO');facts=facts_for(plan);entry=facts.node_facts[0]
    entry=replace(entry,fees=(FeeBound(amount=t.Money(amount=D('0.01'),currency='BTC'),debit='FROM_RECEIPT'),))
    facts=replace(facts,node_facts=(entry,*facts.node_facts[1:]))
    result=derive_program_exposure(plan,facts,now=NOW)
    assert not result.blockers and values(result.program_requirements)=={'USDT':D('81490')}
    assert result.receipts[0].minimum_net.value==D('.99') and result.receipts[0].maximum_net.value==1
    assert 'ACTUAL_NET_RECEIPT' in result.receipts[0].activation_checks
    assert plan.nodes[1].quantity.value is None  # No fabricated child quantity.


def test_quote_and_third_asset_fees_are_currency_segregated():
    plan=make_plan();facts=facts_for(plan)
    fees=(FeeBound(amount=t.Money(amount=D('2'),currency='USDT'),debit='EXTERNAL_BALANCE'),
          FeeBound(amount=t.Money(amount=D('.001'),currency='BNB'),debit='EXTERNAL_BALANCE'))
    facts=replace(facts,node_facts=(replace(facts.node_facts[0],fees=fees),))
    result=derive_program_exposure(plan,facts,now=NOW)
    assert values(result.program_requirements)=={'USDT':D('81492'),'BNB':D('.001')}
    assert result.blockers==('ASSET_CAPACITY_MISSING:BNB',)


@pytest.mark.parametrize('field',['node_facts','asset_limits','consumers','reservations','allocations','atomic_groups','receipts'])
def test_missing_dynamic_inputs_are_never_zero(field):
    plan=make_plan();result=derive_program_exposure(plan,replace(facts_for(plan),**{field:None}),now=NOW)
    assert result.program_requirements is None and result.blockers==('MISSING_'+field.upper(),)


def test_fees_margin_and_close_quantity_are_explicit():
    plan=make_plan();facts=facts_for(plan)
    result=derive_program_exposure(plan,replace(facts,node_facts=(replace(facts.node_facts[0],fees=None),)),now=NOW)
    assert result.blockers[0].startswith('FEE_BOUND_MISSING')
    plan=make_plan(product=t.Product.USD_M_PERP);facts=facts_for(plan)
    assert derive_program_exposure(plan,replace(facts,node_facts=(replace(facts.node_facts[0],margin=None),)),now=NOW).blockers[0].startswith('MARGIN_BOUND_MISSING')


@pytest.mark.parametrize('template',['OTO','OTOCO','OPO','OPOCO'])
def test_full_fill_only_lists_do_not_claim_partial_fill_protection(template):
    plan=make_plan(template)
    assert derive_program_exposure(plan,replace(facts_for(plan),partial_fill_policy='REQUIRE_EACH_FILL'),now=NOW).blockers==('PARTIAL_FILL_PROTECTION_REQUIRES_SEPARATE_PLAN',)


def test_missing_receipt_and_duplicate_source_do_not_double_spend():
    plan=make_plan('OPO');facts=facts_for(plan)
    assert derive_program_exposure(plan,replace(facts,receipts=()),now=NOW).blockers==('LOCKED_NET_RECEIPT_BOUND_MISSING',)
    assert derive_program_exposure(plan,replace(facts,receipts=facts.receipts*2),now=NOW).blockers==('RECEIPT_ALLOCATION_REUSED',)


def test_actual_working_unknown_and_held_reservations_all_consume_budget():
    plan=make_plan();facts=replace(facts_for(plan),consumers=tuple(consumer(plan,k,k,a) for k,a in [('POSITION','100'),('WORKING','200'),('UNKNOWN','300')]),
        reservations=(reservation(plan,'400','200','200'),))
    result=derive_program_exposure(plan,facts,now=NOW)
    assert values(result.existing_requirements)=={'USDT':D('1000')}  # held, not held+subsets
    assert result.gross_before.amount==600 and values(result.total_requirements)['USDT']==D('82490')
    bad=replace(facts.consumers[-1],gross_exposure=None)
    assert derive_program_exposure(plan,replace(facts,consumers=(*facts.consumers[:-1],bad)),now=NOW).blockers==('EXISTING_CONSUMER_BOUND_MISSING:UNKNOWN',)


def test_proven_parent_children_and_position_share_one_envelope_and_one_hold():
    plan=make_plan('TWAP');facts=facts_for(plan)
    members=(consumer(plan,'mother','NATIVE_PARENT'),consumer(plan,'filled','POSITION','40'),consumer(plan,'pending','UNKNOWN','60'))
    allocation=AllocationEnvelope(allocation_id='shared',consumer_ids=tuple(c.consumer_id for c in members),reservation_ids=('reserved',),
        requirements=(t.Money(amount=D(100),currency='USDT'),),gross_exposure=t.Money(amount=D(100),currency='USDT'),evidence_id='complete-envelope')
    facts=replace(facts,consumers=members,reservations=(reservation(plan),),allocations=(allocation,))
    result=derive_program_exposure(plan,facts,now=NOW)
    assert not result.blockers and values(result.existing_requirements)=={'USDT':D(100)} and result.gross_before.amount==100
    unproven=derive_program_exposure(plan,replace(facts,allocations=()),now=NOW)
    assert values(unproven.existing_requirements)=={'USDT':D(300)}
    assert derive_program_exposure(plan,replace(facts,allocations=(allocation,replace(allocation,allocation_id='reuse'))),now=NOW).blockers==('ALLOCATION_DOUBLE_SPEND',)


@pytest.mark.parametrize('kind',['NATIVE_PARENT','LOCAL_PARENT'])
def test_existing_program_envelope_does_not_reserve_mother_and_children_twice(kind):
    plan=make_plan('TWAP','VENUE_NATIVE' if kind=='NATIVE_PARENT' else 'LOCAL_SYNTHETIC');facts=facts_for(plan)
    envelope=D(plan.nodes[0].worst_case_quote_notional)
    members=(consumer(plan,'parent',kind,str(envelope)),consumer(plan,'child','LOCAL_CHILD',str(envelope)))
    allocation=AllocationEnvelope(allocation_id='existing',consumer_ids=('parent','child'),reservation_ids=('reserved',),
        requirements=(t.Money(amount=envelope,currency='USDT'),),gross_exposure=t.Money(amount=envelope,currency='USDT'),evidence_id='verified-existing',program_hash=plan.program_hash)
    facts=replace(facts,consumers=members,reservations=(reservation(plan,str(envelope)),),allocations=(allocation,))
    result=derive_program_exposure(plan,facts,now=NOW)
    assert result.additional_requirements==() and values(result.total_requirements)=={'USDT':envelope}
    assert result.gross_after.amount==envelope and 'EXISTING_PROGRAM_NO_RESUBMISSION' in result.obligations


def test_allocation_must_cover_sum_of_unknown_children_not_only_each_child():
    plan=make_plan();facts=facts_for(plan)
    members=(consumer(plan,'mother','NATIVE_PARENT','100'),consumer(plan,'a','UNKNOWN','70'),consumer(plan,'b','UNKNOWN','70'))
    allocation=AllocationEnvelope(allocation_id='bad',consumer_ids=('mother','a','b'),reservation_ids=(),requirements=(t.Money(amount=D(100),currency='USDT'),),gross_exposure=t.Money(amount=D(100),currency='USDT'),evidence_id='underbound')
    assert derive_program_exposure(plan,replace(facts,consumers=members,allocations=(allocation,)),now=NOW).blockers==('ALLOCATION_FUNDING_UNDERBOUND',)


def test_fx_conversion_reports_exposure_but_never_creates_spendable_usdt():
    plan=make_plan();facts=replace(facts_for(plan),consumers=(consumer(plan,'usd','POSITION','100','USD'),))
    assert derive_program_exposure(plan,facts,now=NOW).blockers==('FX_MISSING:USD',)
    valuation=Valuation('USDT',(FxRate(source='USD',target='USDT',asset_rate=D(1),liability_rate=D('1.01'),haircut=D(0),evidence_id='fx'),))
    result=derive_program_exposure(plan,facts,now=NOW,valuation=valuation)
    assert result.gross_before.amount==101 and result.blockers==('ASSET_CAPACITY_MISSING:USD',)
    assert values(result.existing_requirements)=={'USD':D(100)}


def test_cash_and_futures_unit_price_currency_mismatch_rejects():
    plan=make_plan();facts=replace(facts_for(plan),base_asset='ETH')
    assert derive_program_exposure(plan,facts,now=NOW).blockers==('QUANTITY_UNIT_MISMATCH',)
    assert derive_program_exposure(plan,replace(facts_for(plan),quote_asset='USD'),now=NOW).blockers==('QUOTE_ASSET_MISMATCH',)
    plan=make_plan(product=t.Product.USD_M_PERP);facts=facts_for(plan)
    facts=replace(facts,node_facts=(replace(facts.node_facts[0],quantity_upper=t.Quantity(value=D(1),unit='BTC')),))
    assert derive_program_exposure(plan,facts,now=NOW).blockers==('QUANTITY_UNIT_MISMATCH',)


def test_subdecimal_and_large_reservation_amounts_remain_exact():
    plan=make_plan();facts=facts_for(plan);tiny=D('0.00000000000000000000000000001')
    facts=replace(facts,node_facts=(replace(facts.node_facts[0],fees=(FeeBound(amount=t.Money(amount=tiny,currency='USDT'),debit='EXTERNAL_BALANCE'),)),),
        reservations=(reservation(plan,'9007199254740993.00000001'),),asset_limits=(t.Money(amount=D('10000000000000000'),currency='USDT'),))
    result=derive_program_exposure(plan,facts,now=NOW)
    assert not result.blockers
    assert str(values(result.total_requirements)['USDT'])=='9007199254822483.00000001000000000000000000001'


def test_usdm_nonunit_multiplier_is_not_reapplied_or_assumed_one():
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    k['profile']=replace(k['profile'],usd_m=replace(k['profile'].usd_m,contract_multiplier='0.001',quantity_unit='CONTRACT'))
    p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='100',source_node=None))
    plan=compile_order_program(p,r,c,**k);facts=facts_for(plan)
    result=derive_program_exposure(plan,facts,now=NOW)
    assert not result.blockers and result.gross_program.amount==D('8149')
    assert values(result.program_requirements)=={'USDT':D(10)}


@pytest.mark.parametrize('mode',['VENUE_NATIVE','LOCAL_SYNTHETIC'])
def test_usdm_batch_sums_each_independent_order_margin_and_notional(mode):
    plan=make_plan('BATCH',mode,t.Product.USD_M_PERP)
    result=derive_program_exposure(plan,facts_for(plan),now=NOW)
    assert not result.blockers
    assert values(result.program_requirements)=={'USDT':D(20)} and result.gross_program.amount==D('162980')


def test_close_all_requires_actual_quantity_and_price_bound_with_correct_unit():
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    p=change(p,side='SELL',price=price(),quantity=QuantitySpec(kind='CLOSE_ALL',value=None,source_node=None),
        trigger=trigger(source='MARK_PRICE'),tif=TimeInForceSpec(kind='NONE',expires_at=None))
    plan=compile_order_program(p,r,c,**k);facts=facts_for(plan)
    assert derive_program_exposure(plan,facts,now=NOW).blockers[0].startswith('QUANTITY_BOUND_MISSING')
    info=replace(facts.node_facts[0],quantity_upper=t.Quantity(value=D(2),unit='CONTRACT'))
    assert derive_program_exposure(plan,replace(facts,node_facts=(info,)),now=NOW).blockers[0].startswith('PRICE_BOUND_MISSING')
    info=replace(info,price_upper=t.Price(value=D('90000'),currency='USDT'))
    result=derive_program_exposure(plan,replace(facts,node_facts=(info,)),now=NOW)
    assert not result.blockers and result.gross_program.amount==D('180000')
    assert derive_program_exposure(plan,replace(facts,node_facts=(replace(info,price_upper=t.Price(value=D('90000'),currency='USD')),)),now=NOW).blockers==('PRICE_CURRENCY_MISMATCH',)


def test_quote_sized_sell_never_invents_or_rounds_up_base_quantity():
    p,r,c,k=inputs();p=change(p,side='SELL',price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None))
    plan=compile_order_program(p,r,c,**k);facts=facts_for(plan)
    assert derive_program_exposure(plan,facts,now=NOW).blockers[0].startswith('SELL_QUANTITY_BOUND_MISSING')
    too_small=replace(facts.node_facts[0],quantity_upper=t.Quantity(value=D('.01'),unit='BTC'))
    assert derive_program_exposure(plan,replace(facts,node_facts=(too_small,)),now=NOW).blockers==('QUOTE_SELL_QUANTITY_UNDERBOUND',)
    upper=replace(too_small,quantity_upper=t.Quantity(value=D('.05'),unit='BTC'))
    result=derive_program_exposure(plan,replace(facts,node_facts=(upper,)),now=NOW)
    assert not result.blockers and values(result.program_requirements)=={'BTC':D('.05')}
    assert plan.nodes[0].quantity.kind=='QUOTE_NOTIONAL' and plan.nodes[0].quantity.value=='4000'


@pytest.mark.parametrize('field,value,blocker',[
    ('compilation_hash','wrong','FINANCIAL_FACT_BINDING_MISMATCH'),
    ('valid_until',NOW,'FINANCIAL_FACTS_STALE'),
    ('asset_limits',(t.Money(amount=D(1),currency='USDT'),),'ASSET_CAPACITY_EXCEEDED:USDT'),
])
def test_stale_or_insufficient_financial_facts_are_blocked(field,value,blocker):
    plan=make_plan();result=derive_program_exposure(plan,replace(facts_for(plan),**{field:value}),now=NOW)
    assert result.blockers==(blocker,)


def test_other_account_in_same_collateral_pool_is_counted_but_foreign_pool_is_not_admitted():
    plan=make_plan();other=replace(reservation(plan),reservation=replace(reservation(plan).reservation,venue_account_id='other-account'))
    result=derive_program_exposure(plan,replace(facts_for(plan),reservations=(other,)),now=NOW)
    assert not result.blockers and values(result.existing_requirements)=={'USDT':D(100)}
    bad=replace(other,reservation=replace(other.reservation,collateral_group_id='foreign'))
    assert derive_program_exposure(plan,replace(facts_for(plan),reservations=(bad,)),now=NOW).blockers==('RESERVATION_SCOPE_MISMATCH',)


def test_fx_precision_loss_is_blocked_instead_of_rounding_funding_up_or_down():
    plan=make_plan();facts=replace(facts_for(plan),consumers=(consumer(plan,'huge','POSITION','9'*80,'USD'),))
    valuation=Valuation('USDT',(FxRate(source='USD',target='USDT',asset_rate=D('1.0000000001'),liability_rate=D('1.0000000001'),haircut=D(0),evidence_id='precise'),))
    result=derive_program_exposure(plan,facts,now=NOW,valuation=valuation)
    assert result.blockers==('ARITHMETIC_PRECISION_UNPROVEN',) and result.total_requirements is None


def test_same_program_hash_on_another_account_cannot_cover_new_program_funding():
    plan=make_plan('TWAP');facts=facts_for(plan)
    parent=replace(consumer(plan,'parent','NATIVE_PARENT','90000'),scope=replace(plan.scope,venue_account_id='another-account'))
    envelope=AllocationEnvelope(allocation_id='foreign',consumer_ids=('parent',),reservation_ids=(),
        requirements=(t.Money(amount=D('90000'),currency='USDT'),),gross_exposure=t.Money(amount=D('90000'),currency='USDT'),
        evidence_id='other-account-proof',program_hash=plan.program_hash)
    result=derive_program_exposure(plan,replace(facts,consumers=(parent,),allocations=(envelope,)),now=NOW)
    assert result.blockers==('PROGRAM_ALLOCATION_SCOPE_MISMATCH',)


def test_parent_and_child_from_different_scopes_cannot_share_native_envelope():
    plan=make_plan();facts=facts_for(plan)
    parent=consumer(plan,'parent','NATIVE_PARENT')
    child=replace(consumer(plan,'child','WORKING'),scope=replace(plan.scope,instrument_uid='different'))
    envelope=AllocationEnvelope(allocation_id='mixed',consumer_ids=('parent','child'),reservation_ids=(),
        requirements=(t.Money(amount=D('100'),currency='USDT'),),gross_exposure=t.Money(amount=D('100'),currency='USDT'),evidence_id='mixed')
    assert derive_program_exposure(plan,replace(facts,consumers=(parent,child),allocations=(envelope,)),now=NOW).blockers==('ALLOCATION_MEMBER_SCOPE_MISMATCH',)


def test_shared_funding_does_not_allow_a_smaller_gross_exposure_bound():
    plan=make_plan();facts=facts_for(plan)
    members=(consumer(plan,'parent','NATIVE_PARENT'),consumer(plan,'a','UNKNOWN','70'),consumer(plan,'b','WORKING','70'))
    envelope=AllocationEnvelope(allocation_id='understated-risk',consumer_ids=('parent','a','b'),reservation_ids=(),
        requirements=(t.Money(amount=D('200'),currency='USDT'),),gross_exposure=t.Money(amount=D('100'),currency='USDT'),evidence_id='bound')
    assert derive_program_exposure(plan,replace(facts,consumers=members,allocations=(envelope,)),now=NOW).blockers==('ALLOCATION_EXPOSURE_UNDERBOUND',)


def test_quote_fee_from_sale_proceeds_is_not_assumed_to_need_unrelated_cash():
    plan=make_plan('OCO');facts=facts_for(plan)
    fees=(FeeBound(amount=t.Money(amount=D(1),currency='USDT'),debit='FROM_RECEIPT'),)
    facts=replace(facts,node_facts=tuple(replace(n,fees=fees) for n in facts.node_facts))
    result=derive_program_exposure(plan,facts,now=NOW)
    assert not result.blockers and values(result.program_requirements)=={'BTC':D(1)}
    bad=FeeBound(amount=t.Money(amount=D(1),currency='BNB'),debit='FROM_RECEIPT')
    assert derive_program_exposure(plan,replace(facts,node_facts=tuple(replace(n,fees=(bad,)) for n in facts.node_facts)),now=NOW).blockers==('FEE_RECEIPT_ASSET_MISMATCH',)


def test_fee_cannot_claim_more_assets_than_its_receipt_source():
    plan=make_plan();facts=facts_for(plan)
    fee=FeeBound(amount=t.Money(amount=D(2),currency='BTC'),debit='FROM_RECEIPT')
    result=derive_program_exposure(plan,replace(facts,node_facts=(replace(facts.node_facts[0],fees=(fee,)),)),now=NOW)
    assert result.blockers==('RECEIPT_FEE_EXCEEDS_QUANTITY',)


def test_sell_limit_native_oco_uses_gross_ceiling_not_wire_floor():
    from trading_system.intents.program_compilation import FieldName
    plan=make_plan('OCO')
    assert plan.nodes[0].sizing_price_bound=='83000'
    assert {p.name:p.value for p in plan.nodes[0].parameters}[FieldName.PRICE]=='83000'
    assert D(plan.nodes[0].worst_case_quote_notional)==D('90000')
    result=derive_program_exposure(plan,facts_for(plan),now=NOW)
    assert not result.blockers and result.gross_program.amount==D('90000')
    assert values(result.program_requirements)=={'BTC':D(1)}
    assert 'ENFORCE_EXECUTION_PRICE_ENVELOPE' in result.obligations


def test_usdm_opening_sell_multiplier_uses_independent_gross_ceiling():
    from trading_system.intents.program_compilation import FieldName
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    k['profile']=replace(k['profile'],usd_m=replace(k['profile'].usd_m,contract_multiplier='0.001',quantity_unit='CONTRACT'))
    p=change(p,side='SELL',quantity=QuantitySpec(kind='BASE_QUANTITY',value='100',source_node=None))
    plan=compile_order_program(p,r,c,**k)
    assert D(plan.nodes[0].worst_case_quote_notional)==D('9000')
    assert {x.name:x.value for x in plan.nodes[0].parameters}[FieldName.PRICE]=='81490.00'
    result=derive_program_exposure(plan,facts_for(plan),now=NOW)
    assert not result.blockers and result.gross_program.amount==D('9000')


def sell_without_profile_ceiling():
    p,r,c,k=inputs();k['profile']=replace(k['profile'],execution_price_envelope=None)
    p=change(p,side='SELL',quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None))
    return compile_order_program(p,r,c,**k)


def test_sell_limit_without_profile_or_trusted_node_ceiling_is_blocked():
    plan=sell_without_profile_ceiling()
    assert plan.nodes[0].worst_case_quote_notional is None
    result=derive_program_exposure(plan,facts_for(plan),now=NOW)
    assert result.blockers[0].startswith('SELL_EXECUTION_UPPER_BOUND_MISSING')


def test_fresh_trusted_node_ceiling_can_bound_sell_without_profile_ceiling():
    plan=sell_without_profile_ceiling();facts=facts_for(plan)
    info=replace(facts.node_facts[0],price_upper=t.Price(value=D('90000'),currency='USDT'))
    result=derive_program_exposure(plan,replace(facts,node_facts=(info,)),now=NOW)
    assert not result.blockers and result.gross_program.amount==D('90000')
    assert plan.nodes[0].original.order.price.value=='81490.00'


def test_profile_ceiling_below_sell_limit_cannot_be_used_as_financial_bound():
    from trading_system.intents.product_rules import DecimalRange
    from trading_system.intents.semantic_validation import ProposalRejected
    p,r,c,k=inputs();k['profile']=replace(k['profile'],execution_price_envelope=DecimalRange(minimum='80000',maximum='81000'))
    p=change(p,side='SELL',quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None))
    with pytest.raises(ProposalRejected,match='SELL_LIMIT_EXCEEDS_EXECUTION_UPPER_BOUND'):
        compile_order_program(p,r,c,**k)


def test_trusted_sell_ceiling_may_expand_but_not_shrink_proven_profile_bound():
    plan=make_plan('OCO');facts=facts_for(plan)
    high=tuple(replace(n,price_upper=t.Price(value=D('92000'),currency='USDT')) for n in facts.node_facts)
    result=derive_program_exposure(plan,replace(facts,node_facts=high),now=NOW)
    assert not result.blockers and result.gross_program.amount==D('92000')
    low=tuple(replace(n,price_upper=t.Price(value=D('85000'),currency='USDT')) for n in facts.node_facts)
    assert derive_program_exposure(plan,replace(facts,node_facts=low),now=NOW).blockers==('PRICE_BOUND_CONFLICT',)


def test_old_compiled_sell_floor_roundtrips_but_never_becomes_a_proven_ceiling():
    from trading_system.storage.models import decode
    from trading_system.intents.program_compilation import ProgramCompilation
    from trading_system.contracts.canonical import canonical_hash
    plan=make_plan('OCO')
    old=replace(plan,nodes=tuple(replace(n,worst_case_quote_notional=n.original.order.price.value) for n in plan.nodes),
        required_checks=tuple(c for c in plan.required_checks if c!='SELL_LIMIT_EXECUTION_UPPER_BOUND'))
    raw=domain_payload(old);before=canonical_hash(raw)
    restored=decode(ProgramCompilation,raw)
    assert restored==old and canonical_hash(domain_payload(restored))==before
    result=derive_program_exposure(restored,facts_for(restored),now=NOW)
    assert result.blockers[0].startswith('SELL_EXECUTION_UPPER_BOUND_MISSING')
    facts=facts_for(restored)
    facts=replace(facts,node_facts=tuple(replace(n,price_upper=t.Price(value=D('90000'),currency='USDT')) for n in facts.node_facts))
    result=derive_program_exposure(restored,facts,now=NOW)
    assert not result.blockers and result.gross_program.amount==D('90000')
    assert 'HISTORICAL_SELL_BOUND_REVALIDATION' in result.obligations
    assert canonical_hash(domain_payload(restored))==before and not result.execution_authorized


def test_deferred_receipt_sell_cannot_borrow_its_limit_as_an_upper_bound():
    plan=make_plan('OPO');facts=facts_for(plan)
    facts=replace(facts,node_facts=tuple(replace(n,price_upper=None) for n in facts.node_facts))
    assert derive_program_exposure(plan,facts,now=NOW).blockers[0].startswith('SELL_EXECUTION_UPPER_BOUND_MISSING')
    good=derive_program_exposure(plan,facts_for(plan),now=NOW)
    assert not good.blockers and good.gross_program.amount==D('171490')


def test_buy_limit_remains_its_cap_and_sell_price_upper_requires_correct_currency_and_freshness():
    plan=make_plan();facts=facts_for(plan)
    assert plan.nodes[0].worst_case_quote_notional=='81490.00'
    assert derive_program_exposure(plan,facts,now=NOW).gross_program.amount==D('81490')
    plan=sell_without_profile_ceiling();facts=facts_for(plan)
    bad=replace(facts.node_facts[0],price_upper=t.Price(value=D('90000'),currency='USD'))
    assert derive_program_exposure(plan,replace(facts,node_facts=(bad,)),now=NOW).blockers==('PRICE_CURRENCY_MISMATCH',)
    good=replace(bad,price_upper=t.Price(value=D('90000'),currency='USDT'))
    assert derive_program_exposure(plan,replace(facts,node_facts=(good,),valid_until=NOW),now=NOW).blockers==('FINANCIAL_FACTS_STALE',)


def test_quote_sell_minimum_sizing_bound_stays_distinct_from_gross_quote_budget():
    p,r,c,k=inputs();p=change(p,side='SELL',price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None))
    plan=compile_order_program(p,r,c,**k)
    assert plan.nodes[0].sizing_price_bound=='80000' and plan.nodes[0].worst_case_quote_notional=='4000'

def test_compiled_atomic_fee_is_separate_and_no_authority_created():
    p,r,c,k=inputs();plan=compile_order_program(p,r,c,**k)
    facts=ProgramBudgetFacts(scope=plan.scope,collateral_group_id='group',compilation_hash=compilation_hash(plan),base_asset=r.base_asset,quote_asset=r.quote_asset,
        node_facts=(NodeBudgetFacts(node_id=plan.nodes[0].original.node_id,fees=(FeeBound(amount=t.Money(amount=D('1'),currency=r.quote_asset),debit='EXTERNAL_BALANCE'),),margin=None),),
        asset_limits=(t.Money(amount=D('10000'),currency=r.quote_asset),),consumers=(),reservations=(),allocations=(),
        atomic_groups=(),receipts=(),evidence_id='trusted-fixture',as_of=k['now'],valid_until='2026-10-28T11:00:00Z')
    result=derive_program_exposure(plan,facts,now=k['now'])
    assert not result.blockers
    assert result.program_requirements==(t.Money(amount=D(plan.nodes[0].worst_case_quote_notional)+1,currency=r.quote_asset),)
    assert result.execution_authorized is False
