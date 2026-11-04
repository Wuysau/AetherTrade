"""A distinct, immutable Demo analysis schema. PAPER validation is unchanged."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import re

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, decimal, digest
from .analysis_snapshot import validate_task_profile, _features
from . import market_parse as parse

SCHEMA = 'demo-analysis-snapshot-v1'
TOP = {'schema_revision', 'scope', 'instrument_ref', 'venue', 'product', 'source_id', 'evidence_cutoff', 'built_at',
       'snapshot_id', 'snapshot_hash', 'task_profile', 'market', 'portfolio', 'constraints', 'costs', 'bindings', 'quality', 'evidence_refs', 'rules'}


def snapshot_hash(snapshot):
    return digest({k: v for k, v in snapshot.items() if k not in {'snapshot_id', 'snapshot_hash', 'built_at'}})


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise DemoError('DEMO_SNAPSHOT_SCHEMA_INVALID')


def build_demo_snapshot(*, scope, strategy, account, market, rules, qualification, code_sha):
    if scope.product != 'SPOT' or market.get('environment') != 'BINANCE_DEMO' or market.get('symbol') != 'BTCUSDT':
        raise DemoError('DEMO_MARKET_SOURCE_INVALID')
    profile = validate_task_profile(strategy['profile'])
    now = datetime.now(timezone.utc)
    bid, ask = decimal(market['bid'], positive=True), decimal(market['ask'], positive=True)
    if bid >= ask:
        raise DemoError('DEMO_BOOK_INVALID')
    price = (bid + ask) / 2
    rows, features = {}, {}
    for interval, count in profile['windows'].items():
        candidates = [parse.candle(item, interval, now=now) for item in market.get('candles', {}).get(interval, [])]
        bars = [item for item in candidates if item.get('closed') is True][-count:]
        if len(bars) != count or any(b['time'] - a['time'] != parse.INTERVAL_MS[interval] for a, b in zip(bars, bars[1:])):
            bars = []
        rows[interval] = bars
        if bars:
            features[interval] = _features(bars)
    cash = decimal(account['balances'].get('USDT', {}).get('free', '0'))
    base = decimal(account['position'])
    budget = min(decimal(strategy['budget'], positive=True), Decimal('10000'))
    fee = account.get('fee_rate')
    fee_cap = decimal(strategy['fee_rate_cap'])
    complete = fee is not None and decimal(fee) <= fee_cap
    quote_age = (now - datetime.fromisoformat(market['as_of'])).total_seconds()
    account_age = (now - datetime.fromisoformat(account['observed_at'])).total_seconds()
    sections = {'quote': {'status': 'READY' if 0 <= quote_age <= 5 else 'MISSING', 'reason': None if 0 <= quote_age <= 5 else 'QUOTE_STALE'},
                'book': {'status': 'READY' if 0 <= quote_age <= 5 else 'MISSING', 'reason': None},
                'news': {'status': 'MISSING', 'reason': 'NOT_CONFIGURED'}}
    for interval, bars in rows.items():
        sections['candles:' + interval] = {'status': 'READY' if bars else 'MISSING', 'reason': None if bars else 'MISSING_OR_STALE'}
    missing = [ref for ref in profile['required'] if sections[ref]['status'] != 'READY']
    if not 0 <= account_age <= 15:
        missing.append('account')
    snapshot = {'schema_revision': SCHEMA, 'scope': asdict(scope), 'instrument_ref': 'binance-demo:SPOT:BTCUSDT',
        'venue': 'BINANCE', 'product': 'SPOT', 'source_id': 'BINANCE_DEMO:SPOT', 'evidence_cutoff': now.isoformat(), 'built_at': now.isoformat(),
        'task_profile': profile,
        'market': {'quote': {'symbol': 'BTCUSDT', 'price': str(price), 'change_pct': '0', 'as_of': market['as_of']},
                   'book': {'bid': str(bid), 'ask': str(ask), 'spread': str(ask - bid), 'as_of': market['as_of']}, 'candles': rows, 'features': features, 'execution_context': deepcopy(market.get('execution_context', {}))},
        'portfolio': {'account': {'cash': str(cash), 'equity': str(cash + base * price), 'currency': 'USDT', 'paused': account['state'] != 'READY'},
                      'position': {'symbol': 'BTCUSDT', 'quantity': str(base)} if base else None, 'previous_proposal': deepcopy(account.get('previous_proposal'))},
        'constraints': {'maximum_target_notional': str(budget), 'max_planned_loss': strategy['max_planned_loss'], 'max_daily_loss': strategy['max_daily_loss'],
                        'protection_supported': qualification.get('state') == 'DEMO_VERIFIED' and qualification.get('protection') == strategy['protection_mode'],
                        'execution_supported': qualification.get('state') == 'DEMO_VERIFIED'},
        'costs': {'fee_rate': str(decimal(fee)) if fee is not None else None, 'fee_rate_cap': str(fee_cap), 'fee_source': 'BINANCE_DEMO_ACCOUNT' if complete else 'UNAVAILABLE', 'complete': complete},
        'bindings': {'account_revision': account['revision'], 'credential_revision': account['credential_revision'], 'account_as_of': account['observed_at'],
                     'plan_revision': account.get('plan_revision', 0), 'protection_hash': account.get('protection_hash', digest([])),
                     'strategy_revision': strategy['revision'], 'policy_revision': strategy['policy_revision'], 'model_revisions': strategy['model_revisions'],
                     'rules_hash': digest(rules), 'qualification_hash': digest(qualification), 'code_sha': code_sha,
                     'authorization_ref': strategy['authorization_ref'], 'authorized_until': strategy['authorized_until']},
        'quality': {'status': 'DATA_BLOCKED' if missing else 'READY', 'missing_required': missing,
                    'missing_optional': [ref for ref in profile['optional'] if sections[ref]['status'] != 'READY'], 'sections': sections},
        'evidence_refs': [ref for ref, value in sections.items() if value['status'] == 'READY'], 'rules': deepcopy(rules)}
    snapshot['snapshot_hash'] = snapshot_hash(snapshot)
    snapshot['snapshot_id'] = 'demo-snapshot:' + snapshot['snapshot_hash'][:32]
    return validate_demo_snapshot(snapshot)


def validate_demo_snapshot(snapshot):
    try:
        _exact(snapshot, TOP)
        if len(json.dumps(snapshot, allow_nan=False).encode()) > 96000 or snapshot['schema_revision'] != SCHEMA:
            raise DemoError('DEMO_SNAPSHOT_SCHEMA_INVALID')
        scope = DemoScope(**snapshot['scope'])
        if scope.product != 'SPOT' or snapshot['source_id'] != 'BINANCE_DEMO:SPOT' or snapshot['venue'] != 'BINANCE' or snapshot['product'] != 'SPOT' or snapshot['instrument_ref'] != 'binance-demo:SPOT:BTCUSDT':
            raise DemoError('DEMO_SNAPSHOT_SOURCE_INVALID')
        if snapshot_hash(snapshot) != snapshot['snapshot_hash'] or snapshot['snapshot_id'] != 'demo-snapshot:' + snapshot['snapshot_hash'][:32]:
            raise DemoError('DEMO_SNAPSHOT_HASH_INVALID')
        profile = validate_task_profile(snapshot['task_profile'])
        _exact(snapshot['market'], {'quote', 'book', 'candles', 'features', 'execution_context'})
        quote, book = snapshot['market']['quote'], snapshot['market']['book']
        _exact(quote, {'symbol', 'price', 'change_pct', 'as_of'}); _exact(book, {'bid', 'ask', 'spread', 'as_of'})
        if quote['symbol'] != 'BTCUSDT' or decimal(quote['price'], positive=True) != (decimal(book['bid'], positive=True) + decimal(book['ask'], positive=True)) / 2:
            raise DemoError('DEMO_SNAPSHOT_QUOTE_INVALID')
        if decimal(book['ask']) <= decimal(book['bid']) or decimal(book['spread']) != decimal(book['ask']) - decimal(book['bid']):
            raise DemoError('DEMO_SNAPSHOT_QUOTE_INVALID')
        cutoff = datetime.fromisoformat(snapshot['evidence_cutoff'])
        if cutoff.tzinfo is None or datetime.fromisoformat(quote['as_of']) > cutoff or datetime.fromisoformat(book['as_of']) > cutoff:
            raise DemoError('DEMO_SNAPSHOT_TIME_INVALID')
        context = snapshot['market']['execution_context']
        if context:
            _exact(context, {'average_price', 'average_minutes', 'last_price', 'open_order_count', 'exchange_open_order_count', 'account_base_quantity', 'pending_buy_quantity', 'as_of'})
            for field in ('average_price', 'last_price', 'account_base_quantity', 'pending_buy_quantity'): decimal(context[field])
            if any(type(context[field]) is not int or context[field] < 0 for field in ('average_minutes', 'open_order_count', 'exchange_open_order_count')) or not 0 <= (cutoff - datetime.fromisoformat(context['as_of'])).total_seconds() <= 5:
                raise DemoError('DEMO_FILTER_CONTEXT_INVALID')
        if set(snapshot['market']['candles']) != set(profile['windows']):
            raise DemoError('DEMO_SNAPSHOT_BARS_INVALID')
        expected = {'quote': 0 <= (cutoff - datetime.fromisoformat(quote['as_of'])).total_seconds() <= 5,
                    'book': 0 <= (cutoff - datetime.fromisoformat(book['as_of'])).total_seconds() <= 5, 'news': False}
        for interval, rows in snapshot['market']['candles'].items():
            if rows:
                parsed = [parse.candle(row, interval, now=cutoff) for row in rows]
                if len(rows) != profile['windows'][interval] or any(row.get('closed') is not True for row in parsed) or any(b['time'] - a['time'] != parse.INTERVAL_MS[interval] for a, b in zip(parsed, parsed[1:])) or snapshot['market']['features'].get(interval) != _features(parsed):
                    raise DemoError('DEMO_SNAPSHOT_BARS_INVALID')
                if not 0 <= cutoff.timestamp() * 1000 - parsed[-1]['close_time'] <= parse.INTERVAL_MS[interval] + 5000:
                    raise DemoError('DEMO_SNAPSHOT_BARS_STALE')
            expected['candles:' + interval] = bool(rows)
        _exact(snapshot['portfolio'], {'account', 'position', 'previous_proposal'})
        _exact(snapshot['portfolio']['account'], {'cash', 'equity', 'currency', 'paused'})
        if snapshot['portfolio']['account']['currency'] != 'USDT' or type(snapshot['portfolio']['account']['paused']) is not bool:
            raise DemoError('DEMO_SNAPSHOT_PORTFOLIO_INVALID')
        for field in ('cash', 'equity'): decimal(snapshot['portfolio']['account'][field])
        if snapshot['portfolio']['position'] is not None:
            _exact(snapshot['portfolio']['position'], {'symbol', 'quantity'}); decimal(snapshot['portfolio']['position']['quantity'])
        if snapshot['portfolio']['previous_proposal'] is not None:
            from trading_system.contracts.wire import ContractCodec
            previous = ContractCodec().decode('decision-proposal', snapshot['portfolio']['previous_proposal'])
            if previous.instrument_ref != snapshot['instrument_ref'] or snapshot['bindings']['plan_revision'] < 1:
                raise DemoError('DEMO_PREVIOUS_PROPOSAL_NOT_BOUND')
        _exact(snapshot['constraints'], {'maximum_target_notional', 'max_planned_loss', 'max_daily_loss', 'protection_supported', 'execution_supported'})
        for field in ('maximum_target_notional', 'max_planned_loss', 'max_daily_loss'): decimal(snapshot['constraints'][field])
        if any(type(snapshot['constraints'][field]) is not bool for field in ('protection_supported', 'execution_supported')):
            raise DemoError('DEMO_SNAPSHOT_CONSTRAINT_INVALID')
        _exact(snapshot['costs'], {'fee_rate', 'fee_rate_cap', 'fee_source', 'complete'})
        costs = snapshot['costs']; decimal(costs['fee_rate_cap'])
        complete = costs['fee_rate'] is not None and decimal(costs['fee_rate']) <= decimal(costs['fee_rate_cap'])
        if costs['complete'] is not complete or costs['fee_source'] != ('BINANCE_DEMO_ACCOUNT' if complete else 'UNAVAILABLE'):
            raise DemoError('DEMO_SNAPSHOT_COST_INVALID')
        _exact(snapshot['bindings'], {'account_revision', 'credential_revision', 'account_as_of', 'strategy_revision', 'policy_revision', 'model_revisions', 'rules_hash', 'qualification_hash', 'code_sha', 'authorization_ref', 'authorized_until', 'plan_revision', 'protection_hash'})
        if snapshot['bindings']['rules_hash'] != digest(snapshot['rules']) or snapshot['bindings']['strategy_revision'] != profile['strategy_revision']:
            raise DemoError('DEMO_SNAPSHOT_BINDING_INVALID')
        for field in ('account_revision', 'credential_revision', 'strategy_revision', 'policy_revision', 'authorized_until', 'plan_revision'):
            if type(snapshot['bindings'][field]) is not int or snapshot['bindings'][field] < 0: raise DemoError('DEMO_SNAPSHOT_BINDING_INVALID')
        if not re.fullmatch(r'[a-f0-9]{40}', snapshot['bindings']['code_sha'] or '') or not re.fullmatch(r'[a-f0-9]{64}', snapshot['bindings']['qualification_hash']):
            raise DemoError('DEMO_SNAPSHOT_BINDING_INVALID')
        if not isinstance(snapshot['bindings']['model_revisions'], dict) or any(type(v) is not int or v < 1 for v in snapshot['bindings']['model_revisions'].values()):
            raise DemoError('DEMO_SNAPSHOT_BINDING_INVALID')
        if not re.fullmatch(r'[a-f0-9]{64}', snapshot['bindings']['protection_hash']):
            raise DemoError('DEMO_SNAPSHOT_BINDING_INVALID')
        _exact(snapshot['quality'], {'status', 'missing_required', 'missing_optional', 'sections'})
        if set(snapshot['quality']['sections']) != set(expected):
            raise DemoError('DEMO_SNAPSHOT_QUALITY_INVALID')
        required = [ref for ref in profile['required'] if not expected[ref]]
        if not 0 <= (cutoff - datetime.fromisoformat(snapshot['bindings']['account_as_of'])).total_seconds() <= 15:
            required.append('account')
        optional = [ref for ref in profile['optional'] if not expected[ref]]
        if snapshot['quality']['missing_required'] != required or snapshot['quality']['missing_optional'] != optional or snapshot['quality']['status'] != ('DATA_BLOCKED' if required else 'READY'):
            raise DemoError('DEMO_SNAPSHOT_QUALITY_INVALID')
        for ref, section in snapshot['quality']['sections'].items():
            _exact(section, {'status', 'reason'})
            if ref not in expected or section['status'] != ('READY' if expected[ref] else 'MISSING'): raise DemoError('DEMO_SNAPSHOT_QUALITY_INVALID')
        if set(snapshot['evidence_refs']) != {ref for ref, section in snapshot['quality']['sections'].items() if section['status'] == 'READY'}:
            raise DemoError('DEMO_SNAPSHOT_EVIDENCE_INVALID')
        return deepcopy(snapshot)
    except DemoError:
        raise
    except Exception:
        raise DemoError('DEMO_SNAPSHOT_INVALID') from None
