// Generated from the application contract schemas in /contracts.

export interface DecisionProposalTarget {
  readonly side: "LONG" | "SHORT" | "FLAT";
  readonly sizing: "QUOTE_NOTIONAL" | "BASE_QUANTITY";
  readonly value: string;
}

export interface DecisionProposalEntry {
  readonly order_preference: "MARKET" | "LIMIT";
  readonly limit_price: string | null;
  readonly maximum_entry_slippage_bps: string;
  readonly entry_deadline: string;
  readonly allow_partial: boolean;
}

export interface DecisionProposalExitPlan {
  readonly stop_price: string | null;
  readonly stop_trigger: "LAST" | "MARK" | "BID_ASK";
  readonly review_due_at: string;
  readonly maximum_holding_seconds: number;
  readonly thesis_invalidation: string;
}

export interface DecisionProposalRationale {
  readonly summary: string;
  readonly counter_evidence: ReadonlyArray<string>;
  readonly missing_information: ReadonlyArray<string>;
}

export interface DecisionProposal {
  readonly contract_version: "2026-10-20";
  readonly proposal_id: string;
  readonly instrument_ref: string;
  readonly action: "SET_TARGET" | "UPDATE_EXIT" | "HOLD";
  readonly target: DecisionProposalTarget;
  readonly requested_leverage?: number;
  readonly entry: DecisionProposalEntry;
  readonly exit_plan: DecisionProposalExitPlan;
  readonly evidence_refs: ReadonlyArray<string>;
  readonly rationale: DecisionProposalRationale;
  readonly valid_until: string;
}

export interface TradeIntentExecution {
  readonly order_preference: "MARKET" | "LIMIT";
  readonly limit_price: string | null;
  readonly maximum_entry_slippage_bps: string;
  readonly entry_deadline: string;
  readonly allow_partial: boolean;
}

export interface TradeIntentMaxPlannedLoss {
  readonly amount: string;
  readonly currency: string;
}

export interface TradeIntentExitPlan {
  readonly protection_policy: "NATIVE_REQUIRED" | "HOSTED_ALLOWED_CASH_ONLY";
  readonly stop_trigger: "LAST" | "MARK" | "BID_ASK";
  readonly stop_price: string | null;
  readonly review_due_at: string;
  readonly maximum_holding_seconds: number;
}

export interface TradeIntent {
  readonly contract_version: "2026-10-20";
  readonly intent_id: string;
  readonly environment: "RESEARCH" | "PAPER" | "TESTNET" | "LIVE";
  readonly owner_strategy_id: string;
  readonly strategy_revision: number;
  readonly decision_run_id: string;
  readonly authorization_id: string;
  readonly venue_account_id: string;
  readonly collateral_group_id: string;
  readonly venue: "BINANCE";
  readonly product: "SPOT" | "USD_M_PERP" | "US_EQUITY";
  readonly instrument_uid: string;
  readonly venue_leg: "BOTH" | "LONG" | "SHORT";
  readonly ownership_epoch: number;
  readonly risk_policy_revision: number;
  readonly basis_fingerprint: string;
  readonly snapshot_id: string;
  readonly as_of: string;
  readonly created_at: string;
  readonly valid_until: string;
  readonly evidence_refs: ReadonlyArray<string>;
  readonly action: "SET_TARGET" | "UPDATE_EXIT" | "HOLD";
  readonly target_side: "LONG" | "SHORT" | "FLAT";
  readonly target_quantity: string;
  readonly quantity_unit: "BASE" | "CONTRACT" | "SHARE";
  readonly execution: TradeIntentExecution;
  readonly max_planned_loss: TradeIntentMaxPlannedLoss;
  readonly requested_leverage: number;
  readonly exit_plan: TradeIntentExitPlan;
  readonly proposal_hash: string;
  readonly rationale_summary: string;
  readonly supersedes_intent_id: string | null;
}

export interface RiskPolicyMaximumModelCostPerDay {
  readonly amount: string;
  readonly currency: string;
}

export interface RiskPolicy {
  readonly contract_version: "2026-10-20";
  readonly policy_id: string;
  readonly revision: number;
  readonly environment: "RESEARCH" | "PAPER" | "TESTNET" | "LIVE";
  readonly collateral_group_id: string;
  readonly valuation_currency: string;
  readonly maximum_capital: string;
  readonly cash_buffer: string;
  readonly maximum_order_notional: string;
  readonly maximum_planned_loss_per_intent: string;
  readonly maximum_total_planned_loss: string;
  readonly maximum_gross_exposure: string;
  readonly maximum_net_exposure: string;
  readonly maximum_open_positions: number;
  readonly maximum_active_orders: number;
  readonly maximum_leverage: number;
  readonly daily_loss_fraction: string;
  readonly maximum_drawdown_fraction: string;
  readonly risk_day_timezone: string;
  readonly maximum_quote_age_ms: number;
  readonly maximum_entry_slippage_bps: string;
  readonly maximum_model_cost_per_day: RiskPolicyMaximumModelCostPerDay;
  readonly protection_policy: "NATIVE_REQUIRED" | "HOSTED_ALLOWED_CASH_ONLY";
  readonly daily_lock_release: "REVIEW_REQUIRED" | "RECHECK_ON_NEW_RISK_DAY";
  readonly drawdown_lock_release: "REVIEW_REQUIRED";
  readonly native_risk_bypass: false;
  readonly live_enabled: boolean;
}

export interface ExecutionCommandCanonicalPayload {
  readonly side?: "BUY" | "SELL";
  readonly order_type?: "MARKET" | "LIMIT" | "STOP_MARKET" | "STOP_LIMIT" | "TAKE_PROFIT_MARKET" | "TAKE_PROFIT_LIMIT";
  readonly quantity?: string;
  readonly quote_notional?: string;
  readonly price?: string;
  readonly trigger_price?: string;
  readonly reduce_only?: boolean;
  readonly full_position_exit?: boolean;
  readonly position_side?: "BOTH" | "LONG" | "SHORT";
  readonly time_in_force?: "GTC" | "GTD" | "DAY" | "IOC" | "FOK";
  readonly tokenize?: false;
}

