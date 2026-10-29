"""Lossless unsigned creation requests. No signer, transport, grant or permit.

Client identities are assigned once before durable persistence, then restored
for query/recovery. Materialization never changes order economics or creates
commands for venue-generated children. Routing facts are not financial proof.
"""
from dataclasses import dataclass
import re
import json
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.intents.program_compilation import (
    ProgramCompilation,Namespace,Operation,Endpoint,FieldName,Leg,
)
from trading_system.intents.product_rules import TrustedProductRules


def require(condition,reason):
    if not condition:raise ValueError(reason)


def digest(record):return canonical_hash(domain_payload(record))


@dataclass(frozen=True,slots=True,kw_only=True)
class NodeClientId:
    node_id:str
    client_id:str


@dataclass(frozen=True,slots=True,kw_only=True)
class AssignedClientIds:
    command_id:str
    compilation_hash:str
    request_index:int
    parent_client_id:str|None
    members:tuple[NodeClientId,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class EquityWalletSelection:
    scope:t.ScopeKey
    profile_hash:str
    quote_asset:str
    wallet:Literal['CARD','MAIN']
    evidence:t.EvidenceRef
    valid_until:str


_ID_FIELDS=frozenset(('newClientOrderId','clientOrderId','clientAlgoId','listClientOrderId',
    'aboveClientOrderId','belowClientOrderId','workingClientOrderId','pendingClientOrderId',
    'pendingAboveClientOrderId','pendingBelowClientOrderId','walletType'))
_NAMES=frozenset(f.value for f in FieldName)|_ID_FIELDS|frozenset(
    prefix+f.value[0].upper()+f.value[1:] for prefix in ('above','below','working','pending','pendingAbove','pendingBelow')
    for f in FieldName)


@dataclass(frozen=True,slots=True,kw_only=True)
class VenueField:
    name:str
    value:str|int|bool

    def __post_init__(self):
        require(self.name in _NAMES,'CREATION_FIELD_UNREGISTERED')
        require(type(self.value) in (str,int,bool) and self.value!='','CREATION_FIELD_VALUE_INVALID')

    def text(self):
        return str(self.value).lower() if type(self.value) is bool else str(self.value)


@dataclass(frozen=True,slots=True,kw_only=True)
class BatchOrderFields:
    node_id:str
    fields:tuple[VenueField,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class VenueCreationRequest:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['VENUE_CREATION_REQUEST']
    command_id:str
    scope:t.ScopeKey
    compilation_hash:str
    profile_hash:str
    request_index:int
    namespace:Namespace
    operation:Operation
    endpoint:Endpoint
    fields:tuple[VenueField,...]
    batch_orders:tuple[BatchOrderFields,...]
    assigned_ids:AssignedClientIds
    wallet_selection:EquityWalletSelection|None

    @property
    def execution_authorized(self)->Literal[False]:return False

    def form_parameters(self):
        if self.batch_orders:
            require(not self.fields,'BATCH_ROOT_FIELDS_FORBIDDEN')
            body=[{f.name:f.text() for f in item.fields} for item in self.batch_orders]
            return (('batchOrders',json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)),)
        return tuple((f.name,f.text()) for f in self.fields)


def _selected(plan,index):
    require(type(plan) is ProgramCompilation and type(index) is int and 0<=index<len(plan.requests),
        'CREATION_REQUEST_INDEX_INVALID')
    request=plan.requests[index]
    require(request.operation!=Operation.LOCAL_ALGORITHM and request.endpoint is not None,
        'LOCAL_ALGORITHM_ACTIVATION_IS_NOT_EXTERNAL_WRITE')
    require(len(set(request.node_ids))==len(request.node_ids),'CREATION_NODE_IDENTITIES_DUPLICATED')
    nodes={n.original.node_id:n for n in plan.nodes}
    require(set(request.node_ids)<=set(nodes),'CREATION_NODE_MISSING')
    return request,tuple(nodes[n] for n in request.node_ids)


def assign_client_ids(plan,index,command_id):
    """Creation only. Recovery must read the saved result, never assign again."""
    require(type(command_id) is str and bool(command_id),'CREATION_COMMAND_ID_REQUIRED')
    request,nodes=_selected(plan,index)
    def identity(role):
        return canonical_hash({'command':command_id,'scope':domain_payload(plan.scope),
            'request':index,'role':role})[:32]
    algorithm=request.namespace in (Namespace.SPOT_EXECUTION,Namespace.USD_M_EXECUTION,Namespace.USD_M_CONDITIONAL)
    parent=identity('parent') if algorithm or request.namespace==Namespace.SPOT_LIST else None
    members=() if algorithm else tuple(NodeClientId(node_id=n.original.node_id,
        client_id=identity('node:'+n.original.node_id)) for n in nodes)
    return AssignedClientIds(command_id=command_id,compilation_hash=digest(plan),request_index=index,
        parent_client_id=parent,members=members)


def _fields(parameters):
    result=tuple(VenueField(name=p.wire_name,value=p.value) for p in parameters)
    require(len({f.name for f in result})==len(result),'CREATION_FIELD_DUPLICATED')
    return result


def _append(fields,name,value):
    require(name not in {f.name for f in fields},'CREATION_IDENTITY_FIELD_CONFLICT')
    return (*fields,VenueField(name=name,value=value))


def materialize_creation(plan,index,profile,ids,*,wallet_selection=None,now=None):
    request,nodes=_selected(plan,index)
    require(type(profile) is TrustedProductRules and profile.scope==plan.scope
        and digest(profile)==plan.profile_hash,'CREATION_PROFILE_CHANGED')
    require(type(ids) is AssignedClientIds and ids.compilation_hash==digest(plan) and ids.request_index==index,
        'CREATION_ID_BINDING_CHANGED')
    require(type(ids.command_id) is str and bool(ids.command_id),'CREATION_COMMAND_ID_REQUIRED')
    # The 2026-10-29 assignment scheme is frozen. This verifies stored identity
    # provenance; recovery never replaces those IDs or creates a new attempt.
    require(ids==assign_client_ids(plan,index,ids.command_id),'CREATION_CLIENT_ASSIGNMENT_CHANGED')
    values=tuple(m.client_id for m in ids.members)+((ids.parent_client_id,) if ids.parent_client_id is not None else ())
    require(values and len(set(values))==len(values)
        and all(type(v) is str and re.fullmatch('[A-Za-z0-9_-]{32}',v) for v in values),
        'CREATION_CLIENT_ID_INVALID_OR_DUPLICATED')
    by_id={m.node_id:m.client_id for m in ids.members}
    algorithm=request.namespace in (Namespace.SPOT_EXECUTION,Namespace.USD_M_EXECUTION,Namespace.USD_M_CONDITIONAL)
    if algorithm:
        require(ids.parent_client_id is not None and not ids.members,'ALGORITHM_PARENT_ID_REQUIRED')
    else:
        require(tuple(m.node_id for m in ids.members)==request.node_ids,'CREATION_MEMBER_IDS_MISMATCH')
        require((ids.parent_client_id is not None)==(request.namespace==Namespace.SPOT_LIST),'CREATION_PARENT_ID_MISMATCH')
    fields=_fields(request.parameters);batch=()
    if request.operation!=Operation.BATCH:
        require(tuple(f.value for f in fields if f.name=='symbol')==(profile.venue_symbol,),
            'CREATION_SYMBOL_PROFILE_MISMATCH')
    if request.namespace==Namespace.EQUITY:
        require(len(nodes)==1 and tuple(f.value for f in fields if f.name=='quoteAsset')==(nodes[0].notional_asset,),
            'CREATION_QUOTE_ASSET_BINDING_MISMATCH')
    if request.operation==Operation.BATCH:
        require(request.namespace==Namespace.USD_M_ORDINARY and not fields,'BATCH_ROUTE_FIELDS_INVALID')
        batch=tuple(BatchOrderFields(node_id=n.original.node_id,fields=_append(
            _append(_fields(n.parameters),'symbol',profile.venue_symbol),'newClientOrderId',by_id[n.original.node_id])) for n in nodes)
    elif algorithm:
        fields=_append(fields,'clientAlgoId',ids.parent_client_id)
    elif request.namespace==Namespace.SPOT_LIST:
        fields=_append(fields,'listClientOrderId',ids.parent_client_id)
        names={Leg.ABOVE:'aboveClientOrderId',Leg.BELOW:'belowClientOrderId',Leg.WORKING:'workingClientOrderId',
            Leg.PENDING:'pendingClientOrderId',Leg.PENDING_ABOVE:'pendingAboveClientOrderId',Leg.PENDING_BELOW:'pendingBelowClientOrderId'}
        for node in nodes:
            require(node.leg in names,'CREATION_LIST_LEG_UNBOUND')
            fields=_append(fields,names[node.leg],by_id[node.original.node_id])
    else:
        require(len(nodes)==1,'CREATION_SINGLE_NODE_REQUIRED')
        fields=_append(fields,'clientOrderId' if request.namespace==Namespace.EQUITY else 'newClientOrderId',by_id[nodes[0].original.node_id])
    if request.namespace==Namespace.EQUITY and nodes[0].original.order.side=='BUY':
        selection=wallet_selection
        require(type(selection) is EquityWalletSelection,'EQUITY_PAYMENT_WALLET_REQUIRED')
        require(selection.scope==plan.scope and selection.profile_hash==plan.profile_hash
            and selection.quote_asset==nodes[0].notional_asset and selection.wallet in ('CARD','MAIN')
            and selection.evidence.environment==plan.scope.environment and selection.evidence.id and selection.evidence.source
            and len(selection.evidence.hash)==64 and all(c in '0123456789abcdef' for c in selection.evidence.hash),
            'EQUITY_PAYMENT_WALLET_BINDING_MISMATCH')
        require(now is not None and utc_timestamp(selection.evidence.visible_at)<=utc_timestamp(now)<utc_timestamp(selection.valid_until),
            'EQUITY_PAYMENT_WALLET_STALE')
        fields=_append(fields,'walletType',selection.wallet)
    else:require(wallet_selection is None,'UNEXPECTED_EQUITY_PAYMENT_WALLET')
    return VenueCreationRequest(contract_version='2026-10-29',record_kind='VENUE_CREATION_REQUEST',command_id=ids.command_id,
        scope=plan.scope,compilation_hash=digest(plan),profile_hash=plan.profile_hash,request_index=index,
        namespace=request.namespace,operation=request.operation,endpoint=request.endpoint,fields=fields,
        batch_orders=batch,assigned_ids=ids,wallet_selection=wallet_selection)


def verify_creation(request,plan,profile,ids,*,wallet_selection=None,now=None):
    require(type(request) is VenueCreationRequest,'CREATION_REQUEST_REQUIRED')
    expected=materialize_creation(plan,request.request_index,profile,ids,
        wallet_selection=wallet_selection,now=now)
    # Dataclass equality treats False==0 and True==1, although their wire
    # encodings differ. Compare the complete canonical typed content instead.
    require(digest(request)==digest(expected),'CREATION_REQUEST_CHANGED')
    return digest(expected)
