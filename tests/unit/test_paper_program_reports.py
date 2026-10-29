"""Native economics are source facts, not a Spot q*price assumption or permit."""
from decimal import Decimal as D,localcontext

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.program_financial import FinancialUnit


def normalize(**changes):
    from trading_system.execution.paper_program_reports import normalize_native_economics
    values=dict(quantity=t.Quantity(value=D('3'),unit='CONTRACT'),price=t.Price(value=D('81490'),currency='USDT'),
        fees=(t.Money(amount=D('.001'),currency='BNB'),),reported_gross=t.Money(amount=D('244.47'),currency='USDT'),
        unit=FinancialUnit(native_unit='CONTRACT',ledger_unit='BASE',ledger_units_per_native_unit=D('.001'),
            native_multiplier=D('.001'),ledger_multiplier=D('1')))
    values.update(changes)
    return normalize_native_economics(**values)


def test_native_contract_quantity_and_price_are_preserved_with_explicit_ledger_bridge():
    result=normalize()
    assert result.native_quantity==t.Quantity(value=D('3'),unit='CONTRACT')
    assert result.venue_price==t.Price(value=D('81490'),currency='USDT')
    assert result.ledger_quantity==t.Quantity(value=D('.003'),unit='BASE')
    assert result.expected_gross==t.Money(amount=D('244.47'),currency='USDT')
    assert result.fees==(t.Money(amount=D('.001'),currency='BNB'),)
    assert result.discrepancies==() and result.execution_authorized is False and result.source_verified is False


def test_contracts_with_ledger_multiplier_do_not_drop_the_native_gross_multiplier():
    unit=FinancialUnit(native_unit='CONTRACT',ledger_unit='CONTRACT',ledger_units_per_native_unit=D('1'),
        native_multiplier=D('10'),ledger_multiplier=D('10'))
    result=normalize(unit=unit,reported_gross=t.Money(amount=D('2444700'),currency='USDT'))
    assert result.native_quantity==result.ledger_quantity and result.expected_gross.amount==D('2444700')
    assert result.venue_price.value==D('81490') and not result.discrepancies


def test_reported_gross_mismatch_is_retained_without_capping_quantity_or_repricing():
    result=normalize(reported_gross=t.Money(amount=D('1'),currency='USDT'))
    assert result.reported_gross.amount==D('1') and result.expected_gross.amount==D('244.47')
    assert result.native_quantity.value==3 and result.venue_price.value==81490
    assert result.discrepancies==('GROSS_NOTIONAL_MISMATCH',)


def test_quote_currency_mismatch_never_creates_an_implicit_conversion():
    result=normalize(reported_gross=t.Money(amount=D('244.47'),currency='USDC'))
    assert result.reported_gross.currency=='USDC' and result.expected_gross.currency=='USDT'
    assert result.discrepancies==('GROSS_CURRENCY_MISMATCH',)


def test_ambient_decimal_precision_does_not_erase_native_economic_facts():
    with localcontext() as ctx:
        ctx.prec=3
        result=normalize(quantity=t.Quantity(value=D('123456789012345678901234567890.123'),unit='CONTRACT'),
            reported_gross=t.Money(amount=D('10060493736616049373661604937366.12327'),currency='USDT'))
    assert result.ledger_quantity.value==D('123456789012345678901234567.890123')
    assert result.expected_gross.amount==D('10060493736616049373661604937366.12327')
    assert not result.discrepancies


@pytest.mark.parametrize('quantity',[t.Quantity(value=D('3'),unit='BASE'),t.Quantity(value=D('0'),unit='CONTRACT')])
def test_invalid_native_unit_or_empty_fill_is_not_a_normalized_fill(quantity):
    with pytest.raises(ValueError):normalize(quantity=quantity)
