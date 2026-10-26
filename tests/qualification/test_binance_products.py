"""Offline behavioral tests; execution is deferred to the authorized batch runner."""
from dataclasses import replace
from decimal import Decimal as D, localcontext
import json
from pathlib import Path

import pytest

from trading_system.contracts import types as t
from trading_system.engine.nautilus import binance_products as b
from support_ip_24 import END, NOW, ProductCase, product_case


def test_spot_cash_not_short(product_case):
    c = product_case
    c.state = replace(c.state, actual_quantity=D(0), owned_quantity=D(0),
        balances=(b.BinanceBalance(asset='USDT', total=D(1000), available=D(1000)),))
    result = c.check(side='SELL', reduce=True)
    assert not result.allow_reduction
    assert 'INSUFFICIENT_SELLABLE_BASE' in result.rejection_codes
    snapshot = c.reader().snapshot(c.query)
    assert snapshot.positions == ()
    assert snapshot.balances[0].currency == 'BTC'


def test_margin_mode_no_implicit_switch(product_case):
    result = product_case.authorized_isolated_but_actual_cross()
    assert result.allow_new_risk is False
    assert result.position_mode_writes == []
    c = ProductCase(t.Product.USD_M_PERP)
    c.state = replace(c.state, margin_mode='CROSS')
    assert c.check(side='SELL', reduce=True).allow_reduction
    assert not c.check(side='BUY', reduce=True).allow_reduction
    c.state = replace(c.state, pending_exit_quantity=D('0.995'))
    assert not c.check(side='SELL', reduce=True).allow_reduction


def protection(c, profile=None, **changes):
    args = dict(build_digest='b' * 64, quantity=c.request.quantity,
        trigger_price=c.request.price, trigger='LAST',
        order_type='STOP_LOSS' if c.scope.product == t.Product.SPOT else 'STOP_MARKET',
        combined_oco=False, now=NOW)
    args.update(changes)
    return b.native_protection_missing(profile or c.capability(), c.rules, **args)


def test_native_protection_capability(product_case):
    missing = protection(product_case, trigger='MARK', combined_oco=True)
    assert 'CAPABILITY_UNVERIFIED' in missing
    assert 'TRIGGER_UNSUPPORTED' in missing
    assert 'MISSING:NATIVE_OCO_SHARED_ASSET' in missing
    assert 'MISSING:NATIVE_ORDER_ID_QUERY' in missing
    c = ProductCase(t.Product.USD_M_PERP)
    assert 'MISSING:NATIVE_ALGO_ID_CONTINUITY' in protection(c)
    assert 'MISSING:NATIVE_REDUCE_LEG' in protection(c)
    assert 'OCO_PRODUCT_UNSUPPORTED' in protection(c, combined_oco=True)
    assert 'NATIVE_SIZE_OR_PRICE_RULE' in protection(c,
        quantity=t.Quantity(value=D('0.0001'), unit='BTC'))


@pytest.mark.parametrize('capability', ['NATIVE_CONDITIONAL', 'NATIVE_ORDER_ID_QUERY',
    'NATIVE_MINIMUM_RULES', 'ORDER_TYPE:STOP_LOSS', 'TRIGGER:LAST', 'NATIVE_OCO_SHARED_ASSET'])
def test_each_protection_capability_is_independent(product_case, capability):
    caps = ('NATIVE_CONDITIONAL', 'NATIVE_ORDER_ID_QUERY', 'NATIVE_MINIMUM_RULES',
        'ORDER_TYPE:STOP_LOSS', 'TRIGGER:LAST', 'NATIVE_OCO_SHARED_ASSET')
    profile = replace(product_case.capability(), status=t.CapabilityStatus.VERIFIED,
        capabilities=tuple(x for x in caps if x != capability))
    assert 'MISSING:' + capability in protection(product_case, profile, combined_oco=True)


def test_server_limit_and_rule_changes(product_case):
    c = product_case
    assert c.check().allow_new_risk
    assert not c.check().execution_authorized
    c.rules = replace(c.rules, revision=2, instrument=replace(c.instrument, rule_revision=2))
    assert 'RULE_REVISION_CHANGED' in c.check().rejection_codes
    c.rules = replace(c.rules, rate_budgets=(b.RateBudget(key='shared-ip', limit=10,
        used=10, request_cost=1),))
    assert 'SERVER_RATE_LIMIT' in c.check(rule_revision=2).rejection_codes
    other = ProductCase(t.Product.USD_M_PERP)
    c.rules = other.rules
    assert 'SCOPE_MISMATCH' in c.check().rejection_codes
    result = other.check(requested_leverage=D(1))
    assert 'LEVERAGE_REPLAN_REQUIRED' in result.rejection_codes
    assert result.permitted_leverage is None
    assert other.request.requested_leverage == D(2)


@pytest.mark.parametrize('field,value,code', [
    ('held_base', D('0.999'), 'INSUFFICIENT_SELLABLE_BASE'),
    ('owned_quantity', D('0.001'), 'INSUFFICIENT_SELLABLE_BASE'),
    ('pending_exit_quantity', D('0.999'), 'INSUFFICIENT_SELLABLE_BASE'),
    ('actual_quantity', D('-1'), 'INVALID_CASH_INVENTORY'),
    ('actual_leverage', D(2), 'CASH_ONLY')])
