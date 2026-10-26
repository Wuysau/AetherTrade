"""IP-26 application probes. Authored for batch execution, not T/Q acceptance."""
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_system.contracts import types as t
from trading_system.equities.cash_book import CashBook
from trading_system.equities.corporate_actions import EquityBook
from trading_system.equities.sessions import MarketSessionService
from trading_system.protection.hosted_cash import HostedCashProtection
from trading_system.protection.native import digest
from integration.support_ip_26 import END, STAMP, START, equity_case, money, price, qty


def test_unsettled_cash_and_unsellable_shares(equity_case):
    c = equity_case
    assert c.require(cash=money('6000'), shares=qty('40')).unsettled == money('4000')
    with pytest.raises(ValueError, match='SETTLED_CASH_INSUFFICIENT'):
        c.require(cash=money('6000.01'))
    with pytest.raises(ValueError, match='SELLABLE_SHARES_INSUFFICIENT'):
        c.require(shares=qty('41'))


@pytest.mark.parametrize('operation', ['MINT', 'FX', 'TRANSFER'])
def test_implicit_funding_always_rejected(equity_case, operation):
    with pytest.raises(ValueError, match='AUTOMATIC_FUNDING_UNSUPPORTED'):
        equity_case.require(automatic_operations=(operation,))


@pytest.mark.parametrize('changes', [
    {'settled_available': money('-1')}, {'unsettled': money('-1')},
    {'settled_available': money('10001')}, {'sellable': qty('101')},
    {'sellable': qty('-1')}, {'rule_revision': 2}, {'session_id': 'extended'},
    {'ownership_epoch': 2}, {'position_id': 'other'}, {'report_hash': '0' * 64},
    {'session_hash': '0' * 64}, {'instrument_hash': '0' * 64},
    {'reservations_reconciled': False}, {'valid_until': STAMP},
    {'settled_available': t.Money(amount=Decimal('1'), currency='EUR')},
])
def test_availability_proof_failures(equity_case, changes):
    c = equity_case
    c.availability = replace(c.availability, **changes)
    with pytest.raises(ValueError):
        c.require(cash=money('1'))


@pytest.mark.parametrize('changes', [
    {'next_cursor': 'more'}, {'as_of': START}, {'positions': ()}, {'balances': ()},
    {'evidence': ()},
])
def test_report_missing_never_establishes_availability(equity_case, changes):
    c = equity_case
    c.report = replace(c.report, **changes)
    c.availability = replace(c.availability, report_hash=digest(c.report))
    with pytest.raises(ValueError):
        c.require()


def test_unbound_cash_reader_denies(equity_case):
    equity_case.cash = CashBook()
    with pytest.raises(ValueError, match='PROOF_REQUIRED'):
        equity_case.require()


@pytest.mark.parametrize('state', ['HALTED', 'CLOSED'])
def test_halt_and_outside_session(equity_case, state):
    c = equity_case
    c.session_row = replace(c.session_row, state=state, next_open_at=END)
    c.session = c.sessions.state(c.instrument, c.now)
    c.market = replace(c.market, quote=replace(c.market.quote, last=price('90')))
    c.rebind()
    result = c.tick()
    observation = c.observations[-1]
    assert observation.exit_receipt.state == 'EXIT_PENDING_MARKET'
    assert observation.flat_verified is False
    assert observation.session.next_open_at == END
    assert result.commands == ()
    assert result.locks and observation.alert
    with pytest.raises(ValueError, match='SESSION_UNAVAILABLE'):
        c.require()


@pytest.mark.parametrize('changes', [
    {'instrument_revision': 2}, {'calendar_revision': ''}, {'ends_at': STAMP},
    {'starts_at': END}, {'observed_at': START}, {'state': 'INVALID'},
    {'next_open_at': START}, {'starts_at': '2026-10-23T13:00:00'},
])
def test_calendar_invalid_is_unknown(equity_case, changes):
    c = equity_case
    c.session_row = replace(c.session_row, **changes)
    assert c.sessions.state(c.instrument, c.now).state == 'UNKNOWN'


def test_calendar_no_reader_and_reader_failure(equity_case):
    c = equity_case
    assert MarketSessionService(scope=c.scope).state(c.instrument, c.now).state == 'UNKNOWN'
    def failed(*_):
        raise OSError('calendar disconnected')
    c.sessions.read_session = failed
    assert c.sessions.state(c.instrument, c.now).state == 'UNKNOWN'


