"""Unverified management outcomes; cancel and replacement stay independent.

Archive transport bytes before SDK error decoding. Structural matching is not
source authentication, terminal lifecycle, history coverage or release evidence.
"""
from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.execution.management_requests import VenueManagementRequest
from trading_system.intents.program_compilation import Namespace as N
from trading_system.intents.order_management_compilation import ManagementEndpoint as E
from .native_events import NativeEventBridge
from .native_response import _object,_nonfinite,_identifier


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class ManagementOperationCandidate:
    operation:Literal['CANCEL','REPLACEMENT','AMEND']
    outcome:str
    venue_id:str|None
    payload:object


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeManagementResponseCandidate:
    command_id:str|None
    request_hash:str
    raw:t.RawVenueMessage
    body:object
    status:Literal['ACK_ONLY','PARTIAL','INCOMPLETE','CONFLICT','ERROR']
    operations:tuple[ManagementOperationCandidate,...]
    discrepancies:tuple[str,...]
    retained_duties:tuple[str,...]

    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def can_resubmit(self)->Literal[False]:return False
    @property
    def lifecycle_status(self):return None
    @property
    def fills(self):return ()
    @property
    def orders(self):return ()


def archive_management_response(payload,*,request,bridge,source,received_at,http_status=200):
    require(type(request) is VenueManagementRequest and type(bridge) is NativeEventBridge
        and bridge.codec.scope==request.scope,'NATIVE_MANAGEMENT_ARCHIVE_SCOPE_REQUIRED')
    raw=bridge.ingest(payload,source=source,received_at=received_at,processed_at=received_at).raw
    body=None;status='ACK_ONLY';operations=[];codes=[]
    duties=['QUERY_ORIGINAL_IDS','SOURCE_AUTHENTICATION_REQUIRED','LIFECYCLE_AND_TRADE_HISTORY_REQUIRED',
        'RESERVATION_RECONCILIATION_REQUIRED']
    try:
        require(type(payload) is bytes and len(payload)<=1_048_576,'NATIVE_MANAGEMENT_RESPONSE_BOUND')
        utc_timestamp(received_at)
        body=json.loads(payload,parse_float=Decimal,parse_constant=_nonfinite,object_pairs_hook=_object)
        require(type(body) is dict and type(http_status) is int,'NATIVE_MANAGEMENT_RESPONSE_INVALID')
        def original(row,id_key,client_key=None):
            if type(row) is not dict:return False
            if _identifier(row.get(id_key))!=request.target_venue_id:return False
            if 'symbol' in row and row['symbol']!=request.venue_symbol:return False
            return client_key is None or client_key not in row or row[client_key]==request.original_client_id
        if request.endpoint==E.SPOT_CANCEL_REPLACE:
            duties.append('CANCEL_AND_REPLACEMENT_RECONCILIATION_REQUIRED')
            data=body
            if 'cancelResult' not in data and type(body.get('data')) is dict:data=body['data']
            elif 'cancelResult' in data and type(body.get('data')) is dict and 'cancelResult' in body['data']:
                raise ValueError('NATIVE_AMBIGUOUS_CANCEL_REPLACE_ENVELOPE')
            names=(('CANCEL','cancelResult','cancelResponse'),('REPLACEMENT','newOrderResult','newOrderResponse'))
            fields=dict(request.form_parameters());replacement_id=None;cancel_id=None
            for operation,outcome_key,payload_key in names:
                outcome=data.get(outcome_key,'UNOBSERVED');part=data.get(payload_key)
                venue=None
                if outcome not in ('SUCCESS','FAILURE','NOT_ATTEMPTED'):
                    status='INCOMPLETE';codes.append('NATIVE_MANAGEMENT_RESULT_UNKNOWN')
                if outcome=='SUCCESS':
                    require(type(part) is dict,'NATIVE_SUCCESS_RESPONSE_REQUIRED')
                    venue=_identifier(part.get('orderId'))
                    if operation=='CANCEL':
                        cancel_id=venue
                        if not original(part,'orderId','origClientOrderId'):
                            status='CONFLICT';codes.append('NATIVE_CANCEL_TARGET_CHANGED')
                    else:
                        replacement_id=venue
                        if (part.get('clientOrderId')!=fields.get('newClientOrderId')
                                or 'symbol' in part and part['symbol']!=request.venue_symbol):
                            status='CONFLICT';codes.append('NATIVE_REPLACEMENT_TARGET_CHANGED')
                if outcome=='NOT_ATTEMPTED':duties.append('NOT_ATTEMPTED_BUDGET_RECONCILIATION_REQUIRED')
                operations.append(ManagementOperationCandidate(operation=operation,outcome=outcome,venue_id=venue,payload=part))
            if cancel_id is not None and cancel_id==replacement_id:
                status='CONFLICT';codes.append('NATIVE_CANCEL_REPLACEMENT_IDS_COLLIDE')
            if status=='ACK_ONLY' and any(o.outcome!='SUCCESS' for o in operations):status='PARTIAL'
            # HTTP400/409/429 can carry independent real operation outcomes.
            # Preserve both, including failed or NOT_ATTEMPTED new orders.
        elif request.method=='GET':
            status='INCOMPLETE';duties.append('COMPLETE_ORIGINAL_QUERY_PAGE_SCAN_REQUIRED')
        else:
            require(200<=http_status<300,'NATIVE_MANAGEMENT_HTTP_ERROR')
            ns=request.namespace;part=body.get('amendedOrder',body)
            key='orderListId' if ns==N.SPOT_LIST else 'algoId' if ns in (
                N.USD_M_CONDITIONAL,N.SPOT_EXECUTION,N.USD_M_EXECUTION) else 'orderId'
            client='listClientOrderId' if ns==N.SPOT_LIST else 'clientAlgoId' if ns in (
                N.USD_M_CONDITIONAL,N.SPOT_EXECUTION,N.USD_M_EXECUTION) else 'origClientOrderId'
            if not original(part,key,client):status='CONFLICT';codes.append('NATIVE_MANAGEMENT_TARGET_CHANGED')
            if ns==N.EQUITY:require(body.get('status')=='S','NATIVE_EQUITY_MANAGEMENT_ACK_FAILED')
            if ns in (N.SPOT_EXECUTION,N.USD_M_EXECUTION):
                require(body.get('success') is True and type(body.get('code')) is int
                    and body['code']==0,'NATIVE_ALGORITHM_MANAGEMENT_ACK_FAILED')
            if ns==N.USD_M_CONDITIONAL:
                code=body.get('code')
                require(code is None or type(code) in (str,int) and code in (0,200,'0','200'),
                    'NATIVE_CONDITIONAL_MANAGEMENT_ERROR')
            operations.append(ManagementOperationCandidate(operation='AMEND' if request.method=='PUT' else 'CANCEL',
                outcome='ACKNOWLEDGED',venue_id=_identifier(part.get(key)),payload=part))
            if ns==N.SPOT_LIST:duties.append('LINKED_SIBLING_OUTCOMES_QUERY_REQUIRED')
    except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as exc:
        status='ERROR';codes.append(str(exc) if type(exc) is ValueError else 'NATIVE_MANAGEMENT_RESPONSE_SHAPE_INVALID')
    return NativeManagementResponseCandidate(command_id=request.command_id,request_hash=canonical_hash(domain_payload(request)),
        raw=raw,body=body,status=status,operations=tuple(operations),discrepancies=tuple(codes),retained_duties=tuple(dict.fromkeys(duties)))
