"""Additive lifecycle v3 data. No V2 record is upgraded or reinterpreted.

These records are claims until role-specific, independently authenticated
archive proofs are read by the pure reducer's host. Construction is not proof.
"""
from dataclasses import dataclass
from typing import Literal
from . import types as t
from .canonical import decimal_string,utc_timestamp
from .order_program import amount,identifier
from .order_lifecycle import AssetBudget,Namespace

VERSION='2026-10-29'
ProofRole=Literal['FUNDING_AUTHORITY','PARENT_BINDING','CHILD_MEMBERSHIP','NATIVE_EXCLUSIVITY','LIFECYCLE_EVENT','RECEIPT_ALLOCATION']


def digest_text(value):
    if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Exact SHA256 required')


def exact_time(value):
    utc_timestamp(value)
    seconds,_,fraction=value.removesuffix('Z').removesuffix('+00:00').partition('.')
    return seconds,decimal_string('0.'+(fraction or '0'))


def unique(values):
    if len(set(values))!=len(values):raise ValueError('Duplicate identity')


def reference(ref):
    identifier(ref.id);digest_text(ref.hash);identifier(ref.source);exact_time(ref.visible_at)


@dataclass(frozen=True,slots=True,kw_only=True)
class QuantityTermsV3:
    quantity_unit:str
    position_unit:str
    quantity_to_position:str
    notional_multiplier:str
    base_asset:str
    quote_asset:str
    # Cash base fees are asset amounts, not necessarily position-unit amounts.
    # None on derivatives: collateral fees never subtract position contracts.
    position_asset_per_unit:str|None
    def __post_init__(self):
        for v in (self.quantity_unit,self.position_unit,self.base_asset,self.quote_asset):identifier(v)
        amount(self.quantity_to_position);amount(self.notional_multiplier)
        if self.base_asset==self.quote_asset:raise ValueError('Distinct assets required')
        if self.position_asset_per_unit is not None:amount(self.position_asset_per_unit)


@dataclass(frozen=True,slots=True,kw_only=True)
class NodeAllocationV3:
    node_id:str
    group_id:str
    side:Literal['BUY','SELL']
    origin:Literal['VENUE_GENERATED_CHILD','APPLICATION_GENERATED_CHILD']
    child_mode:Literal['FIXED_LEG','ALGORITHM']
    quantity_mode:Literal['FIXED','NET_RECEIPT']
    quantity_limit:str
    price_upper:str
    activation:Literal['IMMEDIATE','FULL_FILL','NET_RECEIPT']
    source_node_id:str|None
    def __post_init__(self):
        identifier(self.node_id);identifier(self.group_id);amount(self.quantity_limit);amount(self.price_upper)
        if (self.activation=='IMMEDIATE')!=(self.source_node_id is None):raise ValueError('Activation source mismatch')
        if self.source_node_id is not None:identifier(self.source_node_id)
        if (self.quantity_mode=='NET_RECEIPT')!=(self.activation=='NET_RECEIPT'):raise ValueError('Receipt activation required')
        if self.child_mode=='ALGORITHM' and (self.activation!='IMMEDIATE' or self.quantity_mode!='FIXED'):
            raise ValueError('Algorithm allocation must be an immediate bounded pool')


@dataclass(frozen=True,slots=True,kw_only=True)
class AllocationGroupV3:
    group_id:str
    node_ids:tuple[str,...]
    aggregation:Literal['SUM','NATIVE_EXCLUSIVE']
    quantity_limit:str
    notional_limit:str
    def __post_init__(self):
        identifier(self.group_id);unique(self.node_ids)
        if not self.node_ids:raise ValueError('Group members required')
        for v in self.node_ids:identifier(v)
        amount(self.quantity_limit);amount(self.notional_limit)


