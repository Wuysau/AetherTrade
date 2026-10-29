"""Owner-held V3 creation dispatch with committed MAY and original-ID recovery.

No caller-supplied permit, callback transport, fake legacy intent or automatic
retry exists here. The actual controlled engine consumes the single permit.
Receipts describe observations only; later verified source ingestion and the
same ledger's financial reconciliation remain separate responsibilities.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.program_financial import require
from trading_system.engine.paper import PaperEngine
from trading_system.engine.paper_market import PaperCreationReceipt
from trading_system.recovery.original_creation import OriginalCreation,read_original_creation
from trading_system.runtime.execution_owner import ExecutionOwner
from .program_commands import ProgramCommandBoundary,ProgramCommandRepository

def digest(value):return canonical_hash(domain_payload(value))


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramDispatchReceipt:
    command_id:str
    request_hash:str
    status:Literal['OBSERVED','UNKNOWN','RECOVERY_REQUIRED']
    original:OriginalCreation
    observation:PaperCreationReceipt|None

    @property
    def execution_authorized(self)->Literal[False]:return False

    @property
    def funds_release_authorized(self)->Literal[False]:return False


class ProgramCommandDispatcher:
    def __init__(self,uow,scope,*,boundary=None,engine=None,owner=None,side_effect_guard=None):
        require(type(scope) is t.ScopeKey and scope.environment==t.Environment.PAPER,'PROGRAM_PAPER_SCOPE_REQUIRED')
        self.uow,self.scope=uow,scope
        self.boundary,self.engine,self.owner=boundary,engine,owner
        self.side_effect_guard=side_effect_guard

    def _original(self,command_id):
        with self.uow.transaction(self.scope) as tx:original=read_original_creation(tx,command_id)
        require(original is not None,'PROGRAM_ORIGINAL_CREATION_REQUIRED')
        return original

    def dispatch(self,command_id,lease):
        require(type(command_id) is str and bool(command_id),'PROGRAM_COMMAND_ID_REQUIRED')
        require(type(self.boundary) is ProgramCommandBoundary and self.boundary.scope==self.scope
            and type(self.engine) is PaperEngine and self.engine.scope==self.scope
            and type(self.owner) is ExecutionOwner and self.owner.engine is self.engine
            and callable(self.side_effect_guard),'PROGRAM_TRUSTED_DISPATCH_COMPOSITION_REQUIRED')
        with self.owner.guard(lease):
            original=self._original(command_id)
            if original.current_state!='DURABLE_READY':
                return ProgramDispatchReceipt(command_id=command_id,request_hash=digest(original.request),
                    status='RECOVERY_REQUIRED',original=original,observation=None)
            # This method returns only AFTER protected MAY commits. A process
            # death here leaves durable MAY, which the branch above never sends.
            permit=self.boundary.mark_may_have_sent(command_id,lease)
            try:
                # The trusted host supplies the SAME control/revocation guard
                # used by authority changes. There is no permissive default.
                with self.side_effect_guard(original.initial,permit,lease):
                    with self.owner.guard(lease):
                        result=self.engine.submit_creation(original.initial,original.request,permit,
                            boundary=self.boundary,lease=lease,owner=self.owner)
                require(type(result) is PaperCreationReceipt and result.command_id==command_id
                    and result.scope==self.scope and result.request_hash==digest(original.request)
                    and result.namespace==original.request.namespace.value
                    and result.client_ids==tuple(m.client_id for m in original.request.assigned_ids.members)
                    and result.status in ('SUBMITTED','PARTIALLY_FILLED','FILLED','REJECTED','EXPIRED'),
                    'PROGRAM_CREATION_OBSERVATION_CHANGED')
            except Exception:
                # Even an error before the engine's IO cannot prove NOT_SENT.
                # Failure of this write propagates, preserving durable MAY.
                with self.uow.transaction(self.scope) as tx:ProgramCommandRepository(tx).record_unknown(command_id)
                current=self._original(command_id)
                return ProgramDispatchReceipt(command_id=command_id,request_hash=digest(current.request),
                    status='UNKNOWN',original=current,observation=None)
            return ProgramDispatchReceipt(command_id=command_id,request_hash=digest(original.request),
                status='OBSERVED',original=self._original(command_id),observation=result)
