"""IP-16 offline probes. Authored in batch-implement; execution is deferred.

No test here registers T04/T47-T50 or changes qualification. Synthetic archive
readers verify logic only. The opt-in PostgreSQL probes exercise real durable
storage/process loss but still do not prove platform protection continuity.
"""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from types import SimpleNamespace

import pytest

from integration.support_ip_16 import (
    InjectedCrash, MemoryTx, OwnershipCase, attest_cas, connect_role, connect_untrusted_owner,
    crash_worker, ip16_database, ownership_case, postgres_case,
)
from trading_system.contracts import types as t
from trading_system.ownership.manual import ManualInterventionService
from trading_system.ownership.registry import OwnerCommitUnavailable, RegistryTx
from trading_system.ownership.transfers import OwnershipService, transfer_key


@pytest.mark.parametrize('origin', ['delayed_AI', 'queued_command'])
def test_stale_owner_epoch(ownership_case, origin):
    """Both sources must consult the same local gate; no real dispatcher implied."""
    c = ownership_case
    receipt = c.transfer_then_submit_old_epoch()
    assert receipt.reason == 'STALE_OWNER_EPOCH'
    assert receipt.wire_requests == []
    assert c.registry.read(c.scope).position.owner_strategy_id == 'new'
    assert c.registry.read(c.scope).protection_responsibility == 'SYSTEM_PROTECTION'
    assert c.probe(owner='new', epoch=2, action='PROTECT').allowed
    assert not c.probe(owner='old', epoch=2).allowed


def test_retain_owner(ownership_case):
    c = ownership_case
    c.start('RETAIN_OWNER')
    projection = c.advance_to(t.TransferState.ACTIVE)
    owner = c.registry.read(c.scope)
    assert projection.ownership_epoch == 1
    assert owner.position.owner_strategy_id == 'old'
    assert owner.position.owner_book_id == 'portfolio-book'
    assert owner.position.active_exit_plan_id == 'preserved-exit-plan'
    assert owner.mode == 'DRAINING'
    assert c.probe(action='EXIT').allowed
    assert c.probe(action='NEW_RISK').reason == 'OWNER_DRAINING'
    assert c.probe(owner='new', action='EXIT').reason == 'OWNERSHIP_CONFLICT'
    assert c.start('RETAIN_OWNER').reason == 'RETAIN_OWNER_DRAINING'


@pytest.mark.parametrize('phase,new_epoch', [
    ('before_epoch', 1), ('after_epoch_before_commit', 1), ('after_commit', 2),
])
def test_transfer_crash_atomic(ownership_case, phase, new_epoch):
    """Deterministic transaction-fault model, separate from the native probe below."""
    c = ownership_case
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    evidence = c.evidence()
    c.registry.fail_at = phase
    with pytest.raises(InjectedCrash):
        c.service.advance(c.transfer_id, evidence)
    restarted = OwnershipService(c.registry, authorize=c.service.authorize,
                                 read_facts=c.facts, clock=c.service.clock)
    owner = c.registry.read(c.scope)
    projection = restarted.projection(c.transfer_id)
    assert owner.position.ownership_epoch == projection.ownership_epoch == new_epoch
    assert owner.position.owner_strategy_id == ('old' if new_epoch == 1 else 'new')
    assert owner.protection_responsibility == 'SYSTEM_PROTECTION'
    assert owner.position.active_exit_plan_id == 'preserved-exit-plan'
    assert not c.probe().allowed
    assert not c.probe(owner='new', epoch=2).allowed  # cause lock requires explicit recovery
    assert restarted.advance(c.transfer_id, c.evidence()).ownership_epoch == 2
    assert restarted.advance(c.transfer_id, c.evidence()).ownership_epoch == 2


def test_exit_then_switch_closed_market(ownership_case):
    c = ownership_case
    c.start('EXIT_THEN_SWITCH')
    c.advance_to(t.TransferState.RECONCILE_PENDING)
    for _ in range(3):
        result = c.service.advance(c.transfer_id, c.evidence(exit_state='EXIT_PENDING_MARKET'))
        assert result.state == t.TransferState.RECONCILE_PENDING
        assert result.blocked_reasons == ('EXIT_NOT_FLAT_VERIFIED',)
        assert result.ownership_epoch == 1
    assert c.registry.read(c.scope).mode == 'EXITING'
    assert c.probe(action='EXIT').allowed
    assert not c.probe().allowed
    assert c.probe(owner='new').reason == 'OWNERSHIP_CONFLICT'
    assert c.start('EXIT_THEN_SWITCH').state == t.TransferState.RECONCILE_PENDING
    result = c.advance_to(t.TransferState.ACTIVE, flat=True)
    assert result.ownership_epoch == 2
    assert c.registry.read(c.scope).position.actual_quantity.value == 0


def test_manual_reduction_not_refilled(ownership_case):
    c = ownership_case
    case = c.manual.observe(c.event())
    assert case.decision == 'PENDING'
    assert case.lock.state == 'ACTIVE'
    assert case.lock.permission == t.RiskPermission.NO_NEW_RISK
    assert 'TARGET_REBASED' in case.lock.required_evidence
    assert c.probe().reason == 'MANUAL_INTERVENTION'
    assert c.probe().wire_requests == []
    risk = c.manual.risk_observation(c.scope)
    assert risk.total_observed_quantity.value == 4
    assert risk.strategy_id is None and risk.classification == 'EXTERNAL'
    assert risk.reconciliation_required
    # No automatic mutation back to a stale target, no strategy auto-takeover.
    assert c.registry.read(c.scope).position == c.position
    assert c.start().reason == 'STALE_CONTROL_REVISION'
    current = replace(c.context(), expected_sequence=c.registry.read(c.scope).sequence)
    assert c.service.request_transfer(c.request(), current).reason == 'RECONCILIATION_REQUIRED'
    assert c.probe().wire_requests == []
    restarted = ManualInterventionService(c.registry, clock=c.manual.clock)
    assert restarted.observe(c.event()) == case
    assert c.probe().reason == 'MANUAL_INTERVENTION'


