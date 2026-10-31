"""Raw response candidates are never authenticated venue/financial facts."""
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pytest

from test_native_creation_call import request_for,KINDS
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.engine.nautilus.native_events import NativeEventBridge
from trading_system.intents.program_compilation import Namespace,Operation


def body_for(request):
    ids=request.assigned_ids
    if request.namespace==Namespace.EQUITY:return dict(status='S',orderId='equity-native-id',clientOrderId=ids.members[0].client_id)
    if request.namespace in (Namespace.SPOT_EXECUTION,Namespace.USD_M_EXECUTION):
        return dict(clientAlgoId=ids.parent_client_id,success=True,code=0,msg='OK')
    if request.namespace==Namespace.USD_M_CONDITIONAL:
        return dict(algoId=71,clientAlgoId=ids.parent_client_id,symbol='BTCUSDT',algoStatus='NEW',actualOrderId='72')
    rows=[dict(orderId=10+i,clientOrderId=m.client_id,symbol='BTCUSDT',status='FILLED',executedQty='2000000',
        fills=[dict(qty='2000000',price='90000',commission='-0.1',commissionAsset='BNB')]) for i,m in enumerate(ids.members)]
    if request.namespace==Namespace.SPOT_LIST:return dict(orderListId=99,listClientOrderId=ids.parent_client_id,symbol='BTCUSDT',orders=rows)
    if request.operation==Operation.BATCH:return rows
    return rows[0]


def parse(tmp_path,kind,body=None,*,payload=None,http_status=200):
    from trading_system.engine.nautilus.native_response import archive_creation_response
    request,now=request_for(kind);codec=VenueIdCodec(request.scope)
    if payload is None:payload=json.dumps(body_for(request) if body is None else body).encode('utf-8')
    try:result=archive_creation_response(payload,request=request,bridge=NativeEventBridge(codec,tmp_path),
        received_at=now,source='UNAUTHENTICATED_SDK_SCHEMA_FIXTURE',http_status=http_status)
    finally:codec.close()
    assert Path(result.raw.raw_payload_ref.id).read_bytes()==payload
    assert result.raw.payload_hash==sha256(payload).hexdigest()
    return result


@pytest.mark.parametrize('kind',KINDS)
def test_every_creation_family_retains_raw_ids_and_never_turns_ack_into_financial_authority(tmp_path,kind):
    result=parse(tmp_path,kind)
    assert result.status in ('ACK_ONLY','INCOMPLETE') and result.identities
    assert not result.source_verified and not result.execution_authorized and not result.funds_release_authorized
    assert result.fills==() and result.orders==() and 'QUERY_ORIGINAL_IDS' in result.retained_duties
    if kind=='equity':assert result.lifecycle_status is None and result.body['status']=='S'
    if '-' in kind:
        assert result.identities[0].venue_id is None and 'VENUE_ID_QUERY_REQUIRED' in result.retained_duties
    if kind=='conditional':assert result.observed_venue_child_ids==('72',) and not result.child_submission_authorized


@pytest.mark.parametrize('kind',['spot','OCO','conditional','spot-TWAP','equity'])
def test_foreign_original_ids_are_conflicts_with_all_raw_bytes_preserved(tmp_path,kind):
    request,_=request_for(kind);body=body_for(request)
    key='listClientOrderId' if kind=='OCO' else 'clientAlgoId' if kind in ('conditional','spot-TWAP') else 'clientOrderId'
    body[key]='foreign-original'
    result=parse(tmp_path,kind,body)
    assert result.status=='CONFLICT' and not result.identities and not result.source_verified


def test_list_duplicate_or_missing_members_never_imply_complete_native_parent_outcome(tmp_path):
    request,_=request_for('OTOCO');body=body_for(request)
    body['orders'].append(body['orders'][0])
    assert parse(tmp_path,'OTOCO',body).status=='CONFLICT'
    body=body_for(request);body['orders']=body['orders'][:1]
    result=parse(tmp_path,'OTOCO',body)
    assert result.status=='INCOMPLETE' and not result.child_submission_authorized


@pytest.mark.parametrize('payload',[b'not-json',b'{"a":1,"a":2}',b'{"v":NaN}',b'{"v":Infinity}'])
def test_invalid_or_ambiguous_source_is_archived_before_error_classification(tmp_path,payload):
    result=parse(tmp_path,'spot',payload=payload)
    assert result.status=='ERROR' and not result.identities and not result.source_verified


def test_raw_fractional_economics_overfills_and_fee_rebates_are_never_capped_or_coerced_to_float(tmp_path):
    request,_=request_for('spot');body=body_for(request)
    body['precise_fraction']='PLACEHOLDER'
    payload=json.dumps(body).replace('"PLACEHOLDER"','12345678901234567890.123456789').encode()
    result=parse(tmp_path,'spot',payload=payload)
    assert result.body['precise_fraction']==Decimal('12345678901234567890.123456789')
    assert result.body['executedQty']=='2000000' and result.body['fills'][0]['commission']=='-0.1'
    assert result.fills==() and not result.funds_release_authorized


def test_error_ack_is_not_not_sent_or_a_cancelled_order(tmp_path):
    request,_=request_for('equity');body=body_for(request);body['status']='F'
    result=parse(tmp_path,'equity',body,http_status=400)
    assert result.status=='ERROR' and result.lifecycle_status is None and 'QUERY_ORIGINAL_IDS' in result.retained_duties


@pytest.mark.parametrize('order',[(0,1,2),(0,2,1),(1,0,2),(1,2,0),(2,0,1),(2,1,0)])
def test_batch_identity_conflict_is_not_downgraded_by_later_item_error(tmp_path,order):
    request,_=request_for('batch')
    valid=body_for(request)[0]
    foreign={**valid,'clientOrderId':'foreign-original'}
    rejected={'code':-2010,'msg':'rejected'}
    rows=[valid,foreign,rejected]
    result=parse(tmp_path,'batch',[rows[i] for i in order])
    assert result.status=='CONFLICT'
    assert result.identities==()
    assert 'NATIVE_ORDER_CLIENT_CHANGED' in result.discrepancies
    assert 'NATIVE_BATCH_ITEM_ERROR' in result.discrepancies
    assert not result.can_resubmit and not result.funds_release_authorized
