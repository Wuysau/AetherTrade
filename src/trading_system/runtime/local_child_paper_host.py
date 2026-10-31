"""Explicit local-child PAPER host: admit -> permit -> send -> observe.

The host owns sequencing only. It composes the existing execution owner, the
owner-transaction local-child boundary and an injected venue adapter port.
It never mints a parent command, reuses a parent permit, retries an uncertain
send, releases funds or turns an observation into a fill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.intents.local_child_compilation import LocalChildCompilation
from trading_system.storage.uow import UnitOfWork

from trading_system.execution.local_child_admission import LocalChildCommandBoundary
from trading_system.execution.local_child_observation import (LocalChildObservationV1,
                                                              record_local_child_observation)
from trading_system.execution.local_child_original import read_local_child_original


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildSendReceipt:
    command_id: str
    accepted: bool
    raw: object | None = None
    unknown: bool = False

    @property
    def execution_authorized(self) -> Literal[False]:
        return False

    @property
    def funds_release_authorized(self) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildQueryReport:
    state: Literal['ACCEPTED', 'REJECTED', 'UNKNOWN', 'NOT_OBSERVED']
    report_hash: str | None
    evidence: tuple[t.EvidenceRef, ...] = ()


def need(value, code):
    if not value:
        raise ValueError(code)


class LocalChildPaperHost:
    def __init__(self, *, owner, boundary: LocalChildCommandBoundary,
                 owner_uow: UnitOfWork, submit: Callable, query: Callable):
        need(owner is not None and type(boundary) is LocalChildCommandBoundary
             and type(owner_uow) is UnitOfWork and callable(submit) and callable(query),
             'LOCAL_CHILD_HOST_COMPOSITION_REQUIRED')
        self.owner, self.boundary, self.uow = owner, boundary, owner_uow
        self.submit, self.query = submit, query
        self.scope = boundary.scope

    def _original(self, command_id):
        with self.uow.transaction(self.scope) as tx:
            original = read_local_child_original(tx, command_id)
            need(original is not None, 'LOCAL_CHILD_ORIGINAL_REQUIRED')
            return original

    def admit(self, *, lease: t.OwnerLease, activation_id: str, child: LocalChildCompilation,
              command_id: str, rules, capabilities, **context):
        need(type(lease) is t.OwnerLease and lease.shard.environment == t.Environment.PAPER,
             'LOCAL_CHILD_PAPER_LEASE_REQUIRED')
        with self.owner.session(lease.shard):
            with self.owner.guard(lease):
                return self.boundary.admit(
                    activation_id=activation_id, child=child,
                    command_id=command_id, rules=rules, capabilities=capabilities, **context)

    def send(self, command_id: str, lease: t.OwnerLease) -> LocalChildSendReceipt:
        need(type(lease) is t.OwnerLease, 'LOCAL_CHILD_CURRENT_LEASE_REQUIRED')
        with self.owner.session(lease.shard):
            with self.owner.guard(lease):
                original = self._original(command_id)
                need(original.current_state == 'DURABLE_READY',
                     'LOCAL_CHILD_QUERY_ORIGINAL_IDS_NO_RETRY')
            permit = self.boundary.mark_may_have_sent(command_id, lease)
            try:
                with self.owner.guard(lease):
                    raw = self.submit(original, permit, lease)
            except Exception:
                # The permit commitment is already durable. Preserve UNKNOWN and
                # never mint a second permit or change the original ID.
                self.boundary.record_unknown(command_id)
                raise
            return LocalChildSendReceipt(command_id=command_id, accepted=True, raw=raw)

    def observe(self, command_id: str, *, probe_id: str) -> LocalChildObservationV1:
        need(isinstance(probe_id, str) and probe_id, 'LOCAL_CHILD_PROBE_ID_REQUIRED')
        original = self._original(command_id)
        need(original.current_state in ('MAY_HAVE_SENT', 'UNKNOWN'),
             'LOCAL_CHILD_SENT_ORIGINAL_REQUIRED')
        report = self.query(original, command_id)
        need(type(report) is LocalChildQueryReport, 'LOCAL_CHILD_QUERY_REPORT_REQUIRED')
        with self.uow.transaction(self.scope) as tx:
            return record_local_child_observation(
                tx, command_id, probe_id=probe_id, state=report.state,
                observed_at=self._now(), report_hash=report.report_hash,
                evidence=report.evidence)

    def _now(self):
        reading = self.owner.clock()
        need(type(reading) is t.ClockReading and reading.clock_health == 'HEALTHY',
             'LOCAL_CHILD_CURRENT_CLOCK_REQUIRED')
        utc_timestamp(reading.utc_now)
        return reading.utc_now
