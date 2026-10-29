"""Protected historical source attestations; no cash, positions or send authority.

The trusted host explicitly provisions a separate verifier-role UnitOfWork.
Actual PAPER verification and fsync finish before this transaction opens. Its
commit precedes any future owner posting transaction; no cross-role nested lock
or caller-supplied `source_verified` flag can approve an execution fact.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork
from .paper_program_reports import PaperProgramReportVerifier,PaperProgramVerification,NormalizedPaperFill


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFillSourceApproval:
    approval_id:str
    identity_hash:str
    normalized:NormalizedPaperFill


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFillSourceResult:
    command_id:str
    status:Literal['BLOCKED','NO_FILLS','APPROVED','SOURCE_ONLY']
    verification:PaperProgramVerification
    approvals:tuple[ProgramFillSourceApproval,...]

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def balance_credit_authorized(self)->Literal[False]:return False


def _source_context(tx,command_id):
    row=tx.execute('SELECT c.context,m.context,w.payload,b.payload,c.token_hash,m.token_hash '
        'FROM ledger.program_permit_consumptions c JOIN ledger.program_dispatch_marks m USING(scope_id,command_id) '
        'JOIN ledger.order_writes_v2 w ON w.scope_id=c.scope_id AND w.record_id=c.command_id AND w.sequence=1 '
        'JOIN ledger.program_request_bindings b ON b.scope_id=c.scope_id AND b.record_id=c.command_id AND b.sequence=1 '
        'WHERE c.scope_id=%s AND c.command_id=%s',(scope_id(tx.scope),command_id)).fetchone()
    require(row is not None and row[4]==row[5],'PROGRAM_COMMITTED_SOURCE_REQUIRED')
    consumed,marked,write,binding,_,_=row
    require(write['scope']==domain_payload(tx.scope) and binding['request_hash']==write['request_hash']
        and canonical_hash(binding)==write['binding_hash'],'PROGRAM_ORIGINAL_SOURCE_CHANGED')
    return dict(consumption_hash=canonical_hash(consumed),dispatch_mark_hash=canonical_hash(marked),
        original_write_hash=canonical_hash(write),binding_hash=canonical_hash(binding),request_hash=write['request_hash'])


class ProgramFillSourceService:
    def __init__(self,uow,scope,*,verifier):
        require(type(uow) is UnitOfWork,'PROGRAM_SOURCE_UOW_REQUIRED')
        require(type(scope) is t.ScopeKey and scope.environment==t.Environment.PAPER,'PAPER_SOURCE_ONLY')
        require(type(verifier) is PaperProgramReportVerifier and verifier.scope==scope,
            'ACTUAL_SCOPED_PAPER_VERIFIER_REQUIRED')
        self.uow,self.scope,self.verifier=uow,scope,verifier

    def approve(self,command_id):
        require(type(command_id) is str and 0<len(command_id)<=256,'ORIGINAL_COMMAND_ID_REQUIRED')
        # All venue reads, normalization and durable archive fsync finish first.
        verified=self.verifier.verify(command_id)
        require(type(verified) is PaperProgramVerification and verified.command_id==command_id,
            'ACTUAL_PAPER_VERIFICATION_REQUIRED')
        if not verified.source_verified:
            return ProgramFillSourceResult(command_id=command_id,status='BLOCKED',verification=verified,approvals=())
        if not verified.fills:
            return ProgramFillSourceResult(command_id=command_id,status='NO_FILLS',verification=verified,approvals=())
        approvals=[]
        with self.uow.transaction(self.scope) as tx:
            context=_source_context(tx,command_id)
            for normalized in verified.fills:
                payload=dict(contract_version='2026-10-30',record_kind='PAPER_PROGRAM_FILL_SOURCE_V1',
                    scope=domain_payload(self.scope),command_id=command_id,**context,
                    normalized=domain_payload(normalized),verification=domain_payload(verified))
                expected=canonical_hash(payload)
                row=tx.execute('SELECT ledger.approve_program_fill_source(%s,%s,%s::jsonb,%s)',
                    (scope_id(self.scope),command_id,canonical_json(payload),tx.transaction_id)).fetchone()
                require(row==(expected,),'PROGRAM_SOURCE_COMMITMENT_CHANGED')
                identity=canonical_hash(dict(scope=domain_payload(self.scope),venue=normalized.fill.venue,
                    native_trade_id=normalized.fill.native_trade_id))
                approvals.append(ProgramFillSourceApproval(approval_id=expected,identity_hash=identity,normalized=normalized))
        # A receipt is returned only after the protected source transaction commits.
        return ProgramFillSourceResult(command_id=command_id,
            status='SOURCE_ONLY' if verified.discrepancies else 'APPROVED',verification=verified,approvals=tuple(approvals))
