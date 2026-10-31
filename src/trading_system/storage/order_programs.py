"""Append-only OP-B records under the existing collateral transaction/owner.

There is no outbound transport, matcher, balance mutation, or automatic reserve
release here. Trusted parent/ingress verifiers are local composition dependencies;
defaults deny. OP-C must supply compilation/risk inputs, never model callbacks.
"""
from dataclasses import dataclass, replace
from datetime import datetime,timezone,timedelta
from decimal import Decimal,localcontext,Inexact,Rounded
from time import monotonic_ns
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,utc_timestamp
from trading_system.contracts.order_lifecycle import (ParentAuthority,ChildObservation,ProgramState,
    ObservationEvidence,ApplicationWrite,ParentObservation,IngestionResult,apply_child_observation,restrict,TERMINAL,apply_parent_observation)
from trading_system.contracts.wire import TradeIntent,ContractCodec
from trading_system.risk.reservation_repository import ReservationRepository
from trading_system.contracts.order_codec import VersionedOrderCodec
from .models import scope_id,account_key,decode,atomic_write,MissingEvidence,SequenceConflict

@dataclass(frozen=True,kw_only=True)
class ChildLink:
    program_id:str
    parent_venue_id:str
    child_order_id:str
    namespace:str
    evidence_id:str

@dataclass(frozen=True,kw_only=True)
class ReconciliationCase:
    program_id:str
    reason:str
    observation:ChildObservation|ParentObservation|None
    evidence:t.EvidenceRef|None
    original_parent_id:str
    recorded_at:str

@dataclass(frozen=True,kw_only=True)
class FundingLink:
    program_id:str
    reservation_id:str
    intent_id:str

@dataclass(frozen=True,kw_only=True)
class ObservationDisposition:
    program_id:str
    event_key:str
    restriction:str|None


def _validate_program_budget(payload):
    # Keep the pre-existing v2 storage/codec size bound. Explicit version
    # dispatch must not silently widen old safety constraints to the new codec's
    # one-MiB envelope. V3 retains the bound enforced by its versioned codec.
    if payload.get('contract_version')=='2026-10-28' and len(canonical_json(payload).encode())>131072:
        raise ValueError('Program budget exceeded')

