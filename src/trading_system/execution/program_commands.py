"""Full program request records in the existing financial UoW and outbox.

No transport, signer, credential discovery or default qualification. A READY
record is metadata. Only the separately held verifier role can cross MAY and
consume its single use permit after independently reloading current facts.
"""
from dataclasses import dataclass,replace
from datetime import timedelta
import secrets
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,utc_timestamp
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.intents.program_assessment_archive import (financial_intent_from_assessment_archive,
    financial_risk_basis_from_assessment_archive)
from trading_system.risk.program_financial_admission import (ProgramFinancialBundle,ARCHIVE_PREFIX,BASIS_PREFIX,
    DECISION_PREFIX,BUNDLE_PREFIX,program_bundle_command_ids,claim_program_attachment,current_program_bundle_witness)
from trading_system.storage.models import scope_id,account_key,decode,encode,atomic_write,MissingEvidence
from trading_system.storage.order_programs import OrderProgramRepository
from .creation_requests import VenueCreationRequest,EquityWalletSelection,verify_creation
from trading_system.intents.program_compilation import Timing,Namespace

def digest(value):
    return canonical_hash({'items':[domain_payload(v) for v in value]}) if type(value) is tuple else canonical_hash(domain_payload(value))
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
    require(a.compilation.requests[request.request_index].timing==Timing.INITIAL,'PROGRAM_STAGED_REQUEST_NOT_ADMITTED')
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

def validate_stored_program_binding(tx,original,current):
    """Cross-scope obligation check: exclusion never uses a bare text ID."""
    key=scope_id(original.scope)
    row=tx.execute('SELECT payload FROM ledger.program_request_bindings WHERE scope_id=%s AND record_id=%s',
        (key,original.command_id)).fetchone()
    require(row is not None,'PROGRAM_REQUEST_BINDING_REQUIRED')
    binding=decode(ProgramRequestBinding,row[0])
    row=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s ORDER BY sequence DESC LIMIT 1',
        (key,BUNDLE_PREFIX+original.intent_id)).fetchone()
    require(row is not None,'PROGRAM_FINANCIAL_BUNDLE_REQUIRED')
    bundle=decode(ProgramFinancialBundle,row[0])
    validate_creation_binding(original,binding.request,binding,bundle)
    require(tx.execute('SELECT ledger.program_binding_current(%s,%s,%s::jsonb)',
        (key,original.command_id,encode(original))).fetchone()==(True,),'PROGRAM_STORED_BINDING_CHANGED')
    if current.state!='DURABLE_READY':
        row=tx.execute('SELECT write_payload FROM ledger.program_dispatch_marks WHERE scope_id=%s AND command_id=%s',
            (key,original.command_id)).fetchone()
        require(row is not None and row[0]==domain_payload(replace(original,state='MAY_HAVE_SENT')),
            'PROGRAM_MARK_PROOF_REQUIRED')
    return binding,bundle

