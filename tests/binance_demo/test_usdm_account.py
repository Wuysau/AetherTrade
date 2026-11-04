"""Offline contract tests; no venue calls or execution qualification."""
from decimal import Decimal

import pytest

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, DemoEndpointRegistry
from trading_system.engine.binance_demo.usdm_account import (
    PositionMode, account_update, asset_balances, position_mode, position_risk, symbol_configs,
)


@pytest.fixture
def scope():
    return DemoScope(account_id='42', account_epoch='e1', strategy_id='s1', product='USDM')


def test_explicit_position_mode_requires_actual_boolean():
    assert position_mode({'dualSidePosition': True}) == PositionMode.HEDGE
    assert position_mode({'dualSidePosition': False}) == PositionMode.ONE_WAY
    for raw in ({}, {'dualSidePosition': 'false'}, {'dualSidePosition': 0}):
        with pytest.raises(DemoError):
            position_mode(raw)


def test_position_risk_preserves_signed_legs_and_absence(scope):
    rows = [{'symbol': 'BTCUSDT', 'positionSide': 'LONG', 'positionAmt': '1.25','updateTime':0},
            {'symbol': 'BTCUSDT', 'positionSide': 'SHORT', 'positionAmt': '-0.5','updateTime':0}]
    result = position_risk(scope, PositionMode.HEDGE, rows)
    assert [p.update_time_ms for p in result] == [0, 0]
    assert [(p.symbol, p.side, p.amount) for p in result] == [
        ('BTCUSDT', 'LONG', Decimal('1.25')), ('BTCUSDT', 'SHORT', Decimal('-0.5'))]
    assert position_risk(scope, PositionMode.HEDGE, []) == ()  # no inferred flat legs
    assert position_risk(scope, PositionMode.ONE_WAY, [
        {'symbol':'BTCUSDT', 'positionSide':'BOTH', 'positionAmt':'-2','updateTime':0}])[0].amount == Decimal('-2')


@pytest.mark.parametrize('mode,row', [
    (PositionMode.ONE_WAY, {'symbol':'BTCUSDT','positionSide':'LONG','positionAmt':'1','updateTime':0}),
    (PositionMode.HEDGE, {'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':'1','updateTime':0}),
    (PositionMode.HEDGE, {'symbol':'BTCUSDT','positionSide':'LONG','positionAmt':'-1','updateTime':0}),
    (PositionMode.HEDGE, {'symbol':'BTCUSDT','positionSide':'SHORT','positionAmt':'1','updateTime':0}),
    (PositionMode.ONE_WAY, {'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':'NaN','updateTime':0}),
    (PositionMode.ONE_WAY, {'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':1,'updateTime':0}),
    (PositionMode.ONE_WAY, {'symbol':'BTCUSDT','positionSide':'BOTH'}),
])
def test_position_risk_rejects_inconsistent_or_malformed(scope, mode, row):
    with pytest.raises(DemoError):
        position_risk(scope, mode, [row])


def test_reject_duplicate_positions_and_non_usdm_scope(scope):
    row = {'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':'0','updateTime':0}
    with pytest.raises(DemoError):
        position_risk(scope, PositionMode.ONE_WAY, [row, row])
    with pytest.raises(DemoError):
        position_risk(DemoScope(account_id='42', account_epoch='e1', strategy_id='s1'), PositionMode.ONE_WAY, [])
    with pytest.raises(DemoError):
        position_risk(scope, 'ONE_WAY', [])


def test_symbol_config_retains_explicit_margin_and_leverage(scope):
    configs = symbol_configs(scope, [{'symbol':'BTCUSDT','marginType':'CROSSED','leverage':20},
                                     {'symbol':'ETHUSDT','marginType':'ISOLATED','leverage':5}])
    assert [(c.symbol,c.margin_type,c.leverage) for c in configs] == [('BTCUSDT','CROSSED',20), ('ETHUSDT','ISOLATED',5)]
    for row in ({'symbol':'BTCUSDT','marginType':'INVALID','leverage':20},
                {'symbol':'BTCUSDT','marginType':'CROSSED','leverage':True},
                {'symbol':'BTCUSDT','marginType':'CROSSED','leverage':0},
                {'symbol':'BTCUSDT','marginType':'CROSSED'}):
        with pytest.raises(DemoError): symbol_configs(scope, [row])
    with pytest.raises(DemoError): symbol_configs(scope, [{'symbol':'BTCUSDT','marginType':'CROSSED','leverage':20}]*2)


def test_asset_balances_keep_separate_values_and_no_total_currency_assumption(scope):
    raw = {'asset':'USDT','walletBalance':'100','marginBalance':'98', 'unrealizedProfit':'-2',
           'availableBalance':'80','initialMargin':'18','maintMargin':'1','updateTime':0}
    result = asset_balances(scope, {'assets':[raw], 'totalWalletBalance':'999'})
    assert len(result) == 1
    assert result[0].update_time_ms == 0
    assert (result[0].asset,result[0].wallet_balance,result[0].margin_balance,result[0].unrealized_profit,
            result[0].available_balance,result[0].initial_margin,result[0].maintenance_margin) == (
                'USDT',Decimal('100'),Decimal('98'),Decimal('-2'),Decimal('80'),Decimal('18'),Decimal('1'))
    with pytest.raises(DemoError): asset_balances(scope, {'assets':[raw, raw]})
    with pytest.raises(DemoError): asset_balances(scope, {'assets':[{'asset':'USDT'}]})


