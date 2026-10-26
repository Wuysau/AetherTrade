"""Synthetic inputs driving real offline IP-18 components, not qualification."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec
from trading_system.decision.cost_budget import ModelBudget
from trading_system.decision.scheduler import DecisionScheduler, Priority


class Clock:
    def __init__(self):
        self.utc = datetime(2026, 10, 22, tzinfo=timezone.utc)
        self.ns = 1_000_000_000
        self.health = 'HEALTHY'

    def at(self, seconds=0):
        return (self.utc + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')

    def __call__(self):
        return t.ClockReading(utc_now=self.at(), monotonic_ns=self.ns, clock_health=self.health)

    def advance(self, seconds):
        self.utc += timedelta(seconds=seconds)
        self.ns += seconds * 1_000_000_000


def money(value, currency='USD'):
    return t.Money(amount=Decimal(value), currency=currency)


def trigger(clock, *, epoch=1, priority=Priority.NEW_OPPORTUNITY,
            instrument='BTC', key='event', ttl=60):
    return t.DecisionTrigger(
        trigger_id=key, deduplication_key=key, priority=priority,
        slot=t.DecisionSlot(strategy_id='strategy', instrument_or_portfolio_id=instrument,
                            decision_epoch=epoch, expires_at=clock.at(ttl)),
        evidence=t.EventRef(id=key, hash='a' * 64, environment=t.Environment.PAPER,
                            source='offline-test', visible_at=clock.at()))


def request(clock, request_id='call', cost='2', ttl=60):
    return t.ModelCallRequest(request_id=request_id, provider='offline', model='local-fixture',
                              task='research', maximum_cost=money(cost), snapshot_refs=(),
                              expires_at=clock.at(ttl))


def budget(clock, **overrides):
    options = dict(maximum_calls=10, maximum_tokens=1000, tokens_per_call=100,
                   maximum_runtime_ns=30_000_000_000)
    options.update(overrides)
    return ModelBudget(money('10'), clock, **options)


def proposal(clock, ttl=60):
    root = Path(__file__).resolve().parents[2]
    path = root / 'tests/fixtures/contracts/examples/decision-proposal.paper.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['valid_until'] = clock.at(ttl)
    data['entry']['entry_deadline'] = clock.at(ttl)
    data['exit_plan']['review_due_at'] = clock.at(ttl + 60)
    return ContractCodec().decode('decision-proposal', data)


@dataclass
class SchedulerCase:
    clock: Clock
    scheduler: DecisionScheduler

    def enqueue_same_slot(self, count):
        event = trigger(self.clock)
        for _ in range(count):
            self.scheduler.enqueue(event)
        return self.scheduler


@pytest.fixture
def scheduler_case():
    clock = Clock()
    return SchedulerCase(clock, DecisionScheduler(clock, environment=t.Environment.PAPER,
                                                  maximum_runtime_ns=30_000_000_000))
