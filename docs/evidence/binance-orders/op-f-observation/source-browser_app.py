from pathlib import Path
import sys,json
sys.path[:0]=[str(Path.cwd()/'src'),str(Path.cwd()/'tests')]
import psycopg
from psycopg.conninfo import conninfo_to_dict,make_conninfo
from trading_system.server.app import create_app
from trading_system.server.config import Settings
from trading_system.server.order_observation import OrderObservationService
from trading_system.storage.models import decode,scope_id
from trading_system.contracts import types as t
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.storage.uow import UnitOfWork
from trading_system.storage.order_programs import OrderProgramRepository
from dataclasses import replace
import uvicorn
run=Path('.runtime/opf-pg-31bf6df')
entries=dict(line.split('=',1) for line in (run/'container.env').read_text().splitlines())
summary=json.loads(next((run/'evidence/d515').rglob('summary.json')).read_text())
values=conninfo_to_dict(entries['PAPER_CI_ADMIN_DSN']);values.update(host='127.0.0.1',port='15497',dbname=summary['suites'][1]['database'])
dsn=make_conninfo(**values)
with psycopg.connect(dsn) as conn:
    raw_scopes=conn.execute("SELECT DISTINCT payload#>'{context,scopes}' FROM ledger.order_program_intakes").fetchall()
scopes=tuple(decode(t.ScopeKey,scope) for row in raw_scopes for scope in row[0])
def connect():
    conn=psycopg.connect(dsn);conn.execute('SET ROLE ledger_owner');conn.commit();return conn
# Explicit synthetic PROPOSAL fixture only. No financial receipt, writes or fills.
raw=json.loads(Path('tests/fixtures/orders-v2/program.paper.json').read_text())
raw['program_id']='OPF-SYNTHETIC-PLAN-LIMIT81490';raw['template']='BATCH'
entry=raw['nodes'][0];entry['order']['price'].update(kind='FIXED_LIMIT',value='81490');entry['order']['tif']['kind']='GTC';entry['order']['quantity'].update(kind='BASE_QUANTITY',value='0.049',source_node=None)
stop=json.loads(json.dumps(entry));stop['node_id']='protect-81330';stop['role']='LEG';stop['order']['side']='SELL';stop['order']['price'].update(kind='MARKET',value=None);stop['order']['trigger'].update(kind='STATIC',purpose='STOP_LOSS',source='LAST',price='81330',price_protect=False)
raw['nodes'].append(stop)
program=VersionedOrderCodec().decode('order-program',raw)
with UnitOfWork(connect).transaction(scopes[0]) as tx:OrderProgramRepository(tx).save_program(program)
settings=Settings(database_url='sqlite:///'+str((run/'browser.sqlite').resolve()).replace('\\','/'),market_enabled=False,automation_enabled=False,testing=True,port=8897,allowed_origins=('http://127.0.0.1:8897',),model_key_path=run/'no-model.key',web_dist=Path('web/dist').resolve())
app=create_app(settings,order_observer=OrderObservationService(connect,scopes))
uvicorn.run(app,host='127.0.0.1',port=8897,proxy_headers=False,server_header=False)
