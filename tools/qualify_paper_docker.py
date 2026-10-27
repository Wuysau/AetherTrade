"""Run fixed committed PAPER tests in fresh disposable Linux/PostgreSQL containers.

No existing database/container is discovered, reused, deleted, or stopped. Secrets
stay in the ignored run directory; only the evidence subdirectory is publishable.
The test runner shares the new database container's network namespace, with no
published host port. The new runner is stopped; containers remain for inspection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True, help='Exact committed source to test')
    parser.add_argument('--timeout', type=int, default=5400, help='Whole run deadline in seconds')
    parser.add_argument('--python-suite', action='store_true', help='Also run the ordinary full Python suite (DB skips remain explicit)')
    args = parser.parse_args(argv)
    if args.timeout < 60 or args.timeout > 14400:
        parser.error('--timeout must be between 60 and 14400 seconds')
    commit = subprocess.check_output(['git', 'rev-parse', '--verify', args.commit + '^{commit}'], cwd=ROOT, text=True).strip()
    tag = secrets.token_hex(6)
    run = ROOT / '.runtime' / ('paper-qualification-' + tag)
    run.mkdir(parents=True, exist_ok=False)
    if subprocess.run(['git', 'check-ignore', '-q', str(run.relative_to(ROOT)) + '/probe'], cwd=ROOT).returncode:
        raise SystemExit('Run directory must be ignored by Git')
    evidence = run / 'evidence'
    evidence.mkdir()
    pg = 'aethertrade-qualification-pg-' + tag
    runner = 'aethertrade-qualification-runner-' + tag
    password, marker = secrets.token_hex(32), secrets.token_hex(32)
    env = run / 'container.env'
    env.write_text('POSTGRES_USER=paper_admin\nPOSTGRES_DB=paper_ci_admin\nPOSTGRES_PASSWORD=' + password
                   + '\nPAPER_CI_ISOLATED=YES\nPAPER_CI_MARKER=' + marker
                   + '\nPAPER_CI_ADMIN_DSN=host=127.0.0.1 port=5432 dbname=paper_ci_admin user=paper_admin password=' + password
                   + '\nGITHUB_SHA=' + commit + '\n', encoding='utf-8')
    os.chmod(env, 0o600)
    manifest = {'source_commit': commit, 'started_utc': datetime.now(timezone.utc).isoformat(),
                'postgres_container': pg, 'runner_container': runner, 'commands': [],
                'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'scope': 'LOCAL_PAPER_ONLY_NOT_VENUE_QUALIFICATION', 'deadline_seconds': args.timeout}
    deadline = time.monotonic() + args.timeout

    def invoke(command, name, timeout=600, check=True):
        remaining = min(timeout, int(deadline - time.monotonic()))
        if remaining <= 0:
            raise TimeoutError('Whole run deadline expired')
        record = {'argv': command, 'log': name + '.txt', 'timeout_seconds': remaining}
        manifest['commands'].append(record)
        with (evidence / record['log']).open('wb') as output:
            try:
                completed = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                           timeout=remaining, check=False)
                record['exit_code'] = completed.returncode
            except subprocess.TimeoutExpired:
                record['exit_code'] = 124
        print(name + ': exit ' + str(record['exit_code']), flush=True)
        if check and record['exit_code']:
            raise RuntimeError(name + ' failed; inspect ignored evidence')
        return record['exit_code']

    status = 1
    try:
        invoke(['git', 'bundle', 'create', str(run / 'source.bundle'), 'HEAD', commit], 'bundle')
        invoke(['docker', 'run', '-d', '--name', pg, '--env-file', str(env), 'postgres:17-alpine'], 'postgres-start')
        for _ in range(60):
            ready = subprocess.run(['docker', 'exec', pg, 'pg_isready', '-U', 'paper_admin', '-d', 'paper_ci_admin'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Fresh PostgreSQL readiness deadline expired')
        invoke(['docker', 'run', '-d', '--name', runner, '--network', 'container:' + pg,
                '--env-file', str(env), '--mount', 'type=bind,source=' + str(run) + ',target=/handoff',
                'python:3.12-slim', 'sleep', '14400'], 'runner-start')
        invoke(['docker', 'inspect', '--format', '{{.Image}}', pg, runner], 'container-images')
        invoke(['docker', 'exec', runner, 'sh', '-c',
                'apt-get update && apt-get install -y --no-install-recommends git'], 'linux-dependencies')
        invoke(['docker', 'exec', runner, 'git', 'clone', '/handoff/source.bundle', '/tmp/source'], 'source-clone')
        invoke(['docker', 'exec', runner, 'git', '-C', '/tmp/source', 'checkout', '--detach', commit], 'source-checkout')
        invoke(['docker', 'exec', '-w', '/tmp/source', runner, 'python', '-m', 'pip', 'install', '-e', '.[test]'], 'python-dependencies', 900)
        invoke(['docker', 'exec', '-w', '/tmp/source', runner, 'python', 'tools/ci_paper_postgres.py', '--initialize'], 'initialize-marker')
        pg_exit = invoke(['docker', 'exec', '-w', '/tmp/source', runner, 'python', 'tools/ci_paper_postgres.py',
                          '--evidence-dir', '/handoff/evidence/postgres'], 'postgres-suites', 4200, check=False)
        python_exit = None
        if args.python_suite:
            # The mandatory PG runner separately executes DB suites with isolation.
            python_exit = invoke(['docker', 'exec', '-w', '/tmp/source', runner, 'python', '-m', 'pytest', '-q',
                                  '--ignore=tests/integration/test_paper_engine_postgres.py',
                                  '--ignore=tests/integration/test_unified_paper_runtime.py',
                                  '--ignore=tests/integration/test_unified_runtime_faults.py',
                                  '--tb=short', '--junitxml=/handoff/evidence/python-suite.xml'], 'python-suite', 1200, check=False)
        invoke(['docker', 'exec', runner, 'python', '-m', 'pip', 'freeze'], 'resolved-dependencies')
        status = int(pg_exit != 0 or python_exit not in (None, 0))
        manifest['exit_code'] = status
    except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        # Never print process environment, DSNs, or arbitrary driver exceptions.
        manifest['failure_type'] = type(exc).__name__
        manifest['exit_code'] = 1
        print('Qualification failed; see command exit codes in evidence.', flush=True)
    finally:
        # A timed out docker exec can leave a test process running. Stop only the
        # newly created runner; PostgreSQL and every pre-existing resource remain.
        if any(c['argv'][:3] == ['docker', 'run', '-d'] and runner in c['argv'] and c.get('exit_code') == 0
               for c in manifest['commands']):
            try:
                stop_command = ['docker', 'stop', '--time', '10', runner]
                with (evidence / 'runner-stop.txt').open('wb') as output:
                    stopped = subprocess.run(stop_command, stdout=output, stderr=subprocess.STDOUT, timeout=30, check=False)
                manifest['commands'].append({'argv': stop_command, 'exit_code': stopped.returncode,
                                             'timeout_seconds': 30, 'log': 'runner-stop.txt'})
            except (OSError, subprocess.TimeoutExpired):
                manifest['runner_stop'] = 'NOT_CONFIRMED'
        manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
        manifest['files_sha256'] = {str(p.relative_to(evidence)).replace('\\', '/'):
                                    hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted(evidence.rglob('*')) if p.is_file()}
        (evidence / 'launcher-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        print('Evidence: ' + str(evidence), flush=True)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
