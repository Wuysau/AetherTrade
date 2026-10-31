"""Finite resource budgets. Missing or unbounded values are a BLOCKED condition.

These are policy defaults for the shared DEV scheduler, not measured capacity.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class Budget:
    active_worktree_limit: int
    heavy_concurrency: int
    heavy_runtime_seconds: int
    heavy_runs_per_stage: int
    temp_database_limit: int
    container_limit: int
    disk_budget_bytes: int
    artifact_budget_bytes: int
    lease_seconds: int
    trading_mode: str = 'PAPER'


DEFAULT_BUDGET = Budget(
    active_worktree_limit=4,
    heavy_concurrency=1,
    heavy_runtime_seconds=5400,
    heavy_runs_per_stage=1,
    temp_database_limit=2,
    container_limit=2,
    disk_budget_bytes=2 * 1024 ** 3,
    artifact_budget_bytes=100 * 1024 ** 2,
    lease_seconds=900,
)


def validate_budget(budget: Budget) -> None:
    if type(budget) is not Budget:
        raise ValueError('BUDGET_REQUIRED')
    for name in ('active_worktree_limit', 'heavy_concurrency', 'heavy_runtime_seconds',
                 'heavy_runs_per_stage', 'temp_database_limit', 'container_limit',
                 'disk_budget_bytes', 'artifact_budget_bytes', 'lease_seconds'):
        value = getattr(budget, name)
        if type(value) is not int or value <= 0 or value > 2 ** 63:
            raise ValueError('BUDGET_VALUE_INVALID:' + name)
    if budget.heavy_concurrency != 1:
        raise ValueError('HEAVY_CONCURRENCY_MUST_START_AT_ONE')
    if budget.trading_mode != 'PAPER':
        raise ValueError('TRADING_MODE_MUST_BE_PAPER')
    if budget.container_limit > 2:
        # Only the two long-lived fixed environments may exist.
        raise ValueError('CONTAINER_LIMIT_EXCEEDS_FIXED_ENVIRONMENTS')
