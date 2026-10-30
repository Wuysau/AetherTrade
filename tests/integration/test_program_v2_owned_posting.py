"""Actual frozen V2 producer -> private source -> existing owned/FEE/book TX."""
from decimal import Decimal as D
import pytest

from integration.test_program_owned_posting import isolated_postgres,command_database,source_database,database,venue,setup
from integration.test_paper_market_matcher import event
from trading_system.engine.paper_market import PaperMatcher,PaperMarketFill
from trading_system.execution.program_account_cash import read_program_account_cash
from trading_system.execution.program_account_opening import read_program_account_opening
from trading_system.ownership.registry import RegistryTx
from trading_system.storage.models import scope_id


@pytest.fixture(scope='module')
def venue_v2(database,venue):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from trading_system.engine.paper_market_v2 import provision_paper_market_v2
    provision_paper_market_v2(make_conninfo(**{**conninfo_to_dict(database),
        'dbname':conninfo_to_dict(venue.runtime_dsn)['dbname']}),venue)
    return venue


def test_actual_v2_source_posts_exact_native_owner_assets_fees_and_neutral_book_guard(database,venue_v2,tmp_path):
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        source=service.source_service.approve(c.write.command_id)
        assert source.status=='APPROVED' and source.verification.record_kind=='PAPER_PROGRAM_VERIFICATION_V2'
        normalized=source.approvals[0].normalized
        assert type(normalized.raw) is PaperMarketFill and normalized.source_v2.record_kind=='PAPER_FILL_SOURCE_V2'
        assert normalized.fill.quantity.value==D('.4') and normalized.fill.fees[0].amount==D('32.596')
        result=service.apply(c.write.command_id)
        assert result.status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED',result.missing
        with c.service.uow.transaction(c.scope) as tx:
            from trading_system.risk.program_cash_guard import require_funded_program_account_book
            book=tx.projections.book(c.scope);position=RegistryTx(tx).owner().position
            frame=require_funded_program_account_book(tx,book)
            assert frame==read_program_account_cash(tx,read_opening=read_program_account_opening)
            assert position.actual_quantity.value==D('.4') and book.positions==(position,)
            assert {m.currency:m.amount for m in book.balances}==dict(BTC=D('10.4'),USDT=D('967371.404'))
            assert position.cost_evidence.missing and position.realized_pnl is None
            assert tx.reservations.for_intent(c.bundle.intent.intent_id)
            fee=tx.execute('SELECT payload FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()[0]
            assert fee['classification']=='FEE' and D(fee['amount']['amount'])==D('-32.596')
            assert tx.risk_locks.active(c.scope) and not result.funds_release_authorized
    finally:codec.close()


def test_actual_v2_additional_native_fill_and_source_replay_preserve_one_owned_effect_each(database,venue_v2,tmp_path):
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        with c.service.uow.transaction(c.scope) as tx:before=tx.projections.book(c.scope)
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.projections.book(c.scope)==before
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.6')
            assert {m.currency:m.amount for m in before.balances}==dict(BTC=D('10.6'),USDT=D('951057.106'))
            assert len(read_program_account_cash(tx,read_opening=read_program_account_opening).native_identities)==2
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(2,)
            assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(2,)
    finally:codec.close()
