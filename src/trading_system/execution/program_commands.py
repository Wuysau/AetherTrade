"""Full program request records in the existing financial UoW and outbox.

No transport, signer, credential discovery or default qualification. A READY
record is metadata. Only the separately held verifier role can cross MAY and
consume its single use permit after independently reloading current facts.
"""
from dataclasses import dataclass,replace
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.intents.program_assessment_archive import (financial_intent_from_assessment_archive,
    financial_risk_basis_from_assessment_archive)
from trading_system.risk.program_financial_admission import (ProgramFinancialBundle,ARCHIVE_PREFIX,BASIS_PREFIX,
    DECISION_PREFIX,BUNDLE_PREFIX,program_bundle_command_ids,claim_program_attachment)
from trading_system.storage.models import scope_id,account_key,decode,encode,atomic_write,MissingEvidence
from trading_system.storage.order_programs import OrderProgramRepository
from .creation_requests import VenueCreationRequest,verify_creation

def digest(value):return canonical_hash(domain_payload(value))
def require(condition,reason):
    if not condition:raise ValueError(reason)

@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramRequestBinding:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['PROGRAM_REQUEST_BINDING']
    command_id:str
    intent_id:str
    scope:t.ScopeKey
    execution_epoch:int
    request_hash:str
    bundle_hash:str
    source_hash:str
    request:VenueCreationRequest

def _validate_bundle(bundle):
    require(type(bundle) is ProgramFinancialBundle,'PROGRAM_FINANCIAL_BUNDLE_REQUIRED')
    intent=financial_intent_from_assessment_archive(bundle.archive)
    require(digest(intent)==digest(bundle.intent),'PROGRAM_INTENT_ARCHIVE_CHANGED')
    basis=financial_risk_basis_from_assessment_archive(intent,bundle.archive)
    require(digest(basis)==digest(bundle.basis) and digest(bundle.decision)==digest(bundle.archive.assessment),
        'PROGRAM_BASIS_DECISION_ARCHIVE_CHANGED')

def build_creation_write(request,bundle,*,execution_epoch):
    _validate_bundle(bundle)
    require(type(request) is VenueCreationRequest,'PROGRAM_TYPED_CREATION_REQUIRED')
    a=bundle.archive;i=bundle.intent
    verify_creation(request,a.compilation,a.profile,request.assigned_ids,
        wallet_selection=request.wallet_selection,now=i.created_at)
    binding=ProgramRequestBinding(contract_version='2026-10-29',record_kind='PROGRAM_REQUEST_BINDING',
        command_id=request.command_id,intent_id=i.intent_id,scope=i.scope,execution_epoch=execution_epoch,
        request_hash=digest(request),bundle_hash=digest(bundle),source_hash=digest(i.source),request=request)
    ids=request.assigned_ids
    client=ids.parent_client_id or (ids.members[0].client_id if len(ids.members)==1 else None)
    write=ApplicationWriteV3(contract_version='2026-10-29',record_kind='PROGRAM_APPLICATION_WRITE',
        command_id=request.command_id,intent_id=i.intent_id,program_id=i.proposal.program.program_id,
        scope=i.scope,origin='APPLICATION_ACTION',operation='CREATE_REQUEST',service_namespace=request.namespace.value,
        parent_command_id=None,venue_client_id=client,owner_strategy_id=i.owner_strategy_id,
        ownership_epoch=i.ownership_epoch,execution_epoch=execution_epoch,policy_revision=i.risk_policy_revision,
        request_index=request.request_index,canonical_program_hash=i.hashes.program,compilation_hash=i.hashes.compilation,
        allocation_hash=i.hashes.allocation,intent_hash=digest(i),basis_hash=digest(bundle.basis),
        decision_hash=digest(bundle.decision),archive_hash=digest(a),request_hash=digest(request),binding_hash=digest(binding),
        state='DURABLE_READY',durable_record_ref='program-command:'+request.command_id,
        created_at=i.created_at,valid_until=i.valid_until)
    VersionedOrderCodec().encode('application-write',write)
    return write,binding

def validate_creation_binding(write,request,binding,bundle):
    expected,actual=build_creation_write(request,bundle,execution_epoch=binding.execution_epoch)
    require(type(binding) is ProgramRequestBinding and digest(actual)==digest(binding),
        'PROGRAM_REQUEST_BINDING_CHANGED')
    require(type(write) is ApplicationWriteV3 and digest(replace(write,state='DURABLE_READY'))==digest(expected),
        'PROGRAM_WRITE_BINDING_CHANGED')
    return actual

