"""Read-query scheduling only. Exhaustion never creates a send attempt.

One execution owner must hydrate cases from its durable ledger before use and
persist the returned WAITING cases before querying. This scheduler is not a
distributed rate limiter. Restart uses a full-window cooldown, so losing the
local monotonic history cannot immediately replenish a venue query budget.
"""
from dataclasses import replace
from datetime import timedelta

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.execution.commands import CommandRepository
from trading_system.storage.models import SequenceConflict, decode, scope_id


def validate_budget(budget: t.QueryBudget) -> None:
    for value in (budget.maximum_requests, budget.window_seconds, budget.minimum_interval_ms):
        if type(value) is not int or value < 0:
            raise ValueError('query budget requires nonnegative integers')
    if budget.window_seconds == 0:
        raise ValueError('query window must be positive')
    if budget.retry_after is not None:
        utc_timestamp(budget.retry_after)


def unknown_case(*, scope: t.ScopeKey, command_id: str, operation: str,
                 now: t.ClockReading, budget: t.QueryBudget,
                 attempts: int = 0) -> t.ReconciliationCase:
    """Create a separate recovery identity for each durable semantic command.

    Caller persists this alongside the original UNKNOWN command and cause lock;
    no operation here changes command state, releases reservations or locks.
    """
    if not command_id.strip() or operation not in {'SUBMIT', 'CANCEL', 'AMEND', 'CLOSE', 'SET_PROTECTION'}:
        raise ValueError('original command ID and supported operation required')
    if type(attempts) is not int or attempts < 0:
        raise ValueError('attempts must be nonnegative')
    validate_budget(budget)
    utc_timestamp(now.utc_now)
    return t.ReconciliationCase(case_id='command:' + command_id, scope=scope,
        command_ids=(command_id,), position_ids=(), missing=(operation + '_UNKNOWN',),
        query_budget=budget, attempts=attempts, next_query_at=now.utc_now, state='OPEN')


