"""Native OCO uses alternative full legs; source proof is mandatory for ALLOW."""
from dataclasses import replace
from decimal import Decimal as D
import pytest

from test_program_target_allocation import plan_for,scenario
from test_program_risk_assessment import risk_case,assess
from trading_system.intents.program_target_allocation import compile_program_target
from trading_system.risk.program_exposure import derive_program_exposure


def test_native_oco_preserves_each_original_leg_and_allocates_one_target_delta():
    plan,rules=plan_for(template='OCO');book,facts=scenario(plan,rules,actual='2')
    result=compile_program_target(plan,book,facts)
    assert not result.blockers,result.blockers
    assert result.delta.remaining.value==D('-1')
    assert len(result.nodes)==2 and all(n.ledger_quantity.value==1 and n.side=='SELL' for n in result.nodes)
    assert len(result.requests)==1 and result.requests[0].original.native_mother
    assert 'NATIVE_EXCLUSIVE_FINANCIAL_PROOF_REQUIRED' in result.obligations
    assert not result.execution_authorized


def test_genuine_native_oco_assessment_needs_the_exact_atomic_group_source_proof():
    c=risk_case(template='OCO',actual='2',target='1')
    decision=assess(c)
    assert decision.verdict=='ALLOW',decision.rejection_codes
    assert decision.metrics.exposure.net.amount>0
    assert decision.metrics.active_order_count>=2
    assert c.facts.budget_facts.atomic_groups
    facts=replace(c.facts.budget_facts,atomic_groups=())
    c.facts=replace(c.facts,budget_facts=facts)
    c.exposure=derive_program_exposure(c.plan,facts,now=c.facts.inputs.clock.utc_now)
    result=assess(c)
    assert result.verdict=='DENY' and 'NATIVE_EXCLUSIVE_TARGET_PROOF_REQUIRED' in result.rejection_codes


def test_native_oco_flat_target_retains_no_fill_position_loss_and_real_protection_duties():
    c=risk_case(template='OCO',actual='1',target='0')
    result=assess(c)
    assert result.verdict=='ALLOW',result.rejection_codes
    assert result.metrics.planned_loss.amount>0  # Conditional exit has not filled.
    assert result.metrics.exposure.net.amount>=D('81490')
    c.facts=replace(c.facts,protection=())
    denied=assess(c)
    assert denied.verdict=='DENY' and 'PROGRAM_RISK_PARTIAL_OR_RETAINED_PROTECTION_MISSING' in denied.rejection_codes


@pytest.mark.parametrize('fault',['missing_stop','bad_stop','wrong_group','local_race'])
def test_native_oco_cannot_erase_stop_risk_or_borrow_another_group_proof(fault):
    kw={'missing_stop':True} if fault=='missing_stop' else {'stop':'90000'} if fault=='bad_stop' else {}
    c=risk_case(template='OCO',actual='1',target='0',mode='LOCAL_SYNTHETIC' if fault=='local_race' else 'VENUE_NATIVE',**kw)
    if fault=='wrong_group':
        groups=tuple(replace(g,node_ids=('above','foreign')) for g in c.facts.budget_facts.atomic_groups)
        c.facts=replace(c.facts,budget_facts=replace(c.facts.budget_facts,atomic_groups=groups))
    result=assess(c)
    assert result.verdict=='DENY' and result.metrics is None
