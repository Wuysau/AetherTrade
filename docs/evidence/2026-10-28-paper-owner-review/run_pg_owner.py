from pathlib import Path
import json,os,subprocess,sys
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import dotenv_values
from psycopg.conninfo import make_conninfo
root=Path('D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-execution')
cfg=dotenv_values(root/'.runtime/paper-postgres.env')
meta=json.loads((root/'.runtime/paper-postgres.json').read_text(encoding='utf-8'))
assert meta['port']==55433
env=dict(os.environ,PYTHONIOENCODING='utf-8',PAPER_RUNTIME_ISOLATED_DATABASE='YES',PAPER_RUNTIME_ADMIN_DSN=make_conninfo(host='127.0.0.1',port=meta['port'],dbname=cfg['POSTGRES_DB'],user=cfg['POSTGRES_USER'],password=cfg['POSTGRES_PASSWORD']))
result=subprocess.run([sys.executable,'-m','pytest','-q','--tb=short',*sys.argv[1:]],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
name='owner-probe-'+__import__('uuid').uuid4().hex[:8]
Path('.runtime/'+name+'.log').write_bytes(result.stdout)
print(name, result.returncode)
print(result.stdout.decode('utf-8',errors='replace')[-14000:])
raise SystemExit(result.returncode)
