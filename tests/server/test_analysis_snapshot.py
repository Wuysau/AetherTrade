import asyncio
import copy
from datetime import datetime, timezone

import pytest

from trading_system.server import market_parse as parse
from trading_system.server.analysis_snapshot import build_analysis_context, validate_snapshot
from trading_system.server.errors import ApiError

NOW = datetime(2026, 10, 28, 3, 0, 2, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)
PROFILE = dict(strategy_revision=1, decision_interval='1h', holding_period_seconds=3600,
               allowed_operations=['SET_TARGET', 'HOLD'], required=['quote', 'candles:1h'],
               optional=['news', 'book'], windows={'1h': 3})

def bar(offset, closed=True):
    start = MS - 2000 - offset * 3600000
    return dict(time=start, close_time=start + 3599999, closed=closed,
                open='10', high='12', low='9', close='11', volume='20')

class Feed:
    def __init__(self):
        self.rows = [bar(3), bar(2), bar(1), bar(0, False)]
        self.generation = 1
        self.requests = []
    def snapshot(self):
        return dict(source_id='fixture', source_generation=self.generation, source_revision=1,
                    product='SPOT', venue='BINANCE', status='CONNECTED', books={},
                    quotes=[dict(symbol='BTCUSDT', price='11', change_pct='0', as_of=NOW.isoformat(), stale=False)])
    async def candles(self, symbol, interval, limit):
        self.requests.append((interval, limit))
        return dict(candles=self.rows, source_generation=1, source_id='fixture', product='SPOT')

class Store:
    def analysis_context(self, symbol, market, maximum_age):
        return dict(symbol=symbol, quote=market['quotes'][0], account=dict(cash='100',currency='USDT',paused=False),
                    position=None, risk=dict(max_position_pct='25', max_daily_loss='50'),
                    market_status='CONNECTED', quote_max_age_seconds=maximum_age)

@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(parse, 'utc_now', lambda: NOW)

def build(feed=None, profile=None):
    return asyncio.run(build_analysis_context(Store(), feed or Feed(), 'BTCUSDT', 30, profile or PROFILE))['analysis_snapshot']

def test_d01_required_history_blocks():
    feed = Feed(); feed.rows = []
    snapshot = build(feed)
    assert snapshot['quality']['status'] == 'DATA_BLOCKED'
    assert 'candles:1h' in snapshot['quality']['missing_required']

def test_d02_d03_bounded_background_snapshot_roundtrip():
    feed = Feed(); snapshot = build(feed)
    assert feed.requests == [('1h', 4)]
    assert validate_snapshot(snapshot) == snapshot
    assert snapshot['market']['features']['1h']['bars_used'] == 3
    assert snapshot['portfolio']['account']['cash'] == '100'

def test_d04_partial_never_enters_features():
    snapshot = build()
    assert len(snapshot['market']['candles']['1h']) == 3
    assert snapshot['market']['partial']['1h']['closed'] is False
    assert snapshot['market']['features']['1h']['closed_until'] < MS

def test_d05_mixed_generation_blocks():
    feed = Feed(); feed.generation = 2
    assert build(feed)['quality']['status'] == 'DATA_BLOCKED'

def test_d06_hourly_freshness_independent_of_book():
    snapshot = build()
    assert snapshot['quality']['status'] == 'READY'
    assert snapshot['quality']['sections']['book']['status'] == 'MISSING'

@pytest.mark.parametrize('value', [float('nan'), 'https://evil.test/secret', {'api_key':'secret'}])
def test_d07_nested_untrusted_data_rejected(value):
    snapshot = build(); snapshot['portfolio']['account']['cash'] = value
    with pytest.raises(ApiError): validate_snapshot(snapshot)

def test_d07_oversized_rejected():
    snapshot = build(); snapshot['source_id'] = 'a' * 100000
    with pytest.raises(ApiError): validate_snapshot(snapshot)

def test_d08_optional_news_does_not_block():
    assert build()['quality']['missing_optional'] == ['news', 'book']
    assert build()['quality']['status'] == 'READY'

