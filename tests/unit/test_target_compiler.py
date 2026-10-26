"""Offline IP-09 probes; no registered acceptance or native qualification claims.

The in-memory transaction probe checks orchestration, not database isolation.
All scenarios are authored for the final batch runner and were not run here.
"""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from decimal import localcontext

import pytest
from jsonschema.exceptions import ValidationError

from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec
from trading_system.intents.ingress import AcceptedProposal, accepted_intent, revision_key
from trading_system.intents.semantic_validation import (
    ProposalRejected, quantity_from_notional, quantity_on_grid,
)
from trading_system.intents.target_compiler import PendingQuantity, TargetCompiler, compile_locked
from trading_system.risk.exposure import WorkingOrder
from trading_system.storage.models import SequenceConflict, decode, encode
from unit.support_ip_09 import (
    D, BEFORE, END, NOW, ingress_case, make_case, order, position, price, quantity, reservation,
)


@pytest.fixture
def target_case():
    return make_case()


def compile_book(case, *, book=None, facts=None, intent=None):
    return TargetCompiler(facts or case.target).compile(intent or case.bind(), book or case.book)


def records(ledger):
    return [value for (table, _), (_, value) in ledger.records.items() if table == 'proposals']


def test_target_minus_filled_and_working(target_case):
    plan = target_case.compile(target='0.010', actual='0.004', working='0.003')
    assert plan.submit_quantity == '0.003'
    assert plan.actual.value == D('0.004') and plan.working.value == D('0.003')
    assert plan.classification == 'INCREASE' and not plan.cancel_first


def test_distinct_proposals_same_target_subtract_current_fills_and_working(target_case):
    """T24's original injection expressed as an offline intake probe only."""
    case = target_case
    ledger, service = ingress_case(case)
    first = service.accept(case.proposal, case.context)
    assert first.status == t.IntentState.VALIDATING
    ledger.book_value = replace(case.book, sequence=2, basis_fingerprint='c' * 64,
        positions=(position(case.book.scope, '0.004'),), active_orders=(order(case.book.scope, '0.006'),))
    case.target = replace(case.target, order_sides=(WorkingOrder(logical_client_order_id='order', side='BUY'),))
    second = service.accept(replace(case.proposal, proposal_id='second-proposal'), case.context)
    assert second.intent_id != first.intent_id
    assert second.status == t.IntentState.VALIDATING
    assert [r.plan.remaining.value for r in records(ledger)] == [D('0.010'), D('0.000')]
    assert ledger.book_value.positions[0].actual_quantity.value == D('0.004')
    assert all(table in ('proposals', 'audit_events') for table, _ in ledger.records)


@pytest.mark.parametrize('lifecycle', [t.OrderLifecycle.ACCEPTED, t.OrderLifecycle.CANCELED])
def test_cancel_unknown_retains_possible_fills(target_case, lifecycle):
    case = target_case
    book = replace(case.book, positions=(position(case.book.scope, '0.004'),),
        active_orders=(order(case.book.scope, '0.003', pending='CANCEL', certainty='UNKNOWN', lifecycle=lifecycle),))
    facts = replace(case.target, order_sides=(WorkingOrder(logical_client_order_id='order', side='BUY'),))
    plan = compile_book(case, book=book, facts=facts)
    assert plan.submit_quantity == '0.003'
    assert plan.working.value == D('0.003')


def test_unknown_order_without_quantity_bound_rejected(target_case):
    book = replace(target_case.book, active_orders=(order(target_case.book.scope, '0', certainty='UNKNOWN'),))
    facts = replace(target_case.target, order_sides=(WorkingOrder(logical_client_order_id='order', side='BUY'),))
    with pytest.raises(ProposalRejected, match='UNKNOWN_QUANTITY_REQUIRED'):
        compile_book(target_case, book=book, facts=facts)


def test_lower_target_waits_for_old_leaves_without_erasing_fills(target_case):
    plan = target_case.compile(target='0.005', actual='0.004', working='0.003')
    assert plan.remaining.value == 0 and plan.classification == 'NONE'
    assert len(plan.cancel_first) == 1
    assert plan.actual.value == D('0.004') and plan.working.value == D('0.003')


def test_reduction_is_signed_and_preserves_actual(target_case):
    plan = target_case.compile(target='0.003', actual='0.010', working='0')
    assert plan.submit_quantity == '-0.007' and plan.classification == 'REDUCE'
    assert plan.actual.value == D('0.010')