export interface ExecutionCommand {
  readonly contract_version: "2026-10-20";
  readonly command_id: string;
  readonly attempt_id: string;
  readonly intent_id: string;
  readonly parent_command_id: string | null;
  readonly environment: "RESEARCH" | "PAPER" | "TESTNET" | "LIVE";
  readonly venue_account_id: string;
  readonly product: "SPOT" | "USD_M_PERP" | "US_EQUITY";
  readonly instrument_uid: string;
  readonly operation: "SUBMIT" | "CANCEL" | "AMEND" | "SET_PROTECTION" | "CLOSE";
  readonly logical_client_order_id: string;
  readonly venue_client_order_id: string;
  readonly venue_order_id: string | null;
  readonly target_order_id: string | null;
  readonly canonical_payload: ExecutionCommandCanonicalPayload;
  readonly payload_hash: string;
  readonly ownership_epoch: number;
  readonly execution_epoch: number;
  readonly policy_revision: number;
  readonly state: "DURABLE_READY" | "MAY_HAVE_SENT" | "UNKNOWN" | "NOT_SENT" | "VENUE_REJECTED" | "VENUE_CONFIRMED" | "ABORTED_UNSENT";
  readonly outcome_provenance: "LOCAL_VALIDATION" | "VENUE_EVENT" | "VENUE_QUERY" | "RECONCILIATION_INFERRED" | "LOCAL_POLICY" | "NONE";
  readonly durable_record_ref: string;
  readonly created_at: string;
}
// Application contracts generated from types.py.

export type DecimalString = string;

export type VenueLeg = "BOTH" | "LONG" | "SHORT";

export type PendingOperation = "NONE" | "SUBMIT" | "CANCEL" | "AMEND";

export type CommandCertainty = "NOT_SENT" | "UNKNOWN" | "CONFIRMED";

export type OutcomeProvenance = "LOCAL_VALIDATION" | "VENUE_EVENT" | "VENUE_QUERY" | "RECONCILIATION_INFERRED" | "LOCAL_POLICY" | "NONE";

export type ControlAction = "pause_new" | "cancel_entries" | "exit_owned" | "transfer" | "reconcile";

export type ControlStatus = "QUEUED" | "APPLIED" | "REJECTED" | "IN_PROGRESS";

export type CheckStatus = "NOT_RUN" | "PASS" | "FAIL" | "BLOCKED";

export type TransferMode = "TRANSFER_OWNER" | "RETAIN_OWNER" | "EXIT_THEN_SWITCH";

export type Reservation = CapitalReservation;

export type Environment = "RESEARCH" | "PAPER" | "TESTNET" | "LIVE";

export type Product = "SPOT" | "USD_M_PERP" | "US_EQUITY";

export type DeploymentState = "DISABLED" | "ENABLED" | "PAUSED" | "DRAINING";

export type RiskPermission = "NORMAL" | "NO_NEW_RISK" | "EXIT_ONLY" | "RECOVERY_REQUIRED";

export type ProcessState = "BOOTING" | "RECONCILING" | "READY" | "DEGRADED" | "SHUTTING_DOWN" | "STOPPED";

export type IntentState = "RECEIVED" | "REJECTED" | "EXPIRED_UNSENT" | "VALIDATING" | "REPLAN_REQUIRED" | "RESERVED" | "EXECUTING" | "NEEDS_RECONCILIATION" | "CANCEL_REQUESTED" | "SATISFIED";

export type CommandState = "DURABLE_READY" | "MAY_HAVE_SENT" | "UNKNOWN" | "NOT_SENT" | "VENUE_REJECTED" | "VENUE_CONFIRMED" | "ABORTED_UNSENT";

export type ProtectionState = "NOT_REQUIRED" | "PENDING" | "VERIFIED_NATIVE" | "VERIFIED_HOSTED" | "GAP" | "REPAIRING" | "EXITING" | "FLAT_VERIFIED" | "MANUAL_REQUIRED";

export type ReservationState = "HELD" | "PARTIALLY_CONSUMED" | "VENUE_REFLECTED" | "RELEASE_PENDING" | "RELEASED";

export type OrderLifecycle = "INITIALIZED" | "SUBMITTED" | "ACCEPTED" | "PARTIALLY_FILLED" | "FILLED" | "CANCELED" | "EXPIRED" | "REJECTED";

export type CapabilityStatus = "UNVERIFIED" | "VERIFIED" | "DEGRADED" | "UNAVAILABLE";

export type TransferState = "REQUESTED" | "FREEZE_NEW_RISK" | "RECONCILE_PENDING" | "VERIFY_PROTECTION" | "COMMIT_OWNER_EPOCH" | "ACTIVE" | "TRANSFER_BLOCKED";

export interface ScopeKey {
  readonly environment: Environment;
  readonly venue_account_id: string;
  readonly product: Product;
  readonly instrument_uid: string;
  readonly venue_leg: VenueLeg;
}

export interface Money {
  readonly amount: DecimalString;
  readonly currency: string;
}

export interface Price {
  readonly value: DecimalString;
  readonly currency: string;
}

export interface Quantity {
  readonly value: DecimalString;
  readonly unit: string;
}

export interface ClockReading {
  readonly utc_now: string;
  readonly monotonic_ns: number;
  readonly clock_health: "HEALTHY" | "DEGRADED" | "UNKNOWN";
}

export interface EvidenceRef {
  readonly id: string;
  readonly hash: string;
  readonly environment: Environment;
  readonly source: string;
  readonly visible_at: string;
}

export interface SnapshotRef {
  readonly id: string;
  readonly hash: string;
  readonly environment: Environment;
  readonly source: string;
  readonly visible_at: string;
}

export interface MemoryRef {
  readonly id: string;
  readonly hash: string;
  readonly environment: Environment;
  readonly source: string;
  readonly visible_at: string;
}

