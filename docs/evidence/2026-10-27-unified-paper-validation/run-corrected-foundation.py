from pathlib import Path
import subprocess,json,time,hashlib
root=Path(__file__).resolve().parents[1]
evidence=root/'.runtime/paper-qualification-6f1db19c6a28/evidence/corrected'
evidence.mkdir(exist_ok=False)
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
runner='aethertrade-qualification-runner-6f1db19c6a28'
commands=[]
status=1
def call(name,args,timeout=900):
    command=['docker','exec','-e','GITHUB_SHA='+commit,'-w','/tmp/source',runner]+args
    record={'command':command,'log':name+'.txt','timeout_seconds':timeout}
    commands.append(record)
    with (evidence/record['log']).open('wb') as out:
        try:
            result=subprocess.run(command,stdout=out,stderr=subprocess.STDOUT,timeout=timeout,check=False)
            record['exit_code']=result.returncode
        except subprocess.TimeoutExpired:
            record['exit_code']=124
    print(name+': exit '+str(record['exit_code']),flush=True)
    return record['exit_code']
try:
    init=call('initialize-marker',['python','tools/ci_paper_postgres.py','--initialize'])
    if init==0:
        pg=call('postgres-suites',['python','tools/ci_paper_postgres.py','--evidence-dir','/handoff/evidence/corrected/postgres'],4200)
        ordinary=call('python-suite',['python','-m','pytest','-q','--ignore=tests/integration/test_paper_engine_postgres.py','--tb=short','--junitxml=/handoff/evidence/corrected/python-suite.xml'],1200)
        call('dependencies',['python','-m','pip','freeze'])
        status=int(pg!=0 or ordinary!=0)
finally:
    with (evidence/'images.txt').open('wb') as out:
        subprocess.run(['docker','inspect','--format','{{.Name}} {{.Image}}','aethertrade-qualification-pg-6f1db19c6a28',runner],stdout=out,check=False,timeout=30)
    with (evidence/'runner-stop.txt').open('wb') as out:
        subprocess.run(['docker','stop','--time','10',runner],stdout=out,check=False,timeout=30)
    manifest={'source_commit':commit,'exit_code':status,'commands':commands,'scope':'LOCAL_LINUX_POSTGRES_PAPER_NOT_VENUE_QUALIFICATION',
              'initial_gate_failure':'97c4f9d initialization rejected default5432 due get_parameters omission; corrected source differs only harness and five restricted-login probes; cluster remained empty before corrected initialize.',
              'files_sha256':{str(p.relative_to(evidence)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(evidence.rglob('*')) if p.is_file()}}
    (evidence/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
raise SystemExit(status)
