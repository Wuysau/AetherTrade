"""Offline model-cost reservations, never trading-capital reservations.

Amounts/call/token quotas belong to this instance's explicit budget window.
No implicit daily reset, timeout refund, network call or protection callback.
The host must enforce CallLimits at the provider and reconcile billing before
reusing a window after restart. This in-memory implementation is not LIVE-ready.
"""
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from threading import RLock
from uuid import uuid4

from trading_system.contracts import types as t
from .epochs import Deadline, healthy_clock


class BudgetDenied(ValueError):
    """Stable reason code for the UI; no automatic paid retries."""


@dataclass(frozen=True)
class CallLimits:
    maximum_tokens: int
    deadline_monotonic_ns: int


@dataclass
class _Reservation:
    request: t.ModelCallRequest
    permit: t.CostPermit
    deadline: Deadline
    state: str = 'RESERVED'
    actual_cost: t.Money | None = None


class ModelBudget:
    def __init__(self, limit: t.Money, clock: Callable[[], t.ClockReading], *,
                 maximum_calls: int, maximum_tokens: int,
                 tokens_per_call: int, maximum_runtime_ns: int):
        if limit.amount < 0:
            raise ValueError('negative budget')
        if any(type(value) is not int or value <= 0 for value in
               (maximum_calls, maximum_tokens, tokens_per_call, maximum_runtime_ns)):
            raise ValueError('positive integer limits required')
        self._limit, self._clock = limit, clock
        self._calls, self._tokens = maximum_calls, maximum_tokens
        self._per_call, self._runtime = tokens_per_call, maximum_runtime_ns
        self._records: dict[str, _Reservation] = {}
        self._lock = RLock()

    def _money(self, money: t.Money) -> None:
        if money.currency != self._limit.currency or money.amount < 0:
            raise BudgetDenied('COST_INVALID')

    @property
    def committed(self) -> t.Money:
        with self._lock:
            total = t.Money(amount=Decimal(0), currency=self._limit.currency)
            for record in self._records.values():
                total = total + (record.actual_cost if record.actual_cost is not None
                                 else record.request.maximum_cost)
            return total

    def _expire(self, now: t.ClockReading) -> None:
        for record in self._records.values():
            if record.state in ('RESERVED', 'STARTED') and not record.deadline.valid(now):
                # Even an unused reservation requires explicit zero-cost evidence.
                record.state = 'UNKNOWN'

    def reserve(self, request: t.ModelCallRequest) -> t.CostPermit:
        with self._lock:
            now = self._clock()
            healthy_clock(now)
            self._expire(now)
            self._money(request.maximum_cost)
            if not all((request.request_id, request.provider, request.model, request.task)):
                raise BudgetDenied('REQUEST_INVALID')
            if any(ref.environment == t.Environment.LIVE for ref in request.snapshot_refs):
                raise BudgetDenied('OFFLINE_ONLY')
            previous = self._records.get(request.request_id)
            if previous:
                if previous.request != request:
                    raise BudgetDenied('REQUEST_ID_CONFLICT')
                if previous.state != 'RESERVED':
                    raise BudgetDenied('REQUEST_ALREADY_USED_OR_UNKNOWN')
                return previous.permit
            paid = request.maximum_cost.amount > 0
            if paid and any(record.state == 'UNKNOWN' for record in self._records.values()):
                raise BudgetDenied('COST_UNKNOWN')
            if len(self._records) >= self._calls:
                raise BudgetDenied('CALL_BUDGET_EXHAUSTED')
            if (len(self._records) + 1) * self._per_call > self._tokens:
                raise BudgetDenied('TOKEN_BUDGET_EXHAUSTED')
            if (self.committed + request.maximum_cost).amount > self._limit.amount:
                raise BudgetDenied('COST_BUDGET_EXHAUSTED')
            deadline = Deadline.bind(request.expires_at, now, self._runtime)
            permit = t.CostPermit(permit_id=uuid4().hex, request_id=request.request_id,
                                  reserved_cost=request.maximum_cost, expires_at=request.expires_at)
            self._records[request.request_id] = _Reservation(request, permit, deadline)
            return permit

    def start(self, permit: t.CostPermit) -> CallLimits:
        """One-shot dispatch claim. Replayed reserve receipts cannot buy retries."""
        with self._lock:
            now = self._clock()
            healthy_clock(now)
            self._expire(now)
            record = self._records.get(permit.request_id)
            if record is None or record.permit != permit or record.state != 'RESERVED':
                raise BudgetDenied('PERMIT_INVALID_OR_USED')
            if (permit.reserved_cost.amount > 0
                    and any(item.state == 'UNKNOWN' for item in self._records.values())):
                raise BudgetDenied('COST_UNKNOWN')
            if self.committed.amount > self._limit.amount:
                raise BudgetDenied('COST_BUDGET_EXHAUSTED')
            record.state = 'STARTED'
            return CallLimits(self._per_call, record.deadline.end_ns)

    def mark_unknown(self, request_id: str) -> None:
        """Provider timeout/cancellation is not evidence of zero charges."""
        with self._lock:
            record = self._records[request_id]
            if record.state != 'SETTLED':
                record.state = 'UNKNOWN'

    def settle(self, request_id: str, actual_cost: t.Money) -> None:
        """Trusted billing input only; record even an overrun, then block spend.

        Call/token allowances are conservatively consumed for the whole window.
        Settlement is idempotent but conflicting evidence cannot release money.
        """
        with self._lock:
            self._money(actual_cost)
            record = self._records[request_id]
            if record.actual_cost is not None and record.actual_cost != actual_cost:
                raise BudgetDenied('SETTLEMENT_CONFLICT')
            record.actual_cost = actual_cost
            record.state = 'SETTLED'
