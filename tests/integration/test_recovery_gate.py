"""IP-13 application probes, unrun; not registered T32/T35/T36/T56 or Q evidence."""
from dataclasses import replace

import pytest

from integration.support_ip_13 import (
    STAMP, accepted_case, budget, durable_case, ip13_database, reading, recovery_case,
)
from trading_system.contracts import types as t
from trading_system.execution.commands import CommandRepository
from trading_system.recovery.cases import CaseRepository, CaseScheduler
from trading_system.recovery.gate import REQUIREMENTS, RecoveryGate
from trading_system.recovery.reconciler import Reconciler
from trading_system.storage.models import SequenceConflict


def test_exhaustion_retains_unknown(recovery_case):
    c = recovery_case
    case = c.case(attempts=999)
    c.locks = (c.lock(),)
    scheduler = CaseScheduler((case,))
    assert scheduler.due(reading(), budget()) == []
    queried = scheduler.due(reading(60), budget())
    assert len(queried) == 1 and queried[0].attempts == 1000
    assert queried[0].command_ids == case.command_ids
    assert queried[0].state == 'WAITING'
    assert scheduler.due(reading(60), budget()) == []
    assert scheduler.due(reading(119), budget()) == []
    assert scheduler.due(reading(120), budget())[0].attempts == 1001
    assert not c.reconciler.reconcile(case, c.report()).resolved
    verdict = c.gate.evaluate(replace(c.checklist, unresolved_cases=scheduler.snapshot()))
    assert not verdict.ready and verdict.risk_locks == c.locks
    assert c.locks[0].state == 'ACTIVE'


@pytest.mark.parametrize('operation', ['CANCEL', 'AMEND'])
def test_cancel_amend_independent(recovery_case, operation):
    c = recovery_case
    submit, later = c.case(), c.case(operation)
    report = c.report()
    c.attest(submit, report, commands=('submit',))
    assert c.reconciler.reconcile(submit, report).resolved
    c.attest(later, report, commands=('submit',))
    result = c.reconciler.reconcile(later, report)
    assert not result.resolved
    assert 'COMMAND_UNKNOWN:' + operation.lower() in result.missing
    assert later.case_id != submit.case_id
    c.attest(later, report, commands=(operation.lower(),))
    assert c.reconciler.reconcile(later, report).resolved


def test_empty_report_not_flat(recovery_case):
    verdict = recovery_case.evaluate_empty_positions(coverage_complete=False)
    assert verdict.flat_verified is False
    assert verdict.allow_new_risk is False


@pytest.mark.parametrize('failure', ['no_report', 'failed', 'unsupported', 'pagination',
    'cursor', 'not_found', 'no_evidence', 'no_attestation'])
def test_absence_never_proves_flat_or_cancel(recovery_case, failure):
    c = recovery_case
    case = c.positions_case()
    report = c.report()
    if failure == 'no_report':
        report = None
    elif failure == 'failed':
        report = replace(report, coverage=replace(report.coverage, query_succeeded=False))
    elif failure == 'unsupported':
        report = replace(report, coverage=replace(report.coverage, supported=False))
    elif failure == 'pagination':
        report = replace(report, coverage=replace(report.coverage, pagination_complete=False))
    elif failure == 'cursor':
        report = replace(report, next_cursor='next-page')
    elif failure == 'not_found':
        report = replace(report, coverage=replace(report.coverage, missing=('TEMPORARY_NOT_FOUND',)))
    elif failure == 'no_evidence':
        report = replace(report, evidence=())
    if report is not None and failure != 'no_attestation':
        c.attest(case, report)
    result = c.reconciler.assess(case, report)
    assert not result.result.resolved and not result.flat_verified and not result.allow_new_risk
    cancel = c.case('CANCEL')
    if report is not None:
        c.attest(cancel, report)  # Empty orders cannot supply a cancel response.
    assert not c.reconciler.reconcile(cancel, report).resolved


