"""Source-backed cost/history PostgreSQL qualification."""
from decimal import Decimal as D
from integration.test_program_v2_owned_posting import (isolated_postgres,command_database,
    source_database,cash_database,database,venue,venue_v2,setup)
from trading_system.ownership.registry import RegistryTx
from trading_system.execution.program_account_opening import read_program_account_opening


def test_fresh_native_cost_history(database,venue_v2,tmp_path):
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.cost_evidence.missing
            from trading_system.execution.program_cost_history import reconcile_program_cost_history,require_program_cost_history
            proof=reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
            assert proof.remaining_cost_numerator==32628503 and proof.remaining_cost_denominator==1000
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.4')
            assert not RegistryTx(tx).owner().position.cost_evidence.missing
        with c.service.uow.transaction(c.scope) as tx:
            assert require_program_cost_history(tx)==proof
            before=tx.projections.book(c.scope)
            assert reconcile_program_cost_history(tx,read_opening=read_program_account_opening)==proof
            assert tx.projections.book(c.scope)==before
    finally:codec.close()
