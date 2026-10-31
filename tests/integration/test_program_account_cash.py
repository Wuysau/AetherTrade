"""Closed-ledger cash reconstruction; no running balance or source flag credit.

Posting markers in this cohort test authenticated economic reconstruction. They
are not a claim that owner/position/protection/book posting is already complete.
"""
from dataclasses import replace
from decimal import Decimal as D
from datetime import timedelta

import pytest

from integration.test_program_account_opening import isolated_postgres,command_database,source_database,database,provision_uow,inputs
from integration.test_program_fill_source_approval import venue,actual_verifier
from integration.test_program_financial_admission import real_case
from integration.test_paper_market_matcher import case,submit,event
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload,utc_timestamp
from trading_system.engine.paper_market import PaperMatcher
from trading_system.execution.program_account_opening import ProgramAccountOpeningService,read_program_account_opening
from trading_system.storage.models import MissingEvidence,scope_id


def prepared(database):
    from uuid import uuid4
    c=real_case(database,instrument_uid='isolated-cash-market-'+uuid4().hex,provision_opening=False)
    balances,evidence=inputs(c)
    ProgramAccountOpeningService(provision_uow(database),c.scope).initialize(balances,evidence=evidence)
    return c


def matched(database,venue,tmp_path):
    from trading_system.execution.program_accounting import ProgramPaperAccountingService
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    from integration.test_program_fill_source_approval import source_uow
    c=case(database,venue,tmp_path,prepared_case=prepared(database));submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    verifier,codec=actual_verifier(c,tmp_path/'archive')
    source=ProgramFillSourceService(source_uow(database),c.scope,verifier=verifier)
    service=ProgramPaperAccountingService(c.service.uow,c.scope,source_service=source,trade_id_scope='INSTRUMENT')
    service.apply(c.write.command_id)
    approval=source.approve(c.write.command_id).approvals[0]
    return c,service,codec,approval


def economic_marker(tx,service,approval):
    from trading_system.execution.program_asset_reader import ProgramAssetPostingReader
    from trading_system.execution.program_asset_postings import make_program_asset_posting
    fact=ProgramAssetPostingReader(tx,fill_ledger=service._fills).read(approval.approval_id)
    record=make_program_asset_posting(fact)
    tx._append('audit_events',record.record_id,record,0)
    return record


def test_no_generic_history_preserves_legacy_branch_and_explicit_opening_reconstructs(database):
    from trading_system.execution.program_account_cash import read_program_account_cash
    c=prepared(database)
    with c.service.uow.transaction(c.scope) as tx:
        assert read_program_account_cash(tx) is None
        frame=read_program_account_cash(tx,read_opening=read_program_account_opening)
        assert {m.currency:m.amount for m in frame.balances}==dict(BTC=D(10),USDT=D('1000000'))
        assert frame.native_identities==() and not frame.execution_authorized