@pytest.mark.parametrize('missing_fact', ['AUTHENTICATED', 'SUBSCRIBED', 'SNAPSHOT_SYNCHRONIZED', 'GAP_FREE'])
def test_private_stream_not_ready(recovery_case, missing_fact):
    c = recovery_case
    facts = (REQUIREMENTS['private_stream'] - {missing_fact}) | {'PUBLIC_WS_CONNECTED'}
    c.change_record('private_stream', verified=tuple(facts))
    verdict = c.gate.assess(c.checklist)
    assert not verdict.verdict.ready and not verdict.allow_new_risk
    assert 'PRIVATE_STREAM_EVIDENCE_REQUIRED' in verdict.verdict.missing


@pytest.mark.parametrize('kind', list(REQUIREMENTS))
def test_every_checklist_evidence_is_required(recovery_case, kind):
    c = recovery_case
    assert not c.gate.evaluate(replace(c.checklist, **{kind: None})).ready


def test_fixture_is_not_a_production_default(recovery_case):
    c = recovery_case
    verdict = RecoveryGate(clock=lambda: STAMP).evaluate(c.checklist)
    assert not verdict.ready
    assert 'PROTECTION_EVIDENCE_REQUIRED' in verdict.missing
    assert 'RISK_LOCKS_UNVERIFIED' in verdict.missing
    report, case = c.report(), c.positions_case()
    c.attest(case, report)
    assert not Reconciler(clock=lambda: STAMP).reconcile(case, report).resolved


def test_complete_scoped_synthetic_evidence_is_ready(recovery_case):
    c = recovery_case
    result = c.gate.assess(c.checklist)
    assert result.verdict.ready and result.allow_new_risk and result.allow_manage_current
    assert result.performance_complete


def test_missing_history_allows_authorized_current_management(recovery_case):
    c = recovery_case
    for kind in ('funds', 'positions'):
        c.change_record(kind, economic_history_complete=False)
    result = c.gate.assess(c.checklist)
    assert result.allow_manage_current and not result.performance_complete
    assert not c.gate.assess(replace(c.checklist, authorization=None)).allow_manage_current
    assert not c.gate.assess(replace(c.checklist, protection=None)).allow_manage_current
    position = c.position()
    report, case = c.report(positions=(position,)), c.positions_case()
    c.attest(case, report, economic_history_complete=False)
    result = c.reconciler.assess(case, report)
    assert result.result.resolved and not result.performance_complete and not result.flat_verified
    assert position.realized_pnl is None  # No fabricated economic history.


@pytest.mark.parametrize('permission,can_manage', [
    (t.RiskPermission.NO_NEW_RISK, True), (t.RiskPermission.EXIT_ONLY, False),
    (t.RiskPermission.RECOVERY_REQUIRED, False),
])
def test_cause_locks_survive_recovery(recovery_case, permission, can_manage):
    c = recovery_case
    c.locks = (c.lock('DAILY_LOSS', permission),)
    result = c.gate.assess(c.checklist)
    assert result.verdict.risk_locks == c.locks
    assert not result.verdict.ready and not result.allow_new_risk
    assert result.allow_manage_current is can_manage
    assert c.locks[0].state == 'ACTIVE'


def test_multiple_locks_take_action_intersection(recovery_case):
    c = recovery_case
    c.locks = (c.lock('DAILY_LOSS', t.RiskPermission.NO_NEW_RISK), c.lock('SUBMIT_UNKNOWN'))
    result = c.gate.assess(c.checklist)
    assert not result.allow_manage_current and len(result.verdict.risk_locks) == 2


@pytest.mark.parametrize('mutation', ['scope', 'kind', 'expiry', 'reference'])
def test_gate_attestation_binding(recovery_case, mutation):
    c = recovery_case
    changes = {
        'scope': {'scope': replace(c.scope, venue_account_id='foreign')},
        'kind': {'kind': 'public_stream'}, 'expiry': {'valid_until': STAMP},
        'reference': {'ref': c.ref('unbound')},
    }
    c.change_record('private_stream', **changes[mutation])
    assert not c.gate.evaluate(c.checklist).ready


