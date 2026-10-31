"""Pure conservative program budgets, never a ledger, grant, delta or permit.

Facts are supplied by trusted readers under the existing collateral/owner lock.
None means missing; an explicitly empty tuple means proven absence. Asset limits
are gross budget capacities BEFORE the supplied positions/orders/ledger holds,
not venue free balances already net of those consumers. Currency conversion is
for exposure reporting only and never makes another asset spendable.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext, Inexact, Rounded, DecimalException
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.intents.program_compilation import ProgramCompilation
from .valuation import Valuation, nonnegative
from .reservations import amounts

D = Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class FeeBound:
    amount: t.Money
    debit: Literal['EXTERNAL_BALANCE', 'FROM_RECEIPT']


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeBudgetFacts:
    node_id: str
    fees: tuple[FeeBound, ...] | None
    margin: tuple[t.Money, ...] | None
    # Required where the compiler cannot resolve CLOSE_ALL/receipt/quote size.
    quantity_upper: t.Quantity | None = None
    price_upper: t.Price | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AtomicGroupProof:
    node_ids: tuple[str, ...]
    evidence_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LockedReceipt:
    source_node_id: str
    target_node_ids: tuple[str, ...]
    evidence_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReceiptObligation:
    source_node_id: str
    target_node_ids: tuple[str, ...]
    asset: str
    minimum_net: t.Quantity
    maximum_net: t.Quantity
    activation_checks: tuple[str, ...] = ('FULL_FILL', 'ACTUAL_NET_RECEIPT', 'LOCKED_FUNDS', 'FEES_AND_LOT_RECHECK')


@dataclass(frozen=True, slots=True, kw_only=True)
class ExistingConsumer:
    consumer_id: str
    scope: t.ScopeKey
    collateral_group_id: str
    kind: Literal['POSITION', 'WORKING', 'UNKNOWN', 'NATIVE_PARENT', 'LOCAL_PARENT', 'LOCAL_CHILD']
    requirements: tuple[t.Money, ...] | None
    gross_exposure: t.Money | None
    evidence_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AllocationEnvelope:
    """Trusted complete cumulative+remaining parent envelope, not a netting hint.

It must dominate the parent AND the sum of all covered children. Ledger holds
are backing for this same envelope, so they count once at the larger bound.
    Positions may join only in the explicitly complete member set. Their gross
    usage is included in the summed member bound, never netted against orders.
