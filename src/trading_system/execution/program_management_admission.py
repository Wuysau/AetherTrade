"""Current authorization of an existing order, separate from its creation TTL.

LOCAL readers are unconfigured by default. Venue IO is captured before the
group-locked business transaction; no DTO or archived plan grants permission.
"""
from dataclasses import dataclass,replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from trading_system.accounts.authorization import AuthorizationBinding,AuthorizationService
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload,utc_timestamp
from trading_system.contracts.management_write_v4 import ManagementWriteV4
from trading_system.contracts.order_lifecycle import ApplicationWrite
from trading_system.contracts.order_snapshots import OrderRuleSnapshot,OrderCapabilitySnapshot
from trading_system.execution.management_requests import materialize_management
from trading_system.intents.product_rules import TrustedProductRules
from trading_system.intents.order_management_compilation import (ManagementInstruction,ManagementKind,compile_order_management)
from trading_system.intents.generic_program_intake import current_fence
from trading_system.intents.ingress import revision_key
from trading_system.recovery.original_creation import read_original_creation
from trading_system.risk.program_financial_admission import read_program_bundle
from trading_system.risk.reservation_repository import ReservationRepository,merge_holds
from trading_system.risk.asset_capacity import CapacityInputs,check_capacity
from trading_system.risk.locks import ACTIONS
from .program_management_commands import ManagementBinding,ManagementRepository,ManagementBoundary,binding_hash,digest,require,payload

@dataclass(frozen=True,slots=True,kw_only=True)
class ManagementInputs:
    rules:OrderRuleSnapshot
    capabilities:OrderCapabilitySnapshot
    profile:TrustedProductRules
    authorization:AuthorizationBinding
    group_capacity:tuple[t.Money,...]
    strategy_capacity:tuple[t.Money,...]
    buffer:tuple[t.Money,...]