@pytest.mark.parametrize('change', [
    {'visible_at': '2026-10-22T00:00:00Z'}, {'visible_at': '2026-10-24T00:00:00Z'},
    {'visible_at': 'not-a-time'}, {'hash': 'bogus'}, {'id': ''},
    {'environment': t.Environment.LIVE},
])
def test_invalid_references_fail_closed(recovery_case, change):
    c = recovery_case
    ref = replace(c.checklist.protection, **change)
    assert not c.gate.evaluate(replace(c.checklist, protection=ref)).ready
    case = c.positions_case()
    report = c.report(evidence=(ref,))
    c.attest(case, report)
    assert not c.reconciler.reconcile(case, report).resolved


@pytest.mark.parametrize('mutation', ['scope', 'hash', 'case_id', 'case_hash'])
def test_report_attestation_binding(recovery_case, mutation):
    c = recovery_case
    case, report = c.positions_case(), c.report()
    changes = {'scope': {'scope': replace(c.scope, instrument_uid='foreign')},
               'hash': {'report_hash': '0' * 64}, 'case_id': {'case_id': 'other'},
               'case_hash': {'case_hash': '0' * 64}}
    c.attest(case, report, **changes[mutation])
    assert not c.reconciler.reconcile(case, report).resolved


@pytest.mark.parametrize('change', [
    {'command_ids': ('new-command',)}, {'position_ids': ('other-position',)},
    {'attempts': 2}, {'missing': ('NEW_OBLIGATION',)},
])
def test_report_proof_cannot_be_reused_for_changed_case(recovery_case, change):
    c = recovery_case
    case, report = c.positions_case(), c.report()
    c.attest(case, report)
    result = c.reconciler.reconcile(replace(case, **change), report)
    assert not result.resolved
    assert 'ATTESTATION_MISSING_OR_MISMATCH' in result.missing


@pytest.mark.parametrize('stamp', ['2026-10-22T00:00:00Z', '2026-10-24T00:00:00Z', 'invalid'])
def test_report_time_must_be_current(recovery_case, stamp):
    c = recovery_case
    case, report = c.positions_case(), c.report(as_of=stamp)
    c.attest(case, report)
    result = c.reconciler.assess(case, report)
    assert not result.result.resolved and not result.flat_verified


@pytest.mark.parametrize('changes', [{'commands': (None,)}, {'commands': None}, {'satisfied': None}])
def test_malformed_reader_attestation_denied(recovery_case, changes):
    c = recovery_case
    case, report = c.positions_case(), c.report()
    proof = c.attest(case, report)
    c.proofs[(case.case_id, proof.report_hash)] = replace(proof, **changes)
    assert not c.reconciler.reconcile(case, report).resolved


@pytest.mark.parametrize('provenance', ['LOCAL_POLICY', 'NONE', 'LOCAL_VALIDATION'])
def test_local_terminal_cannot_confirm_command(recovery_case, provenance):
    c = recovery_case
    case, report = c.case('CANCEL'), c.report()
    proof = c.attest(case, report, commands=('cancel',))
    resolution = replace(proof.commands[0], provenance=provenance)
    # Store the modified attestation explicitly, keeping its report binding.
    c.proofs[(case.case_id, proof.report_hash)] = replace(proof, commands=(resolution,))
    assert not c.reconciler.reconcile(case, report).resolved


@pytest.mark.parametrize('mutation', ['operation', 'request_hash', 'order', 'evidence', 'duplicate'])
def test_command_proof_requires_exact_identity(recovery_case, mutation):
    c = recovery_case
    case, report = c.case('AMEND'), c.report()
    proof = c.attest(case, report, commands=('amend',))
    resolution = proof.commands[0]
    if mutation == 'operation':
        resolution = replace(resolution, binding=replace(resolution.binding, operation='SUBMIT'))
    elif mutation == 'request_hash':
        resolution = replace(resolution, binding=replace(resolution.binding, request_hash='f' * 64))
    elif mutation == 'order':
        resolution = replace(resolution, binding=replace(resolution.binding,
            order=replace(resolution.binding.order, venue_order_id='wrong-order')))
    elif mutation == 'evidence':
        resolution = replace(resolution, evidence=(c.ref('unbound'),))
    commands = (resolution, resolution) if mutation == 'duplicate' else (resolution,)
    c.proofs[(case.case_id, proof.report_hash)] = replace(proof, commands=commands)
    assert not c.reconciler.reconcile(case, report).resolved


