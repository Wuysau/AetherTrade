"""Authored offline/isolated SQL probes, not registered T/Q acceptance results."""
from dataclasses import replace
from decimal import Decimal

import pytest

from integration.support_ip_14 import child_case, plan_case, ip10_database
from unit.support_ip_09 import quantity, price
from trading_system.contracts import types as t
from trading_system.contracts.wire import ExecutionCommandCanonicalPayload
from trading_system.execution.children import ChildPlanner, make_command, save_window
from trading_system.execution.commands import CommandRepository, IdempotencyConflict
from trading_system.execution.cancel_amend import CancelItem
from trading_system.execution.batch_policy import BatchPolicy
from trading_system.execution.dispatcher import DispatchDenied
from trading_system.intents.semantic_validation import ProposalRejected
from trading_system.storage.models import SequenceConflict


def test_controlled_slices_have_exact_total_and_stable_distinct_ids(plan_case):
    commands = plan_case.plan()
    assert [c.canonical_payload.quantity for c in commands] == ['0.004', '0.004', '0.002']
    assert sum(Decimal(c.canonical_payload.quantity) for c in commands) == Decimal('0.010')
    assert commands == plan_case.plan()
    assert len({c.command_id for c in commands}) == 3
    assert len({c.parent_command_id for c in commands}) == 1
    assert all(plan_case.codec.role(c.venue_client_order_id) == 'ORDER' for c in commands)
    assert all(c.state == 'DURABLE_READY' and c.durable_record_ref.startswith('pending:') for c in commands)


@pytest.mark.parametrize('algorithm', ['TWAP', 'NATIVE', 'EMULATOR'])
def test_uncontrolled_native_algorithms_disabled(plan_case, algorithm):
    with pytest.raises(ValueError, match='uncontrolled'):
        ChildPlanner(plan_case.planner.compiler, plan_case.codec, 1, algorithm=algorithm)


@pytest.mark.parametrize('change,code', [
    ({'maximum_children': 2}, 'MAXIMUM_CHILDREN'),
    ({'slice_quantity': quantity('0.0045')}, 'QUANTITY_OFF_GRID'),
    ({'expires_at': '2026-10-23T06:39:01Z'}, 'EXPIRED'),
    ({'compensation_policy': ''}, 'COMPENSATION_POLICY_REQUIRED'),
])
def test_invalid_child_policy_fails_closed(plan_case, change, code):
    plan_case.policy = replace(plan_case.policy, **change)
    with pytest.raises(ProposalRejected, match=code):
        plan_case.plan()


def test_partial_execution_requires_explicit_permission(plan_case):
    plan_case.intent = replace(plan_case.intent,
        execution=replace(plan_case.intent.execution, allow_partial=False))
    with pytest.raises(ProposalRejected, match='PARTIAL_EXECUTION'):
        plan_case.plan()


def test_every_child_has_durable_command(child_case):
    """Real SQL committed-marker observations; recorder is never venue evidence."""
    case = child_case
    identifiers = case.persist()
    assert len(identifiers) == 3
    assert case.persist() == identifiers
    for identifier in identifiers:
        assert case.dispatcher.dispatch(identifier, case.lease).status == t.CommandState.VENUE_CONFIRMED
    protection = make_command(case.intent, case.codec, case.lease.shard.execution_epoch,
        'protection:' + case.ledger.suffix, 'SET_PROTECTION',
        ExecutionCommandCanonicalPayload(side='SELL', order_type='STOP_MARKET', quantity='0.010',
            trigger_price='49000', reduce_only=True, position_side='BOTH'))
    assert case.codec.role(protection.venue_client_order_id) == 'PROTECTION'
    with case.uow.transaction(case.scope) as tx:
        CommandRepository(tx).append(protection)
        save_window(tx, protection, case.policy.expires_at)
    case.dispatcher.dispatch(protection.command_id, case.lease)
    assert len(case.engine.observations) == 4
    assert all(state == 'MAY_HAVE_SENT' and marker == 1
               for state, marker, identity in case.engine.observations)
    assert len({w['command_id'] for w in case.engine.transport.writes}) == 4


