"""Local child write identity V1; source binding is not a permit or SQL authority.

This separate kind cannot pass the existing V3 writer/permit entrypoints.
Activation and schedule hashes must be authenticated by future child admission.
"""
from dataclasses import dataclass,fields
from typing import Literal
from . import types as t
from .canonical import utc_timestamp
from .order_program import identifier


ServiceNamespace=Literal['BINANCE_SPOT_ORDINARY','BINANCE_SPOT_ORDER_LIST','BINANCE_USD_M_ORDINARY',
    'BINANCE_USD_M_CONDITIONAL_ALGO','BINANCE_SPOT_EXECUTION_ALGO','BINANCE_USD_M_EXECUTION_ALGO','BINANCE_REAL_EQUITY']


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalChildWriteV1:
    contract_version:Literal['2026-10-31']
    record_kind:Literal['LOCAL_CHILD_WRITE_V1']
    command_id:str
    intent_id:str
    program_id:str
    scope:t.ScopeKey
    origin:Literal['APPLICATION_GENERATED_CHILD']
    operation:Literal['CREATE_REQUEST']
    service_namespace:ServiceNamespace
    activation_id:str
    activation_hash:str
    activation_state_sequence:int
    parent_authority_hash:str
    schedule_evidence_hash:str
    venue_client_id:str
    owner_strategy_id:str
    ownership_epoch:int
    execution_epoch:int
    policy_revision:int
    request_index:int
    canonical_program_hash:str
    compilation_hash:str
    allocation_hash:str
    intent_hash:str
    basis_hash:str
    decision_hash:str
    archive_hash:str
    request_hash:str
    binding_hash:str
    state:Literal['DURABLE_READY','MAY_HAVE_SENT','UNKNOWN']
    durable_record_ref:str
    created_at:str
    valid_until:str

    def __post_init__(self):
        if self.contract_version!='2026-10-31' or self.record_kind!='LOCAL_CHILD_WRITE_V1':raise ValueError('PROGRAM_WRITE_VERSION_REQUIRED')
        if self.origin!='APPLICATION_GENERATED_CHILD':raise ValueError('NATIVE_CHILD_IS_NOT_APPLICATION_WRITE')
        if self.operation!='CREATE_REQUEST' or self.state not in ('DURABLE_READY','MAY_HAVE_SENT','UNKNOWN'):raise ValueError('PROGRAM_WRITE_STATE_OR_OPERATION_UNSUPPORTED')
        if self.scope.environment not in (t.Environment.PAPER,t.Environment.RESEARCH):raise ValueError('PROGRAM_WRITE_ENVIRONMENT_DISABLED')
        for name in ('command_id','intent_id','program_id','owner_strategy_id','durable_record_ref'):identifier(getattr(self,name))
        identifier(self.activation_id);identifier(self.venue_client_id)
        if type(self.activation_state_sequence) is not int or self.activation_state_sequence<1:
            raise ValueError('LOCAL_CHILD_ACTIVATION_REVISION_REQUIRED')
        routes={t.Product.SPOT:'BINANCE_SPOT_ORDINARY',t.Product.USD_M_PERP:'BINANCE_USD_M_ORDINARY',
                t.Product.US_EQUITY:'BINANCE_REAL_EQUITY'}
        if self.service_namespace!=routes.get(self.scope.product):
            raise ValueError('LOCAL_CHILD_PRODUCT_NAMESPACE_MISMATCH')
        for name in ('ownership_epoch','execution_epoch','policy_revision'):
            if type(getattr(self,name)) is not int or getattr(self,name)<1:raise ValueError('PROGRAM_WRITE_OWNER_REVISIONS_REQUIRED')
        if type(self.request_index) is not int or self.request_index<0:raise ValueError('PROGRAM_WRITE_REQUEST_INDEX_REQUIRED')
        for field in fields(self):
            if field.name.endswith('_hash'):
                value=getattr(self,field.name)
                if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('PROGRAM_WRITE_EXACT_HASH_REQUIRED')
        if not utc_timestamp(self.created_at)<utc_timestamp(self.valid_until):raise ValueError('PROGRAM_WRITE_VALIDITY_REQUIRED')

    @property
    def execution_authorized(self)->Literal[False]:return False