def test_cash_reservations_and_ownership(product_case, field, value, code):
    c = product_case
    c.state = replace(c.state, **{field: value})
    assert code in c.check(side='SELL', reduce=True).rejection_codes


def test_cash_quote_fee_and_base_fee(product_case):
    c = product_case
    c.state = replace(c.state, held_quote=D('9500'))
    assert 'INSUFFICIENT_QUOTE' in c.check().rejection_codes
    c.rules = replace(c.rules, fee_asset='BTC')
    c.state = replace(c.state, owned_quantity=D('0.01'))
    assert 'INSUFFICIENT_SELLABLE_BASE' in c.check(side='SELL', reduce=True).rejection_codes


@pytest.mark.parametrize('change,code', [
    ({'quantity': t.Quantity(value=D('0.0101'), unit='BTC')}, 'QUANTITY_RULE'),
    ({'price': t.Price(value=D('50000.01'), currency='USDT')}, 'PRICE_RULE'),
    ({'price': t.Price(value=D('1'), currency='USDT')}, 'NOTIONAL_RULE'),
    ({'quantity': t.Quantity(value=D('0.01'), unit='ETH')}, 'ORDER_UNIT_MISMATCH')])
def test_filters_reject_without_rounding(product_case, change, code):
    assert code in product_case.check(**change).rejection_codes


def test_market_rules_and_decimal_context(product_case):
    c = product_case
    with localcontext() as ctx:
        ctx.prec = 2
        assert c.check().allow_new_risk
    c.rules = replace(c.rules, market_quantity_step=None, market_minimum_quantity=None,
        market_maximum_quantity=None)
    assert 'MARKET_RULES_MISSING' in c.check(order_type='MARKET').rejection_codes


@pytest.mark.parametrize('field,value,code', [
    ('valid_until', NOW, 'ACCOUNT_EVIDENCE_STALE_OR_INVALID'),
    ('observed_at', END, 'ACCOUNT_EVIDENCE_STALE_OR_INVALID'),
    ('evidence', (), 'ACCOUNT_EVIDENCE_STALE_OR_INVALID')])
def test_account_freshness(product_case, field, value, code):
    product_case.state = replace(product_case.state, **{field: value})
    assert code in product_case.check().rejection_codes


@pytest.mark.parametrize('environment', [t.Environment.TESTNET, t.Environment.LIVE])
def test_native_environments_never_enabled(product_case, environment):
    c = product_case
    scope = replace(c.scope, environment=environment)
    assert 'ENVIRONMENT_NOT_ENABLED' in c.check(scope=scope).rejection_codes
    with pytest.raises(PermissionError, match='NOT_QUALIFIED'):
        c.reader().snapshot(replace(c.query, scope=scope))
    assert not c.reads


def test_profile_cannot_promote_documentation_or_synthetic_build(product_case):
    c = product_case
    evidence = t.VenueCapabilityEvidence(scope=c.scope, build_digest='b' * 64,
        query_scope='offline', source='documentation', session_id='session',
        coverage=c.coverage, evidence=(c.ref,))
    profile = b.BinanceProductProfile(c.scope).verify(evidence, c.build())
    assert profile.status == t.CapabilityStatus.UNVERIFIED
    assert profile.capabilities == ()
    assert profile.valid_from == profile.valid_until
    assert 'EXACT_BUILD_EVIDENCE_MISSING' in profile.limitations
    wrong = replace(evidence, build_digest='unknown', coverage=replace(c.coverage, missing=('FEES',)))
    profile = b.BinanceProductProfile(c.scope).verify(wrong, c.build())
    assert 'BUILD_MISMATCH_OR_UNRESOLVED' in profile.limitations
    assert 'INCOMPLETE_COVERAGE' in profile.limitations


def test_owner_checked_before_and_after_read(product_case):
    c = product_case
    reader = c.reader()
    c.lease = None
    with pytest.raises(PermissionError):
        reader.snapshot(c.query)
    assert c.reads == []
    c = ProductCase()
    def lose_owner(query):
        c.lease = None
        return c.response
    with pytest.raises(PermissionError):
        c.reader(lose_owner).snapshot(c.query)


@pytest.mark.parametrize('change', [
    {'clock_health': 'UNKNOWN'}, {'monotonic_ns': 1000}, {'utc_now': END}])
def test_owner_clock_and_expiry(product_case, change):
    product_case.clock = replace(product_case.clock, **change)
    with pytest.raises(PermissionError):
        product_case.reader().snapshot(product_case.query)
    assert product_case.reads == []


@pytest.mark.parametrize('as_of', ['invalid', END, '2026-10-22T00:00:00Z'])
def test_invalid_query_is_unavailable(product_case, as_of):
    c = product_case
    result = c.reader().snapshot(replace(c.query, as_of=as_of))
    assert result.coverage.query_succeeded is False
    assert not c.reads


