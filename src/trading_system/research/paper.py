"""Offline PAPER forward recording using the shared intent/risk/event domain.

Each run owns virtual balances and an in-memory queue. No EnginePort, durable
permit, account reader, credential resolver, database or network is accepted.
This stage queues risk-approved simulated targets; it does not invent fills.
Native matching is NOT_RUN and a queued target is never reported as filled.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json, domain_payload, utc_timestamp
from trading_system.intents.ingress import IntentCompiler
from trading_system.intents.semantic_validation import BindingInputs, ProposalRejected
from trading_system.intents.target_compiler import TargetCompiler, TargetInputs
from trading_system.market.provenance import is_digest
from trading_system.risk.evaluator import RiskEvaluator, RiskInputs

from .experiments import (DatasetArchive, ResearchError, check_ref, fingerprint, require,
                          NativeBuildMapping, native_matching_unavailable)


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperFrame:
    proposal: t.DecisionProposal
    context: t.DecisionContext
    book: t.BookSnapshot
    binding: BindingInputs
    target: TargetInputs
    risk_inputs: RiskInputs
    policy: t.RiskPolicy


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperIsolation:
    environment: t.Environment
    venue_account_id: str
    queue_namespace: str
    ledger_namespace: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperObservation:
    proposal: t.DecisionProposal
    recorded_at: str
    input_hash: str
    intent: t.TradeIntent | None
    risk: t.RiskDecision | None
    plan: t.DeltaPlan | None
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperArtifact:
    config: t.PaperRunConfig
    isolation: PaperIsolation
    frames: tuple[PaperFrame, ...]
    observations: tuple[PaperObservation, ...]
    queue: tuple[t.TradeIntent, ...]
    available: tuple[t.Money, ...]
    held: tuple[t.Money, ...]
    events: tuple[t.DomainEvent, ...]
    native_matching: str = 'NOT_RUN'
    evidence_source: str = 'PAPER_LOCAL_SIMULATION'


class PaperRuntime:
    def __init__(self, datasets: DatasetArchive, frames: tuple[PaperFrame, ...], *,
                 account_id: str, created_at: str):
        require(bool(account_id) and isinstance(frames, tuple), 'PAPER_INPUTS_REQUIRED')
        utc_timestamp(created_at)
        self._datasets, self._frames = datasets, frames
        identifier = str(uuid4())
        self.isolation = PaperIsolation(environment=t.Environment.PAPER, venue_account_id=account_id,
            queue_namespace='paper:queue:' + identifier, ledger_namespace='paper:ledger:' + identifier)
        self.isolation_ref = t.EvidenceRef(id='paper:isolation:' + identifier, hash=fingerprint(self.isolation),
            environment=t.Environment.PAPER, source='PAPER_PROCESS_LOCAL_ISOLATION', visible_at=created_at)
        self._artifacts: dict[str, PaperArtifact] = {}

    def artifact(self, run_id: str) -> PaperArtifact:
        return self._artifacts[run_id]

    def artifact_json(self, run_id: str) -> str:
        artifact = self.artifact(run_id)
        return canonical_json({'artifact': domain_payload(artifact), 'sha256': fingerprint(artifact)})

    def request_native_matching(self, config: t.PaperRunConfig,
                                mapping: NativeBuildMapping | None = None) -> None:
        require(config.environment == t.Environment.PAPER, 'PAPER_ONLY')
        native_matching_unavailable(mapping, config.build_digest, config.environment)

    def _validate(self, config):
        require(config.environment == t.Environment.PAPER and config.dataset.environment == t.Environment.PAPER,
                'PAPER_ONLY')
        require(config.isolation_ref == self.isolation_ref, 'PAPER_ISOLATION_REFERENCE_MISMATCH')
        require(is_digest(config.build_digest) and is_digest(config.config_hash), 'PAPER_FINGERPRINT_REQUIRED')
        data = self._datasets.load(config.dataset)
        require(data.scope.venue_account_id == self.isolation.venue_account_id, 'PAPER_ACCOUNT_MISMATCH')
        require(isinstance(config.virtual_capital, tuple) and bool(config.virtual_capital), 'VIRTUAL_CAPITAL_REQUIRED')
        balances = {}
        for money in config.virtual_capital:
            require(isinstance(money, t.Money) and money.amount >= 0
                    and money.currency not in balances, 'INVALID_VIRTUAL_CAPITAL')
            balances[money.currency] = money.amount
        require(any(value > 0 for value in balances.values()), 'POSITIVE_VIRTUAL_CAPITAL_REQUIRED')
        require(utc_timestamp(self.isolation_ref.visible_at) <= utc_timestamp(data.start),
                'ISOLATION_CREATED_AFTER_RUN_START')
        previous = None
        for frame in self._frames:
            require(isinstance(frame, PaperFrame), 'TYPED_PAPER_FRAME_REQUIRED')
            require(frame.policy.live_enabled is False and frame.policy.native_risk_bypass is False,
                    'PAPER_POLICY_SAFETY_FLAGS')
            # Validate all supplied host bindings before processing any frame.
            # An evidence label or dataset text never selects an external queue.
            def check_tree(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in ('environment', 'queue_environment'):
                            require(item == 'PAPER', 'FOREIGN_ENVIRONMENT_IN_PAPER_INPUT')
                        if key == 'venue_account_id':
                            require(item == self.isolation.venue_account_id, 'FOREIGN_ACCOUNT_IN_PAPER_INPUT')
                        check_tree(item)
                elif isinstance(value, list):
                    for item in value:
                        check_tree(item)
            check_tree(domain_payload(frame))
            at = utc_timestamp(frame.binding.clock.utc_now)
            require(utc_timestamp(data.start) <= at <= utc_timestamp(data.end)
                    and (previous is None or previous <= at), 'PAPER_FRAME_TIME_INVALID')
            previous = at
            require(frame.book.scope == data.scope and frame.binding.snapshot in data.snapshots,
                    'PAPER_DATASET_BINDING_MISMATCH')
            require(frame.context.environment == t.Environment.PAPER
                    and frame.context.scopes == (data.scope,), 'PAPER_CONTEXT_MISMATCH')
            require(frame.target.context == frame.context, 'PAPER_TARGET_CONTEXT_MISMATCH')
            require(frame.target.clock == frame.binding.clock == frame.risk_inputs.clock,
                    'PAPER_CLOCK_MISMATCH')
            require(frame.target.instrument == frame.binding.instrument == frame.risk_inputs.instrument,
                    'PAPER_INSTRUMENT_MISMATCH')
            require(not frame.book.positions and not frame.book.active_orders and not frame.book.reservations,
                    'PAPER_INITIAL_BOOK_MUST_BE_FLAT')
            require(dict((m.currency, m.amount) for m in frame.book.balances) == balances
                    and len(frame.book.balances) == len(balances), 'VIRTUAL_BOOK_BALANCE_MISMATCH')
            require(frame.risk_inputs.spendable == frame.book.balances, 'VIRTUAL_SPENDABLE_MISMATCH')
            for ref in frame.book.evidence + frame.binding.evidence + frame.risk_inputs.evidence:
                check_ref(ref, t.Environment.PAPER)
        return balances

    def run(self, config: t.PaperRunConfig) -> t.RunReceipt:
        balances = self._validate(config)
        held = {currency: Decimal(0) for currency in balances}
        observations, queue, events = [], [], []
        seen_proposals, pending_scopes = set(), set()
        run_id = 'paper:' + str(uuid4())
        # Per-run queues/balances never alias a prior run or a production ledger.
        for frame in self._frames:
            intent = risk = plan = None
            status, reasons = 'REJECTED', ()
            at = frame.binding.clock.utc_now
            try:
                require(frame.proposal.proposal_id not in seen_proposals, 'DUPLICATE_PAPER_PROPOSAL')
                seen_proposals.add(frame.proposal.proposal_id)
                require(frame.book.scope not in pending_scopes, 'PENDING_PAPER_TARGET')
                intent = IntentCompiler(frame.binding).bind(frame.proposal, frame.context, frame.book)
                plan = TargetCompiler(frame.target).compile(intent, frame.book)
                if plan.classification == 'NONE':
                    status = 'NO_CHANGE'
                else:
                    require(not plan.cancel_first and plan.classification != 'REVERSAL_REQUIRES_FLAT',
                            'PAPER_TARGET_REQUIRES_RECONCILIATION')
                    facts = replace(frame.risk_inputs, intent_id=intent.intent_id)
                    risk = RiskEvaluator(facts).evaluate(intent, frame.book, frame.binding.snapshot, frame.policy)
                    require(risk.verdict == 'ALLOW', 'RISK:' + ','.join(risk.rejection_codes))
                    require(risk.permitted_quantity.unit == plan.target.unit
                            and risk.permitted_quantity.value == plan.target.value.copy_abs(),
                            'PAPER_RISK_TARGET_MISMATCH')
                    amounts = dict(balances)
                    reserved = dict(held)
                    for money in risk.reserved_assets:
                        require(money.currency in amounts and money.amount >= 0, 'UNKNOWN_VIRTUAL_ASSET')
                        current = t.Money(amount=amounts[money.currency], currency=money.currency)
                        remaining = current + t.Money(amount=money.amount.copy_negate(), currency=money.currency)
                        require(remaining.amount >= 0, 'VIRTUAL_CAPITAL_EXHAUSTED')
                        amounts[money.currency] = remaining.amount
                        reserved[money.currency] = (t.Money(amount=reserved[money.currency], currency=money.currency)
                                                   + money).amount
                    identity = t.OrderIdentity(scope=frame.book.scope,
                        logical_client_order_id=run_id + ':' + str(len(queue)),
                        native_client_order_id='', venue_client_order_id='', venue_order_id=None)
                    projection = t.OrderProjection(order=t.OwnedOrderRef(identity=identity,
                        owner_strategy_id=intent.owner_strategy_id, ownership_epoch=intent.ownership_epoch),
                        lifecycle_state=t.OrderLifecycle.INITIALIZED, pending_operation='NONE',
                        command_certainty='NOT_SENT', outcome_provenance='LOCAL_POLICY',
                        filled_quantity=t.Quantity(value=Decimal(0), unit=intent.quantity_unit),
                        leaves_quantity=t.Quantity(value=plan.remaining.value.copy_abs(), unit=intent.quantity_unit),
                        lifecycle_revision=1, sequence=len(events) + 1)
                    ref = t.EvidenceRef(id='paper:queued:' + intent.intent_id, hash=fingerprint(projection),
                        environment=t.Environment.PAPER, source='PAPER_LOCAL_SIMULATION', visible_at=at)
                    event = t.DomainEvent(event_id=ref.id, scope=frame.book.scope,
                        aggregate_id=identity.logical_client_order_id, revision=1, occurred_at=at,
                        evidence=(ref,), payload=projection)
                    balances, held = amounts, reserved
                    events.append(event)
                    queue.append(intent)
                    pending_scopes.add(frame.book.scope)
                    status = 'QUEUED'
            except (ResearchError, ProposalRejected) as exc:
                reasons = (str(exc),)
            observations.append(PaperObservation(proposal=frame.proposal, recorded_at=at,
                input_hash=fingerprint(frame), intent=intent, risk=risk, plan=plan, status=status, reasons=reasons))
        artifact = PaperArtifact(config=config, isolation=self.isolation, frames=self._frames,
            observations=tuple(observations),
            queue=tuple(queue), available=tuple(t.Money(amount=value, currency=currency)
                for currency, value in sorted(balances.items())), held=tuple(t.Money(amount=value, currency=currency)
                for currency, value in sorted(held.items())), events=tuple(events))
        digest = fingerprint(artifact)
        ref = t.EvidenceRef(id='paper:artifact:' + run_id, hash=digest, environment=t.Environment.PAPER,
            source='PAPER_LOCAL_SIMULATION', visible_at=config.dataset.end)
        self._artifacts[run_id] = artifact
        status = 'QUEUED' if queue else 'FAILED' if any(item.status == 'REJECTED' for item in observations) else 'COMPLETED'
        return t.RunReceipt(run_id=run_id, status=status, evidence=(ref,))
