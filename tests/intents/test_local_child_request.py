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


def test_historical_child_restore_needs_no_current_rules_or_clock():
    from trading_system.execution.local_child_requests import restore_local_child_request
    child,r,c,k=setup();a=materialize_local_child_request(child,'activation-1','child-1',r,c,**k)
    expired={**k,'now':'2027-01-01T00:00:00Z'}
    with pytest.raises(ValueError):verify_local_child_request(a,child,r,c,**expired)
    restored=restore_local_child_request(a,child,activation_id='activation-1',command_id='child-1')
    assert restored is a and not restored.execution_authorized

@pytest.mark.parametrize('fault',['activation','command','quantity'])
def test_historical_restore_is_bound_to_original_source_identity(fault):
    from trading_system.execution.local_child_requests import restore_local_child_request
    child,r,c,k=setup();a=materialize_local_child_request(child,'activation-1','child-1',r,c,**k)
    if fault=='quantity':a=replace(a,request=replace(a.request,fields=tuple(replace(f,value='2') if f.name=='quantity' else f for f in a.request.fields)))
    with pytest.raises(ValueError):restore_local_child_request(a,child,
        activation_id='other' if fault=='activation' else 'activation-1',
        command_id='other' if fault=='command' else 'child-1')



def test_local_conditional_submission_has_own_algo_identity_and_preserves_trigger():
    from test_program_compilation import trigger
    from trading_system.intents.program_compilation import compile_order_program,Namespace
    from trading_system.engine.nautilus.native_creation import creation_call
    parent=make_plan('TWAP','LOCAL_SYNTHETIC',t.Product.USD_M_PERP)
    _,rules,caps,k=inputs(t.Product.USD_M_PERP)
    root=parent.original_program.nodes[0]
    node=replace(root,order=replace(root.order,trigger=trigger(source='MARK_PRICE')))
    proposal=replace(parent.original_proposal,program=replace(parent.original_program,nodes=(node,)))
    parent=compile_order_program(proposal,rules,caps,**k)
    child=compile_local_child(parent,'0.1',rules,caps,**k)
    record=materialize_local_child_request(child,'activation-1','conditional-child-1',rules,caps,**k)
    request=record.request
    assert child.node.trusted_origin=='APPLICATION_GENERATED_CHILD'
    assert child.request.native_mother and child.request.generated_child_origin=='VENUE_GENERATED_CHILD'
    assert request.namespace==Namespace.USD_M_CONDITIONAL
    assert request.assigned_ids.parent_client_id and request.assigned_ids.members==()
    fields=dict(request.form_parameters())
    assert fields['clientAlgoId']==request.assigned_ids.parent_client_id
    assert fields['triggerPrice']=='81330.00' and fields['workingType']=='MARK_PRICE'
    assert fields['type']=='STOP_MARKET' and fields['quantity']=='0.1'
    call=creation_call(request,clock=t.ClockReading(utc_now=k['now'],monotonic_ns=1,clock_health='HEALTHY'))
    assert call.endpoint.value=='/fapi/v1/algoOrder'
    assert not call.execution_authorized
    assert verify_local_child_request(record,child,rules,caps,**k)