def test_d04_parser_preserves_closed_metadata():
    assert parse.candle(bar(1), '1h')['closed'] is True
    row = bar(0, False)
    assert parse.kline([row[k] for k in ('time','open','high','low','close','volume','close_time')], '1h')['closed'] is False

def test_legacy_profile_is_not_implicitly_upgraded():
    assert 'analysis_snapshot' not in asyncio.run(build_analysis_context(Store(), Feed(), 'BTCUSDT', 30))

def test_d06_stale_book_cannot_borrow_hourly_expiry():
    feed = Feed()
    snapshot = feed.snapshot()
    snapshot['books'] = {'BTCUSDT':dict(as_of='2026-10-28T02:59:00Z', bids=[['10','1']], asks=[['12','1']], stale=False)}
    feed.snapshot = lambda: copy.deepcopy(snapshot)
    profile = copy.deepcopy(PROFILE)
    profile['required'].append('book'); profile['optional'].remove('book')
    result = build(feed, profile)
    assert result['quality']['missing_required'] == ['book']
    assert result['market']['features']['1h']['bars_used'] == 3

def test_d05_product_mismatch_blocked():
    feed = Feed(); snapshot = feed.snapshot(); snapshot['product'] = 'USD_M_PERP'
    feed.snapshot = lambda: snapshot
    assert build(feed)['quality']['status'] == 'DATA_BLOCKED'

def test_closed_claim_cannot_cover_future_bar():
    with pytest.raises(parse.InvalidMarketData): parse.candle(bar(0), '1h')

def test_d07_forged_quality_rejected_even_with_recomputed_hash():
    from trading_system.server.analysis_snapshot import _digest
    feed = Feed(); feed.rows = []
    snapshot = build(feed)
    snapshot['quality']['status'] = 'READY'
    snapshot['snapshot_hash'] = _digest(snapshot)
    snapshot['snapshot_id'] = 'snapshot:' + snapshot['snapshot_hash'][:32]
    with pytest.raises(ApiError): validate_snapshot(snapshot)

def test_d04_ws_closed_evidence_survives_feed():
    from trading_system.server.market_live import LiveMarketFeed
    feed = LiveMarketFeed(None)
    row = bar(1)
    payload = dict(stream='btcusdt@kline_1h', data=dict(s='BTCUSDT', E=MS, e='kline',
                   k=dict(s='BTCUSDT',i='1h',t=row['time'],T=row['close_time'],x=True,
                          o='10',h='12',l='9',c='11',v='20')))
    assert feed._apply_binance_ws(payload)
    assert feed.snapshot()['candles']['BTCUSDT']['1h']['closed'] is True
    payload['data']['k']['x'] = 'true'
    with pytest.raises(parse.InvalidMarketData): feed._apply_binance_ws(payload)

def test_runtime_summary_populates_authoritative_capabilities():
    class Runtime:
        def analysis_evidence(self, symbol):
            assert symbol == 'BTCUSDT'
            return dict(portfolio=dict(active_orders=[dict(side='BUY',quantity='2',filled_quantity='1',status='CANCEL_UNKNOWN')],reserved_funds='11',protection_status='VERIFIED_SYNTHETIC'),
                        constraints=dict(execution_supported=True,protection_supported=True),
                        costs=dict(fee_rate='0.001',fee_source='PAPER_POLICY',slippage_bps='20',complete=True))
    store = Store(); store.execution = Runtime(); store.sqlite = False
    result = asyncio.run(build_analysis_context(store, Feed(), 'BTCUSDT', 30, PROFILE))['analysis_snapshot']
    assert result['constraints']['protection_supported'] is True
    assert result['portfolio']['reserved_funds'] == '11'
    assert result['portfolio']['active_orders'][0]['status'] == 'CANCEL_UNKNOWN'
    assert result['costs']['complete'] is True

def test_legacy_feed_missing_history_and_provenance_is_data_blocked():
    feed = Feed(); public = feed.snapshot()
    public.pop('source_generation'); public.pop('source_id')
    feed.snapshot = lambda: public
    feed.candles = None
    assert build(feed)['quality']['status'] == 'DATA_BLOCKED'
