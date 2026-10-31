"""Pre-ACK local quantity commitments, not a second ledger or release authority.

The owner writer must authenticate the complete committed set under its existing
collateral mutex. Original quantities remain consumed across fill/UNKNOWN/cancel
labels. A future protected unused-allocation disposition may return headroom;
this version never infers it from a status string or a missing venue order.
"""
from dataclasses import dataclass
from decimal import Decimal,DecimalException
from typing import Literal
from trading_system.contracts.order_program import amount,identifier
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.intents.program_compilation import ProgramCompilation
from trading_system.intents.local_child_compilation import LocalChildCompilation
from trading_system.risk.policy_checks import exact_risk_arithmetic
from .creation_requests import require,digest
from .local_child_requests import LocalChildRequest,restore_local_child_request


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalChildCommitment:
    compilation:LocalChildCompilation
    request:LocalChildRequest


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalChildQuantityPreview:
    activation_id:str
    parent_hash:str
    commitments_hash:str
    committed_quantity:str
    unallocated_quantity:str
    quantity_unit:str
    duplicate:bool

    @property
    def execution_authorized(self)->Literal[False]:return False


def preview_local_child_quantity(parent,activation_id,committed,proposed):
    identifier(activation_id)
    require(type(parent) is ProgramCompilation and len(parent.nodes)==1
        and parent.original_program.template in ('TWAP','VP')
        and parent.original_program.execution_mode in ('LOCAL_SYNTHETIC','PAPER_MODEL'),
        'LOCAL_CHILD_PARENT_REQUIRED')
    require(type(committed) is tuple and len(committed)<=4096,'LOCAL_CHILD_COMMITMENT_BOUND')
    root=parent.nodes[0];parent_hash=digest(parent)
    require(root.quantity.kind=='BASE_QUANTITY','LOCAL_CHILD_BASE_QUANTITY_REQUIRED')
    def validate(item):
        require(type(item) is LocalChildCommitment and type(item.compilation) is LocalChildCompilation
            and type(item.request) is LocalChildRequest,'LOCAL_CHILD_COMMITMENT_REQUIRED')
        child=item.compilation;request=item.request.request
        require(digest(child.parent)==parent_hash,'LOCAL_CHILD_COMMITMENT_PARENT_CHANGED')
        restore_local_child_request(item.request,child,activation_id=activation_id,command_id=request.command_id)
        node=child.node
        require(node.original.node_id==root.original.node_id and node.quantity.kind=='BASE_QUANTITY'
            and (node.quantity_unit,node.quantity_multiplier)==(root.quantity_unit,root.quantity_multiplier),
            'LOCAL_CHILD_QUANTITY_UNIT_CHANGED')
        fields=[f.value for f in request.fields if f.name=='quantity']
        require(len(fields)==1 and amount(fields[0])==amount(node.quantity.value),
            'LOCAL_CHILD_REQUEST_QUANTITY_CHANGED')
        return request.command_id,amount(node.quantity.value)
    records={};quantities={}
    for item in committed:
        key,quantity=validate(item)
        require(key not in records,'LOCAL_CHILD_DUPLICATE_COMMITMENT')
        records[key]=item;quantities[key]=quantity
    key,quantity=validate(proposed);duplicate=key in records
    if duplicate:require(digest(records[key])==digest(proposed),'LOCAL_CHILD_COMMAND_REPLAY_CONFLICT')
    else:records[key]=proposed;quantities[key]=quantity
    try:
        with exact_risk_arithmetic():
            used=sum(quantities.values(),Decimal(0));remaining=amount(root.quantity.value)-used
            require(remaining>=0,'LOCAL_CHILD_QUANTITY_POOL_EXCEEDED')
    except DecimalException as exc:raise ValueError('LOCAL_CHILD_QUANTITY_ARITHMETIC_NOT_EXACT') from exc
    return LocalChildQuantityPreview(activation_id=activation_id,parent_hash=parent_hash,
        commitments_hash=canonical_hash({'commitments':[domain_payload(records[k]) for k in sorted(records)]}),
        committed_quantity=format(used,'f'),unallocated_quantity=format(remaining,'f'),
        quantity_unit=root.quantity_unit,duplicate=duplicate)
