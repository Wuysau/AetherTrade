"""IP-31 offline terminal control probes.

These probes exercise the production terminal client/session against the real
IP-27 control inbox and IP-28 projection/event services over a test-only
SQLite/in-process adapter. They are not PostgreSQL, HTTPS, a venue connection,
a native process check or a registered T/F/Q acceptance result.
"""
from dataclasses import replace
from decimal import Decimal
import io
import json

import pytest

from terminal.support_ip_31 import (
    GuardedEnviron,
    TerminalCase,
    terminal_case,
)
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.terminal import cli
from trading_system.terminal.client import (
    TerminalConflict,
    TerminalDenied,
    TerminalInvalid,
    TerminalOutcomeUnknown,
)
from trading_system.terminal.tui import (
    AccountExitConfirmation,
    TerminalSession,
)


# --- Revocable token, no exchange secrets ---------------------------------


def test_terminal_no_exchange_secrets(terminal_case):
    case = terminal_case
    guard = GuardedEnviron({
        'AETHERTRADE_CONTROL_TOKEN': case.api.token,
        'BINANCE_API_KEY': 'unused',
        'BINANCE_SECRET_KEY': 'unused',
    })
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        ['stop', 'pause-new', '--key', 'cli-key',
         '--expected-revision', '1', '--ownership-epoch', '1'],
        build_client=case.client_with_token, environ=guard,
        stdout=out, stderr=err)
    assert code == 0, err.getvalue()
    assert 'AETHERTRADE_CONTROL_TOKEN' in guard.reads
    assert all('BINANCE' not in key.upper() for key in guard.reads)
    assert case.api.token not in repr(case.client.token)

    allowed_headers = {'authorization', 'accept', 'content-type',
                       'idempotency-key', 'if-match'}
    for record in case.transport.requests:
        assert set(record['headers']) <= allowed_headers
        assert record['headers']['authorization'] == 'Bearer ' + case.api.token
        rendered = json.dumps(record).lower()
        assert 'binance' not in rendered
        assert 'signature' not in rendered
        assert 'secret' not in rendered

    # Exchange credential flags are refused before token resolution or network.
    before = len(case.transport.requests)
    code = cli.main(['--api-key', 'forbidden', 'status'],
                    environ=GuardedEnviron({}), stdout=io.StringIO(),
                    stderr=io.StringIO())
    assert code == cli.EXIT_USAGE
    assert len(case.transport.requests) == before


def test_control_token_is_revocable(terminal_case):
    case = terminal_case
    before = case.command_count()
    case.revoke_session()
    with pytest.raises(TerminalDenied) as caught:
        case.command('pause_new', 'revoked-key')
    assert caught.value.status == 401
    assert case.command_count() == before
    assert case.api.token not in repr(case.client.token)


def test_cli_requires_a_control_token(terminal_case):
    case = terminal_case
    guard = GuardedEnviron({})
    factory_calls = []

    def factory(token):
        factory_calls.append(token)
        return case.client_with_token(token)

    code = cli.main(['status'], build_client=factory, environ=guard,
                    stdout=io.StringIO(), stderr=io.StringIO())
    assert code == cli.EXIT_USAGE
    assert factory_calls == []
    assert guard.reads == ['AETHERTRADE_CONTROL_TOKEN']

    # stdin is the recommended path and the env value is not consulted.
    insecure = GuardedEnviron({'AETHERTRADE_CONTROL_TOKEN': 'must-not-be-used'})
    code = cli.main(['--control-token-stdin', 'status'],
                    build_client=factory,
                    environ=insecure, stdin=io.StringIO(case.api.token + '\n'),
                    stdout=io.StringIO(), stderr=io.StringIO())
    assert code == 0
    assert factory_calls == [case.api.token]
    assert insecure.reads == []


# --- Explicit stop semantics ----------------------------------------------


