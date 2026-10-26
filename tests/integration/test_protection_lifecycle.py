"""IP-15 application probes authored for the final batch; not T/Q evidence.

Synthetic receipts exercise real manager/compiler/verifier transitions. Native
kill/network/order qualification, latency and real-platform safety stay NOT_RUN.
"""
from dataclasses import replace
from decimal import Decimal

import pytest

from integration.support_ip_15 import (
    ProtectionCase, SimulatedProcessLoss, database_protection_case, ip15_database,
    price, protection_case, quantity,
)
from trading_system.contracts import types as t
from trading_system.contracts.wire import ABSENT
from trading_system.protection.manager import (
    CommandRequest, ProtectionRepository, validate_command,
)
from trading_system.protection.native import NativeVerifier, digest
from trading_system.storage.models import SequenceConflict, scope_id


def test_partial_fill_then_cancel_unknown(protection_case):
    c = protection_case
    c.partial_unknown()
    result = c.protect()
    assert result.plan.coverage_quantity == quantity('0.4')
    assert [cmd.operation for cmd in result.commands] == ['SET_PROTECTION']
    assert result.commands[0].canonical_payload.quantity == '0.4'
    assert c.record().entries == c.entries
    assert c.record().entries[0].order.leaves_quantity == quantity('0.6')
    assert c.record().entries[0].order.command_certainty == 'UNKNOWN'
    assert 'entry-cancel-original' in c.record().case.command_ids
    assert 'ENTRY_REMAINDER_UNRESOLVED:entry-cancel-original' in result.missing
    assert all(lock.permission != t.RiskPermission.NORMAL for lock in result.locks)
    # No reservation repository is involved; the original unknown is not canceled again.
    assert len(c.dispatcher.calls) == 1


def test_late_fill_preserves_unknown_remainder_and_original_stop(protection_case):
    c = protection_case
    c.partial_unknown()
    original = c.protect().plan.native_order_ids
    c.position = replace(c.position, actual_quantity=quantity('0.6'))
    c.fill = replace(c.fill, fill_id='late-fill', native_trade_id='late-trade', quantity=quantity('0.2'))
    c.entries = ()  # An omitted projection does not erase the old uncertainty.
    result = c.protect()
    assert result.plan.coverage_quantity == quantity('0.6')
    assert result.plan.native_order_ids == original
    assert result.plan.state == t.ProtectionState.GAP
    assert len(c.record().entries) == 1
    assert c.record().entries[0].order.leaves_quantity == quantity('0.6')
    assert 'QUERY_ORIGINAL_THEN_REPLACE' in result.missing
    assert not result.commands and len(c.dispatcher.calls) == 1


def test_stop_unknown_query_original(protection_case):
    c = protection_case
    c.dispatcher.status = t.CommandState.UNKNOWN
    first = c.protect()
    ids = c.record().command_ids
    c.restart()
    duplicate = c.protect()
    assert not duplicate.commands
    assert c.record().command_ids == ids
    assert c.record().plan.native_order_ids == first.plan.native_order_ids
    assert any('QUERY_ORIGINAL:' in reason for reason in duplicate.missing)
    replacement = c.manager.replace_protection(c.position, c.next_plan(), c.capability,
        approved_flow='CANCEL_CONFIRM_REPLACE')
    assert 'QUERY_ORIGINAL_BEFORE_REPLACEMENT' in replacement.missing
    assert len(c.dispatcher.calls) == 1


def test_failed_protect_and_failed_exit(protection_case):
    c = protection_case
    c.dispatcher.status = t.CommandState.VENUE_REJECTED
    first = c.protect()
    rejected = c.projection(first.plan.native_order_ids[0], state=t.OrderLifecycle.REJECTED, leaves='0')
    context = c.prepare_exit(orders=(rejected,))
    receipt = c.exits.request(c.position, context)
    assert receipt.state == 'EXITING'
    assert len(receipt.command_ids) == 1
    assert c.record().case.state == 'OPEN'
    assert c.record().plan.state == t.ProtectionState.EXITING
    assert 'PROTECTION_GAP' in {lock.cause for lock in c.record().locks}
    assert all(lock.permission == t.RiskPermission.EXIT_ONLY for lock in c.record().locks)
    assert any(reason.startswith('QUERY_ORIGINAL:') for reason in receipt.incomplete_reasons)
    assert len(c.dispatcher.calls) == 2
    again = c.exits.request(c.position, replace(context,
        context=replace(context.context, idempotency_key='another-click')))
    assert again.command_ids == receipt.command_ids
    assert again.incomplete_reasons == ('QUERY_ORIGINAL_EXIT',)
    assert len(c.dispatcher.calls) == 2