def test_split_adjusts_position_protection(equity_case):
    c = equity_case
    action, report = c.action()
    adjustment = c.book.apply(action, report)
    assert adjustment.quantity_before == qty('100')
    assert adjustment.quantity_after == qty('200')
    assert c.book.state.protection.stop_price == price('50')
    assert c.book.state.protection.coverage_quantity == qty('200')
    assert c.book.state.protection.state == t.ProtectionState.GAP
    assert c.book.state.protection.replacement_revision == 2
    assert c.book.apply(action, report) == adjustment
    assert len(c.book.state.applied) == 1
    restored = EquityBook(c.book.state, clock=lambda: STAMP)
    assert restored.apply(action, report) == adjustment
    with pytest.raises(ValueError, match='ID_CONFLICT'):
        c.book.apply(replace(action, ratio=Decimal('3')), report)


def test_reverse_split(equity_case):
    c = equity_case
    action, report = c.action(ratio=Decimal('.5'), after_qty='50', after_stop='200')
    assert c.book.apply(action, report).quantity_after == qty('50')
    assert c.book.state.protection.stop_price == price('200')


@pytest.mark.parametrize('ratio', [Decimal('0'), Decimal('-2'), Decimal('NaN'), Decimal('Infinity'), None])
def test_bad_split_ratio_no_mutation(equity_case, ratio):
    c = equity_case
    action, report = c.action()
    # Non-finite terms cannot be hashed into a proof. Inject them after
    # preparing valid evidence so rejection occurs in the production call.
    action = replace(action, ratio=ratio)
    before = c.book.state
    with pytest.raises(ValueError):
        c.book.apply(action, report)
    assert c.book.state == before


@pytest.mark.parametrize('field', ['action_hash', 'report_hash', 'before_hash', 'rule_revision',
                                  'after_position', 'after_protection'])
def test_action_proof_binding_atomic(equity_case, field):
    c = equity_case
    action, report = c.action()
    values = {'rule_revision': 9, 'after_position': c.position, 'after_protection': c.plan}
    c.action_proof = replace(c.action_proof, **{field: values.get(field, '0' * 64)})
    before = c.book.state
    with pytest.raises(ValueError):
        c.book.apply(action, report)
    assert c.book.state == before


def test_dividend_is_explicit_cashflow_not_automatically_spendable(equity_case):
    c = equity_case
    flow = t.Cashflow(at=STAMP, amount=money('25'), classification='DIVIDEND',
        internal_pair_id=None, evidence=c.ref)
    action, report = c.action('DIVIDEND', ratio=None, after_qty='100', after_stop='100', cashflow=flow)
    assert c.book.apply(action, report).cashflows == (flow,)
    assert c.availability.settled_available == money('6000')


def test_symbol_change_rebinds_catalog_and_protection(equity_case):
    c = equity_case
    action, report = c.action('SYMBOL_CHANGE', ratio=None, after_qty='100', after_stop='100', new_uid='equity:XYZ')
    result = c.book.apply(action, report)
    assert c.book.state.instrument.native_symbol == 'XYZ'
    assert c.book.state.position.scope.instrument_uid == 'equity:XYZ'
    assert c.book.state.protection.scope == c.book.state.position.scope
    assert c.book.apply(action, report) == result


def test_delisting_retains_position_and_marks_nontradable(equity_case):
    c = equity_case
    action, report = c.action('DELISTING', ratio=None, after_qty='100', after_stop='100')
    c.book.apply(action, report)
    assert c.book.state.tradable is False
    assert c.book.state.position.actual_quantity == qty('100')
    assert c.book.state.protection.state == t.ProtectionState.GAP


def test_missing_action_proof_and_fractional_rounding_deny(equity_case):
    c = equity_case
    action, report = c.action(ratio=Decimal('.333'), after_qty='33.3', after_stop='300')
    with pytest.raises(ValueError):
        c.book.apply(action, report)
    c.action_proof = None
    with pytest.raises(ValueError, match='PROOF_REQUIRED'):
        c.book.apply(action, report)


def test_healthy_hosted_never_native(equity_case):
    c = equity_case
    result = c.tick()
    assert result.plan.state == t.ProtectionState.VERIFIED_HOSTED
    assert result.commands == ()
    assert result.plan.last_verified_at == STAMP
    assert c.observations[-1].exit_receipt is None


