"""Offline unit scenarios only. T14/T15/T16/T18 acceptance remains NOT_RUN."""
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from trading_system.contracts import types as t
from trading_system.risk.exposure import ExposureCalculator, ExposureMark, WorkingOrder
from trading_system.risk.valuation import FxRate, Valuation, floor_quantity
from unit.support_ip_06 import make_case, money, quantity, position, price, D


@pytest.fixture
def risk_case():
    return make_case()


def test_notional_and_planned_loss_distinct(risk_case):
    decision = risk_case.evaluate()
    assert decision.verdict == 'ALLOW', decision.rejection_codes
    assert decision.aggregate_exposure_after.gross.amount == D('200')
    assert decision.reserved_assets == (money('103'),)  # margin 100 + fees 2 + funding 1
    assert decision.estimated_loss.amount == D('23.4')  # stop 20 + slippage .4 + 3
    assert decision.fees_assumption == money('2')


def test_keep_lower_leverage(risk_case):
    decision = risk_case.evaluate(max_leverage=5, requested_leverage=2)
    assert decision.verdict == 'ALLOW'
    assert decision.permitted_leverage == 2
    excessive = risk_case.evaluate(max_leverage=5, requested_leverage=6)
    assert excessive.verdict == 'REPLAN_REQUIRED'
    assert 'REPLAN:LEVERAGE_LIMIT' in excessive.rejection_codes


@pytest.mark.parametrize('product', list(t.Product))
def test_cash_linear_and_real_security_units(product):
    case = make_case(product)
    decision = case.evaluate()
    assert decision.verdict == 'ALLOW', decision.rejection_codes
    assert decision.permitted_quantity.unit == case.intent.quantity_unit
    expected = '103' if product == t.Product.USD_M_PERP else '203'
    assert decision.reserved_assets == (money(expected, case.policy.valuation_currency),)


def test_contract_multiplier(risk_case):
    risk_case.inputs = replace(risk_case.inputs, multiplier=D('2'))
    decision = risk_case.evaluate()
    assert decision.verdict == 'ALLOW'
    assert decision.aggregate_exposure_after.gross.amount == D('400')
    assert decision.reserved_assets == (money('203'),)
    assert decision.estimated_loss == money('43.8')


def test_gross_not_net(risk_case):
    other = replace(risk_case.book.scope, venue_leg='SHORT')
    risk_case.book = replace(risk_case.book, positions=(position(other, '-2'),))
    risk_case.inputs = replace(risk_case.inputs, marks=risk_case.inputs.marks + (
        ExposureMark(scope=other, price=price(), multiplier=D(1), unit='CONTRACT', related_group='related'),))
    risk_case.policy = replace(risk_case.policy, maximum_gross_exposure='300')
    decision = risk_case.evaluate()
    assert decision.aggregate_exposure_after.net.amount == 0
    assert decision.aggregate_exposure_after.gross.amount == 400
    assert decision.aggregate_exposure_after.groups[0].gross.amount == 400
    assert decision.verdict == 'REPLAN_REQUIRED'


def test_related_group_limit(risk_case):
    risk_case.inputs = replace(risk_case.inputs, group_limits=(replace(
        risk_case.inputs.group_limits[0], maximum_gross=money('199')),))
    assert 'REPLAN:RELATED_GROUP' in risk_case.evaluate().rejection_codes


def test_unknown_fx_and_fee(risk_case):
    risk_case.inputs = replace(risk_case.inputs, fee_reserve=None)
    assert 'FEE_OR_FUNDING_EVIDENCE_MISSING' in risk_case.evaluate().rejection_codes
    risk_case.inputs = replace(risk_case.inputs, fee_reserve=(money('2', 'USD'),))
    decision = risk_case.evaluate()
    assert decision.verdict == 'DENY'
    assert any('FX_MISSING:USD' in c for c in decision.rejection_codes)


def test_funding_missing(risk_case):
    risk_case.inputs = replace(risk_case.inputs, funding_reserve=None)
    assert risk_case.evaluate().verdict == 'DENY'


def test_fx_haircut_does_not_discount_liabilities():
    valuation = Valuation('USD', (FxRate(source='USDT', target='USD', asset_rate=D('.98'),
        liability_rate=D('1.01'), haircut=D('.1'), evidence_id='explicit-policy'),))
    assert valuation.convert(money('100')).amount == D('88.2')
    assert valuation.convert(money('100'), liability=True).amount == D('101')
    assert valuation.convert(money('-100')).amount == D('-101')
    with pytest.raises(ValueError, match='FX_MISSING'):
        valuation.convert(money('100', 'EUR'))


