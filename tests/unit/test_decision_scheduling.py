"""Offline scheduling probes only. T58/T60 and all Q gates remain NOT_RUN."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from trading_system.contracts import types as t
from trading_system.decision.cost_budget import BudgetDenied
from trading_system.decision.scheduler import DecisionScheduler, NoTradeReason, Priority
from unit.support_ip_18 import (
    Clock, budget, money, proposal, request, scheduler_case, trigger,
)


def test_duplicate_events_coalesced(scheduler_case):
    assert scheduler_case.enqueue_same_slot(100).active_jobs == 1
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(scheduler.enqueue, [trigger(clock)] * 100))
    assert len({receipt.job_id for receipt in receipts}) == 1
    assert scheduler.active_jobs == 1
    assert scheduler.enqueue(trigger(clock, key='different-event')).coalesced


def test_late_model_response_expired(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    first = scheduler.enqueue(trigger(clock))
    assert scheduler.next_job() == first.job_id
    output = proposal(clock)
    newer = scheduler.enqueue(trigger(clock, priority=Priority.SAFETY, key='safety'))
    assert scheduler.slot_for(newer.job_id).decision_epoch == 2
    assert not scheduler.complete(first.job_id).valid
    assert not scheduler.validate_proposal(first.job_id, output).valid
    assert scheduler.next_job() == newer.job_id
    clock.advance(30)
    assert not scheduler.complete(newer.job_id).valid
    assert not scheduler.validate_proposal(newer.job_id, output).valid
    assert scheduler.active_jobs == 0


def test_cost_unknown_blocks_new_paid_calls(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    model_budget = budget(clock)
    permit = model_budget.reserve(request(clock))
    model_budget.start(permit)
    model_budget.mark_unknown('call')
    assert model_budget.committed == money('2')
    with pytest.raises(BudgetDenied, match='COST_UNKNOWN'):
        model_budget.reserve(request(clock, 'retry'))
    # Safety scheduling has no model-budget or LLM dependency. This does not
    # simulate or assert native/hosted order protection; that remains NOT_RUN.
    safety = scheduler.enqueue(trigger(clock, priority=Priority.SAFETY))
    assert scheduler.next_job() == safety.job_id
    assert model_budget.committed == money('2')
    model_budget.settle('call', money('1'))
    assert model_budget.reserve(request(clock, 'retry')).request_id == 'retry'


def test_position_review_before_new_research(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    ids = {}
    for priority in Priority:
        ids[priority] = scheduler.enqueue(trigger(clock, priority=priority,
                                                  instrument=priority.name)).job_id
    assert [scheduler.next_job() for _ in Priority] == [ids[p] for p in reversed(list(Priority))]
    assert scheduler.next_job() is None


def test_each_proposal_revalidated_and_cancellation_has_no_execution_side_effect(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    job = scheduler.enqueue(trigger(clock)).job_id
    good = proposal(clock)
    stale = replace(proposal(clock, ttl=1), proposal_id='short-lived')
    assert not scheduler.validate_proposal(job, good).valid
    scheduler.next_job()
    assert scheduler.complete(job).valid
    assert scheduler.validate_proposal(job, good).valid
    assert scheduler.validate_proposal(job, stale).valid
    clock.advance(1)
    assert not scheduler.validate_proposal(job, stale).valid
    assert scheduler.validate_proposal(job, good).valid
    scheduler.cancel(job)
    assert not scheduler.validate_proposal(job, good).valid
    assert good.valid_until == clock.at(59)  # No mutation of independent output.


def test_expiry_cannot_be_extended_by_retries_or_wall_clock_rollback(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    event = trigger(clock)
    job = scheduler.enqueue(event).job_id
    scheduler.next_job()
    clock.ns += 30_000_000_000
    assert not scheduler.validate_slot(scheduler.slot_for(job)).valid
    assert scheduler.enqueue(event).job_id == job
    assert scheduler.active_jobs == 0
    with pytest.raises(ValueError, match='DEDUPLICATION_CONFLICT'):
        scheduler.enqueue(replace(event, slot=replace(event.slot, expires_at=clock.at(120))))


def test_unknown_and_changed_slots_fail_closed(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    event = trigger(clock)
    assert not scheduler.validate_slot(event.slot).valid
    scheduler.enqueue(event)
    assert not scheduler.validate_slot(replace(event.slot, expires_at=clock.at(90))).valid
    clock.health = 'UNKNOWN'
    assert not scheduler.validate_slot(event.slot).valid
    with pytest.raises(ValueError, match='CLOCK_UNHEALTHY'):
        scheduler.enqueue(trigger(clock, instrument='ETH'))


def test_epoch_staleness_and_scope_isolation(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    scheduler.enqueue(trigger(clock, epoch=4))
    with pytest.raises(ValueError, match='STALE_EPOCH'):
        scheduler.enqueue(trigger(clock, epoch=3, key='old'))
    scheduler.enqueue(trigger(clock, epoch=1, instrument='ETH'))
    assert scheduler.active_jobs == 2


def test_no_live_or_cross_environment_research(scheduler_case):
    with pytest.raises(ValueError, match='OFFLINE_ONLY'):
        DecisionScheduler(Clock(), environment=t.Environment.LIVE, maximum_runtime_ns=1)
    event = trigger(scheduler_case.clock)
    with pytest.raises(ValueError, match='ENVIRONMENT_MISMATCH'):
        scheduler_case.scheduler.enqueue(replace(event, evidence=replace(event.evidence,
                                                     environment=t.Environment.RESEARCH)))


def test_budget_deduplicates_and_dispatch_claim_is_one_shot():
    clock = Clock()
    model_budget = budget(clock)
    call = request(clock)
    with ThreadPoolExecutor(max_workers=8) as pool:
        permits = list(pool.map(model_budget.reserve, [call] * 100))
    assert len({permit.permit_id for permit in permits}) == 1
    assert model_budget.committed == money('2')
    limits = model_budget.start(permits[0])
    assert limits.maximum_tokens == 100
    assert limits.deadline_monotonic_ns == clock.ns + 30_000_000_000
    with pytest.raises(BudgetDenied, match='PERMIT_INVALID_OR_USED'):
        model_budget.start(permits[0])
    with pytest.raises(BudgetDenied, match='REQUEST_ALREADY_USED'):
        model_budget.reserve(call)


def test_concurrent_distinct_requests_cannot_overspend():
    clock = Clock()
    model_budget = budget(clock)

    def reserve(index):
        try:
            return model_budget.reserve(request(clock, str(index), cost='6'))
        except BudgetDenied:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(20)))
    assert sum(result is not None for result in results) == 1
    assert model_budget.committed == money('6')


@pytest.mark.parametrize('overrides,reason', [
    ({'maximum_calls': 1}, 'CALL_BUDGET_EXHAUSTED'),
    ({'maximum_tokens': 100}, 'TOKEN_BUDGET_EXHAUSTED'),
    ({}, 'COST_BUDGET_EXHAUSTED'),
])
def test_hard_budget_limits(overrides, reason):
    clock = Clock()
    model_budget = budget(clock, **overrides)
    model_budget.reserve(request(clock, cost='10'))
    with pytest.raises(BudgetDenied, match=reason):
        model_budget.reserve(request(clock, 'second'))
    assert model_budget.committed == money('10')


def test_timeout_does_not_refund_and_blocks_already_reserved_dispatch():
    clock = Clock()
    model_budget = budget(clock)
    model_budget.reserve(request(clock, ttl=1))
    later = model_budget.reserve(request(clock, 'later'))
    clock.advance(1)
    with pytest.raises(BudgetDenied, match='COST_UNKNOWN'):
        model_budget.start(later)
    with pytest.raises(BudgetDenied, match='COST_UNKNOWN'):
        model_budget.reserve(request(clock, 'retry'))
    assert model_budget.committed == money('4')
    free = model_budget.reserve(request(clock, 'free', cost='0'))
    assert model_budget.start(free).maximum_tokens == 100


def test_settlement_is_idempotent_and_overrun_is_not_hidden():
    clock = Clock()
    model_budget = budget(clock)
    model_budget.reserve(request(clock))
    model_budget.mark_unknown('call')
    model_budget.settle('call', money('12'))
    model_budget.settle('call', money('12'))
    assert model_budget.committed == money('12')
    with pytest.raises(BudgetDenied, match='SETTLEMENT_CONFLICT'):
        model_budget.settle('call', money('0'))
    with pytest.raises(BudgetDenied, match='COST_BUDGET_EXHAUSTED'):
        model_budget.reserve(request(clock, 'new'))


def test_invalid_costs_identity_conflicts_and_forged_permits():
    clock = Clock()
    model_budget = budget(clock)
    call = request(clock)
    permit = model_budget.reserve(call)
    with pytest.raises(BudgetDenied, match='REQUEST_ID_CONFLICT'):
        model_budget.reserve(replace(call, model='other'))
    for invalid in (money('-1'), money('1', 'EUR')):
        with pytest.raises(BudgetDenied, match='COST_INVALID'):
            model_budget.reserve(replace(call, maximum_cost=invalid))
    with pytest.raises(BudgetDenied, match='PERMIT_INVALID_OR_USED'):
        model_budget.start(replace(permit, permit_id='forged'))
    with pytest.raises(ValueError, match='EXPIRED'):
        model_budget.reserve(request(clock, 'expired', ttl=0))


def test_no_trade_reasons_are_distinct():
    assert {reason.value for reason in NoTradeReason} == {
        'MARKET', 'COST', 'BUDGET', 'DATA', 'CAPABILITY', 'FAULT'}


def test_proposal_deadline_uses_monotonic_time_and_identity(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    job = scheduler.enqueue(trigger(clock)).job_id
    scheduler.next_job()
    scheduler.complete(job)
    output = proposal(clock, ttl=2)
    assert scheduler.validate_proposal(job, output).valid
    assert not scheduler.validate_proposal(job, replace(output, valid_until=clock.at(60))).valid
    clock.ns += 2_000_000_000  # UTC is stalled: deadline must still expire.
    assert not scheduler.validate_proposal(job, output).valid


def test_completed_slot_not_restarted_and_capacity_fails_closed(scheduler_case):
    scheduler, clock = scheduler_case.scheduler, scheduler_case.clock
    job = scheduler.enqueue(trigger(clock)).job_id
    scheduler.next_job()
    scheduler.complete(job)
    assert scheduler.enqueue(trigger(clock, key='another')).job_id == job
    assert scheduler.active_jobs == 0
    limited = DecisionScheduler(clock, environment=t.Environment.PAPER,
                                maximum_runtime_ns=1_000_000_000, maximum_jobs=1)
    limited.enqueue(trigger(clock))
    with pytest.raises(ValueError, match='RESEARCH_CAPACITY_EXHAUSTED'):
        limited.enqueue(trigger(clock, instrument='ETH'))
