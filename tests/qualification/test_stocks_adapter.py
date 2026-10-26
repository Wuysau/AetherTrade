"""Offline behavior only. Batch execution deferred; T61/Q08 remain NOT_RUN."""
from dataclasses import replace

import pytest

from support_ip_25 import stocks_case, compatibility, query
from trading_system.contracts import types as t
from trading_system.contracts.wire import ABSENT, ExecutionCommandCanonicalPayload as Payload
from trading_system.engine.nautilus.bridge import BridgeDenied
from trading_system.engine.nautilus.stocks_adapter import StocksCapabilityProbe, StocksEngineAdapter
from trading_system.engine.nautilus.stocks_models import StocksOrderPreview


def test_real_equity_explicit_tokenize_false(stocks_case):
    case = stocks_case
    preview = case.engine.preview(case.command, case.instrument)
    assert preview.tokenize is False
    receipt = case.engine.submit(case.command, case.permit)
    assert receipt.status == t.CommandState.UNKNOWN
    assert case.engine.offline_writes[0].command.canonical_payload.tokenize is False
    assert not case.engine.live_enabled


@pytest.mark.parametrize('tokenize', [ABSENT, True, None, 0, 'false'])
def test_tokenize_must_be_explicit_boolean_false(stocks_case, tokenize):
    case = stocks_case
    command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, tokenize=tokenize))
    with pytest.raises(BridgeDenied, match='TOKENIZE'):
        case.engine.submit(command, case.permit)
    assert not case.engine.offline_writes


@pytest.mark.parametrize('product', [t.Product.SPOT, t.Product.USD_M_PERP])
def test_no_product_substitution(stocks_case, product):
    case = stocks_case
    with pytest.raises(BridgeDenied):
        case.engine.submit(replace(case.command, product=product), case.permit)
    with pytest.raises(BridgeDenied):
        case.engine.preview(case.command, replace(case.instrument, product=product))
    assert not case.engine.offline_writes


def test_ack_not_fill(stocks_case):
    projection = stocks_case.accept_request_ack_only()
    assert projection.actual_shares == '0'
    assert projection.command_receipt_only is True
    assert projection.lifecycle == 'UNKNOWN'
    assert projection.event.source == 'STOCKS_PLACE_ACK'


@pytest.mark.parametrize('status', ['S', 'F'])
@pytest.mark.parametrize('shares', ['0', '12.345'])
def test_ack_preserves_prior_shares_and_is_idempotent(stocks_case, status, shares):
    case = stocks_case
    raw = case.raw_ack(status)
    first = case.normalizer.observe_ack(raw, actual_shares=shares)
    assert first == case.normalizer.observe_ack(raw, actual_shares=shares)
    assert first.actual_shares == shares and first.lifecycle == 'UNKNOWN'
    assert case.normalizer.normalize(raw) == first.event
    assert case.codec.decode(case.command.venue_client_order_id).venue_order_id is None


def test_cancel_ack_is_not_cancelled_or_flat(stocks_case):
    case = stocks_case
    case.codec.bind_venue_order(case.command.venue_client_order_id, 'observed-order')
    raw = case.raw_ack(source='STOCKS_CANCEL_ACK', data={'orderId': 'observed-order', 'status': 'S'})
    result = case.normalizer.observe_ack(raw, actual_shares='2')
    assert result.actual_shares == '2' and result.lifecycle == 'UNKNOWN'
    assert result.command_receipt_only


@pytest.mark.parametrize('mutation', ['hash', 'scope', 'source', 'environment', 'future', 'unknown', 'lifecycle', 'extra'])
def test_untrusted_ack_rejected(stocks_case, mutation):
    case = stocks_case
    raw = case.raw_ack()
    if mutation == 'hash':
        case.archive[raw.raw_payload_ref.id] = b'{}'
    elif mutation == 'scope':
        raw = replace(raw, scope=replace(raw.scope, venue_account_id='other'))
    elif mutation == 'source':
        raw = replace(raw, source='STOCKS_FILL')
    elif mutation == 'environment':
        raw = replace(raw, raw_payload_ref=replace(raw.raw_payload_ref, environment=t.Environment.LIVE))
    elif mutation == 'future':
        raw = replace(raw, raw_payload_ref=replace(raw.raw_payload_ref, visible_at='2099-01-01T00:00:00Z'))
    elif mutation == 'unknown':
        raw = case.raw_ack(data={'status': 'S', 'orderId': 'x', 'clientOrderId': 'unregistered'})
    elif mutation == 'lifecycle':
        raw = case.raw_ack('FILLED')
    else:
        raw = case.raw_ack(data={'status': 'S', 'orderId': 'x',
            'clientOrderId': case.command.venue_client_order_id, 'filledQuantity': '1'})
    with pytest.raises((ValueError, LookupError)):
        case.normalizer.normalize(raw)


