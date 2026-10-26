"""Kernel-independent application boundaries; no SDK, transport or credentials.

Protocols declare contracts only. A permit is verified by the execution owner,
never minted by a provider. Database transaction scopes must not span model or
venue network calls. Native bindings require separate qualification evidence.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Iterator, Protocol
from . import types as t
from .wire import DecisionProposal, ExecutionCommand, RiskPolicy, TradeIntent


class CommandRepository(Protocol):
    def get(self, command_id: str) -> ExecutionCommand | None: ...
    def append(self, command: ExecutionCommand, expected_sequence: int) -> None: ...
    def mark_may_have_sent(self, tx: LedgerTx, command_id: str, expected_state: str) -> t.DurablePermit: ...


class ReservationRepository(Protocol):
    def for_intent(self, intent_id: str) -> tuple[t.CapitalReservation, ...]: ...
    def append(self, reservation: t.CapitalReservation, expected_sequence: int) -> None: ...


class RiskLockRepository(Protocol):
    def active(self, scope: t.ScopeKey) -> tuple[t.RiskLock, ...]: ...
    def append(self, lock: t.RiskLock, expected_sequence: int) -> None: ...


class AccountRepository(Protocol):
    def get(self, venue_account_id: str) -> t.VenueAccount | None: ...


class ProjectionRepository(Protocol):
    def book(self, scope: t.ScopeKey) -> t.BookSnapshot: ...
    def append(self, update: t.ProjectionUpdate, expected_sequence: int) -> None: ...


class LedgerTx(Protocol):
    @property
    def commands(self) -> CommandRepository: ...
    @property
    def reservations(self) -> ReservationRepository: ...
    @property
    def risk_locks(self) -> RiskLockRepository: ...
    @property
    def accounts(self) -> AccountRepository: ...
    @property
    def projections(self) -> ProjectionRepository: ...


class UnitOfWork(Protocol):
    def transaction(self, scope: t.ScopeKey) -> AbstractContextManager[LedgerTx]: ...


class EventStore(Protocol):
    def append(self, tx: LedgerTx, event: t.DomainEvent, expected_sequence: int) -> t.EventRef: ...


class EnginePort(Protocol):
    def submit(self, command: ExecutionCommand, permit: t.DurablePermit) -> t.CommandReceipt: ...
    def cancel(self, command: ExecutionCommand, permit: t.DurablePermit) -> t.CommandReceipt: ...
    def amend(self, command: ExecutionCommand, permit: t.DurablePermit) -> t.CommandReceipt: ...
    def query(self, query: t.ReconciliationCase) -> t.VenueReport: ...


class IdentityCatalog(Protocol):
    def bind(self, credential_ref: str, evidence: t.AccountIdentityEvidence) -> t.VenueAccount: ...


class CapabilityCatalog(Protocol):
    def intersect(self, query: t.CapabilityQuery) -> t.CapabilityDecision: ...


class AuthorizationService(Protocol):
    def validate(self, context: t.DecisionContext) -> t.AuthorizationDecision: ...


class SnapshotStore(Protocol):
    def put(self, packet: t.SnapshotPacket) -> t.SnapshotRef: ...
    def load(self, ref: t.SnapshotRef) -> t.SnapshotPacket: ...


class FreshnessPolicy(Protocol):
    def evaluate(self, snapshot: t.SnapshotPacket, now: t.ClockReading, session: t.SessionState) -> t.DataVerdict: ...


class RiskEvaluator(Protocol):
    def evaluate(self, intent: TradeIntent, book: t.BookSnapshot, market: t.SnapshotPacket, policy: RiskPolicy) -> t.RiskDecision: ...


class ExposureCalculator(Protocol):
    def project(self, book: t.BookSnapshot, delta: t.DeltaPlan) -> t.ExposureReport: ...


class ReservationService(Protocol):
    def reserve(self, intent: TradeIntent, expected_book_sequence: int) -> t.ReservationOutcome: ...
    def reconcile(self, evidence: t.ReservationEvidence) -> t.ReservationProjection: ...


class RiskLockService(Protocol):
    def raise_lock(self, scope: t.ScopeKey, reason: str, evidence: t.EvidenceRef) -> t.RiskLock: ...
    def resolve(self, lock_id: str, evidence: t.ResolutionEvidence) -> t.LockResolution: ...


class RiskDayService(Protocol):
    def pnl(self, series: list[t.EquityPoint], cashflows: list[t.Cashflow], zone: str) -> t.RiskDayResult: ...


class ProposalIngress(Protocol):
    def accept(self, proposal: DecisionProposal, context: t.DecisionContext) -> t.ProposalReceipt: ...


class IntentCompiler(Protocol):
    def bind(self, proposal: DecisionProposal, context: t.DecisionContext, basis: t.BookSnapshot) -> TradeIntent: ...


class TargetCompiler(Protocol):
    def compile(self, intent: TradeIntent, book: t.BookSnapshot) -> t.DeltaPlan: ...


class CommandDispatcher(Protocol):
    def dispatch(self, command_id: str, lease: t.OwnerLease) -> t.CommandReceipt: ...


class VenueIdCodec(Protocol):
    def encode(self, logical_id: str, product: t.Product, role: str) -> str: ...
    def decode(self, venue_id: str) -> t.OrderIdentity: ...


class NautilusHost(Protocol):
    def run(self, shard: t.ExecutionShard, build: t.QualifiedBuild) -> t.HostExit: ...


class EvidenceIngestor(Protocol):
    def ingest(self, event: t.VenueEvent) -> t.EvidenceReceipt: ...


class OrderProjector(Protocol):
    def apply(self, event: t.DomainEvent, previous: t.OrderProjection) -> t.ProjectionUpdate: ...


class FillLedger(Protocol):
    def record(self, fill: t.Fill, correction_of: str | None = None) -> t.FillReceipt: ...


class Reconciler(Protocol):
    def reconcile(self, case: t.ReconciliationCase, report: t.VenueReport) -> t.ReconciliationResult: ...


class RecoveryGate(Protocol):
    def evaluate(self, checklist: t.RecoveryChecklist) -> t.RecoveryVerdict: ...


class CaseScheduler(Protocol):
    def due(self, now: t.ClockReading, limits: t.QueryBudget) -> list[t.ReconciliationCase]: ...


class ChildPlanner(Protocol):
    def plan(self, intent: TradeIntent, book: t.BookSnapshot, policy: t.ChildPolicy) -> list[ExecutionCommand]: ...


class OrderActionService(Protocol):
    def cancel(self, order: t.OwnedOrderRef, context: t.ControlContext) -> t.CommandReceipt: ...
    def amend(self, order: t.OwnedOrderRef, changes: t.AmendRequest, context: t.ControlContext) -> t.CommandReceipt: ...


class ProtectionManager(Protocol):
    def on_fill(self, fill: t.Fill, position: t.ManagedPosition) -> t.ProtectionActionPlan: ...
    def verify(self, plan: t.ProtectionPlan, evidence: t.VenueReport) -> t.ProtectionVerdict: ...


class ExitService(Protocol):
    def request(self, position: t.ManagedPosition, context: t.EmergencyContext) -> t.ExitReceipt: ...


class OwnershipService(Protocol):
    def request_transfer(self, request: t.TransferRequest, context: t.ControlContext) -> t.TransferReceipt: ...
    def advance(self, transfer_id: str, evidence: t.TransferEvidence) -> t.TransferProjection: ...


class ManualInterventionService(Protocol):
    def observe(self, event: t.ExternalChange) -> t.InterventionCase: ...


class ExecutionOwner(Protocol):
    def run(self, shard: t.ExecutionShard) -> t.HostExit: ...


class EmergencyJournal(Protocol):
    def append_and_fsync(self, record: t.EmergencyRecord) -> t.DurablePermit: ...


class Supervisor(Protocol):
    def takeover(self, shard: t.ExecutionShard, proof: t.ExternalFenceProof) -> t.TakeoverVerdict: ...


class DecisionScheduler(Protocol):
    def enqueue(self, event: t.DecisionTrigger) -> t.ScheduleReceipt: ...
    def validate_slot(self, slot: t.DecisionSlot) -> t.SlotVerdict: ...


class ModelBudget(Protocol):
    def reserve(self, request: t.ModelCallRequest) -> t.CostPermit: ...


class ReadOnlyTools(Protocol):
    def snapshot(self, ref: t.SnapshotRef) -> t.SnapshotPacket: ...
    def history(self, query: t.HistoryQuery) -> t.HistoryPage: ...


class DecisionOrchestrator(Protocol):
    def research(self, job: t.DecisionJob, tools: ReadOnlyTools) -> t.DecisionBatch: ...


class RuleDraftGenerator(Protocol):
    def generate(self, text: str, context: t.RuleDraftContext) -> t.RuleDraft: ...


class RuleCompiler(Protocol):
    def compile(self, graph: t.RuleGraph) -> t.CompiledRule: ...


class RuleEvaluator(Protocol):
    def evaluate(self, rule: t.CompiledRule, snapshot: t.SnapshotPacket, state: t.RuleState) -> t.RuleEvaluation: ...


class RuleSandbox(Protocol):
    def run(self, artifact: t.RuleArtifact, input: t.RuleInput, limits: t.SandboxLimits) -> t.RuleEvaluation: ...


class ReleaseService(Protocol):
    def propose(self, change: t.StrategyChange) -> t.ReleaseCandidate: ...
    def activate(self, candidate: t.ReleaseCandidate, approval: t.ReleaseApproval) -> t.DeploymentRevision: ...


class MemoryStore(Protocol):
    def append(self, entry: t.MemoryEntry) -> t.MemoryRef: ...


class CompositionCompiler(Protocol):
    def compile(self, policy: t.DecisionComposition) -> t.CompiledComposition: ...


class PortfolioBook(Protocol):
    def compose(self, signals: list[t.DecisionSignal], book: t.BookSnapshot) -> DecisionProposal | None: ...


class ReplayService(Protocol):
    def run(self, config: t.ExperimentConfig, dataset: t.DatasetRef) -> t.ExperimentReport: ...


class PaperRuntime(Protocol):
    def run(self, config: t.PaperRunConfig) -> t.RunReceipt: ...


class MetricService(Protocol):
    def evaluate(self, events: t.EventRange, assumptions: t.MetricAssumptions) -> t.MetricReport: ...


class BinanceProductProfile(Protocol):
    def verify(self, evidence: t.VenueCapabilityEvidence, build: t.QualifiedBuild) -> t.CapabilityProfile: ...


class BinanceAccountReader(Protocol):
    def snapshot(self, query: t.AccountQuery) -> t.BookSnapshot: ...


class StocksEvidenceNormalizer(Protocol):
    def normalize(self, raw: t.RawVenueMessage) -> t.VenueEvent: ...


class StocksCapabilityProbe(Protocol):
    def verify(self, input: t.StocksProbeInput) -> t.CapabilityProfile: ...


class EquityBook(Protocol):
    def apply(self, action: t.CorporateAction, evidence: t.VenueReport) -> t.EquityAdjustment: ...


class MarketSessionService(Protocol):
    def state(self, instrument: t.InstrumentSpec, now: t.ClockReading) -> t.SessionState: ...


class HostedCashProtection(Protocol):
    def tick(self, plan: t.ProtectionPlan, market: t.SnapshotPacket, session: t.SessionState) -> t.ProtectionActionPlan: ...


class ControlCommandService(Protocol):
    def enqueue(self, request: t.ControlCommandRequest, actor: t.ActorContext, key: str) -> t.ControlCommandReceipt: ...


class ControlQuery(Protocol):
    def get(self, command_id: str, actor: t.ActorContext) -> t.ControlCommandProjection: ...


class ProjectionQuery(Protocol):
    def read(self, query: t.ProjectionQueryRequest, actor: t.ActorContext) -> t.ProjectionPage: ...


class EventStream(Protocol):
    def read(self, cursor: t.StreamCursor | None, scope: t.ScopeKey) -> t.EventPage: ...


class ControlApiClient(Protocol):
    def command(self, request: t.ControlCommandRequest, key: str) -> t.ControlCommandReceipt: ...
    def query(self, request: t.ProjectionQueryRequest) -> t.ProjectionPage: ...
    def follow(self, cursor: t.StreamCursor | None) -> Iterator[t.ProjectionEvent]: ...


class TerminalClient(ControlApiClient, Protocol):
    pass


class HealthPublisher(Protocol):
    def snapshot(self) -> t.SystemHealth: ...


class Notifier(Protocol):
    def publish(self, alert: t.RiskAlert) -> t.NotificationReceipt: ...


class DeploymentVerifier(Protocol):
    def check(self, manifest: t.DeploymentManifest) -> t.DeploymentEvidence: ...


class ScenarioDriver(Protocol):
    def run(self, case_id: str, environment: t.VerificationEnvironment) -> t.AcceptanceResult: ...


class AcceptanceRunner(Protocol):
    def run(self, case_id: str, environment: t.VerificationEnvironment, driver: ScenarioDriver) -> t.AcceptanceResult: ...


class QualificationEvaluator(Protocol):
    def evaluate(self, results: list[t.AcceptanceResult], build: t.QualifiedBuild, market: t.Product, authorization: t.Authorization | None) -> t.QualificationReport: ...