def test_valuation_is_not_spendable(risk_case):
    risk_case.inputs = replace(risk_case.inputs, spendable=(money('100000', 'USD'),))
    assert 'INSUFFICIENT_SPENDABLE:USDT' in risk_case.evaluate().rejection_codes


@pytest.mark.parametrize('field', ['observed_margin_mode', 'observed_position_mode'])
def test_mode_mismatch_blocks_entry_but_keeps_compliant_exit(risk_case, field):
    risk_case.inputs = replace(risk_case.inputs, **{field: 'MISMATCH'})
    assert 'ACCOUNT_MODE_MISMATCH' in risk_case.evaluate().rejection_codes
    risk_case.exiting()
    assert risk_case.evaluate().verdict == 'ALLOW'


def test_emergency_has_separate_spread_and_age_policy(risk_case):
    risk_case.quote('99', '101', occurred='2026-10-22T11:59:55Z')
    assert risk_case.evaluate().verdict == 'DENY'
    risk_case.exiting()
    assert risk_case.evaluate().verdict == 'ALLOW'
    risk_case.quote('90', '110')
    assert 'EMERGENCY_PRICE_TOLERANCE' in risk_case.evaluate().rejection_codes


@pytest.mark.parametrize('mutation', ['owner', 'epoch', 'quantity', 'type', 'currency', 'session', 'missing_quote'])
def test_emergency_cannot_bypass_boundaries(risk_case, mutation):
    risk_case.exiting()
    if mutation in ('owner', 'epoch'):
        updates = {'owner_strategy_id': 'other'} if mutation == 'owner' else {'ownership_epoch': 2}
        risk_case.book = replace(risk_case.book, positions=(replace(risk_case.book.positions[0], **updates),))
    elif mutation == 'quantity':
        risk_case.inputs = replace(risk_case.inputs, emergency=replace(risk_case.inputs.emergency, maximum_quantity=quantity('1')))
    elif mutation == 'type':
        risk_case.inputs = replace(risk_case.inputs, emergency=replace(risk_case.inputs.emergency, allowed_order_types=('LIMIT',)))
    elif mutation == 'currency':
        risk_case.inputs = replace(risk_case.inputs, emergency=replace(risk_case.inputs.emergency, reference_price=price('100', 'USD')))
    elif mutation == 'session':
        risk_case.inputs = replace(risk_case.inputs, session=replace(risk_case.inputs.session, state='HALTED'))
    else:
        risk_case.market = replace(risk_case.market, quote=replace(risk_case.market.quote, bid=None))
    assert risk_case.evaluate().verdict == 'DENY'


def test_round_down_retains_original_and_never_rounds_up(risk_case):
    risk_case.intent = replace(risk_case.intent, target_quantity='2.09')
    assert risk_case.evaluate().verdict == 'REPLAN_REQUIRED'
    risk_case.inputs = replace(risk_case.inputs, allow_round_down=True)
    decision = risk_case.evaluate()
    assert decision.verdict == 'ALLOW'
    assert decision.permitted_quantity.value == D('2.0')
    assert risk_case.intent.target_quantity == '2.09'
    adjustment = floor_quantity(quantity('2.09'), quantity('.1'))
    assert adjustment.original.value == D('2.09')
    assert adjustment.permitted.value == D('2.0')
    risk_case.intent = replace(risk_case.intent, target_quantity='0.09')
    assert risk_case.evaluate().verdict != 'ALLOW'


@pytest.mark.parametrize('field,value,code', [
    ('maximum_order_notional', '199', 'ORDER_NOTIONAL'),
    ('maximum_planned_loss_per_intent', '23', 'INTENT_LOSS'),
    ('maximum_total_planned_loss', '23', 'TOTAL_LOSS'),
    ('maximum_net_exposure', '199', 'NET'),
    ('maximum_capital', '202', 'CAPITAL'),
    ('maximum_open_positions', 0, 'COUNT_LIMIT'),
    ('maximum_active_orders', 0, 'COUNT_LIMIT'),
])
def test_independent_limits(risk_case, field, value, code):
    risk_case.policy = replace(risk_case.policy, **{field: value})
    assert 'REPLAN:' + code in risk_case.evaluate().rejection_codes