def test_replace_and_dust(protection_case):
    c = protection_case
    c.protect()
    c.manager.verify(c.record().plan, c.native_report())
    old = c.record().plan.native_order_ids[0]
    new = c.next_plan()
    denied = c.manager.replace_protection(c.position, new, c.capability)
    assert 'APPROVED_REPLACEMENT_FLOW_REQUIRED' in denied.missing
    assert len(c.dispatcher.calls) == 1
    started = c.manager.replace_protection(c.position, new, c.capability,
        approved_flow='CANCEL_CONFIRM_REPLACE')
    assert started.commands[0].operation == 'CANCEL'
    assert started.commands[0].target_order_id == old.identity.logical_client_order_id
    assert started.plan.state == t.ProtectionState.GAP
    assert started.plan.coverage_gap_since == c.now
    # Mere absence of the old order cannot advance the replacement.
    empty = c.report()
    waiting = c.manager.replace_protection(c.position, new, c.capability,
        report=empty, read_report_proof=lambda record, report: digest(report))
    assert not waiting.commands
    terminal = c.projection(old, state=t.OrderLifecycle.CANCELED, leaves='0')
    report = c.report(orders=(terminal,))
    pending = c.manager.replace_protection(c.position, new, c.capability,
        report=report, read_report_proof=lambda record, report: digest(report))
    assert [command.operation for command in pending.commands] == ['SET_PROTECTION']
    assert pending.plan.state == t.ProtectionState.GAP
    progress = c.record().replacement
    report = c.native_report(progress.new)
    complete = c.manager.replace_protection(c.position, new, c.capability,
        report=report, read_report_proof=lambda record, report: digest(report))
    assert complete.plan.state == t.ProtectionState.VERIFIED_NATIVE
    assert c.record().replacement.phase == 'COMPLETE'
    assert complete.plan.coverage_gap_since is None
    assert 'CAUSE_LOCK_RESOLUTION_REQUIRED' in complete.missing
    assert complete.locks  # A native check does not clear independent reason locks.


@pytest.mark.parametrize('product,expected', [
    (t.Product.SPOT, 'NONTRADABLE_RESIDUAL'),
    (t.Product.US_EQUITY, 'NONTRADABLE_RESIDUAL'),
    (t.Product.USD_M_PERP, 'EXITING'),
])
def test_dust_is_disclosed_without_illegal_orders(product, expected):
    c = ProtectionCase(product=product, amount='0.001')
    try:
        action = c.protect()
        assert action.plan.state == t.ProtectionState.GAP
        assert 'BELOW_VENUE_MINIMUM_OR_STEP' in action.missing
        receipt = c.exits.request(c.position, c.prepare_exit())
        assert receipt.state == expected
        assert c.record().position.actual_quantity == quantity('0.001')
        assert not receipt.command_ids and not c.dispatcher.calls
        assert c.record().locks and c.record().case.state == 'OPEN'
    finally:
        c.close()


def test_reduce_scope_and_flat_proof(protection_case):
    c = protection_case
    context = c.prepare_exit()
    bad = replace(context, reducible_quantity=quantity('0.5'))
    assert 'REDUCIBLE_QUANTITY_INVALID' in c.exits.request(c.position, bad).incomplete_reasons
    bad = replace(context, context=replace(context.context,
        scopes=(replace(c.scope, venue_leg='SHORT'),)))
    assert 'EXIT_SCOPE_OR_OWNER_INVALID' in c.exits.request(c.position, bad).incomplete_reasons
    assert not c.dispatcher.calls
    receipt = c.exits.request(c.position, context)
    assert receipt.state == 'EXITING' and len(receipt.command_ids) == 1
    assert c.exits.verify_flat(c.position, c.report()).state == 'EXITING'
    empty = c.report(positions=())
    c.attest_flat(empty)
    assert c.exits.verify_flat(c.position, empty).state == 'EXITING'
    zero = replace(c.position, actual_quantity=quantity('0'))
    report = c.report(positions=(zero,))
    c.attest_flat(report)
    result = c.exits.verify_flat(c.position, report)
    assert result.state == 'FLAT_VERIFIED'
    assert result.incomplete_reasons == ('FEES_PENDING',)
    assert c.record().position.actual_quantity == quantity('0')
    assert c.record().locks and c.record().case.state == 'OPEN'
    assert c.exits.request(zero, context) == result


