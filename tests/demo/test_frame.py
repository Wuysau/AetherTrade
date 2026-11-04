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
