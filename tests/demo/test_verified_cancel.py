"""Exercise verified-composition callbacks; all ownership/transport IO is offline."""
from contextlib import nullcontext
from types import SimpleNamespace
from sqlalchemy import update
from test_authority import authority,proof
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.execution.demo_commands import DemoExecutionOwner
from trading_system.runtime.demo_host import AdmissionContext
from trading_system.runtime.demo_service import DemoRuntimeService,_Runtime
from trading_system.server.demo_verified import VerifiedDemoRuntime
from trading_system.storage.demo import accounts,balance_invalidated_evidence
import pytest


def test_verified_composition_can_cancel_after_balance_invalidation(authority,monkeypatch):
    auth,repo,scope,key=authority
    envelope=proof(*authority); auth.install(scope,envelope,generation=1)
    class Transport:
        credential_ref='fixture'; sent=[]
        def sync_time(self): pass
        def cancel_order(self,request,*,permit):
            permit.consume('DELETE',request,'fixture'); permit.before_network(); self.sent.append(request)
            return {'symbol':'BTCUSDT','origClientOrderId':'original','orderId':22}
    transport=Transport(); physical=SimpleNamespace(generation=1,validate=lambda _:None)
    owner=DemoExecutionOwner(repo,transport,clock=lambda:100,physical_owner=physical)
    host=SimpleNamespace(scope=scope,owner=owner,status=lambda:{'configured':True})
    lease=SimpleNamespace(credential_ref='fixture',validate=lambda:None)
    bound=_Runtime(lease,transport,host,SimpleNamespace(healthy=True),None,physical_owner=physical,running=True,send_guard=lambda:None)
    deployment=SimpleNamespace(scope=scope,trust=auth.trust,build_digest=auth.build_digest,verify=lambda:{'qualification':envelope})
    service=VerifiedDemoRuntime(deployment=deployment,connections=None,model_service=None,repository=repo,scope_resolver=None,current_code_sha=auth.code_sha)
    service.authority=auth
    monkeypatch.setattr(DemoRuntimeService,'_bind_session',lambda self,session:self._sessions.update({session:bound}))
    service.bind_session('session')  # Installs actual verified callback wiring.
    monkeypatch.setattr(repo,'owner_guard',lambda *_:nullcontext())
    repo.stage(scope,command_id='entry',request={'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000',
        'timeInForce':'GTC','newClientOrderId':'original'},reserve_asset='USDT',reserve_amount='50',available='1000',authorization_ref='grant',expires_at=150,now=100)
    repo.begin_send(scope,'entry',expected_revision=repo.account(scope)['revision'],now=100)
    repo.acknowledge(scope,'entry',{'symbol':'BTCUSDT','clientOrderId':'original','orderId':22})
    evidence=balance_invalidated_evidence(repo.account(scope)['qualification'])
    with repo.engine.begin() as conn: conn.execute(update(accounts).values(qualification=evidence,state='RECOVERY_REQUIRED'))
    repo.stage(scope,command_id='cancel',request={'symbol':'BTCUSDT','origClientOrderId':'original'},method='DELETE',purpose='CANCEL',
        reserve_asset='BTC',reserve_amount='0',available='0',authorization_ref='cancel-grant',expires_at=150,now=100)
    context=AdmissionContext(now=100,quote_at=100,account_at=100,quote_source='BINANCE_DEMO',model_source='assessment_v1',
        decision_expires_at=150,capability_state='INVALIDATED_BALANCE',code_sha=auth.code_sha,qualified_sha=auth.code_sha,
        private_stream_healthy=True,account_state='RECOVERY_REQUIRED',authorized=True,budget='1000',available='1000',risk_limit='1000',
        unresolved_unknown=0,unprotected=0)
    assert owner.dispatch(scope,'cancel',context=context)['state']=='ACKNOWLEDGED'
    assert transport.sent==[{'symbol':'BTCUSDT','origClientOrderId':'original'}]
    with pytest.raises(DemoError): auth.require(scope,generation=1)
