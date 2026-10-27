"""Mandatory Linux/PostgreSQL PAPER checks against an explicitly disposable cluster.

This is an engineering test runner, not venue or TESTNET qualification. It never
discovers credentials, deletes databases, or connects to trading services. Each
test module gets a new database because module fixtures install the ledger once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote
import xml.etree.ElementTree as ET

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    ('ledger', '03', 'tests/integration/test_ledger_transactions.py'),
    ('reservations', '07', 'tests/integration/test_reservations.py'),
    ('risk-locks', '08', 'tests/integration/test_persistent_risk_locks.py'),
    ('dispatch', '10', 'tests/integration/test_durable_dispatch.py'),
    ('child-cancel', '10', 'tests/integration/test_child_and_cancel_commands.py'),
    ('execution-evidence', '12', 'tests/integration/test_execution_evidence.py'),
    ('recovery', '13', 'tests/integration/test_recovery_gate.py'),
    ('protection', '15', 'tests/integration/test_protection_lifecycle.py'),
    ('ownership', '16', 'tests/integration/test_ownership_transfer.py'),
    ('paper-engine', 'PAPER_ENGINE', 'tests/integration/test_paper_engine_postgres.py'),
    ('console', 'CONSOLE', 'tests/server/test_store.py::test_postgresql_race_restart_and_concurrent_transaction_receipts'),
)


class GateError(RuntimeError):
    pass


def result_counts(path: Path, returncode: int) -> dict:
    """An absent result, any failure, or any skipped safety case fails the gate."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise GateError('missing or malformed PostgreSQL test results') from exc
    cases = list(root.iter('testcase'))
    if not cases:
        raise GateError('PostgreSQL suite did not execute any tests')
    counts = {'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}
    for case in cases:
        if case.find('failure') is not None:
            counts['failed'] += 1
        elif case.find('error') is not None:
            counts['errors'] += 1
        elif case.find('skipped') is not None:
            counts['skipped'] += 1
        else:
            counts['passed'] += 1
    counts['exit_code'] = returncode
    counts['accepted'] = returncode == 0 and not any(counts[k] for k in ('failed', 'errors', 'skipped'))
    return counts


def child_environment(environ) -> dict:
    """Do not inherit account/model credentials or test-selection overrides."""
    allowed = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TMPDIR',
               'TMP', 'TEMP', 'SYSTEMROOT', 'VIRTUAL_ENV', 'CI'}
    return {key: value for key, value in environ.items() if key in allowed}


