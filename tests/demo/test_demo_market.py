"""Bounded quote-budget BUY / owned-quantity SELL, still unqualified offline."""
from copy import deepcopy
from decimal import Decimal
import pytest
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.engine.binance_demo.orders import compile_spot_quote_order
from trading_system.execution.demo_reports import DemoReportVerifier
from test_lifecycle import repo, scope, register, observed
from test_transport import RULES


def rules():
    value = {**deepcopy(RULES), 'quoteOrderQtyMarketAllowed': True, 'quoteAssetPrecision': 8}
    value['filters'][-1]['avgPriceMins'] = 5
    return value


def market_request():
    return compile_spot_quote_order(rules(), quote_quantity='50', client_id='market-original', reference_price='50000')


def market_stage(repo, scope):
    register(repo, scope)
    return repo.stage(scope, command_id='market', request=market_request(), reserve_asset='USDT', reserve_amount='50.05', available='1000',
        authorization_ref='market-grant', expires_at=200, now=100,
        risk_receipt={'market_risk': {'sizing': 'QUOTE_NOTIONAL', 'principal_cap': '50', 'fee_rate_cap': '0.001', 'fee_assets': ['BTC', 'USDT'], 'price_policy': 'UNBOUNDED_EXECUTION_PRICE'}},
        protection_plan={'stop_price': '49000', 'maximum_holding_seconds': 3600, 'mode': 'HOSTED', 'authorization_ref': 'market-grant'})


def quote_report(**changes):
    return {'e': 'executionReport', 'E': 105000, 's': 'BTCUSDT', 'c': 'market-original', 'S': 'BUY', 'o': 'MARKET', 'q': '0', 'Q': '50', 'p': '0',
        'x': 'TRADE', 'X': 'FILLED', 'i': 25, 't': 35, 'l': '0.00099', 'L': '50000', 'z': '0.00099', 'Z': '49.5', 'Y': '49.5', 'n': '0.0495', 'N': 'USDT', **changes}


