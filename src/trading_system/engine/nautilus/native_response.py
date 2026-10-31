"""Lossless raw creation-response candidates, never authenticated venue facts.

Pinned SDK schemas cover ordinary/list/conditional identities. Reviewed official
schemas cover equity and execution ACKs. Source authentication/account history
remain outer host duties; this parser emits no Fill/OrderProjection or permit.
"""
from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.intents.program_compilation import Namespace as N,Operation
from .native_events import NativeEventBridge


def require(condition,code):
    if not condition:raise ValueError(code)


def _object(pairs):
    result={}
    for key,value in pairs:
        require(key not in result,'NATIVE_RESPONSE_DUPLICATE_KEY');result[key]=value
    return result


def _nonfinite(value):raise ValueError('NATIVE_RESPONSE_NONFINITE_NUMBER')


def _identifier(value):
    require(type(value) in (str,int) and bool(str(value)) and (type(value) is not int or value>=0),
        'NATIVE_RESPONSE_ID_INVALID')
    return str(value)


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeIdentityCandidate:
    client_id:str
    venue_id:str|None
    role:Literal['ORDER_MEMBER','PROGRAM_PARENT']


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeResponseCandidate:
    command_id:str
    request_hash:str
    raw:t.RawVenueMessage
    body:object
    status:Literal['ACK_ONLY','INCOMPLETE','CONFLICT','ERROR']
    identities:tuple[NativeIdentityCandidate,...]
    discrepancies:tuple[str,...]
    observed_venue_child_ids:tuple[str,...]=()
    retained_duties:tuple[str,...]=('QUERY_ORIGINAL_IDS','SOURCE_AUTHENTICATION_REQUIRED',
        'LIFECYCLE_AND_TRADE_HISTORY_REQUIRED','RESERVATION_RECONCILIATION_REQUIRED')

    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def child_submission_authorized(self)->Literal[False]:return False
    @property
    def can_resubmit(self)->Literal[False]:return False
    @property
    def lifecycle_status(self):return None
    @property
    def fills(self):return ()
    @property
    def orders(self):return ()