def test_expired_unsent_children_stay_unsent(child_case):
    identifiers = child_case.persist()
    child_case.expire_windows()
    for identifier in identifiers:
        with pytest.raises(DispatchDenied, match='EXPIRED'):
            child_case.dispatcher.dispatch(identifier, child_case.lease)
    assert not child_case.engine.transport.writes
    with child_case.uow.transaction(child_case.scope) as tx:
        assert all(CommandRepository(tx).get(i).state == 'DURABLE_READY' for i in identifiers)


def test_unknown_child_keeps_original_id_after_expiry(child_case):
    identifier = child_case.persist()[0]
    child_case.engine.response_lost = True
    assert child_case.dispatcher.dispatch(identifier, child_case.lease).status == t.CommandState.UNKNOWN
    child_case.expire_windows()
    assert child_case.dispatcher.dispatch(identifier, child_case.lease).status == t.CommandState.UNKNOWN
    assert len(child_case.engine.transport.writes) == 1


def test_replanning_changed_book_is_rejected(child_case):
    child_case.persist()
    child_case.book = replace(child_case.book, sequence=2)
    with pytest.raises(SequenceConflict, match='already exists'):
        child_case.persist()


def test_failed_plan_commit_never_sends(child_case):
    child_case.fail_commit = child_case.commits + 1
    with pytest.raises(OSError, match='commit'):
        child_case.persist()
    assert not child_case.engine.transport.writes
    child_case.fail_commit = None
    assert len(child_case.persist()) == 3


def test_missing_child_window_cannot_dispatch(child_case):
    # IP-10's seed command has no IP-14 window.
    with pytest.raises(DispatchDenied, match='WINDOW_MISSING'):
        child_case.dispatcher.dispatch(child_case.command.command_id, child_case.lease)
    assert not child_case.engine.transport.writes


def test_revocation_after_durable_marker_prevents_send(child_case):
    identifier = child_case.persist()[0]

    def revoke(call):
        if call == 2:
            child_case.binding = replace(child_case.binding, authorization=replace(
                child_case.binding.authorization, revoked_at=child_case.intent.created_at))

    child_case.on_facts = revoke
    assert child_case.dispatcher.dispatch(identifier, child_case.lease).status == t.CommandState.UNKNOWN
    assert not child_case.engine.transport.writes


def test_failed_action_persistence_never_sends(child_case):
    owned = child_case.owned()
    child_case.fail_commit = child_case.commits + 1
    with pytest.raises(OSError):
        child_case.action_service.cancel(owned, child_case.context())
    assert not child_case.engine.transport.writes


def test_cancel_owned_ids_only(child_case):
    case = child_case
    owned = case.owned()
    manual = case.owned('manual', owner='manual-owner')
    other = case.owned('other', owner='other-strategy')
    other_leg = replace(owned, identity=replace(owned.identity,
        scope=replace(case.scope, venue_leg='SHORT')))
    results = case.action_service.cancel_many(tuple(CancelItem(order=o, context=case.context(str(i)))
        for i, o in enumerate((owned, manual, other, other_leg))))
    assert [w['target_order_id'] for w in case.engine.transport.writes] == ['venue:owned_order']
    assert results.results[0].receipt.status == t.CommandState.VENUE_CONFIRMED
    assert all(r.receipt is None for r in results.results[1:])
    assert not results.all_confirmed


def test_partial_cancel_results(child_case):
    case = child_case
    targets = [case.owned(str(i)) for i in range(3)]
    calls = 0

    def responses():
        nonlocal calls
        calls += 1
        case.engine.status = (t.CommandState.VENUE_CONFIRMED if calls == 1
                              else t.CommandState.VENUE_REJECTED)
        case.engine.response_lost = calls == 3

    case.engine.after_send = responses
    result = case.action_service.cancel_many(tuple(CancelItem(order=o, context=case.context(str(i)))
        for i, o in enumerate(targets)))
    assert [r.receipt.status for r in result.results] == [t.CommandState.VENUE_CONFIRMED,
        t.CommandState.VENUE_REJECTED, t.CommandState.UNKNOWN]
    assert not result.all_confirmed
    assert result.results[2].reconciliation_required
    assert len(case.engine.transport.writes) == 3


def test_control_replay_and_conflicting_payload(child_case):
    case = child_case
    owned, context = case.owned(), case.context()
    first = case.action_service.cancel(owned, context)
    assert case.action_service.cancel(owned, context) == first
    assert len(case.engine.transport.writes) == 1
    with pytest.raises(IdempotencyConflict):
        case.action_service.amend(owned, t.AmendRequest(target_order=owned, quantity=quantity('0.005'),
            price=None, trigger_price=None, expected_revision=1), context)


