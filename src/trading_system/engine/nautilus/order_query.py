"""Read-only original-ID observations from archived, scoped query responses.

This is not an authenticated venue transport or an ownership/finality proof.
Even a complete empty scan cannot authorize a replacement submission or release
collateral. The host still verifies its source and existing lifecycle proofs.
"""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,utc_timestamp
from trading_system.execution.management_requests import VenueManagementRequest
from trading_system.intents.order_management_compilation import ManagementEndpoint as E
from trading_system.intents.program_compilation import Namespace as N
from .native_events import NativeEventBridge

COLLECTIONS={N.SPOT_EXECUTION:(E.SPOT_EXECUTION_OPEN,E.SPOT_EXECUTION_HISTORY),
    N.USD_M_EXECUTION:(E.USD_M_EXECUTION_OPEN,E.USD_M_EXECUTION_HISTORY)}
SINGLES={N.SPOT_ORDINARY:E.SPOT_ORDER,N.SPOT_LIST:E.SPOT_LIST,
    N.USD_M_ORDINARY:E.USD_M_ORDER,N.USD_M_CONDITIONAL:E.USD_M_CONDITIONAL,N.EQUITY:E.EQUITY_QUERY}
HISTORY=frozenset((E.SPOT_EXECUTION_HISTORY,E.USD_M_EXECUTION_HISTORY))


def digest(value):return canonical_hash(domain_payload(value))
def require(condition,reason):
    if not condition:raise ValueError(reason)


@dataclass(frozen=True,slots=True,kw_only=True)
class QueryPageRequest:
    base:VenueManagementRequest
    page_number:int
    page_size:int|None

    def form_parameters(self):
        fields=self.base.form_parameters()
        if self.base.endpoint in HISTORY:
            fields+= (('page',str(self.page_number)),('pageSize',str(self.page_size)))
        return fields


def query_page_request(request,*,page_number=1,page_size=None):
    require(type(request) is VenueManagementRequest and request.method=='GET'
        and request.command_id is None,'READ_ONLY_QUERY_REQUIRED')
    require(request.scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),
        'QUERY_ENVIRONMENT_DISABLED')
    routes=COLLECTIONS.get(request.namespace,(SINGLES.get(request.namespace),))
    require(request.endpoint in routes and type(request.filter_original_identity) is bool
        and request.filter_original_identity==(request.namespace in COLLECTIONS),
        'QUERY_SERVICE_ROUTE_MISMATCH')
    require(type(page_number) is int and page_number>=1,'QUERY_PAGE_NUMBER_REQUIRED')
    if request.endpoint in HISTORY:
        require(type(page_size) is int and 1<=page_size<=100,'QUERY_PAGE_SIZE_REQUIRED')
    else:
        require(page_number==1 and page_size is None,'QUERY_ENDPOINT_IS_NOT_PAGINATED')
    return QueryPageRequest(base=request,page_number=page_number,page_size=page_size)


@dataclass(frozen=True,slots=True,kw_only=True)
class QueryOrderRow:
    venue_id:str
    client_id:str
    symbol:str
    status:str
    position_side:Literal['BOTH','LONG','SHORT']|None
    payload_json:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ArchivedQueryPage:
    request:QueryPageRequest
    scan_id:str
    raw:t.RawVenueMessage
    total:int
    rows:tuple[QueryOrderRow,...]


def _object(pairs):
    result={}
    for key,value in pairs:
        require(key not in result,'DUPLICATE_QUERY_RESPONSE_KEY')
        result[key]=value
    return result


def _constant(value):raise ValueError('NONFINITE_QUERY_RESPONSE_NUMBER')


def _identifier(value):
    require(type(value) in (str,int) and bool(str(value)) and
        (type(value) is not int or value>=0),'QUERY_ROW_ID_REQUIRED')
    return str(value)


