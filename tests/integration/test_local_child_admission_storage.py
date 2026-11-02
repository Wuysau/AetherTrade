"""Real 0031 application, unauthorized-write and immutable-history probes."""
import pytest
import psycopg
from psycopg import sql
from integration.test_local_child_storage import child_storage
from integration.test_program_commands_postgres import database
from integration.support_ip_03 import isolated_postgres, ROOT


@pytest.fixture(scope='module')
def admission_storage(child_storage):
    with psycopg.connect(child_storage) as conn:
        conn.execute((ROOT/'migrations/0031_local_child_admission.sql').read_text(encoding='utf-8'))
    return child_storage


def test_invalid_child_write_cannot_commit_or_leave_rows(admission_storage):
    with psycopg.connect(admission_storage) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO ledger.local_child_writes(scope_id,command_id,sequence,payload,transaction_id) VALUES ('invalid','invalid',1,'{}','invalid')")
            conn.commit()
        conn.rollback()
        assert conn.execute('SELECT count(*) FROM ledger.local_child_writes').fetchone()==(0,)


@pytest.mark.parametrize('table',['local_child_writes','local_child_dispatch_marks',
    'local_child_permit_consumptions','local_child_unsent_aborts'])
def test_decision_role_cannot_insert_admission_rows(admission_storage,table):
    with psycopg.connect(admission_storage) as conn:
        conn.execute('SET ROLE ledger_decision')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql.SQL('INSERT INTO ledger.{} DEFAULT VALUES').format(sql.Identifier(table)))


@pytest.mark.parametrize('table',['local_child_writes','local_child_dispatch_marks',
    'local_child_permit_consumptions','local_child_unsent_aborts'])
def test_admin_cannot_erase_admission_history(admission_storage,table):
    with psycopg.connect(admission_storage) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(sql.SQL('TRUNCATE ledger.{}').format(sql.Identifier(table)))
