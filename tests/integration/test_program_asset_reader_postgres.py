"""Source-approved financial facts require the actual existing native ledger.

Positive cases use genuine financial admission/consumption/matching. Negative
cases corrupt observations or normalized owner input; these are not venue fills.
"""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from integration.test_program_fill_source_approval import isolated_postgres,command_database,database,venue,setup_source
from integration.test_program_accounting_postgres import setup_accounting
from integration.test_paper_program_reports_postgres import reseal_report
from trading_system.execution.fills import FillLedger


def reader(tx,c):
    from trading_system.execution.program_asset_reader import ProgramAssetPostingReader
    return ProgramAssetPostingReader(tx,fill_ledger=FillLedger(c.service.uow,c.scope,trade_id_scope='INSTRUMENT'))


def test_genuine_ledger_fill_yields_authenticated_effects_without_issuing_credit(database,venue,tmp_path):
    c,service,codec=setup_accounting(database,venue,tmp_path)
    try:
        service.apply(c.write.command_id)
        approved=service.source_service.approve(c.write.command_id).approvals[0]
    finally:codec.close()
    with c.service.uow.transaction(c.scope) as tx:
        result=reader(tx,c).read(approved.approval_id)
        assert result.command_id==c.write.command_id and result.approval==approved
        assert result.fill.quantity.value==D('.4')
        assert {m.currency:m.amount for m in result.effects.asset_deltas}==dict(BTC=D('.4'),USDT=D('-32596'))
        assert not result.execution_authorized and not result.balance_credit_authorized


def test_private_source_approval_without_ledger_fill_cannot_be_spent(database,venue,tmp_path):
    from trading_system.storage.models import MissingEvidence
    c,source,codec=setup_source(database,venue,tmp_path)
    try:approved=source.approve(c.write.command_id).approvals[0]
    finally:codec.close()
    with c.service.uow.transaction(c.scope) as tx:
        with pytest.raises(MissingEvidence,match='RECORDED_NATIVE_FILL_REQUIRED'):
            reader(tx,c).read(approved.approval_id)


def test_global_source_discrepancy_is_not_hidden_by_consistent_individual_economics(database,venue,tmp_path):
    c,service,codec=setup_accounting(database,venue,tmp_path)
    original=c.engine.query_creation
    def changed(command_id):
        report=original(command_id)
        return reseal_report(replace(report,orders=(replace(report.orders[0],filled_quantity=D('.5')),)))
    c.engine.query_creation=changed
    try:
        service.apply(c.write.command_id)
        approved=service.source_service.approve(c.write.command_id).approvals[0]
    finally:codec.close()
    assert not approved.normalized.economics.discrepancies
    with c.service.uow.transaction(c.scope) as tx:
        with pytest.raises(ValueError,match='SOURCE_ECONOMICS_RECONCILIATION_REQUIRED'):
            reader(tx,c).read(approved.approval_id)


def test_owner_normalized_quantity_cannot_override_private_source_quantity(database,venue,tmp_path):
    c,service,codec=setup_accounting(database,venue,tmp_path)
    try:
        approved=service.source_service.approve(c.write.command_id).approvals[0]
        prepared=service._prepare(approved)
    finally:codec.close()
    # Adversarial decoder input: existing FillLedger does not independently
    # interpret market JSON. The financial reader must compare private source.
    with c.service.uow.transaction(c.scope) as tx:
        evidence=service._evidence.ingest_in_tx(tx,prepared)
        forged=replace(approved.normalized.fill,quantity=replace(approved.normalized.fill.quantity,value=D('.5')),
            evidence=evidence.raw_ref)
        service._fills.record_in_tx(tx,forged)
    with c.service.uow.transaction(c.scope) as tx:
        with pytest.raises(ValueError,match='native trade identity conflict'):
            reader(tx,c).read(approved.approval_id)


def test_foreign_approval_id_is_not_accepted_in_another_instrument(database,venue,tmp_path):
    c,service,codec=setup_accounting(database,venue,tmp_path)
    try:
        service.apply(c.write.command_id)
        approved=service.source_service.approve(c.write.command_id).approvals[0]
    finally:codec.close()
    with c.service.uow.transaction(replace(c.scope,instrument_uid='another-instrument')) as tx:
        with pytest.raises(ValueError,match='PROGRAM_COMMITTED_SOURCE_APPROVAL_REQUIRED'):
            reader(tx,replace_scope(c,tx.scope)).read(approved.approval_id)


def replace_scope(c,scope):
    from types import SimpleNamespace
    return SimpleNamespace(scope=scope,service=c.service)
