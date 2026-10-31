"""Neutral durable local obligations. No cash conversion or release authority."""
from dataclasses import dataclass
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.order_lifecycle_v3 import ParentAuthorityV3,ParentBindingV3,ProgramStateV3,LifecycleProofV3
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.canonical import canonical_hash,domain_payload
from .models import decode,scope_id,account_key,MissingEvidence

ACTIVATION_PREFIX='local-program-activation:'
STATE_PREFIX='local-program-state:'
PROOF_PREFIX='local-program-proof:'


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgrammeActivation:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['LOCAL_PROGRAM_ACTIVATION']
    activation_id:str
    financial_intent_id:str
    bundle_hash:str
    financial_transaction_id:str
    collateral_group_id:str
    activated_at:str
    authority:ParentAuthorityV3
    binding:ParentBindingV3
    proofs:tuple[LifecycleProofV3,...]
    state:ProgramStateV3

    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalProgramObligation:
    original:ProgrammeActivation
    current:ProgramStateV3
    pending_child_command_ids:tuple[str,...]=()

    @property
    def identity(self):return 'LOCAL_PROGRAM:'+self.original.activation_id

    @property
    def unresolved(self):
        # No qualified discharge/source-event port is installed yet. Any state
        # change is unresolved; a terminal label cannot clear an allocation.
        return bool(self.pending_child_command_ids) or self.current!=self.original.state


def local_child_obligation_ids(tx,activation):
    """Only retain duties; no status label or snapshot can discharge a child."""
    row=tx.execute("SELECT to_regclass('ledger.local_child_commitments_v1')").fetchone()
    if row is not None and row[0] is None:return ()  # Historical schema has no child writer.
    if row is None:raise MissingEvidence('LOCAL_CHILD_SCHEMA_UNVERIFIED')
    from trading_system.execution.local_child_quantity import LocalChildCommitment
    from trading_system.execution.local_child_requests import restore_local_child_request
    authority=activation.authority
    rows=tx.execute('SELECT command_id,commitment_hash,payload FROM ledger.local_child_commitments_v1 '
        'WHERE scope_id=%s AND activation_id=%s ORDER BY command_id LIMIT 4097',
        (scope_id(authority.scope),activation.activation_id)).fetchall()
    if len(rows)>4096:raise MissingEvidence('LOCAL_CHILD_COMMITMENT_BOUND')
    ids=[]
    for command,stored_hash,payload in rows:
        if command in ids or canonical_hash(payload)!=stored_hash:
            raise MissingEvidence('LOCAL_CHILD_OBLIGATION_SOURCE_INVALID')
        item=decode(LocalChildCommitment,payload)
        if (canonical_hash(domain_payload(item))!=stored_hash or item.request.request.scope!=authority.scope
                or canonical_hash(domain_payload(item.compilation.parent))!=authority.compilation_hash):
            raise MissingEvidence('LOCAL_CHILD_OBLIGATION_SOURCE_INVALID')
        restore_local_child_request(item.request,item.compilation,
            activation_id=activation.activation_id,command_id=command)
        ids.append(command)
    return tuple(ids)


def local_program_obligations(tx,*,scope_only=False):
    rows=tx.execute('SELECT scope_id,record_id,sequence,payload,transaction_id FROM ledger.audit_events '
        'WHERE left(record_id,%s)=%s ORDER BY scope_id,record_id,sequence',
        (len(ACTIVATION_PREFIX),ACTIVATION_PREFIX)).fetchall()
    result=[]
    for physical,key,sequence,payload,transaction_id in rows:
        try:
            original=decode(ProgrammeActivation,payload);a=original.authority
            if (sequence!=1 or physical!=scope_id(a.scope) or key!=ACTIVATION_PREFIX+original.activation_id
                    or original.financial_transaction_id!=transaction_id or original.state.program_id!=a.program_id
                    or original.state.authority_hash!=canonical_hash(domain_payload(a))):
                raise ValueError('identity')
            if a.scope.environment!=tx.scope.environment:continue
            bound=tx.execute('SELECT collateral_group_id FROM ledger.account_bindings WHERE '
                'environment=%s AND account_id=%s AND product=%s',account_key(a.scope)).fetchone()
            if bound is None or bound[0]!=original.collateral_group_id:raise ValueError('collateral binding')
            if bound[0]!=tx.group_id:continue
            if scope_only and a.scope!=tx.scope:continue
            program=tx.execute('SELECT payload FROM ledger.order_programs WHERE scope_id=%s AND record_id=%s',
                (physical,a.program_id)).fetchone()
            if program is None or VersionedOrderCodec().hash('order-program',VersionedOrderCodec().decode(
                    'order-program',program[0]))!=a.program_hash:raise ValueError('program')
            states=tx.execute('SELECT sequence,payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s '
                'ORDER BY sequence',(physical,STATE_PREFIX+original.activation_id)).fetchall()
            if tuple(r[0] for r in states)!=tuple(range(1,len(states)+1)) or len(states)<3:raise ValueError('state history')
            current=decode(ProgramStateV3,states[-1][1])
            if current.program_id!=a.program_id or current.authority_hash!=original.state.authority_hash:raise ValueError('state identity')
            result.append(LocalProgramObligation(original=original,current=current,
                pending_child_command_ids=local_child_obligation_ids(tx,original)))
        except Exception as exc:
            raise MissingEvidence('LOCAL_PROGRAM_SOURCE_INVALID') from exc
    return tuple(result)