def test_market_quote_compile_never_invents_base_quantity():
    request = market_request()
    assert request == {'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'MARKET', 'quoteOrderQty': '50', 'newClientOrderId': 'market-original'}
    with pytest.raises(DemoError):
        compile_spot_quote_order({**rules(), 'quoteOrderQtyMarketAllowed': False}, quote_quantity='50', client_id='x', reference_price='50000')


def test_under_spent_market_fill_reconciles_both_units_and_fee(repo, scope):
    market_stage(repo, scope)
    repo.begin_send(scope, 'market', expected_revision=0, now=101)
    verifier = DemoReportVerifier(repo)
    oid = observed(repo, scope, quote_report())
    assert verifier.process(scope, oid)
    assert not verifier.process(scope, oid)
    command = repo.command(scope, 'market')
    assert command['reserve_amount'] == '0'
    assert Decimal(command['cumulative_qty']) == Decimal('0.00099')
    assert Decimal(command['cumulative_quote']) == Decimal('49.5')
    assert repo.asset_deltas(scope)['USDT'] == Decimal('-49.5495')


def test_quote_budget_binding_cannot_be_missing(repo, scope):
    market_stage(repo, scope)
    repo.begin_send(scope, 'market', expected_revision=0, now=101)
    event = quote_report(); event.pop('Q')
    with pytest.raises(DemoError): DemoReportVerifier(repo).process(scope, observed(repo, scope, event))
    assert repo.health(scope)['fill_count'] == 0


def test_market_fee_cap_breach_records_fill_and_disables_qualification(repo, scope):
    market_stage(repo, scope)
    repo.begin_send(scope, 'market', expected_revision=0, now=101)
    DemoReportVerifier(repo).process(scope, observed(repo, scope, quote_report(n='0.10')))
    assert repo.health(scope)['fill_count'] == 1
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'
    assert repo.account(scope)['qualification'] is None
    with repo.engine.connect() as conn:
        from sqlalchemy import select
        from trading_system.storage.demo import outbox
        assert 'EXECUTION_RISK_BREACH' in conn.execute(select(outbox.c.kind)).scalars().all()


def test_quote_market_fails_closed_for_unknown_or_position_filter():
    value = rules()
    value['filters'].append({'filterType': 'MAX_POSITION', 'maxPosition': '1'})
    with pytest.raises(DemoError, match='FILTER_NOT_QUALIFIED'):
        compile_spot_quote_order(value, quote_quantity='50', client_id='x', reference_price='50000')


def test_migration_preserves_prior_commands_and_defaults_quote_to_zero():
    import sqlite3
    from pathlib import Path
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE demo_commands (command_id TEXT PRIMARY KEY)')
    db.execute("INSERT INTO demo_commands VALUES ('existing')")
    db.commit()
    sql = (Path(__file__).resolve().parents[2] / 'migrations/0036_binance_demo_market_quote.sql').read_text()
    db.executescript(sql)
    assert db.execute('SELECT command_id, cumulative_quote FROM demo_commands').fetchone() == ('existing', '0')
    db.close()


def test_terminal_quote_order_without_trades_keeps_reservation(repo, scope):
    market_stage(repo, scope)
    repo.begin_send(scope, 'market', expected_revision=0, now=101)
    verifier = DemoReportVerifier(repo)
    event = quote_report(x='REST_ORDER')
    verifier.process(scope, observed(repo, scope, event))
    assert Decimal(repo.command(scope, 'market')['reserve_amount']) == Decimal('50.05')
    verifier.process(scope, observed(repo, scope, quote_report()))
    assert repo.command(scope, 'market')['reserve_amount'] == '0'


def test_market_buy_fee_exhausting_base_is_durable_breach(repo, scope):
    market_stage(repo, scope)
    repo.begin_send(scope, 'market', expected_revision=0, now=101)
    DemoReportVerifier(repo).process(scope, observed(repo, scope, quote_report(n='0.001', N='BTC')))
    assert repo.health(scope)['fill_count'] == 1
    assert repo.asset_deltas(scope)['BTC'] == Decimal('-0.00001')
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'
    assert repo.account(scope)['qualification'] is None


def test_market_sell_excess_base_fee_records_unmatched_exposure(repo, scope):
    from test_management import buy
    buy(repo, scope)
    request = {'symbol': 'BTCUSDT', 'side': 'SELL', 'type': 'MARKET', 'quantity': '0.00998', 'newClientOrderId': 'market-sell'}
    repo.stage(scope, command_id='sell', request=request, reserve_asset='BTC', reserve_amount='0.00998998', available='0.00999', authorization_ref='grant', expires_at=200, now=110, purpose='REDUCTION', risk_receipt={'market_risk': {'sizing': 'BASE_QUANTITY', 'principal_cap': '0.00998', 'fee_rate_cap': '0.001', 'fee_assets': ['BTC', 'USDT'], 'price_policy': 'UNBOUNDED_EXECUTION_PRICE'}})
    repo.begin_send(scope, 'sell', expected_revision=1, now=111)
    event = quote_report(c='market-sell', S='SELL', q='0.00998', Q='0', i=26, t=36, l='0.00998', L='50000', z='0.00998', Z='499', Y='499', n='0.00002', N='BTC')
    DemoReportVerifier(repo).process(scope, observed(repo, scope, event))
    assert repo.health(scope)['fill_count'] == 2
    assert repo.asset_deltas(scope)['BTC'] == Decimal('-0.00001')
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'
    assert repo.account(scope)['qualification'] is None


@pytest.mark.parametrize('field,value', [('applyToMarket', None), ('applyToMarket', 'true'), ('avgPriceMins', None), ('avgPriceMins', True), ('minNotional', None), ('minNotional', 'NaN')])
def test_market_notional_rule_requires_valid_explicit_fields(field, value):
    malformed = rules()
    rule = malformed['filters'][-1]
    if value is None: rule.pop(field)
    else: rule[field] = value
    with pytest.raises(DemoError):
        compile_spot_quote_order(malformed, quote_quantity='50', client_id='x', reference_price='50000')


def test_market_notional_filter_rejects_duplicate_types():
    malformed = rules(); malformed['filters'].append(deepcopy(malformed['filters'][-1]))
    with pytest.raises(DemoError):
        compile_spot_quote_order(malformed, quote_quantity='50', client_id='x', reference_price='50000')


@pytest.mark.parametrize('field,value', [('applyMinToMarket', None), ('applyMaxToMarket', 'false'), ('avgPriceMins', -1), ('maxNotional', None)])
def test_notional_filter_requires_valid_market_fields(field, value):
    malformed = rules()
    malformed['filters'][-1] = {'filterType': 'NOTIONAL', 'minNotional': '5', 'maxNotional': '10000', 'applyMinToMarket': True, 'applyMaxToMarket': False, 'avgPriceMins': 5}
    rule = malformed['filters'][-1]
    if value is None: rule.pop(field)
    else: rule[field] = value
    with pytest.raises(DemoError):
        compile_spot_quote_order(malformed, quote_quantity='50', client_id='x', reference_price='50000')


def test_base_market_rejects_missing_market_flags_and_requires_average_evidence():
    from trading_system.engine.binance_demo.orders import compile_spot_order
    args = dict(side='SELL', order_type='MARKET', quantity='0.001', client_id='x', reference_price='50000')
    malformed = rules(); malformed['filters'][-1].pop('applyToMarket')
    with pytest.raises(DemoError): compile_spot_order(malformed, **args)
    with pytest.raises(DemoError, match='AVERAGE_PRICE_EVIDENCE_REQUIRED'): compile_spot_order(rules(), **args)
    assert compile_spot_order(rules(), **args, filter_context={'average_minutes': 5, 'average_price': '50000'})['quantity'] == '0.001'


def test_fee_breach_blocks_new_entry_but_retains_qualified_cancel(repo, scope, monkeypatch):
    from contextlib import contextmanager
    from dataclasses import replace
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    from trading_system.execution.demo_commands import DemoExecutionOwner
    from test_lifecycle import ready_context
    market_stage(repo, scope)
    context = ready_context()
    proof = {'state': 'DEMO_VERIFIED', 'code_sha': context.code_sha, 'account_key': scope.account_key, 'credential_revision': 0, 'product': 'SPOT',
             'capabilities': {name: {'state':'DEMO_VERIFIED', 'evidence_sha256':'f'*64, 'official_contract_revision':'offline-only'} for name in ('CANCEL', 'LIMIT')}}
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).values(qualification=proof))
    repo.begin_send(scope, 'market', expected_revision=0, now=101)
    DemoReportVerifier(repo).process(scope, observed(repo, scope, quote_report(n='0.10', X='PARTIALLY_FILLED')))
    assert repo.account(scope)['qualification'] == proof
    assert repo.health(scope)['risk_breaches'] == 1
    with pytest.raises(DemoError, match='EXECUTION_RISK_BREACH'):
        market_stage(repo, scope)
    cancel = repo.stage(scope, command_id='cancel-market', request={'symbol':'BTCUSDT', 'origClientOrderId':'market-original'}, method='DELETE', purpose='CANCEL',
        reserve_asset='BTC', reserve_amount='0', available='0', authorization_ref='grant', expires_at=200, now=110)
    @contextmanager
    def offline_guard(_): yield
    monkeypatch.setattr(repo, 'owner_guard', offline_guard)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def cancel_order(self, params, *, permit):
            permit.consume('DELETE', params, self.credential_ref)
            return {'symbol':'BTCUSDT', 'origClientOrderId':'market-original', 'orderId':25}
    result = DemoExecutionOwner(repo, Transport(), clock=lambda:110).dispatch(scope, cancel['command_id'], context=replace(context, account_state='RECOVERY_REQUIRED', decision_expires_at=200))
    assert result['state'] == 'ACKNOWLEDGED'
    terminal = quote_report(x='CANCELED', X='CANCELED')
    DemoReportVerifier(repo).process(scope, observed(repo, scope, terminal))
    duty = repo.protection_duties(scope)[0]
    exit_request = {'symbol':'BTCUSDT', 'side':'SELL', 'type':'LIMIT', 'quantity':'0.0009', 'price':'49000', 'newClientOrderId':'protect', 'timeInForce':'GTC'}
    exit_command = repo.stage(scope, command_id='protect', request=exit_request, reserve_asset='BTC', reserve_amount='0.0009', available='0.00099', authorization_ref='grant', expires_at=200, now=110, purpose='PROTECTION', risk_receipt={'protection_fill_keys':[duty['fill_key']]})
    repo.bind_mitigation(scope, [duty['fill_key']], 'protect')
    def send_order(params, *, permit):
        permit.consume('POST', params, 'demo-session:fixture')
        return {'symbol':'BTCUSDT', 'clientOrderId':'protect', 'orderId':26}
    transport = Transport(); transport.send_order = send_order
    result = DemoExecutionOwner(repo, transport, clock=lambda:110).dispatch(scope, exit_command['command_id'], context=replace(context, account_state='RECOVERY_REQUIRED', quote_at=110, account_at=110, decision_expires_at=200))
    assert result['state'] == 'ACKNOWLEDGED'
