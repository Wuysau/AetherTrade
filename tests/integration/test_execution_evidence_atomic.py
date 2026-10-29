"""Real PG atomicity only; synthetic IP12 inputs are not venue execution proof."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from integration.support_ip_12 import ip12_database,evidence_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json,domain_payload
from trading_system.storage.models import scope_id,ScopeMismatch


def prepared_case(s):
    fill=replace(s.ledger.fill(),scope=s.scope,order=s.projection.order.identity,
        quantity=t.Quantity(value=Decimal('.2'),unit='BTC'),fees=(t.Money(amount=Decimal('.01'),currency='USDT'),))
    raw=s.raw(canonical_json({'fill':domain_payload(fill)}).encode())
    event=s.venue_event(raw)
    return replace(fill,evidence=raw.raw_payload_ref),s.ingestor.prepare(event)


def test_evidence_native_fill_fee_event_outbox_and_notice_share_one_physical_transaction(evidence_case):
    s=evidence_case;fill,prepared=prepared_case(s)
    def forbidden(ref):raise AssertionError('Durable bytes must be read before opening the business transaction')
    s.ingestor.read_raw=forbidden
    with s.uow.transaction(s.scope) as tx:
        assert s.ingestor.ingest_in_tx(tx,prepared).accepted
        receipt=s.fills.record_in_tx(tx,fill)
        assert receipt.recorded
        evidence=tx.execute('SELECT transaction_id,xmin::text FROM ledger.venue_events WHERE scope_id=%s',
            (scope_id(s.scope),)).fetchone()
        native=tx.execute('SELECT transaction_id,xmin::text FROM ledger.fills WHERE record_id=%s',(fill.fill_id,)).fetchone()
        event=tx.execute('SELECT transaction_id,xmin::text FROM ledger.domain_events WHERE event_id=%s',(receipt.event_ref.id,)).fetchone()
        outbox=tx.execute('SELECT transaction_id,xmin::text FROM ledger.outbox WHERE event_id=%s',(receipt.event_ref.id,)).fetchone()
        notice=tx.execute('SELECT transaction_id,xmin::text FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
            (scope_id(s.scope),'ip12:fill-notice:'+fill.fill_id)).fetchone()
        assert evidence==native==event==outbox==notice
        assert tx.execute('SELECT xmin::text FROM ledger.fill_fees WHERE fill_id=%s',(fill.fill_id,)).fetchone()==(native[1],)
    assert s.count('fills')==s.count('fill_fees')==s.count('outbox')==1


def test_caller_failure_rolls_back_evidence_fill_fee_event_and_notice(evidence_case):
    s=evidence_case;fill,prepared=prepared_case(s)
    with pytest.raises(RuntimeError,match='after actual fill'):
        with s.uow.transaction(s.scope) as tx:
            s.ingestor.ingest_in_tx(tx,prepared)
            s.fills.record_in_tx(tx,fill)
            raise RuntimeError('injected after actual fill')
    assert all(s.count(table)==0 for table in ('venue_events','fills','fill_fees','domain_events','outbox','audit_events'))
    assert s.read_raw(prepared.raw.raw_payload_ref)


def test_caught_validation_failure_still_rolls_back_the_whole_caller_transaction(evidence_case):
    s=evidence_case;fill,prepared=prepared_case(s)
    wrong=replace(fill,scope=replace(s.scope,venue_account_id='other'))
    with pytest.raises(RuntimeError,match='rollback-only'):
        with s.uow.transaction(s.scope) as tx:
            s.ingestor.ingest_in_tx(tx,prepared)
            try:s.fills.record_in_tx(tx,wrong)
            except ScopeMismatch:pass
    assert s.count('venue_events')==s.count('fills')==s.count('outbox')==0


def test_same_transaction_and_later_replay_keep_one_native_fill_and_original_notice(evidence_case):
    s=evidence_case;fill,prepared=prepared_case(s)
    with s.uow.transaction(s.scope) as tx:
        evidence=s.ingestor.ingest_in_tx(tx,prepared)
        assert s.fills.record_in_tx(tx,fill).recorded
        duplicate=s.fills.record_in_tx(tx,replace(fill,fill_id=uuid4().hex))
        assert not duplicate.recorded and duplicate.duplicate_of==fill.fill_id
    with s.uow.transaction(s.scope) as tx:
        assert s.ingestor.ingest_in_tx(tx,prepared)==evidence
        assert not s.fills.record_in_tx(tx,fill).recorded
    assert s.count('fills')==s.count('outbox')==s.count('venue_events')==s.count('audit_events')==1