class CaseScheduler:
    def __init__(self, cases: tuple[t.ReconciliationCase, ...] = ()):
        self._cases: dict[str, t.ReconciliationCase] = {}
        self._history: list[tuple[int, str]] = []
        self._started_ns: int | None = None
        self._last_ns: int | None = None
        self._retry_deadlines: dict[str, int] = {}
        self._max_window_ns = 0
        self._limits_shape: tuple[int, int, int] | None = None
        for case in cases:
            self.add(case)

    def add(self, case: t.ReconciliationCase) -> None:
        validate_budget(case.query_budget)
        utc_timestamp(case.next_query_at)
        if not case.case_id or case.state not in {'OPEN', 'WAITING', 'RESOLVED'}:
            raise ValueError('invalid case')
        if type(case.attempts) is not int or case.attempts < 0:
            raise ValueError('invalid attempt count')
        old = self._cases.get(case.case_id)
        if old is not None and old != case:
            raise ValueError('case already registered; stale replacements are forbidden')
        self._cases[case.case_id] = case
        self._max_window_ns = max(self._max_window_ns, case.query_budget.window_seconds * 10**9)

    def snapshot(self) -> tuple[t.ReconciliationCase, ...]:
        return tuple(self._cases.values())

    def _deadline(self, stamp: str, now: t.ClockReading) -> int:
        # Once observed, a wall-clock jump cannot shorten Retry-After/not-before.
        if stamp not in self._retry_deadlines:
            delta = utc_timestamp(stamp) - utc_timestamp(now.utc_now)
            delay_ns = max(0, (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000)
            self._retry_deadlines[stamp] = now.monotonic_ns + delay_ns
        return self._retry_deadlines[stamp]

    @staticmethod
    def _available(history: list[int], now_ns: int, budget: t.QueryBudget) -> bool:
        recent = [stamp for stamp in history if now_ns - stamp < budget.window_seconds * 10**9]
        return (len(recent) < budget.maximum_requests
                and (not history or now_ns - history[-1] >= budget.minimum_interval_ms * 10**6))

    def due(self, now: t.ClockReading, limits: t.QueryBudget) -> list[t.ReconciliationCase]:
        validate_budget(limits)
        shape = (limits.maximum_requests, limits.window_seconds, limits.minimum_interval_ms)
        if self._limits_shape is not None and shape != self._limits_shape:
            raise ValueError('budget changes require rehydration and restart cooldown')
        self._limits_shape = shape
        utc_timestamp(now.utc_now)
        if type(now.monotonic_ns) is not int or now.monotonic_ns < 0:
            raise ValueError('invalid monotonic clock')
        if self._last_ns is not None and now.monotonic_ns < self._last_ns:
            raise ValueError('monotonic clock regressed; rehydrate scheduler')
        self._last_ns = now.monotonic_ns
        if now.clock_health != 'HEALTHY':
            return []
        if self._started_ns is None:
            self._started_ns = now.monotonic_ns
        self._max_window_ns = max(self._max_window_ns, limits.window_seconds * 10**9,
                                 limits.minimum_interval_ms * 10**6,
                                 *(c.query_budget.minimum_interval_ms * 10**6 for c in self._cases.values()))
        # Observe all deadlines even during cooldown, before wall time can jump.
        for stamp in (limits.retry_after, *(c.next_query_at for c in self._cases.values()),
                      *(c.query_budget.retry_after for c in self._cases.values())):
            if stamp is not None:
                self._deadline(stamp, now)
        if now.monotonic_ns - self._started_ns < self._max_window_ns:
            return []
        if limits.retry_after and now.monotonic_ns < self._deadline(limits.retry_after, now):
            return []
        selected = []
        for case in sorted(self._cases.values(), key=lambda c: (utc_timestamp(c.next_query_at), c.case_id)):
            budget = case.query_budget
            if case.state == 'RESOLVED' or now.monotonic_ns < self._deadline(case.next_query_at, now):
                continue
            if budget.retry_after and now.monotonic_ns < self._deadline(budget.retry_after, now):
                continue
            all_stamps = [stamp for stamp, _ in self._history]
            own_stamps = [stamp for stamp, key in self._history if key == case.case_id]
            if not self._available(all_stamps, now.monotonic_ns, limits):
                break
            if not self._available(own_stamps, now.monotonic_ns, budget):
                continue
            delay_ms = max(1, limits.minimum_interval_ms, budget.minimum_interval_ms)
            # Keep durable not-before values forward-only even if UTC moves
            # backwards while the monotonic query budget continues to advance.
            next_base = max(utc_timestamp(now.utc_now), utc_timestamp(case.next_query_at))
            updated = replace(case, state='WAITING', attempts=case.attempts + 1,
                next_query_at=(next_base + timedelta(milliseconds=delay_ms)).isoformat())
            self._cases[case.case_id] = updated
            self._deadline(updated.next_query_at, now)
            self._history.append((now.monotonic_ns, case.case_id))
            selected.append(updated)
        # Keep the newest timestamp for interval checks even after window expiry.
        newest = {key: stamp for stamp, key in self._history}
        self._history = [(stamp, key) for stamp, key in self._history
                         if now.monotonic_ns - stamp < self._max_window_ns or stamp == newest[key]]
        return selected


class CaseRepository:
    """IP-03/IP-10 durable recovery integration; no resolve or send shortcut.

    Successful reconciliation is diagnostic until a separately reviewed command
    outcome / cause-lock resolution transaction consumes its verified evidence.
    """
    def __init__(self, uow):
        self.uow = uow

    def retain_unknown(self, scope: t.ScopeKey, command_id: str,
                       now: t.ClockReading) -> t.ReconciliationCase:
        utc_timestamp(now.utc_now)
        with self.uow.transaction(scope) as tx:
            commands = CommandRepository(tx)
            command = commands.get(command_id)
            if command is None or command.state not in {'MAY_HAVE_SENT', 'UNKNOWN'}:
                raise ValueError('original potentially sent command required')
            # Original UNKNOWN, its cause lock and the case commit together.
            commands.recover_unknown(command_id, now.utc_now)
            case = tx._latest('reconciliation_cases', 'ip10:reconcile:' + command_id, t.ReconciliationCase)
            if (case is None or case.state not in {'OPEN', 'WAITING'}
                    or case.scope != scope or case.command_ids != (command_id,)
                    or case.case_id != 'ip10:reconcile:' + command_id):
                raise ValueError('unresolved durable case required')
            lock = tx._latest('risk_locks', 'ip10:unknown:' + command_id, t.RiskLock)
            if (lock is None or lock.scope != scope or lock.state != 'ACTIVE'
                    or lock.permission != t.RiskPermission.RECOVERY_REQUIRED):
                raise ValueError('original UNKNOWN cause lock must remain active')
        return case

    def load(self, scope: t.ScopeKey) -> tuple[t.ReconciliationCase, ...]:
        with self.uow.transaction(scope) as tx:
            rows = tx.execute('SELECT DISTINCT ON (record_id) payload FROM ledger.reconciliation_cases '
                'WHERE scope_id=%s ORDER BY record_id,sequence DESC', (scope_id(scope),)).fetchall()
            cases = tuple(decode(t.ReconciliationCase, row[0]) for row in rows)
            if any(case.scope != scope for case in cases):
                raise ValueError('case scope mismatch')
        return tuple(case for case in cases if case.state != 'RESOLVED')

    def save_waiting(self, previous: t.ReconciliationCase, updated: t.ReconciliationCase) -> None:
        """Commit a claimed read attempt before query; crash wastes quota safely."""
        if (previous.state == 'RESOLVED' or updated.state != 'WAITING'
                or updated.attempts != previous.attempts + 1
                or utc_timestamp(updated.next_query_at) <= utc_timestamp(previous.next_query_at)
                or replace(updated, attempts=previous.attempts, next_query_at=previous.next_query_at,
                           state=previous.state) != previous):
            raise ValueError('only a forward read-query claim is permitted')
        with self.uow.transaction(previous.scope) as tx:
            current = tx._latest('reconciliation_cases', previous.case_id, t.ReconciliationCase)
            if current != previous:
                raise SequenceConflict('stale recovery case')
            sequence = tx.execute('SELECT max(sequence) FROM ledger.reconciliation_cases '
                'WHERE scope_id=%s AND record_id=%s', (scope_id(previous.scope), previous.case_id)).fetchone()[0]
            tx._append('reconciliation_cases', previous.case_id, updated, sequence)
