"""Lossless local child request candidates; no persistence or sending authority.

Recovery reads this saved binding; materialization is not a retry operation.
Existing V3 program binding rejects this distinct local compilation hash.
"""
from dataclasses import dataclass
from typing import Literal
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_program import identifier
from trading_system.intents.local_child_compilation import LocalChildCompilation,compile_local_child
from trading_system.intents.program_compilation import Namespace
from .creation_requests import (VenueCreationRequest,AssignedClientIds,NodeClientId,
    _fields,_append,require,digest)


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalChildRequest:
    activation_id:str
    compilation_hash:str
    request:VenueCreationRequest
    contract_version:Literal['2026-10-31']='2026-10-31'
    record_kind:Literal['LOCAL_CHILD_REQUEST_V1']='LOCAL_CHILD_REQUEST_V1'

    @property
    def execution_authorized(self)->Literal[False]:return False


def materialize_local_child_request(child,activation_id,command_id,rules,capabilities,**context):
    require(type(child) is LocalChildCompilation,'LOCAL_CHILD_COMPILATION_REQUIRED')
    identifier(activation_id);identifier(command_id)
    expected=compile_local_child(child.parent,child.node.quantity.value,rules,capabilities,**context)
    require(digest(expected)==digest(child),'LOCAL_CHILD_COMPILATION_CHANGED')
    return _encode_child_request(child,activation_id,command_id)


def _encode_child_request(child,activation_id,command_id):
    require(type(child) is LocalChildCompilation,'LOCAL_CHILD_COMPILATION_REQUIRED')
    identifier(activation_id);identifier(command_id)
    route=child.request
    require(route.namespace in (Namespace.SPOT_ORDINARY,Namespace.USD_M_ORDINARY,Namespace.USD_M_CONDITIONAL),
        'LOCAL_CHILD_ADDITIONAL_ROUTE_BINDING_REQUIRED')
    compilation=digest(child)
    client=canonical_hash(dict(version='LOCAL_CHILD_ID_V1',activation_id=activation_id,
        command_id=command_id,scope=domain_payload(child.parent.scope),
        node_id=child.node.original.node_id))[:32]
    conditional=route.namespace==Namespace.USD_M_CONDITIONAL
    ids=AssignedClientIds(command_id=command_id,compilation_hash=compilation,request_index=0,
        parent_client_id=client if conditional else None,
        members=() if conditional else (NodeClientId(node_id=child.node.original.node_id,client_id=client),))
    fields=_append(_fields(route.parameters),'clientAlgoId' if conditional else 'newClientOrderId',client)
    request=VenueCreationRequest(contract_version='2026-10-29',record_kind='VENUE_CREATION_REQUEST',
        command_id=command_id,scope=child.parent.scope,compilation_hash=compilation,
        profile_hash=child.parent.profile_hash,request_index=0,namespace=route.namespace,
        operation=route.operation,endpoint=route.endpoint,fields=fields,batch_orders=(),
        assigned_ids=ids,wallet_selection=None)
    return LocalChildRequest(activation_id=activation_id,compilation_hash=compilation,request=request)


def verify_local_child_request(record,child,rules,capabilities,**context):
    require(type(record) is LocalChildRequest,'LOCAL_CHILD_REQUEST_REQUIRED')
    expected=materialize_local_child_request(child,record.activation_id,record.request.command_id,
        rules,capabilities,**context)
    require(digest(record)==digest(expected),'LOCAL_CHILD_REQUEST_CHANGED')
    return digest(expected)



def restore_local_child_request(record,child,*,activation_id,command_id):
    """Read-only original binding, independent of current expiry/rules.

    Inputs must come from the protected persisted admission source. Matching
    bytes alone do not authenticate that source, establish submission outcome,
    discharge obligations or authorize a resend. No IDs are changed on restore.
    """
    require(type(record) is LocalChildRequest,'LOCAL_CHILD_REQUEST_REQUIRED')
    expected=_encode_child_request(child,activation_id,command_id)
    require(digest(record)==digest(expected),'LOCAL_CHILD_ORIGINAL_BINDING_CHANGED')
    return record