class ProgramCommandRepository:
    def __init__(self,tx,*,load_clock=None,load_lease=None,load_current_facts=None,
                 load_qualification=None,load_action_proof=None):
        self.tx=tx;self.load_clock=load_clock;self.load_lease=load_lease
        self.load_current_facts=load_current_facts;self.load_qualification=load_qualification
        self.load_action_proof=load_action_proof

    def _raw_binding(self,command_id):
        return self.tx._latest('program_request_bindings',command_id,ProgramRequestBinding)

    def _bundle(self,intent_id):
        bundle=self.tx._latest('audit_events',BUNDLE_PREFIX+intent_id,ProgramFinancialBundle)
        require(bundle is not None,'PROGRAM_FINANCIAL_BUNDLE_REQUIRED')
        _validate_bundle(bundle)
        require(digest(self.tx.intents.get(intent_id))==digest(bundle.intent),'PROGRAM_STORED_INTENT_CHANGED')
        for prefix,record in ((ARCHIVE_PREFIX,bundle.archive),(BASIS_PREFIX,bundle.basis),(DECISION_PREFIX,bundle.decision)):
            require(digest(self.tx._latest('audit_events',prefix+intent_id,type(record)))==digest(record),
                'PROGRAM_STORED_FINANCIAL_FACT_CHANGED')
        return bundle

    def write(self,command_id):
        rows=self.tx.execute('SELECT sequence,payload FROM ledger.order_writes_v2 WHERE scope_id=%s '
            'AND record_id=%s ORDER BY sequence',(scope_id(self.tx.scope),command_id)).fetchall()
        if not rows:return None
        writes=tuple(VersionedOrderCodec().decode('application-write',p) for _,p in rows)
        require(all(type(w) is ApplicationWriteV3 for w in writes),'PROGRAM_V3_WRITE_REQUIRED')
        require(tuple(s for s,_ in rows)==tuple(range(1,len(rows)+1)) and writes[0].state=='DURABLE_READY',
            'PROGRAM_WRITE_HISTORY_INVALID')
        binding=self._raw_binding(command_id)
        require(binding is not None,'PROGRAM_REQUEST_BINDING_REQUIRED')
        bundle=self._bundle(writes[0].intent_id)
        validate_creation_binding(writes[0],binding.request,binding,bundle)
        for index,current in enumerate(writes[1:],1):
            require(digest(replace(current,state='DURABLE_READY'))==digest(writes[0]) and
                ((index==1 and current.state=='MAY_HAVE_SENT') or (index==2 and current.state=='UNKNOWN')),
                'PROGRAM_WRITE_HISTORY_INVALID')
        if len(rows)>1:
            marked=self.tx.execute('SELECT write_payload FROM ledger.program_dispatch_marks WHERE scope_id=%s '
                'AND command_id=%s',(scope_id(self.tx.scope),command_id)).fetchone()
            require(marked is not None and marked[0]==domain_payload(writes[1]),'PROGRAM_MARK_PROOF_REQUIRED')
        return writes[-1]

    def binding(self,command_id):
        return None if self.write(command_id) is None else self._raw_binding(command_id)

    def request(self,command_id):
        b=self.binding(command_id)
        return None if b is None else b.request

    def _clock_lease(self,write):
        clock=self.load_clock(self.tx) if self.load_clock else None
        lease=self.load_lease(self.tx) if self.load_lease else None
        require(type(clock) is t.ClockReading and clock.clock_health=='HEALTHY' and
            type(clock.monotonic_ns) is int and clock.monotonic_ns>=0,'PROGRAM_CURRENT_CLOCK_REQUIRED')
        require(type(lease) is t.OwnerLease,'PROGRAM_CURRENT_LEASE_REQUIRED')
        s=lease.shard;now=utc_timestamp(clock.utc_now)
        require(bool(lease.instance_id) and bool(s.node_id) and
            (s.environment,s.venue_account_id,s.product,s.execution_epoch)==(
                write.scope.environment,write.scope.venue_account_id,write.scope.product,write.execution_epoch)
            and now<utc_timestamp(lease.expires_at) and type(lease.deadline_monotonic_ns) is int
            and clock.monotonic_ns<lease.deadline_monotonic_ns,'PROGRAM_CURRENT_LEASE_REQUIRED')
        owner=self.tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
            'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR SHARE',
            (*account_key(write.scope),write.scope.instrument_uid,write.scope.venue_leg)).fetchone()
        require(owner==(write.owner_strategy_id,write.ownership_epoch,write.policy_revision),'PROGRAM_STALE_OWNER')
        require(utc_timestamp(write.created_at)<=now<utc_timestamp(write.valid_until),'PROGRAM_WRITE_EXPIRED')
        return clock,lease

    @atomic_write
    def append_creation(self,write,request,*,bundle):
        self.tx.require_scope(write.scope)
        require(self.tx.active and write.state=='DURABLE_READY','PROGRAM_FINANCIAL_TRANSACTION_REQUIRED')
        self._clock_lease(write)
        whole=program_bundle_command_ids(self.tx,bundle)
        require(write.command_id in whole,'PROGRAM_UNASSIGNED_COMMAND')
        claim_program_attachment(self.tx,bundle,mode='EXTERNAL_REQUESTS',command_ids=whole)
        expected,binding=build_creation_write(request,bundle,execution_epoch=write.execution_epoch)
        validate_creation_binding(write,request,binding,bundle)
        require(digest(self._bundle(write.intent_id))==digest(bundle),'PROGRAM_BUNDLE_CHANGED')
        require(self.write(write.command_id) is None,'PROGRAM_COMMAND_EXISTS_QUERY_ORIGINAL')
        OrderProgramRepository(self.tx).save_program(bundle.intent.proposal.program)
        self.tx._append('program_request_bindings',write.command_id,binding,0)
        ids=request.assigned_ids
        identities=tuple(m.client_id for m in ids.members)+((ids.parent_client_id,) if ids.parent_client_id else ())
        for client_id in identities:
            self.tx.execute('INSERT INTO ledger.program_client_identities '
                '(environment,account_id,product,service_namespace,client_id,scope_id,command_id) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s)',(*account_key(write.scope),write.service_namespace,
                    client_id,scope_id(write.scope),write.command_id))
        self.tx._append('order_writes_v2',write.command_id,expected,0)
        self.tx.execute('INSERT INTO ledger.outbox(message_id,scope_id,aggregate_id,sequence,kind,transaction_id) '
            'VALUES(%s,%s,%s,1,%s,%s)',('program-ready:'+write.command_id,scope_id(write.scope),write.command_id,
                'PROGRAM_COMMAND_READY',self.tx.transaction_id))
        return expected
