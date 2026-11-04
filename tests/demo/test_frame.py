from datetime import datetime, timezone
from sqlalchemy import update
from trading_system.storage.demo import accounts
from trading_system.runtime.demo_frame import DemoFrameLoader
from test_host import host
from test_transport import RULES


class Transport:
    credential_ref = 'demo-session:fixture'
    def sync_time(self): pass
    def account(self):
        return {'accountType': 'SPOT', 'canTrade': True, 'balances': [{'asset': 'USDT', 'free': '1000', 'locked': '0'}, {'asset': 'BTC', 'free': '0', 'locked': '0'}],
                'commissionRates': {'maker': '0.001', 'taker': '0.001', 'buyer': '0', 'seller': '0'}}
    def book_ticker(self, symbol): return {'symbol': symbol, 'bidPrice': '49999', 'askPrice': '50001'}
    def exchange_info(self, symbol): return {'symbols': [RULES]}
    def klines(self, symbol, interval, limit):
        start = int(datetime.now(timezone.utc).timestamp()) // 60 * 60000
        return [[start - i * 60000, '50000', '50000', '50000', '50000', '10', start - (i-1)*60000-1] for i in (2, 1)]


def test_real_read_boundary_builds_distinct_frame_and_binds_financial_revision(host):
    _, repo, scope, _ = host
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).values(qualification={'code_sha': 'a'*40, 'state': 'DEMO_VERIFIED', 'protection': 'HOSTED'}))
    loader = DemoFrameLoader(repo, scope, Transport(), current_code_sha='a' * 40)
    first = loader()
    assert first['market']['quote']['price'] == '50000'
    assert first['portfolio']['position'] is None
    assert first['costs']['complete']
    assert first['bindings']['account_revision'] == 0
    assert loader()['bindings']['account_revision'] == 0
    loader.transport.account = lambda: {'accountType': 'SPOT', 'canTrade': True, 'balances': [{'asset': 'USDT', 'free': '900', 'locked': '100'}], 'commissionRates': {'maker': '0.001', 'taker': '0.001', 'buyer': '0', 'seller': '0'}}
    assert loader()['bindings']['account_revision'] == 1


def test_quote_sized_open_buy_keeps_pending_base_unknown_but_allows_sell_compilation(host):
    from copy import deepcopy
    import pytest
    from trading_system.engine.binance_demo.contracts import DemoError
    from trading_system.engine.binance_demo.orders import compile_spot_order
    _, repo, scope, _ = host
    class QuoteTransport(Transport):
        def exchange_info(self, symbol):
            rules = deepcopy(RULES)
            rules['filters'].append({'filterType': 'MAX_POSITION', 'maxPosition': '10'})
            return {'symbols': [rules]}
        def open_orders(self, symbol):
            return [{'symbol':'BTCUSDT', 'side':'BUY', 'type':'MARKET', 'origQty':'0', 'executedQty':'0.0001', 'origQuoteOrderQty':'50'}]
        def average_price(self, symbol): return {'mins':5, 'closeTime':int(datetime.now(timezone.utc).timestamp()*1000), 'price':'50000'}
        def last_price(self, symbol): return {'symbol':symbol, 'price':'50000'}
    frame = DemoFrameLoader(repo, scope, QuoteTransport(), current_code_sha='a'*40)()
    context = frame['market']['execution_context']
    assert context['pending_buy_quantity'] is None
    args = {'rules':frame['rules'], 'order_type':'LIMIT', 'quantity':'0.001', 'price':'50000', 'client_id':'x', 'reference_price':'50000', 'filter_context':context}
    assert compile_spot_order(side='SELL', **args)['side'] == 'SELL'
    with pytest.raises(DemoError): compile_spot_order(side='BUY', **args)


def test_negative_owned_base_blocks_assessment_but_allows_qualified_cancellation(host, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from sqlalchemy import insert
    from trading_system.storage.demo import postings
    from trading_system.execution.demo_commands import DemoExecutionOwner
    from trading_system.execution.demo_reports import DemoReportVerifier
    from test_lifecycle import report
    service, repo, scope, config = host
    now = int(datetime.now(timezone.utc).timestamp())
    qualification = {'state':'DEMO_VERIFIED', 'code_sha':'a'*40, 'account_key':scope.account_key, 'credential_revision':0, 'product':'SPOT', 'protection':'HOSTED',
        'capabilities':{'CANCEL':{'state':'DEMO_VERIFIED', 'evidence_sha256':'f'*64, 'official_contract_revision':'offline-only'}}}
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).values(state='RECOVERY_REQUIRED', qualification=qualification))
        conn.execute(insert(postings).values(fill_key='unmatched', asset='BTC', account_key=scope.account_key, scope_key=scope.key, delta='-0.02'))
    request = {'symbol':'BTCUSDT', 'side':'SELL', 'type':'LIMIT', 'quantity':'0.999', 'price':'49000', 'newClientOrderId':'leftover', 'timeInForce':'GTC'}
    repo.stage(scope, command_id='leftover', request=request, reserve_asset='BTC', reserve_amount='0.999', available='1', authorization_ref=config['authorization_ref'], expires_at=now+60, now=now, purpose='PROTECTION')
    repo.begin_send(scope, 'leftover', expected_revision=0, now=now)
    event = report(c='leftover', S='SELL', q='0.999', p='49000', i=45, t=-1, l='0', z='0.99', x='NEW', X='PARTIALLY_FILLED', E=now*1000)
    DemoReportVerifier(repo).process(scope, repo.observe(scope, source='WS_EXECUTION_REPORT', payload=event, received_at=now, credential_ref='demo-session:fixture'))
    class CancellationTransport(Transport):
        def last_price(self, symbol): return {'symbol':symbol, 'price':'48000'}
        def cancel_order(self, params, *, permit):
            permit.consume('DELETE', params, self.credential_ref)
            return {'symbol':'BTCUSDT', 'origClientOrderId':'leftover', 'orderId':45}
    transport = CancellationTransport()
    service.frame_loader = DemoFrameLoader(repo, scope, transport, current_code_sha='a'*40)
    service.stream = SimpleNamespace(healthy=True, credential_ref='demo-session:fixture')
    @contextmanager
    def offline_guard(_): yield
    monkeypatch.setattr(repo, 'owner_guard', offline_guard)
    service.owner = DemoExecutionOwner(repo, transport, clock=service.clock)
    snapshot = service._frame()
    assert snapshot['portfolio']['position']['quantity'] == '-0.02'
    assert snapshot['quality']['status'] == 'DATA_BLOCKED'
    cancel = service.protection_tick()['commands'][0]
    assert service.dispatch(cancel['command_id'])['state'] == 'ACKNOWLEDGED'