def test_crash_between_fill_and_protection_receipt_reuses_durable_identity(protection_case):
    c = protection_case
    c.dispatcher.crash = True
    with pytest.raises(SimulatedProcessLoss):
        c.protect()
    before = c.record()
    assert before.plan.state == t.ProtectionState.PENDING
    assert before.locks and before.command_ids and before.case.state == 'OPEN'
    c.dispatcher.crash = False
    c.restart()
    result = c.protect()
    assert result.plan.native_order_ids == before.plan.native_order_ids
    assert len(c.dispatcher.calls) == 1
    assert c.repository.commands(c.scope, before.command_ids)[0].state == 'MAY_HAVE_SENT'
    assert 'DUPLICATE_FILL_NO_DISPATCH' in result.missing


def test_manual_deletion_and_expiry_create_gap_without_model_call(protection_case):
    c = protection_case
    c.protect()
    assert c.manager.verify(c.record().plan, c.native_report()).state == t.ProtectionState.VERIFIED_NATIVE
    plan = c.record().plan
    canceled = c.native_report(state=t.OrderLifecycle.CANCELED)
    assert c.manager.verify(plan, canceled).state == t.ProtectionState.GAP
    assert c.record().plan.coverage_gap_since == c.now
    assert c.record().locks and c.record().case.state == 'OPEN'
    assert len(c.dispatcher.calls) == 1


@pytest.mark.parametrize('oco', [False, True])
def test_spot_double_sell_requires_verified_combination_compiler(oco):
    c = ProtectionCase(product=t.Product.SPOT)
    try:
        c.take_profit = True
        c.capability = replace(c.capability, oco=oco)
        result = c.protect()
        assert 'VERIFIED_OCO_COMPILER_REQUIRED' in result.missing
        assert result.plan.state == t.ProtectionState.GAP
        assert not result.commands and not c.dispatcher.calls
    finally:
        c.close()


def test_create_receipt_alone_never_verifies_native(protection_case):
    c = protection_case
    result = c.protect()
    assert result.plan.state == t.ProtectionState.PENDING
    report = c.native_report()
    verdict = NativeVerifier(clock=lambda: c.now).verify(result.plan, report, c.position)
    assert verdict.state == t.ProtectionState.GAP
    assert verdict.covered_quantity == quantity('0')
    assert 'NATIVE_PROOF_MISSING_OR_MISMATCH' in verdict.missing


@pytest.mark.parametrize('fault', ['quantity', 'side', 'trigger', 'reduce_only', 'full_exit',
    'owner', 'venue_id', 'state', 'pending', 'certainty', 'stale', 'future', 'pagination',
    'unsupported', 'scope', 'position', 'proof_hash', 'missing_order', 'duplicate_order'])
def test_native_evidence_negative_matrix(protection_case, fault):
    c = protection_case
    plan = c.protect().plan
    report = c.native_report()
    order = report.orders[0]
    if fault == 'quantity':
        report = replace(report, orders=(replace(order, leaves_quantity=quantity('0.2')),))
    elif fault in {'state', 'pending', 'certainty'}:
        changes = {'state': {'lifecycle_state': t.OrderLifecycle.CANCELED},
            'pending': {'pending_operation': 'CANCEL'}, 'certainty': {'command_certainty': 'UNKNOWN'}}
        report = replace(report, orders=(replace(order, **changes[fault]),))
    elif fault == 'owner':
        report = replace(report, orders=(replace(order, order=replace(order.order, owner_strategy_id='other')),))
    elif fault == 'venue_id':
        report = replace(report, orders=(replace(order,
            order=replace(order.order, identity=replace(order.order.identity, venue_order_id=None))),))
    elif fault in {'stale', 'future'}:
        report = replace(report, as_of=c.later(-61 if fault == 'stale' else 1))
    elif fault in {'pagination', 'unsupported', 'scope'}:
        changes = {'pagination': {'pagination_complete': False}, 'unsupported': {'supported': False},
                   'scope': {'scope': replace(c.scope, venue_account_id='other')}}
        report = replace(report, coverage=replace(report.coverage, **changes[fault]))
    elif fault == 'position':
        report = replace(report, positions=(replace(c.position, actual_quantity=quantity('0.6')),))
    elif fault == 'missing_order':
        report = replace(report, orders=())
    elif fault == 'duplicate_order':
        report = replace(report, orders=(order, order))
    proof = c.attest_native(plan, report)
    if fault in {'side', 'trigger', 'reduce_only', 'full_exit'}:
        changes = {'side': {'side': 'BUY'}, 'trigger': {'trigger': 'MARK'},
                   'reduce_only': {'reduce_only': False}, 'full_exit': {'full_position_exit': True}}
        c.attest_native(plan, report, orders=(replace(proof.orders[0], **changes[fault]),))
    elif fault == 'proof_hash':
        c.attest_native(plan, report, report_hash='0' * 64)
    verdict = c.manager.verify(plan, report)
    assert verdict.state == t.ProtectionState.GAP and verdict.missing
    assert verdict.covered_quantity == quantity('0')
    assert c.record().locks