export interface EventRef {
  readonly id: string;
  readonly hash: string;
  readonly environment: Environment;
  readonly source: string;
  readonly visible_at: string;
}

export interface ActorContext {
  readonly identity_id: string;
  readonly environment: Environment;
  readonly deployment_id: string;
  readonly authorization_id: string;
  readonly policy_revision: number;
  readonly ownership_epoch: number;
  readonly scopes: ReadonlyArray<ScopeKey>;
}

export interface DecisionContext {
  readonly identity_id: string;
  readonly environment: Environment;
  readonly deployment_id: string;
  readonly authorization_id: string;
  readonly policy_revision: number;
  readonly ownership_epoch: number;
  readonly scopes: ReadonlyArray<ScopeKey>;
  readonly strategy_revision: number;
  readonly decision_run_id: string;
  readonly snapshot_ref: SnapshotRef;
}

export interface ControlContext {
  readonly identity_id: string;
  readonly environment: Environment;
  readonly deployment_id: string;
  readonly authorization_id: string;
  readonly policy_revision: number;
  readonly ownership_epoch: number;
  readonly scopes: ReadonlyArray<ScopeKey>;
  readonly expected_sequence: number;
  readonly idempotency_key: string;
}

export interface CredentialRef {
  readonly id: string;
}

export interface AccountIdentityEvidence {
  readonly venue_account_id: string;
  readonly venue: string;
  readonly product: Product;
  readonly environment: Environment;
  readonly evidence: EvidenceRef;
}

export interface VenueAccount {
  readonly venue_account_id: string;
  readonly venue: string;
  readonly environment: Environment;
  readonly products: ReadonlyArray<Product>;
  readonly credential_refs: ReadonlyArray<CredentialRef>;
  readonly identity_evidence: AccountIdentityEvidence;
}

export interface ExecutionShard {
  readonly environment: Environment;
  readonly venue_account_id: string;
  readonly product: Product;
  readonly node_id: string;
  readonly execution_epoch: number;
}

export interface OwnerLease {
  readonly shard: ExecutionShard;
  readonly instance_id: string;
  readonly expires_at: string;
  readonly deadline_monotonic_ns: number;
}

export interface ExternalFenceProof {
  readonly shard: ExecutionShard;
  readonly old_instance_id: string;
  readonly method: "KEY_REVOKED" | "NETWORK_BLOCKED" | "PROCESS_TERMINATED";
  readonly evidence: EvidenceRef;
}

export interface BuildEnvironment {
  readonly os: string;
  readonly arch: string;
  readonly python_abi: string;
  readonly dependency_digest: string;
  readonly image_digest: string;
}

export interface CandidateSpec {
  readonly candidate_id: string;
  readonly review_ref: string;
  readonly deployment_ref: string;
  readonly source_digest: string;
  readonly build_environment: BuildEnvironment;
}

export interface QualifiedBuild {
  readonly candidate: CandidateSpec;
  readonly artifact_digest: string;
  readonly probe_results: ReadonlyArray<AcceptanceResult>;
  readonly qualification_ref: EvidenceRef;
}

export interface CompatibilityEntry {
  readonly port: string;
  readonly native_api: string;
  readonly evidence: EvidenceRef;
}

export interface CompatibilityMap {
  readonly build_digest: string;
  readonly entries: ReadonlyArray<CompatibilityEntry>;
  readonly send_path_inventory: ReadonlyArray<string>;
  readonly uncovered_paths: ReadonlyArray<string>;
}

export interface QualificationReport {
  readonly build_digest: string;
  readonly market: Product;
  readonly results: ReadonlyArray<AcceptanceResult>;
  readonly unsatisfied_gates: ReadonlyArray<string>;
  readonly live_enabled: false;
}

export interface CapabilityProfile {
  readonly scope: ScopeKey;
  readonly build_digest: string;
  readonly status: CapabilityStatus;
  readonly capabilities: ReadonlyArray<string>;
  readonly limitations: ReadonlyArray<string>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly valid_from: string;
  readonly valid_until: string;
}

export interface CapabilityQuery {
  readonly scope: ScopeKey;
  readonly requested: ReadonlyArray<string>;
  readonly platform: CapabilityProfile;
  readonly adapter: CapabilityProfile;
  readonly account: CapabilityProfile;
  readonly authorization: Authorization;
}

