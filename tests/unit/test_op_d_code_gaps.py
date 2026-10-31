"""Offline OP-D code-gap regressions: bar path, execution range, lists, OCO."""
import pytest

from trading_system.contracts import types as t
from trading_system.contracts.execution_range_v1 import (
    ExecutionRangeDecision, ExecutionRangeEventV1, resolve_execution_range,
)
from trading_system.engine.paper_bar_v2 import (
    BarPathDecision, PaperBarEventV2, bar_source_hash, decide_bar_path,
)
from trading_system.execution.local_oco_race import OcoLegFill, classify_local_oco
from trading_system.execution.native_list_lifecycle import (
    ListMember, resolve_list_terminal,
)


SCOPE = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='paper-1',
                   product=t.Product.SPOT, instrument_uid='BTCUSDT', venue_leg='BOTH')


def bar(**changes):
    values = dict(contract_version='2026-10-31', scope=SCOPE, venue_symbol='BTCUSDT',
                  interval_seconds=60, open_time='2026-10-28T10:00:00Z',
                  close_time='2026-10-28T10:01:00Z', open='100', high='120', low='80',
                  close='110', sequence=1, closed=True, source_hash='b' * 64)
    values.update(changes)
    return PaperBarEventV2(**values)


def test_bar_both_levels_is_explicitly_ambiguous_and_never_fills():
    decision = decide_bar_path(bar(), side='BUY', take_profit='118', stop_loss='82')
    assert decision.state == 'AMBIGUOUS_BOTH_LEVELS_TOUCHED'
    assert decision.policy == 'NO_BAR_PATH_INFERENCE' and decision.fills == ()
    assert 'RECONCILE_AMBIGUOUS_EXIT' in decision.duties
    assert not decision.execution_authorized and not decision.funds_release_authorized


def test_bar_single_touch_is_unknown_not_a_fill():
    decision = decide_bar_path(bar(), side='BUY', take_profit='118', stop_loss='70')
    assert decision.state == 'PATH_UNKNOWN' and decision.fills == ()
    assert 'KEEP_EXIT_DUTY' in decision.duties


def test_open_bar_is_rejected_as_a_path_source():
    decision = decide_bar_path(bar(closed=False), side='BUY', take_profit='118', stop_loss='82')
    assert decision.state == 'PATH_UNKNOWN' and 'KEEP_EXIT_DUTY' in decision.duties


def test_bar_levels_outside_range_are_not_touched():
    decision = decide_bar_path(bar(), side='SELL', take_profit='70', stop_loss='130')
    assert decision.state == 'NO_LEVEL_TOUCHED' and decision.fills == ()
    assert bar_source_hash(bar()) == bar_source_hash(bar())


def test_bar_rejects_bad_side_or_level_order():
    with pytest.raises(ValueError):
        decide_bar_path(bar(), side='HOLD', take_profit='118', stop_loss='82')
    with pytest.raises(ValueError):
        decide_bar_path(bar(), side='BUY', take_profit='80', stop_loss='120')


def event(**changes):
    values = dict(contract_version='2026-10-31', scope=SCOPE, venue_symbol='BTCUSDT',
                  revision=1, reference_price='100', lower_price='99', upper_price='101',
                  valid_from='2026-10-28T09:00:00Z', valid_until='2026-10-28T11:00:00Z',
                  source_hash='e' * 64)
    values.update(changes)
    return ExecutionRangeEventV1(**values)


def test_execution_range_allows_only_current_band():
    assert resolve_execution_range(event(), price='100.5',
                                   now='2026-10-28T10:00:00Z').status == 'ALLOW'
    out = resolve_execution_range(event(), price='102', now='2026-10-28T10:00:00Z')
    assert out.status == 'EXPIRED'
    assert out.reason == 'EXECUTION_RULE_PRICE_RANGE_EXCEEDED'
    assert not out.execution_authorized


def test_missing_or_expired_execution_range_blocks_without_market_fallback():
    assert resolve_execution_range(None, price='100',
                                   now='2026-10-28T10:00:00Z').status == 'BLOCK'
    expired = resolve_execution_range(event(), price='100', now='2026-10-28T11:00:00Z')
    assert expired.status == 'EXPIRED'
    with pytest.raises(ValueError):
        resolve_execution_range(event(lower_price='102'), price='100',
                                now='2026-10-28T10:00:00Z')


def test_list_working_cancel_releases_pending_only_as_a_duty():
    decision = resolve_list_terminal('OTO', (
        ListMember(member_id='working', state='CANCELED', role='WORKING'),
        ListMember(member_id='pending', state='WAITING', role='PENDING')))
    assert decision.parent_state == 'COMPLETED'
    assert {d.member_id: d.state for d in decision.dispositions} == {
        'working': 'CANCELED', 'pending': 'CANCELED'}
    assert 'VERIFY_PENDING_LEG_NEVER_SENT' in decision.duties
    assert not decision.funds_release_authorized


def test_list_unknown_member_keeps_parent_reconciliation():
    decision = resolve_list_terminal('OCO', (
        ListMember(member_id='one', state='FILLED', role='WORKING'),
        ListMember(member_id='two', state='UNKNOWN', role='PENDING')))
    assert decision.parent_state == 'RECONCILIATION_REQUIRED'
    assert 'QUERY_ORIGINAL_IDS' in decision.duties
    assert decision.execution_authorized is False


def test_list_still_live_and_fully_terminal():
    live = resolve_list_terminal('OPOCO', (
        ListMember(member_id='working', state='PARTIALLY_FILLED', role='WORKING'),
        ListMember(member_id='pending', state='WAITING', role='PENDING')))
    assert live.parent_state == 'ACTIVE' and 'RETAIN_LIST_DUTY' in live.duties
    done = resolve_list_terminal('OCO', (
        ListMember(member_id='one', state='FILLED', role='WORKING'),
        ListMember(member_id='two', state='CANCELED', role='PENDING')))
    assert done.parent_state == 'COMPLETED'


def fill(child, fill_id='f1'):
    return OcoLegFill(child_id=child, quantity='0.1', price='100', fill_id=fill_id)


def test_software_oco_double_execution_is_reconciled_not_hidden():
    decision = classify_local_oco({'a': (fill('a'),), 'b': (fill('b'),)})
    assert decision.state == 'SOFTWARE_OCO_DOUBLE_EXECUTED'
    assert decision.executed_children == ('a', 'b')
    assert 'RECONCILE_SOFTWARE_OCO_DOUBLE_EXECUTION' in decision.duties
    assert decision.native_equivalent is False and decision.funds_release_authorized is False


def test_software_oco_single_and_none_retain_duties():
    single = classify_local_oco({'a': (fill('a'),), 'b': ()})
    assert single.state == 'SOFTWARE_OCO_SINGLE_EXECUTED'
    assert 'CANCEL_SIBLING_BY_ORIGINAL_ID_AND_VERIFY' in single.duties
    none = classify_local_oco({'a': (), 'b': ()})
    assert none.state == 'SOFTWARE_OCO_NONE_EXECUTED'
    with pytest.raises(ValueError):
        classify_local_oco({'a': (), 'b': (), 'c': ()})
