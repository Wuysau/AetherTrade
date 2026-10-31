"""Offline host sequencing: admit/permit/send/observe without an adapter claim."""
from contextlib import nullcontext
from types import SimpleNamespace
import pytest

from trading_system.contracts import types as t
from trading_system.execution.local_child_admission import LocalChildCommandBoundary
from trading_system.runtime import local_child_paper_host as module
from trading_system.runtime.local_child_paper_host import (
    LocalChildPaperHost, LocalChildQueryReport, LocalChildSendReceipt,
)
from trading_system.storage.uow import UnitOfWork


SCOPE = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='paper-1',
                   product=t.Product.SPOT, instrument_uid='BTCUSDT', venue_leg='BOTH')
CLOCK = t.ClockReading(utc_now='2026-10-28T10:00:00Z', monotonic_ns=1, clock_health='HEALTHY')
LEASE = t.OwnerLease(
    shard=t.ExecutionShard(environment=SCOPE.environment,
                           venue_account_id=SCOPE.venue_account_id,
                           product=SCOPE.product, node_id='node-1', execution_epoch=1),
    instance_id='owner-1', expires_at='2026-10-28T12:00:00Z', deadline_monotonic_ns=10 ** 12)


class Owner:
    def session(self, shard):
        assert shard == LEASE.shard
        return nullcontext(self)

    def guard(self, lease):
        assert lease == LEASE
        return nullcontext()

    def clock(self):
        return CLOCK


def host(monkeypatch, *, state='DURABLE_READY', submit=None, query=None):
    boundary = object.__new__(LocalChildCommandBoundary)
    boundary.scope = SCOPE
    boundary.calls = []
    boundary.admit = lambda **kw: boundary.calls.append(('admit', kw)) or 'admitted'
    boundary.mark_may_have_sent = lambda cid, lease: boundary.calls.append(('mark', cid)) or 'permit'
    boundary.record_unknown = lambda cid: boundary.calls.append(('unknown', cid))
    uow = object.__new__(UnitOfWork)
    uow.transaction = lambda scope: nullcontext(SimpleNamespace())
    monkeypatch.setattr(module, 'read_local_child_original',
                        lambda tx, cid: SimpleNamespace(current_state=state, command_id=cid))
    monkeypatch.setattr(module, 'record_local_child_observation',
                        lambda tx, cid, **kw: ('recorded', cid, kw))
    return LocalChildPaperHost(
        owner=Owner(), boundary=boundary, owner_uow=uow,
        submit=submit or (lambda original, permit, lease: 'sent'),
        query=query or (lambda original, cid: LocalChildQueryReport(
            state='ACCEPTED', report_hash='r' * 64))), boundary


def test_send_marks_once_then_submits(monkeypatch):
    h, boundary = host(monkeypatch)
    receipt = h.send('child-1', LEASE)
    assert type(receipt) is LocalChildSendReceipt and receipt.accepted
    assert [c[0] for c in boundary.calls] == ['mark']


def test_send_failure_preserves_unknown_and_does_not_retry(monkeypatch):
    def broken(*args):
        raise OSError('transport lost')
    h, boundary = host(monkeypatch, submit=broken)
    with pytest.raises(OSError):
        h.send('child-1', LEASE)
    assert [c[0] for c in boundary.calls] == ['mark', 'unknown']


def test_send_of_sent_command_queries_original_ids_only(monkeypatch):
    h, boundary = host(monkeypatch, state='MAY_HAVE_SENT')
    with pytest.raises(ValueError, match='QUERY_ORIGINAL_IDS_NO_RETRY'):
        h.send('child-1', LEASE)
    assert boundary.calls == []


def test_observe_requires_sent_state_and_records_report(monkeypatch):
    h, boundary = host(monkeypatch, state='UNKNOWN')
    result = h.observe('child-1', probe_id='probe-1')
    assert result[0] == 'recorded' and result[1] == 'child-1'
    assert result[2]['state'] == 'ACCEPTED' and result[2]['probe_id'] == 'probe-1'
    assert result[2]['observed_at'] == CLOCK.utc_now


def test_draft_command_is_not_observable(monkeypatch):
    h, boundary = host(monkeypatch, state='DURABLE_READY')
    with pytest.raises(ValueError, match='SENT_ORIGINAL_REQUIRED'):
        h.observe('child-1', probe_id='probe-1')


def test_admission_uses_owner_session_and_guard(monkeypatch):
    h, boundary = host(monkeypatch)
    result = h.admit(lease=LEASE, activation_id='activation', child='child',
                     command_id='child-1', rules='rules', capabilities='caps')
    assert result == 'admitted'
    assert boundary.calls[0][0] == 'admit'