@pytest.mark.parametrize('change', ['review', 'duration', 'stop', 'target', 'expiry', 'entry', 'leverage'])
def test_hold_does_not_mutate_exit(target_case, change):
    case = target_case
    case.previous()
    proposal = replace(case.proposal, action='HOLD')
    if change == 'review':
        proposal = replace(proposal, exit_plan=replace(proposal.exit_plan, review_due_at='2026-10-23T06:46:00Z'))
    elif change == 'duration':
        proposal = replace(proposal, exit_plan=replace(proposal.exit_plan, maximum_holding_seconds=7200))
    elif change == 'stop':
        proposal = replace(proposal, exit_plan=replace(proposal.exit_plan, stop_price='48000'))
    elif change == 'target':
        proposal = replace(proposal, target=replace(proposal.target, value='0.020'))
    elif change == 'expiry':
        proposal = replace(proposal, valid_until='2026-10-23T06:39:59Z')
        proposal = replace(proposal, entry=replace(proposal.entry, entry_deadline=proposal.valid_until))
    elif change == 'entry':
        proposal = replace(proposal, entry=replace(proposal.entry, limit_price='49999'))
    else:
        proposal = replace(proposal, requested_leverage=2)
    with pytest.raises((ProposalRejected, ValidationError)):
        case.bind(proposal)


def test_hold_cannot_remove_stop(target_case):
    target_case.previous()
    proposal = replace(target_case.proposal, action='HOLD',
        exit_plan=replace(target_case.proposal.exit_plan, stop_price=None))
    with pytest.raises((ProposalRejected, ValidationError)):
        target_case.bind(proposal)


def test_hold_produces_no_cancellation_or_delta(target_case):
    case = target_case
    previous = case.previous()
    case.book = replace(case.book, positions=(position(case.book.scope, '0.020'),),
        active_orders=(order(case.book.scope, '0.003'),))
    case.target = replace(case.target, order_sides=(WorkingOrder(logical_client_order_id='order', side='BUY'),))
    ledger, service = ingress_case(case)
    receipt = service.accept(replace(case.proposal, action='HOLD'), case.context)
    assert receipt.reason == 'HOLD_NO_ACTION'
    record = records(ledger)[0]
    assert not record.plan.cancel_first and record.plan.remaining.value == 0
    bound = accepted_intent(record)
    assert bound.exit_plan == previous.exit_plan and bound.valid_until == previous.valid_until


def test_hold_rejects_server_side_loss_change_as_receipt(target_case):
    case = target_case
    case.previous()
    case.binding = replace(case.binding, max_planned_loss=replace(case.binding.max_planned_loss, amount=D('20')))
    ledger, service = ingress_case(case)
    receipt = service.accept(replace(case.proposal, action='HOLD'), case.context)
    assert receipt.status == t.IntentState.REJECTED and receipt.reason == 'RESPONSIBILITY_CHANGED'
    assert not records(ledger)


def test_update_exit_preserves_normalized_target_and_requires_risk_review(target_case):
    case = target_case
    case.proposal = replace(case.proposal, target=replace(case.proposal.target, sizing='QUOTE_NOTIONAL', value='500'))
    previous = case.previous()
    proposal = replace(case.proposal, action='UPDATE_EXIT',
        exit_plan=replace(case.proposal.exit_plan, stop_price='48000'))
    ledger, service = ingress_case(case)
    receipt = service.accept(proposal, case.context)
    assert receipt.status == t.IntentState.VALIDATING and receipt.reason == 'EXIT_RISK_REVIEW_REQUIRED'
    record = records(ledger)[0]
    assert accepted_intent(record).target_quantity == previous.target_quantity
    assert record.plan.remaining.value == 0 and not record.plan.cancel_first


@pytest.mark.parametrize('action', ['HOLD', 'UPDATE_EXIT'])
def test_responsibility_requires_matching_previous_proposal(target_case, action):
    case = target_case
    case.previous()
    case.binding = replace(case.binding, previous_proposal=replace(case.proposal, proposal_id='unrelated'))
    with pytest.raises(ProposalRejected, match='PREVIOUS_PROPOSAL_MISMATCH'):
        case.bind(replace(case.proposal, action=action))


def test_update_exit_cannot_change_target_or_execution(target_case):
    case = target_case
    case.previous()
    for proposal in (
        replace(case.proposal, action='UPDATE_EXIT', target=replace(case.proposal.target, value='0.020')),
        replace(case.proposal, action='UPDATE_EXIT', entry=replace(case.proposal.entry, limit_price='49999')),
    ):
        with pytest.raises(ProposalRejected):
            case.bind(proposal)


