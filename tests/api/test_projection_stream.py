"""IP-28 offline functional probes. No T/F/Q acceptance case is registered.

The temporary SQLite adapters are local test fixtures, not PostgreSQL, venue or
native evidence. These tests assert the production scoped query/stream/health
logic and redaction boundaries; they do not claim trading or qualification
results.
"""
from decimal import Decimal
import json

import pytest

from api.support_ip_28 import (
    DATABASE,
    EXECUTION_OWNER,
    HEALTH_COMPONENTS,
    MARKET_DATA,
    MODEL,
    PRIVATE_STREAM,
    audit_case,
    health_case,
    projection_case,
    stream_case,
)
from trading_system.contracts import types as t
from trading_system.control.auth import ControlError
from trading_system.storage.models import SequenceConflict


def _error(call, status, code):
    with pytest.raises(ControlError) as caught:
        call()
    assert caught.value.status == status
    assert caught.value.code == code
    return caught.value


def test_scoped_stream_cursor(stream_case):
    case = stream_case
    # Cross-environment, cross-account and forged stream cursors are rejected
    # before the feed returns any record.
    _error(case.read_cross_environment_cursor, 403, 'CURSOR_SCOPE_MISMATCH')
    _error(case.read_cross_account_cursor, 403, 'CURSOR_SCOPE_MISMATCH')
    _error(case.read_tampered_stream, 409, 'CURSOR_STREAM_MISMATCH')
    assert case.feed.read_calls == 0


def test_projection_read_is_scoped_and_cursor_bound(projection_case):
    case = projection_case
    _error(case.read_cross_environment, 403, 'AUTHORIZATION_SCOPE_MISMATCH')
    assert case.store.read_calls == 0
    page = case.read()
    assert {item.position.owner_strategy_id for item in page.positions} == {'owner-a'}
    assert len(page.positions) == 1
    projection = page.positions[0]
    assert projection.position.actual_quantity.value == Decimal('1.5')
    assert projection.protection.mode == 'NATIVE'
    assert projection.protection.state == 'GAP'
    assert page.freshness.age_ms == 1000
    assert page.freshness.stale is False
    assert 'MODEL_UNAVAILABLE' in page.freshness.missing
    assert 'COST_BUDGET_EXCEEDED' in page.freshness.missing
    assert page.next_cursor.sequence == 7
    orders = case.read('orders')
    assert orders.orders[0].leaves_quantity.value == Decimal('0.5')
    assert orders.orders[0].command_certainty == 'UNKNOWN'
    # No intent row exists, so no target is fabricated for the UI.
    assert orders.intents == ()
    # A cursor is bound to one resource stream, not only to a scope.
    valid = case.read(cursor=case.cursor('positions', 7))
    assert valid.next_cursor.sequence == 7
    _error(lambda: case.read(cursor=case.cursor('orders', 7)), 409,
           'CURSOR_STREAM_MISMATCH')
    _error(lambda: case.read(cursor=case.cursor('positions', 8)), 409,
           'CURSOR_AHEAD')
    case.store.set_watermark(case.scope, as_of='2026-10-20T06:40:00Z',
        sequence=7, retained_from=5, missing=('MODEL_UNAVAILABLE',))
    _error(lambda: case.read(cursor=case.cursor('positions', 2)), 409,
           'CURSOR_EXPIRED')


def test_stream_gap_resnapshot(stream_case):
    response = stream_case.read_expired_cursor()
    assert response.gap is True
    assert response.requires_snapshot is True
    assert response.events == ()
    assert response.next_cursor.sequence == stream_case.feed.watermark(stream_case.scope)
    # The lost range was not read and cannot be presented as continuous latest.
    assert stream_case.feed.read_calls == 0


def test_event_replay_keeps_aggregate_order(stream_case):
    page = stream_case.read_next()
    assert [item.event.revision for item in page.events] == [1, 2, 3, 4]
    assert page.gap is False
    assert page.requires_snapshot is False
    assert all(item.cursor.scope == stream_case.scope for item in page.events)
    # A regressed aggregate revision is a storage/feed defect, not a value to
    # accumulate into the previous total.
    with pytest.raises(SequenceConflict):
        stream_case.read_regressed_revision()


def test_states_separate(health_case):
    case = health_case
    snapshot = case.snapshot(states={PRIVATE_STREAM: 'DEGRADED'},
        faults={PRIVATE_STREAM: ('PRIVATE_STREAM_DISCONNECTED',)})
    assert [item.process_id for item in snapshot.processes] == list(HEALTH_COMPONENTS)
    assert len({item.process_id for item in snapshot.processes}) == 5
    states = {item.process_id: item.state for item in snapshot.processes}
    assert states[MARKET_DATA] == t.ProcessState.READY
    assert states[PRIVATE_STREAM] == t.ProcessState.DEGRADED
    assert states[MODEL] == t.ProcessState.READY
    assert states[DATABASE] == t.ProcessState.READY
    assert states[EXECUTION_OWNER] == t.ProcessState.READY
    private = next(item for item in snapshot.processes
                   if item.process_id == PRIVATE_STREAM)
    assert private.faults == ('PRIVATE_STREAM_DISCONNECTED',)
    assert snapshot.risk_permission == t.RiskPermission.NO_NEW_RISK
    assert snapshot.missing == ()

    healthy = case.snapshot()
    assert healthy.risk_permission == t.RiskPermission.NORMAL
    assert healthy.missing == ()

    absent = case.snapshot(omit=(MODEL,))
    assert 'HEALTH_UNAVAILABLE:model' in absent.missing
    assert MODEL not in {item.process_id for item in absent.processes}
    assert absent.risk_permission == t.RiskPermission.RECOVERY_REQUIRED

    unsupported = case.snapshot(no_evidence=(DATABASE,))
    database = next(item for item in unsupported.processes
                    if item.process_id == DATABASE)
    assert database.state == t.ProcessState.DEGRADED
    assert 'EVIDENCE_MISSING' in database.faults
    assert 'HEALTH_EVIDENCE:database' in unsupported.missing
    assert unsupported.risk_permission == t.RiskPermission.RECOVERY_REQUIRED


def test_pii_redaction(audit_case):
    rows = audit_case.read()
    rendered = json.dumps(rows, sort_keys=True)
    for sensitive in (
        'fixture-secret-value',
        'fixture-key-value',
        'fixture-signature',
        'token-value-456',
        'alice@example.com',
        'full-identity-001',
        'use the private stop order now',
    ):
        assert sensitive not in rendered
    row = rows[0]
    assert row['details']['symbol'] == 'BTCUSDT'
    assert row['details']['api_secret'] == '[REDACTED]'
    assert row['details']['credentials'] == '[REDACTED]'
    assert row['details']['prompt']['redacted'] == 'PROMPT_HASH'
    assert len(row['details']['prompt']['sha256']) == 64
    assert row['details']['identity_id'].startswith('pseudonym:')
    assert row['actor'].startswith('pseudonym:')
    assert row['details']['nested'][0]['token'] == '[REDACTED]'


def test_audit_scope_and_limit(audit_case):
    _error(audit_case.read_cross_scope, 403, 'AUTHORIZATION_SCOPE_MISMATCH')
    _error(lambda: audit_case.read(limit=0), 400, 'INVALID_PAGE_LIMIT')
