from dataclasses import replace
import pytest
from test_program_exposure import make_plan
from test_program_compilation import inputs
from trading_system.contracts import types as t
from trading_system.intents.local_child_compilation import compile_local_child
from trading_system.execution.local_child_requests import materialize_local_child_request,verify_local_child_request


def setup():
    p=make_plan('TWAP','LOCAL_SYNTHETIC');_,r,c,k=inputs(t.Product.SPOT)
    return compile_local_child(p,'0.1',r,c,**k),r,c,k


def test_child_identity_is_stable_distinct_and_never_invents_native_parent():
    child,r,c,k=setup()
    a=materialize_local_child_request(child,'activation-1','child-1',r,c,**k)
    b=materialize_local_child_request(child,'activation-1','child-2',r,c,**k)
    assert a==materialize_local_child_request(child,'activation-1','child-1',r,c,**k)
    assert a.request.assigned_ids.parent_client_id is None
    assert a.request.assigned_ids.members[0].client_id!=b.request.assigned_ids.members[0].client_id
    assert dict(a.request.form_parameters())['quantity']=='0.1'
    assert not a.execution_authorized and not a.request.execution_authorized
    assert verify_local_child_request(a,child,r,c,**k)

@pytest.mark.parametrize('fault',['quantity','command','activation','client'])
def test_original_child_request_tampering_is_rejected(fault):
    child,r,c,k=setup();a=materialize_local_child_request(child,'activation-1','child-1',r,c,**k)
    if fault=='quantity':a=replace(a,request=replace(a.request,fields=tuple(replace(f,value='1') if f.name=='quantity' else f for f in a.request.fields)))
    if fault=='command':a=replace(a,request=replace(a.request,command_id='other'))
    if fault=='activation':a=replace(a,activation_id='other')
    if fault=='client':a=replace(a,request=replace(a.request,assigned_ids=replace(a.request.assigned_ids,members=())))
    with pytest.raises(ValueError):verify_local_child_request(a,child,r,c,**k)