@pytest.mark.parametrize('action', ['NEW_RISK', 'DECISION', 'EXIT'])
def test_transfer_freezes_ordinary_writes_immediately(ownership_case, action):
    c = ownership_case
    assert c.start().state == t.TransferState.REQUESTED
    assert c.probe(action=action).reason == 'TRANSFER_FROZEN'
    assert c.probe(action='PROTECT').allowed
    assert c.probe(owner='new', action='PROTECT').reason == 'OWNERSHIP_CONFLICT'
    with c.registry.transaction(c.scope) as tx:
        assert tx.locks()[0].cause == 'OWNERSHIP_TRANSFER'


@pytest.mark.parametrize('contributor', ['sub-strategy-a', 'sub-strategy-b'])
def test_portfolio_contribution_is_not_ownership(ownership_case, contributor):
    c = ownership_case
    assert c.probe(owner=contributor, action='EXIT').reason == 'OWNERSHIP_CONFLICT'
    assert c.probe(book='different-book').reason == 'OWNERSHIP_CONFLICT'
    with pytest.raises(ValueError, match='OWNERSHIP_CONFLICT'):
        c.registry.register(replace(c.position, owner_book_id='another-book'), 1)


@pytest.mark.parametrize('field,value', [
    ('venue_account_id', 'another-account'), ('instrument_uid', 'another-instrument'),
    ('venue_leg', 'SHORT'), ('product', t.Product.SPOT), ('environment', t.Environment.RESEARCH),
])
def test_unique_scope_includes_all_five_components(ownership_case, field, value):
    c = ownership_case
    other = replace(c.scope, **{field: value})
    assert c.registry.check(other, 'portfolio-book', 'old', 1, 'NEW_RISK').reason == 'EXTERNAL_NOT_OWNED'
    assert c.probe().allowed


def test_request_retry_and_body_conflict(ownership_case):
    c = ownership_case
    first = c.start()
    assert c.start() == first
    assert c.registry.read(c.scope).sequence == 2
    changed = replace(c.request(), new_owner='different-new-owner')
    assert c.service.request_transfer(changed, c.context()).reason == 'IDEMPOTENCY_CONFLICT'
    assert c.service.request_transfer(c.request(), c.context('other-key')).reason == 'TRANSFER_ID_CONFLICT'
    second = replace(c.request(), transfer_id='second-transfer')
    assert c.service.request_transfer(second, c.context('other-key')).reason == 'STALE_CONTROL_REVISION'


def test_concurrent_requests_have_one_durable_transfer(ownership_case):
    c = ownership_case
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: c.start(), range(2)))
    assert receipts[0] == receipts[1]
    assert c.registry.read(c.scope).sequence == 2
    assert len(c.service.projection(c.transfer_id).durable_steps) == 1


@pytest.mark.parametrize('change,reason', [
    ({'ownership_epoch': 2}, 'INVALID_CONTROL_BINDING'),
    ({'ownership_epoch': True}, 'INVALID_CONTROL_BINDING'),
    ({'scopes': ()}, 'INVALID_CONTROL_BINDING'),
    ({'authorization_id': ''}, 'INVALID_CONTROL_BINDING'),
    ({'expected_sequence': 0}, 'STALE_CONTROL_REVISION'),
    ({'policy_revision': 2}, 'STALE_CONTROL_REVISION'),
])
def test_control_binding_rejected_without_mutation(ownership_case, change, reason):
    c = ownership_case
    result = c.service.request_transfer(c.request(), replace(c.context(), **change))
    assert result.reason == reason
    assert c.registry.read(c.scope).sequence == 1
    assert c.probe().allowed


def test_readers_default_deny_and_revocation_blocks_progress(ownership_case):
    c = ownership_case
    bare = OwnershipService(c.registry)
    assert bare.request_transfer(c.request(), c.context()).reason == 'TRANSFER_NOT_AUTHORIZED'
    c.start()
    c.allowed = False
    result = c.service.advance(c.transfer_id, c.evidence())
    assert result.state == t.TransferState.TRANSFER_BLOCKED
    assert c.registry.read(c.scope).position.ownership_epoch == 1
    c.allowed = True
    assert c.service.advance(c.transfer_id, c.evidence()) == result
    assert c.probe(action='PROTECT').allowed


def test_active_owner_row_drift_blocks_transfer(ownership_case):
    c = ownership_case
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    c.registry.owners[c.scope] = ('unexpected-owner', 3, 1)
    result = c.service.advance(c.transfer_id, c.evidence())
    assert result.state == t.TransferState.TRANSFER_BLOCKED
    assert 'OWNER_LEDGER_MISMATCH' in result.blocked_reasons
    assert result.ownership_epoch == 1
    assert c.probe(action='PROTECT').reason == 'OWNER_LEDGER_MISMATCH'


def test_storage_failure_never_reports_epoch_commit(ownership_case):
    c = ownership_case
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    evidence = c.evidence()
    original = c.service._persist

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise InjectedCrash('storage failed before transaction commit')

    c.service._persist = interrupted
    with pytest.raises(InjectedCrash):
        c.service.advance(c.transfer_id, evidence)
    assert c.registry.read(c.scope).position.ownership_epoch == 1
    assert c.service.projection(c.transfer_id).state == t.TransferState.COMMIT_OWNER_EPOCH
    assert c.probe().reason == 'TRANSFER_FROZEN'
    assert c.probe(action='PROTECT').allowed


def test_false_flat_receipt_does_not_switch(ownership_case):
    c = ownership_case
    c.start('EXIT_THEN_SWITCH')
    c.advance_to(t.TransferState.RECONCILE_PENDING)
    c.fact_changes['position'] = c.position  # actual quantity remains ten
    result = c.service.advance(c.transfer_id, c.evidence(flat=True))
    assert result.state == t.TransferState.RECONCILE_PENDING
    assert result.ownership_epoch == 1
    assert result.blocked_reasons == ('EXIT_NOT_FLAT_VERIFIED',)