class ProgramCommandRepository:
    def __init__(self,tx,*,load_clock=None,load_lease=None,load_current_facts=None,
                 load_qualification=None,load_action_proof=None,load_wallet_selection=None):
        self.tx=tx;self.load_clock=load_clock;self.load_lease=load_lease
        self.load_current_facts=load_current_facts;self.load_qualification=load_qualification
        self.load_action_proof=load_action_proof
        self.load_wallet_selection=load_wallet_selection

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
        validate_stored_program_binding(self.tx,writes[0],writes[-1])
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
        require(type(s) is t.ExecutionShard and type(lease.instance_id) is str and bool(lease.instance_id)
            and type(s.node_id) is str and bool(s.node_id) and type(s.execution_epoch) is int and s.execution_epoch>0 and
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
        initial_clock,_=self._clock_lease(write)
        witness=current_program_bundle_witness(self.tx,bundle)
        require(utc_timestamp(initial_clock.utc_now)<utc_timestamp(witness.expires_at),'PROGRAM_BUNDLE_WITNESS_EXPIRED')
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
        final_clock,_=self._clock_lease(write)
        require(final_clock.monotonic_ns>=initial_clock.monotonic_ns
            and utc_timestamp(initial_clock.utc_now)<=utc_timestamp(final_clock.utc_now)<utc_timestamp(witness.expires_at),
            'PROGRAM_BUNDLE_WITNESS_EXPIRED')
        return expected

    def _verify_current_request(self,request,archive,clock):
        equity_buy=request.namespace==Namespace.EQUITY and any(f.name=='side' and f.value=='BUY' for f in request.fields)
        selection=None
        if equity_buy:
            selection=self.load_wallet_selection(self.tx,archive,clock) if self.load_wallet_selection else None
            require(type(selection) is EquityWalletSelection,'PROGRAM_CURRENT_WALLET_REQUIRED')
        verify_creation(request,archive.compilation,archive.profile,request.assigned_ids,
            wallet_selection=selection,now=clock.utc_now)
        if equity_buy:
            # OP-E must bind the chosen CARD/MAIN location to independently
            # assessed available balances. Asset totals alone do not prove it.
            raise ValueError('PROGRAM_EQUITY_WALLET_FUNDING_LOCATION_UNBOUND')

    def _current(self,write,expected_lease):
        """Reload and replay actual compiler/target/exposure/RiskEvaluator facts.

        This initial boundary requires the intake book to remain current. New
        source/book/rule compilations require fresh admission, never an update
        to this immutable command. The trusted reader is a LOCAL server port.
        """
        from trading_system.intents.generic_program_intake import read_program_source,current_fence
        from trading_system.risk.program_financial_admission import validate_current_inputs,ProgramFinancialQualification
        from trading_system.accounts.authorization import valid_evidence
        from trading_system.risk.reservation_repository import ReservationRepository,merge_holds
        from trading_system.risk.target_guard import target_obligations
        from trading_system.risk.locked_actions import LockedActionVerifier
        from trading_system.risk.asset_capacity import CapacityInputs,check_capacity,amounts
        from trading_system.execution.commands import CommandRepository
        from trading_system.storage.application_writes import application_write_obligations
        clock,lease=self._clock_lease(write);now=utc_timestamp(clock.utc_now)
        require(type(expected_lease) is t.OwnerLease and digest(lease)==digest(expected_lease),'PROGRAM_LEASE_CHANGED')
        bundle=self._bundle(write.intent_id);source,intake=read_program_source(self.tx,bundle.intent.source.intake_record_id)
        from trading_system.risk.program_financial_admission import ProgramFinancialAttachment,ATTACHMENT_PREFIX
        attachment=self.tx._latest('audit_events',ATTACHMENT_PREFIX+write.intent_id,ProgramFinancialAttachment)
        require(attachment is not None and attachment.mode=='EXTERNAL_REQUESTS'
            and attachment.intent_id==write.intent_id and attachment.bundle_hash==digest(bundle),
            'PROGRAM_COMPLETE_ATTACHMENT_REQUIRED')
        physical=self.tx.execute('SELECT record_id,transaction_id,payload,xmin::text FROM ledger.order_writes_v2 '
            "WHERE scope_id=%s AND sequence=1 AND payload->>'intent_id'=%s",
            (scope_id(write.scope),write.intent_id)).fetchall()
        physical_attachment=self.tx.execute('SELECT xmin::text FROM ledger.audit_events WHERE scope_id=%s '
            'AND record_id=%s AND sequence=1',(scope_id(write.scope),ATTACHMENT_PREFIX+write.intent_id)).fetchone()
        require(len(physical)==len(attachment.command_ids) and {cid for cid,_,_,_ in physical}==set(attachment.command_ids)
            and all(tid==attachment.admission_transaction_id and xmin==physical_attachment[0]
                for _,tid,_,xmin in physical),'PROGRAM_COMPLETE_ATTACHMENT_REQUIRED')
        initial_indices=tuple(index for index,r in enumerate(bundle.archive.compilation.requests) if r.timing==Timing.INITIAL)
        require(len(attachment.command_ids)==len(initial_indices),'PROGRAM_REQUEST_ALLOCATION_MISMATCH')
        assigned=dict(zip(initial_indices,attachment.command_ids))
        for cid,_,payload,_ in physical:
            original=VersionedOrderCodec().decode('application-write',payload)
            require(type(original) is ApplicationWriteV3 and original.intent_id==write.intent_id
                and original.scope==write.scope and original.compilation_hash==write.compilation_hash,
                'PROGRAM_COMPLETE_ATTACHMENT_REQUIRED')
            require(assigned.get(original.request_index)==cid,'PROGRAM_REQUEST_ALLOCATION_MISMATCH')
            validate_stored_program_binding(self.tx,original,self.write(cid))
        require(digest(source)==digest(bundle.intent.source),'PROGRAM_SOURCE_CHANGED')
        fence=current_fence(self.tx,source.context)
        book=self.tx.projections.book(self.tx.scope)
        before=digest(book)
        inputs=self.load_current_facts(self.tx,source,intake,book,clock) if self.load_current_facts else None
        intent,basis=validate_current_inputs(self.tx,source,intake,book,inputs,clock)
        a=inputs.archive
        require(digest(a.compilation)==write.compilation_hash and digest(a.source)==digest(bundle.archive.source)
            and intent.hashes.program==write.canonical_program_hash,'PROGRAM_CURRENT_ECONOMICS_CHANGED')
        require((intent.owner_strategy_id,intent.ownership_epoch,intent.risk_policy_revision)==(
            fence.owner_strategy_id,fence.ownership_epoch,fence.risk_policy_revision),'PROGRAM_STALE_OWNER')
        qualification=self.load_qualification(self.tx,a,clock) if self.load_qualification else None
        require(a.capabilities.transport=='PAPER' and a.capabilities.qualification=='PAPER_VERIFIED'
            and type(qualification) is ProgramFinancialQualification and qualification.scope==self.tx.scope
            and qualification.compilation_hash==intent.hashes.compilation
            and qualification.capabilities_hash==intent.hashes.capabilities and qualification.rules_hash==intent.hashes.rules
            and qualification.profile_hash==intent.hashes.profile and qualification.adapter_build_digest==a.compilation.adapter_build_digest
            and qualification.execution_mode==a.compilation.original_program.execution_mode
            and qualification.evidence and digest(qualification.evidence)==digest(a.capabilities.evidence)
            and all(valid_evidence(e,self.tx.scope.environment,now) for e in qualification.evidence)
            and now<utc_timestamp(qualification.valid_until),'PROGRAM_QUALIFICATION_REQUIRED')
        reservations=ReservationRepository(self.tx)
        # Own exact financial attachment is excluded once; every unrelated
        # family/scope remains in capacity and unresolved obligation readers.
        own_ids={p.reservation.reservation_id for p in bundle.reservations}
        actual_own=tuple(reservations.get(p.reservation.reservation_id) for p in bundle.reservations)
        require(digest(actual_own)==digest(bundle.reservations),'PROGRAM_RESERVATION_CHANGED')
        holds=merge_holds(reservations.group(),book)
        others=tuple(p for p in holds if p.reservation.reservation_id not in own_ids)
        require(digest(others)==digest(a.budget_facts.reservations),'PROGRAM_CURRENT_GROUP_HOLDS_CHANGED')
        obligations=application_write_obligations(self.tx)
        exclusions=tuple(o.original for o in obligations if type(o.original) is ApplicationWriteV3
            and o.original.scope==write.scope and o.original.intent_id==write.intent_id
            and o.original.compilation_hash==write.compilation_hash)
        require(any(w.command_id==write.command_id for w in exclusions),'PROGRAM_ATTACHMENT_REQUIRED')
        require(not any(o.unresolved and not(o.original.scope==write.scope and o.original.command_id==write.command_id)
            for o in obligations),'PROGRAM_OTHER_COMMAND_UNRESOLVED')
        require(not CommandRepository(self.tx).unresolved_in_group('',program_exclusion=(replace(write,state='DURABLE_READY'),)),
            'PROGRAM_LEGACY_COMMAND_UNRESOLVED')
        local_ids={row[0] for row in self.tx.execute('SELECT record_id FROM ledger.reservations WHERE scope_id=%s',
            (scope_id(self.tx.scope),)).fetchall()}
        external_ids={p.reservation.reservation_id for p in book.reservations}-{p.reservation.reservation_id for p in reservations.group()}
        pending=target_obligations(self.tx,intent,book,tuple(p for p in others
            if p.reservation.reservation_id in local_ids|external_ids),clock.utc_now,program_exclusion=exclusions)
        require(digest(pending)==digest(a.target_facts.inputs.pending),'PROGRAM_CURRENT_QUANTITY_OBLIGATIONS_CHANGED')
        held=amounts(bundle.basis.incremental_requirements)
        require(all(m.amount<=held.get(m.currency,-1) for m in basis.incremental_requirements),'PROGRAM_RESERVED_BUDGET_EXCEEDED')
        capacity=check_capacity(CapacityInputs(incremental_requirements=basis.incremental_requirements,
            venue_free_balances=book.balances,remaining_group_capacity=inputs.group_capacity,
            remaining_strategy_capacity=inputs.strategy_capacity,buffers=inputs.buffer,reconciled_group_holds=others,
            owner_strategy_id=intent.owner_strategy_id))
        require(capacity.sufficient,'PROGRAM_CURRENT_CAPACITY_DENIED')
        duty=LockedActionVerifier().verify_program(self.tx,intent,a,clock.utc_now,book=book,read_proof=self.load_action_proof)
        require(duty is not None,'PROGRAM_ACTIVE_RISK_LOCK')
        request=self.request(write.command_id)
        self._verify_current_request(request,a,clock)
        require(before==digest(self.tx.projections.book(self.tx.scope)) and
            digest(current_fence(self.tx,source.context))==digest(fence),'PROGRAM_CURRENT_STATE_CHANGED')
        end_clock,end_lease=self._clock_lease(write)
        require(digest(end_lease)==digest(lease) and end_clock.monotonic_ns>=clock.monotonic_ns
            and utc_timestamp(end_clock.utc_now)>=now,'PROGRAM_CURRENT_CLOCK_CHANGED')
        expires=min(map(utc_timestamp,(write.valid_until,intent.valid_until,a.assessment.expires_at,
            a.risk_terms.submission_deadline,qualification.valid_until,inputs.authorization.authorization.expires_at,
            duty,lease.expires_at)))
        require(utc_timestamp(end_clock.utc_now)<expires,'PROGRAM_CURRENT_FACTS_EXPIRED')
        from trading_system.intents.ingress import revision_key
        context={'write':domain_payload(write),'request_hash':write.request_hash,'source_hash':digest(source),
            'assessment_hash':digest(a),'basis_hash':digest(basis),'qualification_hash':digest(qualification),
            'lease':domain_payload(lease),'clock':domain_payload(end_clock),'fence':domain_payload(fence),
            'fence_record_id':revision_key(source.context),'assessment':domain_payload(a),
            'qualification':domain_payload(qualification),'valid_until':expires.isoformat()}
        return context,end_clock,lease,expires

    @atomic_write
    def mark_may_have_sent(self,command_id,lease,*,token_hash):
        w=self.write(command_id)
        require(w is not None and w.state=='DURABLE_READY','PROGRAM_QUERY_ORIGINAL_IDS_NO_RETRY')
        context,clock,current,expires=self._current(w,lease)
        ttl=min(self.tx.permit_ttl_ms,int((expires-utc_timestamp(clock.utc_now)).total_seconds()*1000),
            (current.deadline_monotonic_ns-clock.monotonic_ns)//1_000_000)
        require(ttl>=1,'PROGRAM_PERMIT_WINDOW_EXPIRED')
        self.tx.execute('SELECT ledger.mark_program_may(%s,%s,%s::jsonb,%s,%s,%s)',
            (scope_id(w.scope),command_id,canonical_json(context),token_hash,ttl,self.tx.transaction_id))
        # This uncommitted candidate never escapes ProgramCommandBoundary.
        return w,clock,ttl

    @atomic_write
    def consume(self,permit,lease):
        require(type(permit) is ProgramWritePermit,'PROGRAM_PERMIT_REQUIRED')
        w=self.write(permit.command_id)
        require(w is not None and w.state=='MAY_HAVE_SENT' and w.scope==permit.scope
            and w.request_hash==permit.request_hash and w.binding_hash==permit.binding_hash,
            'PROGRAM_PERMIT_BINDING_CHANGED')
        context,clock,_,_=self._current(w,lease)
        require(utc_timestamp(clock.utc_now)<utc_timestamp(permit.expires_at)
            and clock.monotonic_ns<permit.deadline_monotonic_ns,'PROGRAM_PERMIT_EXPIRED')
        self.tx.execute('SELECT ledger.consume_program_permit(%s,%s,%s,%s::jsonb,%s)',
            (scope_id(w.scope),w.command_id,_token_hash(permit.token),canonical_json(context),self.tx.transaction_id))
        return self.request(w.command_id)

    @atomic_write
    def record_unknown(self,command_id):
        w=self.write(command_id)
        require(w is not None and w.state in ('MAY_HAVE_SENT','UNKNOWN'),'PROGRAM_DURABLE_MAY_REQUIRED')
        if w.state=='MAY_HAVE_SENT':self.tx._append('order_writes_v2',command_id,replace(w,state='UNKNOWN'),2)
        return self.request(command_id)

def _token_hash(token):return canonical_hash({'program_permit_token':token})

@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramWritePermit:
    command_id:str
    scope:t.ScopeKey
    request_hash:str
    binding_hash:str
    token:str
    expires_at:str
    deadline_monotonic_ns:int

class ProgramCommandBoundary:
    """Server-only verifier UoW; no runtime role creation or transport.

    A verifier connection must be explicitly provided by trusted composition.
    Both methods return only after synchronous commit; uncertain commits return
    nothing. No UNKNOWN replay mints or renews a permit.
    """
    def __init__(self,uow,scope,*,load_clock=None,load_lease=None,load_current_facts=None,
                 load_qualification=None,load_action_proof=None,load_wallet_selection=None):
        self.uow,self.scope=uow,scope
        self.readers=dict(load_clock=load_clock,load_lease=load_lease,load_current_facts=load_current_facts,
            load_qualification=load_qualification,load_action_proof=load_action_proof,load_wallet_selection=load_wallet_selection)

    def mark_may_have_sent(self,command_id,lease):
        token=secrets.token_urlsafe(32)
        with self.uow.transaction(self.scope) as tx:
            w,clock,ttl=ProgramCommandRepository(tx,**self.readers).mark_may_have_sent(command_id,lease,
                token_hash=_token_hash(token))
        return ProgramWritePermit(command_id=command_id,scope=w.scope,request_hash=w.request_hash,binding_hash=w.binding_hash,
            token=token,expires_at=(utc_timestamp(clock.utc_now)+timedelta(milliseconds=ttl)).isoformat(),
            deadline_monotonic_ns=clock.monotonic_ns+ttl*1_000_000)

    def consume(self,permit,lease):
        with self.uow.transaction(self.scope) as tx:
            request=ProgramCommandRepository(tx,**self.readers).consume(permit,lease)
        return request
