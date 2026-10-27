from pathlib import Path
import gzip,json,os
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo
root=Path('/handoff/evidence')
base=conninfo_to_dict(os.environ['PAPER_CI_ADMIN_DSN'])
snapshots=[]
for scope in ('runtime-protection-fixed','runtime-final-faults-fixed'):
    report=json.loads(next((root/scope).rglob('summary.json')).read_text())
    for name in report['suites'][0]['created_databases']:
        if not name.startswith(('paper_runtime_','paper_engine_')):continue
        with psycopg.connect(make_conninfo(**dict(base,dbname=name))) as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            tables={}
            for schema,table in conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname='ledger' OR (schemaname='public' AND tablename LIKE 'paper_engine_%') ORDER BY 1,2").fetchall():
                rows=conn.execute(sql.SQL('SELECT row_to_json(r) FROM {}.{} r').format(sql.Identifier(schema),sql.Identifier(table))).fetchall()
                tables[schema+'.'+table]=[row[0] for row in rows]
            snapshots.append({'source_commit':report['commit'],'scope':scope,'database':name,'tables':tables})
with gzip.open(root/'runtime-protection-ledgers.json.gz','wt',encoding='utf-8') as output:
    json.dump({'classification':'FINAL_SYNTHETIC_APPEND_ONLY_HISTORIES_NOT_FABRICATED_BEFORE_AFTER_TRACE','snapshots':snapshots},output,sort_keys=True,separators=(',',':'))
print(json.dumps({'databases':len(snapshots),'table_rows':sum(len(rows) for s in snapshots for rows in s['tables'].values())}))
