"""Variable quote fills have a real interval, never a normalization-point fill."""
from decimal import Decimal as D
from fractions import Fraction

import pytest

from trading_system.contracts import types as t


def bound(*,quote='4000',low='50000',high='60000',step='0.000001',asset='USD',unit='BASE'):
    from trading_system.intents.quote_fill_interval import compile_quote_fill_interval
    return compile_quote_fill_interval(maximum_gross_quote=t.Money(currency=asset,amount=D(quote)),
        minimum_price=t.Price(currency=asset,value=D(low)),maximum_price=t.Price(currency=asset,value=D(high)),
        quantity_step=t.Quantity(unit=unit,value=D(step)),notional_multiplier=D(1))


def test_unconditional_native_quote_interval_includes_zero_and_partial_fills():
    result=bound()
    assert result.minimum.value==0 and result.maximum.value==D('.08')
    assert result.minimum.unit==result.maximum.unit=='BASE'
    assert not result.execution_authorized
    assert result.fully_spent_minimum is None


def test_quantity_upper_encloses_recurring_quotients_without_rounding_inward():
    result=bound(low='15000',high='20000')
    exact=Fraction(4000,15000)
    assert Fraction(result.maximum.value)>=exact
    assert Fraction(result.maximum.value)-exact<Fraction(D('.000001'))
    assert result.minimum.value==0


def test_explicit_gross_quote_ceiling_includes_allowed_lot_overshoot():
    result=bound(quote='4000.01')
    assert Fraction(result.maximum.value)>=Fraction(D('4000.01'))/50000
    assert result.maximum.value>D('.08')


@pytest.mark.parametrize('unit,asset,step',[('BASE','EUR','.000001'),('SHARE','USD','.001')])
def test_quote_interval_keeps_real_quote_asset_and_native_ledger_unit(unit,asset,step):
    result=bound(unit=unit,asset=asset,step=step)
    assert result.maximum.unit==unit and result.gross_quote.currency==asset


@pytest.mark.parametrize('values',[dict(low='0'),dict(high='40000'),dict(quote='-1'),dict(step='0')])
def test_unbounded_or_invalid_price_and_lot_inputs_cannot_make_a_quantity_bound(values):
    with pytest.raises(ValueError):bound(**values)
