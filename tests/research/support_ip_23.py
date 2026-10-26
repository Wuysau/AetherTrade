"""Synthetic offline inputs for IP-23 production code, never venue evidence.

No model SDK, account lookup, durable ledger or native matcher is exercised.
The inference adapter records its actual input and constructs a typed output;
all reports, views, intents, risk verdicts and metrics come from production.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
import json
from pathlib import Path

from trading_system.accounts.authorization import AuthorizationBinding
from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec
from trading_system.intents.semantic_validation import BindingInputs
from trading_system.intents.target_compiler import TargetInputs
from trading_system.market.snapshots import snapshot_content_hash
from trading_system.research.experiments import (
    Dataset, DatasetArchive, ExperimentArchive, ExperimentMetadata,
    RecordedDecision, TemporalRecord, fingerprint,
)
from trading_system.research.metrics import (
    EquityObservation, EventSequence, MetricInputs, ProtectionInterval,
)
from trading_system.research.paper import PaperFrame, PaperRuntime
from trading_system.research.replay import ReplayService, point_in_time
from trading_system.risk.evaluator import GroupLimit, RiskInputs
from trading_system.risk.exposure import ExposureMark

D = Decimal
START = '2026-10-23T06:39:00Z'
NOW = '2026-10-23T06:39:01Z'
MID = '2026-10-23T06:39:30Z'
END = '2026-10-23T06:40:00Z'
REVIEW = '2026-10-23T06:45:00Z'
MODEL = 'synthetic-offline-model-v1'
PROMPT = 'a' * 64
RULE = 'b' * 64
BUILD = 'c' * 64
CONFIG = 'd' * 64


def money(value='0', currency='USDT'):
    return t.Money(amount=D(value), currency=currency)


def quantity(value='0', unit='BASE'):
    return t.Quantity(value=D(value), unit=unit)


def price(value='50000', currency='USDT'):
    return t.Price(value=D(value), currency=currency)


def evidence(identifier='fixture', at=START, environment=t.Environment.PAPER):
    return t.EvidenceRef(id=identifier, hash='e' * 64, environment=environment,
        source='IP23_SYNTHETIC_OFFLINE', visible_at=at)


def scope(environment=t.Environment.PAPER):
    return t.ScopeKey(environment=environment, venue_account_id='paper-account-demo',
        product=t.Product.SPOT, instrument_uid='paper-binance-spot-btcusdt', venue_leg='BOTH')


def coverage(bound_scope):
    return t.Coverage(scope=bound_scope, query_succeeded=True,
        pagination_complete=True, supported=True, missing=())


def approved_example(name):
    root = Path(__file__).resolve().parents[2] / 'tests/fixtures/contracts/examples'
    return ContractCodec().decode(name, json.loads(
        (root / (name + '.paper.json')).read_text(encoding='utf-8')))


def proposal():
    sample = approved_example('decision-proposal')
    return replace(sample, valid_until=END,
        entry=replace(sample.entry, entry_deadline=END),
        exit_plan=replace(sample.exit_plan, review_due_at=REVIEW))


def snapshot(bound_scope=None):
    bound_scope = bound_scope or scope()
    ref = t.SnapshotRef(id='paper-snapshot-demo', hash='0' * 64,
        environment=bound_scope.environment, source='IP23_SYNTHETIC_OFFLINE', visible_at=START)
    packet = t.SnapshotPacket(ref=ref, scope=bound_scope,
        quote=t.Quote(bid=price(), ask=price(), last=price(), occurred_at=START, received_at=START),
        private_refs=(), economic_refs=(), published_at=START, revision_visible_at=START,
        coverage=coverage(bound_scope))
    return replace(packet, ref=replace(ref, hash=snapshot_content_hash(packet)))


def temporal(key, category='NEWS', revision=1, at=START, content='original'):
    return TemporalRecord(logical_key=key, category=category, source='synthetic-public-source',
        revision=revision, published_at=START, revised_at=at, downloaded_at=at,
        effective_at=START, content=content)


def dataset():
    packet = snapshot()
    return Dataset(scope=packet.scope, start=START, end=END, decision_times=(NOW,),
        records=(temporal('news'), temporal('news', revision=2, at=END, content='future result'),
            temporal('membership', 'CONSTITUENT', content='included'),
            replace(temporal('new-member', 'CONSTITUENT'), effective_at=END),
            temporal('membership', 'CONSTITUENT', revision=2, at=END, content='removed'),
            temporal('fee', 'FEE_SCHEDULE', content='historical fee'),
            temporal('fee', 'FEE_SCHEDULE', revision=2, at=END, content='current fee'),
            replace(temporal('late-download'), downloaded_at=END)),
        snapshots=(packet,), recorded_outputs=(), evidence=(evidence(),))


def recorded(view, kind='FORWARD_RECORDED'):
    return RecordedDecision(decision_at=view.as_of, recorded_at=view.as_of, kind=kind,
        view_hash=fingerprint(view), proposal=proposal(), model_fingerprint=MODEL,
        prompt_hash=PROMPT, rule_hash=RULE, cost=money('1'))


class OfflineInference:
    def __init__(self):
        self.views = []

    def __call__(self, view):
        self.views.append(view)
        output = recorded(view, 'NEW_MODEL_INFERENCE')
        return replace(output, proposal=replace(output.proposal,
            rationale=replace(output.proposal.rationale,
                summary='New offline inference; historical knowledge may leak.')))


@dataclass
class ExperimentCase:
    data: Dataset
    datasets: DatasetArchive
    artifacts: ExperimentArchive
    ref: t.DatasetRef
    config: t.ExperimentConfig
    metadata: ExperimentMetadata
    inference: OfflineInference

    def service(self):
        return ReplayService(self.datasets, self.artifacts, self.metadata, infer=self.inference)

    def replay_and_new_model_call(self):
        replay = self.service().run(self.config, self.ref)
        reinfer = self.service().run(replace(self.config, mode='REINFER', proposal_refs=()), self.ref)
        return self.artifacts.get(replay.run_id), self.artifacts.get(reinfer.run_id)


def experiment_case():
    data = dataset()
    output = recorded(point_in_time(data, NOW))
    data = replace(data, recorded_outputs=(output,))
    archive = DatasetArchive()
    ref = archive.put(data)
    config = t.ExperimentConfig(mode='REPLAY_OUTPUTS', build_digest=BUILD, config_hash=CONFIG,
        model_fingerprint=MODEL, proposal_refs=(output.reference(data.scope.environment),))
    metadata = ExperimentMetadata(prompt_hash=PROMPT, rule_hash=RULE,
        cost_assumptions=t.MetricAssumptions(valuation_currency='USDT',
            fees=(), funding=(), model_costs=(), evidence=(evidence(),)))
    return ExperimentCase(data, archive, ExperimentArchive(), ref, config, metadata, OfflineInference())


def paper_frame():
    packet = snapshot()
    bound_scope = packet.scope
    ref = evidence()
    context = t.DecisionContext(identity_id='offline-user', environment=t.Environment.PAPER,
        deployment_id='offline-deployment', authorization_id='offline-authorization',
        policy_revision=1, ownership_epoch=1, scopes=(bound_scope,), strategy_revision=1,
        decision_run_id='offline-decision', snapshot_ref=packet.ref)
    grant = t.Authorization(authorization_id=context.authorization_id, owner_strategy_id='demo-ai-strategy',
        environment=context.environment, deployment_id=context.deployment_id, scopes=context.scopes,
        maximum_capital=money('1000'), policy_revision=1, protection_options=('NATIVE_REQUIRED',),
        model_cost_budget=money('2'), valid_from=START, expires_at=END, revoked_at=None)
    authorization = AuthorizationBinding(authorization=grant, identity_id=context.identity_id,
        strategy_revision=1, ownership_epoch=1, queue_environment=t.Environment.PAPER,
        capabilities=('NATIVE_STOP',), evidence=(ref,))
    spec = t.InstrumentSpec(instrument_uid=bound_scope.instrument_uid, venue='BINANCE',
        product=t.Product.SPOT, native_symbol='SYNTHETIC', tick_size=price('0.01'),
        quantity_step=quantity('0.001'), minimum_quantity=quantity('0.001'),
        minimum_notional=money('1'), rule_revision=1, quote_currency='USDT', settlement_currency='USDT')
    clock = t.ClockReading(utc_now=NOW, monotonic_ns=100, clock_health='HEALTHY')
    session = t.SessionState(instrument_uid=bound_scope.instrument_uid, session_id='offline-session',
        state='OPEN', observed_at=START, next_open_at=None, evidence=ref)
    balances = (money('1000'),)
    book = t.BookSnapshot(scope=bound_scope, positions=(), active_orders=(), reservations=(),
        balances=balances, sequence=1, basis_fingerprint='f' * 64,
        coverage=coverage(bound_scope), evidence=(ref,))
    binding = BindingInputs(authorization=authorization, instrument_ref=bound_scope.instrument_uid,
        instrument=spec, collateral_group_id='paper-group-demo', snapshot=packet,
        freshness=t.DataVerdict(usable=True, age_ms=1000, missing=(), rejection_codes=()),
        evidence=(packet.ref, ref), clock=clock, deadline_monotonic_ns=1000,
        maximum_valid_until=END, max_planned_loss=money('5'), protection_policy='NATIVE_REQUIRED',
        allowed_stop_triggers=('LAST',))
    target = TargetInputs(instrument=spec, expected_instrument=spec, clock=clock,
        deadline_monotonic_ns=1000, session=session, context=context)
    risk = RiskInputs(intent_id='bound-at-runtime', authorization_id=context.authorization_id,
        owner_strategy_id=grant.owner_strategy_id, ownership_epoch=1, policy_revision=1,
        scope=bound_scope, collateral_group_id='paper-group-demo', basis_fingerprint=book.basis_fingerprint,
        book_sequence=1, snapshot_ref=packet.ref, clock=clock, expires_at=END, instrument=spec,
        session=session, multiplier=D(1), base_asset='BTC', fee_reserve=(money('0.1'),),
        funding_reserve=(money('0'),), existing_planned_loss=money(), capital_in_use=money(),
        spendable=balances, fx_rates=(), marks=(ExposureMark(scope=bound_scope, price=price(),
            multiplier=D(1), unit='BASE', related_group='crypto'),), order_sides=(),
        group_limits=(GroupLimit(group_id='crypto', maximum_gross=money('800')),),
        authorized_margin_mode='CASH', observed_margin_mode='CASH',
        authorized_position_mode='ONE_WAY', observed_position_mode='ONE_WAY',
        new_risk_allowed=True, allow_round_down=False, emergency=None, evidence=(ref,))
    return PaperFrame(proposal=proposal(), context=context, book=book, binding=binding,
        target=target, risk_inputs=risk, policy=approved_example('risk-policy'))


def paper_case(frames=None, data=None):
    frames = (paper_frame(),) if frames is None else frames
    data = dataset() if data is None else data
    datasets = DatasetArchive()
    ref = datasets.put(data)
    runtime = PaperRuntime(datasets, frames, account_id=data.scope.venue_account_id, created_at=START)
    config = t.PaperRunConfig(environment=t.Environment.PAPER, virtual_capital=(money('1000'),),
        build_digest=BUILD, config_hash=CONFIG, dataset=ref, isolation_ref=runtime.isolation_ref)
    return runtime, config


def order(identifier='order', certainty='UNKNOWN', sequence=1):
    identity = t.OrderIdentity(scope=scope(), logical_client_order_id=identifier,
        native_client_order_id='', venue_client_order_id='', venue_order_id=None)
    return t.OrderProjection(order=t.OwnedOrderRef(identity=identity,
        owner_strategy_id='demo-ai-strategy', ownership_epoch=1),
        lifecycle_state=t.OrderLifecycle.ACCEPTED, pending_operation='NONE',
        command_certainty=certainty, outcome_provenance='LOCAL_POLICY',
        filled_quantity=quantity(), leaves_quantity=quantity('2'),
        lifecycle_revision=sequence, sequence=sequence)


def fill(identifier='fill', value='2', correction_of=None):
    return t.Fill(fill_id=identifier, venue='SYNTHETIC', scope=scope(), native_trade_id=None,
        order=order().order.identity, quantity=quantity(value), price=price('10'),
        fees=(money('1'),), occurred_at=NOW, correction_of=correction_of,
        evidence=evidence('simulated-fill', NOW))


def event(payload, identifier='fill-event', at=NOW, revision=1):
    refs = (payload.evidence,) if isinstance(payload, t.Fill) else (evidence(identifier, at),)
    return t.DomainEvent(event_id=identifier, scope=scope(), aggregate_id='order',
        revision=revision, occurred_at=at, evidence=refs, payload=payload)


def rebind(inputs, events, assumptions):
    return replace(inputs, event_range_hash=fingerprint(events), assumptions_hash=fingerprint(assumptions))


def metric_case():
    events = t.EventRange(scope=scope(), first_sequence=1, last_sequence=2,
        events=(event(fill()), event(order(certainty='CONFIRMED', sequence=2), 'resolved', MID, 2)))
    assumptions = t.MetricAssumptions(valuation_currency='USDT', fees=(money('1'),),
        funding=(money('1'),), model_costs=(money('1'),), evidence=(evidence(),))
    inputs = MetricInputs(event_range_hash=fingerprint(events), assumptions_hash=fingerprint(assumptions),
        start=START, end=END, evidence=(evidence(),),
        event_sequences=(EventSequence(event_id='fill-event', sequence=1),
                         EventSequence(event_id='resolved', sequence=2)),
        equity=(EquityObservation(at=START, gross_equity=money('100'),
                    cumulative_external_flow=money(), cumulative_cost=money()),
                EquityObservation(at=MID, gross_equity=money('130'),
                    cumulative_external_flow=money('10'), cumulative_cost=money('2')),
                EquityObservation(at=END, gross_equity=money('120'),
                    cumulative_external_flow=money('10'), cumulative_cost=money('3'))),
        fills_complete=True, fees_complete=True, funding_complete=True,
        model_costs_complete=True, orders_complete=True, initial_orders=(order(),),
        protection=(ProtectionInterval(start=START, end=MID, covered=True),
                    ProtectionInterval(start=MID, end=END, covered=False)))
    return events, assumptions, inputs