def test_reversal_requires_flat():
    """T51's long-to-short injection, including independent flat re-evaluation."""
    case = make_case(t.Product.USD_M_PERP)
    case.proposal = replace(case.proposal, target=replace(case.proposal.target, side='SHORT'),
        exit_plan=replace(case.proposal.exit_plan, stop_price='51000'))
    intent = case.bind()
    long_book = replace(case.book, positions=(position(case.book.scope, '0.004', 'CONTRACT'),))
    with pytest.raises(ProposalRejected, match='REVERSAL_REQUIRES_FLAT'):
        compile_book(case, intent=intent, book=long_book)
    # A separately supplied complete zero book permits a NEW evaluation only.
    plan = compile_book(case, intent=case.bind(), book=case.book)
    assert plan.remaining.value == D('-0.010')
    residual = replace(case.book, active_orders=(order(case.book.scope, '0.004', unit='CONTRACT'),))
    sides = replace(case.target, order_sides=(WorkingOrder(logical_client_order_id='order', side='BUY'),))
    with pytest.raises(ProposalRejected, match='REVERSAL_REQUIRES_FLAT'):
        compile_book(case, book=residual, facts=sides)


def test_closing_orders_cannot_net_away_reversal():
    case = make_case(t.Product.USD_M_PERP)
    case.proposal = replace(case.proposal, target=replace(case.proposal.target, side='SHORT'),
        exit_plan=replace(case.proposal.exit_plan, stop_price='51000'))
    book = replace(case.book, positions=(position(case.book.scope, '0.004', 'CONTRACT'),),
        active_orders=(order(case.book.scope, '0.004', unit='CONTRACT'),))
    facts = replace(case.target, order_sides=(WorkingOrder(logical_client_order_id='order', side='SELL'),))
    with pytest.raises(ProposalRejected, match='REVERSAL_REQUIRES_FLAT'):
        compile_book(case, book=book, facts=facts)


def test_tick_revision_changed_before_send(target_case):
    """Offline pre-send recomputation fence, not a real permit/bridge probe."""
    case = target_case
    intent = case.bind()
    facts = replace(case.target, instrument=replace(case.target.instrument,
        rule_revision=2, tick_size=price('0.1'), quantity_step=quantity('0.004')))
    ledger, _ = ingress_case(case)
    with ledger.transaction(case.book.scope) as tx:
        with pytest.raises(ProposalRejected, match='INSTRUMENT_RULE_CHANGED'):
            compile_locked(tx, intent, lambda *_: facts)
    assert intent.target_quantity == '0.010'


def test_rounding_is_explicit_and_never_up(target_case):
    case = target_case
    proposal = replace(case.proposal, target=replace(case.proposal.target, value='0.0109'))
    with pytest.raises(ProposalRejected, match='QUANTITY_OFF_GRID'):
        case.bind(proposal)
    case.binding = replace(case.binding, allow_round_down=True)
    assert case.bind(proposal).target_quantity == '0.010'
    with pytest.raises(ProposalRejected, match='QUANTITY_BELOW_STEP'):
        quantity_on_grid(D('0.0009'), D('0.001'), True)


def test_notional_conversion_cannot_round_across_step_boundary():
    value = D('0.' + '9' * 100)
    with localcontext() as ctx:
        ctx.prec = 4
        assert quantity_from_notional(value, D('1'), D('0.1'), True) == D('0.9')
        with pytest.raises(ProposalRejected, match='QUANTITY_OFF_GRID'):
            quantity_from_notional(D('1'), D('3'), D('0.1'), False)


def test_delta_below_minimum_never_rounded_up(target_case):
    spec = replace(target_case.target.instrument, minimum_quantity=quantity('0.005'))
    target_case.target = replace(target_case.target, instrument=spec, expected_instrument=spec)
    with pytest.raises(ProposalRejected, match='BELOW_MINIMUM_QUANTITY'):
        target_case.compile(target='0.010', actual='0.009', working='0')


def test_pending_reservations_contribute_quantity_once(target_case):
    case = target_case
    r1, r2 = reservation(case), reservation(case, identifier='fees')
    book = replace(case.book, positions=(position(case.book.scope, '0.004'),), reservations=(r1, r2))
    pending = PendingQuantity(intent_id='older-intent', signed_quantity=quantity('0.003'),
                              reservation_ids=('reservation', 'fees'))
    plan = compile_book(case, book=book, facts=replace(case.target, pending=(pending,)))
    assert plan.remaining.value == D('0.003') and plan.working.value == D('0.003')


