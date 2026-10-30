"""Management-only V4; creation and historical codecs remain unchanged."""
from dataclasses import dataclass,fields
from typing import Literal
from . import types as t
from .canonical import utc_timestamp
from .order_program import identifier


@dataclass(frozen=True,slots=True,kw_only=True)
class ManagementWriteV4:
    contract_version:Literal['2026-10-30.management-v4']
    record_kind:Literal['PROGRAM_MANAGEMENT_WRITE_V4']
    command_id:str
    original_command_id:str
    scope:t.ScopeKey
    program_id:str
    service_namespace:str
    original_client_id:str
    target_venue_id:str
    operation:Literal['CANCEL','KEEP_PRIORITY','MODIFY_LIMIT','CANCEL_REPLACE']
    owner_strategy_id:str
    ownership_epoch:int
    execution_epoch:int
    policy_revision:int
    plan_hash:str
    target_hash:str
    request_hash:str
    risk_hash:str
    funding_hash:str
    binding_hash:str
    state:Literal['DURABLE_READY','MAY_HAVE_SENT','UNKNOWN']
    created_at:str
    valid_until:str

    def __post_init__(self):
        if self.contract_version!='2026-10-30.management-v4' or self.record_kind!='PROGRAM_MANAGEMENT_WRITE_V4':
            raise ValueError('MANAGEMENT_V4_REQUIRED')
        if self.scope.environment!=t.Environment.PAPER:raise ValueError('MANAGEMENT_PAPER_ONLY')
        if self.operation not in ('CANCEL','KEEP_PRIORITY','MODIFY_LIMIT','CANCEL_REPLACE'):
            raise ValueError('MANAGEMENT_OPERATION_REQUIRED')
        if self.state not in ('DURABLE_READY','MAY_HAVE_SENT','UNKNOWN'):raise ValueError('MANAGEMENT_STATE_REQUIRED')
        for name in ('command_id','original_command_id','program_id','original_client_id','target_venue_id','owner_strategy_id'):
            identifier(getattr(self,name))
        for name in ('ownership_epoch','execution_epoch','policy_revision'):
            if type(getattr(self,name)) is not int or getattr(self,name)<1:raise ValueError('MANAGEMENT_EPOCH_REQUIRED')
        for f in fields(self):
            if f.name.endswith('_hash'):
                value=getattr(self,f.name)
                if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                    raise ValueError('MANAGEMENT_FULL_HASH_REQUIRED')
        if not utc_timestamp(self.created_at)<utc_timestamp(self.valid_until):raise ValueError('MANAGEMENT_WINDOW_REQUIRED')

    @property
    def execution_authorized(self)->Literal[False]:return False