@dataclass(frozen=True,slots=True,kw_only=True)
class ParentAuthorityV3:
    """Immutable pre-ACK economic authorization, deliberately no venue ID."""
    contract_version:Literal['2026-10-29']
    authority_id:str
    program_id:str
    program_hash:str
    compilation_hash:str
    target_allocation_hash:str
    funding_admission_hash:str
    scope:t.ScopeKey
    venue:Literal['BINANCE']
    namespace:Namespace
    template:Literal['ATOMIC','OCO','OTO','OTOCO','OPO','OPOCO','TWAP','VP','BATCH']
    execution_mode:Literal['VENUE_NATIVE','LOCAL_SYNTHETIC','PAPER_MODEL']
    parent_command_id:str|None
    parent_client_id:str|None
    owner_strategy_id:str
    ownership_epoch:int
    authorization_id:str
    funding_intent_id:str
    reservation_ids:tuple[str,...]
    funding_requirements:tuple[AssetBudget,...]
    units:QuantityTermsV3
    unit_evidence:t.EvidenceRef
    rule_revision:int
    rule_snapshot_hash:str
    adapter_build_digest:str
    nodes:tuple[NodeAllocationV3,...]
    groups:tuple[AllocationGroupV3,...]
    starting_position_quantity:str
    maximum_notional:str
    fee_budgets:tuple[AssetBudget,...]
    submission_valid_until:str
    budget_valid_until:str
    management_policy:Literal['MANAGE_UNTIL_FLAT']
    protection_required:bool
    def __post_init__(self):
        for v in (self.authority_id,self.program_id,self.owner_strategy_id,self.authorization_id,self.funding_intent_id):identifier(v)
        for v in (self.program_hash,self.compilation_hash,self.target_allocation_hash,self.funding_admission_hash,self.rule_snapshot_hash,self.adapter_build_digest):digest_text(v)
        if type(self.ownership_epoch) is not int or self.ownership_epoch<1 or type(self.rule_revision) is not int or self.rule_revision<1:raise ValueError('Positive revisions required')
        native=self.execution_mode=='VENUE_NATIVE'
        if native:
            identifier(self.parent_command_id);identifier(self.parent_client_id)
        elif self.parent_command_id is not None or self.parent_client_id is not None or self.namespace!='ORDINARY':
            raise ValueError('Local activation has no fictitious native mother')
        if not self.reservation_ids or not self.funding_requirements:raise ValueError('Durable funding references required')
        unique(self.reservation_ids)
        for v in self.reservation_ids:identifier(v)
        unique(tuple(v.asset for v in self.funding_requirements));unique(tuple(v.asset for v in self.fee_budgets))
        if not self.nodes or not self.groups or len(self.nodes)>32 or len(self.groups)>32:raise ValueError('Bounded complete allocation graph required')
        unique(tuple(n.node_id for n in self.nodes));unique(tuple(g.group_id for g in self.groups))
        assigned=tuple(n for g in self.groups for n in g.node_ids);unique(assigned)
        nodes={n.node_id:n for n in self.nodes}
        if set(assigned)!=set(nodes):raise ValueError('Groups must partition all nodes')
        for group in self.groups:
            members=[nodes[n] for n in group.node_ids]
            if any(n.group_id!=group.group_id for n in members):raise ValueError('Node/group binding mismatch')
            if any(n.child_mode=='ALGORITHM' for n in members) and (len(members)!=1 or group.aggregation!='SUM'):
                raise ValueError('Algorithm requires one summed allocation pool')
            if group.aggregation=='NATIVE_EXCLUSIVE':
                if not native or self.namespace!='ORDER_LIST' or self.template not in ('OCO','OTOCO','OPOCO') or len(members)!=2:
                    raise ValueError('Native list exclusivity required')
                if any(n.child_mode!='FIXED_LEG' for n in members) or len({n.side for n in members})!=1:
                    raise ValueError('Exclusive fixed legs must share a side')
                if len({amount(n.quantity_limit) for n in members})!=1:raise ValueError('Exclusive leg quantities must match')
        for node in self.nodes:
            if node.source_node_id is not None and node.source_node_id not in nodes:raise ValueError('Activation source missing')
            if not native and node.origin=='VENUE_GENERATED_CHILD':raise ValueError('Local programs cannot claim venue-generated children')
        def visit(node,active):
            if node.node_id in active:raise ValueError('Activation cycle')
            if node.source_node_id:visit(nodes[node.source_node_id],active|{node.node_id})
        for node in self.nodes:visit(node,set())
        if len(self.starting_position_quantity)>64:raise ValueError('Position amount too large')
        decimal_string(self.starting_position_quantity);amount(self.maximum_notional)
        reference(self.unit_evidence)
        if self.unit_evidence.environment!=self.scope.environment:raise ValueError('Unit evidence scope mismatch')
        if self.scope.product==t.Product.USD_M_PERP:
            if self.units.position_asset_per_unit is not None:raise ValueError('Derivative collateral is not position quantity')
        elif self.units.position_asset_per_unit is None or amount(self.units.notional_multiplier)!=1:
            raise ValueError('Explicit cash unit attribution required')
        exact_time(self.submission_valid_until);exact_time(self.budget_valid_until)