@pytest.mark.parametrize('mutation', ['scope', 'basis', 'sequence', 'policy', 'authorization', 'coverage', 'clock', 'expiry', 'live', 'lock', 'stop', 'negative'])
def test_fail_closed_inputs(risk_case, mutation):
    if mutation == 'scope':
        risk_case.intent = replace(risk_case.intent, venue_account_id='other')
    elif mutation == 'basis':
        risk_case.intent = replace(risk_case.intent, basis_fingerprint='other')
    elif mutation == 'sequence':
        risk_case.book = replace(risk_case.book, sequence=2)
    elif mutation == 'policy':
        risk_case.policy = replace(risk_case.policy, revision=2)
    elif mutation == 'authorization':
        risk_case.intent = replace(risk_case.intent, authorization_id='other')
    elif mutation == 'coverage':
        risk_case.book = replace(risk_case.book, coverage=replace(risk_case.book.coverage, pagination_complete=False))
    elif mutation == 'clock':
        risk_case.inputs = replace(risk_case.inputs, clock=replace(risk_case.inputs.clock, clock_health='UNKNOWN'))
    elif mutation == 'expiry':
        risk_case.intent = replace(risk_case.intent, valid_until='2026-10-22T11:00:00Z')
    elif mutation == 'live':
        risk_case.intent = replace(risk_case.intent, environment='LIVE')
    elif mutation == 'lock':
        risk_case.inputs = replace(risk_case.inputs, new_risk_allowed=False)
    elif mutation == 'stop':
        risk_case.intent = replace(risk_case.intent, exit_plan=replace(risk_case.intent.exit_plan, stop_price=None))
    else:
        risk_case.inputs = replace(risk_case.inputs, fee_reserve=(money('-1'),))
    assert risk_case.evaluate().verdict == 'DENY'


def test_no_reversal_or_cash_short(risk_case):
    risk_case.exiting()
    risk_case.intent = replace(risk_case.intent, target_side='SHORT', target_quantity='1')
    assert 'REVERSAL_REQUIRES_FLAT' in risk_case.evaluate().rejection_codes
    case = make_case(t.Product.US_EQUITY)
    case.intent = replace(case.intent, target_side='SHORT')
    assert 'CASH_ONLY' in case.evaluate().rejection_codes


def test_cash_sell_requires_actual_owned_asset():
    case = make_case(t.Product.SPOT).exiting()
    assert case.evaluate().verdict == 'ALLOW'
    assert money('2', 'ASSET') in case.evaluate().reserved_assets
    case.inputs = replace(case.inputs, spendable=(money('100000'),))
    assert 'INSUFFICIENT_SPENDABLE:ASSET' in case.evaluate().rejection_codes


def test_deterministic_under_ambient_decimal_context(risk_case):
    expected = risk_case.evaluate()
    with localcontext() as context:
        context.prec = 4
        assert risk_case.evaluate() == expected


def test_working_orders_consume_gross_and_missing_side_is_not_zero(risk_case):
    scope = risk_case.book.scope
    identity = t.OrderIdentity(scope=scope, logical_client_order_id='working', native_client_order_id='n',
                              venue_client_order_id='v', venue_order_id='1')
    order = t.OrderProjection(order=t.OwnedOrderRef(identity=identity, owner_strategy_id='demo-ai-strategy', ownership_epoch=1),
        lifecycle_state=t.OrderLifecycle.ACCEPTED, pending_operation='NONE', command_certainty='CONFIRMED',
        outcome_provenance='VENUE_EVENT', filled_quantity=quantity(), leaves_quantity=quantity('2'),
        lifecycle_revision=1, sequence=1)
    book = replace(risk_case.book, positions=(position(scope, '2'),), active_orders=(order,))
    delta = t.DeltaPlan(scope=scope, target=quantity('0'), actual=quantity('2'), working=quantity('-2'),
                        remaining=quantity('0'), classification='REDUCE', cancel_first=())
    calc = ExposureCalculator(Valuation('USDT'), risk_case.inputs.marks,
                              (WorkingOrder(logical_client_order_id='working', side='SELL'),))
    report = calc.project(book, delta)
    assert report.gross == money('400')
    assert report.net == money('0')
    assert not report.missing
    missing = ExposureCalculator(Valuation('USDT'), risk_case.inputs.marks).project(book, delta)
    assert 'WORKING_ORDER_EVIDENCE_MISSING' in missing.missing
    risk_case.book = book
    assert 'ACTIVE_ORDERS_REQUIRE_RECONCILIATION' in risk_case.evaluate().rejection_codes


