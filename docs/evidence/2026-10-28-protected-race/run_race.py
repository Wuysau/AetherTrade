from pathlib import Path
import json,os,subprocess,sys
from dotenv import dotenv_values
from psycopg.conninfo import make_conninfo
root=Path.cwd()
source=Path('D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-execution')
cfg=dotenv_values(source/'.runtime/paper-postgres.env')
meta=json.loads((source/'.runtime/paper-postgres.json').read_text())
env=dict(os.environ,PAPER_RUNTIME_ISOLATED_DATABASE='YES', PAPER_RUNTIME_ADMIN_DSN=make_conninfo(host='127.0.0.1',port=meta['port'],dbname=cfg['POSTGRES_DB'],user=cfg['POSTGRES_USER'],password=cfg['POSTGRES_PASSWORD']))
paths=['src/trading_system/server/paper_runtime.py','src/trading_system/risk/locked_actions.py','src/trading_system/risk/protection_adjustment.py']
backups={p:(root/p).read_bytes() for p in paths}
try:
 if 'red' in sys.argv:
  for p in paths:
   (root/p).write_bytes(subprocess.check_output(['git','show','366e476:'+p]))
 name='protected-race-'+('red' if 'red' in sys.argv else 'regression' if 'tests/integration/test_unified_paper_runtime.py' in sys.argv else 'green')
 result=subprocess.run([sys.executable,'-m','pytest','-q','--tb=short','tests/integration/test_paper_protected_reduction.py',*[a for a in sys.argv[1:] if a != 'red']],env=env,capture_output=True)
 (root/'.runtime'/f'{name}.log').write_bytes(result.stdout+result.stderr)
 (root/'.runtime'/f'{name}.exit').write_text(str(result.returncode))
 print((result.stdout+result.stderr).decode(errors='replace')[-12000:])
finally:
 for p,raw in backups.items(): (root/p).write_bytes(raw)
