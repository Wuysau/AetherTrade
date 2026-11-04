"""Bounded official Demo reads → frozen native assessment evidence.

No public production source, synthetic candle fallback or PAPER portfolio reuse.
"""
from datetime import datetime, timezone
from trading_system.engine.binance_demo.contracts import DemoError, decimal
from trading_system.server.demo_snapshot import build_demo_snapshot


class DemoFrameLoader:
    def __init__(self, repository, scope, transport, *, current_code_sha):
        self.repository, self.scope, self.transport = repository, scope, transport
        self.current_code_sha = current_code_sha

    def __call__(self):
        repo, scope, transport = self.repository, self.scope, self.transport
        account = repo.account(scope)
        if account['credential_ref'] != transport.credential_ref or account['frozen']:
            raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
        strategy = repo.strategy(scope)
        windows = strategy['profile']['windows']
        if len(windows) > 4 or any(type(n) is not int or not 1 <= n <= 200 for n in windows.values()):
            raise DemoError('DEMO_KLINE_BUDGET_INVALID')
        transport.sync_time()
        raw = transport.account()
        observed_at = datetime.now(timezone.utc).isoformat()
        if raw.get('accountType') != 'SPOT' or raw.get('canTrade') is not True:
            raise DemoError('DEMO_ACCOUNT_NOT_TRADABLE')
        balances = {}
        for row in raw['balances']:
            if row['asset'] in balances:
                raise DemoError('DEMO_BALANCE_DUPLICATE')
            balances[row['asset']] = {'free': str(decimal(row['free'])), 'locked': str(decimal(row['locked']))}
        rates = raw.get('commissionRates') or {}
        fee = (max(decimal(rates['maker']), decimal(rates['taker'])) + max(decimal(rates['buyer']), decimal(rates['seller']))) if set(rates) >= {'maker', 'taker', 'buyer', 'seller'} else None
        account = repo.record_account_snapshot(scope, {'balances': balances, 'fee_rate': str(fee) if fee is not None else None},
            observed_at=observed_at, expected_revision=account['revision'])
        info = transport.exchange_info('BTCUSDT')
        rules = [item for item in info.get('symbols', []) if item.get('symbol') == 'BTCUSDT']
        if len(rules) != 1:
            raise DemoError('DEMO_SYMBOL_UNBOUND')
        rules[0] = {**rules[0], 'exchangeFilters': info.get('exchangeFilters', [])}
        candles = {}
        for interval, count in windows.items():
            rows = transport.klines('BTCUSDT', interval, count + 1)
            if not isinstance(rows, list) or len(rows) > count + 1:
                raise DemoError('DEMO_KLINE_RESPONSE_INVALID')
            candles[interval] = [{'time': row[0], 'open': row[1], 'high': row[2], 'low': row[3], 'close': row[4], 'volume': row[5], 'close_time': row[6],
                'closed': row[6] < int(datetime.now(timezone.utc).timestamp() * 1000)} for row in rows]
        execution = {}
        types = {item['filterType'] for item in rules[0]['filters']}
        if types & {'PERCENT_PRICE', 'PERCENT_PRICE_BY_SIDE', 'MAX_NUM_ORDERS', 'MAX_POSITION'} or rules[0]['exchangeFilters']:
            open_orders = transport.open_orders(None)
            if not isinstance(open_orders, list) or len(open_orders) > 100:
                raise DemoError('DEMO_OPEN_ORDER_BUDGET_EXCEEDED')
            selected = [row for row in open_orders if row.get('symbol') == 'BTCUSDT']
            pending_buy = sum((decimal(row['origQty']) - decimal(row['executedQty']) for row in selected if row['side'] == 'BUY'), decimal('0'))
            average = transport.average_price('BTCUSDT')
            last = transport.last_price('BTCUSDT')
            if type(average.get('mins')) is not int or type(average.get('closeTime')) is not int or not 0 <= datetime.now(timezone.utc).timestamp() * 1000 - average['closeTime'] <= 5000 or last.get('symbol') != 'BTCUSDT':
                raise DemoError('DEMO_AVERAGE_PRICE_EVIDENCE_REQUIRED')
            base = balances.get('BTC', {'free': '0', 'locked': '0'})
            execution = {'average_price': average['price'], 'average_minutes': average['mins'], 'last_price': last['price'],
                'open_order_count': len(selected), 'exchange_open_order_count': len(open_orders),
                'account_base_quantity': str(decimal(base['free']) + decimal(base['locked'])), 'pending_buy_quantity': str(pending_buy),
                'as_of': datetime.now(timezone.utc).isoformat()}
        book = transport.book_ticker('BTCUSDT')
        if book.get('symbol') != 'BTCUSDT':
            raise DemoError('DEMO_MARKET_SOURCE_INVALID')
        market = {'environment': 'BINANCE_DEMO', 'symbol': 'BTCUSDT', 'as_of': datetime.now(timezone.utc).isoformat(),
                  'bid': book['bidPrice'], 'ask': book['askPrice'], 'candles': candles, 'execution_context': execution}
        if repo.account(scope)['revision'] != account['revision'] or repo.strategy(scope) != strategy:
            raise DemoError('DEMO_FRAME_BINDING_CHANGED')
        return build_demo_snapshot(scope=scope, strategy=strategy, account={**account, 'balances': balances, 'fee_rate': str(fee) if fee is not None else None,
            'position': str(repo.asset_deltas(scope).get('BTC', 0)), 'observed_at': observed_at}, market=market, rules=rules[0], qualification=account.get('qualification') or {}, code_sha=self.current_code_sha)
