"""Full typed admission through one financial transaction and existing outbox.

Stable request identities are created for the exact compiled initial writes.
They are identities, never permits. A local schedule has no venue mother.
"""
from dataclasses import dataclass,replace
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.program_financial import require
from trading_system.intents.program_compilation import ProgramCompilation,Operation,Timing
from trading_system.intents.generic_program_intake import read_program_source,current_fence,intake_key
from trading_system.risk.admission_clock import AdmissionWindow
from trading_system.risk.program_financial_admission import ProgramFinancialBundle,BUNDLE_PREFIX
from trading_system.risk.program_financial_admission import (
    ProgramFinancialAttachment,ATTACHMENT_PREFIX,current_program_bundle_witness)
from trading_system.storage.models import scope_id

ADMISSION_PREFIX='generic-program-admission:'


def digest(value):return canonical_hash(domain_payload(value))


def creation_command_ids(intent_id,compilation):
    require(type(intent_id) is str and bool(intent_id),'PROGRAM_FINANCIAL_INTENT_REQUIRED')
    require(type(compilation) is ProgramCompilation,'PROGRAM_COMPILATION_REQUIRED')
    local=compilation.original_program.template in ('TWAP','VP') and (
        compilation.original_program.execution_mode!='VENUE_NATIVE')
    if local:
        require(len(compilation.requests)==1
            and compilation.requests[0].operation==Operation.LOCAL_ALGORITHM,
            'LOCAL_PROGRAM_ACTIVATION_REQUIRED')
        return ()
    require(bool(compilation.requests) and all(request.operation!=Operation.LOCAL_ALGORITHM
        and request.timing==Timing.INITIAL for request in compilation.requests),
        'STAGED_PROGRAM_ACTIVATION_REQUIRED')
    return tuple('program-write:'+canonical_hash(dict(intent_id=intent_id,
        scope=domain_payload(compilation.scope),compilation_hash=digest(compilation),request_index=index))
        for index in range(len(compilation.requests)))


@dataclass(frozen=True,slots=True,kw_only=True)
class AdmissionAttachment:
    command_ids:tuple[str,...]
    request_hashes:tuple[str,...]
    activation_id:str|None

    def __post_init__(self):
        require(type(self.command_ids) is tuple and type(self.request_hashes) is tuple
            and len(self.command_ids)==len(set(self.command_ids))==len(self.request_hashes)
            and all(type(i) is str and i for i in self.command_ids),
            'PROGRAM_ADMISSION_ATTACHMENTS_REQUIRED')
        require(bool(self.command_ids)==(self.activation_id is None)
            and (self.activation_id is None or type(self.activation_id) is str and bool(self.activation_id)),
            'PROGRAM_ADMISSION_ATTACHMENT_MODE_REQUIRED')
        require(all(type(h) is str and len(h)==64 and all(c in '0123456789abcdef' for c in h)
            for h in self.request_hashes),'PROGRAM_ADMISSION_REQUEST_HASH_REQUIRED')


@dataclass(frozen=True,slots=True,kw_only=True)
class GenericProgramAdmissionReceipt:
    intent_id:str
    source_hash:str
    context_hash:str
    compilation_hash:str
    bundle_hash:str
    attachment:AdmissionAttachment

    @property
    def execution_authorized(self)->Literal[False]:return False


