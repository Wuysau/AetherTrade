"""Lossless controlled call shape for the pinned native extension.

No signer/client/transport or permission is returned. The owner host must reload
the real consumed command and current guard after every wait before any call.
"""
from dataclasses import dataclass
from datetime import datetime,timezone
from fractions import Fraction
import re
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.order_lifecycle_v3 import exact_time
from trading_system.execution.creation_requests import VenueCreationRequest,VenueField,BatchOrderFields,AssignedClientIds,NodeClientId
from trading_system.intents.program_compilation import Namespace as N,Operation as O,Endpoint as E


# This is a closed extension of exact frozen endpoints, never a URL chosen by
# a proposal, server request, response or signer callback.
CREATION_ROUTES={
    (t.Product.SPOT,N.SPOT_ORDINARY,O.ORDER):E.SPOT_ORDER,
    (t.Product.SPOT,N.SPOT_ORDINARY,O.SOR):E.SPOT_SOR,
    (t.Product.SPOT,N.SPOT_LIST,O.OCO):E.SPOT_OCO,
    (t.Product.SPOT,N.SPOT_LIST,O.OTO):E.SPOT_OTO,
    (t.Product.SPOT,N.SPOT_LIST,O.OTOCO):E.SPOT_OTOCO,
    (t.Product.SPOT,N.SPOT_LIST,O.OPO):E.SPOT_OPO,
    (t.Product.SPOT,N.SPOT_LIST,O.OPOCO):E.SPOT_OPOCO,
    (t.Product.SPOT,N.SPOT_EXECUTION,O.TWAP):E.SPOT_TWAP,
    (t.Product.SPOT,N.SPOT_EXECUTION,O.VP):E.SPOT_VP,
    (t.Product.USD_M_PERP,N.USD_M_ORDINARY,O.ORDER):E.USD_M_ORDER,
    (t.Product.USD_M_PERP,N.USD_M_ORDINARY,O.BATCH):E.USD_M_BATCH,
    (t.Product.USD_M_PERP,N.USD_M_CONDITIONAL,O.CONDITIONAL):E.USD_M_CONDITIONAL,
    (t.Product.USD_M_PERP,N.USD_M_EXECUTION,O.TWAP):E.USD_M_TWAP,
    (t.Product.USD_M_PERP,N.USD_M_EXECUTION,O.VP):E.USD_M_VP,
    (t.Product.US_EQUITY,N.EQUITY,O.ORDER):E.EQUITY_ORDER,
}
_IDS=frozenset(('newClientOrderId','clientOrderId','clientAlgoId','listClientOrderId',
    'aboveClientOrderId','belowClientOrderId','workingClientOrderId','pendingClientOrderId',
    'pendingAboveClientOrderId','pendingBelowClientOrderId'))
_LIST_IDS={O.OCO:('aboveClientOrderId','belowClientOrderId'),
    O.OTO:('workingClientOrderId','pendingClientOrderId'),
    O.OTOCO:('workingClientOrderId','pendingAboveClientOrderId','pendingBelowClientOrderId'),
    O.OPO:('workingClientOrderId','pendingClientOrderId'),
    O.OPOCO:('workingClientOrderId','pendingAboveClientOrderId','pendingBelowClientOrderId')}


def require(condition,code):
    if not condition:raise ValueError(code)


def _field_map(fields):
    require(type(fields) is tuple and len(fields)<=512 and all(type(f) is VenueField for f in fields),'NATIVE_TYPED_FIELDS_REQUIRED')
    require(len({f.name for f in fields})==len(fields),'NATIVE_DUPLICATE_FIELD')
    for field in fields:field.__post_init__()
    require(not any(f.name in ('signature','timestamp','recvWindow') for f in fields),'NATIVE_AUTH_FIELDS_RESERVED')
    return {f.name:f.value for f in fields}