def test_unreflectable_reservation_stays_unavailable(risk_case):
    from unit.support_ip_06 import BEFORE, END
    reservation = t.CapitalReservation(reservation_id='hold', environment=t.Environment.PAPER,
        collateral_group_id=risk_case.intent.collateral_group_id,
        venue_account_id=risk_case.intent.venue_account_id, owner_strategy_id='other', intent_id='prior',
        asset='USDT', amount=D('99900'), reflected_amount=D('0'), state=t.ReservationState.RELEASE_PENDING,
        created_at=BEFORE, expires_at=END, command_refs=('unknown-command',), evidence_watermark='1')
    projection = t.ReservationProjection(reservation=reservation, held=D('99900'), reflected=D('0'),
                                        release_pending=D('99900'), sequence=1)
    risk_case.book = replace(risk_case.book, reservations=(projection,))
    assert 'INSUFFICIENT_SPENDABLE:USDT' in risk_case.evaluate().rejection_codes


@pytest.mark.parametrize('mutation', ['marks', 'groups', 'evidence', 'existing_loss', 'mode', 'leg'])
def test_required_context_not_inferred(risk_case, mutation):
    if mutation == 'marks':
        risk_case.inputs = replace(risk_case.inputs, marks=())
    elif mutation == 'groups':
        risk_case.inputs = replace(risk_case.inputs, group_limits=())
    elif mutation == 'evidence':
        risk_case.inputs = replace(risk_case.inputs, evidence=())
    elif mutation == 'existing_loss':
        risk_case.inputs = replace(risk_case.inputs, existing_planned_loss=money('-1000'))
    elif mutation == 'mode':
        risk_case.inputs = replace(risk_case.inputs, authorized_margin_mode='UNKNOWN', observed_margin_mode='UNKNOWN')
    else:
        risk_case.inputs = replace(risk_case.inputs, authorized_position_mode='HEDGE', observed_position_mode='HEDGE')
    assert risk_case.evaluate().verdict == 'DENY'


@pytest.mark.parametrize('limit', ['0', '100.001', '110'])
def test_limit_price_is_bounded_and_on_tick(risk_case, limit):
    risk_case.intent = replace(risk_case.intent, execution=replace(risk_case.intent.execution,
        order_preference='LIMIT', limit_price=limit))
    assert risk_case.evaluate().verdict == 'DENY'


def test_explicit_fx_evidence_still_requires_fee_currency_balance(risk_case):
    from unit.support_ip_06 import evidence
    rate = FxRate(source='USD', target='USDT', asset_rate=D('.99'), liability_rate=D('1.02'),
                  haircut=D('.1'), evidence_id=evidence().id)
    risk_case.inputs = replace(risk_case.inputs, fx_rates=(rate,), fee_reserve=(money('2', 'USD'),))
    assert 'INSUFFICIENT_SPENDABLE:USD' in risk_case.evaluate().rejection_codes
    risk_case.book = replace(risk_case.book, balances=risk_case.book.balances + (money('10', 'USD'),))
    risk_case.inputs = replace(risk_case.inputs, spendable=risk_case.inputs.spendable + (money('10', 'USD'),))
    decision = risk_case.evaluate()
    assert decision.verdict == 'ALLOW'
    assert decision.fees_assumption == money('2.04')


def test_same_direction_related_positions_still_consume_group(risk_case):
    other = replace(risk_case.book.scope, instrument_uid='related-instrument')
    risk_case.book = replace(risk_case.book, positions=(position(other, '2'),))
    risk_case.inputs = replace(risk_case.inputs, marks=risk_case.inputs.marks + (
        ExposureMark(scope=other, price=price(), multiplier=D(1), unit='CONTRACT', related_group='related'),),
        group_limits=(replace(risk_case.inputs.group_limits[0], maximum_gross=money('300')),))
    decision = risk_case.evaluate()
    assert decision.aggregate_exposure_after.groups[0].gross.amount == D('400')
    assert 'REPLAN:RELATED_GROUP' in decision.rejection_codes


@pytest.mark.parametrize('value', [float('nan'), Decimal('NaN'), Decimal('Infinity'), Decimal('-1')])
def test_invalid_fx_numeric_inputs(value):
    with pytest.raises(ValueError):
        FxRate(source='USD', target='USDT', asset_rate=value, liability_rate=D('1'),
               haircut=D('0'), evidence_id='evidence')