class GenericProgramAdmissionService:
    """Owner-host composition with local trusted readers; no production defaults.

    A receipt establishes committed admission only. Dispatch independently reloads
    current authority and consumes its own single-action permit. Any failed
    attachment or database commit propagates, leaving the restricted intake alone.
    """
    def __init__(self,ingress,reservation_service,*,load_inputs,load_clock=None,
                 load_qualification=None,load_action_proof=None,load_lease=None,load_wallet=None):
        require(ingress.scope==reservation_service.scope
            and ingress.scope.environment==t.Environment.PAPER,'PROGRAM_ADMISSION_SCOPE_REQUIRED')
        require(ingress.uow is not reservation_service.uow,'SEPARATE_INTAKE_UOW_REQUIRED')
        self.ingress,self.reservations=ingress,reservation_service
        self.scope=ingress.scope
        self.load_inputs,self.load_clock=load_inputs,load_clock
        self.load_qualification,self.load_action_proof=load_qualification,load_action_proof
        self.load_lease,self.load_wallet=load_lease,load_wallet

    def _clock(self,tx):
        clock=self.load_clock(tx) if self.load_clock else None
        require(type(clock) is t.ClockReading and clock.clock_health=='HEALTHY'
            and type(clock.monotonic_ns) is int and clock.monotonic_ns>=0,
            'PROGRAM_CURRENT_CLOCK_REQUIRED')
        return clock

    def admit(self,proposal,context,compilation):
        require(type(compilation) is ProgramCompilation and compilation.scope==self.scope
            and digest(compilation.original_proposal)==digest(proposal),
            'PROGRAM_ADMISSION_COMPILATION_MISMATCH')
        source,record=self.ingress.accept(proposal,context)
        ids=creation_command_ids(record.intent_id,compilation)
        key=intake_key(proposal,context)
        def operation(tx):
            current,stored=read_program_source(tx,key)
            require(digest(current)==digest(source) and digest(stored)==digest(record)
                and record.compilation_hash==digest(compilation),'PROGRAM_ADMISSION_SOURCE_CHANGED')
            previous=tx._latest('audit_events',ADMISSION_PREFIX+record.intent_id,GenericProgramAdmissionReceipt)
            if previous is not None:
                self._validate_replay(tx,previous,source,record,compilation,ids)
                return previous
            clock=self._clock(tx)
            fence=digest(current_fence(tx,context))
            book=digest(tx.projections.book(self.scope))
            bundle=self.reservations._reserve_program(tx,key,ids,load_inputs=self.load_inputs,
                load_qualification=self.load_qualification,load_action_proof=self.load_action_proof,
                load_clock=self.load_clock)
            require(type(bundle) is ProgramFinancialBundle and bundle.intent.intent_id==record.intent_id
                and digest(bundle.archive.compilation)==digest(compilation),
                'PROGRAM_ADMISSION_BUNDLE_CHANGED')
            attachment=self._attach_bundle(tx,bundle,ids)
            require(type(attachment) is AdmissionAttachment and attachment.command_ids==ids,
                'PROGRAM_ADMISSION_ATTACHMENTS_REQUIRED')
            attachment_until=self._verify_attachments(tx,bundle,attachment)
            # Attachment readers run inside the same financial transaction. Their
            # latency or a changed deployment/book must not renew admission.
            window=AdmissionWindow(started_at=utc_timestamp(clock.utc_now),started_ns=clock.monotonic_ns,
                wall_clock=self.reservations.clock,monotonic_clock=self.reservations.monotonic_clock)
            self._clock(tx)
            expires=min(map(utc_timestamp,(bundle.intent.valid_until,bundle.decision.expires_at,
                bundle.archive.risk_terms.submission_deadline,attachment_until)))
            require(window.poll(expires) is not None,'PROGRAM_ADMISSION_EXPIRED')
            require(digest(current_fence(tx,context))==fence,'STALE_REVISION')
            require(digest(tx.projections.book(self.scope))==book,'PROGRAM_CURRENT_BOOK_CHANGED')
            receipt=GenericProgramAdmissionReceipt(intent_id=record.intent_id,source_hash=digest(source),
                context_hash=digest(context),compilation_hash=digest(compilation),bundle_hash=digest(bundle),
                attachment=attachment)
            tx._append('audit_events',ADMISSION_PREFIX+record.intent_id,receipt,0)
            return receipt
        return self.reservations._transaction(operation) # return only after commit

    def _attach_bundle(self,tx,bundle,ids):
        if not ids:
            from trading_system.execution.local_program_activation import activate_local_program
            receipt=activate_local_program(tx,bundle,load_clock=self.load_clock)
            return AdmissionAttachment(command_ids=(),request_hashes=(),activation_id=receipt.activation_id)
        lease=self.load_lease(tx) if self.load_lease else None
        require(type(lease) is t.OwnerLease,'PROGRAM_EXECUTION_LEASE_REQUIRED')
        from trading_system.execution.creation_requests import assign_client_ids,materialize_creation
        from trading_system.execution.program_commands import ProgramCommandRepository,build_creation_write
        plan=bundle.archive.compilation
        repo=ProgramCommandRepository(tx,load_clock=self.load_clock,load_lease=self.load_lease)
        hashes=[]
        for index,command_id in enumerate(ids):
            clock=self._clock(tx)
            wallet=self.load_wallet(tx,bundle,index,clock) if self.load_wallet else None
            request=materialize_creation(plan,index,bundle.archive.profile,
                assign_client_ids(plan,index,command_id),wallet_selection=wallet,now=clock.utc_now)
            write,_=build_creation_write(request,bundle,execution_epoch=lease.shard.execution_epoch)
            repo.append_creation(write,request,bundle=bundle)
            hashes.append(digest(request))
        return AdmissionAttachment(command_ids=ids,request_hashes=tuple(hashes),activation_id=None)

    def _verify_attachments(self,tx,bundle,attachment,*,same_transaction=True):
        claim=tx._latest('audit_events',ATTACHMENT_PREFIX+bundle.intent.intent_id,ProgramFinancialAttachment)
        require(claim is not None,'PROGRAM_ADMISSION_ATTACHMENT_REQUIRED')
        witness=current_program_bundle_witness(tx,bundle) if same_transaction else None
        require(claim.intent_id==bundle.intent.intent_id and claim.bundle_hash==digest(bundle)
            and claim.command_ids==attachment.command_ids
            and claim.mode==('EXTERNAL_REQUESTS' if attachment.command_ids else 'LOCAL_ACTIVATION'),
            'PROGRAM_ADMISSION_ATTACHMENT_CHANGED')
        require(not same_transaction or claim.admission_transaction_id==tx.transaction_id,
            'PROGRAM_ADMISSION_ATTACHMENT_TRANSACTION_CHANGED')
        bundle_row=tx.execute('SELECT transaction_id,xmin::text FROM ledger.audit_events WHERE scope_id=%s '
            'AND record_id=%s AND sequence=1',(scope_id(tx.scope),BUNDLE_PREFIX+bundle.intent.intent_id)).fetchone()
        require(bundle_row is not None and bundle_row[0]==claim.admission_transaction_id,
            'PROGRAM_ADMISSION_ATTACHMENT_TRANSACTION_CHANGED')
        claim_row=tx.execute('SELECT transaction_id,xmin::text FROM ledger.audit_events WHERE scope_id=%s '
            'AND record_id=%s AND sequence=1',(scope_id(tx.scope),ATTACHMENT_PREFIX+bundle.intent.intent_id)).fetchone()
        require(claim_row==bundle_row,'PROGRAM_ADMISSION_ATTACHMENT_PHYSICAL_TRANSACTION_CHANGED')
        if attachment.command_ids:
            from trading_system.execution.program_commands import ProgramCommandRepository,validate_stored_program_binding
            repo=ProgramCommandRepository(tx)
            rows=tx.execute('SELECT record_id,transaction_id,xmin::text FROM ledger.order_writes_v2 '
                'WHERE scope_id=%s AND sequence=1 AND payload->>\'intent_id\'=%s',
                (scope_id(tx.scope),bundle.intent.intent_id)).fetchall()
            require(len(rows)==len(attachment.command_ids)
                and {row[0] for row in rows}==set(attachment.command_ids)
                and all(row[1]==claim.admission_transaction_id and row[2]==bundle_row[1] for row in rows),
                'PROGRAM_ADMISSION_WRITES_INCOMPLETE')
            for command_id,request_hash in zip(attachment.command_ids,attachment.request_hashes,strict=True):
                write,request=repo.write(command_id),repo.request(command_id)
                require(write is not None and request is not None and write.command_id==request.command_id==command_id
                    and write.scope==request.scope==tx.scope and write.intent_id==bundle.intent.intent_id
                    and write.request_hash==digest(request)==request_hash
                    and write.archive_hash==bundle.intent.hashes.archive
                    and (not same_transaction or write.state=='DURABLE_READY'),
                    'PROGRAM_ADMISSION_ATTACHMENT_CHANGED')
                validate_stored_program_binding(tx,replace(write,state='DURABLE_READY'),write)
        else:
            from trading_system.execution.local_program_activation import read_local_activation
            local=read_local_activation(tx,attachment.activation_id)
            require(local is not None and local.financial_intent_id==bundle.intent.intent_id
                and local.bundle_hash==digest(bundle),'PROGRAM_ADMISSION_ATTACHMENT_CHANGED')
        return None if witness is None else witness.expires_at

    def _validate_replay(self,tx,receipt,source,record,compilation,ids):
        require(receipt.intent_id==record.intent_id and receipt.source_hash==digest(source)
            and receipt.context_hash==digest(record.context) and receipt.compilation_hash==digest(compilation)
            and receipt.attachment.command_ids==ids,'PROGRAM_ADMISSION_REPLAY_CONFLICT')
        bundle=tx._latest('audit_events',BUNDLE_PREFIX+record.intent_id,ProgramFinancialBundle)
        require(bundle is not None and digest(bundle)==receipt.bundle_hash
            and tx.intents.get(record.intent_id)==bundle.intent,'PROGRAM_ADMISSION_BUNDLE_REQUIRED')
        self._verify_attachments(tx,bundle,receipt.attachment,same_transaction=False)
