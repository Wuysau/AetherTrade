from dataclasses import replace
import pytest
from test_program_exposure import make_plan
from test_program_compilation import inputs
from trading_system.contracts import types as t
from trading_system.contracts.order_program import TimeInForceSpec
from trading_system.intents.program_compilation import compile_order_program,FieldName,Timing
from trading_system.intents.local_child_compilation import compile_local_child

@pytest.mark.parametrize('product',[t.Product.SPOT,t.Product.USD_M_PERP])
@pytest.mark.parametrize('template',['TWAP','VP'])
def test_local_child_uses_existing_order_compiler_without_parent_send(product,template):
    parent=make_plan(template,'LOCAL_SYNTHETIC',product);_,rules,caps,k=inputs(product)
    result=compile_local_child(parent,'0.1',rules,caps,**k)
    assert result.parent==parent and result.node.quantity.value=='0.1'
    assert result.request.timing==Timing.ALGORITHM_CHILD
    assert result.request.requires_independent_command and not result.request.native_mother
    assert result.request.generated_child_origin=='APPLICATION_GENERATED_CHILD'
    assert result.node.original.order==replace(parent.nodes[0].original.order,quantity=result.node.quantity)
    assert not result.execution_authorized

@pytest.mark.parametrize('quantity',['0','-1','1.1','0.000000001','NaN'])
def test_child_quantity_cannot_exceed_pool_or_round_silently(quantity):
    parent=make_plan('TWAP','LOCAL_SYNTHETIC');_,rules,caps,k=inputs(t.Product.SPOT)
    with pytest.raises(ValueError):compile_local_child(parent,quantity,rules,caps,**k)

def test_native_algorithm_cannot_be_resubmitted_as_local_child():
    parent=make_plan('TWAP');_,rules,caps,k=inputs(t.Product.SPOT)
    with pytest.raises(ValueError,match='LOCAL_CHILD_PARENT_REQUIRED'):
        compile_local_child(parent,'0.1',rules,caps,**k)

def test_local_limit_child_preserves_explicit_limit_and_tif():
    parent=make_plan('TWAP','LOCAL_SYNTHETIC');p,r,c,k=inputs(t.Product.SPOT)
    node=replace(parent.original_program.nodes[0],order=replace(p.program.nodes[0].order,
        quantity=parent.nodes[0].quantity,tif=TimeInForceSpec(kind='GTC',expires_at=None)))
    proposal=replace(parent.original_proposal,program=replace(parent.original_program,nodes=(node,)))
    parent=compile_order_program(proposal,r,c,**k)
    result=compile_local_child(parent,'0.1',r,c,**k)
    fields={p.name:p.value for p in result.request.parameters}
    assert fields[FieldName.PRICE]==node.order.price.value
    assert fields[FieldName.TIME_IN_FORCE]=='GTC' and fields[FieldName.TYPE]=='LIMIT'
