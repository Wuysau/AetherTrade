from types import SimpleNamespace
import pytest
from sqlalchemy import update
from test_authority import authority,proof
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.execution.demo_consistency import SerializedAccountPolicy
from trading_system.storage.demo import accounts


def setup_policy(authority):
    auth,repo,scope,key=authority
    auth.install(scope,proof(*authority),generation=1)
    stream=SimpleNamespace(healthy=True,scope=scope,credential_ref='fixture',credential_revision=0,
                           physical_generation=1,active_run_nonce='stream-1',subscription_id=1,validate_owner=lambda:None)
    class Transport:
        credential_ref='fixture'
        def sync_time(self): pass
        def account(self): return {'uid':123,'accountType':'SPOT','canTrade':True,'updateTime':100000,
                                    'balances':[{'asset':'USDT','free':'1000','locked':'0'}]}
        def open_orders(self,symbol): return []
        def trades(self,*args,**kwargs): return []
    policy=SerializedAccountPolicy(repo,Transport(),auth,clock=lambda:100)
    return policy,scope,stream


def test_valid_qualified_dedicated_account_can_resume_without_fake_ws_barrier(authority):
    policy,scope,stream=setup_policy(authority)
    result=policy.resume(scope,stream=stream)
    assert result['state']=='READY'
    assert result['receipt']['policy']=='serialized-account-v1'
    assert result['receipt']['atomic_stream_barrier'] is False
    receipt=policy.prepare(scope,stream=stream)
    policy.validate(receipt,scope,stream=stream)
    stream.active_run_nonce='replacement'
    with pytest.raises(DemoError): policy.validate(receipt,scope,stream=stream)


@pytest.mark.parametrize('failure',['balance','orders','trades','uid','stream','expiry'])
def test_reject_external_activity_or_stale_sources(authority,failure):
    policy,scope,stream=setup_policy(authority)
    if failure=='balance': policy.transport.account=lambda:{'uid':123,'accountType':'SPOT','canTrade':True,'balances':[{'asset':'USDT','free':'1001','locked':'0'}]}
    if failure=='orders': policy.transport.open_orders=lambda *_:[{'symbol':'BTCUSDT','clientOrderId':'foreign'}]
    if failure=='trades': policy.transport.trades=lambda *a,**kw:[{'id':1,'orderId':2}]
    if failure=='uid': policy.transport.account=lambda:{'uid':124,'accountType':'SPOT','canTrade':True,'balances':[]}
    if failure=='stream': stream.healthy=False
    if failure=='expiry': policy.clock=lambda:201; policy.authority.clock=policy.clock
    with pytest.raises(DemoError): policy.resume(scope,stream=stream)
    assert policy.repository.account(scope)['state']!='READY'


def test_receipt_rejects_racing_revision_and_expiry(authority):
    policy,scope,stream=setup_policy(authority)
    policy.resume(scope,stream=stream)
    receipt=policy.prepare(scope,stream=stream)
    policy.clock=lambda:106
    with pytest.raises(DemoError): policy.validate(receipt,scope,stream=stream)
    policy.clock=lambda:100
    policy.repository.pause(scope,'CONCURRENT_CHANGE')
    with pytest.raises(DemoError): policy.validate(receipt,scope,stream=stream)


def test_multiple_durable_entries_cannot_send(authority):
    policy,scope,stream=setup_policy(authority)
    policy.resume(scope,stream=stream)
    for name in ('one','two'):
        policy.repository.stage(scope,command_id=name,request={'symbol':'BTCUSDT','side':'BUY','type':'LIMIT',
            'quantity':'0.001','price':'50000','timeInForce':'GTC','newClientOrderId':name},reserve_asset='USDT',reserve_amount='50',
            available='1000',authorization_ref='test',expires_at=150,now=100)
    with pytest.raises(DemoError): policy.prepare(scope,stream=stream,command_id='one')


def test_unresolved_history_blocks_ready_even_outside_current_overlap(authority):
    policy,scope,stream=setup_policy(authority)
    with policy.repository.transaction(scope.account_key) as conn:
        policy.repository._event(conn,scope.account_key,'UNRESOLVED_TRADE_HISTORY','old',{'trade_hash':'old','symbol':'BTCUSDT','trade_id':'1','order_id':'1'})
    with pytest.raises(DemoError,match='HISTORY'):
        policy.resume(scope,stream=stream)
    assert policy.repository.account(scope)['state']!='READY'


@pytest.mark.parametrize('initial_locked',['50','49'])
def test_resting_limit_lock_and_cancel_frames_have_audited_history(authority,initial_locked):
    from decimal import Decimal
    from test_lifecycle import report
    from trading_system.execution.demo_reports import DemoReportVerifier
    policy,scope,stream=setup_policy(authority); repo=policy.repository
    repo.stage(scope,command_id='resting',request={'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001',
        'price':'50000','timeInForce':'GTC','newClientOrderId':'resting'},reserve_asset='USDT',reserve_amount='50',
        available='1000',authorization_ref='test',expires_at=150,now=100)
    repo.begin_send(scope,'resting',expected_revision=repo.account(scope)['revision'],now=100)
    verifier=DemoReportVerifier(repo)
    for timestamp,status,free,locked in [(100000,'NEW',str(1000-Decimal(initial_locked)),initial_locked),(101000,'CANCELED','1000','0')]:
        event=report(c='resting',q='0.001',E=timestamp,T=timestamp,x=status,X=status,t=-1,l='0',z='0',Z='0',Y='0',n='0',N=None)
        observed=repo.observe(scope,source='WS_EXECUTION_REPORT',payload=event,received_at=timestamp//1000,
            credential_ref='fixture',credential_revision=0,stream_generation=1)
        verifier.process(scope,observed)
        repo.observe(scope,source='WS_ACCOUNT_POSITION',payload={'e':'outboundAccountPosition','E':timestamp,'u':timestamp,
            'B':[{'a':'USDT','f':free,'l':locked}]},received_at=timestamp//1000,credential_ref='fixture',credential_revision=0,stream_generation=1)
    original=policy.transport.account
    policy.transport.account=lambda:{**original(),'updateTime':101000}
    policy.clock=lambda:102
    if initial_locked!='50':
        with pytest.raises(DemoError,match='POSITION_MISMATCH'): policy.resume(scope,stream=stream)
        return
    assert policy.resume(scope,stream=stream)['state']=='READY'
    assert repo.health(scope)['pending_inbox']==0
