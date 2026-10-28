"""Bounded, replayable research evidence; never an execution authorization."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from decimal import Decimal, localcontext

from . import market_parse as parse
from .errors import ApiError

MAX_BYTES = 96000
PROFILE_KEYS = {'strategy_revision', 'decision_interval', 'holding_period_seconds', 'allowed_operations', 'required', 'optional', 'windows'}
REFS = {'quote', 'book', 'news'} | {'candles:' + i for i in parse.INTERVALS}


def _fail():
    raise ApiError('INVALID_ANALYSIS_SNAPSHOT', '分析证据格式、来源或预算无效。')


def validate_task_profile(value):
    if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
        _fail()
    if type(value['strategy_revision']) is not int or value['strategy_revision'] < 1:
        _fail()
    if value['decision_interval'] not in parse.INTERVALS or type(value['holding_period_seconds']) is not int or not 1 <= value['holding_period_seconds'] <= 31536000:
        _fail()
    windows = value['windows']
    if not isinstance(windows, dict) or not 1 <= len(windows) <= 3 or value['decision_interval'] not in windows:
        _fail()
    if any(k not in parse.INTERVALS or type(v) is not int or not 2 <= v <= 199 for k, v in windows.items()):
        _fail()
    for key, allowed in [('allowed_operations', {'SET_TARGET', 'UPDATE_EXIT', 'HOLD'}), ('required', REFS), ('optional', REFS)]:
        rows = value[key]
        if not isinstance(rows, list) or len(rows) > 12 or any(not isinstance(r, str) or r not in allowed for r in rows) or len(set(rows)) != len(rows):
            _fail()
    if not value['allowed_operations'] or 'quote' not in value['required'] or 'candles:' + value['decision_interval'] not in value['required']:
        _fail()
    if set(value['required']) & set(value['optional']):
        _fail()
    if any(r.startswith('candles:') and r[8:] not in windows for r in value['required'] + value['optional']):
        _fail()
    return copy.deepcopy(value)


def _number(value):
    with localcontext() as ctx:
        ctx.prec = 72
        return format(value.quantize(Decimal('0.00000001')), 'f')


def _features(rows):
    closes = [Decimal(r['close']) for r in rows]
    volumes = [Decimal(r['volume']) for r in rows]
    ranges = [max(Decimal(r['high']) - Decimal(r['low']), abs(Decimal(r['high']) - closes[i-1]), abs(Decimal(r['low']) - closes[i-1])) for i, r in enumerate(rows) if i]
    mean_volume = sum(volumes[:-1]) / (len(rows) - 1)
    return dict(formula_revision='closed-v1', bars_used=len(rows), closed_until=rows[-1]['close_time'],
                return_pct=_number((closes[-1] / closes[0] - 1) * 100), sma=_number(sum(closes) / len(rows)),
                atr=_number(sum(ranges) / len(ranges)), relative_volume=_number(volumes[-1] / mean_volume) if mean_volume else None,
                high=max((r['high'] for r in rows), key=Decimal), low=min((r['low'] for r in rows), key=Decimal))


def _digest(value):
    return hashlib.sha256(json.dumps({k:v for k,v in value.items() if k not in ('snapshot_id', 'snapshot_hash', 'built_at')}, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _project(value, keys):
    return {k: value[k] for k in keys if k in value} if isinstance(value, dict) else None


async def build_analysis_context(store, feed, symbol, maximum_age, task_profile=None):
    """All public-market awaits precede the account read (and its DB lock)."""
    parse.symbol(symbol)
    if type(maximum_age) not in (float, int) or not 0 < maximum_age <= 3600:
        _fail()
    if task_profile is None:
        return store.analysis_context(symbol, feed.snapshot(), maximum_age)
    profile = validate_task_profile(task_profile)
    before = feed.snapshot()
    history = {}
    for interval, count in profile['windows'].items():
        if not callable(getattr(feed, 'candles', None)):
            history[interval] = {}
            continue
        try:
            history[interval] = await feed.candles(symbol, interval, count + 1)
        except (ApiError, OSError, TimeoutError):
            history[interval] = {}
    public = feed.snapshot()
    cutoff = parse.utc_now()
    cutoff_ms = int(cutoff.timestamp() * 1000)
    context = store.analysis_context(symbol, public, maximum_age)
    generation = public.get('source_generation', -1)
    source_id = public.get('source_id', 'unbound')
    matched = before.get('source_generation') == generation and before.get('source_id') == source_id and generation >= 0
    matched = matched and public.get('product') == 'SPOT' and public.get('venue') == 'BINANCE'
    sections = {}
    def section(ref, ok, reason='MISSING_OR_STALE'):
        sections[ref] = {'status': 'READY' if ok else 'MISSING', 'reason': None if ok else reason}
    quote = None
    try:
        candidate = context.get('quote')
        parsed = parse.quote(candidate, now=cutoff)
        age = cutoff.timestamp() - parse.timestamp(parsed['as_of'])
        if matched and candidate.get('stale') is False and 0 <= age <= maximum_age:
            quote = _project(parsed, ('symbol','price','change_pct','as_of'))
    except (parse.InvalidMarketData, TypeError, AttributeError):
        pass
    section('quote', quote is not None)
    bars, features, partial = {}, {}, {}
    for interval, count in profile['windows'].items():
        result = history[interval]
        rows = []
        try:
            if not matched or result.get('source_generation') != generation or result.get('source_id') != source_id or result.get('product') != 'SPOT':
                raise parse.InvalidMarketData()
            raw = result.get('candles', [])
            if not isinstance(raw, list) or len(raw) > count + 1:
                raise parse.InvalidMarketData()
            parsed = [parse.candle(r, interval, now=cutoff) for r in raw]
            if any(a['time'] >= b['time'] for a,b in zip(parsed, parsed[1:])):
                raise parse.InvalidMarketData()
            for row in parsed:
                if row.get('closed') is True and row['close_time'] <= cutoff_ms:
                    rows.append(row)
                elif row.get('closed') is False:
                    partial[interval] = row
            rows = rows[-count:]
            if len(rows) != count or any(b['time'] - a['time'] != parse.INTERVAL_MS[interval] for a,b in zip(rows, rows[1:])):
                raise parse.InvalidMarketData()
            if not 0 <= cutoff_ms - rows[-1]['close_time'] <= parse.INTERVAL_MS[interval] + maximum_age * 1000:
                raise parse.InvalidMarketData()
        except (parse.InvalidMarketData, KeyError, TypeError):
            rows = []
        bars[interval] = rows
        if rows:
            features[interval] = _features(rows)
        section('candles:' + interval, bool(rows))
    book = None
    try:
        candidate = parse.book(public.get('books', {}).get(symbol), now=cutoff)
        if matched and 0 <= cutoff.timestamp() - parse.timestamp(candidate['as_of']) <= maximum_age and public['books'][symbol].get('stale') is False:
            bid, ask = candidate['bids'][0][0], candidate['asks'][0][0]
            book = dict(as_of=candidate['as_of'], bid=bid, ask=ask, spread=_number(Decimal(ask)-Decimal(bid)))
    except (parse.InvalidMarketData, KeyError, TypeError):
        pass
    section('book', book is not None)
    section('news', False, 'NOT_CONFIGURED')
    missing_required = [r for r in profile['required'] if sections[r]['status'] != 'READY']
    missing_optional = [r for r in profile['optional'] if sections[r]['status'] != 'READY']
    snapshot = dict(schema_revision='analysis-snapshot-v1', evidence_cutoff=cutoff.isoformat(), built_at=cutoff.isoformat(),
                    source_id=source_id, source_generation=generation, source_revision=public.get('source_revision', 0),
                    instrument_ref='console-paper:' + symbol, venue='BINANCE', product='SPOT', quote_currency='USDT',
                    units={'price':'QUOTE_PER_BASE','volume':'BASE'}, task_profile=profile,
                    market=dict(quote=quote, candles=bars, features=features, book=book, partial=partial),
                    portfolio=dict(account=_project(context.get('account'), ('cash','equity','currency','paused')),
                                   position=_project(context.get('position'), ('symbol','quantity','average_price','avg_price','cost_basis')),
                                   active_orders=None, reserved_funds=None, protection_status='UNKNOWN'),
                    constraints={**(_project(context.get('risk'), ('max_position_pct','max_daily_loss')) or {}), 'execution_supported':False,'protection_supported':False},
                    costs=dict(fee_rate=None, fee_source='UNAVAILABLE', slippage_bps=None, complete=False),
                    previous_assessment=None,
                    quality=dict(status='DATA_BLOCKED' if missing_required else 'READY', missing_required=missing_required, missing_optional=missing_optional, sections=sections),
                    evidence_refs=[r for r,s in sections.items() if s['status']=='READY'] + ['features:' + i for i in features])
    snapshot['snapshot_hash'] = _digest(snapshot)
    snapshot['snapshot_id'] = 'snapshot:' + snapshot['snapshot_hash'][:32]
    context['analysis_snapshot'] = validate_snapshot(snapshot)
    return context


def validate_snapshot(snapshot):
    """Exact nested allowlist and deterministic provenance check, no free text/URLs."""
    from jsonschema import Draft202012Validator
    def obj(properties, required=None):
        return dict(type='object', properties=properties, additionalProperties=False, required=list(properties) if required is None else required)
    def nullable(schema):
        return {'anyOf':[schema, {'type':'null'}]}
    number = {'type':'string','pattern':r'^-?[0-9]+(?:\.[0-9]+)?$','maxLength':64}
    positive_int = {'type':'integer','minimum':0}
    boolean = {'type':'boolean'}
    text = {'type':'string','pattern':r'^[A-Za-z0-9_.:+ -]+$','maxLength':128}
    stamp = {'type':'string','maxLength':40,'pattern':r'^\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|\+00:00)$'}
    row = obj({**{k:number for k in ('open','high','low','close','volume')}, 'time':positive_int, 'close_time':positive_int, 'closed':boolean})
    windows = lambda schema: dict(type='object', properties={i:schema for i in parse.INTERVALS}, additionalProperties=False, maxProperties=3)
    feature = obj(dict(formula_revision={'const':'closed-v1'}, bars_used={'type':'integer','minimum':2,'maximum':199}, closed_until=positive_int,
                       **{k:number for k in ('return_pct','sma','atr','high','low')}, relative_volume=nullable(number)))
    refs = {'type':'array', 'items':text, 'maxItems':20, 'uniqueItems':True}
    account = obj(dict(cash=number,equity=number,currency={'const':'USDT'},paused=boolean), ['cash','currency','paused'])
    position = obj(dict(symbol={'enum':list(parse.ALL_SYMBOLS)}, **{k:number for k in ('quantity','average_price','avg_price','cost_basis')}), ['symbol','quantity'])
    section_schema = obj(dict(status={'enum':['READY','MISSING']},reason=nullable({'enum':['MISSING_OR_STALE','NOT_CONFIGURED']})))
    schema = obj(dict(schema_revision={'const':'analysis-snapshot-v1'}, snapshot_id=text, snapshot_hash={'type':'string','pattern':'^[a-f0-9]{64}$'},
                      evidence_cutoff=stamp,built_at=stamp,source_id=text,source_generation=positive_int,source_revision=positive_int,
                      instrument_ref={'enum':['console-paper:'+s for s in parse.ALL_SYMBOLS]},venue={'const':'BINANCE'},product={'const':'SPOT'},quote_currency={'const':'USDT'},
                      units=obj(dict(price={'const':'QUOTE_PER_BASE'},volume={'const':'BASE'})),task_profile={'type':'object'},
                      market=obj(dict(quote=nullable(obj(dict(symbol={'enum':list(parse.ALL_SYMBOLS)},price=number,change_pct=number,as_of=stamp))),
                                      candles=windows({'type':'array','items':row,'maxItems':199}),features=windows(feature),partial=windows(row),
                                      book=nullable(obj(dict(as_of=stamp,bid=number,ask=number,spread=number))))),
                      portfolio=obj(dict(account=account,position=nullable(position),active_orders={'type':'null'},reserved_funds={'type':'null'},protection_status={'const':'UNKNOWN'})),
                      constraints=obj(dict(max_position_pct=number,max_daily_loss=number,maximum_target_notional=number,execution_supported=boolean,protection_supported=boolean),['execution_supported','protection_supported']),
                      costs=obj(dict(fee_rate=nullable(number),fee_source={'enum':['UNAVAILABLE','PAPER_POLICY']},slippage_bps=nullable(number),complete=boolean)),previous_assessment={'type':'null'},
                      quality=obj(dict(status={'enum':['READY','DATA_BLOCKED']},missing_required=refs,missing_optional=refs,sections=dict(type='object',properties={r:section_schema for r in REFS},additionalProperties=False))),evidence_refs=refs))
    try:
        encoded = json.dumps(snapshot, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode()) > MAX_BYTES or not Draft202012Validator(schema).is_valid(snapshot):
            _fail()
        validate_task_profile(snapshot['task_profile'])
        if _digest(snapshot) != snapshot['snapshot_hash'] or snapshot['snapshot_id'] != 'snapshot:' + snapshot['snapshot_hash'][:32]:
            _fail()
        cutoff = parse.timestamp(parse.iso(snapshot['evidence_cutoff']))
        parse.iso(snapshot['built_at'])
        profile = snapshot['task_profile']
        if set(snapshot['market']['candles']) != set(profile['windows']):
            _fail()
        for interval, rows in snapshot['market']['candles'].items():
            for row in rows:
                parse.candle(row, interval)
                if row['closed'] is not True or row['close_time'] > cutoff * 1000:
                    _fail()
            if rows and snapshot['market']['features'].get(interval) != _features(rows):
                _fail()
            if rows and (len(rows) != profile['windows'][interval] or any(b['time']-a['time'] != parse.INTERVAL_MS[interval] for a,b in zip(rows, rows[1:]))):
                _fail()
        if set(snapshot['market']['features']) != {i for i,rows in snapshot['market']['candles'].items() if rows}:
            _fail()
        for interval, row in snapshot['market']['partial'].items():
            parse.candle(row, interval)
            if row['closed'] is not False or row['time'] > cutoff * 1000:
                _fail()
        quote = snapshot['market']['quote']
        if quote and (parse.timestamp(quote['as_of']) > cutoff or 'console-paper:' + quote['symbol'] != snapshot['instrument_ref']):
            _fail()
        if quote:
            parse.quote(quote)
        book = snapshot['market']['book']
        if book and (parse.timestamp(parse.iso(book['as_of'])) > cutoff or Decimal(book['ask']) <= Decimal(book['bid']) or Decimal(book['bid']) <= 0 or book['spread'] != _number(Decimal(book['ask'])-Decimal(book['bid']))):
            _fail()
        expected = {'quote':quote is not None, 'book':snapshot['market']['book'] is not None,'news':False}
        expected.update({'candles:'+i:bool(rows) for i,rows in snapshot['market']['candles'].items()})
        sections = snapshot['quality']['sections']
        if set(sections) != set(expected) or any((sections[r]['status'] == 'READY') != ok for r,ok in expected.items()):
            _fail()
        required = [r for r in profile['required'] if not expected[r]]
        optional = [r for r in profile['optional'] if not expected[r]]
        if snapshot['quality']['missing_required'] != required or snapshot['quality']['missing_optional'] != optional or snapshot['quality']['status'] != ('DATA_BLOCKED' if required else 'READY'):
            _fail()
        expected_refs = {r for r,ok in expected.items() if ok} | {'features:'+i for i in snapshot['market']['features']}
        if set(snapshot['evidence_refs']) != expected_refs:
            _fail()
        return json.loads(encoded)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, parse.InvalidMarketData):
        _fail()
