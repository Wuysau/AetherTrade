from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from test_order_query import reader,row,NOW,SOURCE
from trading_system.engine.nautilus.order_query import query_page_request,archive_query_page


def page(reader,index=0,*,rows=None,total=None,number=1,size=None,payload=None):
    from trading_system.engine.nautilus.native_query_candidates import archive_native_query_candidate
    requests,bridge,_=reader;rows=[row()] if rows is None else rows
    rows=[dict(r,positionSide=r.get('positionSide','BOTH')) for r in rows]
    if payload is None:payload=json.dumps(dict(total=len(rows) if total is None else total,orders=rows)).encode()
    request=query_page_request(requests[index],page_number=number,page_size=size)
    return archive_native_query_candidate(payload,request=request,scan_id='v2-scan',bridge=bridge,source=SOURCE,received_at=NOW)


def resolve(reader,pages):
    from trading_system.engine.nautilus.native_query_candidates import resolve_native_query_candidates
    requests,bridge,path=reader
    return resolve_native_query_candidates(requests,pages,scope=bridge.codec.scope,scan_id='v2-scan',source=SOURCE,archive=path,now=NOW)


def test_versioned_query_keeps_exact_fractional_numbers_and_v1_raw_hashes_unchanged(reader):
    requests,bridge,path=reader;value=row();value['numeric_fee']='PLACEHOLDER';value['positionSide']='BOTH'
    payload=json.dumps(dict(total=1,orders=[value])).replace('"PLACEHOLDER"','-0.00000000000000000123456789').encode()
    original_request=query_page_request(requests[0])
    # Retain V1's strict old profile and raw evidence; no reinterpretation.
    with pytest.raises(ValueError):archive_query_page(payload,request=original_request,scan_id='old-scan',bridge=bridge,source=SOURCE,received_at=NOW)
    current=page(reader,payload=payload);history=page(reader,1,rows=[],size=100)
    result=resolve(reader,(current,history))
    assert current.contract_version=='2026-10-30' and result.status=='MATCHED'
    assert result.rows[0].payload['numeric_fee']==Decimal('-0.00000000000000000123456789')
    assert Path(current.raw.raw_payload_ref.id).read_bytes()==payload
    assert not result.source_verified and not result.can_resubmit and not result.funds_release_authorized


@pytest.mark.parametrize('fault',['missing_history','gap','duplicate_identity','huge_remote_total','typed_body_forgery','wrong_scope','raw_tamper'])
def test_received_page_bounds_and_original_source_still_control_query_candidates(reader,fault):
    first=page(reader);last=page(reader,1,rows=[],size=100);pages=(first,last)
    if fault=='missing_history':pages=(first,)
    if fault=='gap':pages=(first,page(reader,1,rows=[row()],number=3,size=1,total=3))
    if fault=='duplicate_identity':pages=(first,page(reader,1,rows=[row(),row()],size=100,total=2))
    if fault=='huge_remote_total':pages=(first,page(reader,1,rows=[],size=1,total=10**80))
    if fault=='typed_body_forgery':first=replace(first,body={**first.body,'total':True});pages=(first,last)
    if fault=='wrong_scope':first=replace(first,raw=replace(first.raw,scope=replace(first.raw.scope,venue_account_id='foreign')));pages=(first,last)
    if fault=='raw_tamper':Path(first.raw.raw_payload_ref.id).write_bytes(b'changed')
    if fault in ('typed_body_forgery','wrong_scope','raw_tamper'):
        with pytest.raises(ValueError):resolve(reader,pages)
    else:
        result=resolve(reader,pages)
        assert result.status=='INCOMPLETE' and not result.source_verified


def test_complete_empty_scan_keeps_original_reconciliation_and_never_becomes_not_sent(reader):
    result=resolve(reader,(page(reader,rows=[]),page(reader,1,rows=[],size=100)))
    assert result.status=='NOT_FOUND' and result.reconciliation_required
    assert not result.can_resubmit and result.fills==() and result.orders==()


def test_identical_payload_cannot_hide_changed_pagination_request_on_replay(reader):
    current=page(reader,rows=[]);history=page(reader,1,size=100)
    changed=replace(history,request=replace(history.request,page_size=1))
    with pytest.raises(ValueError):resolve(reader,(current,history,changed))
