"""Read-only local commitment recovery. No admission/source writer is installed.

The existing local activation reader establishes parent provenance. These rows
remain structural recovery candidates until a protected child admission writer
and its transaction/source proofs are independently implemented and qualified.
Missing schema is not an empty pool. SQL permission errors are never suppressed.
"""
from dataclasses import dataclass
from typing import Literal
from trading_system.contracts.order_program import identifier
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.execution.local_child_quantity import LocalChildCommitment,preview_local_child_quantity
from trading_system.execution.local_child_requests import restore_local_child_request
from .models import MissingEvidence,scope_id,decode


def need(condition,code):
    if not condition:raise MissingEvidence(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalChildCommitmentSnapshot:
    activation_id:str
    parent_compilation_hash:str
    commitments:tuple[LocalChildCommitment,...]

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def source_verified(self)->Literal[False]:return False


def read_local_child_commitments(tx,activation_id):
    identifier(activation_id)
    need(tx.active is True,'LOCAL_CHILD_ACTIVE_TRANSACTION_REQUIRED')
    relation=tx.execute("SELECT to_regclass('ledger.local_child_commitments_v1')").fetchone()
    need(relation is not None and relation[0] is not None,'LOCAL_CHILD_STORAGE_NOT_INSTALLED')
    from trading_system.execution.local_program_activation import read_local_activation
    activation=read_local_activation(tx,activation_id)
    need(activation is not None and activation.authority.scope==tx.scope,'LOCAL_CHILD_ACTIVATION_REQUIRED')
    physical=scope_id(tx.scope)
    rows=tx.execute('SELECT scope_id,command_id,activation_id,commitment_hash,payload '
        'FROM ledger.local_child_commitments_v1 WHERE scope_id=%s AND activation_id=%s '
        'ORDER BY command_id LIMIT 4097',(physical,activation_id)).fetchall()
    need(len(rows)<=4096,'LOCAL_CHILD_COMMITMENT_BOUND')
    result=[];seen=set()
    for source_scope,command,source_activation,stored_hash,payload in rows:
        need(source_scope==physical and source_activation==activation_id and command not in seen,
            'LOCAL_CHILD_STORAGE_IDENTITY_CHANGED')
        need(canonical_hash(payload)==stored_hash,'LOCAL_CHILD_STORAGE_HASH_CHANGED')
        item=decode(LocalChildCommitment,payload)
        need(canonical_hash(domain_payload(item))==stored_hash,'LOCAL_CHILD_STORAGE_TYPED_PAYLOAD_CHANGED')
        need(item.request.request.scope==tx.scope and item.request.request.command_id==command
            and canonical_hash(domain_payload(item.compilation.parent))==activation.authority.compilation_hash,
            'LOCAL_CHILD_STORAGE_PARENT_CHANGED')
        restore_local_child_request(item.request,item.compilation,activation_id=activation_id,command_id=command)
        result.append(item);seen.add(command)
    if result:
        preview_local_child_quantity(result[0].compilation.parent,activation_id,tuple(result),result[-1])
    return LocalChildCommitmentSnapshot(activation_id=activation_id,
        parent_compilation_hash=activation.authority.compilation_hash,commitments=tuple(result))