def archive_creation_response(payload,*,request,bridge,received_at,source,http_status=200):
    require(type(request) is VenueCreationRequest and type(bridge) is NativeEventBridge
        and bridge.codec.scope==request.scope,'NATIVE_RESPONSE_SCOPED_ARCHIVE_REQUIRED')
    # Existing durable raw writer runs before any shape, HTTP or JSON decision.
    raw=bridge.ingest(payload,source=source,received_at=received_at,processed_at=received_at).raw
    body=None;identities=[];children=[];codes=[];status='ACK_ONLY';duties=[]
    def result():
        return NativeResponseCandidate(command_id=request.command_id,request_hash=canonical_hash(domain_payload(request)),
            raw=raw,body=body,status=status,identities=tuple(identities),discrepancies=tuple(codes),
            observed_venue_child_ids=tuple(children),retained_duties=(
                'QUERY_ORIGINAL_IDS','SOURCE_AUTHENTICATION_REQUIRED','LIFECYCLE_AND_TRADE_HISTORY_REQUIRED',
                'RESERVATION_RECONCILIATION_REQUIRED',*duties))
    try:
        require(type(payload) is bytes and len(payload)<=1_048_576,'NATIVE_RESPONSE_BOUND_EXCEEDED')
        utc_timestamp(received_at)
        body=json.loads(payload,parse_float=Decimal,parse_constant=_nonfinite,object_pairs_hook=_object)
        require(type(http_status) is int and 200<=http_status<300,'NATIVE_HTTP_RESPONSE_ERROR')
        ids=request.assigned_ids;ns=request.namespace
        expected={m.client_id for m in ids.members}
        symbols={f.text() for f in request.fields if f.name=='symbol'}
        symbols.update(f.text() for b in request.batch_orders for f in b.fields if f.name=='symbol')
        def symbol(row):
            return 'symbol' not in row or type(row['symbol']) is str and row['symbol'] in symbols
        def member(row):
            require(type(row) is dict,'NATIVE_RESPONSE_MEMBER_INVALID')
            client=row.get('clientOrderId')
            if client not in expected or not symbol(row):return False
            identities.append(NativeIdentityCandidate(client_id=client,venue_id=_identifier(row.get('orderId')),role='ORDER_MEMBER'))
            return True
        if ns in (N.SPOT_EXECUTION,N.USD_M_EXECUTION):
            require(type(body) is dict and body.get('success') is True and type(body.get('code')) is int
                and body['code']==0,'NATIVE_ALGORITHM_ACK_ERROR')
            if body.get('clientAlgoId')!=ids.parent_client_id:status='CONFLICT';codes.append('NATIVE_PARENT_CLIENT_CHANGED')
            else:
                venue=None if body.get('algoId') is None else _identifier(body['algoId'])
                identities.append(NativeIdentityCandidate(client_id=ids.parent_client_id,venue_id=venue,role='PROGRAM_PARENT'))
                if venue is None:status='INCOMPLETE';duties.append('VENUE_ID_QUERY_REQUIRED')
        elif ns==N.USD_M_CONDITIONAL:
            require(type(body) is dict and 'code' not in body,'NATIVE_CONDITIONAL_RESPONSE_ERROR')
            if body.get('clientAlgoId')!=ids.parent_client_id or not symbol(body):
                status='CONFLICT';codes.append('NATIVE_PARENT_CLIENT_CHANGED')
            else:
                identities.append(NativeIdentityCandidate(client_id=ids.parent_client_id,venue_id=_identifier(body.get('algoId')),role='PROGRAM_PARENT'))
                if body.get('actualOrderId') is not None:children.append(_identifier(body['actualOrderId']))
        elif ns==N.SPOT_LIST:
            require(type(body) is dict and 'code' not in body,'NATIVE_LIST_RESPONSE_ERROR')
            if body.get('listClientOrderId')!=ids.parent_client_id or not symbol(body):
                status='CONFLICT';codes.append('NATIVE_PARENT_CLIENT_CHANGED')
            else:
                identities.append(NativeIdentityCandidate(client_id=ids.parent_client_id,venue_id=_identifier(body.get('orderListId')),role='PROGRAM_PARENT'))
                observed={}
                for key in ('orders','orderReports'):
                    rows=body.get(key) or []
                    require(type(rows) is list and len(rows)<=512,'NATIVE_LIST_MEMBER_BOUND')
                    seen=set()
                    for row in rows:
                        if not member(row):status='CONFLICT';codes.append('NATIVE_FOREIGN_MEMBER');continue
                        current=identities.pop();client=current.client_id
                        if client in seen or client in observed and observed[client].venue_id!=current.venue_id:
                            status='CONFLICT';codes.append('NATIVE_MEMBER_ID_CONFLICT')
                        seen.add(client);observed[client]=current
                identities.extend(observed.values())
                if status!='CONFLICT' and set(observed)!=expected:status='INCOMPLETE';duties.append('NATIVE_PENDING_MEMBERS_REMAIN')
        elif ns==N.EQUITY:
            require(type(body) is dict and body.get('status')=='S','NATIVE_EQUITY_ACK_ERROR')
            if not member(body):status='CONFLICT';codes.append('NATIVE_ORDER_CLIENT_CHANGED')
            # S/F is acknowledgement, never a lifecycle enum or fill quantity.
        else:
            rows=body if request.operation==Operation.BATCH else [body]
            require(type(rows) is list and len(rows)<=512,'NATIVE_BATCH_RESPONSE_BOUND')
            seen=set()
            for row in rows:
                if type(row) is dict and 'code' in row:
                    status='INCOMPLETE';codes.append('NATIVE_BATCH_ITEM_ERROR');continue
                if not member(row):status='CONFLICT';codes.append('NATIVE_ORDER_CLIENT_CHANGED');continue
                client=identities[-1].client_id
                if client in seen:status='CONFLICT';codes.append('NATIVE_MEMBER_ID_CONFLICT')
                seen.add(client)
            if status!='CONFLICT' and seen!=expected:status='INCOMPLETE';duties.append('NATIVE_MEMBERS_QUERY_REQUIRED')
        if status=='CONFLICT':identities=[]
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as exc:
        status='ERROR';identities=[];children=[];codes.append(str(exc) if type(exc) is ValueError else 'NATIVE_RESPONSE_SHAPE_INVALID')
    return result()
