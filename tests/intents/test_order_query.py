"""Archived original-ID query observations, without transport qualification."""
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.engine.nautilus.native_events import NativeEventBridge
from trading_system.intents.program_compilation import Namespace
from intents.test_management_requests import setup,materialize

NOW='2026-10-28T10:00:00Z'
SOURCE='PAPER_QUERY_FIXTURE_NOT_VENUE_QUALIFICATION'


@pytest.fixture(params=[Namespace.SPOT_EXECUTION,Namespace.USD_M_EXECUTION])
def reader(tmp_path,request):
    plan,k=setup(request.param)
    requests=materialize(plan,**k)
    codec=VenueIdCodec(k['scope'])
    bridge=NativeEventBridge(codec,tmp_path)
    yield requests,bridge,tmp_path
    codec.close()


def row(client='original-client',venue=123,status='WORKING'):
    return dict(algoId=venue,clientAlgoId=client,symbol='BTCUSDT',algoStatus=status,
        executedQty='0.25',feeAsset='BNB',feeAmt='-0.00001')


def archive(reader,index=0,rows=None,total=None,page=1,size=None,payload=None):
    from trading_system.engine.nautilus.order_query import query_page_request,archive_query_page
    requests,bridge,_=reader
    rows=[row()] if rows is None else rows
    body=json.dumps(dict(total=len(rows) if total is None else total,orders=rows)).encode() if payload is None else payload
    request=query_page_request(requests[index],page_number=page,page_size=size)
    return archive_query_page(body,request=request,scan_id='scan-one',bridge=bridge,
        source=SOURCE,received_at=NOW)


def resolve(reader,pages):
    from trading_system.engine.nautilus.order_query import resolve_original_query
    requests,bridge,path=reader
    return resolve_original_query(requests,pages,scope=bridge.codec.scope,scan_id='scan-one',
        source=SOURCE,archive=path,now=NOW)


def test_exact_original_id_query_retains_raw_and_fee_fields_without_ownership(reader):
    pages=(archive(reader),archive(reader,1,rows=[],size=100))
    result=resolve(reader,pages)
    assert result.status=='MATCHED' and len(result.rows)==1
    assert result.rows[0].client_id=='original-client' and result.rows[0].venue_id=='123'
    assert json.loads(result.rows[0].payload_json)['feeAsset']=='BNB'
    assert result.raw_refs==tuple(p.raw.raw_payload_ref for p in pages)
    assert result.reconciliation_required and result.can_resubmit is False
    assert result.ownership_established is False and result.funds_released is False


def test_history_uses_real_page_controls_and_consumes_each_page_once(reader):
    from trading_system.engine.nautilus.order_query import query_page_request
    request=query_page_request(reader[0][1],page_number=2,page_size=1)
    assert dict(request.form_parameters())=={'page':'2','pageSize':'1'}
    pages=(archive(reader,rows=[]),archive(reader,1,rows=[row('unrelated',77)],total=2,size=1),
        archive(reader,1,rows=[row()],total=2,page=2,size=1))
    assert resolve(reader,(*pages,pages[-1])).status=='MATCHED'


@pytest.mark.parametrize('case',['missing_history','gap','wrong_total','overlapping_rows'])
def test_incomplete_or_overlapping_scan_never_means_not_sent(reader,case):
    first=archive(reader,rows=[])
    history=archive(reader,1,rows=[row()],total=2,size=1)
    pages=(first,history)
    if case=='missing_history':pages=(first,)
    if case=='gap':pages=(first,archive(reader,1,rows=[row()],total=3,page=3,size=1))
    if case=='wrong_total':pages=(first,history,archive(reader,1,rows=[],total=3,page=2,size=1))
    if case=='overlapping_rows':pages=(first,history,archive(reader,1,rows=[row()],total=2,page=2,size=1))
    result=resolve(reader,pages)
    assert result.status=='INCOMPLETE' and result.reconciliation_required and not result.can_resubmit


def test_complete_not_found_does_not_release_or_allow_resubmission(reader):
    result=resolve(reader,(archive(reader,rows=[row('other',9)]),archive(reader,1,rows=[],size=100)))
    assert result.status=='NOT_FOUND' and not result.can_resubmit and not result.funds_released


