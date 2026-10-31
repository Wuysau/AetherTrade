"""Offline original-ID and observation regressions for local children."""
from collections import defaultdict
from types import SimpleNamespace
import pytest

from test_local_child_quantity import commitment
from trading_system.contracts.canonical import canonical_json, domain_payload
from trading_system.execution.creation_requests import digest
from trading_system.execution import local_child_original as original_module
from trading_system.execution import local_child_observation as observation_module
from trading_system.execution.local_child_observation import (
    LocalChildObservationV1, read_local_child_observation, record_local_child_observation,
)
from trading_system.execution.local_child_original import read_local_child_original
from trading_system.storage.models import decode, scope_id


class Rows:
    def __init__(self, values=()):
        self.values = tuple(values)

    def fetchone(self):
        return self.values[0] if self.values else None


class Tx:
    def __init__(self, scope):
        self.active = True
        self.scope = scope
        self.transaction_id = 'tx-1'
        self.rows = defaultdict(list)

    def _append(self, table, record_id, record, expected_sequence):
        assert table == 'audit_events'
        assert len(self.rows[record_id]) == expected_sequence
        self.rows[record_id].append((expected_sequence + 1, canonical_json(domain_payload(record))))
        return expected_sequence + 1

    def execute(self, sql, args=()):
        if 'SELECT sequence FROM ledger.audit_events' in sql:
            rows = self.rows.get(args[1], [])
            return Rows(((rows[-1][0],),) if rows else ())
        if 'SELECT sequence,payload FROM ledger.audit_events' in sql:
            rows = self.rows.get(args[1], [])
            return Rows(((rows[-1][0], rows[-1][1]),) if rows else ())
        raise AssertionError('UNEXPECTED_SQL:' + sql)


def command_for(item, *, state='DURABLE_READY'):
    return SimpleNamespace(
        scope=item.compilation.parent.scope, activation_id='activation',
        activation_bundle_hash='bundle', bundle_hash='bundle', request_hash=digest(item.request),
        commitment_hash=digest(item), parent_hash=digest(item.compilation.parent),
        state=state, command_id='one')


def test_original_reader_restores_exact_identity_without_resubmit(monkeypatch):
    item = commitment('0.1')
    command = command_for(item)
    activation = SimpleNamespace(activation_id='activation', bundle_hash='bundle',
                                 authority=SimpleNamespace(scope=item.compilation.parent.scope))
    tx = Tx(item.compilation.parent.scope)
    monkeypatch.setattr(original_module, 'read_local_child_command', lambda _tx, key: command)
    monkeypatch.setattr(original_module, 'read_local_activation', lambda _tx, key: activation)
    monkeypatch.setattr(original_module, 'read_local_child_commitments',
                        lambda _tx, key: SimpleNamespace(commitments=(item,)))
    restored = read_local_child_original(tx, 'one')
    assert restored.command is command and restored.request == item.request
    assert restored.current_state == 'DURABLE_READY'
    assert restored.can_resubmit is False and restored.execution_authorized is False


@pytest.mark.parametrize('fault', ['missing', 'activation', 'wrong_commitment'])
def test_original_reader_fails_closed(monkeypatch, fault):
    item = commitment('0.1')
    command = command_for(item)
    activation = SimpleNamespace(activation_id='activation', bundle_hash='bundle',
                                 authority=SimpleNamespace(scope=item.compilation.parent.scope))
    tx = Tx(item.compilation.parent.scope)
    monkeypatch.setattr(original_module, 'read_local_child_command',
                        lambda _tx, key: None if fault == 'missing' else command)
    monkeypatch.setattr(original_module, 'read_local_activation',
                        lambda _tx, key: None if fault == 'activation' else activation)
    commitments = (item,)
    if fault == 'wrong_commitment':
        commitments = (commitment('0.2'),)
    monkeypatch.setattr(original_module, 'read_local_child_commitments',
                        lambda _tx, key: SimpleNamespace(commitments=commitments))
    if fault == 'missing':
        assert read_local_child_original(tx, 'one') is None
        return
    with pytest.raises(ValueError):
        read_local_child_original(tx, 'one')


def test_observation_is_append_only_and_never_a_fill(monkeypatch):
    item = commitment('0.1')
    command = command_for(item, state='MAY_HAVE_SENT')
    tx = Tx(item.compilation.parent.scope)
    monkeypatch.setattr(observation_module, 'read_local_child_command', lambda _tx, key: command)
    first = record_local_child_observation(
        tx, 'one', probe_id='probe-1', state='ACCEPTED',
        observed_at='2026-10-28T10:00:00Z', report_hash='r' * 64)
    second = record_local_child_observation(
        tx, 'one', probe_id='probe-2', state='UNKNOWN',
        observed_at='2026-10-28T10:00:01Z', report_hash=None)
    assert first.execution_authorized is False and first.funds_release_authorized is False
    assert second.state == 'UNKNOWN' and second.report_hash is None
    assert read_local_child_observation(tx, 'one', tx.scope) == second
    assert len(tx.rows['local-child-observation:one']) == 2


def test_draft_command_cannot_be_observed(monkeypatch):
    item = commitment('0.1')
    command = command_for(item, state='DURABLE_READY')
    tx = Tx(item.compilation.parent.scope)
    monkeypatch.setattr(observation_module, 'read_local_child_command', lambda _tx, key: command)
    with pytest.raises(ValueError, match='SENT_ORIGINAL_REQUIRED'):
        record_local_child_observation(tx, 'one', probe_id='probe', state='ACCEPTED',
                                       observed_at='2026-10-28T10:00:00Z', report_hash=None)