@pytest.mark.parametrize('fault', ['before', 'after', 'read', 'compile', 'dispatch'])
def test_storage_and_boundary_failures_keep_incomplete_state(protection_case, fault):
    c = protection_case
    if fault == 'before':
        c.repository.fail_before_save = True
    elif fault == 'after':
        c.repository.fail_after_save = True
    elif fault == 'read':
        c.repository.fail_load = True
    elif fault == 'compile':
        def fail_compile(request):
            raise ValueError('synthetic compiler failure')
        c.manager.compile_command = fail_compile
    else:
        c.dispatcher.error = OSError('synthetic dispatch error')
    result = c.protect()
    assert result.plan.state != t.ProtectionState.VERIFIED_NATIVE
    assert result.locks and result.missing
    if fault != 'dispatch':
        assert not c.dispatcher.calls
    if fault == 'after':
        original = c.record().command_ids
        c.repository.fail_after_save = False
        c.restart()
        assert not c.protect().commands
        assert c.record().command_ids == original
        assert not c.dispatcher.calls


@pytest.mark.parametrize('fault,reason', [
    ('closed', 'MARKET_CLOSED_OR_HALTED'), ('unbounded', 'BOUNDED_MARKET_EXIT_UNAVAILABLE'),
    ('price', 'EXIT_PRICE_BOUND_EXCEEDED'), ('orders', 'EXISTING_ORDERS_REQUIRE_RECONCILIATION'),
    ('balance', 'CURRENT_QUANTITY_UNVERIFIED'), ('owner', 'CURRENT_EXIT_AUTHORITY_REQUIRED'),
    ('stale', 'EXIT_AUTHORITY_OR_QUANTITY_STALE'), ('revision', 'EXIT_SEQUENCE_OR_KEY_INVALID'),
])
def test_exit_preconditions_keep_exposure(fault, reason):
    c = ProtectionCase(product=t.Product.SPOT)
    try:
        context = c.prepare_exit()
        if fault == 'closed':
            c.exit_facts = replace(c.exit_facts, market_open=False)
        elif fault == 'unbounded':
            context = replace(context, allowed_order_types=('MARKET',))
            c.exit_facts = replace(c.exit_facts, order_type='MARKET', context_hash=digest(context))
        elif fault == 'price':
            c.exit_facts = replace(c.exit_facts, limit_price=price('90'))
        elif fault == 'orders':
            c.exit_facts = replace(c.exit_facts, report=c.report(orders=(c.projection(c.ref_order('entry')),)))
        elif fault == 'balance':
            c.exit_facts = replace(c.exit_facts, balances_and_allocations_verified=False)
        elif fault == 'owner':
            c.exit_facts = replace(c.exit_facts, owner_strategy_id='other')
        elif fault == 'stale':
            context = replace(context, expires_at=c.later(-1))
        elif fault == 'revision':
            context = replace(context, context=replace(context.context, expected_sequence=10))
        receipt = c.exits.request(c.position, context)
        assert reason in receipt.incomplete_reasons
        assert receipt.state != 'FLAT_VERIFIED'
        assert not c.dispatcher.calls
        if fault == 'closed':
            assert receipt.state == 'EXIT_PENDING_MARKET'
    finally:
        c.close()


