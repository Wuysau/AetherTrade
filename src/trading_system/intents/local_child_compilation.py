"""Pure local child compilation; no schedule, source proof, funds or send grant.

The SQL child-admission boundary must authenticate activation, schedule and pool
consumption before materializing/persisting an independently permitted write.
"""
from dataclasses import dataclass,replace
from typing import Literal
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_program import QuantitySpec,amount
from .program_compilation import (ProgramCompilation,CompiledNode,RequestPlan,
    compile_order_program,_compile_node,require,Parameter,FieldName,Namespace,Operation,Timing)


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalChildCompilation:
    parent:ProgramCompilation
    node:CompiledNode
    request:RequestPlan

    @property
    def execution_authorized(self)->Literal[False]:return False


def compile_local_child(parent,quantity,rules,capabilities,**context):
    require(type(parent) is ProgramCompilation,'LOCAL_CHILD_PARENT_REQUIRED')
    program=parent.original_program
    require(program.execution_mode in ('LOCAL_SYNTHETIC','PAPER_MODEL')
        and program.template in ('TWAP','VP') and len(parent.nodes)==1
        and len(parent.requests)==1 and parent.requests[0].operation==Operation.LOCAL_ALGORITHM,
        'LOCAL_CHILD_PARENT_REQUIRED')
    # Revalidate the exact parent using the existing product/capability compiler.
    # Changes require explicit parent re-admission, not a silent child rewrite.
    rebuilt=compile_order_program(parent.original_proposal,rules,capabilities,**context)
    require(canonical_hash(domain_payload(rebuilt))==canonical_hash(domain_payload(parent)),
        'LOCAL_CHILD_PARENT_CHANGED')
    original=parent.nodes[0].original
    require(original.order.quantity.kind=='BASE_QUANTITY','LOCAL_CHILD_BASE_QUANTITY_REQUIRED')
    require(amount(quantity)<=amount(parent.nodes[0].quantity.value),'LOCAL_CHILD_POOL_QUANTITY_EXCEEDED')
    child=replace(original,role='ENTRY',child_origin='APPLICATION_GENERATED_CHILD',
        order=replace(original.order,quantity=QuantitySpec(kind='BASE_QUANTITY',value=quantity,source_node=None)))
    # No implicit GTC for an algorithm's NONE envelope; ordinary child TIF must
    # already be expressible. No rounding or LIMIT-to-MARKET substitution.
    node=_compile_node(child,program,rules,context['profile'],False,context['now'])
    require(node.quantity==child.order.quantity,'LOCAL_CHILD_QUANTITY_CHANGED')
    operation=(Operation.CONDITIONAL if node.namespace==Namespace.USD_M_CONDITIONAL else
        Operation.SOR if child.order.routing=='SOR' else Operation.ORDER)
    request=RequestPlan(namespace=node.namespace,operation=operation,node_ids=(child.node_id,),
        parameters=(Parameter(name=FieldName.SYMBOL,value=context['profile'].venue_symbol),*node.parameters),
        timing=Timing.ALGORITHM_CHILD,native_mother=False,
        generated_child_origin='APPLICATION_GENERATED_CHILD',requires_independent_command=True)
    return LocalChildCompilation(parent=parent,node=node,request=request)
