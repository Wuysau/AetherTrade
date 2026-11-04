"""Restart recovery for strategy memory.

Recovery rebuilds projections from append-only events, verifies state hashes,
reads execution facts from the authoritative ledger, and reports unfinished
model runs/UNKNOWN orders. It never re-calls a provider and never replays a
trade write. An uncertain execution operation stays UNKNOWN until reconciled.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from trading_system.server import model_repository
from trading_system.server.store import strategies

from .assembler import scope_for_strategy


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryReport:
    status: str
    strategies: int = 0
    repaired: tuple[str, ...] = ()
    unfinished_runs: tuple[str, ...] = ()
    unknown_facts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)


class MemoryRecoveryService:
    def __init__(self, memory, *, store=None):
        self.memory = memory
        self.store = store or memory.repository.store

    def recover(self) -> RecoveryReport:
        with self.store.transaction() as connection:
            strategy_rows = list(connection.execute(select(strategies.c.data)).scalars())
            runs = list(connection.execute(select(model_repository.runs.c.key,
                                                  model_repository.runs.c.data)).all())
        repaired: list[str] = []
        unknown: list[str] = []
        errors: list[str] = []
        processed = 0
        for strategy in strategy_rows:
            try:
                scope = scope_for_strategy(strategy)
                state = self.memory.get_state(scope)
                if state.state_revision == 0:
                    continue
                report = self.memory.repository.rebuild_projection(scope)
                if not report.verified:
                    repaired.append(scope.strategy_id)
                facts = self.memory.get_execution_facts(scope)
                unknown.extend(str(fact) for fact in facts.get("unknown", []))
                processed += 1
            except Exception:
                errors.append(str(strategy.get("id")))
        unfinished = tuple(str(row[0]) for row in runs if row[1].get("status") == "IN_FLIGHT")
        status = "PASS"
        if repaired or unknown or unfinished or errors:
            status = "DEGRADED"
        return RecoveryReport(status=status, strategies=processed, repaired=tuple(repaired),
                              unfinished_runs=unfinished, unknown_facts=tuple(dict.fromkeys(unknown)),
                              errors=tuple(errors),
                              details={"provider_calls_replayed": False, "trade_writes_replayed": False})
