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
    InjectedCrash, MemoryTx, OwnershipCase, crash_worker, ip16_database, ownership_case, postgres_case,
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
    assert c.start().reason == 'RECONCILIATION_REQUIRED'
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
def test_native_epoch_commit_crash_not_run(phase):
    pytest.skip('NOT_RUN: reviewed active-owner CAS migration/capability is outside IP-16 ownership; '
                'native crash probe cannot replace it with a permissive role or mock')


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
