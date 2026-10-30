"""Same-account genuine fills invalidate another instrument's cash binding."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from integration.test_program_account_cash import (
    isolated_postgres,command_database,source_database,database,venue,prepared,matched,economic_marker,
)
from trading_system.contracts import types as t
from trading_system.execution.program_account_opening import read_program_account_opening
from trading_system.storage.models import MissingEvidence,scope_id


def functions():
    from trading_system.execution.program_book_cash import bind_program_book_cash,require_current_program_book_cash,reconcile_program_book_cash
    return bind_program_book_cash,require_current_program_book_cash,reconcile_program_book_cash


def test_exact_current_cash_binding_is_required_and_a_forged_book_grants_nothing(database):
    bind,guard,_=functions();c=prepared(database)
    with c.service.uow.transaction(c.scope) as tx:
        book=tx.projections.book(c.scope)
        with pytest.raises(MissingEvidence,match='BOOK_CASH_BINDING_REQUIRED'):
            guard(tx,book,read_opening=read_program_account_opening)
        bind(tx,book,read_opening=read_program_account_opening)
        assert guard(tx,book,read_opening=read_program_account_opening).native_identities==()
        with pytest.raises(MissingEvidence,match='BOOK_CASH_VECTOR_MISMATCH'):
            bind(tx,replace(book,balances=(t.Money(currency='USDT',amount=D('9999999')),)),
                read_opening=read_program_account_opening)


def test_actual_fill_changes_other_instrument_revision_until_its_own_owner_reconciles(database,venue,tmp_path):
    import psycopg
    from trading_system.ownership.registry import OwnershipRegistry
    from integration.test_program_owned_posting import setup
    bind,guard,reconcile=functions();c,s,codec=setup(database,venue,tmp_path)
    financial=s.financial_posting;s.financial_posting=None
    s.apply(c.write.command_id);s.financial_posting=financial
    other=replace(c.scope,instrument_uid='other-instrument')
    try:
        # Source is already committed but not posted, so opening-only cash is
        # not admissible. The old book deliberately has the prior full vector.
        with c.service.uow.transaction(c.scope) as tx:
            with pytest.raises(MissingEvidence,match='UNPOSTED_PROGRAM_ASSET_EFFECTS'):
                guard(tx,tx.projections.book(c.scope),read_opening=read_program_account_opening)
        s.apply(c.write.command_id)
        with psycopg.connect(database) as db:
            db.execute('INSERT INTO ledger.active_owners VALUES(%s,%s,%s,%s,%s,%s,1,1)',
                ('PAPER',other.venue_account_id,'SPOT',other.instrument_uid,'BOTH',c.c.terms.owner_strategy_id))
        coverage=replace(c.c.book.coverage,scope=other)
        with c.service.uow.transaction(other) as tx:
            old=replace(c.c.book,scope=other,coverage=coverage)
            tx.projections.save_book(old,0)
        position=t.ManagedPosition(position_id='other-owned-zero',scope=other,
            actual_quantity=t.Quantity(unit='BASE',value=D(0)),owner_strategy_id=c.c.terms.owner_strategy_id,
            owner_book_id='other-book',ownership_epoch=1,cost_evidence=coverage,
            realized_pnl=None,unrealized_valuation=None,active_exit_plan_id=None,risk_locks=())
        OwnershipRegistry(c.service.uow).register(position,1)
        with c.service.uow.transaction(other) as tx:
            with pytest.raises(MissingEvidence,match='BOOK_CASH_BINDING_REQUIRED'):
                guard(tx,old,read_opening=read_program_account_opening)
            current=reconcile(tx,read_opening=read_program_account_opening)
            assert current.sequence==old.sequence+1
            assert {m.currency:m.amount for m in current.balances}==dict(BTC=D('10.4'),USDT=D('967371.404'))
            assert current.positions==() # initial account inventory is not strategy inventory
            before=guard(tx,current,read_opening=read_program_account_opening)
        # A second genuine venue fill makes the other instrument stale again.
        from integration.test_paper_market_matcher import event
        from trading_system.engine.paper_market import PaperMatcher
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        s.apply(c.write.command_id)
        with c.service.uow.transaction(other) as tx:
            with pytest.raises(MissingEvidence,match='STALE_BOOK_ACCOUNT_CASH'):
                guard(tx,current,read_opening=read_program_account_opening)
            updated=reconcile(tx,read_opening=read_program_account_opening)
            after=guard(tx,updated,read_opening=read_program_account_opening)
            assert after.economic_revision!=before.economic_revision
            assert {m.currency:m.amount for m in updated.balances}==dict(BTC=D('10.6'),USDT=D('951057.106'))
            assert updated.sequence==current.sequence+1
            assert reconcile(tx,read_opening=read_program_account_opening)==updated
    finally:codec.close()


def test_current_cash_binding_requires_actual_owner_before_reconciliation(database):
    _,_,reconcile=functions();c=prepared(database)
    with c.service.uow.transaction(c.scope) as tx:
        before=tx.projections.book(c.scope)
        with pytest.raises(MissingEvidence,match='CURRENT_OWNED_POSITION_REQUIRED'):
            reconcile(tx,read_opening=read_program_account_opening)
        assert tx.projections.book(c.scope)==before
