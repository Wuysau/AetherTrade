"""Execute real pre-capital-policy source history in a pinned isolated process.

The current funded guard stays enabled. This fixture creates historical issued
records using the exact old Git runtime, then returns readonly recovery inputs.
It never fabricates a proof or applies a later migration before its precondition.
"""
from pathlib import Path
from types import SimpleNamespace
import io,json,os,subprocess,sys,zipfile

from trading_system.contracts import types as t
from trading_system.storage.models import decode
from trading_system.storage.uow import UnitOfWork


VINTAGE='3d13721cbcdc2e35cdd347c610372f13fee03b4f'


def historical_source_case(database,venue,tmp_path):
    root=Path(__file__).resolve().parents[2];checkout=tmp_path/'exact-pre-capital-source'
    checkout.mkdir()
    packed=subprocess.check_output(['git','archive','--format=zip',VINTAGE,'src','tests'],cwd=root)
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        for member in archive.infolist():
            if not (checkout/member.filename).resolve().is_relative_to(checkout.resolve()):
                raise ValueError('historical archive escaped isolated checkout')
        archive.extractall(checkout)
    case_dir=tmp_path/'historical-actual-case';case_dir.mkdir()
    archive_dir=case_dir/'raw-archive'
    script=case_dir/'execute_old_runtime.py'
    script.write_text('''from pathlib import Path
from types import SimpleNamespace
import json,os
from integration.test_paper_market_matcher import case,submit,event
from integration.test_program_fill_source_approval import actual_verifier,source_uow
from trading_system.engine.paper_market import PaperMatcher
from trading_system.execution.program_fill_source import ProgramFillSourceService
from trading_system.contracts.canonical import domain_payload
inputs=json.loads(os.environ['PAPER_HISTORICAL_FIXTURE_INPUT'])
venue=SimpleNamespace(runtime_dsn=inputs['runtime'],event_dsn=inputs['events'])
path=Path(inputs['path'])
c=case(inputs['ledger'],venue,path);submit(c)
PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
verifier,codec=actual_verifier(c,path/'raw-archive')
try:result=ProgramFillSourceService(source_uow(inputs['ledger']),c.scope,verifier=verifier).approve(c.write.command_id)
finally:codec.close()
assert result.status=='APPROVED' and len(result.approvals)==1
print(json.dumps(dict(scope=domain_payload(c.scope),command_id=c.write.command_id,
    clock=domain_payload(c.c.facts.inputs.clock),owner_strategy_id=c.bundle.intent.owner_strategy_id,
    quantity_unit=c.bundle.intent.unit.native_unit,price_currency=c.bundle.archive.rules.quote_asset,
    approvals=[domain_payload(a) for a in result.approvals])))
''',encoding='utf-8')
    env={**os.environ,'PYTHONPATH':os.pathsep.join((str(checkout/'src'),str(checkout/'tests'))),
        'PAPER_HISTORICAL_FIXTURE_INPUT':json.dumps(dict(ledger=database,runtime=venue.runtime_dsn,
            events=venue.event_dsn,path=str(case_dir)))}
    run=subprocess.run([sys.executable,str(script)],cwd=checkout,env=env,capture_output=True,text=True,timeout=120)
    if run.returncode:
        raise AssertionError('exact historical runtime failed: '+run.stderr[-12000:])
    receipt=json.loads(run.stdout);scope=decode(t.ScopeKey,receipt['scope']);clock=decode(t.ClockReading,receipt['clock'])
    import psycopg
    def connect():
        db=psycopg.connect(database);db.execute('SET ROLE ledger_owner');db.commit();return db
    def blocked(*args):raise ValueError('historical query fixture cannot issue a current write')
    from trading_system.engine.paper import PaperEngine
    from trading_system.engine.paper_market import PaperMatchingModel
    engine=PaperEngine(scope,dsn=venue.runtime_dsn,event_dsn=venue.event_dsn,authority_guard=blocked,
        owner_strategy_id=receipt['owner_strategy_id'],quantity_unit=receipt['quantity_unit'],
        price_currency=receipt['price_currency'],clock=lambda:clock,
        matching_model=PaperMatchingModel(model_id='ISOLATED_DEPTH_FIFO_EXPLICIT_FEE_FIXTURE',quote_fee_rate='0'))
    c=SimpleNamespace(scope=scope,service=SimpleNamespace(uow=UnitOfWork(connect)),engine=engine,
        write=SimpleNamespace(command_id=receipt['command_id']),c=SimpleNamespace(facts=SimpleNamespace(inputs=SimpleNamespace(clock=clock))))
    return c,receipt['approvals'],archive_dir
