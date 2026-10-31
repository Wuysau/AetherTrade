"""Actual protected cash reconstruction at all shared financial boundaries."""
import pytest

from integration.test_program_account_cash import (
    isolated_postgres,command_database,source_database,database,venue,prepared,matched,economic_marker,
)
from trading_system.execution.program_account_opening import read_program_account_opening
from trading_system.execution.program_book_cash import bind_program_book_cash
from trading_system.storage.models import MissingEvidence


def test_collateral_preparation_requires_an_actual_current_account_binding(database):
    c=prepared(database)
    with c.service.uow.transaction(c.scope) as tx:
        book=tx.projections.book(c.scope)
        denied=c.service._prepare_collateral(tx,book)
        assert denied.rejection_codes==('BOOK_CASH_BINDING_REQUIRED',)
        bind_program_book_cash(tx,book,read_opening=read_program_account_opening)
        assert c.service._prepare_collateral(tx,book).rejection_codes==()


@pytest.mark.parametrize('boundary',['mark_consume_current','consumed_after_wait'])
def test_actual_recorded_native_economics_invalidate_all_current_send_boundaries(database,venue,tmp_path,boundary):
    from trading_system.execution.program_commands import ProgramCommandRepository
    c,service,codec,approval=matched(database,venue,tmp_path)
    try:
        # An authenticated same-ledger economic marker changes the shared account
        # revision. It grants no ownership; the original raw book stays unchanged.
        with c.service.uow.transaction(c.scope) as tx:economic_marker(tx,service,approval)
        with pytest.raises(MissingEvidence,match='STALE_BOOK_ACCOUNT_CASH'):
            if boundary=='consumed_after_wait':
                c.gate.recheck_consumed(c.permit,c.lease)
            else:
                with c.service.uow.transaction(c.scope) as tx:
                    ProgramCommandRepository(tx,**c.gate.readers)._current(c.write,c.lease)
    finally:codec.close()