def test_stop_is_explicit(terminal_case):
    case = terminal_case
    assert case.session.refresh('positions') is not None

    pause = case.session.request_stop('pause-new', key='pause')
    cancel = case.session.request_stop('cancel-entries', key='cancel')
    exit_owned = case.session.request_stop('exit-owned', key='exit')

    assert [result.plan.action for result in (pause, cancel, exit_owned)] == [
        'pause_new', 'cancel_entries', 'exit_owned']
    assert pause.plan.summary != cancel.plan.summary != exit_owned.plan.summary
    receipts = [result.receipts[0] for result in (pause, cancel, exit_owned)]
    assert all(receipt.status == 'QUEUED' for receipt in receipts)
    assert all(receipt.reason == 'EXECUTION_OWNER_UNCONFIRMED'
               for receipt in receipts)
    assert len({receipt.command_id for receipt in receipts}) == 3
    assert all(result.exit_complete is False
               for result in (pause, cancel, exit_owned))
    assert all(result.flat_verified is False
               for result in (pause, cancel, exit_owned))

    # Account-level exit including EXTERNAL needs extra scope confirmation and
    # sends nothing without it.
    before = case.command_count()
    with pytest.raises(TerminalDenied) as caught:
        case.session.request_stop('exit-owned', include_external=True,
                                  key='account-exit')
    assert caught.value.code == 'EXTERNAL_EXIT_CONFIRMATION_REQUIRED'
    assert case.command_count() == before

    wrong_scope = AccountExitConfirmation(
        authorization_scope=(case.streams.other_scope,), include_external=True)
    with pytest.raises(TerminalDenied) as caught:
        case.session.request_stop('exit-owned', include_external=True,
                                  confirmation=wrong_scope, key='account-exit')
    assert caught.value.code == 'AUTHORIZATION_SCOPE_MISMATCH'
    assert case.command_count() == before

    confirmed = AccountExitConfirmation(
        authorization_scope=(case.scope,), include_external=True)
    result = case.session.request_stop(
        'exit-owned', include_external=True, confirmation=confirmed,
        key='account-exit')
    assert result.plan.action == 'exit_owned'
    assert result.plan.includes_external is True
    assert result.external_positions_not_covered == ('position-external',)
    assert 'EXTERNAL_POSITIONS_REQUIRE_SEPARATE_AUTHORIZATION' in (
        result.incomplete_reasons)
    assert 'EXECUTION_OWNER_UNCONFIRMED' in result.incomplete_reasons
    # A queued owned exit is never presented as a verified flat account.
    assert result.exit_complete is False
    assert result.flat_verified is False
    assert case.command_count() == before + 2

    # The external flag cannot silently change pause/cancel semantics.
    with pytest.raises(TerminalInvalid):
        case.session.request_stop('pause-new', include_external=True)
    with pytest.raises(TerminalInvalid):
        case.session.request_stop('cancel-entries', include_external=True)


# --- Same idempotency/CAS as the Web --------------------------------------


def test_same_command_same_id(terminal_case):
    case = terminal_case
    request = case.request('pause_new')
    receipt = case.client.command(request, 'shared-key')
    posts_before = len(case.transport.command_posts)

    again = case.client.command(request, 'shared-key')
    assert again == receipt
    # A repeat resolves the original command instead of posting a second one.
    assert len(case.transport.command_posts) == posts_before
    assert any(record['method'] == 'GET'
               and record['path'].endswith(receipt.command_id)
               for record in case.transport.requests)

    # The Web and terminal share identity/environment/operation/key.
    status, web_receipt = case.web_post(action='pause_new', key='shared-key')
    assert status == 202
    assert web_receipt['command_id'] == receipt.command_id
    assert case.command_count() == 2

    # Same key with a different body cannot overwrite the original command.
    with pytest.raises(TerminalConflict) as conflict:
        case.client.command(case.request('exit_owned'), 'shared-key')
    assert conflict.value.code == 'IDEMPOTENCY_CONFLICT'
    assert case.command_count() == 2

    # Stale CAS is rejected server-side; nothing is applied.
    with pytest.raises(TerminalConflict) as stale:
        case.client.command(case.request('pause_new', expected_revision=0),
                            'stale-key')
    assert stale.value.code == 'POLICY_CHANGED'
    assert case.command_count() == 2


def test_timeout_reuses_the_original_command(terminal_case):
    case = terminal_case
    case.timeout_next_command_after_commit()
    receipt = case.client.command(case.request('pause_new'), 'timeout-key')
    assert receipt.status == 'QUEUED'

    posts = [record for record in case.transport.command_posts
             if record['headers'].get('idempotency-key') == 'timeout-key']
    assert len(posts) == 2
    # The retry is byte-identical, so the server returns the original receipt.
    assert posts[0]['body'] == posts[1]['body']
    assert posts[0]['headers']['if-match'] == '1'
    assert case.command_count() == 2


def test_unknown_outcome_is_not_success(terminal_case):
    case = terminal_case
    case.always_timeout_commands(True)
    with pytest.raises(TerminalOutcomeUnknown) as caught:
        case.client.command(case.request('exit_owned'), 'unknown-key')
    assert caught.value.key == 'unknown-key'
    assert case.command_count() == 0
    assert [pending.key for pending in case.client.pending] == ['unknown-key']

    case.always_timeout_commands(False)
    receipt = case.client.command(case.request('exit_owned'), 'unknown-key')
    assert receipt.status == 'QUEUED'
    assert case.client.pending == ()
    assert case.command_count() == 2


# --- Disconnect is not a trading transition -------------------------------


def test_disconnect_not_shutdown(terminal_case):
    case = terminal_case
    before = case.command_count()
    requests_before = len(case.transport.requests)
    result = case.close_ui()
    assert result.account_commands == []
    assert result.owner_running is True
    assert case.command_count() == before
    assert case.transport_requests_after(requests_before) == []
    assert 'No control command was sent' in result.note


def test_owner_status_without_health_evidence_is_unknown(terminal_case):
    session = TerminalSession(terminal_case.client)
    assert session.owner_running() is None
    assert session.close().owner_running is None


def test_cli_close_does_not_exit_or_stop(terminal_case):
    case = terminal_case
    before = case.command_count()
    out = io.StringIO()
    code = cli.main(['close'], client=case.client, stdout=out)
    assert code == 0
    assert 'No control command was sent' in out.getvalue()
    assert case.command_count() == before
    assert case.transport.requests == []


