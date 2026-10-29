"""Explicit v3 write references, not a legacy payload or permission grant."""
from dataclasses import dataclass,fields
from typing import Literal
from . import types as t
from .canonical import utc_timestamp
from .order_program import identifier


ServiceNamespace=Literal['BINANCE_SPOT_ORDINARY','BINANCE_SPOT_ORDER_LIST','BINANCE_USD_M_ORDINARY',
    'BINANCE_USD_M_CONDITIONAL_ALGO','BINANCE_SPOT_EXECUTION_ALGO','BINANCE_USD_M_EXECUTION_ALGO','BINANCE_REAL_EQUITY']


@dataclass(frozen=True,slots=True,kw_only=True)
class ApplicationWriteV3:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['PROGRAM_APPLICATION_WRITE']
    command_id:str
    intent_id:str
    program_id:str
    scope:t.ScopeKey
    origin:Literal['APPLICATION_ACTION','APPLICATION_GENERATED_CHILD']
    operation:Literal['CREATE_REQUEST']
    service_namespace:ServiceNamespace
    parent_command_id:str|None
    # None for batch: each member has an identity, there is no venue parent.
    venue_client_id:str|None
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
        if self.contract_version!='2026-10-29' or self.record_kind!='PROGRAM_APPLICATION_WRITE':raise ValueError('PROGRAM_WRITE_VERSION_REQUIRED')
        if self.origin not in ('APPLICATION_ACTION','APPLICATION_GENERATED_CHILD'):raise ValueError('NATIVE_CHILD_IS_NOT_APPLICATION_WRITE')
        if self.operation!='CREATE_REQUEST' or self.state not in ('DURABLE_READY','MAY_HAVE_SENT','UNKNOWN'):raise ValueError('PROGRAM_WRITE_STATE_OR_OPERATION_UNSUPPORTED')
        if self.scope.environment not in (t.Environment.PAPER,t.Environment.RESEARCH):raise ValueError('PROGRAM_WRITE_ENVIRONMENT_DISABLED')
        for name in ('command_id','intent_id','program_id','owner_strategy_id','durable_record_ref'):identifier(getattr(self,name))
        if (self.origin=='APPLICATION_GENERATED_CHILD')!=(self.parent_command_id is not None):raise ValueError('PROGRAM_WRITE_PARENT_BINDING_REQUIRED')
        if self.parent_command_id is not None:identifier(self.parent_command_id)
        if self.venue_client_id is not None:identifier(self.venue_client_id)
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