@pytest.mark.parametrize('product', [t.Product.SPOT, t.Product.US_EQUITY])
def test_cash_products_require_balance_allocation_evidence(recovery_case, product):
    c = recovery_case
    c.scope = replace(c.scope, product=product)
    case, report = c.positions_case(), c.report()
    c.attest(case, report, balances_and_allocations_verified=False)
    result = c.reconciler.assess(case, report)
    assert not result.result.resolved and not result.flat_verified
    c.attest(case, report)
    result = c.reconciler.assess(case, report)
    assert result.result.resolved and result.flat_verified
    assert not result.allow_new_risk


def test_nonzero_or_foreign_positions_are_not_flat(recovery_case):
    c = recovery_case
    case, report = c.positions_case(), c.report(positions=(c.position(),))
    c.attest(case, report)
    assert not c.reconciler.assess(case, report).flat_verified
    report = replace(report, positions=(replace(c.position(), scope=replace(c.scope, venue_leg='SHORT')),))
    c.attest(case, report)
    assert not c.reconciler.reconcile(case, report).resolved


@pytest.mark.parametrize('changes', [
    {'lifecycle_state': t.OrderLifecycle.ACCEPTED},
    {'pending_operation': 'CANCEL'}, {'pending_operation': 'AMEND'},
    {'command_certainty': 'UNKNOWN'}, {'outcome_provenance': 'LOCAL_POLICY'},
])
def test_zero_positions_with_unsettled_orders_are_not_flat(recovery_case, changes):
    c = recovery_case
    settled = c.order(lifecycle_state=t.OrderLifecycle.CANCELED,
                      leaves_quantity=c.position('0').actual_quantity)
    case, report = c.positions_case(), c.report(orders=(replace(settled, **changes),))
    c.attest(case, report)
    assert not c.reconciler.assess(case, report).flat_verified
    report = c.report(orders=(settled,))
    c.attest(case, report)
    assert c.reconciler.assess(case, report).flat_verified


def test_missing_obligations_block_flat_even_with_zero_positions(recovery_case):
    c = recovery_case
    case, report = c.case('CANCEL'), c.report()
    c.attest(case, report)
    result = c.reconciler.assess(case, report)
    assert not result.result.resolved and not result.flat_verified


def test_active_order_leaves_prevent_flat(recovery_case):
    c = recovery_case
    case, report = c.positions_case(), c.report(orders=(c.order(),))
    c.attest(case, report)
    assert not c.reconciler.assess(case, report).flat_verified


def test_positions_endpoint_alone_does_not_prove_flat(recovery_case):
    c = recovery_case
    case, report = c.positions_case(), c.report()
    c.attest(case, report, current_orders_verified=False)
    result = c.reconciler.assess(case, report)
    assert result.result.resolved  # The position query obligation was satisfied.
    assert not result.flat_verified and not result.allow_new_risk


def test_unresolved_or_foreign_case_blocks_gate(recovery_case):
    c = recovery_case
    case = c.case()
    assert not c.gate.evaluate(replace(c.checklist, unresolved_cases=(case,))).ready
    foreign = replace(case, scope=replace(c.scope, venue_account_id='other'), state='RESOLVED')
    assert not c.gate.evaluate(replace(c.checklist, unresolved_cases=(foreign,))).ready


def test_reader_failures_fail_closed(recovery_case):
    c = recovery_case
    def broken(*args):
        raise OSError('synthetic archive/storage failure')
    c.gate.read_locks = broken
    assert not c.gate.evaluate(c.checklist).ready
    c.gate.read_evidence = broken
    assert not c.gate.evaluate(c.checklist).ready
    c.reconciler.read_attestation = broken
    assert not c.reconciler.reconcile(c.case(), c.report()).resolved


def test_command_reader_failure_retains_unknown(recovery_case):
    c = recovery_case
    case, report = c.case(), c.report()
    c.attest(case, report, commands=('submit',))
    def broken(*args):
        raise OSError('synthetic command storage failure')
    c.reconciler.read_command = broken
    assert not c.reconciler.reconcile(case, report).resolved


