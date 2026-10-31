"""Append-only local-child observations. Reports never become fills or releases."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.order_program import identifier
from trading_system.storage.models import decode, scope_id

from .creation_requests import digest, require
from .local_child_admission import read_local_child_command


VERSION = '2026-10-31'
PREFIX = 'local-child-observation:'
STATES = ('ACCEPTED', 'REJECTED', 'UNKNOWN', 'NOT_OBSERVED')


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildObservationV1:
    contract_version: Literal['2026-10-31']
    record_kind: Literal['LOCAL_CHILD_OBSERVATION_V1']
    scope: t.ScopeKey
    command_id: str
    commitment_hash: str
    activation_id: str
    request_hash: str
    probe_id: str
    observed_at: str
    state: Literal['ACCEPTED', 'REJECTED', 'UNKNOWN', 'NOT_OBSERVED']
    report_hash: str | None
    evidence: tuple[t.EvidenceRef, ...]

    @property
    def execution_authorized(self) -> Literal[False]:
        return False

    @property
    def funds_release_authorized(self) -> Literal[False]:
        return False


def record_local_child_observation(tx, command_id: str, *, probe_id: str, state: str,
                                   observed_at: str, report_hash: str | None,
                                   evidence: tuple[t.EvidenceRef, ...] = ()):
    identifier(command_id)
    identifier(probe_id)
    require(tx.active is True, 'LOCAL_CHILD_ACTIVE_TRANSACTION_REQUIRED')
    command = read_local_child_command(tx, command_id)
    require(command is not None and command.state in ('MAY_HAVE_SENT', 'UNKNOWN'),
            'LOCAL_CHILD_SENT_ORIGINAL_REQUIRED')
    require(state in STATES and (report_hash is None or isinstance(report_hash, str) and report_hash),
            'LOCAL_CHILD_OBSERVATION_SHAPE_INVALID')
    require(isinstance(evidence, tuple)
            and all(type(item) is t.EvidenceRef for item in evidence),
            'LOCAL_CHILD_OBSERVATION_EVIDENCE_REQUIRED')
    utc_timestamp(observed_at)
    record = LocalChildObservationV1(
        contract_version=VERSION, record_kind='LOCAL_CHILD_OBSERVATION_V1',
        scope=command.scope, command_id=command_id,
        commitment_hash=command.commitment_hash, activation_id=command.activation_id,
        request_hash=command.request_hash, probe_id=probe_id, observed_at=observed_at,
        state=state, report_hash=report_hash, evidence=evidence)
    key = PREFIX + command_id
    row = tx.execute('SELECT sequence FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s '
                     'ORDER BY sequence DESC LIMIT 1', (scope_id(command.scope), key)).fetchone()
    tx._append('audit_events', key, record, row[0] if row else 0)
    return record


def read_local_child_observation(tx, command_id: str, scope: t.ScopeKey) -> LocalChildObservationV1 | None:
    identifier(command_id)
    require(tx.active is True and scope == tx.scope, 'LOCAL_CHILD_ACTIVE_TRANSACTION_REQUIRED')
    row = tx.execute('SELECT sequence,payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s '
                     'ORDER BY sequence DESC LIMIT 1',
                     (scope_id(scope), PREFIX + command_id)).fetchone()
    if row is None:
        return None
    record = decode(LocalChildObservationV1, row[1])
    require(record.command_id == command_id and record.scope == scope,
            'LOCAL_CHILD_OBSERVATION_IDENTITY_CHANGED')
    return record
