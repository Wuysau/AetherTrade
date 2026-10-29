"""Pure program target binding through the SAME TargetCompiler arithmetic.

The host must load these facts in its existing collateral/owner transaction.
This module verifies snapshot bindings, not database authority; transaction IDs
and evidence labels supplied by an untrusted caller prove nothing. It creates
no TradeIntent projection, risk decision, reservation, command or permit.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext, Inexact, Rounded, DecimalException
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_snapshots import OrderRuleSnapshot
from .program_compilation import ProgramCompilation,RequestPlan,Timing
from .target_compiler import TargetCompiler,TargetInputs,TargetTerms
from .semantic_validation import require,ProposalRejected,quantity_from_notional,quantity_on_grid,time_key

D=Decimal


@dataclass(frozen=True,slots=True,kw_only=True)
class TargetOwnerState:
    owner_strategy_id:str
    ownership_epoch:int
    policy_revision:int


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeUnitBinding:
    scope:t.ScopeKey
    compilation_hash:str
    rule_revision:int
    native_unit:str
    ledger_unit:Literal['BASE','CONTRACT','SHARE']
    ledger_units_per_native_unit:Decimal
    native_multiplier:Decimal
    ledger_multiplier:Decimal
    evidence:t.EvidenceRef
    valid_until:str


@dataclass(frozen=True,slots=True,kw_only=True)
class TargetNormalizationPrice:
    scope:t.ScopeKey
    compilation_hash:str
    price:t.Price
    basis:Literal['DECLARED_LIMIT','TRUSTED_MARK']
    evidence:t.EvidenceRef
    valid_until:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramTargetFacts:
    transaction_id:str
    compilation_hash:str
    rules:OrderRuleSnapshot
    context:t.DecisionContext
    owner:TargetOwnerState
    collateral_group_id:str
    book_sequence:int
    book_hash:str
    inputs:TargetInputs
    unit_binding:NativeUnitBinding
    normalization_price:TargetNormalizationPrice|None
    unknown_command_ids:tuple[str,...]
    evidence:t.EvidenceRef
    valid_until:str


@dataclass(frozen=True,slots=True,kw_only=True)
class NodeTargetAllocation:
    node_id:str
    phase:Literal['INITIAL','ALGORITHM_ENVELOPE','AFTER_FULL_FILL','AFTER_NET_RECEIPT']
    side:Literal['BUY','SELL']
    native_quantity:t.Quantity|None
    ledger_quantity:t.Quantity|None
    obligations:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class RequestTargetAllocation:
    request_index:int
    original:RequestPlan
    phase:Literal['INITIAL','ALGORITHM_ENVELOPE','AFTER_FULL_FILL','AFTER_NET_RECEIPT']
    initial_node_ids:tuple[str,...]
    deferred_node_ids:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramTargetAllocation:
    transaction_id:str
    compilation_hash:str
    proposal_hash:str
    program_hash:str
    rules_hash:str
    facts_hash:str
    context_hash:str
    book_hash:str
    terms:TargetTerms
    delta:t.DeltaPlan
    nodes:tuple[NodeTargetAllocation,...]
    requests:tuple[RequestTargetAllocation,...]
    obligations:tuple[str,...]
    blockers:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:
        return False


def _digest(record):
    return canonical_hash(domain_payload(record))


def _evidence(ref,scope,now):
    require(ref.environment==scope.environment and bool(ref.id) and bool(ref.source)
        and len(ref.hash)==64 and all(c in '0123456789abcdef' for c in ref.hash)
        and time_key(ref.visible_at)<=time_key(now),'TARGET_SOURCE_EVIDENCE_REQUIRED')


def compile_program_target(plan:ProgramCompilation,book:t.BookSnapshot,facts:ProgramTargetFacts)->ProgramTargetAllocation:
    try:
        with localcontext() as arithmetic:
            arithmetic.prec=256
            arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            return _compile_program_target(plan,book,facts)
    except DecimalException as error:
        raise ProposalRejected('TARGET_UNIT_ARITHMETIC_NOT_EXACT') from error


def _compile_program_target(plan,book,facts):
    scope=plan.scope;now=facts.inputs.clock.utc_now;proposal=plan.original_proposal;program=plan.original_program
    codec=VersionedOrderCodec();digest=_digest(plan);bridge=facts.unit_binding;spec=facts.inputs.instrument
    require(scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),'ENVIRONMENT_DISABLED')
    require(facts.transaction_id and facts.collateral_group_id,'TARGET_TRANSACTION_BINDING_REQUIRED')
    require(proposal.action=='SET_TARGET','PROGRAM_MANAGEMENT_TARGET_UNSUPPORTED')
    require(proposal.program==program and codec.hash('order-proposal',proposal)==plan.proposal_hash
        and codec.hash('order-program',program)==plan.program_hash
        and tuple(n.original for n in plan.nodes)==program.nodes,'PROGRAM_SOURCE_HASH_MISMATCH')
    require(facts.compilation_hash==digest and book.scope==scope
        and facts.book_sequence==book.sequence and facts.book_hash==_digest(book),'TARGET_BOOK_OR_COMPILATION_CHANGED')
    require(facts.inputs.context==facts.context and scope in facts.context.scopes
        and facts.context.environment==scope.environment and facts.context.snapshot_ref.environment==scope.environment
        and bool(facts.context.identity_id) and bool(facts.context.deployment_id) and bool(facts.context.authorization_id)
        and bool(facts.context.decision_run_id) and facts.context.strategy_revision>0,'PROGRAM_TARGET_CONTEXT_MISMATCH')
    require(facts.owner.owner_strategy_id and type(facts.owner.ownership_epoch) is int
        and facts.owner.ownership_epoch==facts.context.ownership_epoch>0
        and type(facts.owner.policy_revision) is int and facts.owner.policy_revision==facts.context.policy_revision>0,
        'PROGRAM_TARGET_OWNER_MISMATCH')
    _evidence(facts.evidence,scope,now);_evidence(bridge.evidence,scope,now)
    _evidence(facts.context.snapshot_ref,scope,now)
    require(time_key(now)<time_key(facts.valid_until) and time_key(now)<time_key(bridge.valid_until),'TARGET_FACTS_EXPIRED')
    rules=facts.rules
    require(codec.hash('order-rule-snapshot',rules)==plan.rules_hash and rules.scope==scope
        and rules.revision==plan.rule_revision==spec.rule_revision==bridge.rule_revision
        and time_key(rules.as_of)<=time_key(now)<time_key(rules.valid_until),'TARGET_RULE_SNAPSHOT_MISMATCH')
    require(bridge.scope==scope and bridge.compilation_hash==digest
        and bridge.ledger_unit=={t.Product.SPOT:'BASE',t.Product.USD_M_PERP:'CONTRACT',t.Product.US_EQUITY:'SHARE'}[scope.product]
        and bridge.ledger_unit==spec.quantity_step.unit==spec.minimum_quantity.unit,'NATIVE_LEDGER_UNIT_BINDING_MISMATCH')
    for value in (bridge.ledger_units_per_native_unit,bridge.native_multiplier,bridge.ledger_multiplier):
        require(isinstance(value,D) and value.is_finite() and value>0,'NATIVE_UNIT_MULTIPLIER_REQUIRED')
    ratio=bridge.ledger_units_per_native_unit
    require(bridge.native_unit!=bridge.ledger_unit or ratio==1,'IDENTICAL_UNIT_SCALE_MISMATCH')
    require(bridge.native_multiplier==ratio*bridge.ledger_multiplier,'NATIVE_UNIT_MULTIPLIER_MISMATCH')
    require(all(n.quantity_unit==bridge.native_unit and D(n.quantity_multiplier)==bridge.native_multiplier
        and n.notional_asset==rules.quote_asset for n in plan.nodes),'COMPILED_NATIVE_UNIT_MISMATCH')
    if scope.product!=t.Product.USD_M_PERP:
        require(ratio==bridge.native_multiplier==bridge.ledger_multiplier==1
            and bridge.native_unit==('SHARE' if scope.product==t.Product.US_EQUITY else rules.base_asset),
            'CASH_NATIVE_UNIT_BINDING_MISMATCH')
    require(spec.instrument_uid==scope.instrument_uid and spec.venue==rules.venue and spec.product==scope.product
        and D(rules.quantity_step)*ratio==spec.quantity_step.value
        and D(rules.minimum_quantity)*ratio==spec.minimum_quantity.value
        and D(rules.tick_size)==spec.tick_size.value and rules.quote_asset==spec.tick_size.currency==spec.quote_currency
        and D(rules.minimum_notional)==spec.minimum_notional.amount and rules.quote_asset==spec.minimum_notional.currency,
        'TARGET_INSTRUMENT_PROJECTION_MISMATCH')
    require(isinstance(facts.unknown_command_ids,tuple) and len(set(facts.unknown_command_ids))==len(facts.unknown_command_ids),
        'UNKNOWN_COMMAND_COVERAGE_REQUIRED')

    incoming={link.target for link in program.links}
    initial=tuple(n for n in plan.nodes if n.original.node_id not in incoming)
    valid_until=[proposal.valid_until,facts.valid_until,bridge.valid_until,rules.valid_until]
    value=D(proposal.target_value)
    if proposal.target_sizing=='BASE_QUANTITY':
        require(facts.normalization_price is None,'UNEXPECTED_TARGET_NORMALIZATION_PRICE')
        target=quantity_on_grid(value*ratio,spec.quantity_step.value,facts.inputs.allow_round_down)
    elif value==0:
        require(facts.normalization_price is None,'UNEXPECTED_TARGET_NORMALIZATION_PRICE')
        target=D(0)
    else:
        normalization=facts.normalization_price
        require(type(normalization) is TargetNormalizationPrice,'TARGET_NORMALIZATION_PRICE_REQUIRED')
        require(normalization.scope==scope and normalization.compilation_hash==digest
            and normalization.price.currency==rules.quote_asset and isinstance(normalization.price.value,D)
            and normalization.price.value.is_finite() and normalization.price.value>0,'TARGET_NORMALIZATION_PRICE_MISMATCH')
        _evidence(normalization.evidence,scope,now)
        require(time_key(now)<time_key(normalization.valid_until),'TARGET_NORMALIZATION_PRICE_EXPIRED')
        limits={n.original.order.price.value for n in initial if n.original.order.price.kind=='FIXED_LIMIT'}
        if limits:
            require(len(limits)==1 and all(n.original.order.price.kind=='FIXED_LIMIT' for n in initial),
                'MULTI_PRICE_TARGET_NORMALIZATION_UNSUPPORTED')
            require(normalization.basis=='DECLARED_LIMIT' and normalization.price.value==D(next(iter(limits))),
                'DECLARED_TARGET_LIMIT_REQUIRED')
        else:
            require(normalization.basis=='TRUSTED_MARK','TRUSTED_TARGET_NORMALIZATION_REQUIRED')
        valid_until.append(normalization.valid_until)
        target=quantity_from_notional(value,normalization.price.value*bridge.ledger_multiplier,
                                     spec.quantity_step.value,facts.inputs.allow_round_down)
    terms=TargetTerms(scope=scope,venue=rules.venue,owner_strategy_id=facts.owner.owner_strategy_id,
        ownership_epoch=facts.owner.ownership_epoch,collateral_group_id=facts.collateral_group_id,
        basis_fingerprint=book.basis_fingerprint,action=proposal.action,target_side=proposal.target_side,
        target=t.Quantity(value=target,unit=bridge.ledger_unit),valid_until=min(valid_until,key=time_key),
        submission_deadline=program.submit_valid_until)
    delta=TargetCompiler(facts.inputs).compile_terms(terms,book)
    blockers=[]
    if delta.cancel_first:blockers.append('TARGET_CANCEL_AND_RECONCILE_REQUIRED')
    if facts.unknown_command_ids or any(o.order.identity.scope==scope and o.command_certainty=='UNKNOWN' for o in book.active_orders):
        blockers.append('UNKNOWN_ORDER_RECONCILIATION_REQUIRED')
    if delta.remaining.value==0:blockers.append('TARGET_ALREADY_SATISFIED' if not delta.cancel_first else 'TARGET_ALLOCATION_WAITING')
    # Existing scalar working-order arithmetic cannot collapse native OCO leaves
    # to a proven group bound. Do not invent a parallel grouped target calculator.
    if program.template=='OCO':blockers.append('EXCLUSIVE_TARGET_ALLOCATION_PROOF_REQUIRED')
    if any(n.quantity.kind=='QUOTE_NOTIONAL' for n in initial):blockers.append('QUOTE_REQUEST_TARGET_ALLOCATION_UNRESOLVED')
    if incoming:
        if proposal.target_side=='FLAT':
            blockers.append('CONTINGENT_FLAT_TARGET_UNSUPPORTED')
        else:
            closing_side='SELL' if proposal.target_side=='LONG' else 'BUY'
            for node in plan.nodes:
                if node.original.node_id not in incoming:continue
                if node.original.order.side!=closing_side:
                    blockers.append('CONTINGENT_NONPROTECTIVE_TARGET_UNSUPPORTED')
                if node.quantity.kind=='BASE_QUANTITY' and D(node.quantity.value)*ratio>delta.target.value.copy_abs():
                    blockers.append('CONTINGENT_QUANTITY_EXCEEDS_TARGET')
    obligations=('LOCKED_FINANCIAL_ADMISSION_REQUIRED','CURRENT_AUTHORIZATION_AND_OWNER_RECHECK',
                 'CURRENT_COMPILATION_AND_RULES_REVALIDATION','SOURCE_QUANTITIES_NOT_RESIZED','NO_PERMIT_CREATED')
    nodes=[];requests=[]
    if not blockers:
        side='BUY' if delta.remaining.value>0 else 'SELL'
        amount=delta.remaining.value.copy_abs()
        total=D(0)
        for node in initial:
            require(node.original.order.side==side,'PROGRAM_NODE_SIDE_REPLAN_REQUIRED:'+node.original.node_id)
            phase='ALGORITHM_ENVELOPE' if program.template in ('TWAP','VP') else 'INITIAL'
            checks=()
            if node.quantity.kind=='BASE_QUANTITY':
                native=t.Quantity(value=D(node.quantity.value),unit=bridge.native_unit)
                ledger=t.Quantity(value=native.value*ratio,unit=bridge.ledger_unit)
            elif node.quantity.kind=='CLOSE_ALL':
                require(len(initial)==1 and proposal.target_side=='FLAT' and delta.working.value==0
                    and amount==delta.actual.value.copy_abs(),'CLOSE_ALL_REQUIRES_FULL_UNENCUMBERED_POSITION')
                native=None;ledger=t.Quantity(value=amount,unit=bridge.ledger_unit)
                checks=('FULL_POSITION_QUANTITY_RECHECK_AT_DISPATCH',)
            else:
                raise ProposalRejected('INITIAL_NODE_QUANTITY_UNRESOLVED')
            total+=ledger.value
            nodes.append(NodeTargetAllocation(node_id=node.original.node_id,phase=phase,side=side,
                native_quantity=native,ledger_quantity=ledger,obligations=checks))
        require(total==amount,'PROGRAM_NODE_QUANTITY_REPLAN_REQUIRED')
        if len(initial)>1:
            require(program.template=='BATCH','MULTI_INITIAL_TARGET_ALLOCATION_UNSUPPORTED')
        for node in plan.nodes:
            if node.original.node_id not in incoming:continue
            require(node.original.role=='PENDING' and node.activation_requires_full_fill,'CONTINGENT_ACTIVATION_UNPROVEN')
            receipt=node.quantity.kind=='DERIVED_RECEIPT'
            require(node.quantity.kind in ('BASE_QUANTITY','DERIVED_RECEIPT'),'CONTINGENT_QUANTITY_UNRESOLVED')
            native=None if receipt else t.Quantity(value=D(node.quantity.value),unit=bridge.native_unit)
            ledger=None if native is None else t.Quantity(value=native.value*ratio,unit=bridge.ledger_unit)
            nodes.append(NodeTargetAllocation(node_id=node.original.node_id,
                phase='AFTER_NET_RECEIPT' if receipt else 'AFTER_FULL_FILL',side=node.original.order.side,
                native_quantity=native,ledger_quantity=ledger,
                obligations=('FULL_FILL_REQUIRED','CONTINGENT_FINANCIAL_AND_TARGET_REVALIDATION',
                    'ACTUAL_NET_RECEIPT_AND_LOT_RECHECK' if receipt else 'PROTECTION_QUANTITY_RECHECK')))
        indexed={n.node_id:n for n in nodes}
        requested=tuple(n for request in plan.requests for n in request.node_ids)
        require(len(requested)==len(set(requested)) and set(requested)==set(indexed),
            'REQUEST_NODE_COVERAGE_AMBIGUOUS')
        for index,request in enumerate(plan.requests):
            require(set(request.node_ids)<=set(indexed),'REQUEST_NODE_BINDING_MISMATCH')
            deferred=tuple(n for n in request.node_ids if indexed[n].phase in ('AFTER_FULL_FILL','AFTER_NET_RECEIPT'))
            phase={Timing.INITIAL:'INITIAL',Timing.AFTER_FULL_FILL:'AFTER_FULL_FILL',
                   Timing.AFTER_NET_RECEIPT:'AFTER_NET_RECEIPT',Timing.ALGORITHM_CHILD:'ALGORITHM_ENVELOPE'}[request.timing]
            requests.append(RequestTargetAllocation(request_index=index,original=request,phase=phase,
                initial_node_ids=tuple(n for n in request.node_ids if n not in deferred),deferred_node_ids=deferred))
        if incoming:
            obligations+=('CONTINGENT_LEGS_NOT_INITIAL_DELTA','PARTIAL_FILL_PROTECTION_NOT_IMPLIED')
            if program.execution_mode=='VENUE_NATIVE':
                obligations+=('ALL_CONTINGENT_BOUNDS_REQUIRED_BEFORE_MOTHER_SEND',)
        if program.template in ('TWAP','VP'):obligations+=('ALGORITHM_CHILD_ALLOCATION_REQUIRES_INDEPENDENT_ADMISSION',)
    return ProgramTargetAllocation(transaction_id=facts.transaction_id,compilation_hash=digest,
        proposal_hash=plan.proposal_hash,program_hash=plan.program_hash,rules_hash=plan.rules_hash,
        facts_hash=_digest(facts),context_hash=_digest(facts.context),book_hash=_digest(book),terms=terms,delta=delta,
        nodes=tuple(nodes),requests=tuple(requests),obligations=obligations,blockers=tuple(dict.fromkeys(blockers)))
