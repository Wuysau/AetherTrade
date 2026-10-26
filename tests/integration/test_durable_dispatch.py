"""Unrun IP-10 application/SQL probes; not T19-T23/native qualification."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import multiprocessing
import signal

import pytest

from integration.support_ip_10 import (
    DispatchCase, crash_before_wire_worker, dispatch_case, ip10_database,
)
from trading_system.contracts import types as t
from trading_system.execution.commands import CommandRepository, IdempotencyConflict, command_hash
from trading_system.execution.dispatcher import CommandDispatcher, DispatchDenied
from trading_system.execution.outbox import OutboxConsumer
from trading_system.storage.models import MissingEvidence, SequenceConflict, scope_id


def test_commit_before_send(dispatch_case):
    c = dispatch_case
    result = c.dispatch()
    assert result.status == t.CommandState.VENUE_CONFIRMED
    assert len(c.engine.transport.writes) == 1
    assert c.engine.observations[0][:2] == ('MAY_HAVE_SENT', 1)
    assert c.get().state == 'VENUE_CONFIRMED'
    assert c.facts_calls == 2


def test_crash_before_wire_unknown(dispatch_case, ip10_database):
    c = dispatch_case
    ctx = multiprocessing.get_context('spawn')
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=crash_before_wire_worker,
        args=(ip10_database, c.ledger.suffix, writer))
    process.start()
    writer.close()
    try:
        assert reader.poll(20), 'child did not reach committed crash checkpoint'
        assert reader.recv() == 'MAY_HAVE_SENT_COMMITTED_BEFORE_WIRE'
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        reader.close()
    assert c.get().state == 'MAY_HAVE_SENT'
    result = c.new_dispatcher().dispatch(c.command.command_id, c.lease)
    assert result.status == t.CommandState.UNKNOWN
    assert c.get().state == 'UNKNOWN'
    assert not c.engine.transport.writes
    assert c.dispatch().status == t.CommandState.UNKNOWN
    assert not c.engine.transport.writes


def test_outbox_repeat_no_second_send(dispatch_case):
    result = dispatch_case.deliver_same_command_twice(response_lost=True)
    assert result.wire_submit_count == 1
    assert result.command_state == 'UNKNOWN'


def test_same_key_different_payload_conflict(dispatch_case):
    c = dispatch_case
    changed = c.variant(canonical_payload=replace(c.command.canonical_payload, quantity='0.002'))
    with pytest.raises(IdempotencyConflict):
        c.append(changed)
    assert c.get() == c.command
    assert not c.engine.transport.writes


@pytest.mark.parametrize('change', [
    {'attempt_id': 'replacement-attempt'}, {'operation': 'CLOSE'},
    {'target_order_id': 'another-order'}, {'execution_epoch': 2},
])
def test_same_key_envelope_conflict(dispatch_case, change):
    c = dispatch_case
    with pytest.raises(IdempotencyConflict):
        c.append(c.variant(**change))
    assert c.get() == c.command


def test_identical_enqueue_is_idempotent_after_unknown(dispatch_case):
    c = dispatch_case
    c.engine.response_lost = True
    c.dispatch()
    c.append(c.command)
    assert c.get().state == 'UNKNOWN'
    assert len(c.outbox.pending()) == 1
    assert len(c.engine.transport.writes) == 1


def test_new_attempt_or_client_id_cannot_escape_unknown(dispatch_case):
    c = dispatch_case
    c.engine.response_lost = True
    c.dispatch()
    with pytest.raises(IdempotencyConflict):
        c.append(c.variant(command_id='replacement-command', attempt_id='replacement-attempt',
                           venue_client_order_id='replacement-client'))
    assert c.get().state == 'UNKNOWN'


def test_venue_client_identity_collision(dispatch_case):
    c = dispatch_case
    with pytest.raises(IdempotencyConflict):
        c.append(c.variant(command_id='another-command', logical_client_order_id='another-logical'))
    assert c.get() == c.command


def test_conflict_marks_transaction_rollback_only(dispatch_case):
    c = dispatch_case
    with pytest.raises(RuntimeError, match='rollback-only'):
        with c.ledger.uow.transaction(c.scope) as tx:
            with pytest.raises(IdempotencyConflict):
                CommandRepository(tx).append(c.variant(attempt_id='changed'))
    assert c.get() == c.command


@pytest.mark.parametrize('commit_number', [1, 2])
def test_database_commit_failure_never_calls_send(dispatch_case, commit_number):
    c = dispatch_case
    c.fail_commit = commit_number
    if commit_number == 1:
        with pytest.raises(OSError, match='commit failure'):
            c.dispatch()
        assert c.get().state == 'DURABLE_READY'
    else:
        assert c.dispatch().status == t.CommandState.UNKNOWN
        assert c.get().state == 'UNKNOWN'
    assert not c.engine.transport.writes


def test_database_unavailable_before_dispatch(dispatch_case):
    c = dispatch_case
    c.storage_down = True
    with pytest.raises(OSError, match='storage unavailable'):
        c.dispatch()
    assert not c.engine.transport.writes
    assert c.get().state == 'DURABLE_READY'


def test_post_send_database_failure_recovers_original_identity(dispatch_case):
    c = dispatch_case
    c.engine.after_send = lambda: setattr(c, 'storage_down', True)
    with pytest.raises(OSError, match='storage unavailable'):
        c.dispatch()
    assert len(c.engine.transport.writes) == 1
    assert c.get().state == 'MAY_HAVE_SENT'
    c.storage_down = False
    # Even before recovering the original, its MAY_HAVE_SENT row blocks a new ID.
    other = c.make_another_command()
    with pytest.raises(DispatchDenied, match='UNRESOLVED'):
        c.new_dispatcher().dispatch(other.command_id, c.lease)
    assert c.new_dispatcher().dispatch(c.command.command_id, c.lease).status == t.CommandState.UNKNOWN
    assert c.get().venue_client_order_id == c.command.venue_client_order_id
    assert len(c.engine.transport.writes) == 1


def test_lost_response_preserves_reservation_and_protection_obligation(dispatch_case):
    c = dispatch_case
    c.engine.response_lost = True
    assert c.dispatch().status == t.CommandState.UNKNOWN
    locks, reconciliation, reservations = c.recovery_records()
    assert len(locks) == 1 and locks[0].permission == t.RiskPermission.RECOVERY_REQUIRED
    assert reconciliation.command_ids == (c.command.command_id,)
    assert reconciliation.state == 'OPEN' and reconciliation.attempts == 0
    assert 'FILLS_AND_PROTECTION_COVERAGE' in reconciliation.missing
    assert reservations[0].state == t.ReservationState.HELD
    assert reservations[0].amount == c.ledger.reservation.amount
    c.dispatch()
    assert c.recovery_records() == (locks, reconciliation, reservations)


@pytest.mark.parametrize('operation,method', [
    ('SUBMIT', 'submit'), ('CLOSE', 'submit'), ('SET_PROTECTION', 'submit'),
    ('CANCEL', 'cancel'), ('AMEND', 'amend'),
])
def test_each_operation_uses_its_own_durable_command(ip10_database, operation, method):
    c = DispatchCase(ip10_database, operation=operation)
    c.deliver_same_command_twice(response_lost=True)
    assert c.engine.methods == [method]
    assert c.get().operation == operation and c.get().state == 'UNKNOWN'


@pytest.mark.parametrize('fault', ['revoked', 'epoch', 'owner', 'rules', 'checks', 'expired', 'clock'])
def test_authority_change_between_commit_and_send_blocks_wire(dispatch_case, fault):
    c = dispatch_case

    def change_on_recheck(call):
        if call != 2:
            return
        if fault == 'revoked':
            c.binding = replace(c.binding, authorization=replace(c.binding.authorization,
                revoked_at=c.now.isoformat()))
        elif fault == 'epoch':
            c.lease = replace(c.lease, shard=replace(c.lease.shard, execution_epoch=2))
        elif fault == 'owner':
            c.lease = replace(c.lease, instance_id='other-process')
        elif fault == 'rules':
            c.facts_transform = lambda f: replace(f, request_hash='0' * 64)
        elif fault == 'checks':
            c.facts_transform = lambda f: replace(f, checks=frozenset())
        elif fault == 'expired':
            c.facts_transform = lambda f: replace(f, deadline_monotonic_ns=0)
        else:
            c.dispatcher.clock = lambda: t.ClockReading(utc_now=c.now.isoformat(),
                monotonic_ns=1, clock_health='UNKNOWN')

    c.on_facts = change_on_recheck
    assert c.dispatch().status == t.CommandState.UNKNOWN
    assert not c.engine.transport.writes
    assert c.get().state == 'UNKNOWN'


def test_missing_owner_composition_fails_closed(dispatch_case):
    c = dispatch_case
    d = CommandDispatcher(c.uow, c.scope, c.engine)
    with pytest.raises(DispatchDenied, match='COMPOSITION_REQUIRED'):
        d.dispatch(c.command.command_id, c.lease)
    assert c.get().state == 'DURABLE_READY'
    assert not c.engine.transport.writes


@pytest.mark.parametrize('environment', [t.Environment.LIVE, t.Environment.TESTNET])
def test_nonfictional_environment_is_disabled(dispatch_case, environment):
    c = dispatch_case
    with pytest.raises(DispatchDenied):
        CommandDispatcher(c.uow, replace(c.scope, environment=environment), c.engine)
    assert not c.engine.transport.writes


@pytest.mark.parametrize('fault', ['wrong_command', 'no_evidence', 'local_not_sent', 'local_ready'])
def test_invalid_bridge_receipt_stays_unknown(dispatch_case, fault):
    c = dispatch_case
    if fault == 'wrong_command':
        c.engine.wrong_command = True
    elif fault == 'no_evidence':
        c.engine.result_ref = None
    elif fault == 'local_not_sent':
        c.engine.status = t.CommandState.NOT_SENT
    else:
        c.engine.status = t.CommandState.DURABLE_READY
    assert c.dispatch().status == t.CommandState.UNKNOWN
    assert c.dispatch().status == t.CommandState.UNKNOWN
    assert len(c.engine.transport.writes) == 1


@pytest.mark.parametrize('status', [t.CommandState.VENUE_CONFIRMED, t.CommandState.VENUE_REJECTED])
def test_terminal_outcome_and_receipt_survive_consumer_restart(dispatch_case, status):
    c = dispatch_case
    c.engine.status = status
    first = c.outbox.consume(c.message_id(), c.lease)
    second = OutboxConsumer(c.new_dispatcher(), 'new-consumer').consume(c.message_id(), c.lease)
    assert first == second and second.status == status
    assert len(c.engine.transport.writes) == 1
    assert not c.outbox.pending()


def test_ack_failure_can_redeliver_without_resending(dispatch_case):
    c = dispatch_case
    # consume: message read, MAY_HAVE_SENT, final check, outcome, then ack.
    c.fail_commit = 5
    with pytest.raises(OSError, match='commit failure'):
        c.outbox.consume(c.message_id(), c.lease)
    assert len(c.engine.transport.writes) == 1
    assert len(c.outbox.pending()) == 1
    c.fail_commit = None
    assert c.outbox.consume(c.message_id(), c.lease).status == t.CommandState.VENUE_CONFIRMED
    assert not c.outbox.pending()
    assert len(c.engine.transport.writes) == 1


def test_concurrent_consumers_at_most_one_send(dispatch_case, ip10_database):
    c = dispatch_case
    other = DispatchCase(ip10_database, suffix=c.ledger.suffix, seed=False)
    # Separate guards deliberately exercise the durable CAS, not a shared mutex.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda case: case.dispatch(), (c, other)))
    assert sum(len(case.engine.transport.writes) for case in (c, other)) <= 1
    assert all(r.status in (t.CommandState.UNKNOWN, t.CommandState.VENUE_CONFIRMED) for r in results)
    assert c.get().state in ('UNKNOWN', 'VENUE_CONFIRMED')


def test_command_id_and_message_id_cannot_cross_scope(dispatch_case, ip10_database):
    c, other = dispatch_case, DispatchCase(ip10_database)
    with pytest.raises(MissingEvidence):
        other.dispatcher.dispatch(c.command.command_id, other.lease)
    with pytest.raises(MissingEvidence):
        other.outbox.consume(c.message_id(), other.lease)
    assert not c.engine.transport.writes and not other.engine.transport.writes


def test_payload_tampering_fails_before_durable_boundary(dispatch_case):
    c = dispatch_case
    bad = replace(c.command, payload_hash='0' * 64)
    with pytest.raises(ValueError, match='hash mismatch'):
        c.append(bad)
    assert c.get() == c.command
    assert not c.engine.transport.writes


def test_permit_cannot_be_reissued_for_unknown(dispatch_case):
    c = dispatch_case
    c.engine.response_lost = True
    c.dispatch()
    with pytest.raises(SequenceConflict):
        with c.ledger.uow.transaction(c.scope) as tx:
            CommandRepository(tx).mark_may_have_sent(tx, c.command.command_id, 'DURABLE_READY')
    assert len(c.engine.transport.writes) == 1


def test_raw_command_history_remains_conservative(dispatch_case):
    c = dispatch_case
    c.engine.response_lost = True
    c.dispatch()
    with c.ledger.uow.transaction(c.scope) as tx:
        assert tx.commands.get(c.command.command_id).state == 'MAY_HAVE_SENT'
        assert CommandRepository(tx).get(c.command.command_id).state == 'UNKNOWN'
        rows = tx.execute('SELECT count(*) FROM ledger.commands WHERE scope_id=%s AND record_id=%s',
            (scope_id(c.scope), c.command.command_id)).fetchone()[0]
        assert rows == 2
    assert command_hash(c.command) == command_hash(c.get())


def test_lost_commit_acknowledgement_never_calls_send(dispatch_case):
    c = dispatch_case
    c.fail_after_commit = 1
    with pytest.raises(OSError, match='lost commit acknowledgement'):
        c.dispatch()
    assert c.get().state == 'MAY_HAVE_SENT'
    assert not c.engine.transport.writes
    c.fail_after_commit = None
    assert c.dispatch().status == t.CommandState.UNKNOWN
    assert not c.engine.transport.writes


def test_lost_outcome_commit_acknowledgement_returns_persisted_receipt(dispatch_case):
    c = dispatch_case
    c.fail_after_commit = 3
    with pytest.raises(OSError, match='lost commit acknowledgement'):
        c.dispatch()
    assert c.get().state == 'VENUE_CONFIRMED'
    c.fail_after_commit = None
    assert c.dispatch().status == t.CommandState.VENUE_CONFIRMED
    assert len(c.engine.transport.writes) == 1


@pytest.mark.parametrize('status', [t.CommandState.VENUE_CONFIRMED, t.CommandState.VENUE_REJECTED])
def test_late_recovery_preserves_terminal_outcome(dispatch_case, status):
    c = dispatch_case
    c.engine.status = status
    original = c.dispatch()
    with c.ledger.uow.transaction(c.scope) as tx:
        assert CommandRepository(tx).recover_unknown(c.command.command_id, c.now.isoformat()) == original
    locks, case, reservations = c.recovery_records()
    assert not locks and case is None
    assert reservations[0].state == t.ReservationState.HELD
    assert len(c.engine.transport.writes) == 1


def test_identical_enqueue_after_owner_change_only_returns_existing(dispatch_case):
    c = dispatch_case
    c.engine.response_lost = True
    c.dispatch()
    with c.ledger.connect_admin() as conn:
        conn.execute('UPDATE ledger.active_owners SET ownership_epoch=ownership_epoch+1 '
                     'WHERE account_id=%s', (c.scope.venue_account_id,))
    c.append(c.command)
    assert c.get().state == 'UNKNOWN'
    with pytest.raises(IdempotencyConflict):
        c.append(c.variant(attempt_id='changed-after-revocation'))
    assert len(c.engine.transport.writes) == 1


def test_outbox_batch_limit_and_consumer_identity(dispatch_case):
    c = dispatch_case
    for limit in (0, -1, 1001, True, '1'):
        with pytest.raises(ValueError, match='batch limit'):
            c.outbox.pending(limit)
    for identity in ('', ' ', None):
        with pytest.raises(ValueError, match='consumer identity'):
            OutboxConsumer(c.dispatcher, identity)
    assert len(c.outbox.pending(1)) == 1
    assert not c.engine.transport.writes