@pytest.mark.parametrize('phase', [
    t.TransferState.REQUESTED, t.TransferState.FREEZE_NEW_RISK,
    t.TransferState.RECONCILE_PENDING, t.TransferState.VERIFY_PROTECTION,
    t.TransferState.COMMIT_OWNER_EPOCH,
])
def test_missing_bound_evidence_blocks_each_step(ownership_case, phase):
    c = ownership_case
    c.start()
    c.advance_to(phase)
    c.service.read_facts = None
    projection = c.service.advance(c.transfer_id, c.evidence())
    assert projection.state == t.TransferState.TRANSFER_BLOCKED
    assert projection.ownership_epoch == 1
    assert c.probe().reason == 'TRANSFER_FROZEN'
    assert c.probe(action='PROTECT').allowed


@pytest.mark.parametrize('field,value', [
    ('authorization_current', False), ('pending_commands_resolved', False), ('orders_verified', False),
    ('positions_verified', False), ('balances_and_allocations_verified', False),
    ('protection_verified', False), ('exit_plan_accepted', False), ('accepted_exit_plan_id', None),
    ('evidence_hash', '0' * 64), ('context_hash', '0' * 64), ('transfer_sequence', 999),
    ('owner_sequence', 999), ('valid_until', '2026-10-22T00:00:00Z'),
])
def test_epoch_commit_rechecks_all_current_facts(ownership_case, field, value):
    c = ownership_case
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    c.fact_changes[field] = value
    projection = c.service.advance(c.transfer_id, c.evidence())
    assert projection.state == t.TransferState.TRANSFER_BLOCKED
    assert projection.ownership_epoch == 1


@pytest.mark.parametrize('fault', ['wrong-transfer', 'history', 'unresolved', 'wrong-case',
                                  'protection-gap', 'undercovered', 'wrong-unit', 'stale-ref'])
def test_evidence_negatives(ownership_case, fault):
    c = ownership_case
    c.start()
    c.advance_to(t.TransferState.VERIFY_PROTECTION)
    e = c.evidence()
    if fault == 'wrong-transfer':
        e = replace(e, transfer_id='unrelated')
    elif fault == 'history':
        e = replace(e, durable_step_refs=())
    elif fault == 'unresolved':
        e = replace(e, reconciliation=replace(e.reconciliation, resolved=False))
    elif fault == 'wrong-case':
        e = replace(e, reconciliation=replace(e.reconciliation, case_id='another-case'))
    elif fault == 'protection-gap':
        e = replace(e, protection=replace(e.protection, state=t.ProtectionState.GAP))
    elif fault == 'undercovered':
        e = replace(e, protection=replace(e.protection, covered_quantity=c.quantity('9')))
    elif fault == 'wrong-unit':
        e = replace(e, protection=replace(e.protection, covered_quantity=replace(c.quantity('10'), unit='USD')))
    else:
        e = replace(e, reconciliation=replace(e.reconciliation,
            evidence=(replace(c.ref(), visible_at='2026-10-22T00:00:00Z'),)))
    assert c.service.advance(c.transfer_id, e).state == t.TransferState.TRANSFER_BLOCKED


def test_durable_steps_and_epoch_commit_are_coherent(ownership_case):
    c = ownership_case
    c.start()
    p = c.advance_to(t.TransferState.ACTIVE)
    rows = c.registry.rows[(c.scope, 'ownership_transfers', transfer_key(c.transfer_id))]
    assert [r.projection.state for r in rows] == [t.TransferState.REQUESTED,
        t.TransferState.FREEZE_NEW_RISK, t.TransferState.RECONCILE_PENDING,
        t.TransferState.VERIFY_PROTECTION, t.TransferState.COMMIT_OWNER_EPOCH, t.TransferState.ACTIVE]
    assert len(p.durable_steps) == 6
    assert len({r.id for r in p.durable_steps}) == 6
    assert c.registry.read(c.scope).position.ownership_epoch == 2
    # Completing a handoff does not clear cause locks or claim overall READY.
    assert c.probe(owner='new', epoch=2).reason == 'ACTIVE_RISK_LOCKS'


def test_manual_change_interrupts_transfer_without_losing_protection(ownership_case):
    c = ownership_case
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    c.manual.observe(c.event())
    p = c.service.advance(c.transfer_id, c.evidence())
    assert p.state == t.TransferState.TRANSFER_BLOCKED
    assert 'MANUAL_INTERVENTION' in p.blocked_reasons
    assert p.ownership_epoch == 1
    assert c.probe(action='PROTECT').allowed


@pytest.mark.parametrize('kind', ['ORDER', 'MODE', 'QUANTITY'])
def test_external_changes_lock_and_schedule_reconciliation(ownership_case, kind):
    c = ownership_case
    event = c.event(kind=kind, quantity='12' if kind == 'QUANTITY' else None)
    result = c.manual.observe(event)
    assert result.decision == 'PENDING'
    assert c.probe().reason == 'MANUAL_INTERVENTION'
    risk = c.manual.risk_observation(c.scope)
    assert risk.strategy_id is None
    if kind != 'QUANTITY':
        assert risk.total_observed_quantity is None  # unknown, never zero
    with c.registry.transaction(c.scope) as tx:
        case = tx.get('reconciliation_cases', result.case_id + ':reconcile', t.ReconciliationCase)
        assert case.state == 'OPEN'
        assert 'PROTECTION' in case.missing


def test_external_unowned_position_is_never_auto_claimed(ownership_case):
    c = ownership_case
    other = replace(c.scope, venue_leg='SHORT')
    event = replace(c.event(quantity='-3'), scope=other)
    result = c.manual.observe(event)
    assert result.decision == 'PENDING'
    assert c.registry.read(other) is None
    assert c.manual.risk_observation(other).total_observed_quantity.value == -3
    assert c.registry.check(other, 'portfolio-book', 'old', 1, 'EXIT').reason == 'EXTERNAL_NOT_OWNED'


def test_manual_idempotency_conflict_and_out_of_order(ownership_case):
    c = ownership_case
    event = c.event()
    case = c.manual.observe(event)
    assert c.manual.observe(event) == case
    with pytest.raises(ValueError, match='EXTERNAL_EVENT_CONFLICT'):
        c.manual.observe(replace(event, observed_quantity=c.quantity('8')))
    with pytest.raises(ValueError, match='STALE_EXTERNAL_OBSERVATION'):
        c.manual.observe(c.event(name='older-event', quantity='10'))
    assert c.manual.risk_observation(c.scope).total_observed_quantity.value == 4
    c.source_sequence = 2
    c.manual.observe(c.event(kind='ORDER', quantity=None, name='order-change'))
    assert c.manual.risk_observation(c.scope).total_observed_quantity.value == 4
    assert c.registry.read(c.scope).position.owner_strategy_id == 'old'