def test_reservation_already_in_order_is_not_counted_twice(target_case):
    case = target_case
    book = replace(case.book, active_orders=(order(case.book.scope, '0.003'),), reservations=(reservation(case),))
    pending = PendingQuantity(intent_id='older-intent', signed_quantity=quantity('0'),
        reservation_ids=('reservation',), represented_order_ids=('order',))
    facts = replace(case.target, pending=(pending,),
                    order_sides=(WorkingOrder(logical_client_order_id='order', side='BUY'),))
    assert compile_book(case, book=book, facts=facts).working.value == D('0.003')
    bad = replace(facts, pending=(replace(pending, signed_quantity=quantity('0.003')),))
    with pytest.raises(ProposalRejected, match='RESERVATION_DOUBLE_COUNT'):
        compile_book(case, book=book, facts=bad)


@pytest.mark.parametrize('problem', ['missing', 'zero', 'duplicate', 'negative', 'wrong_owner'])
def test_ambiguous_reservations_fail_closed(target_case, problem):
    case = target_case
    r = reservation(case)
    pending = PendingQuantity(intent_id='older-intent', signed_quantity=quantity('0.003'),
                              reservation_ids=('reservation',))
    facts = replace(case.target, pending=(pending,))
    reservations = (r,)
    if problem == 'missing':
        facts = replace(facts, pending=())
    elif problem == 'zero':
        facts = replace(facts, pending=(replace(pending, signed_quantity=quantity('0')),))
    elif problem == 'duplicate':
        reservations = (r, r)
    elif problem == 'negative':
        reservations = (replace(r, held=D('-1')),)
    else:
        reservations = (replace(r, reservation=replace(r.reservation, owner_strategy_id='other')),)
    with pytest.raises(ProposalRejected):
        compile_book(case, book=replace(case.book, reservations=reservations), facts=facts)


def test_conflicting_pending_quantity_requires_reconciliation(target_case):
    case = target_case
    book = replace(case.book, reservations=(reservation(case),))
    pending = PendingQuantity(intent_id='older-intent', signed_quantity=quantity('0.020'),
                              reservation_ids=('reservation',))
    with pytest.raises(ProposalRejected, match='PENDING_RECONCILIATION_REQUIRED'):
        compile_book(case, book=book, facts=replace(case.target, pending=(pending,)))


@pytest.mark.parametrize('problem', ['book', 'position_owner', 'order_owner', 'side', 'duplicate_order'])
def test_incomplete_or_unowned_book_fails_closed(target_case, problem):
    case = target_case
    book, facts = case.book, case.target
    if problem == 'book':
        book = replace(book, coverage=replace(book.coverage, pagination_complete=False))
    elif problem == 'position_owner':
        book = replace(book, positions=(position(book.scope, '0.004', owner='external'),))
    else:
        projection = order(book.scope, '0.003', owner='external' if problem == 'order_owner' else 'demo-ai-strategy')
        book = replace(book, active_orders=(projection, projection) if problem == 'duplicate_order' else (projection,))
        if problem != 'side':
            facts = replace(facts, order_sides=(WorkingOrder(logical_client_order_id='order', side='BUY'),))
    with pytest.raises(ProposalRejected):
        compile_book(case, book=book, facts=facts)


def test_superseding_intent_preserves_filled_position(target_case):
    case = target_case
    previous = case.previous()
    case.book = replace(case.book, positions=(position(case.book.scope, '0.004'),))
    new = case.bind(replace(case.proposal, proposal_id='replacement', target=replace(case.proposal.target, value='0.002')))
    assert new.supersedes_intent_id == previous.intent_id
    plan = compile_book(case, intent=new)
    assert plan.remaining.value == D('-0.002') and plan.actual.value == D('0.004')
    assert case.book.positions[0].actual_quantity.value == D('0.004')


def test_identity_is_bound_by_server_and_evidence_time_not_extended(target_case):
    intent = target_case.bind()
    assert intent.venue_account_id == target_case.book.scope.venue_account_id
    assert intent.owner_strategy_id == 'demo-ai-strategy'
    assert intent.strategy_revision == intent.risk_policy_revision == intent.ownership_epoch == 1
    assert intent.as_of == BEFORE and intent.created_at == NOW and intent.valid_until == END
    payload = ContractCodec().encode(target_case.proposal)
    payload['venue_account_id'] = 'attacker'
    with pytest.raises(ValidationError):
        ContractCodec().decode('decision-proposal', payload)


