"""Explicit pre-0008 PostgreSQL compatibility; requires its own fresh database."""
from integration.support_ip_03 import isolated_postgres, LedgerScenario
from trading_system.execution.commands import CommandRepository
from trading_system.storage.application_writes import application_write_obligations


def test_pre_program_database_retains_legacy_unknown_and_original_id(isolated_postgres):
    s=LedgerScenario(isolated_postgres)
    with s.uow.transaction(s.scope) as tx:
        assert tx.execute("SELECT to_regclass('ledger.order_writes_v2')").fetchone()[0] is None
        assert application_write_obligations(tx)==()
        s.write_bundle(tx)
        tx.commands.mark_may_have_sent(tx,s.command.command_id,'DURABLE_READY')
    with s.uow.transaction(s.scope) as tx:
        assert CommandRepository(tx).unresolved_in_group('other')==(s.command.command_id,)
        assert CommandRepository(tx).unresolved_in_group(s.command.command_id)==()
        assert tx.commands.get(s.command.command_id).venue_client_order_id==s.command.venue_client_order_id