def test_external_evidence_default_deny(ownership_case):
    c = ownership_case
    service = ManualInterventionService(c.registry, clock=c.manual.clock)
    with pytest.raises(ValueError, match='EXTERNAL_ARCHIVE_EVIDENCE_REQUIRED'):
        service.observe(c.event())
    assert c.manual.risk_observation(c.scope) is None


def test_register_cannot_silently_rebind_policy_revision(ownership_case):
    c = ownership_case
    c.registry.owners[c.scope] = ('old', 1, 2)
    with pytest.raises(ValueError, match='OWNERSHIP_CONFLICT'):
        c.registry.register(c.position, 2, contributors=('sub-strategy-a', 'sub-strategy-b'))
    assert c.registry.read(c.scope).policy_revision == 1
    assert c.probe().reason == 'OWNER_LEDGER_MISMATCH'


@pytest.mark.parametrize('distinct_key,reason', [
    (False, 'IDEMPOTENCY_CONFLICT'), (True, 'TRANSFER_ID_CONFLICT'),
])
def test_transfer_identity_cannot_cross_management_scopes(ownership_case, distinct_key, reason):
    c = ownership_case
    c.start()
    other = OwnershipCase(c.registry, replace(c.scope, venue_leg='SHORT'))
    result = other.service.request_transfer(other.request(),
        other.context('different-key' if distinct_key else None))
    assert result.reason == reason
    assert other.registry.read(other.scope).sequence == 1
    assert c.service.projection(c.transfer_id).request.scope == c.scope


def test_lost_freeze_blocks_epoch_commit(ownership_case):
    c = ownership_case
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    with c.registry.transaction(c.scope) as tx:
        lock = tx.locks()[0]
        # Simulate an incorrectly resolved lock in persisted state. Production
        # code supplies no such resolver and may not continue the handoff.
        tx.append('risk_locks', lock.lock_id, replace(lock, state='RESOLVED'), 1)
    result = c.service.advance(c.transfer_id, c.evidence())
    assert result.state == t.TransferState.TRANSFER_BLOCKED
    assert result.blocked_reasons == ('TRANSFER_FREEZE_REQUIRED',)
    assert result.ownership_epoch == 1
    assert c.probe(action='PROTECT').allowed
    assert c.probe().reason == 'TRANSFER_FROZEN'


def test_exit_flat_uses_bound_facts_when_ip13_residual_is_absent(ownership_case):
    c = ownership_case
    c.start('EXIT_THEN_SWITCH')
    c.advance_to(t.TransferState.RECONCILE_PENDING)
    for state in (t.TransferState.VERIFY_PROTECTION, t.TransferState.COMMIT_OWNER_EPOCH,
                  t.TransferState.ACTIVE):
        evidence = c.evidence(flat=True)
        evidence = replace(evidence, reconciliation=replace(evidence.reconciliation, residual=None))
        assert c.service.advance(c.transfer_id, evidence).state == state
    assert c.registry.read(c.scope).position.actual_quantity.value == 0
    assert c.registry.read(c.scope).position.ownership_epoch == 2


@pytest.mark.parametrize('residual', [None, '1'])
def test_missing_or_nonzero_residual_never_supplies_flat_proof(ownership_case, residual):
    c = ownership_case
    c.start('EXIT_THEN_SWITCH')
    c.advance_to(t.TransferState.RECONCILE_PENDING)
    evidence = c.evidence(flat=True)
    if residual is None:
        c.fact_changes['position'] = c.position  # independent current position is not flat
    evidence = replace(evidence, reconciliation=replace(evidence.reconciliation,
        residual=None if residual is None else c.quantity(residual)))
    result = c.service.advance(c.transfer_id, evidence)
    assert result.state == t.TransferState.RECONCILE_PENDING
    assert result.blocked_reasons == ('EXIT_NOT_FLAT_VERIFIED',)
    assert result.ownership_epoch == 1


def test_registry_adapter_requires_reviewed_cas_before_writing(ownership_case):
    c = ownership_case
    owner = c.registry.read(c.scope)
    adapter = RegistryTx(SimpleNamespace(scope=c.scope))
    new = replace(owner, position=replace(owner.position, owner_strategy_id='new', ownership_epoch=2))
    with pytest.raises(OwnerCommitUnavailable, match='REVIEWED_OWNER_CAS_REQUIRED'):
        adapter.commit_owner(owner, new)
    assert c.registry.read(c.scope) == owner


@pytest.mark.parametrize('failure', ['unavailable_after_write', 'wrong_postcondition'])
def test_registry_cas_callback_failure_aborts_whole_transfer(ownership_case, monkeypatch, failure):
    """Real RegistryTx CAS guard inside a synthetic rollback transaction."""
    c = ownership_case
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    before = c.registry.read(c.scope)
    raw_tx = SimpleNamespace(scope=c.scope, failed=False)

    def interrupted_callback(tx, old, new):
        assert tx is raw_tx
        c.registry.owners[c.scope] = ('new', 2 if failure == 'unavailable_after_write' else 99, 1)
        if failure == 'unavailable_after_write':
            raise OwnerCommitUnavailable('callback already wrote; must roll back')

    def commit(tx, old, new):
        adapter = RegistryTx(raw_tx, interrupted_callback)
        adapter.active_owner = tx.active_owner
        adapter.commit_owner(old, new)

    monkeypatch.setattr(MemoryTx, 'commit_owner', commit)
    with pytest.raises(RuntimeError, match='OWNER_CAS_FAILED'):
        c.service.advance(c.transfer_id, c.evidence())
    assert raw_tx.failed is True
    assert c.registry.owners[c.scope] == ('old', 1, 1)
    assert c.registry.read(c.scope) == before
    assert c.service.projection(c.transfer_id).state == t.TransferState.COMMIT_OWNER_EPOCH
    assert c.probe(action='PROTECT').allowed
    assert c.probe().reason == 'TRANSFER_FROZEN'