def test_conflicting_order_identity_rejected(stocks_case):
    case = stocks_case
    case.codec.bind_venue_order(case.command.venue_client_order_id, 'different-order')
    with pytest.raises(ValueError, match='ORDER_ID_MISMATCH'):
        case.normalizer.normalize(case.raw_ack())


def test_market_limit_capability(stocks_case):
    profile = StocksCapabilityProbe().verify(stocks_case.probe_input())
    assert profile.status == t.CapabilityStatus.UNVERIFIED
    assert profile.capabilities == ()
    assert 'NATIVE_STOP_UNVERIFIED' in profile.limitations
    assert profile.valid_until == profile.valid_from


@pytest.mark.parametrize('missing', ['BALANCES', 'SHARES', 'ORDER_HISTORY', 'TRADE_HISTORY'])
def test_account_reads_missing_block(stocks_case, missing):
    inp = stocks_case.probe_input()
    inp = replace(inp, evidence=replace(inp.evidence,
        coverage=replace(inp.evidence.coverage, missing=(missing,))))
    profile = StocksCapabilityProbe().verify(inp)
    assert profile.capabilities == () and profile.status == t.CapabilityStatus.UNVERIFIED
    assert 'ACCOUNT_READS_MISSING_OR_INCOMPLETE' in profile.limitations


@pytest.mark.parametrize('flag', ['supported', 'query_succeeded', 'pagination_complete'])
def test_incomplete_coverage_cannot_admit(stocks_case, flag):
    inp = stocks_case.probe_input()
    inp = replace(inp, evidence=replace(inp.evidence,
        coverage=replace(inp.evidence.coverage, **{flag: False})))
    assert 'ACCOUNT_READS_MISSING_OR_INCOMPLETE' in StocksCapabilityProbe().verify(inp).limitations


def test_empty_query_is_not_zero_position(stocks_case):
    report = stocks_case.engine.query(query(stocks_case.scope))
    assert not report.coverage.query_succeeded and not report.coverage.supported
    assert not report.coverage.pagination_complete
    assert 'SHARES' in report.coverage.missing
    assert report.positions == () and report.evidence == ()
    with pytest.raises(BridgeDenied):
        stocks_case.engine.query(query(replace(stocks_case.scope, venue_account_id='other')))


@pytest.mark.parametrize('kwargs', [
    dict(side='BUY', order_type='MARKET', notional='10'),
    dict(side='SELL', order_type='MARKET', quantity='0.5'),
    dict(side='BUY', order_type='LIMIT', quantity='1', price='10.00', trading_session='RTH'),
    dict(side='SELL', order_type='LIMIT', quantity='0.5', price='10.00', trading_session='24H', time_in_force='GTC'),
])
def test_documented_request_combinations(kwargs):
    fields = dict(symbol='AAPL', quantity=None, price=None, notional=None, trading_session=None, time_in_force='DAY')
    fields.update(kwargs)
    assert StocksOrderPreview(**fields).tokenize is False


@pytest.mark.parametrize('changes', [
    dict(quantity='1'), dict(notional=None), dict(notional='0'), dict(notional='NaN'),
    dict(notional=1.5), dict(price='1'), dict(trading_session='RTH'), dict(time_in_force='GTC'),
    dict(order_type='STOP_MARKET'), dict(tokenize=True), dict(side='SHORT'),
])
def test_invalid_market_preview(changes):
    fields = dict(symbol='AAPL', side='BUY', order_type='MARKET', quantity=None,
        notional='10', price=None, time_in_force='DAY', trading_session=None)
    fields.update(changes)
    with pytest.raises(ValueError):
        StocksOrderPreview(**fields)


@pytest.mark.parametrize('changes', [dict(trading_session=None), dict(notional='1'),
    dict(price='1.001'), dict(quantity='0.5', time_in_force='GTC'), dict(time_in_force='IOC')])
def test_invalid_limit_preview(changes):
    fields = dict(symbol='AAPL', side='SELL', order_type='LIMIT', quantity='1',
        notional=None, price='10', time_in_force='DAY', trading_session='RTH')
    fields.update(changes)
    with pytest.raises(ValueError):
        StocksOrderPreview(**fields)


