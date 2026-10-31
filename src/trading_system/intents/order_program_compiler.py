"""OP-C first slice: preserve an atomic Spot plan and normalize its target.

This is a compile result, not an order delta, risk approval or send permit. The
existing TargetCompiler/ReservationService must subsequently bind positions,
working/unknown orders, whole-account risk and funds under their existing UoW.
"""
from dataclasses import dataclass
from decimal import Decimal,localcontext
from trading_system.contracts import types as t
from trading_system.contracts.order_program import OrderProposal,OrderProgram
from trading_system.contracts.order_snapshots import OrderRuleSnapshot,OrderCapabilitySnapshot
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from .semantic_validation import require,quantity_from_notional,quantity_on_grid

@dataclass(frozen=True,kw_only=True)
class AtomicCompilation:
    original_proposal:OrderProposal
    original_program:OrderProgram
    normalized_target_quantity:str
    sizing_price:str
    order_type:str
    limit_price:str|None
    time_in_force:str
    proposal_hash:str
    program_hash:str
    rules_hash:str
    capabilities_hash:str
    execution_authorized:bool=False


def compile_atomic_target(proposal:OrderProposal,rules:OrderRuleSnapshot,capabilities:OrderCapabilitySnapshot,
                          *,scope:t.ScopeKey,now:str,expected_build_digest:str,expected_rule_revision:int,
                          expected_capabilities_hash:str,expected_instrument_ref:str,allow_round_down:bool=False):
    codec=VersionedOrderCodec();codec.encode('order-proposal',proposal)
    codec.encode('order-rule-snapshot',rules);codec.encode('order-capability-snapshot',capabilities)
    require(scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),'ENVIRONMENT_DISABLED')
    require(scope.product==t.Product.SPOT,'PRODUCT_PROFILE_NOT_BOUND')
    require(proposal.instrument_ref==expected_instrument_ref,'INSTRUMENT_REFERENCE_MISMATCH')
    require(rules.scope==scope and capabilities.scope==scope,'SCOPE_MISMATCH')
    require(capabilities.adapter_build_digest==expected_build_digest,'ADAPTER_BUILD_CHANGED')
    require(rules.revision==expected_rule_revision==capabilities.rule_revision,'RULE_REVISION_CHANGED')
    require(canonical_hash(domain_payload(capabilities))==expected_capabilities_hash,'CAPABILITY_SNAPSHOT_CHANGED')
    require(utc_timestamp(rules.as_of)<=utc_timestamp(now)<utc_timestamp(rules.valid_until),'RULES_EXPIRED')
    require(utc_timestamp(capabilities.as_of)<=utc_timestamp(now)<utc_timestamp(capabilities.valid_until),'CAPABILITIES_EXPIRED')
    require(utc_timestamp(now)<utc_timestamp(proposal.valid_until),'PROPOSAL_EXPIRED')
    program=proposal.program
    require(program is not None and proposal.action=='SET_TARGET','EXECUTABLE_TARGET_REQUIRED')
    require(program.template=='ATOMIC','PROGRAM_COMPILER_NOT_IMPLEMENTED')
    require(program.rule_snapshot_ref==rules.snapshot_id and program.capability_snapshot_ref==capabilities.snapshot_id,'SNAPSHOT_REFERENCE_MISMATCH')
    require(utc_timestamp(now)<utc_timestamp(program.submit_valid_until),'SUBMISSION_EXPIRED')
    order=program.nodes[0].order
    require(proposal.target_side=='LONG' and order.side=='BUY','REDUCTION_REQUIRES_LOCKED_BOOK')
    require(order.trigger.kind=='NONE' and order.price.kind=='FIXED_LIMIT','ORDER_COMPILER_NOT_IMPLEMENTED')
    require(order.position_side=='BOTH' and scope.venue_leg=='BOTH' and proposal.requested_leverage==1,'CASH_POSITION_SEMANTICS_REQUIRED')
    require(order.stp=='NONE' and not order.reduce_only and order.trading_session is None and order.tokenize is None,'MODIFIER_COMPILER_NOT_IMPLEMENTED')
    require(not order.post_only and order.routing=='STANDARD' and order.iceberg_quantity is None,'MODIFIER_COMPILER_NOT_IMPLEMENTED')
    require('SPOT-LIMIT' in capabilities.capability_ids and 'SPOT-TIF' in capabilities.capability_ids,'FEATURE_NOT_AVAILABLE')
    require(order.tif.kind in rules.allowed_tifs,'INVALID_TIF')
    require(order.quantity.kind==proposal.target_sizing and order.quantity.value==proposal.target_value,'TARGET_SPEC_MISMATCH')
    price=Decimal(order.price.value)
    require(proposal.exit_plan.stop_trigger=='LAST' and proposal.exit_plan.stop_price is not None,'PROTECTION_COMPILER_NOT_IMPLEMENTED')
    require(0<Decimal(proposal.exit_plan.stop_price)<price,'INVALID_STOP_DIRECTION')
    require(utc_timestamp(now)<utc_timestamp(proposal.exit_plan.review_due_at),'EXIT_REVIEW_EXPIRED')
    quantity_on_grid(price,Decimal(rules.tick_size),False)
    value=Decimal(proposal.target_value);step=Decimal(rules.quantity_step)
    target=(quantity_from_notional(value,price,step,allow_round_down) if proposal.target_sizing=='QUOTE_NOTIONAL'
            else quantity_on_grid(value,step,allow_round_down))
    with localcontext() as arithmetic:
        arithmetic.prec=256
        require(target>=Decimal(rules.minimum_quantity) and target*price>=Decimal(rules.minimum_notional),'MINIMUM_ORDER_NOT_MET')
    return AtomicCompilation(original_proposal=proposal,original_program=program,
        normalized_target_quantity=format(target,'f'),sizing_price=order.price.value,order_type='LIMIT',
        limit_price=order.price.value,time_in_force=order.tif.kind,proposal_hash=codec.hash('order-proposal',proposal),
        program_hash=codec.hash('order-program',program),rules_hash=codec.hash('order-rule-snapshot',rules),
        capabilities_hash=expected_capabilities_hash)
