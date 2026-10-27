from pathlib import Path
import gzip,json,os
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo
base=Path('/handoff/evidence')
results=[]
options=conninfo_to_dict(os.environ['PAPER_CI_ADMIN_DSN'])
for path in sorted(base.rglob('summary.json')):
    report=json.loads(path.read_text())
    for suite in report['suites']:
        if suite['suite']=='console': continue
        connection=psycopg.connect(make_conninfo(**{**options,'dbname':suite['database']}))
        with connection as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            tables=conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname='ledger' OR (schemaname='public' AND tablename LIKE 'paper_engine_%') ORDER BY 1,2").fetchall()
            data={}
            for schema,table in tables:
                rows=conn.execute(sql.SQL('SELECT row_to_json(t) FROM {}.{} t').format(sql.Identifier(schema),sql.Identifier(table))).fetchall()
                data[schema+'.'+table]=sorted((r[0] for r in rows),key=lambda r:json.dumps(r,sort_keys=True))
            results.append({'source_commit':report['commit'],'suite':suite['suite'],'database':suite['database'],'tables':data})
document={'classification':'FINAL_SYNTHETIC_SQL_SNAPSHOTS_ONLY_NOT_A_BEFORE_AFTER_EVENT_TRACE','snapshots':results}
with gzip.open(base/'synthetic-ledgers.json.gz','wt',encoding='utf-8') as output:
    json.dump(document,output,sort_keys=True,separators=(',',':'))
print(json.dumps({'databases':len(results),'table_rows':sum(len(rows) for r in results for rows in r['tables'].values())}))
