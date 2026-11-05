"""Real AI parser/admission/command/reducer with offline IO and lock substitutes.

This is not a PostgreSQL owner or Binance venue qualification test.
"""
import asyncio,base64,json,time
from contextlib import nullcontext
from types import SimpleNamespace
import httpx
from fastapi.testclient import TestClient
from test_authority import authority,proof
from test_host import frame,assessment,strategy
from test_lifecycle import report
from trading_system.engine.binance_demo.attestation import canonical
from trading_system.engine.binance_demo.contracts import digest
from trading_system.execution.demo_consistency import SerializedAccountPolicy
from trading_system.execution.demo_commands import DemoExecutionOwner
from trading_system.execution.demo_reports import DemoReportVerifier
from trading_system.runtime.demo_strategy import DemoStrategyHost
from trading_system.server.demo_snapshot import snapshot_hash
from trading_system.server.app import create_app
from trading_system.server.config import Settings


def test_real_model_to_qualified_command_fill_and_takeover(authority,tmp_path,monkeypatch):
    auth,repo,scope,key=authority
    now=int(time.time()); auth.clock=time.time
    envelope=proof(*authority)
    envelope['payload'].update(issued_at=now-1,expires_at=now+600)
    envelope['signature']=base64.b64encode(key.sign(canonical(envelope['payload']))).decode()
    auth.install(scope,envelope,generation=1)
    stream=SimpleNamespace(healthy=True,scope=scope,credential_ref='fixture',credential_revision=0,
        physical_generation=1,active_run_nonce='offline-stream',subscription_id=1,validate_owner=lambda:None)
    class Transport:
        credential_ref='fixture'
        sent=[]
        def sync_time(self): pass
        def account(self): return {'uid':123,'accountType':'SPOT','canTrade':True,'updateTime':now*1000,
            'balances':[{'asset':'USDT','free':'1000','locked':'0'}]}
        def open_orders(self,*args): return []
        def trades(self,*args,**kwargs): return []
        def send_order(self,request,*,permit):
            permit.consume('POST',request,self.credential_ref); permit.before_network()
            self.sent.append(request)
            return {'symbol':'BTCUSDT','clientOrderId':request['newClientOrderId'],'orderId':22}
    transport=Transport(); policy=SerializedAccountPolicy(repo,transport,auth)
    policy.resume(scope,stream=stream)
    config={**strategy(),'mode':'AI','allow_model_calls':True}
    repo.save_strategy(scope,config,expected_revision=0)
    def current():
        snapshot=frame(scope,config)
        snapshot['bindings'].update(code_sha=auth.code_sha,account_revision=repo.account(scope)['revision'],
            qualification_hash=digest(repo.account(scope)['qualification']))
        snapshot['snapshot_hash']=snapshot_hash(snapshot); snapshot['snapshot_id']='demo-snapshot:'+snapshot['snapshot_hash'][:32]
        return snapshot
    requests=[]; provider_errors=[]
    def provider(request):
        body=json.loads(request.content)
        requests.append(body)
        try: snapshot=json.loads(body['messages'][-1]['content'])['analysis_snapshot']
        except Exception as exc:
            provider_errors.append(repr(exc)); raise
        try: result=assessment(snapshot)
        except Exception as exc:
            provider_errors.append(repr(exc)); raise
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':10}})
    model_client=httpx.AsyncClient(transport=httpx.MockTransport(provider),trust_env=False)
    settings=Settings(database_url=f'sqlite:///{tmp_path / "model.sqlite"}',testing=True,market_enabled=False,
        allowed_origins=('http://testserver',),model_key_path=tmp_path/'models.key',web_dist=tmp_path/'web')
    app=create_app(settings,model_client=model_client)
    owner=DemoExecutionOwner(repo,transport); owner.consistency_policy=policy; owner.consistency_stream=stream
    # Only the offline test replaces the PG lock. Production code still rejects SQLite execution ownership.
    monkeypatch.setattr(repo,'owner_guard',lambda *_:nullcontext())
    host=DemoStrategyHost(repo,scope,frame_loader=current,model_service=app.state.models,execution_owner=owner,
        current_code_sha=auth.code_sha,stream=stream)
    with TestClient(app) as client:
        client.headers['Origin']='http://testserver'
        setup=client.post('/api/setup',json={'username':'owner','password':'local-fixture-password','initial_cash':'100000'})
        client.headers['X-CSRF-Token']=setup.json()['csrf_token']
        saved=client.put('/api/model-config',json={'protocol':'openai_chat','base_url':'http://127.0.0.1:18083/v1',
            'model':'controlled-model','enabled':True,'local_mode':True,'expected_revision':0,'api_key':'provider-fixture-secret'})
        assert saved.status_code==200
        decision=asyncio.run(host.assess_once('chain'))
        assert decision['result']['decision']['assessment_status']=='DECIDED', (provider_errors, list(requests[0]) if requests else [])
        command=host.admit_assessment('chain')
        sent=host.dispatch(command['command_id'])
        assert sent['state']=='ACKNOWLEDGED' and len(transport.sent)==1
        assert asyncio.run(host.assess_once('chain'))['command_id']==command['command_id'] and len(requests)==1
        event=report(c=command['client_id'],q='0.001',l='0.001',z='0.001',Z='50',Y='50',n='0.000001')
        observation=repo.observe(scope,source='WS_EXECUTION_REPORT',payload=event,received_at=now,
            credential_ref='fixture',credential_revision=0,stream_generation=1)
        verifier=DemoReportVerifier(repo); verifier.process(scope,observation); verifier.process(scope,observation)
        assert repo.command(scope,command['command_id'])['order_status']=='FILLED'
        assert len(repo.operator_view(scope)['fills'])==1
        assert client.get('/api/dashboard').json()['orders']==[]
        view=auth.responsibilities(scope)
        receipt=auth.acknowledge_takeover(scope,view['digest'])
        assert receipt['flat_account_verified'] is False and repo.strategy(scope)['allow_execution'] is False
    asyncio.run(model_client.aclose())
