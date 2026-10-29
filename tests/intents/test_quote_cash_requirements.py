"""Quote amounts, native quantity and per-asset fees never substitute units."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from trading_system.contracts import types as t
from trading_system.intents.quote_fill_interval import compile_quote_fill_interval
from trading_system.risk.program_exposure import FeeBound


def interval(*,asset='USDT',unit='BASE'):
    return compile_quote_fill_interval(maximum_gross_quote=t.Money(currency=asset,amount=D('4000')),
        minimum_price=t.Price(currency=asset,value=D('50000')),maximum_price=t.Price(currency=asset,value=D('60000')),
        quantity_step=t.Quantity(unit=unit,value=D('.000001')),notional_multiplier=D(1))


def fee(asset,amount,debit):return FeeBound(amount=t.Money(currency=asset,amount=D(amount)),debit=debit)


def derive(*,side='BUY',fees=(),base='BTC',bound=None):
    from trading_system.risk.quote_cash_requirements import derive_quote_cash_requirements
    return derive_quote_cash_requirements(bound or interval(),side=side,base_asset=base,fees=fees)


def amounts(rows):return {m.currency:m.amount for m in rows}


def test_buy_receipt_base_fee_does_not_become_external_cash_or_fake_fixed_net_quantity():
    result=derive(fees=(fee('BTC','.00008','FROM_RECEIPT'),))
    assert amounts(result.required_assets)=={'USDT':D('4000')}
    assert result.net_received_minimum.amount==0 and result.net_received_maximum.amount==D('.08')
    assert result.net_received_maximum.currency=='BTC'
    assert amounts(result.receipt_fee_bounds)=={'BTC':D('.00008')}
    assert not result.execution_authorized


def test_external_base_fee_requires_owned_asset_even_when_buy_would_receive_base():
    result=derive(fees=(fee('BTC','.001','EXTERNAL_BALANCE'),))
    assert amounts(result.required_assets)=={'BTC':D('.001'),'USDT':D('4000')}
    assert result.position_delta_minimum.value==D('-.001')
    assert result.position_delta_maximum.value==D('.08')


def test_sell_quote_budget_requires_maximum_owned_base_plus_external_base_fee():
    result=derive(side='SELL',fees=(fee('BTC','.0001','EXTERNAL_BALANCE'),))
    assert amounts(result.required_assets)=={'BTC':D('.0801')}
    assert result.position_delta_minimum.value==D('-.0801') and result.position_delta_maximum.value==0


def test_sell_receipt_quote_fee_is_separate_from_gross_proceeds_and_not_prefunded_quote():
    result=derive(side='SELL',fees=(fee('USDT','10','FROM_RECEIPT'),))
    assert amounts(result.required_assets)=={'BTC':D('.08')}
    assert result.net_received_minimum.amount==0 and result.net_received_maximum.amount==D('4000')
    assert amounts(result.receipt_fee_bounds)=={'USDT':D(10)}


def test_third_asset_fee_is_reserved_in_its_real_asset_without_implicit_fx():
    result=derive(fees=(fee('BNB','.01','EXTERNAL_BALANCE'),))
    assert amounts(result.required_assets)=={'BNB':D('.01'),'USDT':D('4000')}


def test_real_share_and_non_usdt_quote_units_are_preserved():
    result=derive(base='REAL_SHARE',bound=interval(asset='EUR',unit='SHARE'))
    assert amounts(result.required_assets)=={'EUR':D('4000')}
    assert result.net_received_maximum.currency=='REAL_SHARE' and result.position_delta_maximum.unit=='SHARE'


@pytest.mark.parametrize('fault',['unknown_fees','wrong_receipt_asset','forged_interval'])
def test_missing_fee_facts_or_inconsistent_interval_cannot_lower_debits(fault):
    args={}
    if fault=='unknown_fees':args['fees']=None
    if fault=='wrong_receipt_asset':args['fees']=(fee('BNB','.01','FROM_RECEIPT'),)
    if fault=='forged_interval':args['bound']=replace(interval(),maximum=t.Quantity(unit='BASE',value=D('.001')))
    with pytest.raises(ValueError):derive(**args)