def test_read_timeout_is_not_flat(product_case):
    def timeout(query):
        raise TimeoutError('offline injected timeout')
    result = product_case.reader(timeout).snapshot(product_case.query)
    assert result.positions == ()
    assert not result.coverage.query_succeeded
    assert 'ACCOUNT_READ_FAILED' in result.coverage.missing


@pytest.mark.parametrize('section', b.COMMON_READ_SECTIONS + ('CASH_ALLOCATIONS',))
def test_missing_sections_are_not_flat(product_case, section):
    c = product_case
    c.response = replace(c.response, sections=tuple(x for x in c.response.sections if x != section))
    assert section in c.reader().snapshot(c.query).coverage.missing


def test_empty_balance_and_partial_pages_are_not_zero(product_case):
    c = product_case
    c.response = replace(c.response, balances=(), coverage=replace(c.coverage, pagination_complete=False))
    snapshot = c.reader().snapshot(c.query)
    assert not snapshot.coverage.pagination_complete
    assert 'BALANCE:BTC' in snapshot.coverage.missing
    assert 'BALANCE:USDT' in snapshot.coverage.missing


@pytest.mark.parametrize('change,code', [
    ({'session_id': 'wrong'}, 'ACCOUNT_RESPONSE_SCOPE_MISMATCH'),
    ({'as_of': END}, 'ACCOUNT_RESPONSE_EVIDENCE_INVALID'),
    ({'evidence': ()}, 'ACCOUNT_RESPONSE_EVIDENCE_INVALID'),
    ({'sequence': -1}, 'ACCOUNT_ROWS_INVALID')])
def test_invalid_response(product_case, change, code):
    c = product_case
    c.response = replace(c.response, **change)
    assert code in c.reader().snapshot(c.query).coverage.missing


def test_sequence_conflict_regression_and_session_switch(product_case):
    c = product_case
    reader = c.reader()
    first = reader.snapshot(c.query)
    assert reader.snapshot(c.query).basis_fingerprint == first.basis_fingerprint
    c.response = replace(c.response, sequence=0, session_id='new')
    assert 'ACCOUNT_SEQUENCE_REGRESSION_OR_CONFLICT' in reader.snapshot(
        replace(c.query, session_id='new')).coverage.missing
    c.response = replace(c.response, sequence=1, session_id='session', balances=(
        b.BinanceBalance(asset='BTC', total=D('2'), available=D('2')), c.state.balances[1]))
    assert 'ACCOUNT_SEQUENCE_REGRESSION_OR_CONFLICT' in reader.snapshot(c.query).coverage.missing
    c.response = replace(c.response, sequence=2)
    assert reader.snapshot(c.query).sequence == 2


def test_usdm_actual_mode_required_for_empty_position_list():
    c = ProductCase(t.Product.USD_M_PERP)
    assert not c.reader().snapshot(c.query).coverage.missing
    c.response = replace(c.response, position_mode=None)
    assert 'ACTUAL_MODE_OR_POSITION_INVALID' in c.reader().snapshot(c.query).coverage.missing


def test_usdm_short_exit_and_hedge_leg():
    c = ProductCase(t.Product.USD_M_PERP)
    c.state = replace(c.state, actual_quantity=D('-1'))
    assert c.check(side='BUY', reduce=True).allow_reduction
    assert not c.check(side='SELL', reduce=True).allow_reduction
    c.state = replace(c.state, position_mode='HEDGE')
    assert 'ACTUAL_LEG_MISMATCH' in c.check(side='BUY', reduce=True).rejection_codes


def test_stale_missing_and_changed_fee_rules(product_case):
    c = product_case
    c.rules = replace(c.rules, missing=('PERCENT_PRICE',))
    assert 'RULES_STALE_OR_INCOMPLETE' in c.check().rejection_codes
    c.rules = replace(c.rules, missing=(), valid_until=NOW)
    assert 'RULES_STALE_OR_INCOMPLETE' in c.check().rejection_codes
    c.rules = replace(c.rules, valid_until=END, fee_fraction=D('0.1'))
    c.state = replace(c.state, held_quote=D('9490'))
    assert 'INSUFFICIENT_QUOTE' in c.check().rejection_codes


def test_process_identity_guard(product_case, monkeypatch):
    reader = product_case.reader()
    monkeypatch.setattr(b.os, 'getpid', lambda: -1)
    with pytest.raises(PermissionError):
        reader.snapshot(product_case.query)
    assert not product_case.reads


@pytest.mark.parametrize('value', [D('NaN'), D('Infinity'), D('-1'), D('1e100')])
def test_invalid_balance_rejected(value):
    with pytest.raises(ValueError):
        b.BinanceBalance(asset='BTC', total=value, available=D(0))


@pytest.mark.parametrize('name,product', [('spot', 'SPOT'), ('usdm', 'USD_M_PERP')])
def test_checked_in_profiles_are_non_authorizing(name, product):
    path = Path(__file__).resolve().parents[2] / 'qualification' / 'binance' / (name + '-profile.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['product'] == product
    assert data['status'] == 'UNVERIFIED'
    assert data['live_enabled'] is False
    assert data['capabilities'] == []
    assert all(p['status'] == 'NOT_RUN' for p in data['required_probes'])
