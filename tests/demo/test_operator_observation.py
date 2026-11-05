"""Offline projection tests. Fixtures do not represent venue qualification."""
from sqlalchemy import create_engine, insert
from trading_system.engine.binance_demo.contracts import DemoScope
from trading_system.storage.demo import DemoRepository, metadata, decisions, fills


def test_operator_observation_is_scoped_and_does_not_invent_pnl(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "observer.sqlite"}'); metadata.create_all(engine)
    repo = DemoRepository(engine)
    a = DemoScope(account_id='123',account_epoch='epoch',strategy_id='a')
    b = DemoScope(account_id='123',account_epoch='epoch',strategy_id='b')
    repo.register_account(a, credential_ref='demo-session:fixture', fingerprint='fp', budget='1000')
    def decision(scope, run, summary):
        return dict(scope_key=scope.key, run_id=run, account_key=scope.account_key, state='ASSESSED',source='assessment_v1',binding_hash='a'*64,
            snapshot={}, strategy={}, result={'decision':{'summary':summary,'assessment_status':'DECIDED','action':'HOLD'}}, memory_context=None)
    with engine.begin() as conn:
        conn.execute(insert(decisions), [decision(a,'run-a','scope-a'), decision(b,'run-b','scope-b')])
        conn.execute(insert(fills).values(fill_key='f'*64,account_key=a.account_key,scope_key=a.key,symbol='BTCUSDT',trade_id='1',order_id='2',payload_hash='a'*64,
            payload={'symbol':'BTCUSDT','order_id':'2','trade_id':'1','side':'BUY','quantity':'0.001','price':'50000','quote_quantity':'50','commission':'0','commission_asset':'USDT'},occurred_at=100))
    request={'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000','newClientOrderId':'original','timeInForce':'GTC'}
    repo.stage(a,command_id='command-a',request=request,reserve_asset='USDT',reserve_amount='50',available='100',authorization_ref='grant',expires_at=200,now=100)
    repo.begin_send(a,'command-a',expected_revision=0,now=101)
    repo.mark_unknown(a,'command-a','LOST_RESPONSE')
    view = repo.operator_view(a, limit=20)
    assert [item['summary'] for item in view['decisions']] == ['scope-a']
    assert view['orders'][0]['confirmation'] == 'OUTCOME_UNKNOWN'
    assert view['fills'][0]['confirmation'] == 'VENUE_OBSERVED'
    assert view['pnl']['state'] == 'UNAVAILABLE' and view['pnl']['realized_usdt'] is None
    assert 'scope-b' not in str(view) and 'demo-session:fixture' not in str(view)
    engine.dispose()