def admin_connection(*, initialize=False):
    if sys.platform != 'linux' or os.environ.get('PAPER_CI_ISOLATED') != 'YES':
        raise GateError('isolated Linux PostgreSQL must be explicitly supplied')
    raw, marker = os.environ.get('PAPER_CI_ADMIN_DSN'), os.environ.get('PAPER_CI_MARKER')
    if not raw or not marker or not 16 <= len(marker) <= 100:
        raise GateError('disposable admin DSN and isolation marker are required')
    values = conninfo_to_dict(raw)
    if (values.get('dbname') != 'paper_ci_admin' or values.get('user') != 'paper_admin'
            or values.get('host') not in ('127.0.0.1', 'localhost', 'host.docker.internal')):
        raise GateError('admin DSN must select the dedicated local paper_ci_admin database')
    connection = psycopg.connect(raw, autocommit=True)
    try:
        current, user, version = connection.execute(
            "SELECT current_database(), current_user, current_setting('server_version_num')::int").fetchone()
        if current != 'paper_ci_admin' or user != 'paper_admin' or version < 170000:
            raise GateError('database identity or PostgreSQL version is not the isolated test target')
        if connection.execute("SELECT to_regclass('public.paper_ci_isolation_marker')").fetchone()[0] is None:
            if not initialize:
                raise GateError('independently provisioned disposable cluster marker is missing')
            objects = connection.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                         "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')").fetchone()[0]
            routines = connection.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                                          "WHERE n.nspname='public'").fetchone()[0]
            schemas = connection.execute("SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') "
                                         "AND nspname NOT LIKE 'pg_%'").fetchone()[0]
            if objects or routines or schemas:
                raise GateError('refusing to initialize a database with existing application objects')
            connection.execute('CREATE TABLE public.paper_ci_isolation_marker(marker text NOT NULL)')
            connection.execute('INSERT INTO public.paper_ci_isolation_marker VALUES (%s)', (marker,))
        row = connection.execute('SELECT marker FROM public.paper_ci_isolation_marker').fetchall()
        if row != [(marker,)]:
            raise GateError('disposable cluster marker does not match')
        return connection, values, version
    except BaseException:
        connection.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=[entry[0] for entry in SUITES], help='Run one diagnostic suite; not full qualification')
    parser.add_argument('--evidence-dir', type=Path, default=Path('.runtime/paper-pg-ci'))
    parser.add_argument('--initialize', action='store_true', help='Mark a fresh, empty, explicitly isolated admin database')
    args = parser.parse_args(argv)
    try:
        connection, values, version = admin_connection(initialize=args.initialize)
        try:
            if args.initialize:
                print('Verified disposable PostgreSQL marker.')
                return 0
            commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()
            if status:
                raise GateError('commit the source and tests before recording PostgreSQL evidence')
            expected = os.environ.get('GITHUB_SHA')
            if expected and commit != expected:
                raise GateError('checked-out commit differs from the CI commit')
            evidence_root = args.evidence_dir.resolve()
            evidence_root.mkdir(parents=True, exist_ok=True)
            selected = [entry for entry in SUITES if not args.suite or entry[0] == args.suite]
            missing = [path for _, _, path in selected if not (ROOT / path.split('::')[0]).is_file()]
            if missing:
                raise GateError('required PostgreSQL suite is missing: ' + ', '.join(missing))
            migrations = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted((ROOT / 'migrations').glob('*.sql'))}
            summary = {'commit': commit, 'postgres_version_num': version, 'migrations': migrations,
                       'qualification': 'NOT_RUN' if args.suite else 'INCOMPLETE', 'suites': []}
            tag = secrets.token_hex(6)
            # A retry never overwrites an earlier run's original evidence.
            evidence = evidence_root / (commit[:12] + '-' + tag)
            evidence.mkdir(exist_ok=False)
            for name, number, testfile in selected:
                dbname = ('aethertrade_tests' if number == 'CONSOLE' else
                          ('paper_engine_' if number == 'PAPER_ENGINE' else f'ip{number.lower()}_test_') + tag + '_' + name.replace('-', '_'))
                if len(dbname) > 63:
                    raise GateError('generated database name is too long')
                if connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (dbname,)).fetchone():
                    raise GateError('fresh test database name unexpectedly exists')
                connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(dbname)))
                # Never inherit an unrelated account/database opt-in from the
                # runner's environment, even when only one suite is selected.
                env = child_environment(os.environ)
                # Never print the DSN: it can contain a password even in a test cluster.
                dsn = make_conninfo(**{**values, 'dbname': dbname})
                if number == 'CONSOLE':
                    # The existing console fixture intentionally reads a relative
                    # env file. Supply it in a fresh scratch cwd, never overwrite
                    # the developer's .runtime/postgres-test.env.
                    cwd = evidence / 'console-cwd'
                    (cwd / '.runtime').mkdir(parents=True, exist_ok=False)
                    url = ('postgresql+psycopg://' + quote(values['user'], safe='') + ':'
                           + quote(values.get('password', ''), safe='') + '@' + values['host']
                           + ':' + str(values.get('port', 5432)) + '/' + dbname)
                    (cwd / '.runtime/postgres-test.env').write_text('AETHERTRADE_TEST_DATABASE_URL=' + url + '\n', encoding='utf-8')
                    env['AETHERTRADE_RUN_POSTGRES_TESTS'] = '1'
                else:
                    cwd = ROOT
                    env[f'{number}_ISOLATED_DATABASE' if number == 'PAPER_ENGINE' else f'IP{number}_ISOLATED_DATABASE'] = 'YES'
                    env[f'{number}_DATABASE_DSN' if number == 'PAPER_ENGINE' else f'IP{number}_DATABASE_DSN'] = dsn
                xml, log = evidence / f'{name}.xml', evidence / f'{name}.log'
                absolute_test = str(ROOT / testfile.split('::')[0]) + ('::' + testfile.split('::', 1)[1] if '::' in testfile else '')
                command = [sys.executable, '-m', 'pytest', '-q', '--tb=short', f'--rootdir={ROOT}', f'--junitxml={xml}', absolute_test]
                with log.open('wb') as output:
                    try:
                        outcome = subprocess.run(command, cwd=cwd, env=env, stdout=output,
                                                 stderr=subprocess.STDOUT, timeout=300, check=False)
                        exit_code = outcome.returncode
                    except subprocess.TimeoutExpired:
                        exit_code = 124
                try:
                    counts = result_counts(xml, exit_code)
                except GateError:
                    counts = {'passed': 0, 'failed': 0, 'errors': 1, 'skipped': 0,
                              'exit_code': exit_code, 'accepted': False}
                entry = {'suite': name, 'database': dbname, 'command': command,
                         'isolation_flag': ('PAPER_ENGINE_ISOLATED_DATABASE' if number == 'PAPER_ENGINE' else
                                            'AETHERTRADE_RUN_POSTGRES_TESTS' if number == 'CONSOLE' else f'IP{number}_ISOLATED_DATABASE'),
                         'results': counts, 'output': log.name, 'junit': xml.name}
                summary['suites'].append(entry)
                print(f"{name}: {counts['passed']} passed, {counts['failed']} failed, "
                      f"{counts['errors']} errors, {counts['skipped']} skipped; exit {exit_code}", flush=True)
            accepted = all(s['results']['accepted'] for s in summary['suites'])
            if not args.suite and accepted:
                summary['qualification'] = 'POSTGRES_TESTS_PASSED_NOT_VENUE_QUALIFICATION'
            (evidence / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
            return 0 if accepted else 1
        finally:
            connection.close()
    except GateError as exc:
        print('PAPER PostgreSQL checks not accepted:', str(exc), file=sys.stderr)
        return 2
    except (psycopg.Error, OSError, ValueError) as exc:
        # Avoid exception strings: drivers may include DSNs or server details.
        print('PAPER PostgreSQL checks not accepted:', type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