export interface CapabilityDecision {
  readonly status: CapabilityStatus;
  readonly allowed: ReadonlyArray<string>;
  readonly rejection_codes: ReadonlyArray<string>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface VenueCapabilityEvidence {
  readonly scope: ScopeKey;
  readonly build_digest: string;
  readonly query_scope: string;
  readonly source: string;
  readonly session_id: string;
  readonly coverage: Coverage;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface StocksProbeInput {
  readonly evidence: VenueCapabilityEvidence;
  readonly instrument: InstrumentSpec;
  readonly requested_capabilities: ReadonlyArray<string>;
}

export interface InstrumentSpec {
  readonly instrument_uid: string;
  readonly venue: string;
  readonly product: Product;
  readonly native_symbol: string;
  readonly tick_size: Price;
  readonly quantity_step: Quantity;
  readonly minimum_quantity: Quantity;
  readonly minimum_notional: Money;
  readonly rule_revision: number;
  readonly quote_currency: string;
  readonly settlement_currency: string;
}

export interface SessionState {
  readonly instrument_uid: string;
  readonly session_id: string;
  readonly state: "OPEN" | "CLOSED" | "HALTED" | "UNKNOWN";
  readonly observed_at: string;
  readonly next_open_at: string | null;
  readonly evidence: EvidenceRef;
}

export interface Coverage {
  readonly scope: ScopeKey;
  readonly query_succeeded: boolean;
  readonly pagination_complete: boolean;
  readonly supported: boolean;
  readonly missing: ReadonlyArray<string>;
}

export interface Quote {
  readonly bid: Price | null;
  readonly ask: Price | null;
  readonly last: Price | null;
  readonly occurred_at: string;
  readonly received_at: string;
}

export interface SnapshotPacket {
  readonly ref: SnapshotRef;
  readonly scope: ScopeKey;
  readonly quote: Quote;
  readonly private_refs: ReadonlyArray<EvidenceRef>;
  readonly economic_refs: ReadonlyArray<EvidenceRef>;
  readonly published_at: string;
  readonly revision_visible_at: string;
  readonly coverage: Coverage;
}

export interface DataVerdict {
  readonly usable: boolean;
  readonly age_ms: number;
  readonly missing: ReadonlyArray<string>;
  readonly rejection_codes: ReadonlyArray<string>;
}

export interface BookSnapshot {
  readonly scope: ScopeKey;
  readonly positions: ReadonlyArray<ManagedPosition>;
  readonly active_orders: ReadonlyArray<OrderProjection>;
  readonly reservations: ReadonlyArray<ReservationProjection>;
  readonly balances: ReadonlyArray<Money>;
  readonly sequence: number;
  readonly basis_fingerprint: string;
  readonly coverage: Coverage;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface Authorization {
  readonly authorization_id: string;
  readonly owner_strategy_id: string;
  readonly environment: Environment;
  readonly deployment_id: string;
  readonly scopes: ReadonlyArray<ScopeKey>;
  readonly maximum_capital: Money;
  readonly policy_revision: number;
  readonly protection_options: ReadonlyArray<"NATIVE_REQUIRED" | "HOSTED_ALLOWED_CASH_ONLY">;
  readonly model_cost_budget: Money;
  readonly valid_from: string;
  readonly expires_at: string;
  readonly revoked_at: string | null;
}

export interface AuthorizationDecision {
  readonly allowed: boolean;
  readonly authorization_id: string;
  readonly rejection_codes: ReadonlyArray<string>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface RiskDecision {
  readonly risk_decision_id: string;
  readonly intent_id: string;
  readonly verdict: "ALLOW" | "DENY" | "REPLAN_REQUIRED";
  readonly policy_revision: number;
  readonly evaluated_at: string;
  readonly expires_at: string;
  readonly snapshot_ref: SnapshotRef;
  readonly permitted_quantity: Quantity;
  readonly permitted_leverage: number;
  readonly reserved_assets: ReadonlyArray<Money>;
  readonly estimated_loss: Money;
  readonly fees_assumption: Money;
  readonly aggregate_exposure_after: ExposureReport;
  readonly rejection_codes: ReadonlyArray<string>;
  readonly evidence_completeness: Coverage;
}

export interface RiskPolicyDraft {
  readonly policy: RiskPolicy;
  readonly expected_revision: number;
  readonly author_id: string;
}

export interface RiskAlert {
  readonly alert_id: string;
  readonly scope: ScopeKey;
  readonly cause: string;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly occurred_at: string;
}

export interface RiskLock {
  readonly lock_id: string;
  readonly scope: ScopeKey;
  readonly cause: string;
  readonly state: "ACTIVE" | "RESOLVED";
  readonly permission: RiskPermission;
  readonly required_evidence: ReadonlyArray<string>;
  readonly raised_at: string;
}

export interface ResolutionEvidence {
  readonly lock_id: string;
  readonly scope: ScopeKey;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly authorization_id: string;
}

export interface LockResolution {
  readonly lock_id: string;
  readonly resolved: boolean;
  readonly remaining_locks: ReadonlyArray<RiskLock>;
  readonly missing: ReadonlyArray<string>;
}

export interface DeltaPlan {
  readonly scope: ScopeKey;
  readonly target: Quantity;
  readonly actual: Quantity;
  readonly working: Quantity;
  readonly remaining: Quantity;
  readonly classification: "INCREASE" | "REDUCE" | "NONE" | "REVERSAL_REQUIRES_FLAT";
  readonly cancel_first: ReadonlyArray<OwnedOrderRef>;
}

export interface ExposureGroup {
  readonly group_id: string;
  readonly gross: Money;
  readonly net: Money;
}

export interface ExposureReport {
  readonly gross: Money;
  readonly net: Money;
  readonly groups: ReadonlyArray<ExposureGroup>;
  readonly missing: ReadonlyArray<string>;
}

export interface CapitalReservation {
  readonly reservation_id: string;
  readonly environment: Environment;
  readonly collateral_group_id: string;
  readonly venue_account_id: string;
  readonly owner_strategy_id: string;
  readonly intent_id: string;
  readonly asset: string;
  readonly amount: DecimalString;
  readonly reflected_amount: DecimalString;
  readonly state: ReservationState;
  readonly created_at: string;
  readonly expires_at: string;
  readonly command_refs: ReadonlyArray<string>;
  readonly evidence_watermark: string;
}

export interface ReservationProjection {
  readonly reservation: CapitalReservation;
  readonly held: DecimalString;
  readonly reflected: DecimalString;
  readonly release_pending: DecimalString;
  readonly sequence: number;
}

export interface ReservationOutcome {
  readonly accepted: boolean;
  readonly reservations: ReadonlyArray<ReservationProjection>;
  readonly book_sequence: number;
  readonly rejection_codes: ReadonlyArray<string>;
}

export interface ReservationEvidence {
  readonly reservation_id: string;
  readonly venue_freeze_ref: EvidenceRef;
  readonly reflected_amount: DecimalString;
  readonly command_certainty: CommandCertainty;
  readonly terminal_verified: boolean;
  readonly watermark: string;
}

export interface EquityPoint {
  readonly at: string;
  readonly equity: Money;
  readonly evidence: EvidenceRef;
}

export interface Cashflow {
  readonly at: string;
  readonly amount: Money;
  readonly classification: "EXTERNAL" | "INTERNAL" | "FEE" | "FUNDING" | "DIVIDEND";
  readonly internal_pair_id: string | null;
  readonly evidence: EvidenceRef;
}

export interface RiskDayResult {
  readonly risk_day: string;
  readonly timezone: string;
  readonly adjusted_pnl: Money | null;
  readonly high_water_mark: Money | null;
  readonly missing: ReadonlyArray<string>;
}

export interface ProposalReceipt {
  readonly receipt_id: string;
  readonly status: IntentState;
  readonly reason: string | null;
  readonly existing_result_ref: string | null;
  readonly intent_id: string | null;
}

export interface CommandReceipt {
  readonly receipt_id: string;
  readonly command_id: string;
  readonly status: CommandState;
  readonly reason: string | null;
  readonly existing_result_ref: string | null;
}

export interface DurablePermit {
  readonly durable_record_ref: string;
  readonly command_id: string;
  readonly payload_hash: string;
  readonly scope: ScopeKey;
  readonly ownership_epoch: number;
  readonly execution_epoch: number;
  readonly policy_revision: number;
  readonly expires_at: string;
  readonly deadline_monotonic_ns: number;
}

export interface EmergencyRecord {
  readonly record_id: string;
  readonly command: ExecutionCommand;
  readonly context: EmergencyContext;
  readonly prior_record_hash: string;
}

export interface OrderIdentity {
  readonly scope: ScopeKey;
  readonly logical_client_order_id: string;
  readonly native_client_order_id: string;
  readonly venue_client_order_id: string;
  readonly venue_order_id: string | null;
}

export interface OwnedOrderRef {
  readonly identity: OrderIdentity;
  readonly owner_strategy_id: string;
  readonly ownership_epoch: number;
}

export interface OrderProjection {
  readonly order: OwnedOrderRef;
  readonly lifecycle_state: OrderLifecycle;
  readonly pending_operation: PendingOperation;
  readonly command_certainty: CommandCertainty;
  readonly outcome_provenance: OutcomeProvenance;
  readonly filled_quantity: Quantity;
  readonly leaves_quantity: Quantity;
  readonly lifecycle_revision: number;
  readonly sequence: number;
}

export interface RawVenueMessage {
  readonly scope: ScopeKey;
  readonly source: string;
  readonly payload_hash: string;
  readonly raw_payload_ref: EvidenceRef;
  readonly received_at: string;
}

export interface VenueEvent {
  readonly event_id: string;
  readonly source: string;
  readonly source_event_id: string | null;
  readonly source_sequence: string | null;
  readonly account_id: string;
  readonly instrument_uid: string;
  readonly order_refs: ReadonlyArray<OrderIdentity>;
  readonly occurred_at: string;
  readonly received_at: string;
  readonly processed_at: string;
  readonly payload_hash: string;
  readonly raw_payload_ref: EvidenceRef;
}

export interface VenueReport {
  readonly coverage: Coverage;
  readonly orders: ReadonlyArray<OrderProjection>;
  readonly positions: ReadonlyArray<ManagedPosition>;
  readonly balances: ReadonlyArray<Money>;
  readonly fills: ReadonlyArray<Fill>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly next_cursor: string | null;
  readonly as_of: string;
}

export interface Fill {
  readonly fill_id: string;
  readonly venue: string;
  readonly scope: ScopeKey;
  readonly native_trade_id: string | null;
  readonly order: OrderIdentity;
  readonly quantity: Quantity;
  readonly price: Price;
  readonly fees: ReadonlyArray<Money>;
  readonly occurred_at: string;
  readonly correction_of: string | null;
  readonly evidence: EvidenceRef;
}

export interface FillReceipt {
  readonly fill_id: string;
  readonly recorded: boolean;
  readonly duplicate_of: string | null;
  readonly event_ref: EventRef | null;
}

export interface EvidenceReceipt {
  readonly event_id: string;
  readonly accepted: boolean;
  readonly reason: string | null;
  readonly raw_ref: EvidenceRef;
}

export interface DomainEvent {
  readonly event_id: string;
  readonly scope: ScopeKey;
  readonly aggregate_id: string;
  readonly revision: number;
  readonly occurred_at: string;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly payload: Fill | OrderProjection | RiskLock | ManagedPosition | ReservationProjection;
}

export interface ProjectionUpdate {
  readonly projection: OrderProjection;
  readonly events: ReadonlyArray<DomainEvent>;
}

export interface QueryBudget {
  readonly maximum_requests: number;
  readonly window_seconds: number;
  readonly minimum_interval_ms: number;
  readonly retry_after: string | null;
}

export interface ReconciliationCase {
  readonly case_id: string;
  readonly scope: ScopeKey;
  readonly command_ids: ReadonlyArray<string>;
  readonly position_ids: ReadonlyArray<string>;
  readonly missing: ReadonlyArray<string>;
  readonly query_budget: QueryBudget;
  readonly attempts: number;
  readonly next_query_at: string;
  readonly state: "OPEN" | "WAITING" | "RESOLVED";
}

export interface ReconciliationResult {
  readonly case_id: string;
  readonly resolved: boolean;
  readonly missing: ReadonlyArray<string>;
  readonly residual: Quantity | null;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface RecoveryChecklist {
  readonly scope: ScopeKey;
  readonly unique_process: EvidenceRef | null;
  readonly commands: EvidenceRef | null;
  readonly funds: EvidenceRef | null;
  readonly private_stream: EvidenceRef | null;
  readonly positions: EvidenceRef | null;
  readonly protection: EvidenceRef | null;
  readonly authorization: EvidenceRef | null;
  readonly unresolved_cases: ReadonlyArray<ReconciliationCase>;
}

export interface RecoveryVerdict {
  readonly ready: boolean;
  readonly missing: ReadonlyArray<string>;
  readonly risk_locks: ReadonlyArray<RiskLock>;
}

export interface ChildPolicy {
  readonly expires_at: string;
  readonly slice_quantity: Quantity;
  readonly maximum_children: number;
  readonly compensation_policy: string;
  readonly mutable_fields: ReadonlyArray<"quantity" | "price" | "trigger_price">;
}

export interface AmendRequest {
  readonly target_order: OwnedOrderRef;
  readonly quantity: Quantity | null;
  readonly price: Price | null;
  readonly trigger_price: Price | null;
  readonly expected_revision: number;
}

export interface ManagedPosition {
  readonly position_id: string;
  readonly scope: ScopeKey;
  readonly actual_quantity: Quantity;
  readonly owner_strategy_id: string | null;
  readonly owner_book_id: string;
  readonly ownership_epoch: number;
  readonly cost_evidence: Coverage;
  readonly realized_pnl: Money | null;
  readonly unrealized_valuation: Money | null;
  readonly active_exit_plan_id: string | null;
  readonly risk_locks: ReadonlyArray<RiskLock>;
}

export interface ProtectionPlan {
  readonly plan_id: string;
  readonly mode: "NATIVE" | "HOSTED";
  readonly scope: ScopeKey;
  readonly coverage_quantity: Quantity;
  readonly stop_price: Price;
  readonly stop_trigger: "LAST" | "MARK" | "BID_ASK";
  readonly native_order_ids: ReadonlyArray<OwnedOrderRef>;
  readonly monitor_id: string | null;
  readonly state: ProtectionState;
  readonly last_verified_at: string | null;
  readonly coverage_gap_since: string | null;
  readonly replacement_revision: number;
  readonly on_gap_policy: string;
}

export interface ProtectionActionPlan {
  readonly plan: ProtectionPlan;
  readonly commands: ReadonlyArray<ExecutionCommand>;
  readonly locks: ReadonlyArray<RiskLock>;
  readonly missing: ReadonlyArray<string>;
}

export interface ProtectionVerdict {
  readonly state: ProtectionState;
  readonly covered_quantity: Quantity;
  readonly missing: ReadonlyArray<string>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface EmergencyContext {
  readonly context: ControlContext;
  readonly reducible_quantity: Quantity;
  readonly quantity_evidence: EvidenceRef;
  readonly allowed_order_types: ReadonlyArray<"MARKET" | "LIMIT">;
  readonly maximum_slippage_bps: DecimalString;
  readonly expires_at: string;
}

export interface ExitReceipt {
  readonly position_id: string;
  readonly state: "EXITING" | "EXIT_PENDING_MARKET" | "FLAT_VERIFIED" | "MANUAL_REQUIRED" | "NONTRADABLE_RESIDUAL";
  readonly command_ids: ReadonlyArray<string>;
  readonly incomplete_reasons: ReadonlyArray<string>;
}

export interface TransferRequest {
  readonly transfer_id: string;
  readonly scope: ScopeKey;
  readonly mode: TransferMode;
  readonly old_owner: string;
  readonly new_owner: string;
  readonly ownership_epoch: number;
}

export interface TransferReceipt {
  readonly transfer_id: string;
  readonly state: TransferState;
  readonly reason: string | null;
}

export interface TransferEvidence {
  readonly transfer_id: string;
  readonly durable_step_refs: ReadonlyArray<EvidenceRef>;
  readonly reconciliation: ReconciliationResult;
  readonly protection: ProtectionVerdict;
  readonly exit: ExitReceipt | null;
}

export interface TransferProjection {
  readonly request: TransferRequest;
  readonly state: TransferState;
  readonly ownership_epoch: number;
  readonly durable_steps: ReadonlyArray<EvidenceRef>;
  readonly blocked_reasons: ReadonlyArray<string>;
}

export interface ExternalChange {
  readonly scope: ScopeKey;
  readonly kind: "ORDER" | "QUANTITY" | "MODE";
  readonly evidence: EvidenceRef;
  readonly observed_quantity: Quantity | null;
}

export interface InterventionCase {
  readonly case_id: string;
  readonly change: ExternalChange;
  readonly decision: "PENDING" | "RETAIN" | "TAKEOVER" | "RELINQUISH";
  readonly lock: RiskLock;
}

export interface DecisionSlot {
  readonly strategy_id: string;
  readonly instrument_or_portfolio_id: string;
  readonly decision_epoch: number;
  readonly expires_at: string;
}

export interface DecisionTrigger {
  readonly trigger_id: string;
  readonly slot: DecisionSlot;
  readonly priority: number;
  readonly deduplication_key: string;
  readonly evidence: EventRef;
}

export interface ScheduleReceipt {
  readonly job_id: string;
  readonly coalesced: boolean;
  readonly reason: string | null;
}

export interface SlotVerdict {
  readonly valid: boolean;
  readonly reasons: ReadonlyArray<string>;
}

export interface ModelCallRequest {
  readonly request_id: string;
  readonly provider: string;
  readonly model: string;
  readonly task: string;
  readonly maximum_cost: Money;
  readonly snapshot_refs: ReadonlyArray<SnapshotRef>;
  readonly expires_at: string;
}

export interface CostPermit {
  readonly permit_id: string;
  readonly request_id: string;
  readonly reserved_cost: Money;
  readonly expires_at: string;
}

export interface DecisionJob {
  readonly job_id: string;
  readonly slot: DecisionSlot;
  readonly context: DecisionContext;
  readonly request: ModelCallRequest;
  readonly cost_permit: CostPermit;
}

export interface DecisionBatch {
  readonly job_id: string;
  readonly proposals: ReadonlyArray<DecisionProposal>;
  readonly cost: Money;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly missing: ReadonlyArray<string>;
}

export interface HistoryQuery {
  readonly scope: ScopeKey;
  readonly start: string;
  readonly end: string;
  readonly cursor: string | null;
  readonly limit: number;
}

export interface HistoryPage {
  readonly snapshots: ReadonlyArray<SnapshotPacket>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly next_cursor: string | null;
  readonly coverage: Coverage;
}

export interface RuleCondition {
  readonly operator: "ALL" | "ANY" | "NOT" | "GT" | "GE" | "LT" | "LE" | "EQ";
  readonly input_ref: string | null;
  readonly threshold: DecimalString | null;
  readonly children: ReadonlyArray<RuleCondition>;
}

export interface RuleGraph {
  readonly rule_id: string;
  readonly revision: number;
  readonly instrument_ref: string;
  readonly condition: RuleCondition;
  readonly trigger: "EDGE" | "CONTINUOUS";
  readonly window_seconds: number;
  readonly closed_bar_only: boolean;
  readonly cooldown_seconds: number;
  readonly sizing: "QUOTE_NOTIONAL" | "BASE_QUANTITY";
  readonly target_value: DecimalString;
  readonly exit_priority: ReadonlyArray<string>;
  readonly missing_data: "HOLD" | "REJECT";
}

export interface RuleDraftContext {
  readonly context: DecisionContext;
  readonly allowed_inputs: ReadonlyArray<string>;
}

export interface RuleDraft {
  readonly graph: RuleGraph | null;
  readonly ambiguities: ReadonlyArray<string>;
  readonly source_ref: EvidenceRef;
}

export interface CompiledRule {
  readonly graph: RuleGraph;
  readonly hash: string;
  readonly compiler_revision: string;
}

export interface RuleState {
  readonly previous_condition: boolean | null;
  readonly last_triggered_at: string | null;
  readonly last_bar_id: string | null;
  readonly revision: number;
}

export interface RuleEvaluation {
  readonly proposal: DecisionProposal | null;
  readonly action: "PROPOSAL" | "HOLD" | "REJECT";
  readonly state: RuleState;
  readonly reasons: ReadonlyArray<string>;
}

export interface RuleArtifact {
  readonly hash: string;
  readonly artifact_ref: string;
  readonly dependency_lock_hash: string;
}

export interface RuleInput {
  readonly snapshot: SnapshotPacket;
  readonly state: RuleState;
}

export interface SandboxLimits {
  readonly cpu_ms: number;
  readonly memory_bytes: number;
  readonly output_bytes: number;
  readonly network: "DENY";
  readonly credentials: "NONE";
}

export interface StrategyChange {
  readonly strategy_id: string;
  readonly expected_revision: number;
  readonly artifact: RuleArtifact;
  readonly review_ref: EvidenceRef;
}

export interface ReleaseCandidate {
  readonly candidate_id: string;
  readonly change: StrategyChange;
  readonly evaluation_refs: ReadonlyArray<EvidenceRef>;
}

export interface ReleaseApproval {
  readonly candidate_id: string;
  readonly authorization_id: string;
  readonly actor: ActorContext;
  readonly approved_at: string;
}

export interface DeploymentRevision {
  readonly deployment_id: string;
  readonly revision: number;
  readonly candidate: ReleaseCandidate;
  readonly approval: ReleaseApproval;
  readonly rollback_revision: number | null;
}

export interface MemoryEntry {
  readonly id: string;
  readonly environment: Environment;
  readonly classification: "FACT" | "INFERENCE" | "EXPIRED";
  readonly text: string;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly visible_at: string;
  readonly expires_at: string | null;
}

export interface CompositionStage {
  readonly stage_id: string;
  readonly responsibility: "RESEARCH" | "ENTRY" | "EXIT" | "PROTECTION";
  readonly manager_id: string;
}

export interface DecisionComposition {
  readonly composition_id: string;
  readonly revision: number;
  readonly stages: ReadonlyArray<CompositionStage>;
}

export interface CompiledComposition {
  readonly policy: DecisionComposition;
  readonly hash: string;
  readonly ordinary_exit_owner: string;
}

export interface DecisionSignal {
  readonly stage_id: string;
  readonly scope: ScopeKey;
  readonly proposal: DecisionProposal;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface DatasetRef {
  readonly id: string;
  readonly hash: string;
  readonly environment: Environment;
  readonly start: string;
  readonly end: string;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface ExperimentConfig {
  readonly mode: "REPLAY_OUTPUTS" | "REINFER";
  readonly build_digest: string;
  readonly config_hash: string;
  readonly model_fingerprint: string;
  readonly proposal_refs: ReadonlyArray<EvidenceRef>;
}

export interface ExperimentReport {
  readonly run_id: string;
  readonly config: ExperimentConfig;
  readonly dataset: DatasetRef;
  readonly metrics: MetricReport;
  readonly missing: ReadonlyArray<string>;
}

export interface PaperRunConfig {
  readonly environment: "PAPER";
  readonly virtual_capital: ReadonlyArray<Money>;
  readonly build_digest: string;
  readonly config_hash: string;
  readonly dataset: DatasetRef;
  readonly isolation_ref: EvidenceRef;
}

export interface RunReceipt {
  readonly run_id: string;
  readonly status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface EventRange {
  readonly scope: ScopeKey;
  readonly first_sequence: number;
  readonly last_sequence: number;
  readonly events: ReadonlyArray<DomainEvent>;
}

export interface MetricAssumptions {
  readonly valuation_currency: string;
  readonly fees: ReadonlyArray<Money>;
  readonly funding: ReadonlyArray<Money>;
  readonly model_costs: ReadonlyArray<Money>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface MetricValue {
  readonly name: string;
  readonly value: DecimalString | null;
  readonly unit: string;
}

export interface MetricReport {
  readonly metrics: ReadonlyArray<MetricValue>;
  readonly assumptions: MetricAssumptions;
  readonly missing: ReadonlyArray<string>;
}

export interface AccountQuery {
  readonly scope: ScopeKey;
  readonly session_id: string;
  readonly as_of: string;
}

export interface CorporateAction {
  readonly action_id: string;
  readonly scope: ScopeKey;
  readonly kind: "SPLIT" | "DIVIDEND" | "SYMBOL_CHANGE" | "DELISTING";
  readonly effective_at: string;
  readonly ratio: DecimalString | null;
  readonly cashflow: Cashflow | null;
  readonly evidence: EvidenceRef;
}

export interface EquityAdjustment {
  readonly action_id: string;
  readonly quantity_before: Quantity;
  readonly quantity_after: Quantity;
  readonly cashflows: ReadonlyArray<Cashflow>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface ControlCommandRequest {
  readonly action: ControlAction;
  readonly scope: ScopeKey;
  readonly expected_revision: number;
  readonly ownership_epoch: number;
  readonly transfer: TransferRequest | null;
}

export interface ControlCommandReceipt {
  readonly command_id: string;
  readonly status: ControlStatus;
  readonly reason: string | null;
  readonly existing_result_ref: string | null;
}

export interface ControlCommandProjection {
  readonly receipt: ControlCommandReceipt;
  readonly request: ControlCommandRequest;
  readonly payload_hash: string;
  readonly idempotency_key: string;
  readonly sequence: number;
  readonly incomplete_reasons: ReadonlyArray<string>;
}

export interface StreamCursor {
  readonly scope: ScopeKey;
  readonly sequence: number;
  readonly stream_id: string;
}

export interface ProjectionQueryRequest {
  readonly scope: ScopeKey;
  readonly resource: "positions" | "orders" | "risk-locks" | "intents" | "control-commands";
  readonly owner_strategy_id: string | null;
  readonly cursor: StreamCursor | null;
  readonly limit: number;
  readonly maximum_age_ms: number | null;
}

export interface FreshnessView {
  readonly as_of: string;
  readonly age_ms: number;
  readonly stale: boolean;
  readonly missing: ReadonlyArray<string>;
}

export interface PositionProjection {
  readonly position: ManagedPosition;
  readonly protection: ProtectionPlan | null;
  readonly freshness: FreshnessView;
  readonly sequence: number;
}

export interface ProjectionPage {
  readonly scope: ScopeKey;
  readonly positions: ReadonlyArray<PositionProjection>;
  readonly orders: ReadonlyArray<OrderProjection>;
  readonly risk_locks: ReadonlyArray<RiskLock>;
  readonly controls: ReadonlyArray<ControlCommandProjection>;
  readonly intents: ReadonlyArray<TradeIntent>;
  readonly next_cursor: StreamCursor | null;
  readonly freshness: FreshnessView;
}

export interface ProjectionEvent {
  readonly cursor: StreamCursor;
  readonly event: DomainEvent;
}

export interface EventPage {
  readonly events: ReadonlyArray<ProjectionEvent>;
  readonly next_cursor: StreamCursor;
  readonly gap: boolean;
  readonly requires_snapshot: boolean;
}

export interface SessionRef {
  readonly session_id: string;
  readonly identity_id: string;
  readonly environment: Environment;
  readonly expires_at: string;
  readonly authorization_revision: number;
}

export interface ProcessHealth {
  readonly process_id: string;
  readonly state: ProcessState;
  readonly observed_at: string;
  readonly faults: ReadonlyArray<string>;
}

export interface SystemHealth {
  readonly processes: ReadonlyArray<ProcessHealth>;
  readonly risk_permission: RiskPermission;
  readonly missing: ReadonlyArray<string>;
}

export interface NotificationReceipt {
  readonly alert_id: string;
  readonly status: "QUEUED" | "DELIVERED" | "FAILED";
  readonly delivered_at: string | null;
}

export interface DeploymentManifest {
  readonly deployment_id: string;
  readonly artifact_digest: string;
  readonly config_digest: string;
  readonly environment: Environment;
  readonly processes: ReadonlyArray<string>;
}

export interface DeploymentEvidence {
  readonly manifest: DeploymentManifest;
  readonly evidence: ReadonlyArray<EvidenceRef>;
  readonly faults: ReadonlyArray<string>;
  readonly status: CheckStatus;
}

export interface HostExit {
  readonly instance_id: string;
  readonly exit_code: number;
  readonly unresolved: ReadonlyArray<ReconciliationCase>;
  readonly evidence: ReadonlyArray<EvidenceRef>;
}

export interface TakeoverVerdict {
  readonly allowed: boolean;
  readonly missing: ReadonlyArray<string>;
  readonly fence_proof: ExternalFenceProof | null;
}

export interface VerificationEnvironment {
  readonly environment_id: string;
  readonly build_digest: string;
  readonly migration_digest: string;
  readonly config_digest: string;
  readonly platform: BuildEnvironment;
  readonly evidence_layer: "CONTRACT" | "OFFLINE" | "NATIVE" | "PLATFORM";
}

export interface AcceptanceResult {
  readonly case_id: string;
  readonly environment: VerificationEnvironment;
  readonly status: CheckStatus;
  readonly injection: ReadonlyArray<EvidenceRef>;
  readonly actual_events: ReadonlyArray<EventRef>;
  readonly before_book: EvidenceRef | null;
  readonly after_book: EvidenceRef | null;
  readonly command_refs: ReadonlyArray<EvidenceRef>;
  readonly reason: string | null;
}

export interface DeploymentRequest {
  readonly strategy_revision: number;
  readonly authorization_id: string;
  readonly environment: Environment;
  readonly expected_revision: number;
}

export interface IntentProjection {
  readonly intent: TradeIntent;
  readonly state: IntentState;
  readonly risk: RiskDecision | null;
  readonly commands: ReadonlyArray<ExecutionCommand>;
  readonly fills: ReadonlyArray<Fill>;
  readonly protection: ReadonlyArray<ProtectionPlan>;
  readonly freshness: FreshnessView;
}

export interface ApiError {
  readonly reason_code: string;
  readonly message: string;
  readonly correctable_fields: ReadonlyArray<string>;
  readonly allowed_actions: ReadonlyArray<ControlAction>;
}

export type WireRecord = DecisionProposal | TradeIntent | RiskPolicy | ExecutionCommand;

export interface ControlApiClient {
  command(request: ControlCommandRequest, key: string): Promise<ControlCommandReceipt>;
  query(request: ProjectionQueryRequest): Promise<ProjectionPage>;
  follow(cursor: StreamCursor | null): AsyncIterable<ProjectionEvent>;
}

export interface RuleEditor { toGraph(): RuleGraph; fromGraph(graph: RuleGraph): void; }

export interface PolicyEditor { submit(draft: RiskPolicyDraft, expectedRevision: number): Promise<ControlCommandReceipt>; }
