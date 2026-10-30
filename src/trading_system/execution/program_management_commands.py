"""Immutable management history and protected single-action permits."""
from dataclasses import dataclass,replace
from datetime import timedelta
from typing import Literal
import secrets
import json
from hashlib import sha256
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,utc_timestamp
from trading_system.contracts.management_write_v4 import ManagementWriteV4
from trading_system.contracts.management_codec import ManagementCodec
from trading_system.intents.order_management_compilation import ManagementCompilation
from trading_system.execution.management_requests import VenueManagementRequest
from trading_system.storage.models import decode,scope_id,atomic_write

def payload(value):
    if type(value) in (tuple,list):return [payload(v) for v in value]
    if type(value) is dict:return {k:payload(v) for k,v in value.items()}
    if type(value) in (str,int,bool) or value is None:return value
    return domain_payload(value)
def digest(value):
    packed=payload(value)
    return canonical_hash(packed if type(packed) is dict else {'items':packed})
def require(ok,reason):
    if not ok:raise ValueError(reason)
def token_hash(token):return canonical_hash({'management_v4_private_token':token})

@dataclass(frozen=True,slots=True,kw_only=True)
class ManagementBinding:
    write:ManagementWriteV4
    plan:ManagementCompilation
    request:VenueManagementRequest
    source_context:t.DecisionContext
    original_write_hash:str
    book_hash:str
    reservation_hash:str
    risk_hash:str
    funding_hash:str
    target_source_json:str
    target_source_hash:str

def binding_hash(binding):return digest(replace(binding,write=replace(binding.write,binding_hash='0'*64)))

@dataclass(frozen=True,slots=True,kw_only=True)
class ManagementPermit:
    command_id:str
    scope:t.ScopeKey
    binding_hash:str
    request_hash:str
    token:str
    expires_at:str
    deadline_monotonic_ns:int

class ManagementRepository:
    def __init__(self,tx):self.tx=tx

    def read(self,command_id):
        row=self.tx.execute('SELECT payload FROM ledger.management_bindings WHERE scope_id=%s AND record_id=%s',
            (scope_id(self.tx.scope),command_id)).fetchone()
        if row is None:return None
        binding=decode(ManagementBinding,row[0]);w=binding.write
        ManagementCodec().encode(w)
        require(binding_hash(binding)==w.binding_hash and digest(binding.plan)==w.plan_hash
            and digest(binding.plan.target)==w.target_hash and digest(binding.request)==w.request_hash
            and binding.risk_hash==w.risk_hash and binding.funding_hash==w.funding_hash,
            'MANAGEMENT_IMMUTABLE_BINDING_CHANGED')
        require(sha256(binding.target_source_json.encode('utf-8')).hexdigest()==binding.target_source_hash
            and json.loads(binding.target_source_json)['evidence']['hash']==binding.plan.target.evidence.hash,
            'MANAGEMENT_TARGET_SOURCE_CHANGED')
        rows=self.tx.execute('SELECT sequence,payload FROM ledger.management_writes WHERE scope_id=%s AND record_id=%s ORDER BY sequence',
            (scope_id(self.tx.scope),command_id)).fetchall()
        require(tuple(n for n,_ in rows)==tuple(range(1,len(rows)+1)) and 1<=len(rows)<=3,'MANAGEMENT_HISTORY_REQUIRED')
        writes=tuple(ManagementCodec().decode(p) for _,p in rows)
        require(tuple(v.state for v in writes)==('DURABLE_READY','MAY_HAVE_SENT','UNKNOWN')[:len(writes)]
            and all(digest(replace(v,state='DURABLE_READY'))==digest(w) for v in writes),'MANAGEMENT_HISTORY_CHANGED')
        return replace(binding,write=writes[-1])

    @atomic_write
    def append(self,binding,context):
        w=binding.write;self.tx.require_scope(w.scope)
        require(self.read(w.command_id) is None,'MANAGEMENT_QUERY_ORIGINAL_NO_RETRY')
        self.tx.execute('SELECT ledger.admit_management(%s,%s,%s::jsonb,%s::jsonb,%s)',
            (scope_id(w.scope),w.command_id,canonical_json(domain_payload(binding)),canonical_json(context),self.tx.transaction_id))
        return w

class ManagementBoundary:
    def __init__(self,service):self.service=service;self.uow=service.verifier_uow;self.scope=service.scope

    def mark_may_have_sent(self,command_id,lease):
        candidate=secrets.token_urlsafe(32)
        target=self.service.capture_for(command_id)
        with self.uow.transaction(self.scope) as tx:
            binding=ManagementRepository(tx).read(command_id)
            require(binding is not None and binding.write.state=='DURABLE_READY','MANAGEMENT_QUERY_ORIGINAL_NO_RETRY')
            context,clock,expires=self.service.current(tx,binding,target,lease)
            ttl=min(tx.permit_ttl_ms,int((expires-utc_timestamp(clock.utc_now)).total_seconds()*1000),
                (lease.deadline_monotonic_ns-clock.monotonic_ns)//1_000_000)
            require(ttl>0,'MANAGEMENT_PERMIT_EXPIRED')
            self.tx_mark(tx,binding,context,candidate,ttl)
        w=binding.write
        return ManagementPermit(command_id=command_id,scope=w.scope,binding_hash=w.binding_hash,request_hash=w.request_hash,
            token=candidate,expires_at=(utc_timestamp(clock.utc_now)+timedelta(milliseconds=ttl)).isoformat(),
            deadline_monotonic_ns=clock.monotonic_ns+ttl*1_000_000)

    def tx_mark(self,tx,binding,context,candidate,ttl):
        tx.execute('SELECT ledger.mark_management_may(%s,%s,%s::jsonb,%s,%s,%s)',
            (scope_id(self.scope),binding.write.command_id,canonical_json(context),token_hash(candidate),ttl,tx.transaction_id))

    def consume(self,permit,lease):
        require(type(permit) is ManagementPermit,'MANAGEMENT_PRIVATE_PERMIT_REQUIRED')
        target=self.service.capture_for(permit.command_id)
        with self.uow.transaction(self.scope) as tx:
            binding=ManagementRepository(tx).read(permit.command_id)
            require(binding is not None and binding.write.state=='MAY_HAVE_SENT' and permit.scope==self.scope
                and permit.binding_hash==binding.write.binding_hash and permit.request_hash==binding.write.request_hash,
                'MANAGEMENT_PERMIT_BINDING_CHANGED')
            context,clock,_=self.service.current(tx,binding,target,lease)
            require(utc_timestamp(clock.utc_now)<utc_timestamp(permit.expires_at)
                and clock.monotonic_ns<permit.deadline_monotonic_ns,'MANAGEMENT_PERMIT_EXPIRED')
            tx.execute('SELECT ledger.consume_management_permit(%s,%s,%s,%s::jsonb,%s)',
                (scope_id(self.scope),permit.command_id,token_hash(permit.token),canonical_json(context),tx.transaction_id))
        return binding

    def recheck_consumed(self,permit,lease):
        from .program_management_consumed_boundary import recheck_consumed
        return recheck_consumed(self,permit,lease)

    def record_unknown(self,command_id):
        with self.uow.transaction(self.scope) as tx:
            tx.execute('SELECT ledger.mark_management_unknown(%s,%s,%s)',
                (scope_id(self.scope),command_id,tx.transaction_id))
