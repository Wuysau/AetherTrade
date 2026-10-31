"""Read original programme query identities without reassessing or resending.

Historical hashes and protected send history establish which original IDs to
query. They grant no present financial authority, ownership or new write permit.
Current send verification remains entirely in the existing command boundary.
"""
from dataclasses import dataclass,replace
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.execution.program_commands import ProgramRequestBinding
from trading_system.intents.program_compilation import Namespace
from trading_system.risk.program_financial_admission import ProgramFinancialBundle,BUNDLE_PREFIX
from trading_system.risk.program_financial_admission import _bundle_type_valid,decode_program_bundle
from trading_system.storage.models import decode,scope_id
from .cases import validate_budget


def digest(value):return canonical_hash(domain_payload(value))
def require(condition,reason):
    if not condition:raise ValueError(reason)


@dataclass(frozen=True,slots=True,kw_only=True)
class OriginalCreation:
    initial:ApplicationWriteV3
    request:VenueCreationRequest
    current_state:Literal['DURABLE_READY','MAY_HAVE_SENT','UNKNOWN']

    @property
    def can_resubmit(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False


def _restore_original_creation(writes,binding,bundle,mark):
    require(type(writes) is tuple and 1<=len(writes)<=3
        and all(type(w) is ApplicationWriteV3 for w in writes),'ORIGINAL_CREATION_VERSION_REQUIRED')
    initial=writes[0]
    require(tuple(w.state for w in writes)==('DURABLE_READY','MAY_HAVE_SENT','UNKNOWN')[:len(writes)]
        and all(digest(replace(w,state='DURABLE_READY'))==digest(initial) for w in writes),
        'ORIGINAL_CREATION_HISTORY_CHANGED')
    require(type(binding) is ProgramRequestBinding and _bundle_type_valid(bundle),
        'ORIGINAL_CREATION_BINDING_REQUIRED')
    request=binding.request;i=bundle.intent;a=bundle.archive
    require(type(request) is VenueCreationRequest and initial.binding_hash==digest(binding)
        and initial.request_hash==binding.request_hash==digest(request)
        and binding.bundle_hash==digest(bundle) and binding.source_hash==digest(i.source),
        'ORIGINAL_CREATION_HASH_CHANGED')
    require(initial.scope==binding.scope==request.scope==i.scope
        and initial.command_id==binding.command_id==request.command_id==request.assigned_ids.command_id
        and initial.intent_id==binding.intent_id==i.intent_id
        and initial.execution_epoch==binding.execution_epoch
        and initial.service_namespace==request.namespace.value
        and initial.request_index==request.request_index==request.assigned_ids.request_index,
        'ORIGINAL_CREATION_IDENTITY_CHANGED')
    require(initial.intent_hash==digest(i) and initial.basis_hash==digest(bundle.basis)
        and initial.decision_hash==digest(bundle.decision) and initial.archive_hash==digest(a)
        and initial.compilation_hash==request.compilation_hash==request.assigned_ids.compilation_hash==digest(a.compilation)
        and initial.allocation_hash==digest(a.allocation)
        and initial.canonical_program_hash==digest(i.proposal.program)
        and initial.program_id==i.proposal.program.program_id
        and request.profile_hash==digest(a.profile)
        and (initial.owner_strategy_id,initial.ownership_epoch,initial.policy_revision)==(
            i.owner_strategy_id,i.ownership_epoch,i.risk_policy_revision),
        'ORIGINAL_CREATION_FINANCIAL_HASH_CHANGED')
    if len(writes)>1:
        require(type(mark) is dict and canonical_hash(mark)==digest(replace(initial,state='MAY_HAVE_SENT')),
            'ORIGINAL_CREATION_PROTECTED_MAY_REQUIRED')
    return OriginalCreation(initial=initial,request=request,current_state=writes[-1].state)


def read_original_creation(tx,command_id):
    """SELECT only; later projections, authorizations and submission TTL stay intact.

    Use the exact initial immutable records, not latest audit revisions or a new
    compiler/risk replay whose behavior may have changed since this command.
    """
    require(type(command_id) is str and bool(command_id),'ORIGINAL_COMMAND_ID_REQUIRED')
    key=scope_id(tx.scope)
    rows=tx.execute('SELECT sequence,payload FROM ledger.order_writes_v2 '
        'WHERE scope_id=%s AND record_id=%s ORDER BY sequence',(key,command_id)).fetchall()
    if not rows:return None
    require(tuple(n for n,_ in rows)==tuple(range(1,len(rows)+1)),'ORIGINAL_CREATION_SEQUENCE_CHANGED')
    writes=tuple(VersionedOrderCodec().decode('application-write',p) for _,p in rows)
    require(type(writes[0]) is ApplicationWriteV3 and writes[0].scope==tx.scope
        and writes[0].command_id==command_id,'ORIGINAL_CREATION_SCOPE_CHANGED')
    row=tx.execute('SELECT payload FROM ledger.program_request_bindings WHERE scope_id=%s '
        'AND record_id=%s AND sequence=1',(key,command_id)).fetchone()
    require(row is not None,'ORIGINAL_CREATION_BINDING_REQUIRED')
    binding=decode(ProgramRequestBinding,row[0])
    row=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s '
        'AND record_id=%s AND sequence=1',(key,BUNDLE_PREFIX+writes[0].intent_id)).fetchone()
    require(row is not None,'ORIGINAL_CREATION_BUNDLE_REQUIRED')
    bundle=decode_program_bundle(row[0])
    mark=tx.execute('SELECT write_payload FROM ledger.program_dispatch_marks WHERE scope_id=%s '
        'AND command_id=%s',(key,command_id)).fetchone() if len(writes)>1 else None
    original=_restore_original_creation(writes,binding,bundle,None if mark is None else mark[0])
    ids=original.request.assigned_ids
    expected={m.client_id for m in ids.members}|({ids.parent_client_id} if ids.parent_client_id else set())
    registered=tx.execute('SELECT environment,account_id,product,service_namespace,client_id '
        'FROM ledger.program_client_identities WHERE scope_id=%s AND command_id=%s',(key,command_id)).fetchall()
    require(bool(expected) and len(registered)==len(expected) and {r[4] for r in registered}==expected
        and all(tuple(r[:4])==(str(tx.scope.environment),tx.scope.venue_account_id,str(tx.scope.product),
            writes[0].service_namespace) for r in registered),'ORIGINAL_CREATION_REGISTRY_CHANGED')
    return original