@dataclass(frozen=True,slots=True,kw_only=True)
class ParentBindingV3:
    contract_version:Literal['2026-10-29']
    binding_id:str
    authority_hash:str
    scope:t.ScopeKey
    venue:Literal['BINANCE']
    namespace:Namespace
    binding_kind:Literal['VENUE_ACCEPTANCE','LOCAL_PROGRAM_ACTIVATION']
    parent_command_id:str|None
    parent_client_id:str|None
    parent_venue_id:str|None
    bound_at:str
    source_evidence:t.EvidenceRef
    def __post_init__(self):
        identifier(self.binding_id);digest_text(self.authority_hash);exact_time(self.bound_at);reference(self.source_evidence)
        ids=(self.parent_command_id,self.parent_client_id,self.parent_venue_id)
        if self.binding_kind=='VENUE_ACCEPTANCE':
            for v in ids:identifier(v)
        elif any(v is not None for v in ids):raise ValueError('Local activation has no native parent identity')


@dataclass(frozen=True,slots=True,kw_only=True)
class ChildMembershipV3:
    contract_version:Literal['2026-10-29']
    membership_id:str
    authority_hash:str
    binding_hash:str
    scope:t.ScopeKey
    venue:Literal['BINANCE']
    parent_namespace:Namespace
    child_namespace:Literal['ORDINARY']
    node_id:str
    group_id:str
    child_order_id:str
    child_client_id:str|None
    origin:Literal['VENUE_GENERATED_CHILD','APPLICATION_GENERATED_CHILD']
    command_id:str|None
    command_hash:str|None
    durable_record_ref:str|None
    source_evidence:t.EvidenceRef
    def __post_init__(self):
        for v in (self.membership_id,self.node_id,self.group_id,self.child_order_id):identifier(v)
        for v in (self.authority_hash,self.binding_hash):digest_text(v)
        if self.child_client_id is not None:identifier(self.child_client_id)
        if self.origin=='APPLICATION_GENERATED_CHILD':
            identifier(self.command_id);digest_text(self.command_hash);identifier(self.durable_record_ref)
        elif any(v is not None for v in (self.command_id,self.command_hash,self.durable_record_ref)):
            raise ValueError('Venue children are not additional application sends')
        reference(self.source_evidence)


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeExclusivityV3:
    contract_version:Literal['2026-10-29']
    group_id:str
    authority_hash:str
    binding_hash:str
    membership_hash:str
    scope:t.ScopeKey
    venue:Literal['BINANCE']
    namespace:Literal['ORDER_LIST']
    template:Literal['OCO','OTOCO','OPOCO']
    node_ids:tuple[str,...]
    guarantee:Literal['ONE_EXECUTING_MEMBER']
    rule_revision:int
    rule_snapshot_hash:str
    adapter_build_digest:str
    source_evidence:t.EvidenceRef
    def __post_init__(self):
        identifier(self.group_id);unique(self.node_ids)
        if len(self.node_ids)!=2:raise ValueError('Exact native pair required')
        for v in (self.authority_hash,self.binding_hash,self.membership_hash,self.rule_snapshot_hash,self.adapter_build_digest):digest_text(v)
        if type(self.rule_revision) is not int or self.rule_revision<1:raise ValueError('Rule revision required')
        reference(self.source_evidence)