def test_incremental_account_update_does_not_imply_flat_or_derive_funding(scope):
    event = {'e':'ACCOUNT_UPDATE','E':1700000000000,'T':1700000000001,'a':{'m':'FUNDING_FEE','B':[{'a':'USDT','wb':'100','cw':'90','bc':'-0.1'}]}}
    update = account_update(scope, PositionMode.HEDGE, event)
    assert update.reason == 'FUNDING_FEE'
    assert (update.event_time_ms, update.transaction_time_ms) == (1700000000000, 1700000000001)
    assert update.positions == ()
    assert update.balances[0].balance_change == Decimal('-0.1')
    assert update.positions_present is False
    event['a']['P'] = [{'s':'BTCUSDT','ps':'SHORT','pa':'-0.25','mt':'isolated'}]
    update = account_update(scope, PositionMode.HEDGE, event)
    assert update.positions_present is True
    assert update.positions[0].amount == Decimal('-0.25')
    assert update.positions[0].margin_type == 'ISOLATED'
    event['a']['P'] = []
    assert account_update(scope, PositionMode.HEDGE, event).positions_present is True


def test_event_rejects_duplicates_wrong_type_and_unrecognized_margin(scope):
    position = {'s':'BTCUSDT','ps':'LONG','pa':'1','mt':'cross'}
    for event in [
        {'e':'ACCOUNT_CONFIG_UPDATE','a':{'m':'ORDER','P':[]}},
        {'e':'ACCOUNT_UPDATE','E':1700000000000,'T':1700000000001,'a':{'m':'ORDER','P':[position,position]}},
        {'e':'ACCOUNT_UPDATE','E':1700000000000,'T':1700000000001,'a':{'m':'ORDER','P':[dict(position,mt='UNKNOWN')]}},
        {'e':'ACCOUNT_UPDATE','E':1700000000000,'T':1700000000001,'a':{'m':'ORDER','B':[{'a':'USDT','wb':'1','cw':'1'}]}},
    ]:
        with pytest.raises(DemoError): account_update(scope, PositionMode.HEDGE, event)


def test_usdm_endpoint_still_unqualified():
    with pytest.raises(DemoError, match='DEMO_PRODUCT_NOT_QUALIFIED'):
        DemoEndpointRegistry.url('USDM','GET','/fapi/v3/positionRisk')


def test_delivery_symbol_and_malformed_event_margin_type(scope):
    row = {'symbol':'BTCUSDT_250627','positionSide':'BOTH','positionAmt':'0','updateTime':0}
    assert position_risk(scope, PositionMode.ONE_WAY, [row])[0].symbol == 'BTCUSDT_250627'
    with pytest.raises(DemoError):
        account_update(scope, PositionMode.ONE_WAY, {'e':'ACCOUNT_UPDATE','E':1700000000000,'T':1700000000001,'a':{'m':'ORDER',
            'P':[{'s':'BTCUSDT','ps':'BOTH','pa':'0','mt':[]}]}})


def test_observations_retain_distinct_scope_identity(scope):
    other = DemoScope(account_id='43', account_epoch='e1', strategy_id='s1', product='USDM')
    row = {'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':'0','updateTime':0}
    assert position_risk(scope, PositionMode.ONE_WAY, [row])[0] != position_risk(other, PositionMode.ONE_WAY, [row])[0]


@pytest.mark.parametrize('amount', ['1_000', ' 1', '1 ', '', 'Infinity', '-Infinity', 'sNaN'])
def test_rejects_non_venue_decimal_strings(scope, amount):
    with pytest.raises(DemoError):
        position_risk(scope, PositionMode.ONE_WAY, [
            {'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':amount,'updateTime':0}])


@pytest.mark.parametrize('field,value', [('E', None), ('T', None), ('E', True), ('T', '1700000000000'), ('E', -1), ('T', 2**63)])
def test_event_rejects_missing_or_malformed_timestamps(scope, field, value):
    event = {'e':'ACCOUNT_UPDATE','E':1700000000000,'T':1700000000001,'a':{'m':'ORDER'}}
    if value is None:
        del event[field]
    else:
        event[field] = value
    with pytest.raises(DemoError):
        account_update(scope, PositionMode.ONE_WAY, event)


@pytest.mark.parametrize('value', [None, True, '1700000000000', -1, 2**63])
def test_position_risk_rejects_missing_or_malformed_update_time(scope, value):
    row = {'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':'0'}
    if value is not None:
        row['updateTime'] = value
    with pytest.raises(DemoError):
        position_risk(scope, PositionMode.ONE_WAY, [row])


def test_position_risk_preserves_nonzero_update_time(scope):
    row = {'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':'0','updateTime':1700000000000}
    assert position_risk(scope, PositionMode.ONE_WAY, [row])[0].update_time_ms == 1700000000000


@pytest.mark.parametrize('value', [None, True, '1700000000000', -1, 2**63])
def test_asset_balance_rejects_missing_or_malformed_update_time(scope, value):
    row = {'asset':'USDT','walletBalance':'100','marginBalance':'98','unrealizedProfit':'-2',
           'availableBalance':'80','initialMargin':'18','maintMargin':'1'}
    if value is not None:
        row['updateTime'] = value
    with pytest.raises(DemoError):
        asset_balances(scope, {'assets':[row]})


def test_asset_balance_preserves_nonzero_update_time(scope):
    row = {'asset':'USDT','walletBalance':'100','marginBalance':'98','unrealizedProfit':'-2',
           'availableBalance':'80','initialMargin':'18','maintMargin':'1','updateTime':1700000000000}
    assert asset_balances(scope, {'assets':[row]})[0].update_time_ms == 1700000000000