"""
    allocation_id: str
    consumer_ids: tuple[str, ...]
    reservation_ids: tuple[str, ...]
    requirements: tuple[t.Money, ...]
    gross_exposure: t.Money
    evidence_id: str
    program_hash: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramBudgetFacts:
    scope: t.ScopeKey
    collateral_group_id: str
    compilation_hash: str
    base_asset: str
    quote_asset: str
    node_facts: tuple[NodeBudgetFacts, ...] | None
    asset_limits: tuple[t.Money, ...] | None
    consumers: tuple[ExistingConsumer, ...] | None
    reservations: tuple[t.ReservationProjection, ...] | None
    allocations: tuple[AllocationEnvelope, ...] | None
    atomic_groups: tuple[AtomicGroupProof, ...] | None
    receipts: tuple[LockedReceipt, ...] | None
    evidence_id: str
    as_of: str
    valid_until: str
    partial_fill_policy: Literal['FULL_FILL_ONLY_ACCEPTED', 'REQUIRE_EACH_FILL'] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramExposure:
    compilation_hash: str
    facts_hash: str
    program_requirements: tuple[t.Money, ...] | None
    additional_requirements: tuple[t.Money, ...] | None
    existing_requirements: tuple[t.Money, ...] | None
    total_requirements: tuple[t.Money, ...] | None
    gross_before: t.Money | None
    gross_program: t.Money | None
    gross_after: t.Money | None
    receipts: tuple[ReceiptObligation, ...]
    obligations: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


def compilation_hash(plan: ProgramCompilation) -> str:
    return canonical_hash(domain_payload(plan))


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _sum(*values):
    result = {}
    for value in values:
        for asset, amount in value.items():
            result[asset] = result.get(asset, D(0)) + amount
    return result


def _max(*values):
    return {asset:max(v.get(asset,D(0)) for v in values) for asset in {a for v in values for a in v}}


def _money(values):
    return tuple(t.Money(amount=value,currency=asset) for asset,value in sorted(values.items()) if value != 0)


def derive_program_exposure(plan: ProgramCompilation, facts: ProgramBudgetFacts, *, now: str,
                            valuation: Valuation | None = None) -> ProgramExposure:
    digest, facts_digest = compilation_hash(plan), canonical_hash(domain_payload(facts))
    obligations = ('LOCKED_TARGET_DELTA_REQUIRED', 'CURRENT_OWNER_AND_RISK_ADMISSION',
                   'RECONCILED_FUNDS_AND_RESERVATIONS', 'NO_RELEASE_OR_PERMIT_CREATED')
    try:
        with localcontext() as arithmetic:
            arithmetic.prec = 256
            arithmetic.traps[Inexact] = arithmetic.traps[Rounded] = True
            return _derive(plan,facts,now,valuation or Valuation(facts.quote_asset),digest,facts_digest,obligations)
    except (ValueError,DecimalException) as error:
        reason = 'ARITHMETIC_PRECISION_UNPROVEN' if isinstance(error,DecimalException) else str(error)
        return ProgramExposure(compilation_hash=digest,facts_hash=facts_digest,program_requirements=None,
            additional_requirements=None,existing_requirements=None,total_requirements=None,
            gross_before=None,gross_program=None,gross_after=None,receipts=(),obligations=obligations,blockers=(reason,))


def _derive(plan,facts,now,valuation,digest,facts_digest,obligations):
    _require(facts.scope==plan.scope and facts.compilation_hash==digest,'FINANCIAL_FACT_BINDING_MISMATCH')
    _require(facts.evidence_id and facts.collateral_group_id and facts.base_asset and facts.quote_asset and facts.base_asset!=facts.quote_asset,'ASSET_PROVENANCE_REQUIRED')
    _require(utc_timestamp(facts.as_of)<=utc_timestamp(now)<utc_timestamp(facts.valid_until),'FINANCIAL_FACTS_STALE')
    for name in ('node_facts','asset_limits','consumers','reservations','allocations','atomic_groups','receipts'):
        _require(getattr(facts,name) is not None,'MISSING_'+name.upper())
    program=plan.original_program
    _require(len({n.original.node_id for n in plan.nodes})==len(plan.nodes),'AMBIGUOUS_COMPILED_NODES')
    _require(tuple(n.original for n in plan.nodes)==program.nodes,'COMPILED_TOPOLOGY_MISMATCH')
    by_id={n.original.node_id:n for n in plan.nodes}
    inputs={f.node_id:f for f in facts.node_facts}
    _require(len(inputs)==len(facts.node_facts) and set(inputs)==set(by_id),'NODE_FINANCIAL_FACTS_INCOMPLETE')
    native=program.execution_mode=='VENUE_NATIVE'
    explicit_groups={frozenset(g.node_ids):g for g in facts.atomic_groups}
    _require(len(explicit_groups)==len(facts.atomic_groups),'DUPLICATE_ATOMIC_PROOF')
    for group,proof in explicit_groups.items():
        _require(len(group)==len(proof.node_ids),'DUPLICATE_ATOMIC_PROOF')
        _require(native and group in {frozenset(g) for g in program.exclusive_groups} and proof.evidence_id,'ATOMICITY_NOT_PROVEN')
    if not native and program.exclusive_groups:
        obligations+=('LOCAL_EXCLUSIVITY_OVERLAP_RESERVED',)
    if program.links:
        _require(facts.partial_fill_policy is not None,'PARTIAL_FILL_POLICY_MISSING')
        _require(facts.partial_fill_policy=='FULL_FILL_ONLY_ACCEPTED','PARTIAL_FILL_PROTECTION_REQUIRES_SEPARATE_PLAN')
        obligations+=('FULL_FILL_ACTIVATION_RECHECK','PARTIAL_FILL_PROTECTION_NOT_IMPLIED')
    if any(n.original.order.price.kind!='FIXED_LIMIT' for n in plan.nodes):
        obligations+=('ENFORCE_EXECUTION_PRICE_ENVELOPE',)

    receipt_by_target={}
    receipts=[]
    for receipt in facts.receipts:
        _require(program.template in ('OPO','OPOCO') and receipt.evidence_id,'RECEIPT_TEMPLATE_OR_PROVENANCE_INVALID')
        _require(receipt.source_node_id in by_id and len(set(receipt.target_node_ids))==len(receipt.target_node_ids),'RECEIPT_TOPOLOGY_INVALID')
        source=by_id[receipt.source_node_id]
        _require(source.original.order.side=='BUY' and source.quantity.kind=='BASE_QUANTITY','RECEIPT_SOURCE_QUANTITY_UNRESOLVED')
        source_input=inputs[receipt.source_node_id]
        _require(source_input.fees is not None,'FEE_BOUND_MISSING:'+receipt.source_node_id)
        fees=sum((f.amount.amount for f in source_input.fees if f.debit=='FROM_RECEIPT' and f.amount.currency==facts.base_asset),D(0))
        upper=D(source.quantity.value);lower=upper-fees
        _require(lower>=0,'RECEIPT_FEE_EXCEEDS_QUANTITY')
        for target in receipt.target_node_ids:
            _require(target in by_id and target not in receipt_by_target,'RECEIPT_ALLOCATION_REUSED')
            node=by_id[target]
            _require(node.quantity.kind=='DERIVED_RECEIPT' and node.quantity.source_node==receipt.source_node_id
                and node.original.order.side=='SELL' and any(e.source==receipt.source_node_id and e.target==target
                and e.condition=='NET_RECEIPT' for e in program.links),'RECEIPT_TOPOLOGY_INVALID')
            receipt_by_target[target]=upper
        _require(set(receipt.target_node_ids)=={n.original.node_id for n in plan.nodes if n.quantity.source_node==receipt.source_node_id},'RECEIPT_TARGETS_INCOMPLETE')
        receipts.append(ReceiptObligation(source_node_id=receipt.source_node_id,target_node_ids=receipt.target_node_ids,
            asset=facts.base_asset,minimum_net=t.Quantity(value=lower,unit=source.quantity_unit),maximum_net=t.Quantity(value=upper,unit=source.quantity_unit)))
    _require(len({r.source_node_id for r in receipts})==len(receipts),'RECEIPT_SOURCE_REUSED')

    node_budgets={}
    for node_id,node in by_id.items():
        info=inputs[node_id]
        _require(node.notional_asset==facts.quote_asset,'QUOTE_ASSET_MISMATCH')
        multiplier=nonnegative(D(node.quantity_multiplier))
        _require(multiplier>0 and node.quantity_unit,'QUANTITY_MULTIPLIER_MISSING')
        if plan.scope.product!=t.Product.USD_M_PERP:
            _require(multiplier==1 and node.quantity_unit==('SHARE' if plan.scope.product==t.Product.US_EQUITY else facts.base_asset),'QUANTITY_UNIT_MISMATCH')
        _require(info.fees is not None,'FEE_BOUND_MISSING:'+node_id)
        external={}
        for fee in info.fees:
            nonnegative(fee.amount.amount)
            _require(fee.amount.currency and fee.debit in ('EXTERNAL_BALANCE','FROM_RECEIPT'),'INVALID_FEE_BOUND')
            if fee.debit=='FROM_RECEIPT':
                _require(plan.scope.product!=t.Product.USD_M_PERP and fee.amount.currency==
                    (facts.base_asset if node.original.order.side=='BUY' else facts.quote_asset),'FEE_RECEIPT_ASSET_MISMATCH')
            else:
                external=_sum(external,{fee.amount.currency:fee.amount.amount})
        quantity=D(node.quantity.value) if node.quantity.kind=='BASE_QUANTITY' else receipt_by_target.get(node_id)
        if info.quantity_upper is not None:
            _require(info.quantity_upper.unit==node.quantity_unit,'QUANTITY_UNIT_MISMATCH')
            nonnegative(info.quantity_upper.value)
            _require(quantity is None or info.quantity_upper.value==quantity,'QUANTITY_BOUND_CONFLICT')
            quantity=info.quantity_upper.value
        if node.quantity.kind=='DERIVED_RECEIPT':
            _require(node_id in receipt_by_target,'LOCKED_NET_RECEIPT_BOUND_MISSING')
        notional=D(node.worst_case_quote_notional) if node.worst_case_quote_notional is not None else None
        fixed_sell=node.original.order.side=='SELL' and node.original.order.price.kind=='FIXED_LIMIT'
        if fixed_sell:
            _require(quantity is not None,'QUANTITY_BOUND_MISSING:'+node_id)
            floor=D(node.original.order.price.value)
            # A historical plan's q*SELL-limit must never acquire new ceiling
            # meaning. It needs a fresh trusted upper, without rewriting it.
            marked='SELL_LIMIT_EXECUTION_UPPER_BOUND' in plan.required_checks
            proven=notional if marked else None
            if proven is not None:
                _require(proven>=quantity*floor*multiplier,'PRICE_BOUND_CONFLICT')
            if info.price_upper is not None:
                _require(info.price_upper.currency==facts.quote_asset,'PRICE_CURRENCY_MISMATCH')
                nonnegative(info.price_upper.value)
                _require(info.price_upper.value>=floor,'PRICE_BOUND_CONFLICT')
                supplied=quantity*info.price_upper.value*multiplier
                _require(proven is None or supplied>=proven,'PRICE_BOUND_CONFLICT')
                proven=supplied
            _require(proven is not None,'SELL_EXECUTION_UPPER_BOUND_MISSING:'+node_id)
            notional=proven
            obligations+=('ENFORCE_EXECUTION_PRICE_ENVELOPE','REVALIDATE_FEES_AND_MARGIN_AT_GROSS_CEILING')
            if not marked:
                obligations+=('HISTORICAL_SELL_BOUND_REVALIDATION',)
        elif notional is None:
            _require(quantity is not None,'QUANTITY_BOUND_MISSING:'+node_id)
            price=D(node.original.order.price.value) if node.original.order.price.kind=='FIXED_LIMIT' else None
            if info.price_upper is not None:
                _require(info.price_upper.currency==facts.quote_asset,'PRICE_CURRENCY_MISMATCH')
                nonnegative(info.price_upper.value)
                _require(price is None or info.price_upper.value>=price,'PRICE_BOUND_CONFLICT')
                price=info.price_upper.value
            _require(price is not None and price>0,'PRICE_BOUND_MISSING:'+node_id)
            notional=quantity*price*multiplier
        nonnegative(notional)
        deducted=sum((fee.amount.amount for fee in info.fees if fee.debit=='FROM_RECEIPT'),D(0))
        if deducted:
            if node.original.order.side=='BUY':
                _require(quantity is not None,'RECEIPT_FEE_QUANTITY_MISSING')
                _require(deducted<=quantity,'RECEIPT_FEE_EXCEEDS_QUANTITY')
            else:
                _require(deducted<=notional,'RECEIPT_FEE_EXCEEDS_NOTIONAL')
            obligations+=('ACTUAL_RECEIPT_FEE_RECHECK',)
        if not fixed_sell and node.quantity.kind=='BASE_QUANTITY' and node.sizing_price_bound is not None:
            _require(notional==quantity*D(node.sizing_price_bound)*multiplier,'COMPILED_NOTIONAL_MISMATCH')
            if info.price_upper is not None:
                _require(info.price_upper.currency==facts.quote_asset and info.price_upper.value==D(node.sizing_price_bound),'PRICE_BOUND_CONFLICT')
        if node.quantity.kind=='QUOTE_NOTIONAL' and node.original.order.side=='SELL':
            _require(quantity is not None and node.sizing_price_bound is not None,'SELL_QUANTITY_BOUND_MISSING:'+node_id)
            _require(quantity*D(node.sizing_price_bound)*multiplier>=notional,'QUOTE_SELL_QUANTITY_UNDERBOUND')
        if plan.scope.product==t.Product.USD_M_PERP:
            _require(info.margin is not None,'MARGIN_BOUND_MISSING:'+node_id)
            principal=amounts(info.margin)
        elif node.original.order.side=='BUY':
            _require(info.margin is None,'CASH_MARGIN_FACT_UNSUPPORTED')
            principal={facts.quote_asset:notional}
        else:
            _require(info.margin is None,'CASH_MARGIN_FACT_UNSUPPORTED')
            _require(quantity is not None,'SELL_QUANTITY_BOUND_MISSING:'+node_id)
            principal={facts.base_asset:quantity}
        node_budgets[node_id]=(principal,external,notional)

    principal,fee_budget={},{}
    program_gross=D(0);used=set()
    for group in program.exclusive_groups:
        if frozenset(group) not in explicit_groups:
            continue  # No native atomicity evidence => overlapping sum.
        _require(not used.intersection(group),'OVERLAPPING_EXCLUSIVE_GROUPS')
        used.update(group)
        principal=_sum(principal,_max(*(node_budgets[n][0] for n in group)))
        fee_budget=_sum(fee_budget,_max(*(node_budgets[n][1] for n in group)))
        program_gross+=max(node_budgets[n][2] for n in group)
    for node_id,(p,f,g) in node_budgets.items():
        if node_id not in used:
            principal=_sum(principal,p);fee_budget=_sum(fee_budget,f);program_gross+=g
    for receipt in receipts:
        # Derived child quantities track the SAME actual net receipt. Only one
        # envelope is credited, even if local OCO legs can overlap. This is a
        # conditional source allocation, never credit to current spendable cash.
        principal[facts.base_asset]-=receipt.maximum_net.value
        _require(principal[facts.base_asset]>=0,'RECEIPT_CREDIT_EXCEEDS_CONTINGENT_REQUIREMENT')
    requirements=_sum(principal,fee_budget)

    existing,prior_gross,covered,covered_gross=_existing(plan,facts,valuation)
    program_value=valuation.convert(t.Money(amount=program_gross,currency=facts.quote_asset),liability=True)
    additional={asset:max(value-covered.get(asset,D(0)),D(0)) for asset,value in requirements.items()}
    if covered:
        obligations+=('EXISTING_PROGRAM_NO_RESUBMISSION','INDEPENDENT_CHILD_ALLOCATION_REQUIRED')
    total=_sum(existing,additional)
    limits=amounts(facts.asset_limits)
    blockers=[]
    for asset,value in total.items():
        if value and asset not in limits: blockers.append('ASSET_CAPACITY_MISSING:'+asset)
        elif value>limits.get(asset,D(0)): blockers.append('ASSET_CAPACITY_EXCEEDED:'+asset)
    money=lambda v:t.Money(amount=v,currency=valuation.currency)
    return ProgramExposure(compilation_hash=digest,facts_hash=facts_digest,program_requirements=_money(requirements),
        additional_requirements=_money(additional),existing_requirements=_money(existing),total_requirements=_money(total),
        gross_before=money(prior_gross),gross_program=program_value,
        gross_after=money(prior_gross+max(program_value.amount-covered_gross,D(0))),receipts=tuple(receipts),
        obligations=tuple(dict.fromkeys(obligations)),blockers=tuple(blockers))


def _existing(plan,facts,valuation):
    consumers={c.consumer_id:c for c in facts.consumers}
    _require(len(consumers)==len(facts.consumers),'DUPLICATE_EXISTING_CONSUMER')
    budgets={};exposure={}
    for key,c in consumers.items():
        _require(c.scope.environment==facts.scope.environment and c.collateral_group_id==facts.collateral_group_id,'CONSUMER_COLLATERAL_GROUP_MISMATCH')
        _require(c.evidence_id and c.kind in ('POSITION','WORKING','UNKNOWN','NATIVE_PARENT','LOCAL_PARENT','LOCAL_CHILD'),'EXISTING_CONSUMER_UNVERIFIED')
        _require(c.requirements is not None and c.gross_exposure is not None,'EXISTING_CONSUMER_BOUND_MISSING:'+key)
        budgets[key]=amounts(c.requirements);nonnegative(c.gross_exposure.amount)
        exposure[key]=valuation.convert(c.gross_exposure,liability=True).amount
    holds={}
    for p in facts.reservations:
        r=p.reservation
        _require(r.reservation_id not in holds,'DUPLICATE_RESERVATION')
        _require(r.environment==facts.scope.environment and r.collateral_group_id==facts.collateral_group_id,'RESERVATION_SCOPE_MISMATCH')
        for value in (p.held,p.reflected,p.release_pending,r.amount):nonnegative(value)
        _require(p.reflected<=p.held<=r.amount and p.release_pending<=p.held,'INVALID_RESERVATION_PROJECTION')
        holds[r.reservation_id]={r.asset:p.held}  # Subsets never add money or grant release.
    total={};gross=D(0);used=set();used_holds=set();covered={};covered_gross=D(0);allocation_ids=set();covered_program=False
    for a in facts.allocations:
        _require(a.evidence_id and a.allocation_id not in allocation_ids,'ALLOCATION_PROOF_AMBIGUOUS')
        allocation_ids.add(a.allocation_id)
        _require(len(set(a.consumer_ids))==len(a.consumer_ids) and len(set(a.reservation_ids))==len(a.reservation_ids),'ALLOCATION_PROOF_AMBIGUOUS')
        _require(set(a.consumer_ids)<=set(consumers) and set(a.reservation_ids)<=set(holds),'ALLOCATION_MEMBER_MISSING')
        _require(not used.intersection(a.consumer_ids) and not used_holds.intersection(a.reservation_ids),'ALLOCATION_DOUBLE_SPEND')
        members=[consumers[k] for k in a.consumer_ids]
        parents=[c for c in members if c.kind in ('NATIVE_PARENT','LOCAL_PARENT')]
        _require(len(parents)==1,'PARENT_ALLOCATION_REQUIRED')
        parent=parents[0]
        children=[c for c in members if c is not parent]
        _require(all(c.scope==parent.scope for c in children),'ALLOCATION_MEMBER_SCOPE_MISMATCH')
        bound=amounts(a.requirements)
        minimum=_max(budgets[parent.consumer_id],_sum(*(budgets[c.consumer_id] for c in children)))
        _require(all(bound.get(asset,D(0))>=value for asset,value in minimum.items()),'ALLOCATION_FUNDING_UNDERBOUND')
        nonnegative(a.gross_exposure.amount)
        value=valuation.convert(a.gross_exposure,liability=True).amount
        _require(value>=max(exposure[parent.consumer_id],sum((exposure[c.consumer_id] for c in children),D(0))),'ALLOCATION_EXPOSURE_UNDERBOUND')
        consumption=_max(bound,_sum(*(holds[k] for k in a.reservation_ids)))
        total=_sum(total,consumption);gross+=value;used.update(a.consumer_ids);used_holds.update(a.reservation_ids)
        if a.program_hash==plan.program_hash:
            _require(parent.scope==plan.scope,'PROGRAM_ALLOCATION_SCOPE_MISMATCH')
            _require(parent.kind==('NATIVE_PARENT' if plan.original_program.execution_mode=='VENUE_NATIVE' else 'LOCAL_PARENT'),'PROGRAM_ALLOCATION_ORIGIN_MISMATCH')
            _require(not covered_program,'PROGRAM_ALLOCATION_AMBIGUOUS')
            covered_program=True;covered=consumption;covered_gross=value
    for key,value in budgets.items():
        if key not in used:total=_sum(total,value);gross+=exposure[key]
    for key,value in holds.items():
        if key not in used_holds:total=_sum(total,value)
    return total,gross,covered,covered_gross