@dataclass(frozen=True,slots=True,kw_only=True)
class FeeV3:
    asset:str
    amount:str
    receipt_effect:Literal['DEDUCT_RECEIVED_ASSET','NO_RECEIPT_DEBIT']
    position_effect:Literal['DEDUCT_OWNED_BASE','NO_POSITION_DEBIT']
    def __post_init__(self):identifier(self.asset);amount(self.amount,zero=True)


@dataclass(frozen=True,slots=True,kw_only=True)
class FillV3:
    native_trade_id:str
    quantity:str
    quantity_unit:str
    price:str
    price_asset:str
    fees:tuple[FeeV3,...]|None
    occurred_at:str
    correction_of:str|None
    def __post_init__(self):
        for v in (self.native_trade_id,self.quantity_unit,self.price_asset):identifier(v)
        amount(self.quantity);amount(self.price);exact_time(self.occurred_at)
        if self.fees is not None:unique(tuple(f.asset for f in self.fees))
        if self.correction_of is not None:identifier(self.correction_of)


@dataclass(frozen=True,slots=True,kw_only=True)
class ChildObservationV3:
    contract_version:Literal['2026-10-29']
    observation_id:str
    membership_id:str
    scope:t.ScopeKey
    venue:Literal['BINANCE']
    parent_namespace:Namespace
    child_namespace:Literal['ORDINARY']
    parent_client_id:str|None
    parent_venue_id:str|None
    child_order_id:str
    child_client_id:str|None
    side:Literal['BUY','SELL']
    status:Literal['PENDING_NEW','SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED','UNKNOWN','CANCEL_PENDING','CANCEL_UNKNOWN']
    quantity_unit:str
    quantity:str|None
    cumulative_quantity:str
    leaves_quantity:str|None
    fills:tuple[FillV3,...]
    report_sequence:int
    occurred_at:str
    source_evidence:t.EvidenceRef
    def __post_init__(self):
        for v in (self.observation_id,self.membership_id,self.child_order_id,self.quantity_unit):identifier(v)
        for v in (self.parent_client_id,self.parent_venue_id,self.child_client_id):
            if v is not None:identifier(v)
        if self.quantity is not None:amount(self.quantity)
        amount(self.cumulative_quantity,zero=True)
        if self.leaves_quantity is not None:amount(self.leaves_quantity,zero=True)
        if type(self.report_sequence) is not int or self.report_sequence<1:raise ValueError('Authenticated report sequence required')
        exact_time(self.occurred_at);reference(self.source_evidence)


@dataclass(frozen=True,slots=True,kw_only=True)
class GroupReportV3:
    group_id:str
    cumulative_quantity:str
    unallocated_quantity:str|None
    def __post_init__(self):
        identifier(self.group_id);amount(self.cumulative_quantity,zero=True)
        if self.unallocated_quantity is not None:amount(self.unallocated_quantity,zero=True)