def test_real_native_effects_are_counted_once_across_repeated_source_snapshots(database,venue,tmp_path):
    from trading_system.execution.program_account_cash import observe_program_account_cash as read_program_account_cash
    c,s,codec,approval=matched(database,venue,tmp_path)
    try:
        with c.service.uow.transaction(c.scope) as tx:
            economic_marker(tx,s,approval)
            first=read_program_account_cash(tx,read_opening=read_program_account_opening)
        assert {m.currency:m.amount for m in first.balances}==dict(BTC=D('10.4'),USDT=D('967404'))
        assert len(first.native_identities)==1
        old=c.c.facts.inputs.clock
        c.c.facts=replace(c.c.facts,inputs=replace(c.c.facts.inputs,clock=replace(old,
            utc_now=(utc_timestamp(old.utc_now)+timedelta(seconds=1)).isoformat(),
            monotonic_ns=old.monotonic_ns+1_000_000_000)))
        s.source_service.approve(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_snapshots WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(2,)
            again=read_program_account_cash(tx,read_opening=read_program_account_opening)
            assert again.economic_revision==first.economic_revision and again.balances==first.balances
    finally:codec.close()


def test_unposted_source_is_a_blocker_and_never_zero_contribution(database,venue,tmp_path):
    from trading_system.execution.program_account_cash import read_program_account_cash
    c,s,codec,approval=matched(database,venue,tmp_path)
    try:
        with c.service.uow.transaction(c.scope) as tx:
            with pytest.raises(MissingEvidence,match='UNPOSTED_PROGRAM_ASSET_EFFECTS'):
                read_program_account_cash(tx,read_opening=read_program_account_opening)
    finally:codec.close()


def test_economic_audit_marker_without_actual_owned_transition_cannot_be_spendable(database,venue,tmp_path):
    from trading_system.execution.program_account_cash import read_program_account_cash
    c,s,codec,approval=matched(database,venue,tmp_path)
    try:
        with c.service.uow.transaction(c.scope) as tx:
            economic_marker(tx,s,approval)
            with pytest.raises(MissingEvidence,match='UNPOSTED_PROGRAM_OWNED_EFFECTS'):
                read_program_account_cash(tx,read_opening=read_program_account_opening)
    finally:codec.close()


def test_ordinary_audit_delta_cannot_mint_more_than_approved_native_effects(database,venue,tmp_path):
    from trading_system.execution.program_account_cash import read_program_account_cash
    from trading_system.execution.program_asset_reader import ProgramAssetPostingReader
    from trading_system.execution.program_asset_postings import make_program_asset_posting
    c,s,codec,approval=matched(database,venue,tmp_path)
    try:
        with c.service.uow.transaction(c.scope) as tx:
            fact=ProgramAssetPostingReader(tx,fill_ledger=s._fills).read(approval.approval_id)
            record=make_program_asset_posting(fact)
            forged=replace(record,asset_deltas=(t.Money(amount=D('1000000'),currency='USDT'),))
            tx._append('audit_events',record.record_id,forged,0)
        with c.service.uow.transaction(c.scope) as tx:
            with pytest.raises(MissingEvidence,match='PROGRAM_ASSET_POSTING_MISMATCH'):
                read_program_account_cash(tx,read_opening=read_program_account_opening)
    finally:codec.close()


def test_unknown_opening_provider_cannot_turn_a_dto_into_spendable_cash(database,venue,tmp_path):
    from trading_system.execution.program_account_cash import read_program_account_cash
    c,s,codec,approval=matched(database,venue,tmp_path)
    try:
        with c.service.uow.transaction(c.scope) as tx:
            economic_marker(tx,s,approval)
            with pytest.raises(MissingEvidence,match='TRUSTED_ACCOUNT_OPENING_READER_REQUIRED'):
                read_program_account_cash(tx,read_opening=lambda _:object())
    finally:codec.close()


def test_callable_equality_cannot_impersonate_a_registered_capital_reader(database):
    from trading_system.execution.program_account_cash import read_program_account_cash
    c=prepared(database)
    with c.service.uow.transaction(c.scope) as tx:
        actual=read_program_account_opening(tx)
        class ImpersonatingReader:
            def __eq__(self,other):return True
            def __call__(self,tx):
                return replace(actual,balances=(t.Money(currency='USDT',amount=D('9000000')),))
        with pytest.raises(MissingEvidence,match='TRUSTED_ACCOUNT_OPENING_READER_REQUIRED'):
            read_program_account_cash(tx,read_opening=ImpersonatingReader())


def test_actual_unapproved_extra_fill_cannot_be_ignored_by_account_reconstruction(database,venue,tmp_path):
    from trading_system.execution.program_account_cash import observe_program_account_cash as read_program_account_cash
    c,s,codec,approval=matched(database,venue,tmp_path)
    try:
        with c.service.uow.transaction(c.scope) as tx:
            economic_marker(tx,s,approval)
            # Adversarial normalized input, not a valid exchange fill proof.
            forged=replace(approval.normalized.fill,fill_id='unapproved-extra',native_trade_id='unapproved-extra',
                quantity=replace(approval.normalized.fill.quantity,value=D('.1')))
            s._fills.record_in_tx(tx,forged)
        with c.service.uow.transaction(c.scope) as tx:
            with pytest.raises(MissingEvidence,match='UNAPPROVED_ACCOUNT_FILL_HISTORY'):
                read_program_account_cash(tx,read_opening=read_program_account_opening)
    finally:codec.close()


def test_owner_sql_legacy_origin_and_fill_cannot_mint_mixed_account_credit(database,venue,tmp_path):
    import json
    from pathlib import Path
    from trading_system.contracts.canonical import canonical_json,canonical_hash
    from trading_system.storage.events import EventStore
    from trading_system.execution.program_account_cash import read_program_account_cash
    c,s,codec,approval=matched(database,venue,tmp_path)
    try:
        with c.service.uow.transaction(c.scope) as tx:
            economic_marker(tx,s,approval)
            command=json.loads((Path(__file__).resolve().parents[1]/'fixtures/contracts/examples/execution-command.paper.json').read_text())
            command.update(command_id='owner-forged-legacy',venue_account_id=c.scope.venue_account_id,
                instrument_uid=c.scope.instrument_uid,logical_client_order_id='owner-forged-legacy-logical')
            command['canonical_payload']['side']='SELL'
            command['payload_hash']=canonical_hash(command['canonical_payload'])
            tx.execute('INSERT INTO ledger.commands(scope_id,record_id,sequence,payload,transaction_id) '
                'VALUES(%s,%s,1,%s::jsonb,%s)',(scope_id(c.scope),command['command_id'],canonical_json(command),tx.transaction_id))
            tx.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
                'VALUES(%s,%s,1,%s::jsonb,%s)',(scope_id(c.scope),'console:request:'+command['command_id'],
                canonical_json(dict(symbol='BTCUSDT')),tx.transaction_id))
            # Adversarial ordinary owner writes, never a legitimate native fill.
            fake=replace(approval.normalized.fill,fill_id='owner-forged-legacy-fill',native_trade_id='owner-forged-legacy-trade',
                order=replace(approval.normalized.fill.order,logical_client_order_id='owner-forged-legacy-logical'),
                quantity=t.Quantity(unit='BASE',value=D(1)),price=t.Price(currency='USDT',value=D('1000000')),
                fees=(t.Money(currency='USDT',amount=D(0)),))
            EventStore().append(tx,t.DomainEvent(event_id='owner-forged-legacy-event',scope=c.scope,
                aggregate_id='owner-forged-legacy-fill',revision=1,occurred_at=fake.occurred_at,evidence=(fake.evidence,),payload=fake),0)
        with c.service.uow.transaction(c.scope) as tx:
            with pytest.raises(MissingEvidence,match='LEGACY_AUTHENTICATED_SOURCE_REQUIRED'):
                read_program_account_cash(tx,read_opening=read_program_account_opening)
    finally:codec.close()