@pytest.mark.parametrize('environment', [t.Environment.LIVE, t.Environment.TESTNET])
def test_no_real_environment_enabled(recovery_case, environment):
    c = recovery_case
    scope = replace(c.scope, environment=environment)
    assert not c.gate.evaluate(replace(c.checklist, scope=scope)).ready
    assert not c.reconciler.reconcile(replace(c.case(), scope=scope), c.report()).resolved


def test_shared_query_budget_and_interval(recovery_case):
    c = recovery_case
    cases = tuple(c.case(command_id=str(i)) for i in range(3))
    scheduler = CaseScheduler(cases)
    limits = budget(maximum=2)
    assert scheduler.due(reading(), limits) == []
    first = scheduler.due(reading(60), limits)
    assert len(first) == 1
    assert scheduler.due(reading(60), limits) == []
    second = scheduler.due(reading(61), limits)
    assert len(second) == 1 and second[0].case_id != first[0].case_id
    assert scheduler.due(reading(62), limits) == []


def test_retry_after_uses_monotonic_deadline(recovery_case):
    case = recovery_case.case()
    scheduler = CaseScheduler((case,))
    limits = budget(retry_after=reading(120).utc_now)
    assert scheduler.due(reading(), limits) == []
    assert scheduler.due(reading(60, wall_seconds=1000), limits) == []
    assert len(scheduler.due(reading(120, wall_seconds=1001), limits)) == 1


def test_case_retry_after_and_restart_cooldown(recovery_case):
    case = replace(recovery_case.case(), query_budget=budget(retry_after=reading(90).utc_now))
    scheduler = CaseScheduler((case,))
    assert scheduler.due(reading(), budget()) == []
    assert scheduler.due(reading(60), budget()) == []
    assert len(scheduler.due(reading(90), budget())) == 1
    restarted = CaseScheduler(scheduler.snapshot())
    assert restarted.due(reading(91), budget()) == []
    assert restarted.due(reading(150), budget()) == []
    assert len(restarted.due(reading(151), budget())) == 1


def test_backward_wall_clock_keeps_query_claim_forward(recovery_case):
    case = recovery_case.case()
    scheduler = CaseScheduler((case,))
    assert scheduler.due(reading(), budget()) == []
    claim = scheduler.due(reading(60, wall_seconds=-100), budget())[0]
    # Monotonic elapsed time permits the read, but persistence must never move
    # next_query_at behind its previous durable value.
    from trading_system.contracts.canonical import utc_timestamp
    assert utc_timestamp(claim.next_query_at) > utc_timestamp(case.next_query_at)
    assert claim.attempts == case.attempts + 1
    assert scheduler.due(reading(61, wall_seconds=-99), budget()) == []


def test_resolved_zero_budget_unhealthy_and_clock_regression(recovery_case):
    case = recovery_case.case()
    scheduler = CaseScheduler((replace(case, state='RESOLVED'),))
    assert scheduler.due(reading(), budget()) == []
    assert scheduler.due(reading(60), budget()) == []
    scheduler = CaseScheduler((case,))
    assert scheduler.due(reading(), budget(maximum=0)) == []
    assert scheduler.due(reading(60), budget(maximum=0)) == []
    assert scheduler.due(reading(61, health='UNKNOWN'), budget(maximum=0)) == []
    with pytest.raises(ValueError, match='regressed'):
        scheduler.due(reading(60), budget(maximum=0))


@pytest.mark.parametrize('invalid', [budget(maximum=-1), budget(window=0), budget(interval=-1), budget(maximum=True)])
def test_invalid_query_limits_rejected(invalid):
    with pytest.raises(ValueError):
        CaseScheduler().due(reading(), invalid)


def test_budget_change_requires_restart(recovery_case):
    scheduler = CaseScheduler((recovery_case.case(),))
    scheduler.due(reading(), budget())
    with pytest.raises(ValueError, match='rehydration'):
        scheduler.due(reading(60), budget(maximum=100))


