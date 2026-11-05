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