@pytest.mark.parametrize('changes', [
    {'heartbeat_monotonic_ns': 0}, {'heartbeat_monotonic_ns': 21_000_000_000},
    {'network_healthy': False}, {'heartbeat_monitor_id': 'other'},
])
def test_hosted_loss_of_heartbeat(equity_case, changes):
    c = equity_case
    c.inputs = replace(c.inputs, **changes)
    result = c.tick()
    assert result.plan.state == t.ProtectionState.GAP
    assert result.plan.state != t.ProtectionState.VERIFIED_NATIVE
    assert result.locks[0].permission == t.RiskPermission.NO_NEW_RISK
    assert c.observations[-1].alert is not None
    assert 'HOSTED_HEARTBEAT_LOST' in result.missing


@pytest.mark.parametrize('changes', [
    {'protection_options': ('NATIVE_REQUIRED',)}, {'revoked_at': STAMP},
    {'expires_at': STAMP}, {'valid_from': END}, {'scopes': ()},
    {'owner_strategy_id': 'other'}, {'policy_revision': 99},
    {'protection_options': ('HOSTED_ALLOWED_CASH_ONLY', 'NATIVE_REQUIRED')},
    {'deployment_id': ''}, {'authorization_id': ''},
])
def test_hosted_explicit_authorization_required(equity_case, changes):
    c = equity_case
    c.inputs = replace(c.inputs, authorization=replace(c.authorization, **changes))
    assert 'EXPLICIT_HOSTED_AUTHORIZATION_REQUIRED' in c.tick().missing


@pytest.mark.parametrize('changes', [
    {'fully_cash_funded': False}, {'capital_at_risk': money('16000')},
    {'allowed_sessions': ('extended',)}, {'sellable': qty('101')},
    {'plan_hash': '0' * 64}, {'market_hash': '0' * 64}, {'session_hash': '0' * 64},
])
def test_hosted_negative_facts(equity_case, changes):
    c = equity_case
    c.inputs = replace(c.inputs, **changes)
    assert c.tick().plan.state == t.ProtectionState.GAP


@pytest.mark.parametrize('change', ['native', 'perpetual', 'short', 'partial', 'mark', 'stale', 'currency', 'unknown'])
def test_hosted_cannot_silently_downgrade_or_guess(equity_case, change):
    c = equity_case
    if change == 'native':
        c.plan = replace(c.plan, mode='NATIVE')
    elif change == 'perpetual':
        c.plan = replace(c.plan, scope=replace(c.scope, product=t.Product.USD_M_PERP))
    elif change == 'short':
        c.inputs = replace(c.inputs, position=replace(c.position, actual_quantity=qty('-100')))
    elif change == 'partial':
        c.plan = replace(c.plan, coverage_quantity=qty('50'))
    elif change == 'mark':
        c.plan = replace(c.plan, stop_trigger='MARK')
    elif change == 'stale':
        c.market = replace(c.market, quote=replace(c.market.quote, occurred_at=START))
    elif change == 'currency':
        c.market = replace(c.market, quote=replace(c.market.quote, last=t.Price(value=Decimal('90'), currency='EUR')))
    else:
        c.session = replace(c.session, state='UNKNOWN')
    c.rebind()
    assert c.tick().plan.state == t.ProtectionState.GAP


def test_pending_trigger_survives_monitor_recreation_and_price_rebound(equity_case):
    c = equity_case
    c.stop_triggered_while_halted()
    c.session = replace(c.session, state='OPEN', next_open_at=None)
    c.market = replace(c.market, quote=replace(c.market.quote, last=price('120')))
    c.rebind()
    c.hosted = c.new_hosted()
    result = c.tick()
    receipt = c.observations[-1].exit_receipt
    assert receipt.state == 'EXITING'
    assert 'UNSELLABLE_RESIDUAL' in receipt.incomplete_reasons
    assert c.observations[-1].flat_verified is False
    assert result.commands == ()


def test_storage_failure_does_not_claim_verified_or_send(equity_case):
    c = equity_case
    c.fail_record = True
    result = c.tick()
    assert result.plan.state == t.ProtectionState.GAP
    assert 'HOSTED_STORAGE_UNAVAILABLE' in result.missing
    assert result.commands == ()
    assert c.observations == []


def test_unbound_hosted_defaults_fail_closed(equity_case):
    c = equity_case
    result = HostedCashProtection(clock=lambda: c.now).tick(c.plan, c.market, c.session)
    assert result.plan.state == t.ProtectionState.GAP
    assert 'HOSTED_STORAGE_UNAVAILABLE' in result.missing