@pytest.mark.parametrize('fault', ['unit', 'stale', 'future', 'environment', 'missing-quantity'])
def test_invalid_external_observation_cannot_replace_risk(ownership_case, fault):
    c = ownership_case
    c.manual.observe(c.event())
    before = c.manual.risk_observation(c.scope)
    c.source_sequence = 2
    event = c.event(name='invalid-change')
    if fault == 'unit':
        event = replace(event, observed_quantity=replace(c.quantity('4'), unit='USD'))
    elif fault == 'missing-quantity':
        event = replace(event, observed_quantity=None)
    else:
        change = ({'environment': t.Environment.LIVE} if fault == 'environment' else
                  {'visible_at': '2026-10-22T00:00:00Z' if fault == 'stale' else '2026-10-24T00:00:00Z'})
        event = replace(event, evidence=replace(event.evidence, **change))
    with pytest.raises(ValueError):
        c.manual.observe(event)
    assert c.manual.risk_observation(c.scope) == before
    assert c.probe().reason == 'MANUAL_INTERVENTION'


@pytest.mark.parametrize('environment', [t.Environment.LIVE, t.Environment.TESTNET])
def test_online_environments_are_not_enabled(ownership_case, environment):
    c = ownership_case
    scope = replace(c.scope, environment=environment)
    with pytest.raises(ValueError, match='ENVIRONMENT_NOT_ENABLED'):
        c.service.request_transfer(replace(c.request(), scope=scope), c.context())
    with pytest.raises(ValueError, match='ENVIRONMENT_NOT_ENABLED'):
        c.manual.observe(replace(c.event(), scope=scope))


def test_postgres_process_crash_before_epoch(ip16_database):
    c, scenario = postgres_case(ip16_database)
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    process = multiprocessing.get_context('spawn').Process(target=crash_worker,
        args=(ip16_database, c.scope, c.old_owner, 'before_epoch'))
    process.start()
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail('native crash probe timed out')
    assert process.exitcode == 71
    owner = c.registry.read(c.scope)
    projection = c.service.projection(c.transfer_id)
    with c.registry.transaction(c.scope) as tx:
        assert tx.active_owner() == (owner.position.owner_strategy_id, 1, 1)
        assert tx.locks()
    assert owner.position.ownership_epoch == projection.ownership_epoch == 1
    assert owner.protection_responsibility == 'SYSTEM_PROTECTION'
    assert projection.state == t.TransferState.COMMIT_OWNER_EPOCH


def test_postgres_missing_reviewed_cas_blocks_handoff(ip16_database):
    c, scenario = postgres_case(ip16_database)
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    projection = c.service.advance(c.transfer_id, c.evidence())
    assert projection.state == t.TransferState.TRANSFER_BLOCKED
    assert projection.blocked_reasons == ('REVIEWED_OWNER_CAS_REQUIRED',)
    assert projection.ownership_epoch == 1
    with c.registry.transaction(c.scope) as tx:
        assert tx.active_owner() == (c.old_owner, 1, 1)
        assert tx.owner().protection_responsibility == 'SYSTEM_PROTECTION'


@pytest.mark.parametrize('phase', ['after_epoch_before_commit', 'after_commit'])
def test_native_epoch_commit_crash_not_run(ip16_database, phase, record_property):
    """Original process-crash scenarios now use the reviewed real PG CAS.

    Name retained for evidence continuity. Synthetic trusted archive facts do
    not qualify native venue protection; no transport is present in this test.
    """
    c, scenario = postgres_case(ip16_database, reviewed_cas=True)
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    attest_cas(c, ip16_database)
    before = c.registry.read(c.scope)
    record_property('before_crash_ledger', crash_ledger_json(c))
    process = multiprocessing.get_context('spawn').Process(target=crash_worker,
        args=(ip16_database, c.scope, c.old_owner, phase))
    process.start()
    process.join(20)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail('native crash probe timed out')
    assert process.exitcode == (72 if phase == 'after_epoch_before_commit' else 73)
    record_property('process_exit_code', process.exitcode)
    # Preserve the raw durable histories before any safe replay of the rollback.
    record_property('after_crash_ledger', crash_ledger_json(c))
    owner = c.registry.read(c.scope)
    projection = c.service.projection(c.transfer_id)
    expected_epoch = 1 if phase == 'after_epoch_before_commit' else 2
    expected_owner = c.old_owner if expected_epoch == 1 else 'new'
    with c.registry.transaction(c.scope) as tx:
        assert tx.active_owner() == (expected_owner, expected_epoch, 1)
        assert tx.locks()  # A handoff never silently resolves the freeze cause.
    assert owner.position.ownership_epoch == projection.ownership_epoch == expected_epoch
    assert owner.position.owner_book_id == before.position.owner_book_id
    assert owner.position.actual_quantity == before.position.actual_quantity
    assert owner.protection_responsibility == 'SYSTEM_PROTECTION'
    assert projection.state == (t.TransferState.COMMIT_OWNER_EPOCH if expected_epoch == 1 else t.TransferState.ACTIVE)
    assert c.probe(owner=expected_owner, epoch=expected_epoch, action='PROTECT').allowed
    assert not c.probe(owner=c.old_owner).allowed
    if expected_epoch == 1:
        # Abrupt rollback did not consume the independently issued authority.
        assert c.service.advance(c.transfer_id, c.evidence()).state == t.TransferState.ACTIVE
    else:
        assert c.service.advance(c.transfer_id, c.evidence()) == projection


def crash_ledger_json(case):
    import json
    from trading_system.storage.models import scope_id
    with case.registry.transaction(case.scope) as tx:
        snapshot = {'active_owner': tx.active_owner(), 'histories': {}}
        for table, record_id in (('audit_events', 'ip16:owner'),
                                 ('ownership_transfers', transfer_key(case.transfer_id)),
                                 ('risk_locks', 'ip16:freeze:' + case.transfer_id)):
            snapshot['histories'][table] = tx.tx.execute(
                f'SELECT sequence,payload,transaction_id FROM ledger.{table} '
                'WHERE scope_id=%s AND record_id=%s ORDER BY sequence',
                (scope_id(case.scope), record_id)).fetchall()
    return json.dumps(snapshot, sort_keys=True, separators=(',', ':'))