def _decode(payload,request,scan_id,raw):
    canonical=query_page_request(request.base,page_number=request.page_number,page_size=request.page_size)
    require(digest(canonical)==digest(request),'QUERY_PAGE_REQUEST_CHANGED')
    require(type(scan_id) is str and bool(scan_id),'QUERY_SCAN_REQUIRED')
    try:
        body=json.loads(payload,parse_float=Decimal,parse_constant=_constant,object_pairs_hook=_object)
    except (UnicodeError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError('QUERY_RESPONSE_INVALID') from exc
    require(type(body) is dict and 'code' not in body,'QUERY_RESPONSE_ERROR')
    base=request.base
    if base.namespace in COLLECTIONS:
        rows=body.get('orders');total=body.get('total')
        require(type(rows) is list and type(total) is int and total>=0,'QUERY_COLLECTION_INVALID')
    else:rows=[body];total=1
    venue,client,status=('orderListId','listClientOrderId','listOrderStatus') if base.namespace==N.SPOT_LIST else (
        'algoId','clientAlgoId','algoStatus') if base.namespace in (*COLLECTIONS,N.USD_M_CONDITIONAL) else (
        'orderId','clientOrderId','status')
    parsed=[]
    for row in rows:
        require(type(row) is dict and type(row.get('symbol')) is str and bool(row['symbol'])
            and type(row.get(status)) is str and bool(row[status]),'QUERY_ROW_CONTEXT_REQUIRED')
        require(type(row.get(client)) is str and bool(row[client]),'QUERY_CLIENT_ID_STRING_REQUIRED')
        leg=row.get('positionSide')
        require(leg is None or type(leg) is str and leg in ('BOTH','LONG','SHORT'),
            'QUERY_POSITION_LEG_INVALID')
        parsed.append(QueryOrderRow(venue_id=_identifier(row.get(venue)),client_id=row[client],
            symbol=row['symbol'],status=row[status],position_side=leg,payload_json=canonical_json(row)))
    return ArchivedQueryPage(request=request,scan_id=scan_id,raw=raw,total=total,rows=tuple(parsed))


def archive_query_page(payload,*,request,scan_id,bridge,source,received_at):
    require(type(bridge) is NativeEventBridge and type(request) is QueryPageRequest
        and bridge.codec.scope==request.base.scope,'QUERY_ARCHIVE_SCOPE_REQUIRED')
    # Existing archive fsync/hash semantics are reused. Decode failures retain
    # their original bytes as well; no response is converted into NOT_SENT.
    raw=bridge.ingest(payload,source=source,received_at=received_at,processed_at=received_at).raw
    return _decode(payload,request,scan_id,raw)


@dataclass(frozen=True,slots=True,kw_only=True)
class OriginalQueryObservation:
    status:Literal['MATCHED','NOT_FOUND','INCOMPLETE','CONFLICT']
    rows:tuple[QueryOrderRow,...]
    raw_refs:tuple[t.EvidenceRef,...]

    @property
    def reconciliation_required(self)->Literal[True]:return True
    @property
    def can_resubmit(self)->Literal[False]:return False
    @property
    def ownership_established(self)->Literal[False]:return False
    @property
    def funds_released(self)->Literal[False]:return False


def resolve_original_query(requests,pages,*,scope,scan_id,source,archive,now):
    require(type(requests) is tuple and bool(requests) and type(pages) is tuple
        and all(type(r) is VenueManagementRequest for r in requests),'QUERY_SCAN_REQUIRED')
    expected={digest(r):r for r in requests}
    require(len(expected)==len(requests),'QUERY_REQUEST_DUPLICATED')
    first=requests[0]
    endpoints=COLLECTIONS.get(first.namespace,(SINGLES.get(first.namespace),))
    require(None not in endpoints and len(requests)==len(endpoints)
        and {r.endpoint for r in requests}==set(endpoints),'QUERY_ENDPOINT_SET_REQUIRED')
    for request in requests:
        require(type(request) is VenueManagementRequest and request.scope==scope
            and request.method=='GET' and request.command_id is None
            and (request.management_hash,request.target_hash,request.namespace,request.original_client_id,
                request.target_venue_id,request.venue_symbol)==(first.management_hash,first.target_hash,first.namespace,
                first.original_client_id,first.target_venue_id,first.venue_symbol),'QUERY_TARGET_CHANGED')
    require(scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),'QUERY_ENVIRONMENT_DISABLED')
    archive=Path(archive).resolve()
    scans={key:{} for key in expected};refs=[]
    for page in pages:
        require(type(page) is ArchivedQueryPage and page.scan_id==scan_id,'QUERY_SCAN_CHANGED')
        key=digest(page.request.base)
        require(key in expected,'QUERY_REQUEST_CHANGED')
        raw=page.raw;ref=raw.raw_payload_ref
        require(raw.scope==scope and raw.source==source==ref.source and ref.environment==scope.environment
            and raw.payload_hash==ref.hash and ref.visible_at==raw.received_at
            and utc_timestamp(raw.received_at)<=utc_timestamp(now),'QUERY_SOURCE_CHANGED')
        path=archive/(raw.payload_hash+'.raw')
        require(Path(ref.id).resolve()==path,'QUERY_ARCHIVE_PATH_CHANGED')
        payload=path.read_bytes()
        require(sha256(payload).hexdigest()==raw.payload_hash,'QUERY_ARCHIVE_HASH_CHANGED')
        replay=_decode(payload,page.request,scan_id,raw)
        require(digest(replay)==digest(page),'QUERY_PAGE_CHANGED')
        number=page.request.page_number
        previous=scans[key].get(number)
        require(previous is None or digest(previous)==digest(page),'QUERY_PAGE_REPLAY_CONFLICT')
        scans[key][number]=page
        if ref not in refs:refs.append(ref)
    complete=True;rows=[]
    for key,by_number in scans.items():
        ordered=tuple(by_number[n] for n in sorted(by_number))
        if not ordered:
            complete=False;continue
        count=sum(len(p.rows) for p in ordered)
        size=ordered[0].request.page_size;total=ordered[0].total
        page_count=max(1,(total+size-1)//size) if size is not None else 1
        identities=[(r.venue_id,r.client_id,r.symbol) for p in ordered for r in p.rows]
        # A remote total must never cause allocation/iteration proportional to
        # the claimed result size; inspect only the pages actually received.
        valid=(len(by_number)==page_count
            and all(number==index for index,number in enumerate(sorted(by_number),1))
            and all(p.total==total and p.request.page_size==size for p in ordered)
            and count==total and len(identities)==len(set(identities)))
        if size is not None:
            valid=valid and all(len(p.rows)==min(size,max(0,total-(p.request.page_number-1)*size)) for p in ordered)
        complete=complete and valid
        rows.extend(r for p in ordered for r in p.rows)
    matches={};conflict=False
    for row in rows:
        same_client=row.client_id==first.original_client_id
        same_venue=first.target_venue_id is not None and row.venue_id==first.target_venue_id
        if not(same_client or same_venue):continue
        if scope.product==t.Product.USD_M_PERP:
            if row.position_side is None:complete=False
            elif row.position_side!=scope.venue_leg:conflict=True;continue
        if row.symbol!=first.venue_symbol or not same_client or (
            first.target_venue_id is not None and not same_venue):
            conflict=True;continue
        identity=(row.venue_id,row.client_id,row.symbol)
        old=matches.get(identity)
        if old is not None and digest(old)!=digest(row):conflict=True
        matches[identity]=row
    conflict=conflict or len(matches)>1
    status='CONFLICT' if conflict else 'INCOMPLETE' if not complete else 'MATCHED' if matches else 'NOT_FOUND'
    return OriginalQueryObservation(status=status,rows=() if conflict else tuple(matches.values()),raw_refs=tuple(refs))
