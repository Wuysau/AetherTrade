"""Existing-ledger transaction for approved programme execution facts.

Raw native fills are not cash/position ownership. This transaction preserves
them, the unit bridge and unresolved accounting duties together. The financial
posting stage must rederive exact asset effects from the committed source and
current allocation; until that stage is installed the book remains unchanged
and a durable recovery lock prevents more trading risk.
"""
from dataclasses import dataclass,replace
from pathlib import Path
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.program_financial import FinancialUnit
from trading_system.risk.locks import RiskLockService,POLICIES
from trading_system.storage.events import EventStore
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork
from .evidence import EvidenceIngestor
from .fills import FillLedger
from .program_fill_source import ProgramFillSourceService,CommittedProgramFillSources


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFillAccountingRecord:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_FILL_ACCOUNTING_RAW_V1']
    scope:t.ScopeKey
    command_id:str
    identity_hash:str
    source_approval_id:str
    fill_id:str
    native_unit_bridge:FinancialUnit
    fees_known:bool
    book_before_hash:str
    missing:tuple[str,...]
    retained_duties:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramAccountingResult:
    command_id:str
    status:Literal['SOURCE_BLOCKED','NO_FILLS','RAW_RECORDED_ECONOMICS_BLOCKED']
    fills:tuple[t.FillReceipt,...]
    missing:tuple[str,...]
    retained_duties:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def balance_credit_authorized(self)->Literal[False]:return False


class ProgramPaperAccountingService:
    def __init__(self,uow,scope,*,source_service,trade_id_scope):
        require(type(uow) is UnitOfWork and type(scope) is t.ScopeKey
            and scope.environment==t.Environment.PAPER,'PAPER_ACCOUNTING_UOW_REQUIRED')
        require(type(source_service) is ProgramFillSourceService and source_service.scope==scope,
            'ACTUAL_SCOPED_SOURCE_SERVICE_REQUIRED')
        self.uow,self.scope,self.source_service=uow,scope,source_service
        self._fills=FillLedger(uow,scope,trade_id_scope=trade_id_scope)
        self._evidence=EvidenceIngestor(uow,scope,read_raw=self._read_raw)

    def _read_raw(self,ref):
        bridge=self.source_service.verifier.archive_bridge
        root=bridge.archive.resolve();path=Path(ref.id).resolve()
        require(path.parent==root and path.name==ref.hash+'.raw' and ref.environment==self.scope.environment,
            'PROGRAM_ACCOUNTING_ARCHIVE_BINDING_CHANGED')
        return path.read_bytes()

    def _prepare(self,approved):
        normalized=approved.normalized;fill=normalized.fill;ref=fill.evidence
        event=t.VenueEvent(event_id='program-source:'+canonical_hash(dict(scope=domain_payload(self.scope),
            native_trade_id=fill.native_trade_id,hash=ref.hash)),source=ref.source,
            source_event_id=fill.native_trade_id,source_sequence=None,account_id=self.scope.venue_account_id,
            instrument_uid=self.scope.instrument_uid,order_refs=(fill.order,),occurred_at=fill.occurred_at,
            received_at=ref.visible_at,processed_at=ref.visible_at,payload_hash=ref.hash,raw_payload_ref=ref)
        prepared=self._evidence.prepare(event)
        require(prepared.event is not None,'PROGRAM_APPROVED_EVENT_BINDING_INVALID')
        return prepared

    def apply(self,command_id):
        # Source verification/fsync and its separate protected transaction both
        # finish before opening the owner's collateral transaction.
        source=self.source_service.approve(command_id)
        if not source.approvals:
            return ProgramAccountingResult(command_id=command_id,
                status='SOURCE_BLOCKED' if source.status=='BLOCKED' else 'NO_FILLS',fills=(),
                missing=source.verification.discrepancies,retained_duties=source.verification.retained_duties)
        prepared={a.approval_id:self._prepare(a) for a in source.approvals}
        missing=tuple(dict.fromkeys(('CURRENT_ACCOUNTING_FACTS_REQUIRED',*source.verification.discrepancies)))
        receipts=[]
        with self.uow.transaction(self.scope) as tx:
            book=tx.projections.book(self.scope)
            reader=CommittedProgramFillSources(tx)
            for supplied in source.approvals:
                actual=reader.read(supplied.approval_id)
                require(actual==supplied,'PROGRAM_ACCOUNTING_APPROVED_FACTS_CHANGED')
                evidence=self._evidence.ingest_in_tx(tx,prepared[actual.approval_id])
                require(evidence.accepted,'PROGRAM_ACCOUNTING_EVIDENCE_UNRESOLVED')
                # A duplicate delivery returns its original committed raw ref.
                # Do not bypass FillLedger's exact evidence equality check.
                fill=replace(actual.normalized.fill,evidence=evidence.raw_ref)
                receipt=self._fills.record_in_tx(tx,fill)
                receipts.append(receipt)
                record_id='program-fill-accounting:'+actual.identity_hash
                old=tx._latest('audit_events',record_id,ProgramFillAccountingRecord)
                if old is None:
                    record=ProgramFillAccountingRecord(contract_version='2026-10-30',
                        record_kind='PROGRAM_FILL_ACCOUNTING_RAW_V1',scope=self.scope,command_id=command_id,
                        identity_hash=actual.identity_hash,source_approval_id=actual.approval_id,fill_id=receipt.fill_id,
                        native_unit_bridge=actual.normalized.economics.unit,fees_known=actual.normalized.fees_known,
                        book_before_hash=canonical_hash(domain_payload(book)),missing=missing,
                        retained_duties=source.verification.retained_duties)
                    tx._append('audit_events',record_id,record,0)
                    permission,required=POLICIES['PROGRAM_ACCOUNTING_UNRESOLVED']
                    lock=t.RiskLock(lock_id=record_id,scope=self.scope,cause='PROGRAM_ACCOUNTING_UNRESOLVED',
                        state='ACTIVE',permission=permission,required_evidence=required,raised_at=evidence.raw_ref.visible_at)
                    tx.risk_locks.append(lock,0)
                    EventStore(received_at=evidence.raw_ref.visible_at).append(tx,
                        RiskLockService._event(lock,1,(evidence.raw_ref,),lock.raised_at),0)
                else:
                    require(old.scope==self.scope and old.command_id==command_id and old.identity_hash==actual.identity_hash
                        and old.fill_id==receipt.fill_id and old.native_unit_bridge==actual.normalized.economics.unit,
                        'PROGRAM_ACCOUNTING_NATIVE_IDENTITY_CONFLICT')
        return ProgramAccountingResult(command_id=command_id,status='RAW_RECORDED_ECONOMICS_BLOCKED',
            fills=tuple(receipts),missing=missing,retained_duties=source.verification.retained_duties)
