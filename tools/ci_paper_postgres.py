"""Mandatory Linux/PostgreSQL PAPER checks against an explicitly disposable cluster.

This is an engineering test runner, not venue or TESTNET qualification. It never
discovers credentials, deletes databases, or connects to trading services. Each
test module gets a new database because module fixtures install the ledger once.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    ('ledger', '03', 'tests/integration/test_ledger_transactions.py'),
    ('reservations', '07', 'tests/integration/test_reservations.py'),
    ('duplicate-target', '07', 'tests/integration/test_paper_duplicate_target.py'),
    ('risk-locks', '08', 'tests/integration/test_persistent_risk_locks.py'),
    ('group-daily-loss', '08', 'tests/integration/test_group_daily_locks.py'),
    ('dispatch', '10', 'tests/integration/test_durable_dispatch.py'),
    ('child-cancel', '10', 'tests/integration/test_child_and_cancel_commands.py'),
    ('execution-evidence', '12', 'tests/integration/test_execution_evidence.py'),
    ('recovery', '13', 'tests/integration/test_recovery_gate.py'),
    ('protection', '15', 'tests/integration/test_protection_lifecycle.py'),
    ('ownership', '16', 'tests/integration/test_ownership_transfer.py'),
    ('paper-engine', 'PAPER_ENGINE', 'tests/integration/test_paper_engine_postgres.py'),
    ('console', 'PAPER_RUNTIME', 'tests/integration/test_unified_console_contract.py'),
    ('unified-runtime', 'PAPER_RUNTIME', 'tests/integration/test_unified_paper_runtime.py'),
    ('runtime-faults', 'PAPER_RUNTIME', 'tests/integration/test_unified_runtime_faults.py'),
    ('console-contract', 'PAPER_RUNTIME', 'tests/server'),
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


@contextmanager
def clean_libpq_environment():
    """libpq otherwise silently inherits PGHOSTADDR, PGPORT, PGSERVICE, etc."""
    saved = {key: os.environ.pop(key) for key in tuple(os.environ) if key.startswith('PG')}
    try:
        yield
    finally:
        os.environ.update(saved)


def validated_admin_options(raw: str) -> dict:
    try:
        values = conninfo_to_dict(raw)
    except (psycopg.Error, TypeError, ValueError):
        raise GateError('invalid admin connection string') from None
    allowed = {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'connect_timeout'}
    port = values.get('port', '')
    if (set(values) - allowed or values.get('dbname') != 'paper_ci_admin'
            or values.get('user') != 'paper_admin'
            or values.get('host') not in ('127.0.0.1', 'localhost', 'host.docker.internal')
            or not values.get('password') or not port or not port.isascii() or not port.isdecimal()
            or not 1 <= int(port) <= 65535):
        raise GateError('admin DSN must explicitly select the dedicated local test database and port')
    if values['host'] in ('127.0.0.1', 'localhost'):
        address = '127.0.0.1'
    else:
        answers = socket.getaddrinfo(values['host'], int(values['port']), type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses:
            raise GateError('local Docker host did not resolve')
        for candidate in addresses:
            ip = ipaddress.ip_address(candidate)
            if (not (ip.is_private or ip.is_loopback) or ip.is_link_local or ip.is_multicast
                    or ip.is_unspecified or ip.is_reserved):
                raise GateError('Docker host must resolve to a local private address')
        address = addresses[0]
    return {**values, 'hostaddr': address}


def console_database_url(values: dict, dbname: str) -> str:
    """Use the same pinned address and port as the validated admin connection."""
    query = {'hostaddr': values['hostaddr']}
    query.update({key: values[key] for key in ('sslmode', 'connect_timeout') if key in values})
    return ('postgresql+psycopg://' + quote(values['user'], safe='') + ':'
            + quote(values.get('password', ''), safe='') + '@' + values['host']
            + ':' + values['port'] + '/' + dbname + '?' + urlencode(query))


@contextmanager
def console_scratch(values: dict, dbname: str, evidence: Path):
    """Keep the fixture DSN out of uploaded artifacts and remove it on exit."""
    with tempfile.TemporaryDirectory(prefix='paper-console-ci-') as directory:
        cwd = Path(directory).resolve()
        artifact_dir = evidence.resolve()
        if cwd == artifact_dir or artifact_dir in cwd.parents:
            raise GateError('console credential scratch must be outside uploaded evidence')
        (cwd / '.runtime').mkdir(mode=0o700)
        path = cwd / '.runtime/postgres-test.env'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write('AETHERTRADE_TEST_DATABASE_URL=' + console_database_url(values, dbname) + '\n')
        yield cwd


def admin_connection(*, initialize=False):
    if sys.platform != 'linux' or os.environ.get('PAPER_CI_ISOLATED') != 'YES':
        raise GateError('isolated Linux PostgreSQL must be explicitly supplied')
    raw, marker = os.environ.get('PAPER_CI_ADMIN_DSN'), os.environ.get('PAPER_CI_MARKER')
    if not raw or not marker or not 16 <= len(marker) <= 100:
        raise GateError('disposable admin DSN and isolation marker are required')
    values = validated_admin_options(raw)
    with clean_libpq_environment():
        connection = psycopg.connect(make_conninfo(**values), autocommit=True)
    try:
        # libpq omits compiled defaults (notably 5432) from get_parameters().
        # ConnectionInfo.port reports the actual effective numeric port even
        # when the explicit validated input equals that compiled default.
        actual = {**connection.info.get_parameters(), 'port': str(connection.info.port)}
        if any(actual.get(key) != values.get(key) for key in ('host', 'hostaddr', 'port', 'dbname', 'user')):
            raise GateError('effective PostgreSQL endpoint differs from validated endpoint')
        current, user, version = connection.execute(
            "SELECT current_database(), current_user, current_setting('server_version_num')::int").fetchone()
        if current != 'paper_ci_admin' or user != 'paper_admin' or version < 170000:
            raise GateError('database identity or PostgreSQL version is not the isolated test target')
        marker_oid = connection.execute("SELECT to_regclass('public.paper_ci_isolation_marker')::oid").fetchone()[0]
        if marker_oid is None and not initialize:
            raise GateError('independently provisioned disposable cluster marker is missing')
        if marker_oid is not None:
            marker_kind = connection.execute(
                "SELECT c.relkind,pg_get_userbyid(c.relowner) FROM pg_class c WHERE c.oid=%s", (marker_oid,)).fetchone()
            if marker_kind != ('r', 'paper_admin'):
                raise GateError('isolation marker must be an administrator-owned ordinary table')
        # Check on initialization and every later invocation. A marker must not
        # silently turn a subsequently shared database into a disposable one.
        objects = connection.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                     "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f') "
                                     "AND c.oid IS DISTINCT FROM %s::oid", (marker_oid,)).fetchone()[0]
        routines = connection.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                                      "WHERE n.nspname='public'").fetchone()[0]
        types = connection.execute("SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
                                   "WHERE n.nspname='public' AND t.typrelid IS DISTINCT FROM %s::oid "
                                   "AND t.typelem NOT IN (SELECT oid FROM pg_type WHERE typrelid=%s::oid)",
                                   (marker_oid, marker_oid)).fetchone()[0]
        schemas = connection.execute("SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') "
                                     "AND NOT starts_with(nspname,'pg_')").fetchone()[0]
        dependencies = connection.execute(
            "SELECT count(*) FROM pg_depend d WHERE d.refclassid='pg_namespace'::regclass "
            "AND d.refobjid='public'::regnamespace AND d.deptype IN ('n','a','i') "
            "AND NOT (d.classid='pg_class'::regclass AND d.objid IS NOT DISTINCT FROM %s::oid)",
            (marker_oid,)).fetchone()[0]
        if objects or routines or types or schemas or dependencies:
            raise GateError('refusing a database with existing application objects')
        if marker_oid is None:
            if not initialize:
                raise GateError('independently provisioned disposable cluster marker is missing')
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
            missing = [path for _, _, path in selected if not (ROOT / path.split('::')[0]).exists()]
            if missing:
                raise GateError('required PostgreSQL suite is missing: ' + ', '.join(missing))
            migrations = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted((ROOT / 'migrations').glob('*.sql'))}
            summary = {'commit': commit, 'postgres_version_num': version, 'migrations': migrations,
                       'provisioning_sources': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                                for name in ('deploy/db/roles.sql', 'scripts/provision_paper_engine_roles.py')},
                       'qualification': 'NOT_RUN' if args.suite else 'INCOMPLETE', 'suites': []}
            tag = secrets.token_hex(6)
            # A retry never overwrites an earlier run's original evidence.
            evidence = evidence_root / (commit[:12] + '-' + tag)
            evidence.mkdir(exist_ok=False)
            for name, number, testfile in selected:
                dbname = (values['dbname'] if number == 'PAPER_RUNTIME' else 'aethertrade_tests' if number == 'CONSOLE' else
                          ('paper_engine_' if number == 'PAPER_ENGINE' else f'ip{number.lower()}_test_') + tag + '_' + name.replace('-', '_'))
                if len(dbname) > 63:
                    raise GateError('generated database name is too long')
                if number == 'PAPER_RUNTIME':
                    before_databases = {row[0] for row in connection.execute('SELECT datname FROM pg_database').fetchall()}
                else:
                    if connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (dbname,)).fetchone():
                        raise GateError('fresh test database name unexpectedly exists')
                    connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(dbname)))
                # Never inherit an unrelated account/database opt-in from the
                # runner's environment, even when only one suite is selected.
                env = child_environment(os.environ)
                # Never print the DSN: it can contain a password even in a test cluster.
                dsn = make_conninfo(**{**values, 'dbname': dbname})
                with ExitStack() as stack:
                    if number == 'CONSOLE':
                        # GitHub uploads the entire evidence root, not just the
                        # current run's child directory.
                        cwd = stack.enter_context(console_scratch(values, dbname, evidence_root))
                        env['AETHERTRADE_RUN_POSTGRES_TESTS'] = '1'
                    elif number == 'PAPER_RUNTIME':
                        cwd = ROOT
                        env['PAPER_RUNTIME_ISOLATED_DATABASE'] = 'YES'
                        env['PAPER_RUNTIME_ADMIN_DSN'] = dsn
                        env['PAPER_RUNTIME_EVIDENCE_DIR'] = str(evidence / (name + '-states'))
                    else:
                        cwd = ROOT
                        env[f'{number}_ISOLATED_DATABASE' if number == 'PAPER_ENGINE' else f'IP{number}_ISOLATED_DATABASE'] = 'YES'
                        env[f'{number}_DATABASE_DSN' if number == 'PAPER_ENGINE' else f'IP{number}_DATABASE_DSN'] = dsn
                    xml, log = evidence / f'{name}.xml', evidence / f'{name}.log'
                    absolute_test = str(ROOT / testfile.split('::')[0]) + ('::' + testfile.split('::', 1)[1] if '::' in testfile else '')
                    command = [sys.executable, '-m', 'pytest', '-q', '--tb=short', f'--rootdir={ROOT}', f'--junitxml={xml}', absolute_test]
                    if name == 'console-contract':
                        command.extend(['-m', 'paper_runtime'])
                    suite_timeout = 600 if name == 'console-contract' else 300
                    with log.open('wb') as output:
                        try:
                            outcome = subprocess.run(command, cwd=cwd, env=env, stdout=output,
                                                     stderr=subprocess.STDOUT, timeout=suite_timeout, check=False)
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
                                            'PAPER_RUNTIME_ISOLATED_DATABASE' if number == 'PAPER_RUNTIME' else
                                            'AETHERTRADE_RUN_POSTGRES_TESTS' if number == 'CONSOLE' else f'IP{number}_ISOLATED_DATABASE'),
                         'results': counts, 'output': log.name, 'junit': xml.name}
                entry['timeout_seconds'] = suite_timeout
                if number == 'PAPER_RUNTIME':
                    entry['created_databases'] = sorted({row[0] for row in connection.execute(
                        'SELECT datname FROM pg_database').fetchall()} - before_databases)
                    if not entry['created_databases']:
                        counts['accepted'] = False
                    entry['database'] = 'FRESH_PER_CASE_RUNTIME_AND_VENUE_DATABASES'
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
