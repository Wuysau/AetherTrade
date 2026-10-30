"""Current release proof is reverified, never inherited from a financial DTO."""
from dataclasses import replace
from decimal import Decimal as D
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from unit.support_ip_09 import make_case,reservation,evidence,NOW
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.wire import ContractCodec
from trading_system.storage.models import MissingEvidence,scope_id
from trading_system.storage.command_state import CommandOutcome,command_hash
from trading_system.risk.reservations import (
    ReservationService,ReleasedBalanceAttestation,ReconciliationProof,prepare_collateral,
)
from trading_system.risk.program_quantity_scope import current_program_quantity_reservation_ids


@pytest.fixture
def released_case(monkeypatch):
    raw=make_case().book;sid=scope_id(raw.scope)
    path=Path(__file__).resolve().parents[1]/'fixtures/contracts/examples/execution-command.paper.json'
    command=ContractCodec().decode('execution-command',json.loads(path.read_text(encoding='utf-8')))
    initial=replace(reservation(make_case()).reservation,command_refs=(command.command_id,),intent_id=command.intent_id)
    released=t.ReservationProjection(reservation=replace(initial,state=t.ReservationState.RELEASED,
        evidence_watermark='older-valid-book'),held=D(0),reflected=D(0),release_pending=D(0),sequence=1)
    proof=ReconciliationProof(evidence=t.ReservationEvidence(reservation_id=initial.reservation_id,
        venue_freeze_ref=evidence(),reflected_amount=D(0),command_certainty='NOT_SENT',terminal_verified=True,
        watermark='older-valid-book'),scope=raw.scope,intent_id=initial.intent_id,asset=initial.asset,
        command_refs=initial.command_refs,book_sequence=0,obligations_cleared=True)
    outcome=CommandOutcome(command_id=command.command_id,request_hash=command_hash(command),
        durable_record_ref=command.durable_record_ref,receipt=t.CommandReceipt(receipt_id='abort',
        command_id=command.command_id,status=t.CommandState.ABORTED_UNSENT,reason=None,existing_result_ref=None),
        provenance='LOCAL_UNSENT_ABORT')
    cp=ContractCodec().encode(command);op=domain_payload(outcome)
    class Rows:
        def __init__(self,rows):self.rows=rows
        def fetchone(self):return self.rows[0] if self.rows else None
        def fetchall(self):return self.rows
    class Tx:
        scope=raw.scope;group_id=initial.collateral_group_id;transaction_id='current-release-test'
        projections=SimpleNamespace(book=lambda scope:raw)
        abort=(cp,op);reconciliation=(sid,domain_payload(proof))
        def require_scope(self,scope):assert scope==self.scope
        def execute(self,sql,args=()):
            if 'SELECT r.scope_id,r.record_id' in sql:return Rows([(sid,initial.reservation_id,domain_payload(initial),1,domain_payload(released))])
            if 'SELECT r.payload, p.payload' in sql:return Rows([(domain_payload(initial),domain_payload(released))])
            if 'SELECT count(*),min(sequence)' in sql:return Rows([(1,1,1)])
            if 'FROM ledger.audit_events WHERE record_id=' in sql:return Rows([self.reconciliation] if self.reconciliation else [])
            if 'SELECT scope_id FROM ledger.commands' in sql:return Rows([(sid,)])
            if 'SELECT payload FROM ledger.commands' in sql:return Rows([(cp,)])
            if 'FROM ledger.audit_events WHERE scope_id=' in sql:return Rows([(op,)])
            if 'FROM ledger.order_projections' in sql:return Rows([])
            if 'FROM ledger.command_aborts' in sql:return Rows([self.abort] if self.abort else [])
            raise AssertionError(sql)
    tx=Tx()
    attestation=ReleasedBalanceAttestation(transaction_id=tx.transaction_id,book_hash=canonical_hash(domain_payload(raw)),
        reservation_ids=(initial.reservation_id,),evidence=raw.evidence)
    # Isolate only unrelated opening authentication. All release/reconciliation/
    # protected-abort/immutable physical projection branches execute unmodified.
    monkeypatch.setattr('trading_system.risk.program_cash_guard.require_current_account_book',lambda *args:None)
    return tx,raw,released,attestation


def test_scoped_hold_reconstruction_reuses_the_authenticated_current_preparation(released_case):
    tx,raw,released,attestation=released_case
    reader=lambda *args:attestation
    prepared=prepare_collateral(tx,raw,load_released_balances=reader)
    service=ReservationService(None,raw.scope,None,clock=lambda:NOW,load_released_balances=reader)
    assert prepared==service._prepare_collateral(tx,raw)
    assert prepared.holds==(released,) and prepared.released_proof==attestation
    assert current_program_quantity_reservation_ids(tx,raw,prepared.book,load_released_balances=reader)==(released.reservation.reservation_id,)
    with pytest.raises(MissingEvidence,match='PROGRAM_CURRENT_FINANCIAL_HOLDS_CHANGED'):
        current_program_quantity_reservation_ids(tx,raw,prepared.book)


@pytest.mark.parametrize('fault',['transaction','book','evidence','duplicate_id','foreign_id','missing_abort','missing_reconciliation'])
def test_current_attestation_still_requires_every_original_binding(released_case,fault):
    tx,raw,released,attestation=released_case
    if fault=='transaction':attestation=replace(attestation,transaction_id='old-transaction')
    elif fault=='book':attestation=replace(attestation,book_hash='f'*64)
    elif fault=='evidence':attestation=replace(attestation,evidence=())
    elif fault=='duplicate_id':attestation=replace(attestation,reservation_ids=attestation.reservation_ids*2)
    elif fault=='foreign_id':attestation=replace(attestation,reservation_ids=('unmapped',))
    elif fault=='missing_abort':tx.abort=None
    else:tx.reconciliation=None
    with pytest.raises(MissingEvidence,match='RELEASE_BALANCE_ATTESTATION_INVALID'):
        current_program_quantity_reservation_ids(tx,raw,replace(raw,reservations=(released,)),
            load_released_balances=lambda *args:attestation)