def test_durable_exhaustion_retains_lock_reservation_and_never_resends(durable_case):
    c = durable_case
    store = CaseRepository(c.uow)
    original = store.retain_unknown(c.scope, c.command.command_id, reading())
    assert store.retain_unknown(c.scope, c.command.command_id, reading()) == original
    # IP-10's receipt timestamp is current time, so start from the stored time.
    now = t.ClockReading(utc_now=original.next_query_at, monotonic_ns=0, clock_health='HEALTHY')
    scheduler = CaseScheduler(store.load(c.scope))
    assert scheduler.due(now, original.query_budget) == []
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    later = replace(now, monotonic_ns=60 * 10**9,
                    utc_now=(utc_timestamp(now.utc_now) + timedelta(seconds=60)).isoformat())
    updated = scheduler.due(later, original.query_budget)[0]
    store.save_waiting(original, updated)
    assert CaseRepository(c.uow).load(c.scope) == (updated,)
    locks, _, reservations = c.recovery_records()
    assert c.get().state == 'UNKNOWN' and locks and reservations[0].state == t.ReservationState.HELD
    assert c.dispatch().status == t.CommandState.UNKNOWN
    assert len(c.engine.transport.writes) == 1
    assert all(lock.state == 'ACTIVE' for lock in locks)
    with pytest.raises(SequenceConflict):
        store.save_waiting(original, updated)


def test_case_persistence_cannot_resolve_or_mutate_identity(durable_case):
    c = durable_case
    store = CaseRepository(c.uow)
    case = store.retain_unknown(c.scope, c.command.command_id, reading())
    for change in ({'state': 'RESOLVED'}, {'command_ids': ('replacement',)}, {'missing': ()}):
        altered = replace(case, attempts=case.attempts + 1, state='WAITING',
                          next_query_at='2099-01-01T00:00:00Z')
        with pytest.raises(ValueError):
            store.save_waiting(case, replace(altered, **change))
    assert store.load(c.scope) == (case,)
    assert c.get().state == 'UNKNOWN'


@pytest.mark.parametrize('operation', ['CANCEL', 'AMEND'])
def test_durable_cancel_amend_unknown_after_accepted_submit(accepted_case, operation):
    c = accepted_case
    original_id = c.command.command_id
    # A distinct semantic command targets the accepted original order. The
    # trusted target/authorization facts and transport remain synthetic IP-10
    # fixtures; SQL identities and outcomes use the actual repositories.
    command = c.variant(command_id=original_id + ':' + operation,
        attempt_id=c.command.attempt_id + ':' + operation,
        venue_client_order_id=c.command.venue_client_order_id + ':' + operation,
        operation=operation, target_order_id=c.command.venue_client_order_id)
    c.append(command)
    c.command = command
    c.engine.response_lost = True
    assert c.dispatch().status == t.CommandState.UNKNOWN
    case = CaseRepository(c.uow).retain_unknown(c.scope, command.command_id, reading())
    with c.uow.transaction(c.scope) as tx:
        repo = CommandRepository(tx)
        assert repo.get(original_id).state == 'VENUE_CONFIRMED'
        assert repo.get(command.command_id).state == 'UNKNOWN'
    assert case.command_ids == (command.command_id,)
    assert c.dispatch().status == t.CommandState.UNKNOWN
    assert c.engine.methods == ['submit', operation.lower()]
    assert len(c.engine.transport.writes) == 2


def test_read_claim_commit_failure_preserves_durable_case(durable_case):
    c = durable_case
    store = CaseRepository(c.uow)
    original = store.retain_unknown(c.scope, c.command.command_id, reading())
    updated = replace(original, attempts=original.attempts + 1, state='WAITING',
                      next_query_at='2099-01-01T00:00:00Z')
    c.fail_commit = c.commits + 1
    with pytest.raises(OSError, match='commit failure'):
        store.save_waiting(original, updated)
    c.fail_commit = None
    assert store.load(c.scope) == (original,)
    assert c.get().state == 'UNKNOWN'
    assert len(c.engine.transport.writes) == 1


def test_retention_cannot_downgrade_confirmed_command(accepted_case):
    c = accepted_case
    store = CaseRepository(c.uow)
    with pytest.raises(ValueError, match='potentially sent'):
        store.retain_unknown(c.scope, c.command.command_id, reading())
    assert c.get().state == 'VENUE_CONFIRMED'
    assert store.load(c.scope) == ()
