"""Offline, single-process research scheduling, separate from proposal execution.

One instance belongs to one server-bound deployment/environment. This module
does not accept, cancel or execute proposals. Its verdict is only one input to
the downstream authorization/structure/risk gates. Restart loses research state
and invalidates all old job IDs; this is not a durable LIVE scheduler.
"""
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import IntEnum, StrEnum
from threading import RLock
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.wire import DecisionProposal
from .epochs import Deadline, Epochs, healthy_clock


class Priority(IntEnum):
    NEW_OPPORTUNITY = 10
    EXISTING_ORDER = 20
    POSITION_REVIEW = 30
    SAFETY = 40


class NoTradeReason(StrEnum):
    MARKET = 'MARKET'
    COST = 'COST'
    BUDGET = 'BUDGET'
    DATA = 'DATA'
    CAPABILITY = 'CAPABILITY'
    FAULT = 'FAULT'


@dataclass
class _Research:
    job_id: str
    slot: t.DecisionSlot
    priority: int
    deadline: Deadline
    state: str = 'QUEUED'


class DecisionScheduler:
    def __init__(self, clock: Callable[[], t.ClockReading], *,
                 environment: t.Environment, maximum_runtime_ns: int,
                 maximum_jobs: int = 10000):
        if environment not in (t.Environment.RESEARCH, t.Environment.PAPER, t.Environment.TESTNET):
            raise ValueError('OFFLINE_ONLY')
        if any(type(x) is not int or x <= 0 for x in (maximum_runtime_ns, maximum_jobs)):
            raise ValueError('positive limits required')
        self._clock, self._environment = clock, environment
        self._runtime, self._maximum_jobs = maximum_runtime_ns, maximum_jobs
        self._epochs = Epochs()
        self._jobs: dict[str, _Research] = {}
        self._slots: dict[tuple[str, str], _Research] = {}
        self._events: dict[tuple[tuple[str, str], int, str], tuple[t.DecisionTrigger, str]] = {}
        self._proposals: dict[tuple[str, str], tuple[DecisionProposal, Deadline]] = {}
        self._lock = RLock()

    def enqueue(self, event: t.DecisionTrigger) -> t.ScheduleReceipt:
        with self._lock:
            key = self._epochs.key(event.slot)
            if event.evidence.environment != self._environment:
                raise ValueError('ENVIRONMENT_MISMATCH')
            if (not event.trigger_id or not event.deduplication_key
                    or type(event.priority) not in (int, Priority)
                    or event.priority not in set(Priority)):
                raise ValueError('INVALID_TRIGGER')
            now = self._clock()
            healthy_clock(now)
            dedup = key, event.slot.decision_epoch, event.deduplication_key
            previous = self._events.get(dedup)
            if previous:
                original, job_id = previous
                # Transport IDs/evidence may differ for the same semantic event.
                if original.slot != event.slot or original.priority != event.priority:
                    raise ValueError('DEDUPLICATION_CONFLICT')
                return t.ScheduleReceipt(job_id=job_id, coalesced=True, reason='DUPLICATE')
            deadline = Deadline.bind(event.slot.expires_at, now, self._runtime)
            current = self._slots.get(key)
            if event.slot.decision_epoch < self._epochs.latest(event.slot):
                raise ValueError('STALE_EPOCH')
            if current and event.slot.decision_epoch == current.slot.decision_epoch:
                if event.priority <= current.priority:
                    # A completed/expired slot cannot be revived by a retry either.
                    return t.ScheduleReceipt(job_id=current.job_id, coalesced=True,
                                             reason='SLOT_COALESCED')
                slot = replace(event.slot, decision_epoch=current.slot.decision_epoch + 1)
            else:
                slot = event.slot
            if len(self._jobs) >= self._maximum_jobs:
                raise ValueError('RESEARCH_CAPACITY_EXHAUSTED')
            if current:
                current.state = 'SUPERSEDED'
            self._epochs.advance(slot)
            job = _Research(uuid4().hex, slot, event.priority, deadline)
            self._slots[key] = job
            self._jobs[job.job_id] = job
            self._events[dedup] = event, job.job_id
            return t.ScheduleReceipt(job_id=job.job_id, coalesced=False, reason=None)

    def slot_for(self, job_id: str) -> t.DecisionSlot:
        with self._lock:
            return self._jobs[job_id].slot

    def validate_slot(self, slot: t.DecisionSlot) -> t.SlotVerdict:
        with self._lock:
            try:
                job = self._slots.get(self._epochs.key(slot))
                if job is None or job.slot != slot:
                    return t.SlotVerdict(valid=False, reasons=('STALE_OR_UNKNOWN_SLOT',))
                if job.state not in ('QUEUED', 'RUNNING', 'COMPLETE'):
                    return t.SlotVerdict(valid=False, reasons=(job.state,))
                if not job.deadline.valid(self._clock()):
                    job.state = 'EXPIRED'
                    return t.SlotVerdict(valid=False, reasons=('EXPIRED',))
            except (ValueError, TypeError):
                return t.SlotVerdict(valid=False, reasons=('CLOCK_OR_SLOT_INVALID',))
            return t.SlotVerdict(valid=True, reasons=())

    def next_job(self) -> str | None:
        """Claim highest-priority queued research, FIFO within equal priorities."""
        with self._lock:
            candidates = [job for job in self._jobs.values()
                          if job.state == 'QUEUED' and self.validate_slot(job.slot).valid]
            if not candidates:
                return None
            job = max(candidates, key=lambda candidate: candidate.priority)
            job.state = 'RUNNING'
            return job.job_id

    @property
    def active_jobs(self) -> int:
        with self._lock:
            return sum(job.state in ('QUEUED', 'RUNNING') and self.validate_slot(job.slot).valid
                       for job in self._jobs.values())

    def complete(self, job_id: str) -> t.SlotVerdict:
        with self._lock:
            job = self._jobs[job_id]
            verdict = self.validate_slot(job.slot)
            if not verdict.valid:
                return verdict
            if job.state != 'RUNNING':
                return t.SlotVerdict(valid=False, reasons=('NOT_RUNNING',))
            job.state = 'COMPLETE'
            return verdict

    def cancel(self, job_id: str) -> None:
        """Invalidate research only; there is deliberately no execution callback."""
        with self._lock:
            self._jobs[job_id].state = 'CANCELED'

    def validate_proposal(self, job_id: str, proposal: DecisionProposal) -> t.SlotVerdict:
        """Recheck EACH proposal at handoff; does not accept or authorize it."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != 'COMPLETE':
                return t.SlotVerdict(valid=False, reasons=('RESEARCH_NOT_COMPLETE',))
            verdict = self.validate_slot(job.slot)
            if not verdict.valid:
                return verdict
            try:
                now = self._clock()
                key = job_id, proposal.proposal_id
                previous = self._proposals.get(key)
                if previous is not None:
                    original, deadline = previous
                    if proposal != original:
                        return t.SlotVerdict(valid=False, reasons=('PROPOSAL_ID_CONFLICT',))
                else:
                    deadline = Deadline.bind(proposal.valid_until, now, self._runtime)
                    self._proposals[key] = proposal, deadline
                if not deadline.valid(now):
                    return t.SlotVerdict(valid=False, reasons=('PROPOSAL_EXPIRED_OR_INVALID',))
            except (ValueError, TypeError):
                return t.SlotVerdict(valid=False, reasons=('PROPOSAL_EXPIRED_OR_INVALID',))
            return verdict