@pytest.mark.parametrize('field', ['strategy_revision', 'policy_revision', 'ownership_epoch', 'identity_id'])
def test_ingress_rejects_stale_context_without_writes(target_case, field):
    ledger, service = ingress_case(target_case)
    context = replace(target_case.context, **{field: 'other-user' if field == 'identity_id' else 2})
    before = ledger.records.copy()
    receipt = service.accept(target_case.proposal, context)
    assert receipt.status == t.IntentState.REJECTED
    assert ledger.records == before and ledger.commits == 0


def test_disabled_or_missing_deployment_fence_rejects(target_case):
    ledger, service = ingress_case(target_case)
    key = ('audit_events', revision_key(target_case.context))
    sequence, fence = ledger.records[key]
    ledger.records[key] = (sequence, replace(fence, enabled=False))
    assert service.accept(target_case.proposal, target_case.context).reason == 'DEPLOYMENT_FENCE_REQUIRED'
    del ledger.records[key]
    assert service.accept(target_case.proposal, target_case.context).reason == 'DEPLOYMENT_FENCE_REQUIRED'
    assert not records(ledger)


def test_duplicate_returns_existing_result_without_new_intent(target_case):
    ledger, service = ingress_case(target_case)
    first = service.accept(target_case.proposal, target_case.context)
    before = ledger.records.copy()
    second = service.accept(target_case.proposal, target_case.context)
    assert first.status == t.IntentState.VALIDATING
    assert second.receipt_id == first.receipt_id and second.intent_id == first.intent_id
    assert second.existing_result_ref == first.receipt_id and ledger.records == before


def test_concurrent_duplicate_intake_orchestration(target_case):
    """Synthetic lock orchestration only; not PostgreSQL race evidence."""
    ledger, service = ingress_case(target_case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.accept, target_case.proposal, target_case.context) for _ in range(2)]
        receipts = [future.result(timeout=10) for future in futures]
    assert all(r.status == t.IntentState.VALIDATING for r in receipts)
    assert len({r.intent_id for r in receipts}) == 1 and len(records(ledger)) == 1
    assert sum(r.existing_result_ref is None for r in receipts) == 1


def test_intake_rejects_mismatched_compiler_inputs(target_case):
    target_case.target = replace(target_case.target, allow_round_down=True)
    ledger, service = ingress_case(target_case)
    receipt = service.accept(target_case.proposal, target_case.context)
    assert receipt.reason == 'COMPILER_INPUT_MISMATCH' and not records(ledger)


def test_conflicting_proposal_id_rejected(target_case):
    ledger, service = ingress_case(target_case)
    service.accept(target_case.proposal, target_case.context)
    conflicting = replace(target_case.proposal, target=replace(target_case.proposal.target, value='0.020'))
    receipt = service.accept(conflicting, target_case.context)
    assert receipt.reason == 'PROPOSAL_ID_CONFLICT' and len(records(ledger)) == 1


@pytest.mark.parametrize('failure', ['fail_commit', 'fail_cas', 'fail_append'])
def test_failed_persistence_cannot_return_acceptance(target_case, failure):
    ledger, service = ingress_case(target_case)
    setattr(ledger, failure, True)
    before = ledger.records.copy()
    with pytest.raises((SequenceConflict, OSError)):
        service.accept(target_case.proposal, target_case.context)
    assert ledger.records == before and ledger.commits == 0


def test_intake_record_roundtrips_through_real_storage_codec(target_case):
    ledger, service = ingress_case(target_case)
    receipt = service.accept(target_case.proposal, target_case.context)
    assert receipt.status == t.IntentState.VALIDATING
    record = records(ledger)[0]
    restored = decode(AcceptedProposal, encode(record))
    assert restored == record and accepted_intent(restored).intent_id == receipt.intent_id


