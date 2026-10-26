"""Read-only health projection with component-separated evidence.

Market data, private stream, model, database and execution-owner health are
never collapsed into one optimistic flag. A READY process requires evidence;
missing components remain explicitly listed and cannot produce NORMAL risk
permission. This module does not raise or clear risk locks and never contacts a
venue, model provider or database.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp


MARKET_DATA = 'market-data'
PRIVATE_STREAM = 'private-stream'
MODEL = 'model'
DATABASE = 'database'
EXECUTION_OWNER = 'execution-owner'
HEALTH_COMPONENTS = (MARKET_DATA, PRIVATE_STREAM, MODEL, DATABASE, EXECUTION_OWNER)


@dataclass(frozen=True, kw_only=True)
class ComponentHealth:
    """Evidence-bearing report from a single process family.

    evidence may contain references to a probe, heartbeat or status document.
    A bare READY state is insufficient. No credential material belongs here.
    """

    component: str
    state: t.ProcessState
    observed_at: str
    faults: tuple[str, ...] = ()
    evidence: tuple[t.EvidenceRef, ...] = ()


class HealthSource(Protocol):
    def reports(self) -> tuple[ComponentHealth, ...]: ...


class HealthProjection:
    """Implementation of the HealthPublisher port.

    The result is an observation for UI/alerting. It is not a permit and does
    not release a risk lock; a UI must not display NORMAL unless every required
    component supplied current evidence.
    """

    def __init__(self, source: HealthSource):
        self.source = source

    def snapshot(self) -> t.SystemHealth:
        reports = tuple(self.source.reports())
        by_component: dict[str, ComponentHealth] = {}
        missing: list[str] = []
        for report in reports:
            if not isinstance(report, ComponentHealth):
                raise ValueError('health report must be ComponentHealth')
            if report.component not in HEALTH_COMPONENTS:
                raise ValueError('unknown health component')
            if report.component in by_component:
                raise ValueError('duplicate health component')
            by_component[report.component] = report

        processes = []
        for name in HEALTH_COMPONENTS:
            report = by_component.get(name)
            if report is None:
                missing.append('HEALTH_UNAVAILABLE:' + name)
                continue
            try:
                state = (report.state if isinstance(report.state, t.ProcessState)
                         else t.ProcessState(report.state))
            except ValueError as exc:
                raise ValueError('invalid process state') from exc
            observed_at = report.observed_at
            utc_timestamp(observed_at)
            if (not isinstance(report.faults, tuple)
                    or any(type(item) is not str or not item
                           for item in report.faults)):
                raise ValueError('invalid health faults')
            if (not isinstance(report.evidence, tuple)
                    or any(not isinstance(item, t.EvidenceRef)
                           for item in report.evidence)):
                raise ValueError('invalid health evidence')
            faults = list(report.faults)
            if state == t.ProcessState.READY and not report.evidence:
                # No evidence means not healthy, even if a process claims READY.
                state = t.ProcessState.DEGRADED
                if 'EVIDENCE_MISSING' not in faults:
                    faults.append('EVIDENCE_MISSING')
                missing.append('HEALTH_EVIDENCE:' + name)
            processes.append(t.ProcessHealth(process_id=name, state=state,
                observed_at=observed_at, faults=tuple(faults)))

        states = {process.process_id: process.state for process in processes}
        if missing:
            permission = t.RiskPermission.RECOVERY_REQUIRED
        elif (states.get(DATABASE) != t.ProcessState.READY
              or states.get(EXECUTION_OWNER) != t.ProcessState.READY):
            permission = t.RiskPermission.RECOVERY_REQUIRED
        elif any(state != t.ProcessState.READY for state in states.values()):
            permission = t.RiskPermission.NO_NEW_RISK
        elif any(process.faults for process in processes):
            permission = t.RiskPermission.NO_NEW_RISK
        else:
            permission = t.RiskPermission.NORMAL
        return t.SystemHealth(processes=tuple(processes),
                              risk_permission=permission,
                              missing=tuple(missing))
