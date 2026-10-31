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
