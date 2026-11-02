"""Serial SQL regression on fixed DEV; existing application databases untouched.

Windows opt-in is limited to SQL fixtures. Linux-specific process probes retain
their original guards. This entry is not full OP-G or venue qualification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from ci_paper_postgres import SUITES, child_environment, result_counts

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--suite', action='append')
    args = parser.parse_args()
    catalog = list(SUITES) + [('local-child-storage', '03', 'tests/integration/test_local_child_storage.py')]
    if args.suite and set(args.suite) - {s[0] for s in catalog}:
        parser.error('unknown suite')
    selected = [s for s in catalog if not args.suite or s[0] in args.suite]
    args.evidence_dir.mkdir(parents=True, exist_ok=False)
    report = {'source_sha': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'platform': sys.platform, 'status': 'RUNNING', 'suites': [],
              'scope': 'FIXED_DEV_SQL_REGRESSION_NOT_FULL_OP_G', 'started_at': time.time()}
    def save():
        (args.evidence_dir/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    save()
    password = Path('D:/Projects/Python/AetherTrade/.secrets/dev_admin_password').read_text().strip()
    base = dict(host='127.0.0.1',port=55432,user='aether_dev_admin',password=password,connect_timeout=5)
    tag = secrets.token_hex(6)
    with psycopg.connect(**base, dbname='postgres', autocommit=True) as admin:
        report['server_version'] = admin.execute("SELECT current_setting('server_version_num')").fetchone()[0]
        report['protected_databases'] = admin.execute("SELECT datname,oid FROM pg_database WHERE datname IN ('paper_runtime_dev','paper_engine_dev','paper_control_dev') ORDER BY datname").fetchall()
        if not admin.execute('SELECT pg_try_advisory_lock(20261102,55432)').fetchone()[0]:
            raise RuntimeError('another fixed DEV test batch owns the lock')
        roles_before = {r[0] for r in admin.execute('SELECT rolname FROM pg_roles')}
        for index,(name,number,testfile) in enumerate(selected):
            entry = {'suite':name,'testfile':testfile,'status':'RUNNING'}
            report['suites'].append(entry)
            if number in ('PAPER_RUNTIME','CONSOLE'):
                entry.update(status='NOT_RUN',reason='Per-case database provisioning needs bounded registration/cleanup adaptation')
                save()
                continue
            dbname = ('paper_engine_' if number=='PAPER_ENGINE' else f'ip{number.lower()}_test_')+tag+'_'+str(index)
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(dbname)))
            oid = admin.execute('SELECT oid FROM pg_database WHERE datname=%s',(dbname,)).fetchone()[0]
            entry.update(database=dbname, database_oid=oid)
            save()
            env = child_environment(os.environ)
            env['AETHER_FIXED_DEV_DATABASE_TESTS']='YES'
            prefix = 'PAPER_ENGINE' if number=='PAPER_ENGINE' else 'IP'+number
            env[prefix+'_ISOLATED_DATABASE']='YES'
            env[prefix+'_DATABASE_DSN']=make_conninfo(**base,dbname=dbname)
            xml=args.evidence_dir/(name+'.xml'); log=args.evidence_dir/(name+'.log')
            command=[sys.executable,'-m','pytest','-q','--tb=short','-p','no:cacheprovider',f'--junitxml={xml}',testfile]
            entry['command']=command
            with log.open('wb') as output:
                try:
                    result=subprocess.run(command,cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=600)
                    code=result.returncode
                except subprocess.TimeoutExpired:
                    entry.update(status='BLOCKED',reason='TIMEOUT: retained database; batch stopped')
                    report['status']='BLOCKED'; save(); return 78
            entry['exit_code']=code
            try:
                entry['results']=result_counts(xml,code)
                entry['status']='PASS' if entry['results']['accepted'] else 'FAIL'
            except Exception:
                entry['status']='FAIL'
                entry['reason']='Missing or malformed JUnit results'
            for f in (xml,log):
                if f.exists(): entry[f.suffix+'_sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
            actual=admin.execute('SELECT oid FROM pg_database WHERE datname=%s',(dbname,)).fetchone()
            active=admin.execute('SELECT count(*) FROM pg_stat_activity WHERE datid=%s',(oid,)).fetchone()[0]
            if actual != (oid,) or active:
                entry['cleanup']='BLOCKED: identity changed or connections remain'
                report['status']='BLOCKED'; save(); return 78
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(dbname)))
            entry['cleanup']='REMOVED' if admin.execute('SELECT oid FROM pg_database WHERE datname=%s',(dbname,)).fetchone() is None else 'BLOCKED'
            report['new_roles_retained']=sorted({r[0] for r in admin.execute('SELECT rolname FROM pg_roles')}-roles_before)
            save()
            print(name+': '+entry['status'],flush=True)
        report['status']='PASS' if all(s['status']=='PASS' for s in report['suites']) else 'INCOMPLETE'
        report['finished_at']=time.time(); save()
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (psycopg.Error,OSError,RuntimeError) as exc:
        # Never print driver messages or connection strings with secrets.
        print('Fixed DEV batch stopped: '+type(exc).__name__,file=sys.stderr)
        raise SystemExit(2)