@dataclass(frozen=True,slots=True,kw_only=True)
class OriginalQueryTarget:
    scope:t.ScopeKey
    namespace:Namespace
    client_id:str
    node_ids:tuple[str,...]
    role:Literal['PROGRAM_PARENT','ORDER_MEMBER']


def original_query_targets(original):
    require(type(original) is OriginalCreation,'ORIGINAL_CREATION_REQUIRED')
    request=original.request;ids=request.assigned_ids
    if ids.parent_client_id is not None:
        return (OriginalQueryTarget(scope=request.scope,namespace=request.namespace,client_id=ids.parent_client_id,
            node_ids=tuple(m.node_id for m in ids.members),role='PROGRAM_PARENT'),)
    return tuple(OriginalQueryTarget(scope=request.scope,namespace=request.namespace,client_id=m.client_id,
        node_ids=(m.node_id,),role='ORDER_MEMBER') for m in ids.members)


def original_creation_case(original,*,now,budget):
    require(type(original) is OriginalCreation and original.current_state in ('MAY_HAVE_SENT','UNKNOWN'),
        'POTENTIALLY_SENT_ORIGINAL_REQUIRED')
    require(type(now) is t.ClockReading and now.clock_health=='HEALTHY'
        and type(now.monotonic_ns) is int and now.monotonic_ns>=0,'QUERY_CLOCK_REQUIRED')
    utc_timestamp(now.utc_now);validate_budget(budget)
    write=original.initial
    case_id='program-original:'+canonical_hash(dict(scope=domain_payload(write.scope),
        command_id=write.command_id,service_namespace=write.service_namespace,request_hash=write.request_hash))
    return t.ReconciliationCase(case_id=case_id,scope=write.scope,command_ids=(write.command_id,),position_ids=(),
        missing=('CREATE_REQUEST_UNKNOWN',),query_budget=budget,attempts=0,next_query_at=now.utc_now,state='OPEN')