@pytest.mark.parametrize('fault', ['positions', 'orders', 'fees', 'commands', 'balance',
                                  'owner', 'context', 'active_order', 'empty', 'nonzero'])
def test_flat_requires_all_proofs(fault):
    c = ProtectionCase(product=t.Product.SPOT)
    try:
        c.exits.request(c.position, c.prepare_exit())
        zero = replace(c.position, actual_quantity=quantity('0'))
        report = c.report(positions=(zero,))
        if fault == 'active_order':
            report = replace(report, orders=(c.projection(c.ref_order('unresolved-stop')),))
        elif fault == 'empty':
            report = replace(report, positions=())
        elif fault == 'nonzero':
            report = replace(report, positions=(c.position,))
        changes = {'positions': {'positions_verified': False}, 'orders': {'orders_verified': False},
            'fees': {'economic_missing_recorded': False}, 'commands': {'commands_resolved': ()},
            'balance': {'balances_and_allocations_verified': False}, 'owner': {'ownership_epoch': 2},
            'context': {'exit_context_hash': 'other'}}
        c.attest_flat(report, **changes.get(fault, {}))
        result = c.exits.verify_flat(c.position, report)
        assert result.state == 'EXITING' and result.incomplete_reasons
        assert c.record().position.actual_quantity == quantity('0.4')
    finally:
        c.close()


@pytest.mark.parametrize('leg,amount,side', [('LONG', '0.4', 'SELL'),
    ('SHORT', '0.4', 'BUY'), ('BOTH', '-0.4', 'BUY')])
def test_compiler_bounds_each_derivative_leg(leg, amount, side):
    c = ProtectionCase(leg=leg, amount=amount)
    try:
        context = c.prepare_exit()
        result = c.exits.request(c.position, context)
        assert len(result.command_ids) == 1
        command = c.repository.commands(c.scope, result.command_ids)[0]
        payload = command.canonical_payload
        assert payload.side == side and payload.position_side == leg
        assert Decimal(payload.quantity) == abs(Decimal(amount))
        assert payload.reduce_only is True and payload.full_position_exit is False
        assert payload.quote_notional is ABSENT
    finally:
        c.close()


def test_compiler_rejects_wrong_leg_and_unencodable_trigger(protection_case):
    c = protection_case
    request = CommandRequest(key='bounded', operation='CLOSE', position=c.position,
        plan=c.plan, quantity=quantity('0.4'), order_type='LIMIT', price=price('100'))
    compiled = c.compiler(request)
    bad = replace(compiled, command=replace(compiled.command, canonical_payload=replace(
        compiled.command.canonical_payload, position_side='SHORT')))
    with pytest.raises(ValueError, match='COMMAND_BINDING_MISMATCH'):
        validate_command(request, bad)
    c.plan = replace(c.plan, stop_trigger='MARK')
    c.capability = replace(c.capability, triggers=('LAST', 'MARK'))
    result = c.protect()
    assert 'COMMAND_COMPILATION_FAILED' in result.missing
    assert not c.dispatcher.calls


def test_live_rejected_before_storage_or_dispatch(protection_case):
    c = protection_case
    position = replace(c.position, scope=replace(c.scope, environment=t.Environment.LIVE))
    with pytest.raises(ValueError, match='ENVIRONMENT_NOT_ENABLED'):
        c.manager.on_fill(c.fill, position)
    with pytest.raises(ValueError, match='ENVIRONMENT_NOT_ENABLED'):
        c.exits.request(position, c.prepare_exit())
    assert not c.repository.history and not c.dispatcher.calls


@pytest.mark.parametrize('mode,first', [('atomic', 'AMEND'), ('coexist', 'SET_PROTECTION'),
                                     ('close', 'CANCEL')])