def _check_ids(request):
    ids=request.assigned_ids
    require(type(ids) is AssignedClientIds and ids.command_id==request.command_id
        and ids.compilation_hash==request.compilation_hash and ids.request_index==request.request_index,
        'NATIVE_ORIGINAL_ID_BINDING_CHANGED')
    require(type(ids.members) is tuple and all(type(m) is NodeClientId for m in ids.members),'NATIVE_MEMBER_IDS_REQUIRED')
    members={m.node_id:m.client_id for m in ids.members}
    values=tuple(members.values())+((ids.parent_client_id,) if ids.parent_client_id is not None else ())
    require(len(members)==len(ids.members) and values and len(set(values))==len(values)
        and all(type(v) is str and re.fullmatch('[A-Za-z0-9_-]{32}',v) for v in values),'NATIVE_ORIGINAL_IDS_INVALID')
    fields=_field_map(request.fields)
    if request.operation==O.BATCH:
        require(not fields and ids.parent_client_id is None and type(request.batch_orders) is tuple
            and 0<len(request.batch_orders)<=512 and all(type(b) is BatchOrderFields for b in request.batch_orders),
            'NATIVE_BATCH_SHAPE_CHANGED')
        require(len({b.node_id for b in request.batch_orders})==len(request.batch_orders)
            and {b.node_id for b in request.batch_orders}==set(members),'NATIVE_BATCH_IDS_CHANGED')
        for order in request.batch_orders:
            child=_field_map(order.fields)
            require({k:v for k,v in child.items() if k in _IDS}=={'newClientOrderId':members[order.node_id]},'NATIVE_BATCH_IDS_CHANGED')
            _position_side(request,child)
        return
    require(request.batch_orders==(),'NATIVE_UNEXPECTED_BATCH')
    actual={k:v for k,v in fields.items() if k in _IDS}
    if request.namespace in (N.USD_M_CONDITIONAL,N.SPOT_EXECUTION,N.USD_M_EXECUTION):
        require(not members and ids.parent_client_id is not None
            and actual=={'clientAlgoId':ids.parent_client_id},'NATIVE_PARENT_ID_CHANGED')
    elif request.namespace==N.SPOT_LIST:
        names=_LIST_IDS[request.operation]
        require(ids.parent_client_id is not None and actual.get('listClientOrderId')==ids.parent_client_id
            and len(members)==len(names) and set(actual)=={'listClientOrderId',*names}
            and {actual[n] for n in names}==set(members.values()),'NATIVE_LIST_IDS_CHANGED')
    else:
        name='clientOrderId' if request.namespace==N.EQUITY else 'newClientOrderId'
        require(ids.parent_client_id is None and len(members)==1 and actual=={name:next(iter(members.values()))},'NATIVE_ORDER_ID_CHANGED')
    _position_side(request,fields)


def _position_side(request,fields):
    leg=request.scope.venue_leg
    require(leg in ('BOTH','LONG','SHORT'),'NATIVE_POSITION_LEG_CHANGED')
    if request.scope.product==t.Product.USD_M_PERP:
        require(fields.get('positionSide', 'BOTH')==leg,'NATIVE_POSITION_LEG_CHANGED')
    else:require(leg=='BOTH' and 'positionSide' not in fields,'NATIVE_POSITION_LEG_CHANGED')


def timestamp_milliseconds(clock):
    require(type(clock) is t.ClockReading and clock.clock_health=='HEALTHY'
        and type(clock.monotonic_ns) is int and clock.monotonic_ns>=0
        and type(clock.utc_now) is str and len(clock.utc_now)<=128,'NATIVE_HEALTHY_CLOCK_REQUIRED')
    seconds,fraction=exact_time(clock.utc_now)
    epoch=utc_timestamp(seconds+'Z')-datetime(1970,1,1,tzinfo=timezone.utc)
    value=(epoch.days*86400+epoch.seconds)*1000+int(Fraction(fraction)*1000)
    require(value>=0,'NATIVE_TIMESTAMP_INVALID')
    return value


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeCreationCall:
    command_id:str
    request_hash:str
    namespace:N
    endpoint:E
    original_parameters:tuple[tuple[str,str],...]
    parameters:tuple[tuple[str,str],...]
    method:Literal['POST']='POST'

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def native_order_qualified(self)->Literal[False]:return False
    @property
    def external_activation_allowed(self)->Literal[False]:return False


def creation_call(request,*,clock,recv_window_ms=1000):
    require(type(request) is VenueCreationRequest and request.contract_version=='2026-10-29'
        and request.record_kind=='VENUE_CREATION_REQUEST','NATIVE_FROZEN_REQUEST_REQUIRED')
    scope=request.scope
    require(type(scope) is t.ScopeKey and scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),
        'NATIVE_ENVIRONMENT_DISABLED')
    require(type(scope.product) is t.Product and type(request.namespace) is N and type(request.operation) is O
        and type(request.endpoint) is E and CREATION_ROUTES.get((scope.product,request.namespace,request.operation))==request.endpoint,
        'NATIVE_STATIC_SERVICE_ROUTE_CHANGED')
    require(type(request.request_index) is int and request.request_index>=0,'NATIVE_REQUEST_INDEX_INVALID')
    _check_ids(request)
    require(type(recv_window_ms) is int and 1<=recv_window_ms<=60000,'NATIVE_BOUNDED_RECEIVE_WINDOW_REQUIRED')
    parameters=request.form_parameters()
    require(len({k for k,_ in parameters})==len(parameters),'NATIVE_DUPLICATE_FIELD')
    return NativeCreationCall(command_id=request.command_id,request_hash=canonical_hash(domain_payload(request)),
        namespace=request.namespace,endpoint=request.endpoint,original_parameters=parameters,
        parameters=(*parameters,('timestamp',str(timestamp_milliseconds(clock))),('recvWindow',str(recv_window_ms))))