def test_compile_locked_reloads_current_book_and_strategy_fence(target_case):
    case = target_case
    ledger, _ = ingress_case(case)
    intent = case.bind()
    with pytest.raises(ProposalRejected, match='TRANSACTION_REQUIRED'):
        compile_locked(ledger, intent, lambda *_: case.target)
    ledger.book_value = replace(case.book, sequence=2, basis_fingerprint='c' * 64,
                                positions=(position(case.book.scope, '0.004'),))
    with ledger.transaction(case.book.scope) as tx:
        with pytest.raises(ProposalRejected, match='STALE_BOOK'):
            compile_locked(tx, intent, lambda *_: case.target)
    # Rebind against the changed basis before a fresh locked calculation.
    case.book = ledger.book_value
    intent = case.bind()
    with ledger.transaction(case.book.scope) as tx:
        plan = compile_locked(tx, intent, lambda *_: case.target)
        assert plan.remaining.value == D('0.006')
    key = ('audit_events', revision_key(case.context))
    seq, fence = ledger.records[key]
    ledger.records[key] = (seq, replace(fence, strategy_revision=2))
    with ledger.transaction(case.book.scope) as tx:
        with pytest.raises(ProposalRejected, match='STALE_REVISION'):
            compile_locked(tx, intent, lambda *_: case.target)


def test_compile_locked_requires_context_and_current_owner(target_case):
    case = target_case
    ledger, _ = ingress_case(case)
    intent = case.bind()
    with ledger.transaction(case.book.scope) as tx:
        with pytest.raises(ProposalRejected, match='INTENT_CONTEXT_REQUIRED'):
            compile_locked(tx, intent, lambda *_: replace(case.target, context=None))
    ledger.owner = ('demo-ai-strategy', 2, 1)
    with ledger.transaction(case.book.scope) as tx:
        with pytest.raises(ProposalRejected, match='STALE_OWNER'):
            compile_locked(tx, intent, lambda *_: case.target)


@pytest.mark.parametrize('problem', ['expired', 'monotonic', 'unhealthy', 'future', 'missing', 'stale_hash', 'revoked'])
def test_binding_rejects_unusable_authority_or_evidence(target_case, problem):
    case = target_case
    if problem == 'expired':
        case.proposal = replace(case.proposal, valid_until=NOW)
    elif problem == 'monotonic':
        case.binding = replace(case.binding, deadline_monotonic_ns=100)
    elif problem == 'unhealthy':
        case.binding = replace(case.binding, clock=replace(case.binding.clock, clock_health='UNKNOWN'))
    elif problem == 'future':
        from trading_system.market.snapshots import snapshot_content_hash
        snapshot = replace(case.binding.snapshot, revision_visible_at=END)
        snapshot = replace(snapshot, ref=replace(snapshot.ref, hash=snapshot_content_hash(snapshot)))
        case.context = replace(case.context, snapshot_ref=snapshot.ref)
        case.binding = replace(case.binding, snapshot=snapshot)
    elif problem == 'missing':
        case.proposal = replace(case.proposal, evidence_refs=('unknown',))
    elif problem == 'stale_hash':
        case.binding = replace(case.binding, snapshot=replace(case.binding.snapshot, published_at=NOW))
    else:
        binding = case.binding.authorization
        case.binding = replace(case.binding, authorization=replace(binding,
            authorization=replace(binding.authorization, revoked_at=NOW)))
    with pytest.raises((ProposalRejected, ValidationError)):
        case.bind()


@pytest.mark.parametrize('problem', ['stale_book', 'expired', 'session', 'wrong_scope'])
def test_target_revalidation_fails_closed(target_case, problem):
    case = target_case
    intent, book, facts = case.bind(), case.book, case.target
    if problem == 'stale_book':
        book = replace(book, basis_fingerprint='c' * 64)
    elif problem == 'expired':
        facts = replace(facts, clock=replace(facts.clock, utc_now=END))
    elif problem == 'session':
        facts = replace(facts, session=replace(facts.session, state='CLOSED'))
    else:
        intent = replace(intent, venue_account_id='other')
    with pytest.raises(ProposalRejected):
        compile_book(case, book=book, facts=facts, intent=intent)


def test_live_scope_has_no_ingress(target_case):
    from trading_system.intents.ingress import ProposalIngress
    with pytest.raises(ProposalRejected, match='LIVE_DISABLED'):
        ProposalIngress(None, replace(target_case.book.scope, environment=t.Environment.LIVE), None)


def test_cash_short_and_perpetual_quote_conversion_are_rejected(target_case):
    proposal = replace(target_case.proposal, target=replace(target_case.proposal.target, side='SHORT'),
        exit_plan=replace(target_case.proposal.exit_plan, stop_price='51000'))
    with pytest.raises(ProposalRejected, match='CASH_SHORT'):
        target_case.bind(proposal)
    case = make_case(t.Product.USD_M_PERP)
    with pytest.raises(ProposalRejected, match='CONTRACT_MULTIPLIER_REQUIRED'):
        case.bind(replace(case.proposal, target=replace(case.proposal.target, sizing='QUOTE_NOTIONAL', value='500')))
