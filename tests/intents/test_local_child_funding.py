from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_program_exposure import make_plan,facts_for,NOW,values
from trading_system.contracts import types as t
from trading_system.risk.program_exposure import ExistingConsumer,AllocationEnvelope,preview_local_child_funding


def scenario():
    plan=make_plan('TWAP','LOCAL_SYNTHETIC');facts=facts_for(plan)
    money=lambda x:t.Money(amount=D(x),currency='USDT')
    parent=ExistingConsumer(consumer_id='parent',scope=plan.scope,collateral_group_id=facts.collateral_group_id,
        kind='LOCAL_PARENT',requirements=(money('90000'),),gross_exposure=money('90000'),evidence_id='protected-parent')
    old=replace(parent,consumer_id='old-child',kind='UNKNOWN',requirements=(money('40000'),),gross_exposure=money('40000'))
    envelope=AllocationEnvelope(allocation_id='pool',consumer_ids=('parent','old-child'),reservation_ids=(),
        requirements=(money('90000'),),gross_exposure=money('90000'),evidence_id='protected-pool',program_hash=plan.program_hash)
    facts=replace(facts,consumers=(parent,old),allocations=(envelope,))
    candidate=replace(old,consumer_id='new-child',kind='LOCAL_CHILD',requirements=(money('50000'),),gross_exposure=money('50000'))
    return plan,facts,candidate


def test_existing_unknown_and_new_child_share_existing_parent_envelope_once():
    p,f,c=scenario();result=preview_local_child_funding(p,f,'pool',c,now=NOW)
    assert not result.blockers
    assert values(result.total_requirements)=={'USDT':D('90000')}
    assert values(result.additional_requirements)=={}
    assert len(f.consumers)==2 and not result.execution_authorized


def test_candidate_cannot_spend_budget_already_committed_to_unknown():
    p,f,c=scenario();c=replace(c,requirements=(t.Money(amount=D('50001'),currency='USDT'),))
    result=preview_local_child_funding(p,f,'pool',c,now=NOW)
    assert 'ALLOCATION_FUNDING_UNDERBOUND' in result.blockers

@pytest.mark.parametrize('fault',['duplicate','foreign','origin','missing_pool','wrong_program'])
def test_child_pool_binding_is_explicit(fault):
    p,f,c=scenario();pool='pool'
    if fault=='duplicate':c=replace(c,consumer_id='old-child')
    if fault=='foreign':c=replace(c,scope=replace(c.scope,venue_account_id='foreign'))
    if fault=='origin':c=replace(c,kind='NATIVE_PARENT')
    if fault=='missing_pool':pool='absent'
    if fault=='wrong_program':f=replace(f,allocations=(replace(f.allocations[0],program_hash='f'*64),))
    with pytest.raises(ValueError):preview_local_child_funding(p,f,pool,c,now=NOW)
