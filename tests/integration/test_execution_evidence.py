"""Offline IP-12 probes, never registered T27/T30/T31 or Q qualification.

Pure projection probes run without PostgreSQL. Persistence, concurrency and
rollback probes require the explicitly provisioned isolated Linux fixture.
No mock database, platform request or native trading engine is used.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal, localcontext
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest

from integration.support_ip_12 import (
    evidence_case, ip12_database, initial_projection, domain_event, failing_uow,
    STAMP,
)
from trading_system.contracts import types as t
from trading_system.execution.evidence import EvidenceIngestor
from trading_system.execution.fills import FillLedger, FillNotice
from trading_system.execution.projections import OrderProjector, subtract_exact
from trading_system.storage.models import MissingEvidence, ScopeMismatch, SequenceConflict, scope_id


def test_duplicate_fill_scoped(evidence_case):
    s = evidence_case
    fill = s.fill(native_id='same-trade')
    first = s.fills.record(fill)
    duplicate = s.fills.record(replace(fill, fill_id=uuid4().hex))
    assert first.recorded and first.event_ref is not None
    assert not duplicate.recorded and duplicate.duplicate_of == fill.fill_id
    assert s.count('fills') == s.count('domain_events') == s.count('outbox') == 1
    for changes in (
        {'venue_account_id': 'other-' + uuid4().hex},
        {'product': t.Product.USD_M_PERP},
        {'instrument_uid': 'other-instrument'},
        {'environment': t.Environment.TESTNET},
    ):
        other = s.scoped(**changes)
        assert other.fills.record(other.fill(native_id='same-trade')).recorded
        assert other.count('fills') == 1
    assert s.count('fills') == 1


def test_account_product_native_scope_does_not_ignore_collision(evidence_case):
    s = evidence_case
    s.fills = FillLedger(s.uow, s.scope, trade_id_scope='ACCOUNT_PRODUCT')
    assert s.fills.record(s.fill(native_id='account-trade')).recorded
    other = s.scoped(instrument_uid='different-instrument')
    with pytest.raises(ValueError, match='identity conflict'):
        other.fills.record(other.fill(native_id='account-trade'))
    assert other.count('fills') == 0


def test_duplicate_native_id_with_different_facts_fails_closed(evidence_case):
    s = evidence_case
    fill = s.fill(native_id='fixed')
    s.fills.record(fill)
    with pytest.raises(ValueError, match='explicit correction'):
        s.fills.record(s.fill('0.3', native_id='fixed'))
    assert s.count('fills') == s.count('domain_events') == 1


def test_concurrent_duplicate_fill_is_recorded_once(evidence_case):
    s = evidence_case
    fill = s.fill()
    barrier = Barrier(2)

    def record():
        barrier.wait(timeout=10)
        return s.fills.record(fill)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(record) for _ in range(2)]
        receipts = [future.result(timeout=30) for future in futures]
    assert sum(receipt.recorded for receipt in receipts) == 1
    assert s.count('fills') == s.count('outbox') == s.count('audit_events') == 1


def test_late_fill_after_cancel(evidence_case):
    s = evidence_case
    state = s.cancel_then_late_fill(quantity='0.2')
    assert state.actual_quantity == '0.2'
    assert state.protection_recheck_required is True
    assert s.projection.lifecycle_state == t.OrderLifecycle.CANCELED
    assert s.projection.leaves_quantity.value == 0
    assert s.projection.pending_operation == 'CANCEL'
    assert s.projection.command_certainty == 'UNKNOWN'
    # Snapshot + fill outbox records are durable notifications, not protection.
    assert s.count('outbox') == 2
    assert s.count('commands') == s.count('reservations') == 0
    with s.ledger.connect_admin() as conn:
        row = conn.execute('SELECT payload FROM ledger.fills WHERE scope_id=%s',
                           (scope_id(s.scope),)).fetchone()
    from trading_system.storage.models import decode
    assert not s.fills.record(decode(t.Fill, row[0])).recorded
    assert s.count('fills') == 1


@pytest.mark.parametrize('body', [b'{"accepted":', b'\xff\x00broken'])
def test_parse_error_is_unknown(evidence_case, body):
    s = evidence_case
    raw = s.raw(body)

    def broken_decoder(payload):
        assert payload == body
        raise ValueError('private response contents must not enter reason text')

    receipt = s.ingestor.ingest_raw(raw, decoder=broken_decoder)
    assert not receipt.accepted
    assert receipt.reason == 'UNKNOWN:PARSE_OR_IDENTITY_ERROR'
    assert receipt.raw_ref.hash == sha256(body).hexdigest()
    assert s.read_raw(receipt.raw_ref) == body
    record, = s.evidence_records()
    assert record.event is None and record.status == 'UNKNOWN'
    assert record.raw == raw
    assert record.aggregate_sequence == 1
    assert s.ingestor.ingest_raw(raw, decoder=broken_decoder) == receipt
    assert s.count('venue_events') == 1
    assert s.count('commands') == s.count('fills') == s.count('outbox') == 0


def test_bad_hash_cannot_become_accepted_evidence(evidence_case):
    s = evidence_case
    raw = s.raw()
    forged = replace(raw, payload_hash='0' * 64)
    with pytest.raises(ValueError, match='hash mismatch'):
        s.ingestor.ingest_raw(forged, decoder=s.venue_event)
    assert s.count('venue_events') == 0


def test_wrong_identity_is_archived_as_unknown(evidence_case):
    s = evidence_case
    event = replace(s.venue_event(s.raw()), account_id='wrong-account')
    receipt = s.ingestor.ingest(event)
    assert not receipt.accepted and 'UNKNOWN' in receipt.reason
    assert s.evidence_records()[0].status == 'UNKNOWN'


def test_invalid_processing_time_is_unknown(evidence_case):
    s = evidence_case
    event = replace(s.venue_event(s.raw()), processed_at='2026-10-19T00:00:00Z')
    assert not s.ingestor.ingest(event).accepted
    assert s.evidence_records()[0].raw.received_at == STAMP


def test_ingress_keeps_original_receipt_and_rejects_conflicting_decode(evidence_case):
    s = evidence_case
    event = s.venue_event(s.raw())
    receipt = s.ingestor.ingest(event)
    later = replace(event, received_at='2026-10-20T06:40:00Z',
                    processed_at='2026-10-20T06:41:00Z')
    assert s.ingestor.ingest(later) == receipt
    assert s.evidence_records()[0].event == event
    alias = s.archive / 'same-content-alias.raw'
    alias.write_bytes(s.read_raw(event.raw_payload_ref))
    repeated = replace(later, raw_payload_ref=replace(event.raw_payload_ref,
                                                     id=str(alias.resolve())))
    assert s.ingestor.ingest(repeated).raw_ref == receipt.raw_ref
    with pytest.raises(ValueError, match='conflicting decoded facts'):
        s.ingestor.ingest(replace(event, source_sequence='different'))
    assert s.count('venue_events') == 1


def test_account_sequence_ignores_source_sequence_and_occurrence_order(evidence_case):
    s = evidence_case
    first = replace(s.venue_event(s.raw(b'first')), source_sequence='100',
                    occurred_at='2026-10-20T06:38:00Z')
    assert s.ingestor.ingest(first).accepted
    other = s.scoped(instrument_uid='second-instrument')
    second = replace(other.venue_event(other.raw(b'second')), source_sequence='2',
                     occurred_at='2026-10-20T06:37:00Z')
    assert other.ingestor.ingest(second).accepted
    assert s.evidence_records()[0].aggregate_sequence == 1
    assert other.evidence_records()[0].aggregate_sequence == 2
    assert other.evidence_records()[0].event == second


def test_fill_and_event_preserve_three_timestamps(evidence_case):
    s = evidence_case
    fill = s.fill()
    receipt = s.fills.record(fill)
    with s.ledger.connect_admin() as conn:
        row = conn.execute('SELECT occurred_at,received_at,processed_at FROM ledger.domain_events '
                           'WHERE event_id=%s', (receipt.event_ref.id,)).fetchone()
    from trading_system.contracts.canonical import utc_timestamp
    assert row[0] == utc_timestamp(fill.occurred_at)
    assert row[1] == utc_timestamp(STAMP)
    assert row[2] >= row[1]
    assert s.notice(fill).aggregate_sequence > s.evidence_records()[0].aggregate_sequence


def test_local_terminal_does_not_resolve():
    previous = initial_projection()
    local = replace(previous, lifecycle_state=t.OrderLifecycle.CANCELED,
                    outcome_provenance='LOCAL_POLICY', pending_operation='NONE',
                    command_certainty='NOT_SENT', lifecycle_revision=1,
                    leaves_quantity=t.Quantity(value=Decimal(0), unit='BTC'))
    update = OrderProjector().apply(domain_event(previous, local), previous)
    assert update.projection.lifecycle_state == t.OrderLifecycle.CANCELED
    assert update.projection.outcome_provenance == 'LOCAL_POLICY'
    assert update.projection.command_certainty == 'UNKNOWN'
    assert update.projection.pending_operation == 'CANCEL'
    assert update.projection.leaves_quantity.value == 1
    assert update.events == ()


def test_local_cannot_relabel_venue_terminal():
    previous = replace(initial_projection(), lifecycle_state=t.OrderLifecycle.CANCELED,
                       outcome_provenance='VENUE_EVENT', lifecycle_revision=5,
                       leaves_quantity=t.Quantity(value=Decimal(0), unit='BTC'))
    local = replace(previous, outcome_provenance='LOCAL_POLICY', lifecycle_revision=99)
    actual = OrderProjector().apply(domain_event(previous, local), previous).projection
    assert actual.outcome_provenance == 'VENUE_EVENT'
    assert actual.lifecycle_revision == 5


def test_parse_failure_and_local_terminal_preserve_durable_unknown(evidence_case):
    s = evidence_case
    s.seed_unknown_command()
    outbox_count = s.count('outbox')
    raw = s.raw(b'accepted-but-malformed')

    def broken_decoder(payload):
        raise ValueError('malformed synthetic accepted response')

    assert not s.ingestor.ingest_raw(raw, decoder=broken_decoder).accepted
    s.assert_unknown_still_held()
    assert s.count('outbox') == outbox_count
    s.snapshot(provenance='LOCAL_POLICY')
    s.assert_unknown_still_held()
    assert s.projection.command_certainty == 'UNKNOWN'
    assert s.projection.leaves_quantity.value == 1
    assert s.count('outbox') == outbox_count + 1  # projection event only


@pytest.mark.parametrize('revision', [0, 2, -1])
def test_projection_rejects_duplicate_or_gapped_sequence(revision):
    previous = initial_projection()
    with pytest.raises(SequenceConflict):
        OrderProjector().apply(domain_event(previous, previous, revision=revision), previous)


def test_projection_rejects_wrong_scope_and_owner():
    previous = initial_projection()
    event = domain_event(previous, previous)
    with pytest.raises(ScopeMismatch):
        OrderProjector().apply(replace(event, scope=replace(event.scope, venue_account_id='other')), previous)
    with pytest.raises(ScopeMismatch):
        OrderProjector().apply(replace(event, aggregate_id='order:other'), previous)
    changed = replace(previous, order=replace(previous.order, ownership_epoch=2))
    with pytest.raises(ScopeMismatch):
        OrderProjector().apply(replace(event, payload=changed), previous)


def test_claimed_venue_snapshot_without_verifier_is_rejected():
    previous = initial_projection()
    payload = replace(previous, outcome_provenance='VENUE_EVENT', lifecycle_revision=1)
    with pytest.raises(MissingEvidence):
        OrderProjector().apply(domain_event(previous, payload), previous)
    ref = t.EvidenceRef(id='not-ingested', hash='a' * 64, environment=t.Environment.PAPER,
                        source='synthetic', visible_at=STAMP)
    with pytest.raises(MissingEvidence, match='verifier'):
        OrderProjector().apply(domain_event(previous, payload, evidence=(ref,)), previous)


def test_uningested_fill_and_snapshot_cannot_claim_venue_evidence(evidence_case):
    s = evidence_case
    fill = s.fill()
    unbound = replace(fill.evidence, id='unbound-archive-reference')
    with pytest.raises(MissingEvidence):
        s.fills.record(replace(fill, evidence=unbound))
    payload = replace(s.projection, outcome_provenance='VENUE_EVENT', lifecycle_revision=1)
    with pytest.raises(MissingEvidence):
        s.projector.apply(domain_event(s.projection, payload, evidence=(unbound,)), s.projection)
    assert s.count('fills') == 0


def test_rest_and_ws_out_of_order_do_not_double_count(evidence_case):
    s = evidence_case
    s.snapshot(lifecycle=t.OrderLifecycle.PARTIALLY_FILLED, filled='0.4', leaves='0.6',
               lifecycle_revision=10, provenance='VENUE_QUERY', source='synthetic-rest')
    fill = s.fill('0.2')
    s.project(s.event(s.fills.record(fill)))
    assert s.projection.filled_quantity.value == Decimal('0.4')
    s.snapshot(lifecycle=t.OrderLifecycle.ACCEPTED, filled='0.1', leaves='0.9',
               lifecycle_revision=2)
    assert s.projection.lifecycle_state == t.OrderLifecycle.PARTIALLY_FILLED
    assert s.projection.filled_quantity.value == Decimal('0.4')
    assert s.projection.leaves_quantity.value == Decimal('0.6')
    assert s.projection.lifecycle_revision == 10
    fill2 = s.fill('0.3')
    s.project(s.event(s.fills.record(fill2)))
    assert s.projection.filled_quantity.value == Decimal('0.5')
    assert s.projection.leaves_quantity.value == Decimal('0.5')
    assert s.projection.command_certainty == 'UNKNOWN'


def test_venue_evidence_supersedes_local_revision_namespace(evidence_case):
    s = evidence_case
    s.snapshot(lifecycle=t.OrderLifecycle.CANCELED, lifecycle_revision=99,
               provenance='LOCAL_POLICY')
    assert s.projection.leaves_quantity.value == 1
    s.snapshot(lifecycle=t.OrderLifecycle.ACCEPTED, lifecycle_revision=1, leaves='1')
    assert s.projection.lifecycle_state == t.OrderLifecycle.ACCEPTED
    assert s.projection.outcome_provenance == 'VENUE_EVENT'
    assert s.projection.lifecycle_revision == 1
    assert s.projection.command_certainty == 'UNKNOWN'


def test_fill_after_local_terminal_restores_observed_lifecycle(evidence_case):
    s = evidence_case
    s.snapshot(lifecycle_revision=99, provenance='LOCAL_POLICY')
    fill = s.fill()
    s.project(s.event(s.fills.record(fill)))
    assert s.projection.lifecycle_state == t.OrderLifecycle.PARTIALLY_FILLED
    assert s.projection.outcome_provenance == 'VENUE_EVENT'
    assert s.projection.lifecycle_revision == 0
    assert s.projection.leaves_quantity.value == Decimal('0.8')
    assert s.projection.command_certainty == 'UNKNOWN'


def test_correction_appends_and_replays_without_erasing_original(evidence_case):
    s = evidence_case
    fill = s.fill('0.4', fees=(t.Money(amount=Decimal('0.02'), currency='USDT'),))
    receipt = s.fills.record(fill)
    first_event = s.event(receipt)
    s.project(first_event)
    correction = s.fill('-0.1', correction_of=fill.fill_id,
                        fees=(t.Money(amount=Decimal('-0.005'), currency='USDT'),))
    corrected = s.fills.record(correction)
    s.project(s.event(corrected))
    assert s.projection.filled_quantity.value == Decimal('0.3')
    assert s.projection.leaves_quantity.value == Decimal('0.7')
    assert s.count('fills') == s.count('fill_fees') == 2
    assert s.event(receipt) == first_event
    assert s.fills.quantity_at(first_event).value == Decimal('0.4')
    assert not s.fills.record(correction).recorded
    assert s.notice(correction).protection_recheck_required
    replay = initial_projection(s.scope)
    for event in (first_event, s.event(corrected)):
        replay = s.projector.apply(event, replay).projection
    assert replay == s.projection


def test_correction_preserves_missing_historical_quantity(evidence_case):
    s = evidence_case
    s.snapshot(lifecycle=t.OrderLifecycle.PARTIALLY_FILLED, filled='0.8', leaves='0.2')
    fill = s.fill('0.2')
    s.project(s.event(s.fills.record(fill)))
    correction = s.fill('-0.1', correction_of=fill.fill_id)
    s.project(s.event(s.fills.record(correction)))
    assert s.projection.filled_quantity.value == Decimal('0.7')
    assert s.projection.leaves_quantity.value == Decimal('0.3')
    assert s.notice(correction).history_complete is False


def test_correction_target_and_bounds_are_enforced(evidence_case):
    s = evidence_case
    fill = s.fill('0.2')
    s.fills.record(fill)
    with pytest.raises(MissingEvidence, match='missing'):
        s.fills.record(s.fill('-0.1', correction_of='missing'))
    with pytest.raises(ValueError, match='exceeds original'):
        s.fills.record(s.fill('-0.3', correction_of=fill.fill_id))
    correction = s.fill('-0.1', correction_of=fill.fill_id)
    s.fills.record(correction)
    with pytest.raises(ValueError, match='original execution'):
        s.fills.record(s.fill('-0.01', correction_of=correction.fill_id))
    other = s.scoped(instrument_uid='another-instrument')
    with pytest.raises(ScopeMismatch):
        other.fills.record(other.fill('-0.1', correction_of=fill.fill_id))
    assert s.count('fills') == 2 and other.count('fills') == 0


def test_correction_argument_is_bound_and_self_reference_rejected(evidence_case):
    s = evidence_case
    original = s.fill()
    s.fills.record(original)
    candidate = s.fill('-0.1')
    receipt = s.fills.record(candidate, correction_of=original.fill_id)
    assert receipt.recorded
    assert s.event(receipt).payload.correction_of == original.fill_id
    with pytest.raises(ValueError, match='conflicting correction'):
        s.fills.record(replace(candidate, correction_of=original.fill_id), correction_of='other')
    with pytest.raises(ValueError, match='invalid correction'):
        s.fills.record(replace(candidate, correction_of=candidate.fill_id))


def test_zero_quantity_fee_adjustment_is_not_silently_accepted(evidence_case):
    # IP-03 currently forbids zero quantity fills. Keep the gap explicit until
    # a reviewed append-only fee adjustment contract/storage path exists.
    s = evidence_case
    original = s.fill()
    s.fills.record(original)
    candidate = s.fill('0', correction_of=original.fill_id,
                       fees=(t.Money(amount=Decimal('0.01'), currency='USDT'),))
    with pytest.raises(ValueError, match='nonzero'):
        s.fills.record(candidate)
    assert s.count('fills') == 1 and s.count('fill_fees') == 0


def test_unknown_fees_and_native_identity_remain_missing(evidence_case):
    s = evidence_case
    fill = s.fill(native_id=None)
    receipt = s.fills.record(fill)
    assert not receipt.recorded and receipt.duplicate_of is None
    assert receipt.event_ref is None
    assert s.count('fills') == s.count('fill_fees') == s.count('outbox') == 0
    assert s.count('audit_events') == 1
    assert not s.fills.record(fill).recorded
    assert s.count('audit_events') == 1
    with s.ledger.connect_admin() as conn:
        row = conn.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s',
                           (scope_id(s.scope),)).fetchone()
    from trading_system.storage.models import decode
    notice = decode(FillNotice, row[0])
    assert notice.status == 'UNKNOWN:NATIVE_ID_AND_COVERAGE_REQUIRED'
    assert not notice.fees_known and not notice.history_complete
    assert notice.protection_recheck_required
    known = s.fill()
    assert s.fills.record(known).recorded
    assert not s.notice(known).fees_known and not s.notice(known).history_complete


def test_projection_requires_exact_committed_fill_event(evidence_case):
    s = evidence_case
    fill = s.fill()
    event = domain_event(s.projection, fill, evidence=(fill.evidence,))
    with pytest.raises(MissingEvidence):
        s.projector.apply(event, s.projection)
    receipt = s.fills.record(fill)
    event = s.event(receipt)
    forged = replace(event, payload=replace(fill, quantity=t.Quantity(value=Decimal('0.9'), unit='BTC')))
    with pytest.raises(MissingEvidence):
        s.projector.apply(forged, s.projection)
    assert s.project(event).filled_quantity.value == Decimal('0.2')


def test_fill_commit_failure_rolls_back_event_fees_notice_and_outbox(evidence_case):
    s = evidence_case
    fill = s.fill(fees=(t.Money(amount=Decimal('0.001'), currency='USDT'),))
    failing = FillLedger(failing_uow(s), s.scope, trade_id_scope='INSTRUMENT')
    with pytest.raises(OSError, match='commit failure'):
        failing.record(fill)
    for table in ('fills', 'fill_fees', 'domain_events', 'audit_events', 'outbox'):
        assert s.count(table) == 0
    receipt = s.fills.record(fill)
    assert receipt.recorded and s.event(receipt).revision == 1
    assert s.notice(fill).aggregate_sequence == 2


def test_ingress_commit_failure_does_not_issue_accepted_receipt(evidence_case):
    s = evidence_case
    event = s.venue_event(s.raw())
    ingestor = EvidenceIngestor(failing_uow(s), s.scope, read_raw=s.read_raw)
    with pytest.raises(OSError, match='commit failure'):
        ingestor.ingest(event)
    assert s.count('venue_events') == 0
    assert s.ingestor.ingest(event).accepted
    assert s.evidence_records()[0].aggregate_sequence == 1


def test_fill_identity_unit_and_scope_conflicts_fail_closed(evidence_case):
    s = evidence_case
    fill = s.fill()
    s.fills.record(fill)
    second = s.fill()
    with pytest.raises(ValueError, match='fill_id already'):
        s.fills.record(replace(second, fill_id=fill.fill_id))
    with pytest.raises(ScopeMismatch, match='units changed'):
        s.fills.record(replace(second, quantity=t.Quantity(value=Decimal('0.2'), unit='ETH')))
    with pytest.raises(ScopeMismatch, match='scope mismatch'):
        s.fills.record(replace(second, scope=replace(s.scope, venue_account_id='different')))
    assert s.count('fills') == 1


def test_decimal_subtraction_does_not_round_before_exact_addition():
    with localcontext() as context:
        context.prec = 6
        actual = subtract_exact(Decimal('9007199254740993.00000002'),
                                Decimal('9007199254740993.00000001'))
    assert actual == Decimal('0.00000001')


def test_live_is_not_enabled_and_native_scope_is_required():
    live = replace(initial_projection().order.identity.scope, environment=t.Environment.LIVE)
    with pytest.raises(ValueError, match='LIVE'):
        EvidenceIngestor(None, live, read_raw=lambda ref: b'')
    with pytest.raises(ValueError, match='LIVE'):
        FillLedger(None, live, trade_id_scope='INSTRUMENT')
    previous = initial_projection(live)
    with pytest.raises(ValueError, match='LIVE'):
        OrderProjector().apply(domain_event(previous, previous), previous)
    with pytest.raises(ValueError, match='trade ID scope'):
        FillLedger(None, initial_projection().order.identity.scope, trade_id_scope='UNKNOWN')
