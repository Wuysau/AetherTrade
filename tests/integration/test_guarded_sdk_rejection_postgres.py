"""Real durable boundary: an unqualified SDK must reject before consume."""
import pytest
from integration.test_native_creation_owner_postgres import prepared
from integration.test_program_commands_postgres import isolated_postgres,database
from trading_system.engine.nautilus.guarded_creation import submit_guarded_creation_probe
from trading_system.storage.models import scope_id


def test_unqualified_full_sdk_never_consumes_original_private_pg_permit(database,tmp_path):
    c=prepared(database,tmp_path)
    try:
        with c.exec_owner.session(c.lease.shard):
            permit=c.gate.mark_may_have_sent(c.write.command_id,c.lease)
            with pytest.raises(ValueError,match='GUARDED_SDK_BUILD_NOT_QUALIFIED'):
                submit_guarded_creation_probe(c.engine,c.write,c.request,permit,
                    boundary=c.gate,lease=c.lease,owner=c.exec_owner)
        with c.owner.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
                              (scope_id(c.scope),)).fetchone()==(0,)
            assert tx.execute('SELECT count(*) FROM ledger.program_dispatch_marks WHERE scope_id=%s',
                              (scope_id(c.scope),)).fetchone()==(1,)
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',
                              (scope_id(c.scope),)).fetchone()==(0,)
    finally:c.codec.close()
