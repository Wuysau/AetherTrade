"""Static review of the dormant local-child storage/admission candidates.

No SQL is executed. These checks preserve additive, append-only and
least-privilege properties; real migration, trigger, role and concurrency
execution remains NOT_RUN until the shared DEV scheduler is available.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / 'migrations'


def read(name):
    return (MIGRATIONS / name).read_text(encoding='utf-8')


def test_migration_registry_is_contiguous_and_candidates_are_explicit():
    names = sorted(p.name for p in MIGRATIONS.glob('*.sql'))
    numbers = [int(name[:4]) for name in names]
    assert numbers == list(range(1, max(numbers) + 1))
    assert '0030_local_child_commitments.sql' in names
    assert '0031_local_child_admission.sql' in names


def test_storage_candidate_preserves_parent_fk_and_read_only_grants():
    sql = read('0030_local_child_commitments.sql')
    assert sql.startswith('-- Dormant local-child source storage')
    assert 'BEGIN;' in sql and 'COMMIT;' in sql
    assert 'FOREIGN KEY(scope_id,activation_record_id,activation_sequence)' in sql
    assert 'REVOKE ALL ON ledger.local_child_commitments_v1 FROM PUBLIC' in sql
    assert 'GRANT SELECT ON ledger.local_child_commitments_v1 TO ledger_owner' in sql
    assert 'GRANT INSERT' not in sql and 'GRANT UPDATE' not in sql
    assert 'DROP TABLE' not in sql and 'TRUNCATE ' not in sql.replace('OR TRUNCATE', '')


def test_admission_candidate_is_additive_append_only_and_single_use():
    sql = read('0031_local_child_admission.sql')
    assert sql.startswith('-- CANDIDATE')
    assert 'NOT APPLIED' in sql and 'NOT DB-VERIFIED' in sql
    assert 'BEGIN;' in sql and sql.rstrip().endswith('COMMIT;')
    for table in ('local_child_writes', 'local_child_dispatch_marks',
                  'local_child_permit_consumptions', 'local_child_unsent_aborts'):
        assert 'CREATE TABLE ledger.' + table in sql
    # No destructive or authority-widening shortcuts.
    assert 'DROP TABLE' not in sql
    assert 'DROP ROLE' not in sql
    assert 'TO PUBLIC' not in sql
    assert not re.search(r'GRANT\s+ALL', sql, re.IGNORECASE)
    # Required protections are present, not merely documented.
    assert 'DEFERRABLE INITIALLY DEFERRED' in sql
    assert 'local child quantity pool exceeded' in sql
    assert 'unused current local child permit required' in sql
    assert 'local child activation source required' in sql
    assert 'FOR UPDATE' not in sql  # admission runs inside the existing mutex
    assert 'LOCAL_CHILD_COMMAND_READY' in sql
    # Only the existing owner identity receives the insert privilege.
    grants = re.findall(r'GRANT INSERT ON ([^;]+);', sql)
    assert grants and all('ledger_owner' in grant for grant in grants)


def test_admission_code_is_only_composed_by_explicit_host_modules():
    allowed = {
        'src/trading_system/execution/local_child_admission.py',
        'src/trading_system/execution/local_child_current.py',
        'src/trading_system/execution/local_child_original.py',
        'src/trading_system/execution/local_child_observation.py',
        'src/trading_system/runtime/local_child_paper_host.py',
    }
    hits = set()
    for path in (ROOT / 'src').rglob('*.py'):
        if 'local_child_admission' in path.read_text(encoding='utf-8'):
            hits.add(path.relative_to(ROOT).as_posix())
    assert hits <= allowed
    # No server route, startup module or package initializer activates it.
    for relative in ('src/trading_system/server/app.py',
                     'src/trading_system/server/paper_runtime.py'):
        text = (ROOT / relative).read_text(encoding='utf-8')
        assert 'local_child_admission' not in text
        assert 'local_child_paper_host' not in text