def test_live_scope_rejected_before_reader(equity_case):
    c = equity_case
    c.plan = replace(c.plan, scope=replace(c.scope, environment=t.Environment.LIVE))
    with pytest.raises(ValueError, match='ENVIRONMENT_NOT_ENABLED'):
        c.tick()
    assert c.observations == []


@pytest.mark.parametrize('changes', [
    {'owner_strategy_id': ''}, {'owner_book_id': ''}, {'ownership_epoch': 0},
])
def test_ownerless_position_cannot_establish_availability_or_hosted_protection(equity_case, changes):
    c = equity_case
    c.position = replace(c.position, **changes)
    c.inputs = replace(c.inputs, position=c.position)
    with pytest.raises(ValueError, match='SESSION_UNAVAILABLE'):
        c.require()
    assert 'CASH_ONLY_COVERAGE_REQUIRED' in c.tick().missing


def test_crossing_during_heartbeat_gap_remains_pending_after_recovery(equity_case):
    c = equity_case
    c.market = replace(c.market, quote=replace(c.market.quote, last=price('90')))
    c.inputs = replace(c.inputs, network_healthy=False)
    c.rebind()
    assert 'HOSTED_HEARTBEAT_LOST' in c.tick().missing
    assert c.observations[-1].exit_latched is True
    c.market = replace(c.market, quote=replace(c.market.quote, last=price('120')))
    c.inputs = replace(c.inputs, network_healthy=True)
    c.rebind()
    c.hosted = c.new_hosted()
    result = c.tick()
    assert result.plan.state == t.ProtectionState.GAP
    assert c.observations[-1].exit_receipt.state == 'EXITING'
    assert result.commands == ()


@pytest.mark.parametrize('stamp', [START, END])
def test_stale_or_future_crossing_is_not_new_trigger_evidence(equity_case, stamp):
    c = equity_case
    c.market = replace(c.market, quote=replace(c.market.quote,
        last=price('90'), occurred_at=stamp))
    c.rebind()
    assert 'HOSTED_MARKET_DATA_UNAVAILABLE' in c.tick().missing
    assert c.observations[-1].exit_latched is False


def test_bid_ask_trigger_uses_bid_for_cash_long(equity_case):
    c = equity_case
    c.plan = replace(c.plan, stop_trigger='BID_ASK')
    c.market = replace(c.market, quote=replace(c.market.quote, bid=price('99')))
    c.rebind()
    assert c.tick().commands == ()
    assert c.observations[-1].exit_receipt.state == 'EXITING'


def test_hosted_reader_failure_locks_and_alerts(equity_case):
    c = equity_case
    def unavailable(*_):
        raise OSError('synthetic archive unavailable')
    c.hosted.read_inputs = unavailable
    result = c.tick()
    assert result.plan.state == t.ProtectionState.GAP
    assert result.locks and c.observations[-1].alert
    assert result.commands == ()


@pytest.mark.parametrize('change', ['native_orders', 'active_exit', 'future', 'missing_evidence', 'wrong_scope'])
def test_corporate_action_requires_reconciliation_without_mutating_state(equity_case, change):
    c = equity_case
    if change == 'native_orders':
        c.book.state = replace(c.book.state,
            protection=replace(c.plan, native_order_ids=('existing-stop',)))
    elif change == 'active_exit':
        c.book.state = replace(c.book.state,
            position=replace(c.position, active_exit_plan_id='pending-exit'))
    action, report = c.action()
    if change == 'future':
        action = replace(action, effective_at=END)
    elif change == 'missing_evidence':
        report = replace(report, evidence=())
    elif change == 'wrong_scope':
        action = replace(action, scope=replace(c.scope, venue_account_id='other'))
    before = c.book.state
    with pytest.raises(ValueError):
        c.book.apply(action, report)
    assert c.book.state == before


def test_healthy_session_uses_half_open_evidence_interval(equity_case):
    c = equity_case
    assert c.sessions.state(c.instrument, c.now).state == 'OPEN'
    c.now = replace(c.now, utc_now=END)
    assert c.sessions.state(c.instrument, c.now).state == 'UNKNOWN'


def test_cash_and_action_readers_fail_without_state_changes(equity_case):
    c = equity_case
    def unavailable(*_):
        raise OSError('synthetic archive unavailable')
    c.cash.read_proof = unavailable
    with pytest.raises(OSError):
        c.require()
    action, report = c.action()
    before = c.book.state
    c.book.read_proof = unavailable
    with pytest.raises(OSError):
        c.book.apply(action, report)
    assert c.book.state == before
