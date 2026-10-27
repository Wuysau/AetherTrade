"""Linux-only real PostgreSQL probes; fresh databases, no venue/network orders.

Set IP07_ISOLATED_DATABASE=YES and ATOMIC_TEST_ADMIN_DSN to an explicitly
isolated administrator DSN. Databases are retained, never deleted. This script
does not provision credentials, install dependencies, or start PostgreSQL.
"""
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def main():
    if sys.platform != 'linux' or sys.version_info[:2] != (3, 12):
        raise SystemExit('NOT_RUN: Linux Python 3.12 required')
    if os.environ.get('IP07_ISOLATED_DATABASE') != 'YES':
        raise SystemExit('NOT_RUN: explicit isolated database authorization required')
    admin = os.environ.get('ATOMIC_TEST_ADMIN_DSN')
    if not admin:
        raise SystemExit('NOT_RUN: isolated administrator DSN required')
    root = Path(__file__).resolve().parents[1]
    output = root / '.runtime' / ('atomic-' + uuid4().hex)
    output.mkdir(parents=True)
    password = conninfo_to_dict(admin).get('password')
    failures = 0
    for module in ('tests/integration/test_paper_duplicate_target.py', 'tests/integration/test_reservations.py'):
        database = 'ip07_test_atomic_' + uuid4().hex
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute('CREATE DATABASE ' + database)
        label = Path(module).stem
        command = [sys.executable, '-m', 'pytest', '-q', module,
            '--junitxml=' + str(output / (label + '.xml'))]
        env = dict(os.environ, IP07_DATABASE_DSN=make_conninfo(admin, dbname=database))
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        log = result.stdout + result.stderr
        if password:
            log = log.replace(password, '<redacted>')
            xml = output / (label + '.xml')
            if xml.exists():
                xml.write_text(xml.read_text().replace(password, '<redacted>'))
        (output / (label + '.log')).write_text(log)
        (output / (label + '.exit')).write_text(str(result.returncode))
        (output / (label + '.db')).write_text(database)
        print(f'{module}: exit={result.returncode}; database={database}')
        print(log)
        failures += result.returncode != 0
    print('Evidence: ' + str(output))
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
