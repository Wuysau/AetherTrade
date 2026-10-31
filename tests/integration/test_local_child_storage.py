"""Real PostgreSQL dormant-storage checks; NOT_RUN until shared DEV is ready."""
import pytest
from integration.test_program_commands_postgres import database
from integration.support_ip_03 import ROOT

@pytest.fixture(scope='module')
def child_storage(database):
    import psycopg
    with psycopg.connect(database) as db:
        db.execute((ROOT/'migrations/0030_local_child_commitments.sql').read_text(encoding='utf-8'))
    return database

@pytest.mark.parametrize('role',['ledger_owner','ledger_control','ledger_decision','ledger_program_dispatch_verifier'])
def test_runtime_roles_cannot_mint_or_mutate_child_commitments(child_storage,role):
    import psycopg
    with psycopg.connect(child_storage) as db:
        for privilege in ('INSERT','UPDATE','DELETE','TRUNCATE','TRIGGER'):
            assert db.execute('SELECT has_table_privilege(%s,%s,%s)',
                (role,'ledger.local_child_commitments_v1',privilege)).fetchone()==(False,)
        db.execute('SET ROLE '+role)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("INSERT INTO ledger.local_child_commitments_v1(scope_id,command_id,activation_id,commitment_hash,payload,transaction_id) VALUES ('x','x','x','x','{}','x')")


def test_storage_rejects_missing_source_shape_and_is_append_only(child_storage):
    import psycopg
    with psycopg.connect(child_storage) as db:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("INSERT INTO ledger.local_child_commitments_v1(scope_id,command_id,activation_id,commitment_hash,payload,transaction_id) VALUES ('x','x','x','x','{}','x')")
        db.rollback()
        for statement in ('UPDATE ledger.local_child_commitments_v1 SET command_id=command_id',
                          'DELETE FROM ledger.local_child_commitments_v1',
                          'TRUNCATE ledger.local_child_commitments_v1'):
            with pytest.raises(psycopg.errors.CheckViolation):db.execute(statement)
            db.rollback()



def test_orphan_child_cannot_escape_parent_based_obligation_scan(child_storage):
    import psycopg
    from psycopg.types.json import Jsonb
    scope=dict(environment='PAPER',venue_account_id='isolated-orphan',product='SPOT',
        instrument_uid='BTCUSDT',venue_leg='BOTH')
    payload=dict(request=dict(contract_version='2026-10-31',record_kind='LOCAL_CHILD_REQUEST_V1',
        activation_id='missing-activation',request=dict(command_id='orphan-child',scope=scope)))
    # Shape-valid dormant row, deliberately no financial/source claim. Even the
    # migration administrator cannot persist it without the exact parent row.
    with psycopg.connect(child_storage) as db:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.execute("INSERT INTO ledger.local_child_commitments_v1 "
                "(scope_id,command_id,activation_id,commitment_hash,payload,transaction_id) "
                "VALUES (ledger.program_digest(%s::jsonb),'orphan-child','missing-activation',"
                "ledger.program_digest(%s::jsonb),%s::jsonb,'orphan-test')",
                (Jsonb(scope),Jsonb(payload),Jsonb(payload)))
