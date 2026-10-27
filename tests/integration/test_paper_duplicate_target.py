"""Real PostgreSQL acceptance: in-flight target quantity must not be bought twice."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4
from decimal import Decimal
from hashlib import sha256
import pytest

from integration.support_ip_07 import ReservationCase, ip07_database, NOW
from trading_system.risk.reservations import ReservationService
from trading_system.contracts import types as t
from trading_system.execution.commands import CommandRepository
from trading_system.execution.evidence import EvidenceIngestor
from trading_system.execution.fills import FillLedger
from trading_system.risk.target_guard import TargetReportService
from trading_system.storage.models import encode
from trading_system.intents.semantic_validation import ProposalRejected
from unit.support_ip_06 import position, money


def test_two_new_intents_with_same_target_cannot_reserve_twice(ip07_database):
    """Two distinct IDs targeting one owned leg race under the actual group mutex."""
    case = ReservationCase(ip07_database, available='1000')
    first_intent, first_command = case.case.intent, case.command
    suffix = uuid4().hex
    second_intent = replace(first_intent, intent_id='duplicate-' + suffix)
    second_command = replace(first_command, command_id='command-' + suffix,
                             intent_id=second_intent.intent_id, attempt_id='attempt-' + suffix,
                             logical_client_order_id='logical-' + suffix,
                             venue_client_order_id='client-' + suffix)
    commands = {first_intent.intent_id: first_command, second_intent.intent_id: second_command}

    def inputs(tx, intent, book):
        values = case.inputs(tx, intent, book)
        return replace(values, command=commands[intent.intent_id],
                       risk=replace(values.risk, intent_id=intent.intent_id))

    service = ReservationService(case.ledger.uow, case.scope, inputs, clock=lambda: NOW)
    barrier = Barrier(2)

    def run(intent):
        barrier.wait(timeout=10)
        return service.reserve(intent, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (first_intent, second_intent)))

    assert sum(result.accepted for result in results) == 1
    assert [case.count(table) for table in ('intents', 'reservations', 'commands', 'outbox')] == [1, 1, 1, 1]


@pytest.mark.parametrize('state', ['DURABLE_READY', 'MAY_HAVE_SENT', 'UNKNOWN', 'VENUE_CONFIRMED'])
def test_prior_command_cannot_be_ignored_even_without_snapshot_order(ip07_database, state):
    case = ReservationCase(ip07_database, available='1000')
    result = case.reserve()
    assert result.accepted
    if state == 'MAY_HAVE_SENT':
        with case.ledger.uow.transaction(case.scope) as tx:
            CommandRepository(tx).mark_may_have_sent(tx, case.command.command_id, 'DURABLE_READY')
    elif state != 'DURABLE_READY':
        case.command_state(state)
    before = [case.count(table) for table in ('intents', 'reservations', 'commands', 'outbox')]
    case.next_intent('0.7')
    result = case.reserve()
    assert not result.accepted
    assert [case.count(table) for table in ('intents', 'reservations', 'commands', 'outbox')] == before


def test_reservation_terminal_boolean_cannot_replace_target_history(ip07_database):
    case = ReservationCase(ip07_database, available='1000')
    initial = case.reserve()
    case.command_state('VENUE_CONFIRMED')
    reservation = initial.reservations[0].reservation
    evidence = case.evidence(reservation, reflected='0', terminal=True, available='1000')
    assert case.service.reconcile(evidence).held == 0
    case.next_intent('0.7')
    result = case.reserve(case.case.book.sequence)
    assert not result.accepted and result.rejection_codes == ('TARGET_RECONCILIATION_REQUIRED',)


def test_authoritative_unsent_abort_removes_quantity_obligation(ip07_database):
    case = ReservationCase(ip07_database, available='1000')
    assert case.reserve().accepted
    case.command_state('ABORTED_UNSENT')
    case.next_intent('0.7')
    result = case.reserve()
    assert result.accepted, result.rejection_codes
    # Cash is independently conservative until reservation reconciliation.
    assert result.reservations[0].held == Decimal('70')


@pytest.mark.parametrize('state', ['DURABLE_READY', 'VENUE_CONFIRMED', 'UNKNOWN'])
def test_zero_asset_reduction_still_occupies_target_quantity(ip07_database, state):
    case = ReservationCase(ip07_database, sell=True, product=t.Product.USD_M_PERP)
    assert case.reserve().accepted
    if state != 'DURABLE_READY':
        case.command_state(state)
    before = case.count('commands')
    case.next_intent('2')
    case.case.intent = replace(case.case.intent, target_quantity='0')
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload,
        quantity='2', side='SELL'))
    case.rehash_command()
    assert not case.reserve().accepted
    assert case.count('commands') == before and case.count('reservations') == 0


def test_larger_target_compiles_only_uncommitted_remaining_delta(ip07_database):
    case = ReservationCase(ip07_database, available='1000')
    assert case.reserve().accepted
    case.next_intent('0.9')
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity='0.2'))
    case.rehash_command()
    result = case.reserve()
    assert result.accepted, result.rejection_codes
    assert case.count('commands') == 2
    # Risk still conservatively reserves the complete desired delta, in
    # addition to the previous hold. This never understates the pool exposure.
    assert result.reservations[0].held == Decimal('90')


def test_exact_replay_returns_original_durable_basis_after_book_advances(ip07_database):
    case = ReservationCase(ip07_database)
    original = case.reserve()
    assert original.accepted
    case.save_book(replace(case.case.book, sequence=2))
    assert case.reserve(2) == original
    assert case.count('commands') == 1


def test_old_full_delta_command_does_not_match_larger_target(ip07_database):
    case = ReservationCase(ip07_database, available='1000')
    assert case.reserve().accepted
    case.next_intent('0.9')
    with pytest.raises(ValueError, match='evaluated delta'):
        case.reserve()
    assert case.count('commands') == case.count('intents') == 1


@pytest.mark.parametrize('fault', ['missing', 'rule', 'fence', 'book', 'deadline', 'snapshot'])
def test_target_revalidation_fails_closed_before_any_bundle(ip07_database, fault):
    case = ReservationCase(ip07_database)
    original = case.service.load_inputs
    def load(*args):
        inputs = original(*args)
        if fault == 'missing':
            return replace(inputs, target=None)
        if fault == 'rule':
            return replace(inputs, target=replace(inputs.target,
                expected_instrument=replace(inputs.target.instrument, rule_revision=2)))
        if fault == 'fence':
            return replace(inputs, target=replace(inputs.target,
                context=replace(inputs.target.context, strategy_revision=2)))
        if fault == 'deadline':
            return replace(inputs, target=replace(inputs.target, deadline_monotonic_ns=0))
        if fault == 'snapshot':
            return replace(inputs, target=replace(inputs.target, context=replace(inputs.target.context,
                snapshot_ref=replace(inputs.market.ref, id='other-snapshot'))))
        return inputs
    case.service.load_inputs = load
    if fault == 'book':
        case.save_book(replace(case.case.book, sequence=2))
    result = case.reserve()
    assert not result.accepted
    assert [case.count(table) for table in ('intents', 'commands', 'reservations', 'outbox')] == [0]*4


def filled_history(case, *, amount='0.7', terminal=True):
    """Synthetic archived venue event, ingested fill and authoritative report."""
    command = case.command
    identity = t.OrderIdentity(scope=case.scope, logical_client_order_id=command.logical_client_order_id,
        native_client_order_id='native-' + command.logical_client_order_id,
        venue_client_order_id=command.venue_client_order_id, venue_order_id='venue-' + uuid4().hex)
    raw = ('synthetic-fill-' + uuid4().hex).encode()
    ref = t.EvidenceRef(id=uuid4().hex, hash=sha256(raw).hexdigest(),
        environment=case.scope.environment, source='synthetic-venue-fill', visible_at=NOW)
    event = t.VenueEvent(event_id=uuid4().hex, source=ref.source, source_event_id=uuid4().hex,
        source_sequence='1', account_id=case.scope.venue_account_id,
        instrument_uid=case.scope.instrument_uid, order_refs=(identity,), occurred_at=NOW,
        received_at=NOW, processed_at=NOW, payload_hash=ref.hash, raw_payload_ref=ref)
    assert EvidenceIngestor(case.ledger.uow, case.scope, read_raw=lambda _: raw).ingest(event).accepted
    quantity = t.Quantity(value=Decimal(amount), unit=case.case.intent.quantity_unit)
    fill = t.Fill(fill_id=uuid4().hex, venue='BINANCE', scope=case.scope,
        native_trade_id=uuid4().hex, order=identity, quantity=quantity,
        price=t.Price(value=Decimal('100'), currency='USDT'), fees=(money('0'),),
        occurred_at=NOW, correction_of=None, evidence=ref)
    assert FillLedger(case.ledger.uow, case.scope, trade_id_scope='INSTRUMENT').record(fill).recorded
    order = t.OrderProjection(order=t.OwnedOrderRef(identity=identity,
        owner_strategy_id=case.owner, ownership_epoch=1),
        lifecycle_state=t.OrderLifecycle.FILLED if terminal else t.OrderLifecycle.PARTIALLY_FILLED,
        pending_operation='NONE' if terminal else 'CANCEL',
        command_certainty='CONFIRMED' if terminal else 'UNKNOWN', outcome_provenance='VENUE_QUERY',
        filled_quantity=quantity, leaves_quantity=replace(quantity,
            value=Decimal('0') if terminal else Decimal('0.7') - quantity.value),
        lifecycle_revision=2, sequence=1)
    book = replace(case.case.book, sequence=2, evidence=(ref,),
        positions=(position(case.scope, amount, case.case.intent.quantity_unit, case.owner),),
        active_orders=() if terminal else (order,))
    case.save_book(book)
    report = t.VenueReport(coverage=book.coverage, orders=(order,), positions=book.positions,
        balances=book.balances, fills=(fill,), evidence=book.evidence, next_cursor=None, as_of=NOW)
    raw_report = encode(report).encode()
    archive = t.EvidenceRef(id='report-' + uuid4().hex, hash=sha256(raw_report).hexdigest(),
        environment=case.scope.environment, source='synthetic-complete-query', visible_at=NOW)
    service = TargetReportService(case.ledger.uow, case.scope,
        read_archive=lambda _: raw_report, clock=lambda: NOW)
    return service, archive, report


def test_terminal_fills_and_position_reconciliation_allow_next_delta(ip07_database):
    case = ReservationCase(ip07_database, available='1000')
    assert case.reserve().accepted
    case.command_state('VENUE_CONFIRMED')
    service, ref, report = filled_history(case)
    recorded = service.record(ref)
    assert recorded == service.record(ref)
    case.next_intent('0.9')
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity='0.2'))
    case.rehash_command()
    result = case.reserve(2)
    assert result.accepted, result.rejection_codes
    assert result.reservations[0].held == Decimal('20')
    # Replay remains a receipt, then a distinct same target cannot buy again.
    assert case.reserve(2) == result
    case.next_intent('0.9')
    assert not case.reserve(2).accepted


def test_forty_percent_fill_cancel_unknown_keeps_remainder_and_denies_resend(ip07_database):
    case = ReservationCase(ip07_database, available='1000')
    initial = case.reserve()
    case.command_state('VENUE_CONFIRMED')
    service, ref, report = filled_history(case, amount='0.28', terminal=False)
    with pytest.raises(ProposalRejected):
        service.record(ref)
    case.next_intent('0.7')
    assert not case.reserve(2).accepted
    assert case.projection(initial.reservations[0].reservation.reservation_id).held == Decimal('70')


@pytest.mark.parametrize('fault', ['hash', 'position', 'fill', 'stale'])
def test_target_reconciliation_requires_exact_archive_fills_position_and_book(ip07_database, fault):
    case = ReservationCase(ip07_database, available='1000')
    assert case.reserve().accepted
    case.command_state('VENUE_CONFIRMED')
    service, ref, report = filled_history(case)
    if fault == 'hash':
        with pytest.raises(ProposalRejected, match='HASH'):
            service.record(replace(ref, hash='a'*64))
    elif fault in ('position', 'fill'):
        if fault == 'position':
            report = replace(report, positions=(replace(report.positions[0],
                actual_quantity=replace(report.positions[0].actual_quantity, value=Decimal('0.6'))),))
            # A manual position change cannot be hidden behind a matching
            # current report: durable fills still prove .7, not .6.
            case.save_book(replace(case.case.book, sequence=3, positions=report.positions))
        else:
            report = replace(report, fills=())
        raw = encode(report).encode()
        service.read_archive = lambda _: raw
        with pytest.raises(ProposalRejected):
            service.record(replace(ref, hash=sha256(raw).hexdigest()))
    else:
        service.record(ref)
        case.save_book(replace(case.case.book, sequence=3))
    case.next_intent('0.9')
    assert not case.reserve(case.case.book.sequence).accepted


def intake(case):
    """Real ProposalIngress transaction, reused by the bounded domain bridge."""
    from unit.support_ip_09 import make_case
    from trading_system.intents.ingress import ProposalIngress, IngressInputs, AcceptedProposal, accepted_intent
    from trading_system.storage.models import decode, scope_id
    template = make_case()
    context = case.target_context(case.case.intent)
    grant = replace(template.binding.authorization.authorization,
        authorization_id=context.authorization_id, owner_strategy_id=case.owner,
        deployment_id=context.deployment_id, scopes=(case.scope,),
        valid_from=case.case.intent.as_of, expires_at=case.case.intent.valid_until)
    authorization = replace(template.binding.authorization, authorization=grant,
        identity_id=context.identity_id, strategy_revision=context.strategy_revision,
        ownership_epoch=context.ownership_epoch, evidence=case.case.book.evidence)
    target = case.inputs(None, case.case.intent, case.case.book).target
    binding = replace(template.binding, authorization=authorization,
        instrument_ref=case.scope.instrument_uid, instrument=target.instrument,
        collateral_group_id=case.group, snapshot=case.case.market,
        evidence=(case.case.market.ref,), clock=target.clock,
        deadline_monotonic_ns=target.deadline_monotonic_ns,
        maximum_valid_until=case.case.intent.valid_until, max_planned_loss=money('100'))
    proposal = replace(template.proposal, instrument_ref=case.scope.instrument_uid,
        evidence_refs=(case.case.market.ref.id,), valid_until=case.case.intent.valid_until,
        target=replace(template.proposal.target, value='0.7'),
        entry=replace(template.proposal.entry, order_preference='MARKET', limit_price=None,
            entry_deadline=case.case.intent.valid_until),
        exit_plan=replace(template.proposal.exit_plan, stop_price='90',
            review_due_at=case.case.intent.valid_until))
    from trading_system.storage.uow import UnitOfWork
    def connect_intake():
        connection = case.ledger.connect_admin()
        connection.execute('SET ROLE ledger_intake')
        connection.commit()
        return connection
    ingress = ProposalIngress(UnitOfWork(connect_intake), case.scope,
        lambda *args: IngressInputs(binding=binding, target=target))
    receipt = ingress.accept(proposal, context)
    assert receipt.status == t.IntentState.VALIDATING, receipt.reason
    with case.ledger.uow.transaction(case.scope) as tx:
        row = tx.execute("SELECT payload FROM ledger.proposals WHERE scope_id=%s "
            "AND payload->'receipt'->>'intent_id'=%s", (scope_id(case.scope), receipt.intent_id)).fetchone()
    case.case.intent = accepted_intent(decode(AcceptedProposal, row[0]))
    case.case.inputs = replace(case.case.inputs, intent_id=receipt.intent_id)
    case.command = replace(case.command, intent_id=receipt.intent_id)
    return receipt


def test_durable_proposal_bridge_revalidates_and_commits_one_bundle(ip07_database):
    case = ReservationCase(ip07_database)
    receipt = intake(case)
    assert case.count('intents') == case.count('commands') == 0
    result = case.service.reserve_proposal(receipt.intent_id)
    assert result.accepted, result.rejection_codes
    assert case.service.reserve_proposal(receipt.intent_id) == result
    assert case.count('intents') == case.count('commands') == 1


def test_intake_role_cannot_write_execution_or_fund_facts(ip07_database):
    case = ReservationCase(ip07_database)
    with case.ledger.connect_admin() as conn:
        for table in ('intents', 'reservations', 'commands', 'fills', 'positions', 'outbox',
                      'authorizations', 'policies', 'risk_locks'):
            assert not conn.execute("SELECT has_table_privilege('ledger_intake',%s,'INSERT')",
                ('ledger.' + table,)).fetchone()[0]
        assert not conn.execute("SELECT has_table_privilege('ledger_owner','ledger.proposals','INSERT')").fetchone()[0]


def test_same_account_other_instrument_shares_cash_without_sharing_target_quantity(ip07_database):
    first = ReservationCase(ip07_database)
    second = ReservationCase(ip07_database, group=first.group, account=first.scope.venue_account_id,
        instrument='synthetic-second-instrument')
    assert first.reserve().accepted
    second.next_intent('0.2')
    result = second.reserve()
    assert result.accepted, result.rejection_codes
    assert result.reservations[0].held == Decimal('20')
    second.next_intent('0.4')
    second.command = replace(second.command,
        canonical_payload=replace(second.command.canonical_payload, quantity='0.2'))
    second.rehash_command()
    denied = second.reserve()
    assert not denied.accepted and denied.rejection_codes == ('INSUFFICIENT_SPENDABLE:USDT',)


def test_old_proposal_cannot_replenish_after_manual_position_change(ip07_database):
    case = ReservationCase(ip07_database)
    receipt = intake(case)
    case.save_book(replace(case.case.book, sequence=2,
        positions=(position(case.scope, '0.4', case.case.intent.quantity_unit, case.owner),)))
    result = case.service.reserve_proposal(receipt.intent_id)
    assert not result.accepted and result.rejection_codes == ('REPLAN_REQUIRED:STALE_BOOK',)
    assert case.count('intents') == case.count('commands') == 0