def test_replacement_modes_keep_original_until_evidence(protection_case, mode, first):
    c = protection_case
    c.protect()
    c.manager.verify(c.record().plan, c.native_report())
    old = c.record().plan.native_order_ids[0]
    c.capability = replace(c.capability, atomic_amend=mode == 'atomic', coexist=mode == 'coexist')
    new = c.next_plan()
    action = c.manager.replace_protection(c.position, new, c.capability,
        approved_flow='CANCEL_CONFIRM_CLOSE' if mode == 'close' else None)
    assert [command.operation for command in action.commands] == [first]
    assert c.record().replacement.old.native_order_ids == (old,)
    progress = c.record().replacement
    if mode == 'close':
        report = c.report(orders=(c.projection(old, state=t.OrderLifecycle.CANCELED, leaves='0'),))
    else:
        report = c.native_report(progress.new)
    next_action = c.manager.replace_protection(c.position, new, c.capability,
        report=report, read_report_proof=lambda record, report: digest(report))
    if mode == 'atomic':
        assert not next_action.commands
        assert c.record().replacement.phase == 'COMPLETE'
    elif mode == 'close':
        assert not next_action.commands
        assert 'AUTHORIZED_EXIT_REQUIRED' in next_action.missing
        assert c.record().replacement.phase == 'EXIT_REQUIRED'
    else:
        assert [command.operation for command in next_action.commands] == ['CANCEL']
        assert next_action.commands[0].target_order_id == old.identity.logical_client_order_id
        assert c.record().replacement.phase == 'VERIFY_OLD_CANCEL'
        progress = c.record().replacement
        terminal = c.projection(old, state=t.OrderLifecycle.CANCELED, leaves='0')
        report = replace(c.native_report(progress.new), orders=(
            c.projection(progress.new.native_order_ids[0]), terminal))
        c.attest_native(progress.new, report)
        done = c.manager.replace_protection(c.position, new, c.capability,
            report=report, read_report_proof=lambda record, report: digest(report))
        assert done.plan.state == t.ProtectionState.VERIFIED_NATIVE
        assert c.record().replacement.phase == 'COMPLETE'


def test_replacement_rejects_stale_or_changed_position_report(protection_case):
    c = protection_case
    c.protect()
    old = c.record().plan.native_order_ids[0]
    new = c.next_plan()
    c.manager.replace_protection(c.position, new, c.capability, approved_flow='CANCEL_CONFIRM_REPLACE')
    canceled = c.projection(old, state=t.OrderLifecycle.CANCELED, leaves='0')
    for report in (c.report(orders=(canceled,), as_of=c.later(-1)),
                   c.report(orders=(canceled,), positions=(replace(c.position, actual_quantity=quantity('0.2')),))):
        action = c.manager.replace_protection(c.position, new, c.capability,
            report=report, read_report_proof=lambda record, report: digest(report))
        assert 'REPLACEMENT_REPORT_UNVERIFIED' in action.missing
        assert not action.commands
    assert len(c.dispatcher.calls) == 2


def test_stop_verification_cannot_overwrite_exiting(protection_case):
    c = protection_case
    c.protect()
    old = c.record().plan.native_order_ids[0]
    terminal = c.projection(old, state=t.OrderLifecycle.CANCELED, leaves='0')
    c.exits.request(c.position, c.prepare_exit(orders=(terminal,)))
    verdict = c.manager.verify(c.record().plan, c.native_report())
    assert verdict.state == t.ProtectionState.EXITING
    assert c.record().exit_receipt.state == 'EXITING'
    c.position = replace(c.position, actual_quantity=quantity('0.6'))
    c.fill = replace(c.fill, fill_id='late-entry-during-exit', quantity=quantity('0.2'))
    action = c.protect()
    assert action.plan.state == t.ProtectionState.EXITING
    assert 'EXIT_IN_PROGRESS' in action.missing and not action.commands


def test_verified_native_persistence_failure_never_returns_verified(protection_case):
    c = protection_case
    plan = c.protect().plan
    report = c.native_report()
    c.repository.fail_before_save = True
    verdict = c.manager.verify(plan, report)
    assert verdict.state == t.ProtectionState.GAP
    assert 'STORAGE_UNAVAILABLE' in verdict.missing
    assert c.record().plan.state == t.ProtectionState.PENDING


def test_expired_verification_is_not_extended_by_old_receipt(protection_case):
    c = protection_case
    c.protect()
    report = c.native_report()
    assert c.manager.verify(c.record().plan, report).state == t.ProtectionState.VERIFIED_NATIVE
    c.now = c.later(61)
    verdict = c.manager.verify(c.record().plan, report)
    assert verdict.state == t.ProtectionState.GAP
    assert c.record().plan.last_verified_at is None
    assert c.record().plan.coverage_gap_since == c.now


