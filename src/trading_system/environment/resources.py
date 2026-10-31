"""Resource inventory classification and thresholds.

Reports only. There is deliberately no delete/cleanup entrypoint: unknown,
legacy or over-budget resources block new admission and are reported for an
operator. Disk pressure never relaxes global-cleanup prohibitions.
"""
from __future__ import annotations

from dataclasses import dataclass

from .budget import Budget, validate_budget


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    kind: str
    resource_id: str
    instance_id: str
    owner_task: str | None
    registered: bool
    bytes: int


@dataclass(frozen=True, slots=True)
class ResourceReport:
    status: str
    action: str
    unregistered: tuple[ResourceRecord, ...]
    legacy: tuple[str, ...]
    disk_used_bytes: int
    disk_budget_bytes: int
    reasons: tuple[str, ...]


def evaluate_resources(records: tuple[ResourceRecord, ...], budget: Budget,
                       *, disk_used_bytes: int) -> ResourceReport:
    validate_budget(budget)
    if type(disk_used_bytes) is not int or disk_used_bytes < 0:
        raise ValueError('DISK_USAGE_INVALID')
    unregistered = tuple(sorted((r for r in records if not r.registered),
                                key=lambda r: r.resource_id))
    legacy = tuple(r.resource_id for r in unregistered)
    containers = tuple(r for r in records if r.kind == 'CONTAINER')
    reasons: list[str] = []
    if unregistered:
        reasons.append('UNREGISTERED_RESOURCES_PRESENT')
    if len(containers) > budget.container_limit:
        reasons.append('CONTAINER_LIMIT_EXCEEDED')
    if disk_used_bytes >= budget.disk_budget_bytes:
        reasons.append('DISK_BUDGET_EXHAUSTED')
    elif disk_used_bytes * 4 >= budget.disk_budget_bytes * 3:
        reasons.append('DISK_BUDGET_WARNING')
    if reasons and any(r in reasons for r in ('UNREGISTERED_RESOURCES_PRESENT',
                                              'CONTAINER_LIMIT_EXCEEDED',
                                              'DISK_BUDGET_EXHAUSTED')):
        status, action = 'BLOCKED', 'BLOCK_NEW_ADMISSION_AND_REPORT_OWNER'
    elif reasons:
        status, action = 'ALERT', 'STOP_NEW_HEAVY_RUNS'
    else:
        status, action = 'OK', 'CONTINUE'
    return ResourceReport(status=status, action=action, unregistered=unregistered,
                          legacy=legacy, disk_used_bytes=disk_used_bytes,
                          disk_budget_bytes=budget.disk_budget_bytes,
                          reasons=tuple(reasons))
