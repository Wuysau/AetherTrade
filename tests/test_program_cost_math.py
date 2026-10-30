from decimal import Decimal as D,localcontext
from fractions import Fraction as F
import pytest
from trading_system.execution.program_cost_math import SpotCost,apply_spot_cost,exact_decimal
from trading_system.storage.models import MissingEvidence


def test_recurring_partial_sell_is_exact_and_flat_has_no_residue():
    s=apply_spot_cost(SpotCost(),side='BUY',base_delta=D(3),quote_delta=D(-10))
    s=apply_spot_cost(s,side='SELL',base_delta=D(-1),quote_delta=D(4))
    assert s==SpotCost(F(2),F(20,3),F(2,3))
    assert exact_decimal(s.realized) is None
    s=apply_spot_cost(s,side='SELL',base_delta=D(-2),quote_delta=D(9))
    assert s==SpotCost(F(0),F(0),F(3))


def test_net_base_and_quote_fees_are_not_charged_twice():
    s=apply_spot_cost(SpotCost(),side='BUY',base_delta=D('.99'),quote_delta=D('-101'))
    assert s==SpotCost(F(99,100),F(101),F(0))
    s=apply_spot_cost(s,side='SELL',base_delta=D('-.11'),quote_delta=D('19.8'))
    assert s.quantity==F(88,100) and s.cost==F(808,9)
    assert s.realized==F(386,45)


def test_external_fee_requires_fx_and_oversell_fails():
    with pytest.raises(MissingEvidence,match='FX'):
        apply_spot_cost(SpotCost(),side='BUY',base_delta=D(1),quote_delta=D(-10),external_fee=True)
    with pytest.raises(MissingEvidence,match='SELL'):
        apply_spot_cost(SpotCost(),side='SELL',base_delta=D(-1),quote_delta=D(10))


def test_finite_conversion_ignores_decimal_context():
    with localcontext() as ctx:
        ctx.prec=2
        assert exact_decimal(F(32628596,1000))==D('32628.596')


def test_homogeneous_same_time_economics_commute_but_mixed_side_does_not():
    # This algebra justifies accepting equal native timestamps for one side.
    a=dict(side='BUY',base_delta=D(3),quote_delta=D(-10))
    b=dict(side='BUY',base_delta=D(2),quote_delta=D(-9))
    assert apply_spot_cost(apply_spot_cost(SpotCost(),**a),**b)==apply_spot_cost(apply_spot_cost(SpotCost(),**b),**a)
    initial=SpotCost(F(5),F(19),F(0))
    x=dict(side='SELL',base_delta=D(-1),quote_delta=D(5))
    y=dict(side='SELL',base_delta=D('-1.1'),quote_delta=D('5.7'))
    assert apply_spot_cost(apply_spot_cost(initial,**x),**y)==apply_spot_cost(apply_spot_cost(initial,**y),**x)
    assert apply_spot_cost(apply_spot_cost(initial,**a),**x)!=apply_spot_cost(apply_spot_cost(initial,**x),**a)