@dataclass(frozen=True,slots=True,kw_only=True)
class ParentObservationV3:
    contract_version:Literal['2026-10-29']
    observation_id:str
    scope:t.ScopeKey
    venue:Literal['BINANCE']
    namespace:Namespace
    parent_client_id:str|None
    parent_venue_id:str|None
    status:Literal['ACTIVE','SUBMIT_UNKNOWN','CANCEL_PENDING','CANCEL_UNKNOWN','STOPPED','COMPLETED']
    groups:tuple[GroupReportV3,...]
    child_inventory:Literal['COMPLETE','INCOMPLETE']
    child_order_ids:tuple[str,...]
    report_sequence:int
    occurred_at:str
    source_evidence:t.EvidenceRef
    def __post_init__(self):
        identifier(self.observation_id);unique(tuple(g.group_id for g in self.groups));unique(self.child_order_ids)
        for v in (self.parent_client_id,self.parent_venue_id):
            if v is not None:identifier(v)
        if type(self.report_sequence) is not int or self.report_sequence<1:raise ValueError('Authenticated report sequence required')
        exact_time(self.occurred_at);reference(self.source_evidence)


@dataclass(frozen=True,slots=True,kw_only=True)
class ReceiptAllocationV3:
    contract_version:Literal['2026-10-29']
    receipt_id:str
    authority_hash:str
    binding_hash:str
    scope:t.ScopeKey
    source_node_id:str
    target_group_id:str
    source_fill_hash:str
    asset:str
    quantity_unit:str
    gross_received_quantity:str
    receipt_commission_quantity:str
    locked_quantity:str
    pending_quantity:str
    unused_quantity:str
    unused_disposition:Literal['STILL_LOCKED','UNLOCK_REPORTED']
    lot_step:str
    rule_snapshot_hash:str
    occurred_at:str
    source_evidence:t.EvidenceRef
    def __post_init__(self):
        for v in (self.receipt_id,self.source_node_id,self.target_group_id,self.asset,self.quantity_unit):identifier(v)
        for v in (self.authority_hash,self.binding_hash,self.source_fill_hash,self.rule_snapshot_hash):digest_text(v)
        for v in (self.gross_received_quantity,self.receipt_commission_quantity,self.locked_quantity,self.pending_quantity,self.unused_quantity):amount(v,zero=True)
        amount(self.lot_step);exact_time(self.occurred_at);reference(self.source_evidence)


@dataclass(frozen=True,slots=True,kw_only=True)
class LifecycleProofV3:
    """Bytes read from an authenticated role archive, not accepted as an object."""
    contract_version:Literal['2026-10-29']
    proof_id:str
    role:ProofRole
    scope:t.ScopeKey
    subject_hash:str
    context_hash:str
    valid_from:str
    valid_until:str
    source_refs:tuple[t.EvidenceRef,...]
    def __post_init__(self):
        identifier(self.proof_id);digest_text(self.subject_hash);digest_text(self.context_hash)
        if not self.source_refs:raise ValueError('Independent source material required')
        for ref in self.source_refs:reference(ref)
        if exact_time(self.valid_from)>=exact_time(self.valid_until):raise ValueError('Proof validity required')


@dataclass(frozen=True,slots=True,kw_only=True)
class ProvenMembershipV3:
    membership:ChildMembershipV3
    proof_ref:t.EvidenceRef
    def __post_init__(self):reference(self.proof_ref)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProvenExclusivityV3:
    exclusivity:NativeExclusivityV3
    proof_ref:t.EvidenceRef
    def __post_init__(self):reference(self.proof_ref)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProvenReceiptV3:
    receipt:ReceiptAllocationV3
    proof_ref:t.EvidenceRef
    def __post_init__(self):reference(self.proof_ref)


@dataclass(frozen=True,slots=True,kw_only=True)
class ChildStateV3:
    observation:ChildObservationV3
    event_hash:str
    fills:tuple[FillV3,...]
    def __post_init__(self):
        digest_text(self.event_hash);unique(tuple(f.native_trade_id for f in self.fills))


@dataclass(frozen=True,slots=True,kw_only=True)
class EventDigestV3:
    observation_id:str
    payload_hash:str
    kind:Literal['CHILD','PARENT']
    def __post_init__(self):identifier(self.observation_id);digest_text(self.payload_hash)