def test_postgres_manual_lock_survives_service_restart(ip16_database):
    c, scenario = postgres_case(ip16_database)
    event = c.event()
    case = c.manual.observe(event)
    restarted = ManualInterventionService(c.registry, clock=c.manual.clock)
    assert restarted.observe(event) == case
    assert restarted.risk_observation(c.scope).total_observed_quantity.value == 4
    assert c.probe(owner=c.old_owner).reason == 'MANUAL_INTERVENTION'
    with c.registry.transaction(c.scope) as tx:
        assert tx.locks()[0] == case.lock


def ready_postgres_cas(dsn):
    c, scenario = postgres_case(dsn, reviewed_cas=True)
    c.start()
    c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
    return c, scenario


@pytest.mark.parametrize('statement', [
    'UPDATE ledger.active_owners SET ownership_epoch=ownership_epoch+1',
    'UPDATE ledger.active_owners SET ownership_epoch=ownership_epoch WHERE false',
    'DELETE FROM ledger.active_owners', 'TRUNCATE ledger.active_owners',
    "INSERT INTO ledger.owner_cas_approvals(authority_id) VALUES ('forged')",
    "INSERT INTO ledger.owner_cas_authorities(authority_id) VALUES ('forged')",
    "SELECT ledger.commit_owner_transfer('forged','scope','{}','{}','tx')",
    "SELECT ledger.attest_owner_transfer('forged','scope','{}','{}','{}','{}','{}',clock_timestamp())",
    'SET ROLE ledger_owner_controller', 'SET ROLE ledger_owner_verifier',
])
def test_postgres_owner_cannot_forge_cas_authority(ip16_database, statement):
    import psycopg
    with connect_untrusted_owner(ip16_database) as conn:
        conn.execute("SELECT set_config('app.owner_cas_approved','true',true)")
        with pytest.raises(psycopg.Error):
            conn.execute(statement)
        conn.rollback()


def test_postgres_cas_roles_have_no_cross_authority_or_broad_update(ip16_database):
    import psycopg
    with psycopg.connect(ip16_database) as conn:
        for role in ('ledger_owner', 'ledger_control', 'ledger_decision', 'ledger_risk_resolver',
                     'ledger_owner_verifier', 'ledger_owner_controller'):
            assert conn.execute('SELECT has_function_privilege(%s,%s,\'EXECUTE\')',
                (role, 'ledger.commit_owner_transfer(text,text,jsonb,jsonb,text)')).fetchone()[0] is (role == 'ledger_owner_controller')
            assert conn.execute('SELECT has_function_privilege(%s,%s,\'EXECUTE\')',
                (role, 'ledger.attest_owner_transfer(text,text,jsonb,jsonb,jsonb,jsonb,jsonb,timestamp with time zone)')).fetchone()[0] is (role == 'ledger_owner_verifier')
            for column in ('owner_strategy_id', 'policy_revision'):
                assert not conn.execute('SELECT has_column_privilege(%s,\'ledger.active_owners\',%s,\'UPDATE\')',
                    (role, column)).fetchone()[0]
            for table in ('owner_cas_approvals', 'owner_cas_authorities', 'owner_cas_revocations'):
                assert not conn.execute('SELECT has_table_privilege(%s,%s,\'INSERT,UPDATE,DELETE,TRUNCATE\')',
                    (role, 'ledger.' + table)).fetchone()[0]
        assert conn.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid='ledger.active_owners'::regclass "
            "AND tgname IN ('owner_immutable','owner_cas_row') AND tgenabled='O'").fetchone()[0] == 2


@pytest.mark.parametrize('fault', [
    'scope', 'owner', 'epoch-skip', 'policy', 'quantity-unit', 'undercovered',
    'nan-coverage', 'missing-coverage', 'protection-responsibility', 'plan',
    'authorization', 'evidence-binding', 'expired',
])
def test_postgres_verifier_requires_exact_transfer_proof(ip16_database, fault):
    import json
    import psycopg
    c, scenario = ready_postgres_cas(ip16_database)
    before = c.registry.read(c.scope)

    def corrupt(v):
        if fault == 'scope':
            v['scope']['venue_leg'] = 'SHORT'
        elif fault == 'owner':
            v['new']['position']['owner_strategy_id'] = 'attacker'
        elif fault == 'epoch-skip':
            v['new']['position']['ownership_epoch'] = 3
        elif fault == 'policy':
            v['new']['policy_revision'] = 2
        elif fault == 'quantity-unit':
            v['proof']['facts']['position']['actual_quantity']['unit'] = 'USDT'
        elif fault == 'protection-responsibility':
            v['new']['protection_responsibility'] = 'NONE'
        elif fault == 'plan':
            v['proof']['facts']['accepted_exit_plan_id'] = 'unaccepted'
        elif fault == 'authorization':
            v['proof']['facts']['authorization_current'] = False
        elif fault == 'evidence-binding':
            v['proof']['facts']['evidence_hash'] = '0' * 64
        elif fault == 'expired':
            v['expiry'] = '2026-01-01T00:00:00Z'
        else:
            e = json.loads(v['proof']['evidence_json'])
            q = e['protection']['covered_quantity']
            if fault == 'missing-coverage':
                q.pop('value')
            else:
                q['value'] = '9' if fault == 'undercovered' else 'NaN'
            from trading_system.contracts.canonical import canonical_hash, canonical_json
            v['proof']['evidence_json'] = canonical_json(e)
            v['proof']['facts']['evidence_hash'] = canonical_hash({'value': e})

    with pytest.raises(psycopg.Error):
        attest_cas(c, ip16_database, mutate=corrupt)
    assert c.registry.read(c.scope) == before
    with c.registry.transaction(c.scope) as tx:
        assert tx.active_owner() == (c.old_owner, 1, 1)


def raw_cas(conn, values, transaction='raw-controller'):
    import json
    return conn.execute('SELECT ledger.commit_owner_transfer(%s,%s,%s::jsonb,%s::jsonb,%s)',
        (values['authority_id'], values['sid'], json.dumps(values['old']), json.dumps(values['new']), transaction))


