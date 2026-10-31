from dataclasses import replace
from decimal import Decimal as D,localcontext
import pytest
from test_local_child_request import setup
from trading_system.intents.local_child_compilation import compile_local_child
from trading_system.execution.local_child_requests import materialize_local_child_request
from trading_system.execution.local_child_quantity import LocalChildCommitment,preview_local_child_quantity


def commitment(qty,command='one',activation='activation'):
    original,r,c,k=setup();child=compile_local_child(original.parent,qty,r,c,**k)
    request=materialize_local_child_request(child,activation,command,r,c,**k)
    return LocalChildCommitment(compilation=child,request=request)


def test_pre_ack_commitments_consume_exact_pool_and_replay_does_not_allocate_twice():
    a=commitment('0.4');b=commitment('0.6','two');parent=a.compilation.parent
    with localcontext() as ctx:
        ctx.prec=1
        result=preview_local_child_quantity(parent,'activation',(a,),b)
    assert D(result.committed_quantity)==1 and D(result.unallocated_quantity)==0
    assert not result.duplicate and not result.execution_authorized
    replay=preview_local_child_quantity(parent,'activation',(a,b),b)
    assert replay.duplicate and D(replay.committed_quantity)==1


def test_unresolved_original_request_is_never_implicitly_released():
    a=commitment('0.6');b=commitment('0.5','two')
    with pytest.raises(ValueError,match='QUANTITY_POOL_EXCEEDED'):
        preview_local_child_quantity(a.compilation.parent,'activation',(a,),b)

@pytest.mark.parametrize('fault',['duplicate_history','changed_command','foreign_activation','foreign_parent','field_quantity'])
def test_corrupt_commitment_set_never_becomes_headroom(fault):
    a=commitment('0.4');b=commitment('0.5','two');history=(a,);parent=a.compilation.parent
    if fault=='duplicate_history':history=(a,a)
    if fault=='changed_command':b=commitment('0.5','one')
    if fault=='foreign_activation':b=commitment('0.5','two','other')
    if fault=='foreign_parent':parent=replace(parent,program_hash='f'*64)
    if fault=='field_quantity':b=replace(b,request=replace(b.request,request=replace(b.request.request,
        fields=tuple(replace(f,value='0.01') if f.name=='quantity' else f for f in b.request.request.fields))))
    with pytest.raises(ValueError):preview_local_child_quantity(parent,'activation',history,b)



def test_final_commitment_bound_preserves_exact_replay_at_capacity(monkeypatch):
    from trading_system.execution import local_child_quantity as module
    # Small limit exercises final-set behavior without 4096 large source archives.
    monkeypatch.setattr(module,'MAX_LOCAL_CHILD_COMMITMENTS',1,raising=False)
    a=commitment('0.4');b=commitment('0.4','two');parent=a.compilation.parent
    assert preview_local_child_quantity(parent,'activation',(a,),a).duplicate
    with pytest.raises(ValueError,match='LOCAL_CHILD_COMMITMENT_BOUND'):
        preview_local_child_quantity(parent,'activation',(a,),b)