# --- Server projections, cursor and gap display ---------------------------


def test_projection_is_typed_and_cursor_bound(terminal_case):
    case = terminal_case
    page = case.query('positions')
    assert isinstance(page, t.ProjectionPage)
    assert page.scope == case.scope
    assert page.freshness.age_ms == 1000
    assert page.freshness.stale is False
    assert page.next_cursor.sequence == 7
    external = [item for item in page.positions
                if item.position.owner_strategy_id is None]
    assert len(external) == 1
    assert external[0].position.position_id == 'position-external'
    assert external[0].position.actual_quantity.value == Decimal('1.5')

    orders = case.query('orders')
    assert orders.orders[0].leaves_quantity.value == Decimal('0.5')
    assert orders.orders[0].command_certainty == 'UNKNOWN'
    locks = case.query('risk-locks')
    assert locks.risk_locks[0].permission == t.RiskPermission.NO_NEW_RISK

    # There is no frozen list endpoint for these resources.
    for resource in ('control-commands', 'intents'):
        with pytest.raises(TerminalInvalid):
            case.query(resource)

    valid = case.query('positions',
                       cursor=case.projection.cursor('positions', 7))
    assert valid.next_cursor.sequence == 7
    with pytest.raises(TerminalConflict) as stream:
        case.query('positions', cursor=case.projection.cursor('orders', 7))
    assert stream.value.code == 'CURSOR_STREAM_MISMATCH'

    before = len(case.transport.requests)
    with pytest.raises(TerminalDenied):
        case.query('positions',
                   cursor=case.streams.cursor(7, scope=case.streams.other_scope))
    assert len(case.transport.requests) == before


def test_event_gap_and_disconnect_display(terminal_case):
    case = terminal_case
    events = case.session.refresh_events()
    assert [event.event.revision for event in events] == [1, 2, 3, 4]
    assert all(event.cursor.scope == case.scope for event in events)
    assert case.session.gap is False
    assert case.session.requires_snapshot is False
    assert 'EVENT_STREAM gap=false events=4' in case.session.display()

    case.expire_event_history(3)
    expired = case.session.refresh_events(case.event_cursor(0))
    assert expired == ()
    assert case.session.gap is True
    assert case.session.requires_snapshot is True
    text = case.session.display()
    assert 'EVENT_STREAM gap=true requires_snapshot=true' in text
    # No fabricated continuous stream is yielded.
    assert case.session.events == ()

    assert case.session.refresh('positions') is not None
    assert 'age_ms=1000' in case.session.display()
    case.fail_reads(True)
    assert case.session.refresh('positions') is None
    disconnected = case.session.display()
    assert 'CONNECTION disconnected' in disconnected
    assert 'age_ms=' in disconnected
    assert 'stale=true' in disconnected


def test_live_and_cross_scope_are_rejected_before_transport(terminal_case):
    case = terminal_case
    live = replace(case.scope, environment=t.Environment.LIVE)
    with pytest.raises(TerminalDenied):
        case.client.command(t.ControlCommandRequest(
            action='pause_new', scope=live, expected_revision=1,
            ownership_epoch=1, transfer=None), 'live-key')
    other = replace(case.scope, venue_account_id='another-account')
    with pytest.raises(TerminalDenied):
        case.query('positions', scope=other)
    assert case.transport.command_posts == []
    assert case.transport.requests == []


# --- CLI receipts never claim flat -----------------------------------------


def test_cli_stop_reports_queued_not_flat(terminal_case):
    case = terminal_case
    out = io.StringIO()
    code = cli.main(
        ['stop', 'exit-owned', '--key', 'cli-exit',
         '--expected-revision', '1', '--ownership-epoch', '1'],
        client=case.client, stdout=out, stderr=io.StringIO())
    assert code == 0
    text = out.getvalue()
    assert 'status=QUEUED' in text
    assert 'NOT FLAT' in text


def test_cli_external_exit_needs_confirmation(terminal_case):
    case = terminal_case
    before = case.command_count()
    err = io.StringIO()
    code = cli.main(
        ['stop', 'exit-owned', '--include-external', '--key', 'cli-account',
         '--expected-revision', '1', '--ownership-epoch', '1'],
        client=case.client, stdout=io.StringIO(), stderr=err)
    assert code == cli.EXIT_DENIED
    assert 'EXTERNAL_EXIT_CONFIRMATION_REQUIRED' in err.getvalue()
    assert case.command_count() == before

    out = io.StringIO()
    code = cli.main(
        ['stop', 'exit-owned', '--include-external', '--confirm-external',
         '--key', 'cli-account', '--expected-revision', '1',
         '--ownership-epoch', '1'],
        client=case.client, stdout=out, stderr=io.StringIO())
    assert code == 0
    text = out.getvalue()
    assert 'EXTERNAL not covered: position-external' in text
    assert 'EXTERNAL_POSITIONS_REQUIRE_SEPARATE_AUTHORIZATION' in text
    assert 'NOT FLAT' in text
