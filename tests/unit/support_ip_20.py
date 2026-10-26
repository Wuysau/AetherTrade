"""Synthetic rule graphs and snapshots for IP-20 unit checks.

Values are fabricated, fixed and offline: no venue, account, credential,
network or order path is touched, and nothing here is qualification evidence.
The fixture always drives the real `RuleCompiler`/`RuleEvaluator`; it never
returns a precomputed verdict.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.rules.evaluator import RuleEvaluator
from trading_system.rules.graph import RuleCompiler

BASE = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
SPOT_INSTRUMENT = 'binance:spot:BTCUSDT'
PERP_INSTRUMENT = 'binance:usdm:BTCUSDT'


def iso_at(offset_seconds: int = 0) -> str:
    return (BASE + timedelta(seconds=offset_seconds)).isoformat()


def moment(offset_seconds: int = 0) -> datetime:
    return BASE + timedelta(seconds=offset_seconds)


def snapshot_digest(packet: t.SnapshotPacket) -> str:
    """Content digest, excluding the reference that names the content."""
    payload = domain_payload(packet)
    payload.pop('ref', None)
    return canonical_hash(payload)


def _price(value: str | None, currency: str) -> t.Price | None:
    return None if value is None else t.Price(value=Decimal(value), currency=currency)


def make_snapshot(*, occurred_offset_s: int = 10, visible_offset_s: int = 12,
                  published_offset_s: int | None = None,
                  bid: str | None = '64000.10', ask: str | None = '64000.20',
                  last: str | None = '64000.15', currency: str = 'USDT',
                  product: t.Product = t.Product.SPOT,
                  instrument: str = SPOT_INSTRUMENT,
                  account: str = 'paper-account-1',
                  environment: t.Environment = t.Environment.PAPER,
                  missing: tuple[str, ...] = (),
                  query_succeeded: bool = True,
                  pagination_complete: bool = True,
                  supported: bool = True) -> t.SnapshotPacket:
    if published_offset_s is None:
        published_offset_s = occurred_offset_s + 1
    scope_key = t.ScopeKey(environment=environment, venue_account_id=account,
                           product=product, instrument_uid=instrument,
                           venue_leg='BOTH')
    placeholder = t.SnapshotRef(id='', hash='', environment=environment,
                                source='offline-fixture',
                                visible_at=iso_at(visible_offset_s))
    packet = t.SnapshotPacket(
        ref=placeholder, scope=scope_key,
        quote=t.Quote(bid=_price(bid, currency), ask=_price(ask, currency),
                      last=_price(last, currency),
                      occurred_at=iso_at(occurred_offset_s),
                      received_at=iso_at(occurred_offset_s)),
        private_refs=(), economic_refs=(),
        published_at=iso_at(published_offset_s),
        revision_visible_at=iso_at(visible_offset_s),
        coverage=t.Coverage(scope=scope_key, query_succeeded=query_succeeded,
                            pagination_complete=pagination_complete,
                            supported=supported, missing=missing))
    digest = snapshot_digest(packet)
    ref = t.SnapshotRef(id=f'snap-{digest[:16]}', hash=digest, environment=environment,
                        source='offline-fixture',
                        visible_at=iso_at(visible_offset_s))
    return replace(packet, ref=ref)


class RecordingEngine:
    """Would-be order port; the rule evaluator is never handed one."""

    def __init__(self):
        self.calls: list[str] = []

    def submit(self, command, permit):
        self.calls.append('submit')
        raise AssertionError('rule evaluation must not submit orders')

    def cancel(self, command, permit):
        self.calls.append('cancel')
        raise AssertionError('rule evaluation must not cancel orders')

    def amend(self, command, permit):
        self.calls.append('amend')
        raise AssertionError('rule evaluation must not amend orders')

    def query(self, query):
        self.calls.append('query')
        raise AssertionError('rule evaluation must not query a venue')


class RuleCase:
    """Builds typed graphs/snapshots and evaluates them through production code."""

    def __init__(self) -> None:
        self.compiler = RuleCompiler()
        self.evaluator = RuleEvaluator()
        self.engine = RecordingEngine()
        self.evaluations: list[t.RuleEvaluation] = []

    def condition(self, *, operator: str = 'GT', input_ref: str | None = 'quote.last',
                  threshold: object = '64000.00',
                  children: tuple[t.RuleCondition, ...] = ()) -> t.RuleCondition:
        if isinstance(threshold, str):
            threshold = Decimal(threshold)
        return t.RuleCondition(operator=operator, input_ref=input_ref,
                               threshold=threshold, children=children)

    def graph(self, *, condition: t.RuleCondition | None = None,
              operator: str = 'GT', input_ref: str | None = 'quote.last',
              threshold: object = '64000.00',
              children: tuple[t.RuleCondition, ...] = (),
              **overrides) -> t.RuleGraph:
        if condition is None:
            condition = self.condition(operator=operator, input_ref=input_ref,
                                       threshold=threshold, children=children)
        target = overrides.pop('target_value', '0.001')
        if isinstance(target, str):
            target = Decimal(target)
        fields: dict[str, object] = {
            'rule_id': 'ip20-rule',
            'revision': 1,
            'instrument_ref': SPOT_INSTRUMENT,
            'condition': condition,
            'trigger': 'EDGE',
            'window_seconds': 60,
            'closed_bar_only': False,
            'cooldown_seconds': 0,
            'sizing': 'BASE_QUANTITY',
            'target_value': target,
            'exit_priority': ('protection', 'ordinary'),
            'missing_data': 'HOLD',
        }
        fields.update(overrides)
        return t.RuleGraph(**fields)  # type: ignore[arg-type]

    def compile(self, **overrides) -> t.CompiledRule:
        return self.compiler.compile(self.graph(**overrides))

    def state(self, *, previous_condition: bool | None = None,
              last_triggered_at: str | None = None, last_bar_id: str | None = None,
              revision: int = 1) -> t.RuleState:
        return t.RuleState(previous_condition=previous_condition,
                           last_triggered_at=last_triggered_at,
                           last_bar_id=last_bar_id, revision=revision)

    def snapshot(self, **overrides) -> t.SnapshotPacket:
        return make_snapshot(**overrides)

    def evaluate(self, rule: t.CompiledRule | None = None,
                 snapshot: t.SnapshotPacket | None = None,
                 state: t.RuleState | None = None) -> t.RuleEvaluation:
        rule = self.compile() if rule is None else rule
        snapshot = self.snapshot() if snapshot is None else snapshot
        state = self.state() if state is None else state
        evaluation = self.evaluator.evaluate(rule, snapshot, state)
        self.evaluations.append(evaluation)
        return evaluation

    def evaluate_same_input(self) -> t.RuleEvaluation:
        """Fixed rule revision, snapshot and state; used for replay checks."""
        return self.evaluate(self.compile(),
                             self.snapshot(occurred_offset_s=10, visible_offset_s=12),
                             self.state(previous_condition=False))


@pytest.fixture
def rule_case() -> RuleCase:
    return RuleCase()
