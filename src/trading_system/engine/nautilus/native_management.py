"""Lossless unsigned management/query SDK shapes; no creation-permit alias.

Management writes await their own versioned durable boundary. This module has
no sender, signer, client factory or callback and cannot activate any endpoint.
"""
from dataclasses import dataclass
import re
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.execution.management_requests import VenueManagementRequest
from trading_system.intents.program_compilation import Namespace as N,FieldName
from trading_system.intents.order_management_compilation import ManagementEndpoint as E,ManagementParameter,ManagementField
from .native_creation import timestamp_milliseconds
from .order_query import COLLECTIONS,query_page_request

_PRODUCT={N.SPOT_ORDINARY:t.Product.SPOT,N.SPOT_LIST:t.Product.SPOT,N.SPOT_EXECUTION:t.Product.SPOT,
    N.USD_M_ORDINARY:t.Product.USD_M_PERP,N.USD_M_CONDITIONAL:t.Product.USD_M_PERP,
    N.USD_M_EXECUTION:t.Product.USD_M_PERP,N.EQUITY:t.Product.US_EQUITY}
MANAGEMENT_ROUTES=frozenset((
    (N.SPOT_ORDINARY,'GET',E.SPOT_ORDER),(N.SPOT_ORDINARY,'DELETE',E.SPOT_ORDER),
    (N.SPOT_ORDINARY,'PUT',E.SPOT_KEEP_PRIORITY),(N.SPOT_ORDINARY,'POST',E.SPOT_CANCEL_REPLACE),
    (N.SPOT_LIST,'GET',E.SPOT_LIST),(N.SPOT_LIST,'DELETE',E.SPOT_LIST),
    (N.USD_M_ORDINARY,'GET',E.USD_M_ORDER),(N.USD_M_ORDINARY,'DELETE',E.USD_M_ORDER),
    (N.USD_M_ORDINARY,'PUT',E.USD_M_ORDER),
    (N.USD_M_CONDITIONAL,'GET',E.USD_M_CONDITIONAL),(N.USD_M_CONDITIONAL,'DELETE',E.USD_M_CONDITIONAL),
    (N.SPOT_EXECUTION,'GET',E.SPOT_EXECUTION_OPEN),(N.SPOT_EXECUTION,'GET',E.SPOT_EXECUTION_HISTORY),
    (N.SPOT_EXECUTION,'DELETE',E.SPOT_EXECUTION),
    (N.USD_M_EXECUTION,'GET',E.USD_M_EXECUTION_OPEN),(N.USD_M_EXECUTION,'GET',E.USD_M_EXECUTION_HISTORY),
    (N.USD_M_EXECUTION,'DELETE',E.USD_M_EXECUTION),
    (N.EQUITY,'GET',E.EQUITY_QUERY),(N.EQUITY,'POST',E.EQUITY_CANCEL),
))
_SELECTORS=frozenset(('orderId','origClientOrderId','clientOrderId','orderListId','algoId','clientAlgoId','cancelOrderId'))


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeManagementCall:
    command_id:str|None
    request_hash:str
    namespace:N
    endpoint:E
    method:Literal['GET','POST','PUT','DELETE']
    original_client_id:str
    target_venue_id:str|None
    original_parameters:tuple[tuple[str,str],...]
    parameters:tuple[tuple[str,str],...]

    @property
    def requires_independent_command(self):return self.method!='GET'
    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def native_order_qualified(self)->Literal[False]:return False


def management_call(request,*,clock,recv_window_ms=1000,page_number=1,page_size=None):
    require(type(request) is VenueManagementRequest,'NATIVE_TYPED_MANAGEMENT_REQUIRED')
    require(type(request.scope) is t.ScopeKey and request.scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),
        'NATIVE_EXTERNAL_ENVIRONMENT_DISABLED')
    require(type(request.namespace) is N and type(request.endpoint) is E and type(request.method) is str
        and _PRODUCT[request.namespace]==request.scope.product
        and (request.namespace,request.method,request.endpoint) in MANAGEMENT_ROUTES,'NATIVE_MANAGEMENT_ROUTE_CHANGED')
    require(type(request.original_client_id) is str and 0<len(request.original_client_id)<=256
        and (request.target_venue_id is None or type(request.target_venue_id) is str and bool(request.target_venue_id)),
        'NATIVE_MANAGEMENT_ORIGINAL_IDS_REQUIRED')
    require(all(type(h) is str and re.fullmatch('[0-9a-f]{64}',h) for h in
        (request.management_hash,request.instruction_hash,request.target_hash,request.source_request_hash)),
        'NATIVE_MANAGEMENT_BINDINGS_REQUIRED')
    require(type(request.parameters) is tuple and len(request.parameters)<=512
        and all(type(p) is ManagementParameter and type(p.name) in (FieldName,ManagementField)
            and type(p.value) in (str,int,bool) for p in request.parameters),'NATIVE_MANAGEMENT_FIELDS_REQUIRED')
    parameters=request.form_parameters();fields=dict(parameters)
    require(len(fields)==len(parameters),'NATIVE_MANAGEMENT_DUPLICATE_FIELD')
    require('symbol' not in fields or fields['symbol']==request.venue_symbol,'NATIVE_MANAGEMENT_SYMBOL_CHANGED')
    if request.method=='GET':
        require(request.command_id is None,'NATIVE_QUERY_IS_NOT_A_WRITE')
        parameters=query_page_request(request,page_number=page_number,page_size=page_size).form_parameters()
    else:
        require(type(request.command_id) is str and bool(request.command_id) and request.target_venue_id is not None
            and request.filter_original_identity is False,'NATIVE_INDEPENDENT_MANAGEMENT_COMMAND_REQUIRED')
        require(page_number==1 and page_size is None,'NATIVE_WRITE_IS_NOT_PAGINATED')
    actual={k:v for k,v in fields.items() if k in _SELECTORS}
    if request.method=='GET' and request.namespace in COLLECTIONS:
        require(not actual and request.filter_original_identity is True,'NATIVE_QUERY_ORIGINAL_FILTER_REQUIRED')
    else:
        venue_key='orderListId' if request.namespace==N.SPOT_LIST else 'algoId' if request.namespace in (
            N.USD_M_CONDITIONAL,N.SPOT_EXECUTION,N.USD_M_EXECUTION) else 'orderId'
        client_key='clientAlgoId' if request.namespace in (N.USD_M_CONDITIONAL,N.SPOT_EXECUTION,N.USD_M_EXECUTION) else (
            'clientOrderId' if request.namespace==N.EQUITY else 'origClientOrderId')
        if request.endpoint==E.SPOT_CANCEL_REPLACE:venue_key='cancelOrderId'
        expected={venue_key:request.target_venue_id} if request.target_venue_id is not None else {client_key:request.original_client_id}
        require(actual==expected,'NATIVE_MANAGEMENT_TARGET_CHANGED')
    require(type(recv_window_ms) is int and 1<=recv_window_ms<=60000,'NATIVE_BOUNDED_RECEIVE_WINDOW_REQUIRED')
    return NativeManagementCall(command_id=request.command_id,request_hash=canonical_hash(domain_payload(request)),
        namespace=request.namespace,endpoint=request.endpoint,method=request.method,original_client_id=request.original_client_id,
        target_venue_id=request.target_venue_id,original_parameters=parameters,
        parameters=(*parameters,('timestamp',str(timestamp_milliseconds(clock))),('recvWindow',str(recv_window_ms))))
