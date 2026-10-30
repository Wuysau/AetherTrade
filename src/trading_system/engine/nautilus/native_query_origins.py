"""Server-only historical ledger provenance for original native query IDs.

No venue source, current ownership, financial validity or new send authority is
inferred. Token expiry does not erase historical original-ID recovery duties.
"""
from dataclasses import dataclass,replace
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.recovery.original_creation import OriginalCreation,read_original_creation
from trading_system.storage.models import scope_id


def require(condition,code):
    if not condition:raise ValueError(code)


def digest(value):return canonical_hash(domain_payload(value))


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeOriginalQueryOrigins:
    original:OriginalCreation
    mark_ref:str
    consumption_ref:str|None
    consumption_context_hash:str|None

    @property
    def command_id(self):return self.original.initial.command_id
    @property
    def request(self):return self.original.request
    @property
    def current_state(self):return self.original.current_state
    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def can_resubmit(self)->Literal[False]:return False
    @property
    def ownership_established(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


def load_native_query_origins(uow,scope,command_id):
    require(type(scope) is t.ScopeKey and scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH)
        and type(command_id) is str and 0<len(command_id)<=256,'NATIVE_ORIGINAL_QUERY_SCOPE_REQUIRED')
    with uow.transaction(scope) as tx:
        original=read_original_creation(tx,command_id)
        require(original is not None and original.current_state in ('MAY_HAVE_SENT','UNKNOWN'),
            'NATIVE_POTENTIALLY_SENT_ORIGINAL_REQUIRED')
        key=scope_id(scope);may=replace(original.initial,state='MAY_HAVE_SENT')
        marked=tx.execute('SELECT write_payload,token_hash,database_transaction::text,pg_current_xact_id()::text '
            'FROM ledger.program_dispatch_marks WHERE scope_id=%s AND command_id=%s',(key,command_id)).fetchone()
        require(marked is not None and canonical_hash(marked[0])==digest(may) and marked[2]!=marked[3],
            'NATIVE_COMMITTED_ORIGINAL_MARK_REQUIRED')
        consumed=tx.execute('SELECT context,token_hash,database_transaction::text,pg_current_xact_id()::text '
            'FROM ledger.program_permit_consumptions WHERE scope_id=%s AND command_id=%s',(key,command_id)).fetchone()
        context_hash=consumption_ref=None
        if consumed is not None:
            context=consumed[0]
            require(consumed[1]==marked[1] and consumed[2] not in (marked[2],consumed[3])
                and canonical_hash(context['write'])==digest(may)
                and context['request_hash']==original.initial.request_hash==digest(original.request),
                'NATIVE_ORIGINAL_CONSUMPTION_CHANGED')
            context_hash=canonical_hash(context)
            consumption_ref='ledger.program_permit_consumptions:'+key+':'+command_id
        result=NativeOriginalQueryOrigins(original=original,mark_ref='ledger.program_dispatch_marks:'+key+':'+command_id,
            consumption_ref=consumption_ref,consumption_context_hash=context_hash)
    return result