def test_caller_cannot_remove_history_endpoint_from_the_expected_scan(reader):
    from trading_system.engine.nautilus.order_query import resolve_original_query
    requests,bridge,path=reader
    page=archive(reader,rows=[])
    with pytest.raises(ValueError,match='QUERY_ENDPOINT_SET_REQUIRED'):
        resolve_original_query(requests[:1],(page,),scope=bridge.codec.scope,scan_id='scan-one',
            source=SOURCE,archive=path,now=NOW)


def test_conflicting_original_mother_ids_are_not_merged(reader):
    result=resolve(reader,(archive(reader,rows=[row(),row(venue=456)]),archive(reader,1,rows=[],size=100)))
    assert result.status=='CONFLICT' and result.rows==() and result.reconciliation_required


@pytest.mark.parametrize('change',['account','namespace','request','source','scan','tamper_raw','tamper_rows'])
def test_independent_scope_and_archived_page_binding_are_required(reader,change):
    first=archive(reader)
    second=archive(reader,1,rows=[],size=100)
    if change=='account':first=replace(first,raw=replace(first.raw,scope=replace(first.raw.scope,venue_account_id='other')))
    if change=='namespace':first=replace(first,request=replace(first.request,base=replace(first.request.base,
        namespace=Namespace.SPOT_EXECUTION if first.request.base.namespace==Namespace.USD_M_EXECUTION else Namespace.USD_M_EXECUTION)))
    if change=='request':first=replace(first,request=replace(first.request,base=replace(first.request.base,original_client_id='other')))
    if change=='source':first=replace(first,raw=replace(first.raw,source='UNTRUSTED'))
    if change=='scan':first=replace(first,scan_id='another-scan')
    if change=='tamper_raw':(reader[2]/(first.raw.payload_hash+'.raw')).write_bytes(b'changed')
    if change=='tamper_rows':first=replace(first,rows=(replace(first.rows[0],venue_id='456'),))
    with pytest.raises(ValueError):resolve(reader,(first,second))


@pytest.mark.parametrize('payload',[b'{invalid',b'{"total":0,"total":1,"orders":[]}',b'{"code":-1007,"msg":"timeout"}'])
def test_query_errors_are_archived_before_decoding_and_never_not_found(reader,payload):
    with pytest.raises(ValueError):archive(reader,payload=payload)
    assert (reader[2]/(sha256(payload).hexdigest()+'.raw')).read_bytes()==payload


def test_untrusted_large_total_does_not_allocate_the_claimed_number_of_pages(reader):
    result=resolve(reader,(archive(reader,rows=[]),archive(reader,1,rows=[],total=10**50,size=100)))
    assert result.status=='INCOMPLETE' and not result.can_resubmit


def test_client_identity_is_a_string_not_a_coerced_number(reader):
    with pytest.raises(ValueError):archive(reader,rows=[row(client=123)])


@pytest.mark.parametrize('namespace',[n for n in Namespace if n not in (
    Namespace.SPOT_EXECUTION,Namespace.USD_M_EXECUTION)])
def test_single_original_identity_queries_use_product_specific_identity_fields(tmp_path,namespace):
    from trading_system.engine.nautilus.order_query import query_page_request,archive_query_page,resolve_original_query
    plan,k=setup(namespace)
    requests=materialize(plan,**k)
    fields=({'orderListId':123,'listClientOrderId':'original-client','listOrderStatus':'EXECUTING'}
        if namespace==Namespace.SPOT_LIST else {'algoId':123,'clientAlgoId':'original-client','algoStatus':'NEW'}
        if namespace==Namespace.USD_M_CONDITIONAL else {'orderId':123,'clientOrderId':'original-client','status':'NEW'})
    fields['symbol']='BTCUSDT'
    codec=VenueIdCodec(k['scope'])
    try:
        bridge=NativeEventBridge(codec,tmp_path)
        page=archive_query_page(json.dumps(fields).encode(),request=query_page_request(requests[0]),
            scan_id='single',bridge=bridge,source=SOURCE,received_at=NOW)
        result=resolve_original_query(requests,(page,),scope=k['scope'],scan_id='single',
            source=SOURCE,archive=tmp_path,now=NOW)
        assert result.status=='MATCHED' and result.rows[0].venue_id=='123'
    finally:codec.close()