@pytest.mark.parametrize('mutation', ['owner', 'account', 'product', 'epoch', 'venue_id', 'native_id'])
def test_forged_target_is_not_sent(child_case, mutation):
    case = child_case
    owned = case.owned()
    if mutation == 'owner':
        owned = replace(owned, owner_strategy_id='foreign')
    elif mutation == 'epoch':
        owned = replace(owned, ownership_epoch=2)
    elif mutation in ('account', 'product'):
        scope = replace(case.scope, **({'venue_account_id': 'foreign'} if mutation == 'account'
                                      else {'product': t.Product.USD_M_PERP}))
        owned = replace(owned, identity=replace(owned.identity, scope=scope))
    else:
        owned = replace(owned, identity=replace(owned.identity,
            **({'venue_order_id': 'forged'} if mutation == 'venue_id' else {'native_client_order_id': 'forged'})))
    with pytest.raises(ValueError):
        case.action_service.cancel(owned, case.context())
    assert not case.engine.transport.writes


@pytest.mark.parametrize('mutation', ['stale', 'pending', 'unknown', 'terminal', 'revoked'])
def test_action_rechecks_current_authority_and_projection(child_case, mutation):
    case = child_case
    owned = case.owned()
    p = case.projections['owned_order']
    if mutation == 'revoked':
        case.binding = replace(case.binding,
            authorization=replace(case.binding.authorization, revoked_at=case.intent.created_at))
    else:
        changes = {'stale': {'sequence': 2}, 'pending': {'pending_operation': 'CANCEL'},
            'unknown': {'command_certainty': 'UNKNOWN'},
            'terminal': {'lifecycle_state': t.OrderLifecycle.CANCELED}}[mutation]
        case.projections['owned_order'] = replace(p, **changes)
    with pytest.raises(DispatchDenied):
        case.action_service.cancel(owned, case.context())
    assert not case.engine.transport.writes


@pytest.mark.parametrize('amount', ['0', '-1', 'NaN', '50000.001'])
def test_invalid_amend_price_never_sends(child_case, amount):
    owned = child_case.owned()
    with pytest.raises(ValueError):
        child_case.action_service.amend(owned, t.AmendRequest(target_order=owned,
            quantity=None, price=price(amount), trigger_price=None, expected_revision=1), child_case.context())
    assert not child_case.engine.transport.writes


@pytest.mark.parametrize('mutation', ['revision', 'field', 'quantity'])
def test_amend_rejects_stale_disallowed_and_below_fill_changes(child_case, mutation):
    owned = child_case.owned()
    request = t.AmendRequest(target_order=owned, quantity=quantity('0.005'),
                            price=None, trigger_price=None, expected_revision=1)
    if mutation == 'revision':
        request = replace(request, expected_revision=2)
    elif mutation == 'field':
        request = replace(request, trigger_price=price('49000'))
    else:
        child_case.projections['owned_order'] = replace(child_case.projections['owned_order'],
            filled_quantity=quantity('0.006'))
    with pytest.raises(DispatchDenied):
        child_case.action_service.amend(owned, request, child_case.context())
    assert not child_case.engine.transport.writes


def test_amend_has_independent_durable_identity(child_case):
    case = child_case
    owned = case.owned()
    changes = t.AmendRequest(target_order=owned, quantity=quantity('0.005'),
                            price=price('49999'), trigger_price=None, expected_revision=1)
    case.engine.response_lost = True
    result = case.action_service.amend(owned, changes, case.context('amend'))
    assert result.status == t.CommandState.UNKNOWN
    assert case.engine.methods == ['amend']
    wire = case.engine.transport.writes[0]
    assert wire['command_id'] != owned.identity.logical_client_order_id
    assert wire['target_order_id'] == owned.identity.venue_order_id
    assert wire['venue_order_id'] is None
    assert case.action_service.amend(owned, changes, case.context('amend')).status == t.CommandState.UNKNOWN
    assert len(case.engine.transport.writes) == 1