def test_limit_has_no_silent_session_default(stocks_case):
    case = stocks_case
    case.set_payload(Payload(side='BUY', order_type='LIMIT', quantity='1', price='10', tokenize=False))
    with pytest.raises(ValueError, match='SESSION'):
        case.engine.preview(case.command, case.instrument)
    assert case.engine.preview(case.command, case.instrument, trading_session='RTH').order_type == 'LIMIT'
    with pytest.raises(BridgeDenied, match='CANONICAL_CONTRACT'):
        case.engine.submit(case.command, case.permit)
    assert not case.engine.offline_writes


@pytest.mark.parametrize('method,operation', [('amend', 'AMEND'), ('submit', 'SET_PROTECTION')])
def test_unsupported_writes_fail_closed(stocks_case, method, operation):
    case = stocks_case
    with pytest.raises(BridgeDenied, match='NOT_QUALIFIED'):
        getattr(case.engine, method)(replace(case.command, operation=operation), case.permit)
    assert not case.engine.offline_writes


def test_native_map_cannot_unlock_writes(stocks_case):
    engine = StocksEngineAdapter(compatibility())
    with pytest.raises(BridgeDenied, match='NATIVE_BINDING_NOT_QUALIFIED'):
        engine.submit(stocks_case.command, stocks_case.permit)
    with pytest.raises(BridgeDenied):
        engine.enable_native_path('STOCKS_WRITE')
    assert not engine.live_enabled


def test_cancel_uses_separate_durable_command(stocks_case):
    case = stocks_case
    command, permit = case.make_command('CANCEL')
    result = case.engine.cancel(command, permit)
    assert result.command_id == command.command_id and result.status == t.CommandState.UNKNOWN
    assert case.engine.offline_writes[0].command.target_order_id == 'original-venue-order'
    with pytest.raises(BridgeDenied, match='WRONG_ENGINE_METHOD'):
        case.engine.submit(command, permit)


def test_close_observes_only_explicit_sell_quantity(stocks_case):
    case = stocks_case
    case.command, case.permit = case.make_command('CLOSE')
    case.set_payload(Payload(side='SELL', order_type='MARKET', quantity='1', tokenize=False))
    assert case.engine.submit(case.command, case.permit).status == t.CommandState.UNKNOWN
    assert len(case.engine.offline_writes) == 1


@pytest.mark.parametrize('payload', [
    Payload(side='BUY', order_type='MARKET', quantity='1', tokenize=False),
    Payload(side='SELL', order_type='MARKET', quote_notional='1', tokenize=False),
    Payload(side='BUY', order_type='MARKET', quote_notional='1', tokenize=False, reduce_only=False),
    Payload(side='BUY', order_type='STOP_MARKET', quantity='1', tokenize=False),
])
def test_invalid_write_never_reaches_bridge(stocks_case, payload):
    case = stocks_case
    command = replace(case.command, canonical_payload=payload)
    with pytest.raises(ValueError):
        case.engine.submit(command, case.permit)
    assert not case.engine.offline_writes


def test_probe_scope_and_missing_evidence(stocks_case):
    inp = stocks_case.probe_input()
    inp = replace(inp, instrument=replace(inp.instrument, product=t.Product.USD_M_PERP),
                  evidence=replace(inp.evidence, evidence=()))
    result = StocksCapabilityProbe().verify(inp)
    assert result.capabilities == ()
    assert 'REAL_EQUITY_SCOPE_MISMATCH' in result.limitations
    assert 'ACCOUNT_READS_MISSING_OR_INCOMPLETE' in result.limitations


@pytest.mark.parametrize('failure', ['missing_permit', 'revoked', 'expired', 'tampered', 'replay'])
def test_inherits_owner_durable_boundary(stocks_case, failure):
    case = stocks_case
    permit = case.permit
    if failure == 'missing_permit':
        permit = None
    elif failure == 'revoked':
        case.revoked = True
    elif failure == 'expired':
        case.reading = replace(case.reading, monotonic_ns=1000)
    elif failure == 'tampered':
        case.command = replace(case.command, payload_hash='0' * 64)
    else:
        case.engine.submit(case.command, permit)
    before = len(case.engine.offline_writes)
    with pytest.raises(BridgeDenied):
        case.engine.submit(case.command, permit)
    assert len(case.engine.offline_writes) == before