def test_postgres_cas_requires_atomic_projections_even_for_controller(ip16_database):
    import psycopg
    c, scenario = ready_postgres_cas(ip16_database)
    values = attest_cas(c, ip16_database)
    before = c.registry.read(c.scope)
    with connect_role(ip16_database, 'ledger_owner_controller') as conn:
        raw_cas(conn, values)
        with pytest.raises(psycopg.errors.CheckViolation, match='atomic owner and transfer projections required'):
            conn.commit()
        conn.rollback()
    assert c.registry.read(c.scope) == before
    # The failed commit rolled back the approval as well as the active row.
    assert c.service.advance(c.transfer_id, c.evidence()).state == t.TransferState.ACTIVE


@pytest.mark.parametrize('fault', ['revoked', 'expired', 'owner-revision', 'ledger-revision',
                                  'authorization-revision', 'scope', 'old-owner'])
def test_postgres_cas_rechecks_current_authority(ip16_database, fault):
    from datetime import datetime, timedelta, timezone
    import psycopg
    c, scenario = ready_postgres_cas(ip16_database)
    if fault == 'expired':
        c.fact_changes['valid_until'] = (datetime.now(timezone.utc) + timedelta(milliseconds=150)).isoformat()
    values = attest_cas(c, ip16_database)
    if fault == 'revoked':
        with connect_role(ip16_database, 'ledger_owner_verifier') as conn:
            conn.execute('SELECT ledger.revoke_owner_transfer(%s)', (values['authority_id'],))
    elif fault == 'expired':
        import time
        time.sleep(.2)
    elif fault == 'owner-revision':
        with c.registry.transaction(c.scope) as tx:
            old = tx.owner()
            tx.save_owner(old, replace(old, sequence=old.sequence+1))
    elif fault == 'ledger-revision':
        with c.registry.transaction(c.scope) as tx:
            case = tx.get('reconciliation_cases', 'ip16:reconcile:' + c.transfer_id, t.ReconciliationCase)
            tx.append('reconciliation_cases', case.case_id, replace(case, attempts=1), 1)
    elif fault == 'authorization-revision':
        with scenario.connect_admin() as conn:
            conn.execute('INSERT INTO ledger.authorizations(scope_id,record_id,sequence,payload,transaction_id) '
                "VALUES ('global',%s,1,'{\"state\":\"REVOKED\"}','isolated-verifier-revocation')",
                ('revoked:' + c.transfer_id,))
    elif fault == 'scope':
        values['sid'] = 'another-scope'
    else:
        values['old']['position']['owner_strategy_id'] = 'stale'
    with connect_role(ip16_database, 'ledger_owner_controller') as conn:
        with pytest.raises(psycopg.Error):
            raw_cas(conn, values)
        conn.rollback()
    with c.registry.transaction(c.scope) as tx:
        assert tx.active_owner() == (c.old_owner, 1, 1)


def test_postgres_concurrent_cas_advances_exactly_one_epoch(ip16_database):
    import psycopg
    c, scenario = ready_postgres_cas(ip16_database)
    values = attest_cas(c, ip16_database)
    evidence = c.evidence()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: c.service.advance(c.transfer_id, evidence), range(2)))
    assert all(p.state == t.TransferState.ACTIVE and p.ownership_epoch == 2 for p in results)
    with c.registry.transaction(c.scope) as tx:
        assert tx.active_owner() == ('new', 2, 1)
        assert tx.owner().sequence == 7
    assert c.probe(owner=c.old_owner).reason == 'STALE_OWNER_EPOCH'
    assert c.probe(owner='new', epoch=2, action='PROTECT').allowed
    with connect_role(ip16_database, 'ledger_owner_controller') as conn:
        with pytest.raises(psycopg.Error):
            raw_cas(conn, values)  # A consumed authority never advances again.
        conn.rollback()


def test_postgres_controller_capability_without_trusted_selector_fails_closed(ip16_database):
    from trading_system.ownership.registry import PostgresOwnerCAS
    c, scenario = ready_postgres_cas(ip16_database)
    before = c.registry.read(c.scope)
    c.registry.owner_commit = PostgresOwnerCAS()
    with pytest.raises(RuntimeError, match='OWNER_CAS_FAILED'):
        c.service.advance(c.transfer_id, c.evidence())
    assert c.registry.read(c.scope) == before
    assert c.service.projection(c.transfer_id).state == t.TransferState.COMMIT_OWNER_EPOCH


@pytest.mark.parametrize('revoked', [False, True])
def test_postgres_waiting_cas_rechecks_expiry_and_revocation(ip16_database, revoked):
    """A stale snapshot before waiting on the collateral mutex cannot authorize."""
    from datetime import datetime, timedelta, timezone
    import time
    import psycopg
    c, scenario = ready_postgres_cas(ip16_database)
    if not revoked:
        c.fact_changes['valid_until'] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    values = attest_cas(c, ip16_database)
    guard_connection = connect_role(ip16_database, 'ledger_owner_verifier') if revoked else scenario.connect_owner()
    with guard_connection as guard:
        if revoked:
            guard.execute('SELECT ledger.revoke_owner_transfer(%s)', (values['authority_id'],))
        else:
            # Test admin holds the same group lock through a normal UoW.
            guard.execute('SELECT group_id FROM ledger.collateral_mutex WHERE environment=%s AND group_id=%s FOR UPDATE',
                ('PAPER', scenario.group))
        with connect_role(ip16_database, 'ledger_owner_controller') as waiting:
            pid = waiting.execute('SELECT pg_backend_pid()').fetchone()[0]
            waiting.commit()

            def commit_after_wait():
                with pytest.raises(psycopg.errors.CheckViolation) as error:
                    raw_cas(waiting, values)
                waiting.rollback()
                return str(error.value)

            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(commit_after_wait)
                deadline = time.monotonic()+3
                try:
                    with scenario.connect_admin() as admin:
                        while not admin.execute('SELECT cardinality(pg_blocking_pids(%s))', (pid,)).fetchone()[0]:
                            assert time.monotonic()<deadline, 'CAS did not wait on the owner/group guard'
                            time.sleep(.01)
                    if not revoked:
                        time.sleep(1.05)
                finally:
                    guard.commit()  # Release the waiter even if the probe fails.
                message = pending.result(timeout=5)
                assert ('revoked or stale' if revoked else 'current authority binding') in message
    with c.registry.transaction(c.scope) as tx:
        assert tx.active_owner() == (c.old_owner, 1, 1)


