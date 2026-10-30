"""First generic risk requires independently provisioned initial assets."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from integration.test_program_fill_source_approval import database,command_database
from integration.test_paper_market_matcher import isolated_postgres,venue,case,submit
from integration.test_program_financial_admission import real_case
from integration.test_program_commands_postgres import attach
from trading_system.contracts import types as t
from trading_system.execution.program_account_cash import read_program_account_cash
from trading_system.execution.program_legacy_opening import read_program_local_account_opening
from trading_system.storage.models import MissingEvidence,scope_id


def test_first_generic_intent_cannot_spend_a_book_without_actual_initial_capital(database):
    c=real_case(database,provision_opening=False)
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.projections.book(c.scope).balances
        assert read_program_account_cash(tx,read_opening=read_program_local_account_opening) is None
    with pytest.raises((MissingEvidence,ValueError),match='ACCOUNT_OPENING_REQUIRED'):
        c.service._transaction(lambda tx:attach(c,tx))
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
        assert tx.reservations.for_intent(c.record.intent_id)==()


def test_first_generic_intent_cannot_credit_owner_authored_inflated_book(database):
    c=real_case(database)
    with c.service.uow.transaction(c.scope) as tx:
        old=tx.projections.book(c.scope)
        forged=replace(old,sequence=old.sequence+1,balances=tuple(
            replace(m,amount=D('900000000')) if m.currency=='USDT' else m for m in old.balances))
        tx.projections.save_book(forged,old.sequence)
    with pytest.raises((MissingEvidence,ValueError),match='BOOK_CASH_BINDING_REQUIRED'):
        c.service._transaction(lambda tx:attach(c,tx))
    with c.service.uow.transaction(c.scope) as tx:assert tx.intents.get(c.record.intent_id) is None


def test_provisioned_first_generic_full_chain_binds_capital_without_creating_inventory(database,venue,tmp_path):
    c=case(database,venue,tmp_path)
    with c.service.uow.transaction(c.scope) as tx:
        frame=read_program_account_cash(tx,read_opening=read_program_local_account_opening)
        assert frame is not None and frame.native_identities==()
        assert tx.execute('SELECT count(*) FROM ledger.positions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
    submit(c)
    raw=c.engine.query_creation(c.write.command_id)
    assert raw.status=='NEW' and not raw.fills and raw.remaining_quantity==1
    with c.service.uow.transaction(c.scope) as tx:
        context=tx.execute('SELECT context FROM ledger.program_permit_consumptions WHERE scope_id=%s AND command_id=%s',
            (scope_id(c.scope),c.write.command_id)).fetchone()[0]
        assert context['account_opening_id']==frame.opening_id
        assert context['account_cash_revision']==frame.economic_revision
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
