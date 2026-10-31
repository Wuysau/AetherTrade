from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_program_target_allocation import plan_for,scenario
from test_program_compilation import inputs
from trading_system.contracts import types as t
from trading_system.intents.local_child_compilation import compile_local_child
from trading_system.intents.program_target_allocation import compile_local_child_target,compile_program_target


def case(quantity='0.2'):
    parent,rules=plan_for(template='TWAP',mode='LOCAL_SYNTHETIC',target='1',quantity='1')
    _,_,caps,k=inputs(t.Product.SPOT)
    child=compile_local_child(parent,quantity,rules,caps,**k)
    book,facts=scenario(parent,rules,actual='.4',working='.3')
    return child,book,facts


def test_child_uses_current_actual_and_working_without_resizing_parent():
    child,book,facts=case()
    result=compile_local_child_target(child,book,facts)
    assert result.delta.actual.value==D('.4') and result.delta.working.value==D('.3')
    assert result.delta.remaining.value==D('.3')
    assert result.child_quantity.value==D('.2') and result.unallocated_target_quantity.value==D('.1')
    assert child.parent.nodes[0].quantity.value=='1'
    assert not result.execution_authorized
    with pytest.raises(ValueError,match='PROGRAM_NODE_QUANTITY_REPLAN_REQUIRED'):
        compile_program_target(child.parent,book,facts)

@pytest.mark.parametrize('fault',['oversize','unknown','book_hash','direction'])
def test_child_allocation_rejects_current_target_ambiguity(fault):
    child,book,facts=case('0.4' if fault=='oversize' else '0.2')
    if fault=='unknown':facts=replace(facts,unknown_command_ids=('unknown-child',))
    if fault=='book_hash':facts=replace(facts,book_hash='f'*64)
    if fault=='direction':child=replace(child,node=replace(child.node,original=replace(child.node.original,
        order=replace(child.node.original.order,side='SELL'))))
    with pytest.raises(ValueError):compile_local_child_target(child,book,facts)