def test_postgres_owner_cas_preserves_exact_decimal_and_protection(ip16_database):
    from decimal import Decimal, localcontext
    c, scenario = postgres_case(ip16_database, reviewed_cas=True)
    value = Decimal('9007199254740993.123456789123456789123456789')
    with c.registry.transaction(c.scope) as tx:
        old = tx.owner()
        tx.save_owner(old, replace(old, sequence=old.sequence+1,
            position=replace(old.position, actual_quantity=replace(old.position.actual_quantity, value=value))))
    c.position = c.registry.read(c.scope).position
    # Bind the reviewed control revision and exact coverage to the changed row.
    context = replace(c.context(), expected_sequence=2)
    assert c.service.request_transfer(c.request(), context).state == t.TransferState.REQUESTED
    original_evidence = c.evidence
    c.evidence = lambda **kw: replace(original_evidence(**kw), protection=replace(
        original_evidence(**kw).protection, covered_quantity=c.position.actual_quantity))
    # The original transfer code's Decimal abs precision bug is a separately
    # reviewed sibling change. A sufficiently large context isolates CAS here.
    with localcontext() as ctx:
        ctx.prec = 100
        c.advance_to(t.TransferState.COMMIT_OWNER_EPOCH)
        attest_cas(c, ip16_database)
        assert c.service.advance(c.transfer_id, c.evidence()).state == t.TransferState.ACTIVE
    owner = c.registry.read(c.scope)
    assert owner.position.actual_quantity.value == value
    assert str(owner.position.actual_quantity.value) == str(value)
    assert owner.protection_responsibility == 'SYSTEM_PROTECTION'
    assert owner.position.owner_book_id == c.position.owner_book_id


@pytest.mark.parametrize('attack', ['early-check-owner', 'early-check-transfer', 'expiry',
                                  'early-check-expiry', 'early-check-named', 'early-check-do',
                                  'early-check-multistatement', 'manual-lock', 'reconciliation',
                                  'async-commit', 'late-deferred-expiry'])
def test_postgres_cas_cannot_bypass_final_guard(ip16_database, attack):
    """Real controller SQL after correct CAS/projections, before actual COMMIT."""
    from datetime import datetime, timedelta, timezone
    import time
    import psycopg
    c, scenario = ready_postgres_cas(ip16_database)
    if 'expiry' in attack:
        c.fact_changes['valid_until'] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    attest_cas(c, ip16_database)
    before = crash_ledger_json(c)
    persist = c.service._persist
    reached_final_projections = False

    def after_correct_projections(tx, *args, **kwargs):
        nonlocal reached_final_projections
        updated = persist(tx, *args, **kwargs)
        if updated.projection.state != t.TransferState.ACTIVE:
            return updated
        reached_final_projections = True
        assert tx.tx.execute('SELECT current_user').fetchone()[0] == 'ledger_owner_controller'
        if attack == 'early-check-named':
            tx.tx.execute('SET CONSTRAINTS ledger.owner_cas_atomic_commit IMMEDIATE')
        elif attack == 'early-check-do':
            tx.tx.execute('DO $$ BEGIN SET CONSTRAINTS ALL IMMEDIATE; END $$')
        elif attack == 'early-check-multistatement':
            tx.tx.execute('SET CONSTRAINTS ALL IMMEDIATE; COMMIT')
        elif attack.startswith('early-check'):
            tx.tx.execute('SET CONSTRAINTS ALL IMMEDIATE')
        if attack == 'early-check-owner':
            old = tx.owner()
            tx.save_owner(old, replace(old, sequence=old.sequence+1,
                position=replace(old.position, ownership_epoch=1, owner_strategy_id=c.old_owner)))
        elif attack == 'early-check-transfer':
            tx.append('ownership_transfers', transfer_key(c.transfer_id), replace(updated,
                sequence=updated.sequence+1, projection=replace(updated.projection,
                    state=t.TransferState.COMMIT_OWNER_EPOCH, ownership_epoch=1)), updated.sequence)
        elif attack == 'late-deferred-expiry':
            tx.tx.execute('CREATE TEMP TABLE cas_commit_delay(value integer) ON COMMIT DROP')
            tx.tx.execute('CREATE FUNCTION pg_temp.cas_commit_delay() RETURNS trigger LANGUAGE plpgsql AS '
                "$$ BEGIN PERFORM pg_sleep(1.1); RETURN NEW; END $$")
            tx.tx.execute('CREATE CONSTRAINT TRIGGER cas_commit_delay AFTER INSERT ON cas_commit_delay '
                'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pg_temp.cas_commit_delay()')
            tx.tx.execute('INSERT INTO cas_commit_delay VALUES(1)')
        elif 'expiry' in attack:
            time.sleep(1.1)
        elif attack == 'manual-lock':
            tx.raise_lock(t.RiskLock(lock_id='manual:' + c.transfer_id, scope=c.scope,
                cause='MANUAL_INTERVENTION', state='ACTIVE', permission=t.RiskPermission.NO_NEW_RISK,
                required_evidence=('RECONCILIATION',), raised_at=c.stamp), c.ref('manual'))
        elif attack == 'async-commit':
            tx.tx.execute('SET LOCAL synchronous_commit=off')
        else:
            case = tx.get('reconciliation_cases', 'ip16:reconcile:' + c.transfer_id, t.ReconciliationCase)
            tx.append('reconciliation_cases', case.case_id, replace(case, attempts=case.attempts+1), 1)
        return updated

    c.service._persist = after_correct_projections
    with pytest.raises(psycopg.errors.CheckViolation):
        c.service.advance(c.transfer_id, c.evidence())
    assert reached_final_projections, 'the attack must execute after real CAS and both correct projections'
    assert crash_ledger_json(c) == before