class OrderProgramRepository:
    def __init__(self,tx,*,verify_parent=None,verify_ingress=None,clock=None):
        self.tx=tx
        self.verify_parent=verify_parent or (lambda authority:False)
        self.verify_ingress=verify_ingress or (lambda event,evidence:False)
        self.clock=clock or (lambda:datetime.now(timezone.utc).isoformat())
        if tx.scope.environment not in (t.Environment.PAPER,t.Environment.RESEARCH):raise ValueError('ENVIRONMENT_DISABLED')

    def program(self,program_id):
        row=self.tx.execute('SELECT payload FROM ledger.order_programs WHERE scope_id=%s AND record_id=%s',(scope_id(self.tx.scope),program_id)).fetchone()
        if row is None:return None
        program=VersionedOrderCodec().decode('order-program',row[0])
        _validate_program_budget(row[0])
        return program

    def binding(self,program_id):return self.tx._latest('order_program_bindings',program_id,ParentAuthority)
    def state(self,program_id):return self.tx._latest('order_program_states',program_id,ProgramState)

    def _lifecycle_version_error(self,program_id):
        program=self.program(program_id)
        if program is None:return 'PARENT_PROGRAM_MISSING'
        if program.contract_version!='2026-10-28':return 'PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED'
        return None

    @atomic_write
    def save_program(self,program):
        _validate_program_budget(VersionedOrderCodec().encode('order-program',program))
        previous=self.program(program.program_id)
        if previous is not None:
            if previous!=program:raise ValueError('Program identity reused with different content')
            return previous
        self.tx._append('order_programs',program.program_id,program,0)
        return program

    def _owner(self):
        return self.tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
            'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR SHARE',
            (*account_key(self.tx.scope),self.tx.scope.instrument_uid,self.tx.scope.venue_leg)).fetchone()

    def _funding(self,authority):
        with localcontext() as arithmetic:
            arithmetic.prec=256
            arithmetic.traps[Inexact]=True
            arithmetic.traps[Rounded]=True
            return self._funding_exact(authority)

    def _funding_exact(self,authority):
        owner=self._owner()
        if owner is None or owner[:2]!=(authority.owner_strategy_id,authority.ownership_epoch):return False
        intent=self.tx._latest('intents',authority.funding_intent_id,TradeIntent)
        if (intent is None or intent.owner_strategy_id!=authority.owner_strategy_id
            or intent.ownership_epoch!=authority.ownership_epoch or owner[2]!=intent.risk_policy_revision or intent.authorization_id!=authority.authorization_id):return False
        assets={}
        for identifier in authority.reservation_ids:
            p=ReservationRepository(self.tx).get(identifier)
            r=p.reservation
            if r.intent_id!=authority.funding_intent_id or r.owner_strategy_id!=authority.owner_strategy_id or r.collateral_group_id!=self.tx.group_id:return False
            if not all(v.is_finite() for v in (p.held,p.reflected,p.release_pending)) or not (0<=p.reflected<=p.held and 0<=p.release_pending<=p.held):return False
            # reflected and release_pending are subsets of held, not extra money.
            assets[r.asset]=assets.get(r.asset,Decimal(0))+p.held
        return all(assets.get(f.asset,Decimal(0))>=Decimal(f.amount) for f in authority.funding_requirements)

    def write(self,command_id):return self.tx._latest('order_writes_v2',command_id,ApplicationWrite)

    @atomic_write
    def create_write(self,command):
        VersionedOrderCodec().encode('application-write',command)
        self.tx.require_scope(command.scope)
        if command.origin not in ('APPLICATION_ACTION','APPLICATION_GENERATED_CHILD'):raise ValueError('Observation is not a write')
        program=self.program(command.program_id)
        if program is None or VersionedOrderCodec().hash('order-program',program)!=command.canonical_program_hash:raise MissingEvidence('Program hash binding required')
        if command.origin=='APPLICATION_GENERATED_CHILD' and self.write(command.parent_command_id) is None:raise MissingEvidence('Application parent command missing')
        owner=self._owner()
        if owner is None or owner!=(command.owner_strategy_id,command.ownership_epoch,command.policy_revision):raise MissingEvidence('Current owner required')
        if self.write(command.command_id) is not None:raise ValueError('Command already exists; query original identity')
        if command.state!='DURABLE_READY':raise ValueError('Initial command state must be durable ready')
        self.tx._append('order_writes_v2',command.command_id,command,0)

    @atomic_write
    def mark_may_have_sent(self,command_id,lease):
        command=self.write(command_id);owner=self._owner();now=self.clock()
        if command is None or command.state!='DURABLE_READY':raise MissingEvidence('UNKNOWN commands require original-ID reconciliation')
        shard=lease.shard
        if (owner is None or owner!=(command.owner_strategy_id,command.ownership_epoch,command.policy_revision)
            or shard.environment!=self.tx.scope.environment or shard.venue_account_id!=self.tx.scope.venue_account_id
            or shard.product!=self.tx.scope.product or shard.execution_epoch!=command.execution_epoch
            or utc_timestamp(now)>=utc_timestamp(lease.expires_at) or monotonic_ns()>=lease.deadline_monotonic_ns):raise MissingEvidence('Current execution owner required')
        # OP-B has no compiled-risk authorization/transport. Command persistence
        # alone must not issue a write permit before OP-C is implemented.
        raise MissingEvidence('ORDER_COMPILER_RISK_ADMISSION_NOT_IMPLEMENTED')

    @atomic_write
    def bind_parent(self,authority):
        VersionedOrderCodec().encode('parent-authority',authority)
        self.tx.require_scope(authority.scope)
        program=self.program(authority.program_id)
        if program is None:raise MissingEvidence('Native parent program required')
        # V3 storage/command hashes are readable but do not extend v2 lifecycle
        # authority. Units, multipliers and complete allocation need their own
        # reviewed authority revision before native parent/child admission.
        if program.contract_version!='2026-10-28':raise MissingEvidence('PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED')
        if program.execution_mode!='VENUE_NATIVE' or VersionedOrderCodec().hash('order-program',program)!=authority.program_hash:raise MissingEvidence('Native parent program required')
        command=self.write(authority.parent_command_id)
        if (command is None or command.state!='MAY_HAVE_SENT' or command.operation!='SUBMIT_PROGRAM'
            or command.program_id!=authority.program_id or command.canonical_program_hash!=authority.program_hash
            or command.ownership_epoch!=authority.ownership_epoch or command.owner_strategy_id!=authority.owner_strategy_id
            or command.venue_client_id!=authority.parent_client_id or command.service_namespace!=authority.namespace):raise MissingEvidence('Original durable parent command required')
        if self.verify_parent(authority) is not True:raise MissingEvidence('Trusted parent authorization/receipt required')
        # Imported parent evidence must bind the original durable identity; it is
        # never permission to synthesize/resend a command during migration.
        if authority.evidence.environment!=self.tx.scope.environment or utc_timestamp(authority.evidence.visible_at)>utc_timestamp(self.clock()):raise MissingEvidence('Invalid parent evidence')
        if not self._funding(authority):raise MissingEvidence('Parent funding or ownership unverified')
        previous=self.binding(authority.program_id)
        if previous is not None:
            if previous!=authority:raise ValueError('Immutable parent identity')
            return previous
        for reservation_id in authority.reservation_ids:
            old=self.tx._latest('order_funding_links',reservation_id,FundingLink)
            if old is not None and old.program_id!=authority.program_id:raise MissingEvidence('Reservation already assigned to another parent')
        for reservation_id in authority.reservation_ids:
            self.tx._append('order_funding_links',reservation_id,FundingLink(program_id=authority.program_id,reservation_id=reservation_id,intent_id=authority.funding_intent_id),0)
        self.tx._append('order_program_bindings',authority.program_id,authority,0)
        state=ProgramState(program_id=authority.program_id,sequence=1,status='ACTIVE',unallocated_quantity=authority.maximum_quantity,children=(),observation_ids=(),restriction_codes=(),protection_quantity=authority.starting_position_quantity.lstrip('-'))
        self.tx._append('order_program_states',authority.program_id,state,0)
        return authority

    def _restrict(self,authority,state,reason,event,evidence):
        result=restrict(state,reason)
        self._save_restriction(authority,result,event,evidence)
        return result

    def _save_restriction(self,authority,result,event,evidence):
        self.tx._append('order_program_states',authority.program_id,result.state,result.state.sequence-1)
        case=ReconciliationCase(program_id=authority.program_id,reason=result.restriction,observation=event,evidence=evidence,original_parent_id=authority.parent_venue_id,recorded_at=self.clock())
        key=canonical_hash({'case':domain_payload(case),'state_sequence':result.state.sequence})
        self.tx._append('order_reconciliation_cases',key,case,0)
        lock_id='order-program:'+key
        lock=self.tx._latest('risk_locks',lock_id,t.RiskLock)
        if lock is None:
            self.tx.risk_locks.append(t.RiskLock(lock_id=lock_id,scope=self.tx.scope,cause='ORDER_OUTCOME_UNKNOWN',state='ACTIVE',permission=t.RiskPermission.RECOVERY_REQUIRED,required_evidence=('ORIGINAL_COMMAND_RECONCILIATION',),raised_at=self.clock()),0)

    def _unbound(self,program_id,event,evidence):
        case=ReconciliationCase(program_id=program_id,reason='PARENT_BINDING_MISSING',observation=event,
            evidence=evidence,original_parent_id=event.parent_venue_id,recorded_at=self.clock())
        key=canonical_hash(dict(program_id=program_id,event=domain_payload(event),reason=case.reason))
        previous=self.tx._latest('order_reconciliation_cases',key,ReconciliationCase)
        if previous is not None:return IngestionResult(state=None,restriction=case.reason,duplicate=True)
        self.tx._append('order_reconciliation_cases',key,case,0)
        self.tx.risk_locks.append(t.RiskLock(lock_id='order-unbound:'+key,scope=self.tx.scope,cause='IDENTITY_ERROR',state='ACTIVE',permission=t.RiskPermission.RECOVERY_REQUIRED,required_evidence=('IDENTITY_RECONCILED',),raised_at=self.clock()),0)
        return IngestionResult(state=None,restriction=case.reason)

    @atomic_write
    def ingest(self,program_id,event,evidence):
        VersionedOrderCodec().encode('child-observation',event)
        authority=self.binding(program_id);state=self.state(program_id)
        if authority is None or state is None:return self._unbound(program_id,event,evidence)
        raw_hash=canonical_hash(domain_payload(event))
        verified=(evidence.environment==self.tx.scope.environment and evidence.hash==raw_hash
            and evidence.source in ('PAPER_MODEL_EVENT','BINANCE_USER_DATA','BINANCE_ORDER_QUERY')
            and utc_timestamp(event.occurred_at)<=utc_timestamp(evidence.visible_at)<=utc_timestamp(self.clock())
            and self.verify_ingress(event,evidence) is True)
        if not verified:return self._restrict(authority,state,'SOURCE_UNVERIFIED',event,evidence)
        event_key=canonical_hash(dict(venue=event.venue,namespace=event.namespace,event_id=event.observation_id))
        previous=self.tx._latest('order_source_observations',event_key,ObservationEvidence)
        if previous is not None:
            if previous.observation!=event:return self._restrict(authority,state,'EVENT_CONTENT_CONFLICT',event,evidence)
        else:
            self.tx._append('order_source_observations',event_key,ObservationEvidence(observation=event,evidence=evidence,verified_at=self.clock()),0)
        # Keep authentic historical V3 reports and original IDs, but never run
        # them through the incomplete V2 units/allocation reducer. This guard
        # also applies to events previously seen under an imported binding.
        if (event.scope!=authority.scope or event.parent_client_id!=authority.parent_client_id
            or event.parent_venue_id!=authority.parent_venue_id or event.namespace!=authority.namespace):
            return self._restrict(authority,state,'PARENT_LINK_UNVERIFIED',event,evidence)
        version_error=self._lifecycle_version_error(program_id)
        if version_error:return self._restrict(authority,state,version_error,event,evidence)
        disposition_key=canonical_hash(dict(program_id=program_id,event_key=event_key))
        disposition=self.tx._latest('order_observation_dispositions',disposition_key,ObservationDisposition)
        if disposition is not None:return IngestionResult(state=state,restriction=disposition.restriction or (state.restriction_codes[-1] if state.status=='RECONCILIATION_REQUIRED' else None),duplicate=True)
        if not self._funding(authority):return self._restrict(authority,state,'PARENT_FUNDING_OR_OWNER_CHANGED',event,evidence)
        child_key=canonical_hash(dict(venue=event.venue,namespace=event.child_namespace,child_id=event.child_order_id))
        link=self.tx._latest('order_child_links',child_key,ChildLink)
        if link is not None and link.program_id!=program_id:return self._restrict(authority,state,'CHILD_PARENT_CONFLICT',event,evidence)
        for fill in event.fills:
            trade_key=canonical_hash(dict(venue=event.venue,namespace=event.child_namespace,trade_id=fill.native_trade_id))
            trade_link=self.tx._latest('order_trade_links',trade_key,ChildLink)
            if trade_link is not None and (trade_link.program_id!=program_id or trade_link.child_order_id!=event.child_order_id):return self._restrict(authority,state,'TRADE_PARENT_CONFLICT',event,evidence)
        result=apply_child_observation(authority,state,event)
        self.tx._append('order_observation_dispositions',disposition_key,ObservationDisposition(program_id=program_id,event_key=event_key,restriction=result.restriction),0)
        if result.restriction:
            self._save_restriction(authority,result,event,evidence)
            return result
        accepted=ChildLink(program_id=program_id,parent_venue_id=event.parent_venue_id,child_order_id=event.child_order_id,namespace=event.child_namespace,evidence_id=evidence.id)
        if link is None:self.tx._append('order_child_links',child_key,accepted,0)
        for fill in event.fills:
            trade_key=canonical_hash(dict(venue=event.venue,namespace=event.child_namespace,trade_id=fill.native_trade_id))
            if self.tx._latest('order_trade_links',trade_key,ChildLink) is None:self.tx._append('order_trade_links',trade_key,accepted,0)
        self.tx._append('order_program_states',program_id,result.state,state.sequence)
        return result


    @atomic_write
    def observe_parent(self,program_id,event,evidence):
        VersionedOrderCodec().encode('parent-observation',event)
        authority=self.binding(program_id);state=self.state(program_id)
        if authority is None or state is None:return self._unbound(program_id,event,evidence)
        if (event.scope!=authority.scope or event.namespace!=authority.namespace or event.venue!=authority.venue
            or event.parent_venue_id!=authority.parent_venue_id or event.parent_client_id!=authority.parent_client_id):
            return self._restrict(authority,state,'PARENT_LINK_UNVERIFIED',event,evidence)
        if (evidence.environment!=self.tx.scope.environment or evidence.hash!=canonical_hash(domain_payload(event))
            or evidence.source not in ('PAPER_MODEL_EVENT','BINANCE_USER_DATA','BINANCE_ORDER_QUERY')
            or not utc_timestamp(event.occurred_at)<=utc_timestamp(evidence.visible_at)<=utc_timestamp(self.clock())
            or self.verify_ingress(event,evidence) is not True):return self._restrict(authority,state,'SOURCE_UNVERIFIED',event,evidence)
        key='parent-observation:'+canonical_hash(dict(program=program_id,event=event.observation_id))
        previous=self.tx._latest('audit_events',key,ParentObservation)
        if previous is not None:
            if previous!=event:return self._restrict(authority,state,'EVENT_CONTENT_CONFLICT',event,evidence)
        else:
            self.tx._append('audit_events',key,event,0)
            self.tx._append('audit_events','parent-proof:'+key,evidence,0)
        version_error=self._lifecycle_version_error(program_id)
        if version_error:return self._restrict(authority,state,version_error,event,evidence)
        if previous is not None:
            return IngestionResult(state=state,restriction=state.restriction_codes[-1] if state.status=='RECONCILIATION_REQUIRED' else None,duplicate=True)
        result=apply_parent_observation(authority,state,event)
        if result.restriction:
            self._save_restriction(authority,result,event,evidence)
            return result
        self.tx._append('order_program_states',program_id,result.state,state.sequence)
        return result
