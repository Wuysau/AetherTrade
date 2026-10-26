"""Immutable application contracts; construction conveys no trading authority.

Service implementations must validate evidence, scope and state transitions.
References contain identifiers only, never credential material. Decimal values
are serialized as strings at API boundaries; no implicit currency conversion.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Literal
from .wire import DecisionProposal, TradeIntent, RiskPolicy, ExecutionCommand


class Environment(StrEnum):
    RESEARCH = 'RESEARCH'
    PAPER = 'PAPER'
    TESTNET = 'TESTNET'
    LIVE = 'LIVE'


class Product(StrEnum):
    SPOT = 'SPOT'
    USD_M_PERP = 'USD_M_PERP'
    US_EQUITY = 'US_EQUITY'


class DeploymentState(StrEnum):
    DISABLED = 'DISABLED'
    ENABLED = 'ENABLED'
    PAUSED = 'PAUSED'
    DRAINING = 'DRAINING'


class RiskPermission(StrEnum):
    NORMAL = 'NORMAL'
    NO_NEW_RISK = 'NO_NEW_RISK'
    EXIT_ONLY = 'EXIT_ONLY'
    RECOVERY_REQUIRED = 'RECOVERY_REQUIRED'


class ProcessState(StrEnum):
    BOOTING = 'BOOTING'
    RECONCILING = 'RECONCILING'
    READY = 'READY'
    DEGRADED = 'DEGRADED'
    SHUTTING_DOWN = 'SHUTTING_DOWN'
    STOPPED = 'STOPPED'


class IntentState(StrEnum):
    RECEIVED = 'RECEIVED'
    REJECTED = 'REJECTED'
    EXPIRED_UNSENT = 'EXPIRED_UNSENT'
    VALIDATING = 'VALIDATING'
    REPLAN_REQUIRED = 'REPLAN_REQUIRED'
    RESERVED = 'RESERVED'
    EXECUTING = 'EXECUTING'
    NEEDS_RECONCILIATION = 'NEEDS_RECONCILIATION'
    CANCEL_REQUESTED = 'CANCEL_REQUESTED'
    SATISFIED = 'SATISFIED'


class CommandState(StrEnum):
    DURABLE_READY = 'DURABLE_READY'
    MAY_HAVE_SENT = 'MAY_HAVE_SENT'
    UNKNOWN = 'UNKNOWN'
    NOT_SENT = 'NOT_SENT'
    VENUE_REJECTED = 'VENUE_REJECTED'
    VENUE_CONFIRMED = 'VENUE_CONFIRMED'
    ABORTED_UNSENT = 'ABORTED_UNSENT'


class ProtectionState(StrEnum):
    NOT_REQUIRED = 'NOT_REQUIRED'
    PENDING = 'PENDING'
    VERIFIED_NATIVE = 'VERIFIED_NATIVE'
    VERIFIED_HOSTED = 'VERIFIED_HOSTED'
    GAP = 'GAP'
    REPAIRING = 'REPAIRING'
    EXITING = 'EXITING'
    FLAT_VERIFIED = 'FLAT_VERIFIED'
    MANUAL_REQUIRED = 'MANUAL_REQUIRED'


class ReservationState(StrEnum):
    HELD = 'HELD'
    PARTIALLY_CONSUMED = 'PARTIALLY_CONSUMED'
    VENUE_REFLECTED = 'VENUE_REFLECTED'
    RELEASE_PENDING = 'RELEASE_PENDING'
    RELEASED = 'RELEASED'


class OrderLifecycle(StrEnum):
    INITIALIZED = 'INITIALIZED'
    SUBMITTED = 'SUBMITTED'
    ACCEPTED = 'ACCEPTED'
    PARTIALLY_FILLED = 'PARTIALLY_FILLED'
    FILLED = 'FILLED'
    CANCELED = 'CANCELED'
    EXPIRED = 'EXPIRED'
    REJECTED = 'REJECTED'


class CapabilityStatus(StrEnum):
    UNVERIFIED = 'UNVERIFIED'
    VERIFIED = 'VERIFIED'
    DEGRADED = 'DEGRADED'
    UNAVAILABLE = 'UNAVAILABLE'


class TransferState(StrEnum):
    REQUESTED = 'REQUESTED'
    FREEZE_NEW_RISK = 'FREEZE_NEW_RISK'
    RECONCILE_PENDING = 'RECONCILE_PENDING'
    VERIFY_PROTECTION = 'VERIFY_PROTECTION'
    COMMIT_OWNER_EPOCH = 'COMMIT_OWNER_EPOCH'
    ACTIVE = 'ACTIVE'
    TRANSFER_BLOCKED = 'TRANSFER_BLOCKED'


VenueLeg = Literal['BOTH', 'LONG', 'SHORT']
PendingOperation = Literal['NONE', 'SUBMIT', 'CANCEL', 'AMEND']
CommandCertainty = Literal['NOT_SENT', 'UNKNOWN', 'CONFIRMED']
OutcomeProvenance = Literal['LOCAL_VALIDATION', 'VENUE_EVENT', 'VENUE_QUERY', 'RECONCILIATION_INFERRED', 'LOCAL_POLICY', 'NONE']
ControlAction = Literal['pause_new', 'cancel_entries', 'exit_owned', 'transfer', 'reconcile']
ControlStatus = Literal['QUEUED', 'APPLIED', 'REJECTED', 'IN_PROGRESS']
CheckStatus = Literal['NOT_RUN', 'PASS', 'FAIL', 'BLOCKED']
TransferMode = Literal['TRANSFER_OWNER', 'RETAIN_OWNER', 'EXIT_THEN_SWITCH']


@dataclass(frozen=True, slots=True, kw_only=True)
class ScopeKey:
    environment: Environment
    venue_account_id: str
    product: Product
    instrument_uid: str
    venue_leg: VenueLeg


@dataclass(frozen=True, slots=True, kw_only=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self):
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise ValueError('money requires finite Decimal')
        if not self.currency:
            raise ValueError('currency required')

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money) or self.currency != other.currency:
            raise ValueError('currency mismatch')
        low = min(self.amount.as_tuple().exponent, other.amount.as_tuple().exponent)
        high = max(self.amount.adjusted(), other.amount.adjusted())
        with localcontext() as ctx:
            ctx.prec = max(1, high - low + 2)
            return Money(amount=self.amount + other.amount, currency=self.currency)


@dataclass(frozen=True, slots=True, kw_only=True)
class Price:
    value: Decimal
    currency: str

    def __post_init__(self):
        if not isinstance(self.value, Decimal) or not self.value.is_finite() or self.value <= 0:
            raise ValueError('price requires positive finite Decimal')
        if not self.currency:
            raise ValueError('currency required')


@dataclass(frozen=True, slots=True, kw_only=True)
class Quantity:
    value: Decimal
    unit: str

    def __post_init__(self):
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError('quantity requires finite Decimal')
        if not self.unit:
            raise ValueError('unit required')


@dataclass(frozen=True, slots=True, kw_only=True)
class ClockReading:
    utc_now: str
    monotonic_ns: int
    clock_health: Literal['HEALTHY', 'DEGRADED', 'UNKNOWN']


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRef:
    id: str
    hash: str
    environment: Environment
    source: str
    visible_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotRef(EvidenceRef):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryRef(EvidenceRef):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class EventRef(EvidenceRef):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ActorContext:
    identity_id: str
    environment: Environment
    deployment_id: str
    authorization_id: str
    policy_revision: int
    ownership_epoch: int
    scopes: tuple[ScopeKey, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionContext(ActorContext):
    strategy_revision: int
    decision_run_id: str
    snapshot_ref: SnapshotRef


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlContext(ActorContext):
    expected_sequence: int
    idempotency_key: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CredentialRef:
    id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountIdentityEvidence:
    venue_account_id: str
    venue: str
    product: Product
    environment: Environment
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueAccount:
    venue_account_id: str
    venue: str
    environment: Environment
    products: tuple[Product, ...]
    credential_refs: tuple[CredentialRef, ...]
    identity_evidence: AccountIdentityEvidence


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionShard:
    environment: Environment
    venue_account_id: str
    product: Product
    node_id: str
    execution_epoch: int


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnerLease:
    shard: ExecutionShard
    instance_id: str
    expires_at: str
    deadline_monotonic_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalFenceProof:
    shard: ExecutionShard
    old_instance_id: str
    method: Literal['KEY_REVOKED', 'NETWORK_BLOCKED', 'PROCESS_TERMINATED']
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildEnvironment:
    os: str
    arch: str
    python_abi: str
    dependency_digest: str
    image_digest: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateSpec:
    candidate_id: str
    review_ref: str
    deployment_ref: str
    source_digest: str
    build_environment: BuildEnvironment


@dataclass(frozen=True, slots=True, kw_only=True)
class QualifiedBuild:
    candidate: CandidateSpec
    artifact_digest: str
    probe_results: tuple[AcceptanceResult, ...]
    qualification_ref: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class CompatibilityEntry:
    port: str
    native_api: str
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class CompatibilityMap:
    build_digest: str
    entries: tuple[CompatibilityEntry, ...]
    send_path_inventory: tuple[str, ...]
    uncovered_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class QualificationReport:
    build_digest: str
    market: Product
    results: tuple[AcceptanceResult, ...]
    unsatisfied_gates: tuple[str, ...]
    live_enabled: Literal[False] = False

    def __post_init__(self):
        if self.live_enabled is not False:
            raise ValueError('qualification reports do not enable LIVE')


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityProfile:
    scope: ScopeKey
    build_digest: str
    status: CapabilityStatus
    capabilities: tuple[str, ...]
    limitations: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    valid_from: str
    valid_until: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityQuery:
    scope: ScopeKey
    requested: tuple[str, ...]
    platform: CapabilityProfile
    adapter: CapabilityProfile
    account: CapabilityProfile
    authorization: Authorization


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityDecision:
    status: CapabilityStatus
    allowed: tuple[str, ...]
    rejection_codes: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueCapabilityEvidence:
    scope: ScopeKey
    build_digest: str
    query_scope: str
    source: str
    session_id: str
    coverage: Coverage
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StocksProbeInput:
    evidence: VenueCapabilityEvidence
    instrument: InstrumentSpec
    requested_capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class InstrumentSpec:
    instrument_uid: str
    venue: str
    product: Product
    native_symbol: str
    tick_size: Price
    quantity_step: Quantity
    minimum_quantity: Quantity
    minimum_notional: Money
    rule_revision: int
    quote_currency: str
    settlement_currency: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionState:
    instrument_uid: str
    session_id: str
    state: Literal['OPEN', 'CLOSED', 'HALTED', 'UNKNOWN']
    observed_at: str
    next_open_at: str | None
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class Coverage:
    scope: ScopeKey
    query_succeeded: bool
    pagination_complete: bool
    supported: bool
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Quote:
    bid: Price | None
    ask: Price | None
    last: Price | None
    occurred_at: str
    received_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotPacket:
    ref: SnapshotRef
    scope: ScopeKey
    quote: Quote
    private_refs: tuple[EvidenceRef, ...]
    economic_refs: tuple[EvidenceRef, ...]
    published_at: str
    revision_visible_at: str
    coverage: Coverage


@dataclass(frozen=True, slots=True, kw_only=True)
class DataVerdict:
    usable: bool
    age_ms: int
    missing: tuple[str, ...]
    rejection_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class BookSnapshot:
    scope: ScopeKey
    positions: tuple[ManagedPosition, ...]
    active_orders: tuple[OrderProjection, ...]
    reservations: tuple[ReservationProjection, ...]
    balances: tuple[Money, ...]
    sequence: int
    basis_fingerprint: str
    coverage: Coverage
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Authorization:
    authorization_id: str
    owner_strategy_id: str
    environment: Environment
    deployment_id: str
    scopes: tuple[ScopeKey, ...]
    maximum_capital: Money
    policy_revision: int
    protection_options: tuple[Literal['NATIVE_REQUIRED', 'HOSTED_ALLOWED_CASH_ONLY'], ...]
    model_cost_budget: Money
    valid_from: str
    expires_at: str
    revoked_at: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorizationDecision:
    allowed: bool
    authorization_id: str
    rejection_codes: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskDecision:
    risk_decision_id: str
    intent_id: str
    verdict: Literal['ALLOW', 'DENY', 'REPLAN_REQUIRED']
    policy_revision: int
    evaluated_at: str
    expires_at: str
    snapshot_ref: SnapshotRef
    permitted_quantity: Quantity
    permitted_leverage: int
    reserved_assets: tuple[Money, ...]
    estimated_loss: Money
    fees_assumption: Money
    aggregate_exposure_after: ExposureReport
    rejection_codes: tuple[str, ...]
    evidence_completeness: Coverage


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskPolicyDraft:
    policy: RiskPolicy
    expected_revision: int
    author_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskAlert:
    alert_id: str
    scope: ScopeKey
    cause: str
    evidence: tuple[EvidenceRef, ...]
    occurred_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskLock:
    lock_id: str
    scope: ScopeKey
    cause: str
    state: Literal['ACTIVE', 'RESOLVED']
    permission: RiskPermission
    required_evidence: tuple[str, ...]
    raised_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolutionEvidence:
    lock_id: str
    scope: ScopeKey
    evidence: tuple[EvidenceRef, ...]
    authorization_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class LockResolution:
    lock_id: str
    resolved: bool
    remaining_locks: tuple[RiskLock, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeltaPlan:
    scope: ScopeKey
    target: Quantity
    actual: Quantity
    working: Quantity
    remaining: Quantity
    classification: Literal['INCREASE', 'REDUCE', 'NONE', 'REVERSAL_REQUIRES_FLAT']
    cancel_first: tuple[OwnedOrderRef, ...]

    @property
    def submit_quantity(self) -> str:
        """Lossless remaining delta for inspection; not an execution permit."""
        return format(self.remaining.value, 'f')


@dataclass(frozen=True, slots=True, kw_only=True)
class ExposureGroup:
    group_id: str
    gross: Money
    net: Money


@dataclass(frozen=True, slots=True, kw_only=True)
class ExposureReport:
    gross: Money
    net: Money
    groups: tuple[ExposureGroup, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CapitalReservation:
    reservation_id: str
    environment: Environment
    collateral_group_id: str
    venue_account_id: str
    owner_strategy_id: str
    intent_id: str
    asset: str
    amount: Decimal
    reflected_amount: Decimal
    state: ReservationState
    created_at: str
    expires_at: str
    command_refs: tuple[str, ...]
    evidence_watermark: str


Reservation = CapitalReservation


@dataclass(frozen=True, slots=True, kw_only=True)
class ReservationProjection:
    reservation: CapitalReservation
    held: Decimal
    reflected: Decimal
    release_pending: Decimal
    sequence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ReservationOutcome:
    accepted: bool
    reservations: tuple[ReservationProjection, ...]
    book_sequence: int
    rejection_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReservationEvidence:
    reservation_id: str
    venue_freeze_ref: EvidenceRef
    reflected_amount: Decimal
    command_certainty: CommandCertainty
    terminal_verified: bool
    watermark: str


@dataclass(frozen=True, slots=True, kw_only=True)
class EquityPoint:
    at: str
    equity: Money
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class Cashflow:
    at: str
    amount: Money
    classification: Literal['EXTERNAL', 'INTERNAL', 'FEE', 'FUNDING', 'DIVIDEND']
    internal_pair_id: str | None
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskDayResult:
    risk_day: str
    timezone: str
    adjusted_pnl: Money | None
    high_water_mark: Money | None
    missing: tuple[str, ...]

    @property
    def trading_pnl(self) -> str | None:
        return None if self.adjusted_pnl is None else format(self.adjusted_pnl.amount, 'f')


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalReceipt:
    receipt_id: str
    status: IntentState
    reason: str | None
    existing_result_ref: str | None
    intent_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandReceipt:
    receipt_id: str
    command_id: str
    status: CommandState
    reason: str | None
    existing_result_ref: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DurablePermit:
    durable_record_ref: str
    command_id: str
    payload_hash: str
    scope: ScopeKey
    ownership_epoch: int
    execution_epoch: int
    policy_revision: int
    expires_at: str
    deadline_monotonic_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class EmergencyRecord:
    record_id: str
    command: ExecutionCommand
    context: EmergencyContext
    prior_record_hash: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderIdentity:
    scope: ScopeKey
    logical_client_order_id: str
    native_client_order_id: str
    venue_client_order_id: str
    venue_order_id: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnedOrderRef:
    identity: OrderIdentity
    owner_strategy_id: str
    ownership_epoch: int


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderProjection:
    order: OwnedOrderRef
    lifecycle_state: OrderLifecycle
    pending_operation: PendingOperation
    command_certainty: CommandCertainty
    outcome_provenance: OutcomeProvenance
    filled_quantity: Quantity
    leaves_quantity: Quantity
    lifecycle_revision: int
    sequence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RawVenueMessage:
    scope: ScopeKey
    source: str
    payload_hash: str
    raw_payload_ref: EvidenceRef
    received_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueEvent:
    event_id: str
    source: str
    source_event_id: str | None
    source_sequence: str | None
    account_id: str
    instrument_uid: str
    order_refs: tuple[OrderIdentity, ...]
    occurred_at: str
    received_at: str
    processed_at: str
    payload_hash: str
    raw_payload_ref: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueReport:
    coverage: Coverage
    orders: tuple[OrderProjection, ...]
    positions: tuple[ManagedPosition, ...]
    balances: tuple[Money, ...]
    fills: tuple[Fill, ...]
    evidence: tuple[EvidenceRef, ...]
    next_cursor: str | None
    as_of: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Fill:
    fill_id: str
    venue: str
    scope: ScopeKey
    native_trade_id: str | None
    order: OrderIdentity
    quantity: Quantity
    price: Price
    fees: tuple[Money, ...]
    occurred_at: str
    correction_of: str | None
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class FillReceipt:
    fill_id: str
    recorded: bool
    duplicate_of: str | None
    event_ref: EventRef | None


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceReceipt:
    event_id: str
    accepted: bool
    reason: str | None
    raw_ref: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    event_id: str
    scope: ScopeKey
    aggregate_id: str
    revision: int
    occurred_at: str
    evidence: tuple[EvidenceRef, ...]
    payload: Fill | OrderProjection | RiskLock | ManagedPosition | ReservationProjection


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionUpdate:
    projection: OrderProjection
    events: tuple[DomainEvent, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class QueryBudget:
    maximum_requests: int
    window_seconds: int
    minimum_interval_ms: int
    retry_after: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconciliationCase:
    case_id: str
    scope: ScopeKey
    command_ids: tuple[str, ...]
    position_ids: tuple[str, ...]
    missing: tuple[str, ...]
    query_budget: QueryBudget
    attempts: int
    next_query_at: str
    state: Literal['OPEN', 'WAITING', 'RESOLVED']


@dataclass(frozen=True, slots=True, kw_only=True)
class ReconciliationResult:
    case_id: str
    resolved: bool
    missing: tuple[str, ...]
    residual: Quantity | None
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryChecklist:
    scope: ScopeKey
    unique_process: EvidenceRef | None
    commands: EvidenceRef | None
    funds: EvidenceRef | None
    private_stream: EvidenceRef | None
    positions: EvidenceRef | None
    protection: EvidenceRef | None
    authorization: EvidenceRef | None
    unresolved_cases: tuple[ReconciliationCase, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryVerdict:
    ready: bool
    missing: tuple[str, ...]
    risk_locks: tuple[RiskLock, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ChildPolicy:
    expires_at: str
    slice_quantity: Quantity
    maximum_children: int
    compensation_policy: str
    mutable_fields: tuple[Literal['quantity', 'price', 'trigger_price'], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AmendRequest:
    target_order: OwnedOrderRef
    quantity: Quantity | None
    price: Price | None
    trigger_price: Price | None
    expected_revision: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagedPosition:
    position_id: str
    scope: ScopeKey
    actual_quantity: Quantity
    owner_strategy_id: str | None
    owner_book_id: str
    ownership_epoch: int
    cost_evidence: Coverage
    realized_pnl: Money | None
    unrealized_valuation: Money | None
    active_exit_plan_id: str | None
    risk_locks: tuple[RiskLock, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtectionPlan:
    plan_id: str
    mode: Literal['NATIVE', 'HOSTED']
    scope: ScopeKey
    coverage_quantity: Quantity
    stop_price: Price
    stop_trigger: Literal['LAST', 'MARK', 'BID_ASK']
    native_order_ids: tuple[OwnedOrderRef, ...]
    monitor_id: str | None
    state: ProtectionState
    last_verified_at: str | None
    coverage_gap_since: str | None
    replacement_revision: int
    on_gap_policy: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtectionActionPlan:
    plan: ProtectionPlan
    commands: tuple[ExecutionCommand, ...]
    locks: tuple[RiskLock, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtectionVerdict:
    state: ProtectionState
    covered_quantity: Quantity
    missing: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class EmergencyContext:
    context: ControlContext
    reducible_quantity: Quantity
    quantity_evidence: EvidenceRef
    allowed_order_types: tuple[Literal['MARKET', 'LIMIT'], ...]
    maximum_slippage_bps: Decimal
    expires_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExitReceipt:
    position_id: str
    state: Literal['EXITING', 'EXIT_PENDING_MARKET', 'FLAT_VERIFIED', 'MANUAL_REQUIRED', 'NONTRADABLE_RESIDUAL']
    command_ids: tuple[str, ...]
    incomplete_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class TransferRequest:
    transfer_id: str
    scope: ScopeKey
    mode: TransferMode
    old_owner: str
    new_owner: str
    ownership_epoch: int


@dataclass(frozen=True, slots=True, kw_only=True)
class TransferReceipt:
    transfer_id: str
    state: TransferState
    reason: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class TransferEvidence:
    transfer_id: str
    durable_step_refs: tuple[EvidenceRef, ...]
    reconciliation: ReconciliationResult
    protection: ProtectionVerdict
    exit: ExitReceipt | None


@dataclass(frozen=True, slots=True, kw_only=True)
class TransferProjection:
    request: TransferRequest
    state: TransferState
    ownership_epoch: int
    durable_steps: tuple[EvidenceRef, ...]
    blocked_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalChange:
    scope: ScopeKey
    kind: Literal['ORDER', 'QUANTITY', 'MODE']
    evidence: EvidenceRef
    observed_quantity: Quantity | None


@dataclass(frozen=True, slots=True, kw_only=True)
class InterventionCase:
    case_id: str
    change: ExternalChange
    decision: Literal['PENDING', 'RETAIN', 'TAKEOVER', 'RELINQUISH']
    lock: RiskLock


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionSlot:
    strategy_id: str
    instrument_or_portfolio_id: str
    decision_epoch: int
    expires_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionTrigger:
    trigger_id: str
    slot: DecisionSlot
    priority: int
    deduplication_key: str
    evidence: EventRef


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduleReceipt:
    job_id: str
    coalesced: bool
    reason: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SlotVerdict:
    valid: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelCallRequest:
    request_id: str
    provider: str
    model: str
    task: str
    maximum_cost: Money
    snapshot_refs: tuple[SnapshotRef, ...]
    expires_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CostPermit:
    permit_id: str
    request_id: str
    reserved_cost: Money
    expires_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionJob:
    job_id: str
    slot: DecisionSlot
    context: DecisionContext
    request: ModelCallRequest
    cost_permit: CostPermit


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionBatch:
    job_id: str
    proposals: tuple[DecisionProposal, ...]
    cost: Money
    evidence: tuple[EvidenceRef, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryQuery:
    scope: ScopeKey
    start: str
    end: str
    cursor: str | None
    limit: int


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryPage:
    snapshots: tuple[SnapshotPacket, ...]
    evidence: tuple[EvidenceRef, ...]
    next_cursor: str | None
    coverage: Coverage


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleCondition:
    operator: Literal['ALL', 'ANY', 'NOT', 'GT', 'GE', 'LT', 'LE', 'EQ']
    input_ref: str | None
    threshold: Decimal | None
    children: tuple[RuleCondition, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleGraph:
    rule_id: str
    revision: int
    instrument_ref: str
    condition: RuleCondition
    trigger: Literal['EDGE', 'CONTINUOUS']
    window_seconds: int
    closed_bar_only: bool
    cooldown_seconds: int
    sizing: Literal['QUOTE_NOTIONAL', 'BASE_QUANTITY']
    target_value: Decimal
    exit_priority: tuple[str, ...]
    missing_data: Literal['HOLD', 'REJECT']


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleDraftContext:
    context: DecisionContext
    allowed_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleDraft:
    graph: RuleGraph | None
    ambiguities: tuple[str, ...]
    source_ref: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class CompiledRule:
    graph: RuleGraph
    hash: str
    compiler_revision: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleState:
    previous_condition: bool | None
    last_triggered_at: str | None
    last_bar_id: str | None
    revision: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleEvaluation:
    proposal: DecisionProposal | None
    action: Literal['PROPOSAL', 'HOLD', 'REJECT']
    state: RuleState
    reasons: tuple[str, ...]

    @property
    def proposal_hash(self) -> str | None:
        from .wire import ContractCodec
        return None if self.proposal is None else ContractCodec().hash(self.proposal)


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleArtifact:
    hash: str
    artifact_ref: str
    dependency_lock_hash: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RuleInput:
    snapshot: SnapshotPacket
    state: RuleState


@dataclass(frozen=True, slots=True, kw_only=True)
class SandboxLimits:
    cpu_ms: int
    memory_bytes: int
    output_bytes: int
    network: Literal['DENY']
    credentials: Literal['NONE']


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyChange:
    strategy_id: str
    expected_revision: int
    artifact: RuleArtifact
    review_ref: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleaseCandidate:
    candidate_id: str
    change: StrategyChange
    evaluation_refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleaseApproval:
    candidate_id: str
    authorization_id: str
    actor: ActorContext
    approved_at: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DeploymentRevision:
    deployment_id: str
    revision: int
    candidate: ReleaseCandidate
    approval: ReleaseApproval
    rollback_revision: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryEntry:
    id: str
    environment: Environment
    classification: Literal['FACT', 'INFERENCE', 'EXPIRED']
    text: str
    evidence: tuple[EvidenceRef, ...]
    visible_at: str
    expires_at: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CompositionStage:
    stage_id: str
    responsibility: Literal['RESEARCH', 'ENTRY', 'EXIT', 'PROTECTION']
    manager_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionComposition:
    composition_id: str
    revision: int
    stages: tuple[CompositionStage, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CompiledComposition:
    policy: DecisionComposition
    hash: str
    ordinary_exit_owner: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionSignal:
    stage_id: str
    scope: ScopeKey
    proposal: DecisionProposal
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetRef:
    id: str
    hash: str
    environment: Environment
    start: str
    end: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentConfig:
    mode: Literal['REPLAY_OUTPUTS', 'REINFER']
    build_digest: str
    config_hash: str
    model_fingerprint: str
    proposal_refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentReport:
    run_id: str
    config: ExperimentConfig
    dataset: DatasetRef
    metrics: MetricReport
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperRunConfig:
    environment: Literal[Environment.PAPER]
    virtual_capital: tuple[Money, ...]
    build_digest: str
    config_hash: str
    dataset: DatasetRef
    isolation_ref: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class RunReceipt:
    run_id: str
    status: Literal['QUEUED', 'RUNNING', 'COMPLETED', 'FAILED']
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class EventRange:
    scope: ScopeKey
    first_sequence: int
    last_sequence: int
    events: tuple[DomainEvent, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricAssumptions:
    valuation_currency: str
    fees: tuple[Money, ...]
    funding: tuple[Money, ...]
    model_costs: tuple[Money, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricValue:
    name: str
    value: Decimal | None
    unit: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricReport:
    metrics: tuple[MetricValue, ...]
    assumptions: MetricAssumptions
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountQuery:
    scope: ScopeKey
    session_id: str
    as_of: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CorporateAction:
    action_id: str
    scope: ScopeKey
    kind: Literal['SPLIT', 'DIVIDEND', 'SYMBOL_CHANGE', 'DELISTING']
    effective_at: str
    ratio: Decimal | None
    cashflow: Cashflow | None
    evidence: EvidenceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class EquityAdjustment:
    action_id: str
    quantity_before: Quantity
    quantity_after: Quantity
    cashflows: tuple[Cashflow, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlCommandRequest:
    action: ControlAction
    scope: ScopeKey
    expected_revision: int
    ownership_epoch: int
    transfer: TransferRequest | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlCommandReceipt:
    command_id: str
    status: ControlStatus
    reason: str | None
    existing_result_ref: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlCommandProjection:
    receipt: ControlCommandReceipt
    request: ControlCommandRequest
    payload_hash: str
    idempotency_key: str
    sequence: int
    incomplete_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StreamCursor:
    scope: ScopeKey
    sequence: int
    stream_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionQueryRequest:
    scope: ScopeKey
    resource: Literal['positions', 'orders', 'risk-locks', 'intents', 'control-commands']
    owner_strategy_id: str | None
    cursor: StreamCursor | None
    limit: int
    maximum_age_ms: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class FreshnessView:
    as_of: str
    age_ms: int
    stale: bool
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionProjection:
    position: ManagedPosition
    protection: ProtectionPlan | None
    freshness: FreshnessView
    sequence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionPage:
    scope: ScopeKey
    positions: tuple[PositionProjection, ...]
    orders: tuple[OrderProjection, ...]
    risk_locks: tuple[RiskLock, ...]
    controls: tuple[ControlCommandProjection, ...]
    intents: tuple[TradeIntent, ...]
    next_cursor: StreamCursor | None
    freshness: FreshnessView


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionEvent:
    cursor: StreamCursor
    event: DomainEvent


@dataclass(frozen=True, slots=True, kw_only=True)
class EventPage:
    events: tuple[ProjectionEvent, ...]
    next_cursor: StreamCursor
    gap: bool
    requires_snapshot: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionRef:
    session_id: str
    identity_id: str
    environment: Environment
    expires_at: str
    authorization_revision: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcessHealth:
    process_id: str
    state: ProcessState
    observed_at: str
    faults: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SystemHealth:
    processes: tuple[ProcessHealth, ...]
    risk_permission: RiskPermission
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class NotificationReceipt:
    alert_id: str
    status: Literal['QUEUED', 'DELIVERED', 'FAILED']
    delivered_at: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeploymentManifest:
    deployment_id: str
    artifact_digest: str
    config_digest: str
    environment: Environment
    processes: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DeploymentEvidence:
    manifest: DeploymentManifest
    evidence: tuple[EvidenceRef, ...]
    faults: tuple[str, ...]
    status: CheckStatus


@dataclass(frozen=True, slots=True, kw_only=True)
class HostExit:
    instance_id: str
    exit_code: int
    unresolved: tuple[ReconciliationCase, ...]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class TakeoverVerdict:
    allowed: bool
    missing: tuple[str, ...]
    fence_proof: ExternalFenceProof | None


@dataclass(frozen=True, slots=True, kw_only=True)
class VerificationEnvironment:
    environment_id: str
    build_digest: str
    migration_digest: str
    config_digest: str
    platform: BuildEnvironment
    evidence_layer: Literal['CONTRACT', 'OFFLINE', 'NATIVE', 'PLATFORM']


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptanceResult:
    case_id: str
    environment: VerificationEnvironment
    status: CheckStatus
    injection: tuple[EvidenceRef, ...]
    actual_events: tuple[EventRef, ...]
    before_book: EvidenceRef | None
    after_book: EvidenceRef | None
    command_refs: tuple[EvidenceRef, ...]
    reason: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeploymentRequest:
    strategy_revision: int
    authorization_id: str
    environment: Environment
    expected_revision: int


@dataclass(frozen=True, slots=True, kw_only=True)
class IntentProjection:
    intent: TradeIntent
    state: IntentState
    risk: RiskDecision | None
    commands: tuple[ExecutionCommand, ...]
    fills: tuple[Fill, ...]
    protection: tuple[ProtectionPlan, ...]
    freshness: FreshnessView


@dataclass(frozen=True, slots=True, kw_only=True)
class ApiError:
    reason_code: str
    message: str
    correctable_fields: tuple[str, ...]
    allowed_actions: tuple[ControlAction, ...]