@dataclass(frozen=True,slots=True,kw_only=True)
class GroupPoolV3:
    group_id:str
    unallocated_quantity:str
    def __post_init__(self):identifier(self.group_id);amount(self.unallocated_quantity,zero=True)


@dataclass(frozen=True,slots=True,kw_only=True)
class GroupProjectionV3:
    group_id:str
    aggregation:Literal['SUM','VERIFIED_NATIVE_EXCLUSIVE']
    cumulative_quantity:str
    reported_remaining_quantity:str|None
    effective_remaining_quantity:str|None
    actual_notional:str|None
    possible_notional:str|None
    executing_node_id:str|None
    unsettled_child_ids:tuple[str,...]
    def __post_init__(self):
        identifier(self.group_id);amount(self.cumulative_quantity,zero=True);unique(self.unsettled_child_ids)
        for v in (self.reported_remaining_quantity,self.effective_remaining_quantity,self.actual_notional,self.possible_notional):
            if v is not None:amount(v,zero=True)
        if self.executing_node_id is not None:identifier(self.executing_node_id)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramStateV3:
    contract_version:Literal['2026-10-29']
    program_id:str
    authority_hash:str
    authority_proof_ref:t.EvidenceRef
    prepared_at:str
    sequence:int
    status:Literal['PREPARED','BOUND','ACTIVE','SUBMIT_UNKNOWN','CANCEL_PENDING','CANCEL_UNKNOWN','STOPPED','COMPLETED','RECONCILIATION_REQUIRED']
    binding:ParentBindingV3|None
    binding_proof_ref:t.EvidenceRef|None
    memberships:tuple[ProvenMembershipV3,...]
    exclusivities:tuple[ProvenExclusivityV3,...]
    receipts:tuple[ProvenReceiptV3,...]
    pools:tuple[GroupPoolV3,...]
    children:tuple[ChildStateV3,...]
    events:tuple[EventDigestV3,...]
    parent_observation:ParentObservationV3|None
    projections:tuple[GroupProjectionV3,...]
    fee_totals:tuple[AssetBudget,...]|None
    net_position_quantity:str|None
    required_protection_quantity:str|None
    protection_status:Literal['UNVERIFIED_DUTY','RECONCILIATION_REQUIRED']
    restriction_codes:tuple[str,...]
    def __post_init__(self):
        identifier(self.program_id);digest_text(self.authority_hash);reference(self.authority_proof_ref);exact_time(self.prepared_at)
        if type(self.sequence) is not int or self.sequence<1:raise ValueError('Positive state sequence required')
        if (self.binding is None)!=(self.binding_proof_ref is None):raise ValueError('Binding proof must be paired')
        if self.binding_proof_ref is not None:reference(self.binding_proof_ref)
        unique(tuple(p.membership.membership_id for p in self.memberships));unique(tuple(p.membership.child_order_id for p in self.memberships))
        unique(tuple(g.exclusivity.group_id for g in self.exclusivities));unique(tuple(r.receipt.target_group_id for r in self.receipts))
        unique(tuple(p.group_id for p in self.pools));unique(tuple(c.observation.child_order_id for c in self.children))
        unique(tuple((e.kind,e.observation_id) for e in self.events));unique(tuple(p.group_id for p in self.projections))
        if self.net_position_quantity is not None:decimal_string(self.net_position_quantity)
        if self.required_protection_quantity is not None:amount(self.required_protection_quantity,zero=True)
        if self.fee_totals is not None:unique(tuple(f.asset for f in self.fee_totals))


@dataclass(frozen=True,slots=True,kw_only=True)
class LifecycleResultV3:
    state:ProgramStateV3|None
    restrictions:tuple[str,...]
    retained:ChildObservationV3|ParentObservationV3|ReceiptAllocationV3|ChildMembershipV3|ParentBindingV3|NativeExclusivityV3|None
    proof_ref:t.EvidenceRef|None
    duplicate:bool=False
    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_released(self)->Literal[False]:return False
