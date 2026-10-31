from dataclasses import replace
from pathlib import Path
import json

import pytest

from test_management_requests import setup,materialize
from trading_system.intents.order_management_compilation import ManagementKind
from trading_system.intents.program_compilation import Namespace
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.engine.nautilus.native_events import NativeEventBridge


def observe(tmp_path,namespace,kind,body,*,http_status=200):
    from trading_system.engine.nautilus.native_management_response import archive_management_response
    plan,k=setup(namespace,kind);request,=materialize(plan,**k);codec=VenueIdCodec(request.scope)
    payload=json.dumps(body).encode()
    try:result=archive_management_response(payload,request=request,bridge=NativeEventBridge(codec,tmp_path),
        source='UNAUTHENTICATED_MANAGEMENT_SCHEMA_FIXTURE',received_at=k['now'],http_status=http_status)
    finally:codec.close()
    assert Path(result.raw.raw_payload_ref.id).read_bytes()==payload
    assert not result.source_verified and not result.funds_release_authorized and not result.can_resubmit
    assert result.fills==() and result.orders==()
    return result


@pytest.mark.parametrize('cancel,new,http_status',[('SUCCESS','SUCCESS',200),('SUCCESS','FAILURE',409),
    ('FAILURE','SUCCESS',409),('FAILURE','FAILURE',400),('FAILURE','NOT_ATTEMPTED',400)])
def test_cancel_replace_retains_independent_outcomes_even_for_http_error_envelopes(tmp_path,cancel,new,http_status):
    plan,k=setup(Namespace.SPOT_ORDINARY,ManagementKind.CANCEL_REPLACE);request,=materialize(plan,**k)
    data=dict(cancelResult=cancel,newOrderResult=new,
        cancelResponse=dict(orderId=123,origClientOrderId=request.original_client_id,clientOrderId='server-cancel-id',executedQty='0.4') if cancel=='SUCCESS' else dict(code=-2011,msg='cancel failed'),
        newOrderResponse=dict(orderId=124,clientOrderId=dict(request.form_parameters())['newClientOrderId'],executedQty='1.7',feeAsset='BNB',feeAmount='-.01') if new=='SUCCESS' else dict(code=-2010,msg='new failed'))
    result=observe(tmp_path,Namespace.SPOT_ORDINARY,ManagementKind.CANCEL_REPLACE,
        data if http_status==200 else dict(code=-2021,msg='partial result',data=data),http_status=http_status)
    assert tuple(o.outcome for o in result.operations)==(cancel,new)
    assert result.status in ('ACK_ONLY','PARTIAL')
    assert 'CANCEL_AND_REPLACEMENT_RECONCILIATION_REQUIRED' in result.retained_duties
    assert result.operations[0].payload==data['cancelResponse'] and result.operations[1].payload==data['newOrderResponse']
    if new=='NOT_ATTEMPTED':assert 'NOT_ATTEMPTED_BUDGET_RECONCILIATION_REQUIRED' in result.retained_duties


@pytest.mark.parametrize('namespace',list(Namespace))
def test_single_cancel_ack_does_not_become_cancelled_lifecycle_or_release(namespace,tmp_path):
    if namespace==Namespace.EQUITY:body=dict(orderId='123',status='S')
    elif namespace in (Namespace.SPOT_EXECUTION,Namespace.USD_M_EXECUTION):body=dict(algoId=123,success=True,code=0,msg='OK')
    elif namespace==Namespace.USD_M_CONDITIONAL:body=dict(algoId=123,clientAlgoId='original-client',code='200',msg='OK')
    elif namespace==Namespace.SPOT_LIST:body=dict(orderListId=123,listClientOrderId='original-client',listOrderStatus='ALL_DONE',orders=[])
    else:body=dict(orderId=123,origClientOrderId='original-client',clientOrderId='server-cancel-id',status='CANCELED',executedQty='0.4')
    result=observe(tmp_path,namespace,ManagementKind.CANCEL,body)
    assert result.status=='ACK_ONLY' and result.lifecycle_status is None


@pytest.mark.parametrize('fault',['cancel_id','old_client','new_client','new_id_is_old','unknown_result'])
def test_cross_target_or_ambiguous_cancel_replace_data_is_not_adopted(tmp_path,fault):
    plan,k=setup(Namespace.SPOT_ORDINARY,ManagementKind.CANCEL_REPLACE);request,=materialize(plan,**k)
    body=dict(cancelResult='SUCCESS',newOrderResult='SUCCESS',
        cancelResponse=dict(orderId=123,origClientOrderId='original-client'),
        newOrderResponse=dict(orderId=124,clientOrderId=dict(request.form_parameters())['newClientOrderId']))
    if fault=='cancel_id':body['cancelResponse']['orderId']=777
    if fault=='old_client':body['cancelResponse']['origClientOrderId']='foreign'
    if fault=='new_client':body['newOrderResponse']['clientOrderId']='foreign'
    if fault=='new_id_is_old':body['newOrderResponse']['orderId']=123
    if fault=='unknown_result':body['newOrderResult']='MAYBE'
    result=observe(tmp_path,Namespace.SPOT_ORDINARY,ManagementKind.CANCEL_REPLACE,body)
    assert result.status in ('CONFLICT','INCOMPLETE')


@pytest.mark.parametrize('namespace',[Namespace.SPOT_EXECUTION,Namespace.USD_M_CONDITIONAL])
def test_boolean_code_is_not_an_integer_success_code(tmp_path,namespace):
    result=observe(tmp_path,namespace,ManagementKind.CANCEL,
        dict(algoId=123,clientAlgoId='original-client',success=True,code=False,msg='ambiguous'))
    assert result.status=='ERROR'