def test_multi_leg_partial_success(plan_case):
    commands = plan_case.plan()
    legs = (plan_case.leg(commands[0], t.CommandState.VENUE_CONFIRMED, '0.001'),
            plan_case.leg(commands[1], t.CommandState.VENUE_REJECTED),
            plan_case.leg(commands[2], t.CommandState.UNKNOWN))
    result = BatchPolicy().evaluate(legs, plan_case.now)
    assert result.partial_success and not result.atomic_success and result.requires_attention
    assert [r.possible_remaining.value for r in result.legs] == [Decimal('0.003'), Decimal(0), Decimal('0.002')]
    assert [r.retained_budget for r in result.legs] == [leg.held_budget for leg in legs]
    assert 'VERIFY_PROTECTION_FOR_OBSERVED_FILLS' in result.legs[0].actions
    assert 'RECONCILE_ORIGINAL_COMMAND' in result.legs[2].actions
    assert all('REQUEST_OWNED_CANCEL' not in r.actions for r in result.legs)
    compensated = BatchPolicy().evaluate(legs, plan_case.now, plan_case.compensation())
    assert 'REQUEST_OWNED_CANCEL' in compensated.legs[0].actions
    assert 'REQUEST_OWNED_CANCEL' not in compensated.legs[2].actions
    assert all('HEDGE' not in action for r in compensated.legs for action in r.actions)


@pytest.mark.parametrize('change', [ {'authorization_id': 'wrong'}, {'ownership_epoch': 2},
    {'scopes': ()}, {'expires_at': '2026-10-23T06:39:00Z'} ])
def test_invalid_compensation_cannot_propose_cancel(plan_case, change):
    commands = plan_case.plan()
    legs = (plan_case.leg(commands[0], t.CommandState.VENUE_CONFIRMED),
            plan_case.leg(commands[1], t.CommandState.VENUE_REJECTED))
    result = BatchPolicy().evaluate(legs, plan_case.now, replace(plan_case.compensation(), **change))
    assert result.policy_applied is None
    assert all('REQUEST_OWNED_CANCEL' not in r.actions for r in result.legs)


def test_batch_missing_and_contradictory_evidence_stays_unknown(plan_case):
    commands = plan_case.plan()
    legs = (plan_case.leg(commands[0], None),
            plan_case.leg(commands[1], t.CommandState.VENUE_REJECTED, '0.001'))
    result = BatchPolicy().evaluate(legs, plan_case.now)
    assert all(r.outcome == 'UNKNOWN' for r in result.legs)
    assert all(r.retained_budget for r in result.legs)
    with pytest.raises(ValueError, match='different command'):
        BatchPolicy().evaluate((replace(legs[1], receipt=replace(legs[1].receipt,
            command_id='foreign')),), plan_case.now)


def test_duplicate_batch_commands_rejected(plan_case):
    leg = plan_case.leg(plan_case.plan()[0], t.CommandState.VENUE_CONFIRMED)
    with pytest.raises(ValueError, match='unique'):
        BatchPolicy().evaluate((leg, leg), plan_case.now)


def test_separate_scope_budgets_are_never_netted(plan_case):
    commands = plan_case.plan()
    first = plan_case.leg(commands[0], t.CommandState.VENUE_CONFIRMED)
    second = plan_case.leg(commands[1], t.CommandState.VENUE_REJECTED)
    other_scope = replace(second.order.identity.scope, venue_account_id='other-paper-account')
    second = replace(second, intent=replace(second.intent, venue_account_id=other_scope.venue_account_id),
        command=replace(second.command, venue_account_id=other_scope.venue_account_id),
        order=replace(second.order, identity=replace(second.order.identity, scope=other_scope)),
        held_budget=(t.Money(amount=Decimal('20'), currency='EUR'),))
    result = BatchPolicy().evaluate((first, second), plan_case.now, plan_case.compensation())
    assert result.partial_success and result.policy_applied is None
    assert result.legs[0].retained_budget[0].currency == 'USDT'
    assert result.legs[1].retained_budget[0].currency == 'EUR'
    assert result.legs[1].order.identity.scope == other_scope


@pytest.mark.parametrize('environment', [t.Environment.TESTNET, t.Environment.LIVE])
def test_online_scope_is_disabled(plan_case, environment):
    book = replace(plan_case.book, scope=replace(plan_case.book.scope, environment=environment))
    with pytest.raises(DispatchDenied, match='OFFLINE_ONLY'):
        plan_case.planner.plan(plan_case.intent, book, plan_case.policy)
