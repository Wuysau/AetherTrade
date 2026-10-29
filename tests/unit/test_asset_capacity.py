"""Incremental financial sufficiency, without authorizing or executing orders."""
from dataclasses import replace
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from trading_system.contracts import types as t
from trading_system.risk.reservation_repository import balance_watermark, merge_holds
from unit.support_ip_09 import make_case, reservation
from unit.support_ip_06 import money


def inputs(**changes):
    from trading_system.risk.asset_capacity import CapacityInputs
    return replace(CapacityInputs(incremental_requirements=(money('30'),),
        venue_free_balances=(money('100'),),remaining_group_capacity=(money('100'),),
        remaining_strategy_capacity=(money('100'),),buffers=(money('0'),),
        reconciled_group_holds=(),owner_strategy_id='owner'),**changes)


def hold(amount='70',reflected='0',pending='0',owner='owner',identifier='hold',asset='USDT'):
    p=reservation(make_case(),identifier=identifier)
    return replace(p,held=Decimal(amount),reflected=Decimal(reflected),release_pending=Decimal(pending),
        reservation=replace(p.reservation,amount=Decimal(amount),reflected_amount=Decimal(reflected),
            owner_strategy_id=owner,asset=asset))


def check(value):
    from trading_system.risk.asset_capacity import check_capacity
    return check_capacity(value)


@pytest.mark.parametrize('field',['venue_free_balances','remaining_group_capacity','remaining_strategy_capacity','buffers'])
def test_missing_required_currency_fails_closed(field):
    result=check(inputs(**{field:(money('100000','BTC'),)}))
    assert not result.sufficient and result.rejection_codes==('INSUFFICIENT_CAPACITY:USDT',)


@pytest.mark.parametrize('required,free,group,strategy,held,reflected,pending,owner,expected',[
    ('30','30','100','100','70','70','0','owner',True),
    ('30','30','100','100','70','0','0','owner',False),
    ('1','30','100','100','70','70','70','owner',False),
    ('30','80','100','100','70','20','40','owner',True),
    ('31','100','100','100','70','70','0','other',False),
    ('30','100','100','30','70','0','0','other',True),
    ('30','100','100','30','70','0','0','owner',False),
])
def test_legacy_asset_cases_preserve_reflection_pending_and_owner_limits(required,free,group,strategy,
        held,reflected,pending,owner,expected):
    result=check(inputs(incremental_requirements=(money(required),),venue_free_balances=(money(free),),
        remaining_group_capacity=(money(group),),remaining_strategy_capacity=(money(strategy),),
        reconciled_group_holds=(hold(held,reflected,pending,owner),)))
    assert result.sufficient is expected
    assert result.rejection_codes==(() if expected else ('INSUFFICIENT_CAPACITY:USDT',))


def test_other_account_and_other_owner_still_consume_shared_pool():
    p=hold(owner='other')
    p=replace(p,reservation=replace(p.reservation,venue_account_id='another-account'))
    assert not check(inputs(incremental_requirements=(money('31'),),reconciled_group_holds=(p,))).sufficient


def test_no_currency_conversion_or_order_dependent_asset_disappearance():
    value=inputs(incremental_requirements=(money('1','BTC'),money('1','USDT')),
        venue_free_balances=(money('100000','USDT'),money('0','BTC')),
        remaining_group_capacity=(money('100000','USDT'),money('10','BTC')),
        remaining_strategy_capacity=(money('100000','USDT'),money('10','BTC')),
        buffers=(money('0','USDT'),money('0','BTC')))
    assert check(value).rejection_codes==('INSUFFICIENT_CAPACITY:BTC',)


def test_current_book_reconciliation_controls_credit_before_capacity():
    case=make_case();book=replace(case.book,balances=(money('30'),))
    p=hold(reflected='70')
    p=replace(p,reservation=replace(p.reservation,evidence_watermark=balance_watermark(book,book.evidence[0])))
    current=merge_holds((p,),book)
    assert check(inputs(venue_free_balances=book.balances,reconciled_group_holds=current)).sufficient
    newer=replace(book,sequence=book.sequence+1)
    stale=merge_holds((p,),newer)
    assert not check(inputs(venue_free_balances=newer.balances,reconciled_group_holds=stale)).sufficient


def test_unknown_snapshot_release_never_discards_financial_obligation():
    case=make_case();p=hold()
    p=replace(p,held=Decimal(0),reservation=replace(p.reservation,state=t.ReservationState.RELEASED))
    book=replace(case.book,reservations=(p,))
    unknown=merge_holds((),book)
    assert not check(inputs(incremental_requirements=(money('31'),),reconciled_group_holds=unknown)).sufficient


@pytest.mark.parametrize('precision',[6,28,80])
def test_tiny_held_debit_is_never_rounded_away(precision):
    large='1'+'0'*80;available='1'+'0'*78+'70'
    value=inputs(incremental_requirements=(money('70'),),venue_free_balances=(money(available),),
        remaining_group_capacity=(money(available),),remaining_strategy_capacity=(money(available),),
        reconciled_group_holds=(hold('1',identifier='small'),hold(large,identifier='large')))
    with localcontext() as arithmetic:
        arithmetic.prec=precision
        assert not check(value).sufficient


def test_arithmetic_outside_exact_boundary_fails_closed():
    large='1'+'0'*300
    value=inputs(reconciled_group_holds=(hold(large),),venue_free_balances=(money(large),),
        remaining_group_capacity=(money(large),),remaining_strategy_capacity=(money(large),))
    with pytest.raises(ValueError,match='CAPACITY_ARITHMETIC_NOT_EXACT'):
        check(value)


@pytest.mark.parametrize('fault',['negative','nan','duplicate','wrong_type','bad_subset','duplicate_hold'])
def test_invalid_financial_inputs_never_become_capacity(fault):
    value=inputs()
    if fault=='negative':value=replace(value,venue_free_balances=(money('-1'),))
    if fault=='nan':value=replace(value,venue_free_balances=(SimpleNamespace(amount=Decimal('NaN'),currency='USDT'),))
    if fault=='duplicate':value=replace(value,venue_free_balances=(money('1'),money('1')))
    if fault=='wrong_type':value=replace(value,venue_free_balances=(SimpleNamespace(amount=1,currency='USDT'),))
    if fault=='bad_subset':value=replace(value,reconciled_group_holds=(hold(reflected='71'),))
    if fault=='duplicate_hold':value=replace(value,reconciled_group_holds=(hold(),hold()))
    with pytest.raises(ValueError):check(value)


def test_zero_increment_and_public_amounts_alias_preserve_legacy_contract():
    from trading_system.risk.asset_capacity import amounts
    from trading_system.risk.reservations import amounts as public_amounts
    assert amounts is public_amounts
    assert amounts((money('1'),))=={'USDT':Decimal(1)}
    assert check(inputs(incremental_requirements=(),venue_free_balances=(),
        remaining_group_capacity=(),remaining_strategy_capacity=(),buffers=())).sufficient