def test_model_failure_does_not_disable_protection_planning(protection_case):
    c = protection_case
    model_lock = t.RiskLock(lock_id='model-health', scope=c.scope, cause='MODEL_HEALTH',
        state='ACTIVE', permission=t.RiskPermission.NO_NEW_RISK,
        required_evidence=('VALID_MODEL_OUTPUT',), raised_at=c.now)
    c.position = replace(c.position, risk_locks=(model_lock,))
    result = c.protect()
    assert [command.operation for command in result.commands] == ['SET_PROTECTION']
    assert c.record().position.risk_locks == (model_lock,)
    assert c.record().locks[0].cause == 'PROTECTION_GAP'
    # Recorder reachability is not evidence that the real dispatcher permits it.
    assert len(c.dispatcher.calls) == 1


def test_flat_persistence_failure_and_missing_report_remain_exiting(protection_case):
    c = protection_case
    c.exits.request(c.position, c.prepare_exit())
    assert c.exits.verify_flat(c.position, None).state == 'EXITING'
    report = c.report(positions=(replace(c.position, actual_quantity=quantity('0')),))
    c.attest_flat(report)
    c.repository.fail_before_save = True
    receipt = c.exits.verify_flat(c.position, report)
    assert receipt.state == 'EXITING' and 'STORAGE_UNAVAILABLE' in receipt.incomplete_reasons
    assert c.record().plan.state == t.ProtectionState.EXITING


def test_repository_persists_gap_before_real_dispatcher_denial(database_protection_case):
    c, dispatch = database_protection_case
    result = c.protect()
    assert any(reason.startswith('DISPATCH_INCOMPLETE:') for reason in result.missing)
    assert not dispatch.engine.transport.writes
    assert dispatch.facts_calls == 1  # Existing IP-10 lock gate, not a mock denial.
    restarted = ProtectionRepository(dispatch.ledger.uow)
    saved = restarted.load(c.scope, c.position.position_id)
    assert saved == c.record()
    assert restarted.load_plan(c.scope, saved.plan.plan_id) == saved
    assert saved.locks[0].cause == 'PROTECTION_GAP'
    assert saved.case.state == 'OPEN'
    assert restarted.commands(c.scope, saved.command_ids)[0].state == 'DURABLE_READY'
    with dispatch.ledger.uow.transaction(c.scope) as tx:
        assert tx.risk_locks.active(c.scope) == saved.locks
        reservations = tx.reservations.for_intent(dispatch.intent.intent_id)
        assert reservations[0].state == t.ReservationState.HELD


def test_repository_stale_revision_is_atomic(database_protection_case):
    c, dispatch = database_protection_case
    c.protect()
    saved = c.record()
    with pytest.raises(SequenceConflict):
        c.repository.save(replace(saved, revision=saved.revision + 1), None)
    assert c.record() == saved
    assert not dispatch.engine.transport.writes


def test_repository_gap_event_is_compatible_with_dedicated_resolution(database_protection_case):
    from trading_system.risk.locks import RiskLockService
    c, dispatch = database_protection_case
    c.protect()
    lock = c.record().locks[0]
    # Deliberately insufficient proof must keep the lock; this is not permission
    # to resolve it just because a receipt or an offline fixture exists.
    resolution = t.ResolutionEvidence(lock_id=lock.lock_id, scope=c.scope,
        evidence=(c.ref('insufficient-resolution'),), authorization_id='')
    denied = RiskLockService(dispatch.ledger.uow).resolve(lock.lock_id, resolution)
    assert denied.resolved is False
    with dispatch.ledger.uow.transaction(c.scope) as tx:
        row = tx.execute('SELECT sequence FROM ledger.aggregate_heads '
            'WHERE scope_id=%s AND aggregate_id=%s',
            (scope_id(c.scope), 'events:risk-lock:' + lock.lock_id)).fetchone()
    assert row == (1,)


def test_repository_commit_failure_never_dispatches(database_protection_case):
    c, dispatch = database_protection_case
    # First commit loads the current record; fail the lifecycle write commit.
    dispatch.fail_commit = dispatch.commits + 2
    result = c.protect()
    dispatch.fail_commit = None
    assert 'STORAGE_UNAVAILABLE' in result.missing
    assert 'NOT_DISPATCHED' in result.missing
    assert c.record() is None
    assert dispatch.facts_calls == 0 and not dispatch.engine.transport.writes