class ProgramManagementService:
    def __init__(self,reservation_service,verifier_uow,*,target_reader=None,load_clock=None,load_lease=None,
                 load_inputs=None,load_authorization=None):
        self.reservations=reservation_service;self.scope=reservation_service.scope;self.verifier_uow=verifier_uow
        self.target_reader=target_reader;self.load_clock=load_clock;self.load_lease=load_lease;self.load_inputs=load_inputs
        self.boundary=ManagementBoundary(self)

    def abort_unsent(self,command_id,lease):
        from trading_system.storage.models import scope_id,decode
        from .program_management_commands import ManagementUnsentAbort
        with self.verifier_uow.transaction(self.scope) as tx:
            b=ManagementRepository(tx).read(command_id)
            require(b is not None and b.write.state=='DURABLE_READY','MANAGEMENT_PROVABLY_UNSENT_REQUIRED')
            current_fence(tx,b.source_context)
            clock=self.load_clock(tx) if self.load_clock else None
            actual=self.load_lease(tx) if self.load_lease else None
            require(type(clock) is t.ClockReading and clock.clock_health=='HEALTHY' and type(actual) is t.OwnerLease
                and actual==lease and actual.shard.environment==self.scope.environment
                and actual.shard.venue_account_id==self.scope.venue_account_id and actual.shard.product==self.scope.product
                and utc_timestamp(clock.utc_now)<utc_timestamp(actual.expires_at)
                and clock.monotonic_ns<actual.deadline_monotonic_ns,'MANAGEMENT_CURRENT_LEASE_REQUIRED')
            row=tx.execute('SELECT ledger.abort_management_unsent(%s,%s,%s)',
                (scope_id(self.scope),command_id,tx.transaction_id)).fetchone()
            return decode(ManagementUnsentAbort,row[0])

    def _capture(self,original,execution_epoch):
        from .program_management_paper import PaperManagementTargetReader
        require(type(self.target_reader) is PaperManagementTargetReader,'MANAGEMENT_TRUSTED_TARGET_READER_REQUIRED')
        return self.target_reader.capture(original,execution_epoch=execution_epoch)

    def capture_for(self,command_id):
        # This transaction closes BEFORE independent venue source IO.
        with self.verifier_uow.transaction(self.scope) as tx:
            binding=ManagementRepository(tx).read(command_id)
            require(binding is not None,'MANAGEMENT_COMMAND_REQUIRED')
            original=read_original_creation(tx,binding.write.original_command_id)
        return self._capture(original,binding.write.execution_epoch)

    def _facts(self,tx,original,target,lease,*,valid_until):
        require(tx.active and tx.scope==self.scope and self.scope.environment==t.Environment.PAPER,'MANAGEMENT_PAPER_ONLY')
        w=original.initial
        require(original.current_state in ('MAY_HAVE_SENT','UNKNOWN'),'MANAGEMENT_ORIGINAL_MAY_REQUIRED')
        clock=self.load_clock(tx) if self.load_clock else None
        current_lease=self.load_lease(tx) if self.load_lease else None
        require(type(clock) is t.ClockReading and clock.clock_health=='HEALTHY' and type(clock.monotonic_ns) is int
            and clock.monotonic_ns>=0,'MANAGEMENT_CURRENT_CLOCK_REQUIRED')
        now=utc_timestamp(clock.utc_now)
        require(type(lease) is t.OwnerLease and current_lease==lease and lease.shard.environment==self.scope.environment
            and lease.shard.venue_account_id==self.scope.venue_account_id and lease.shard.product==self.scope.product
            and lease.shard.execution_epoch==target.execution_epoch and bool(lease.instance_id and lease.shard.node_id)
            and now<utc_timestamp(lease.expires_at) and clock.monotonic_ns<lease.deadline_monotonic_ns,
            'MANAGEMENT_CURRENT_LEASE_REQUIRED')
        bundle=read_program_bundle(tx,w.intent_id)
        require(bundle is not None and digest(bundle.intent)==w.intent_hash,'MANAGEMENT_ORIGINAL_FINANCIAL_REQUIRED')
        require(bundle.archive.allocation.delta.classification=='INCREASE' and target.order is not None
            and target.order.reduce_only is False and target.order.trigger.kind=='NONE'
            and bundle.archive.compilation.original_program.template=='ATOMIC',
            'MANAGEMENT_PROTECTION_ACTION_PROOF_REQUIRED')
        context=bundle.intent.source.context;fence=current_fence(tx,context)
        require((fence.owner_strategy_id,fence.ownership_epoch,fence.risk_policy_revision)==(
            w.owner_strategy_id,w.ownership_epoch,w.policy_revision),'MANAGEMENT_CURRENT_OWNER_REQUIRED')
        require(target.scope==self.scope and target.program_id==w.program_id and target.program_hash==w.canonical_program_hash
            and target.namespace.value==w.service_namespace and (target.owner_strategy_id,target.ownership_epoch,
            target.execution_epoch,target.policy_revision)==(w.owner_strategy_id,w.ownership_epoch,lease.shard.execution_epoch,w.policy_revision),
            'MANAGEMENT_TARGET_IDENTITY_CHANGED')
        book=tx.projections.book(self.scope)
        from trading_system.risk.program_cash_guard import require_current_account_book
        require_current_account_book(tx,book)
        inputs=self.load_inputs(tx,original,target,book,clock) if self.load_inputs else None
        require(type(inputs) is ManagementInputs,'MANAGEMENT_CURRENT_INPUTS_REQUIRED')
        auth=AuthorizationService(lambda _:inputs.authorization,clock=lambda:now).validate(context)
        require(auth.allowed is True and inputs.authorization.authorization.owner_strategy_id==w.owner_strategy_id,
            'MANAGEMENT_CURRENT_AUTHORIZATION_REQUIRED')
        require(inputs.capabilities.transport=='PAPER' and inputs.capabilities.qualification=='PAPER_VERIFIED'
            and inputs.capabilities.evidence,'MANAGEMENT_PAPER_QUALIFICATION_REQUIRED')
        # Same cause permission core, with conservative protection treatment:
        # a cancellation cannot assume it is safe to remove a protective order.
        locks=tuple(tx.risk_locks.applicable(self.scope))+tuple(lock for p in book.positions for lock in p.risk_locks)
        require(all('MANAGE' in ACTIONS.get(lock.permission,frozenset()) for lock in locks),'MANAGEMENT_LOCKED_ACTION_DENIED')
        require(not locks,'MANAGEMENT_LOCKED_ACTION_PROOF_UNCONFIGURED')
        require(book.coverage.query_succeeded is True and book.coverage.pagination_complete is True
            and book.coverage.supported is True and not book.coverage.missing,'MANAGEMENT_CURRENT_BOOK_REQUIRED')
        from trading_system.recovery.reconciler import valid_ref
        require(book.evidence and all(valid_ref(ref,self.scope,clock.utc_now,60) for ref in book.evidence),
            'MANAGEMENT_CURRENT_BOOK_EVIDENCE_REQUIRED')
        repo=ReservationRepository(tx)
        own=tuple(repo.get(p.reservation.reservation_id) for p in bundle.reservations)
        require(own and all(p.held>=prior.held for p,prior in zip(own,bundle.reservations)),
            'MANAGEMENT_ORIGINAL_FUNDS_MUST_REMAIN_HELD')
        holds=merge_holds(repo.group(),book)
        zeros=tuple(t.Money(currency=p.reservation.asset,amount=Decimal(0)) for p in own)
        zeros=tuple({m.currency:m for m in zeros}.values())
        capacity=check_capacity(CapacityInputs(incremental_requirements=zeros,venue_free_balances=book.balances,
            remaining_group_capacity=inputs.group_capacity,remaining_strategy_capacity=inputs.strategy_capacity,
            buffers=inputs.buffer,reconciled_group_holds=holds,owner_strategy_id=w.owner_strategy_id))
        require(capacity.sufficient,'MANAGEMENT_CURRENT_CAPACITY_DENIED')
        expires=min(map(utc_timestamp,(valid_until,lease.expires_at,inputs.authorization.authorization.expires_at,
            target.valid_until,inputs.rules.valid_until,inputs.capabilities.valid_until,inputs.profile.valid_until)))
        require(now<expires,'MANAGEMENT_WRITE_EXPIRED')
        risk={'original_basis_hash':w.basis_hash,'target':domain_payload(target),'locks':payload(locks),
            'authorization':domain_payload(auth),'capabilities_hash':digest(inputs.capabilities)}
        funding={'group_id':tx.group_id,'holds':payload(holds),'book_hash':digest(book),
            'group_capacity':payload(inputs.group_capacity),'strategy_capacity':payload(inputs.strategy_capacity),
            'buffer':payload(inputs.buffer)}
        return bundle,inputs,clock,expires,risk,funding,fence

    def admit(self,original_command_id,instruction,lease,*,command_id=None,valid_until=None):
        require(type(instruction) is ManagementInstruction and instruction.kind!=ManagementKind.QUERY,'MANAGEMENT_QUERY_READ_ONLY')
        with self.verifier_uow.transaction(self.scope) as tx:
            original=read_original_creation(tx,original_command_id)
            current_lease=self.load_lease(tx) if self.load_lease else None
        require(original is not None,'MANAGEMENT_ORIGINAL_REQUIRED')
        require(type(current_lease) is t.OwnerLease and current_lease==lease,'MANAGEMENT_CURRENT_LEASE_REQUIRED')
        observation=self._capture(original,current_lease.shard.execution_epoch);target=observation.target
        with self.verifier_uow.transaction(self.scope) as tx:
            again=read_original_creation(tx,original_command_id)
            require(digest(again)==digest(original),'MANAGEMENT_ORIGINAL_CHANGED')
            clock=self.load_clock(tx) if self.load_clock else None
            require(type(clock) is t.ClockReading,'MANAGEMENT_CURRENT_CLOCK_REQUIRED')
            deadline=valid_until or (utc_timestamp(clock.utc_now)+timedelta(seconds=30)).isoformat()
            bundle,inputs,clock,expires,risk,funding,fence=self._facts(tx,original,target,lease,valid_until=deadline)
            # Incremental/overlap financial admission is an independent remaining
            # integration. No replacement or modified quantity can borrow holds.
            require(instruction.kind==ManagementKind.CANCEL,'MANAGEMENT_MODIFICATION_FINANCIAL_ADMISSION_REQUIRED')
            cid=command_id or 'management:'+uuid4().hex
            service='ORDER_LIST' if 'ORDER_LIST' in target.namespace.value else 'CONDITIONAL_ALGO' if 'CONDITIONAL' in target.namespace.value else 'EXECUTION_ALGO' if 'EXECUTION' in target.namespace.value else 'ORDINARY'
            metadata=ApplicationWrite(contract_version='2026-10-28',command_id=cid,program_id=target.program_id,scope=self.scope,
                origin='APPLICATION_ACTION',operation='CANCEL' if service=='ORDINARY' else 'CANCEL_PROGRAM',parent_command_id=None,
                target_venue_id=target.venue_id,canonical_program_hash=target.program_hash,ownership_epoch=target.ownership_epoch,
                execution_epoch=target.execution_epoch,policy_revision=target.policy_revision,owner_strategy_id=target.owner_strategy_id,
                venue_client_id='management-client:'+uuid4().hex,service_namespace=service,durable_record_ref='management:'+cid,
                state='DURABLE_READY',created_at=clock.utc_now)
            kwargs=self._compiler_kwargs(target,inputs,clock)
            plan=compile_order_management(instruction,target,command=metadata,**kwargs)
            request=materialize_management(plan,current_target=target,**kwargs)[0]
            w=ManagementWriteV4(contract_version='2026-10-30.management-v4',record_kind='PROGRAM_MANAGEMENT_WRITE_V4',
                command_id=cid,original_command_id=original_command_id,scope=self.scope,program_id=target.program_id,
                service_namespace=target.namespace.value,original_client_id=target.client_id,target_venue_id=target.venue_id,
                operation=instruction.kind.value,owner_strategy_id=target.owner_strategy_id,ownership_epoch=target.ownership_epoch,
                execution_epoch=target.execution_epoch,policy_revision=target.policy_revision,plan_hash=digest(plan),target_hash=digest(target),
                request_hash=digest(request),risk_hash=digest(risk),funding_hash=digest(funding),binding_hash='0'*64,
                state='DURABLE_READY',created_at=clock.utc_now,valid_until=expires.isoformat())
            binding=ManagementBinding(write=w,plan=plan,request=request,source_context=bundle.intent.source.context,
                original_write_hash=digest(original.initial),book_hash=funding['book_hash'],reservation_hash=digest(funding['holds']),
                risk_hash=digest(risk),funding_hash=digest(funding),target_source_json=observation.source_json,
                target_source_hash=observation.source_hash)
            binding=replace(binding,write=replace(w,binding_hash=binding_hash(binding)))
            ctx=self._context(binding,clock,lease,expires,risk,funding,fence)
            return ManagementRepository(tx).append(binding,ctx)

    def _compiler_kwargs(self,target,inputs,clock):
        return dict(scope=self.scope,now=clock.utc_now,expected_owner_strategy_id=target.owner_strategy_id,
            expected_ownership_epoch=target.ownership_epoch,expected_execution_epoch=target.execution_epoch,
            expected_policy_revision=target.policy_revision,rules=inputs.rules,capabilities=inputs.capabilities,
            profile=inputs.profile,expected_build_digest=inputs.profile.adapter_build_digest,
            expected_rule_revision=inputs.rules.revision,expected_capabilities_hash=digest(inputs.capabilities))

    def _context(self,binding,clock,lease,expires,risk,funding,fence):
        return dict(write=domain_payload(binding.write),clock=domain_payload(clock),lease=domain_payload(lease),
            fence=domain_payload(fence),fence_record_id=revision_key(binding.source_context),valid_until=expires.isoformat(),
            risk=risk,funding=funding,request_hash=binding.write.request_hash)

    def current(self,tx,binding,target,lease):
        from .program_management_paper import PaperManagementTargetObservation
        require(type(target) is PaperManagementTargetObservation,'MANAGEMENT_TRUSTED_TARGET_OBSERVATION_REQUIRED')
        target=target.target
        w=binding.write;original=read_original_creation(tx,w.original_command_id)
        require(original is not None and digest(original.initial)==binding.original_write_hash,'MANAGEMENT_ORIGINAL_CHANGED')
        # Repeated trusted reads may have later observation timestamps, but must
        # describe the exact same scoped source bytes and economic facts.
        previous=binding.plan.target
        require((target.evidence.id,target.evidence.hash,target.evidence.environment,target.evidence.source)==(
            previous.evidence.id,previous.evidence.hash,previous.evidence.environment,previous.evidence.source),
            'MANAGEMENT_CURRENT_TARGET_CHANGED')
        target=replace(target,evidence=previous.evidence,as_of=previous.as_of,valid_until=previous.valid_until)
        bundle,inputs,clock,expires,risk,funding,fence=self._facts(tx,original,target,lease,valid_until=w.valid_until)
        require(digest(target)==w.target_hash,'MANAGEMENT_CURRENT_TARGET_CHANGED')
        requests=materialize_management(binding.plan,current_target=target,**self._compiler_kwargs(target,inputs,clock))
        require(len(requests)==1 and digest(requests[0])==w.request_hash,'MANAGEMENT_REQUEST_CHANGED')
        require(digest(risk)==w.risk_hash and digest(funding)==w.funding_hash,'MANAGEMENT_CURRENT_RISK_FUNDING_CHANGED')
        return self._context(binding,clock,lease,expires,risk,funding,fence),clock,expires
