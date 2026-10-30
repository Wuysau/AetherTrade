"""Versioned, lossless original-ID query candidates; V1 stays unchanged.

Decimal JSON values retain their numeric type in this internal candidate model.
Raw-byte replay and typed comparison avoid equating a JSON number with a string
or bool. Structural matching never authenticates a venue or grants ownership.
"""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.execution.management_requests import VenueManagementRequest
from trading_system.intents.program_compilation import Namespace as N
from .native_events import NativeEventBridge
from .native_response import _object,_nonfinite,_identifier
from .order_query import QueryPageRequest,query_page_request,COLLECTIONS,SINGLES


def require(condition,code):
    if not condition:raise ValueError(code)


def digest(record):return canonical_hash(domain_payload(record))


def _typed_same(left,right):
    if type(left) is not type(right):return False
    if type(left) is dict:
        return left.keys()==right.keys() and all(_typed_same(left[k],right[k]) for k in left)
    if type(left) is list:
        return len(left)==len(right) and all(_typed_same(a,b) for a,b in zip(left,right))
    return type(left) in (str,int,bool,Decimal,type(None)) and left==right


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeQueryCandidatePageV2:
    request:QueryPageRequest
    scan_id:str
    raw:t.RawVenueMessage
    body:object
    contract_version:Literal['2026-10-30']='2026-10-30'
    record_kind:Literal['NATIVE_QUERY_CANDIDATE_PAGE']='NATIVE_QUERY_CANDIDATE_PAGE'

    @property
    def source_verified(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeQueryRowCandidateV2:
    venue_id:str
    client_id:str
    symbol:str
    status:str
    position_side:str|None
    payload:object


@dataclass(frozen=True,slots=True,kw_only=True)
class OriginalNativeQueryCandidateV2:
    status:Literal['MATCHED','NOT_FOUND','INCOMPLETE','CONFLICT']
    rows:tuple[NativeQueryRowCandidateV2,...]
    raw_refs:tuple[t.EvidenceRef,...]
    observed_venue_child_ids:tuple[str,...]
    retained_duties:tuple[str,...]=('QUERY_ORIGINAL_IDS','SOURCE_AUTHENTICATION_REQUIRED',
        'COMPLETE_ACCOUNT_AND_CHILD_HISTORY_REQUIRED','RESERVATION_RECONCILIATION_REQUIRED')

    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def reconciliation_required(self)->Literal[True]:return True
    @property
    def can_resubmit(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def child_submission_authorized(self)->Literal[False]:return False
    @property
    def ownership_established(self)->Literal[False]:return False
    @property
    def fills(self):return ()
    @property
    def orders(self):return ()


def _decode(payload,request):
    require(type(payload) is bytes and len(payload)<=1_048_576,'NATIVE_QUERY_PAYLOAD_BOUND')
    require(type(request) is QueryPageRequest,'NATIVE_QUERY_PAGE_REQUEST_REQUIRED')
    expected=query_page_request(request.base,page_number=request.page_number,page_size=request.page_size)
    require(digest(expected)==digest(request),'NATIVE_QUERY_PAGE_REQUEST_CHANGED')
    try:body=json.loads(payload,parse_float=Decimal,parse_constant=_nonfinite,object_pairs_hook=_object)
    except (ValueError,UnicodeError,RecursionError) as exc:raise ValueError('NATIVE_QUERY_JSON_INVALID') from exc
    require(type(body) is dict and 'code' not in body,'NATIVE_QUERY_RESPONSE_ERROR')
    ns=request.base.namespace
    if ns in COLLECTIONS:
        rows=body.get('orders');total=body.get('total')
        require(type(rows) is list and len(rows)<=4096 and type(total) is int and total>=0,'NATIVE_QUERY_COLLECTION_INVALID')
    else:rows=[body];total=1
    venue,client,status=('orderListId','listClientOrderId','listOrderStatus') if ns==N.SPOT_LIST else (
        'algoId','clientAlgoId','algoStatus') if ns in (*COLLECTIONS,N.USD_M_CONDITIONAL) else ('orderId','clientOrderId','status')
    parsed=[]
    for row in rows:
        require(type(row) is dict and type(row.get(client)) is str and bool(row[client])
            and type(row.get('symbol')) is str and bool(row['symbol'])
            and type(row.get(status)) is str and bool(row[status]),'NATIVE_QUERY_ROW_CONTEXT_REQUIRED')
        leg=row.get('positionSide')
        require(leg is None or type(leg) is str and leg in ('BOTH','LONG','SHORT'),'NATIVE_QUERY_LEG_INVALID')
        parsed.append(NativeQueryRowCandidateV2(venue_id=_identifier(row.get(venue)),client_id=row[client],symbol=row['symbol'],
            status=row[status],position_side=leg,payload=row))
    return body,total,tuple(parsed)


def archive_native_query_candidate(payload,*,request,scan_id,bridge,source,received_at):
    require(type(bridge) is NativeEventBridge and type(request) is QueryPageRequest
        and bridge.codec.scope==request.base.scope,'NATIVE_QUERY_ARCHIVE_SCOPE_REQUIRED')
    raw=bridge.ingest(payload,source=source,received_at=received_at,processed_at=received_at).raw
    require(type(scan_id) is str and 0<len(scan_id)<=256,'NATIVE_QUERY_SCAN_REQUIRED')
    body,_,_=_decode(payload,request)
    return NativeQueryCandidatePageV2(request=request,scan_id=scan_id,raw=raw,body=body)


def resolve_native_query_candidates(requests,pages,*,scope,scan_id,source,archive,now):
    require(type(requests) is tuple and requests and all(type(r) is VenueManagementRequest for r in requests)
        and type(pages) is tuple and len(pages)<=4096,'NATIVE_QUERY_BOUNDED_SCAN_REQUIRED')
    expected={digest(r):r for r in requests};first=requests[0]
    endpoints=COLLECTIONS.get(first.namespace,(SINGLES.get(first.namespace),))
    require(scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH) and None not in endpoints
        and len(expected)==len(requests)==len(endpoints) and {r.endpoint for r in requests}==set(endpoints),
        'NATIVE_QUERY_COMPLETE_ENDPOINT_SET_REQUIRED')
    for r in requests:
        require(r.scope==scope and r.method=='GET' and r.command_id is None
            and (r.management_hash,r.target_hash,r.namespace,r.original_client_id,r.target_venue_id,r.venue_symbol)==(
                first.management_hash,first.target_hash,first.namespace,first.original_client_id,first.target_venue_id,first.venue_symbol),
            'NATIVE_QUERY_ORIGINAL_TARGET_CHANGED')
    grouped={key:{} for key in expected};refs=[];bytes_seen=0;rows_seen=0
    for page in pages:
        require(type(page) is NativeQueryCandidatePageV2 and page.contract_version=='2026-10-30'
            and page.record_kind=='NATIVE_QUERY_CANDIDATE_PAGE' and page.scan_id==scan_id,'NATIVE_QUERY_V2_PAGE_REQUIRED')
        key=digest(page.request.base);require(key in grouped,'NATIVE_QUERY_REQUEST_CHANGED')
        raw=page.raw;ref=raw.raw_payload_ref
        require(raw.scope==scope and raw.source==ref.source==source and ref.environment==scope.environment
            and raw.payload_hash==ref.hash and ref.visible_at==raw.received_at
            and utc_timestamp(raw.received_at)<=utc_timestamp(now),'NATIVE_QUERY_ARCHIVE_SOURCE_CHANGED')
        target=Path(archive).resolve()/(raw.payload_hash+'.raw')
        require(Path(ref.id).resolve()==target,'NATIVE_QUERY_ARCHIVE_PATH_CHANGED')
        payload=target.read_bytes();bytes_seen+=len(payload)
        require(bytes_seen<=16_777_216 and sha256(payload).hexdigest()==raw.payload_hash,'NATIVE_QUERY_RAW_HASH_OR_BOUND')
        body,total,rows=_decode(payload,page.request);rows_seen+=len(rows)
        require(rows_seen<=100000 and _typed_same(body,page.body),'NATIVE_QUERY_TYPED_BODY_CHANGED')
        prior=grouped[key].get(page.request.page_number)
        require(prior is None or (prior[0].raw.payload_hash==raw.payload_hash
            and digest(prior[0].request)==digest(page.request)),'NATIVE_QUERY_PAGE_REPLAY_CONFLICT')
        grouped[key][page.request.page_number]=(page,total,rows)
        if ref not in refs:refs.append(ref)
    complete=True;all_rows=[]
    for by_number in grouped.values():
        ordered=[by_number[n] for n in sorted(by_number)]
        if not ordered:complete=False;continue
        size=ordered[0][0].request.page_size;total=ordered[0][1]
        page_count=max(1,(total+size-1)//size) if size is not None else 1
        identities=[(r.venue_id,r.client_id,r.symbol) for _,_,rows in ordered for r in rows]
        valid=(len(ordered)==page_count and all(n==i for i,n in enumerate(sorted(by_number),1))
            and all(t==total and p.request.page_size==size for p,t,_ in ordered)
            and len(identities)==total and len(set(identities))==len(identities))
        if size is not None:
            valid=valid and all(len(rows)==min(size,max(0,total-(p.request.page_number-1)*size)) for p,_,rows in ordered)
        complete=complete and valid
        all_rows.extend(r for _,_,rows in ordered for r in rows)
    matched={};conflict=False;children=[]
    for row in all_rows:
        same_client=row.client_id==first.original_client_id
        same_venue=first.target_venue_id is not None and row.venue_id==first.target_venue_id
        if not (same_client or same_venue):continue
        if scope.product==t.Product.USD_M_PERP:
            if row.position_side is None:complete=False
            elif row.position_side!=scope.venue_leg:conflict=True;continue
        if not same_client or row.symbol!=first.venue_symbol or first.target_venue_id is not None and not same_venue:
            conflict=True;continue
        identity=(row.venue_id,row.client_id,row.symbol);old=matched.get(identity)
        if old is not None and not _typed_same(old.payload,row.payload):conflict=True
        matched[identity]=row
        if row.payload.get('actualOrderId') is not None:children.append(_identifier(row.payload['actualOrderId']))
    conflict=conflict or len(matched)>1
    return OriginalNativeQueryCandidateV2(status='CONFLICT' if conflict else 'INCOMPLETE' if not complete else 'MATCHED' if matched else 'NOT_FOUND',
        rows=() if conflict else tuple(matched.values()),raw_refs=tuple(refs),observed_venue_child_ids=() if conflict else tuple(dict.fromkeys(children)))
