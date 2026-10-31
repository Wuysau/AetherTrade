"""Offline OP-E regressions: incremental management funding and split outcomes."""
from decimal import Decimal as D
import pytest

from trading_system.contracts import types as t
from trading_system.execution.management_outcome_v2 import (
    ManagementOutcomeV2, bind_management_outcome, management_outcome_hash,
)
from trading_system.risk.management_funding import incremental_management_requirements


def money(value, currency='USDT'):
    return t.Money(amount=D(value), currency=currency)


def test_modify_limit_requires_only_the_increase_but_never_releases():
    up = incremental_management_requirements(
        kind='MODIFY_LIMIT', product='USD_M_PERP', quote_asset='USDT',
        old_quantity='2', old_price='100', new_quantity='2', new_price='110',
        remaining_quantity='2', native_multiplier='1')
    assert up == (money('20'),)
    down = incremental_management_requirements(
        kind='MODIFY_LIMIT', product='USD_M_PERP', quote_asset='USDT',
        old_quantity='2', old_price='100', new_quantity='2', new_price='90',
        remaining_quantity='2', native_multiplier='1')
    assert down == ()
    partial = incremental_management_requirements(
        kind='MODIFY_LIMIT', product='USD_M_PERP', quote_asset='USDT',
        old_quantity='2', old_price='100', new_quantity='2', new_price='110',
        remaining_quantity='1', native_multiplier='1')
    assert partial == (money('120'),)


def test_cancel_replace_reserves_full_new_order_and_keeps_old_hold():
    result = incremental_management_requirements(
        kind='CANCEL_REPLACE', product='SPOT', quote_asset='USDT',
        old_quantity='1', old_price='100', new_quantity='1', new_price='100',
        remaining_quantity='1', new_fees=(money('2'),))
    assert result == (money('102'),)
    assert incremental_management_requirements(
        kind='CANCEL', product='SPOT', quote_asset='USDT', old_quantity='1',
        old_price='100', new_quantity=None, new_price=None,
        remaining_quantity='1') == ()
    assert incremental_management_requirements(
        kind='KEEP_PRIORITY', product='SPOT', quote_asset='USDT', old_quantity='2',
        old_price='100', new_quantity='1', new_price=None,
        remaining_quantity='2') == ()


@pytest.mark.parametrize('fault', ['kind', 'product', 'terms', 'fee'])
def test_management_funding_fails_closed(fault):
    kwargs = dict(kind='MODIFY_LIMIT', product='USD_M_PERP', quote_asset='USDT',
                  old_quantity='2', old_price='100', new_quantity='2',
                  new_price='110', remaining_quantity='2')
    if fault == 'kind':
        kwargs['kind'] = 'UNKNOWN'
    if fault == 'product':
        kwargs['product'] = 'SPOT'
    if fault == 'terms':
        kwargs['new_price'] = None
    if fault == 'fee':
        kwargs['new_fees'] = (object(),)
    with pytest.raises(ValueError):
        incremental_management_requirements(**kwargs)


@pytest.mark.parametrize('cancel,replacement,duty', [
    ('SUCCESS', 'FAILURE', 'REPLACEMENT_FAILED_OLD_HOLD_RETAINED'),
    ('FAILURE', 'NOT_ATTEMPTED', 'ORIGINAL_ORDER_STILL_LIVE_OR_LATE_FILL'),
    ('UNKNOWN', 'SUCCESS', 'BOTH_ORDERS_MAY_BE_LIVE'),
    ('SUCCESS', 'UNKNOWN', 'QUERY_BOTH_ORIGINAL_IDS'),
    ('SUCCESS', 'SUCCESS', 'REPLACEMENT_MUST_HAVE_OWN_COMMAND_AND_PERMIT'),
])
def test_cancel_replace_outcomes_stay_independent(cancel, replacement, duty):
    outcome = bind_management_outcome(
        command_id='cmd-1', request_hash='r' * 64,
        cancel_result=cancel, replacement_result=replacement,
        raw_payload_hash='p' * 64, not_attempted_reason=(
            'RATE_LIMIT' if 'NOT_ATTEMPTED' in (cancel, replacement) else None))
    assert type(outcome) is ManagementOutcomeV2
    assert outcome.cancel_result == cancel and outcome.replacement_result == replacement
    assert duty in outcome.duties and 'NO_FUNDS_RELEASE' in outcome.duties
    assert outcome.funds_released is False and outcome.children_terminal is False
    assert outcome.execution_authorized is False
    assert len(management_outcome_hash(outcome)) == 64


def test_not_attempted_requires_a_reason_and_bad_state_rejected():
    with pytest.raises(ValueError, match='NOT_ATTEMPTED_REASON'):
        bind_management_outcome(command_id='c', request_hash='r', cancel_result='FAILURE',
                                replacement_result='NOT_ATTEMPTED', raw_payload_hash='p')
    with pytest.raises(ValueError, match='OUTCOME_STATE_INVALID'):
        bind_management_outcome(command_id='c', request_hash='r', cancel_result='OK',
                                replacement_result='SUCCESS', raw_payload_hash='p')
