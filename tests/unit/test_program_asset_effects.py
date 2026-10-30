"""Asset arithmetic only. These fixtures are not approved execution evidence."""
from dataclasses import replace
from decimal import Decimal as D,localcontext

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.program_financial import FinancialUnit
from trading_system.execution.paper_program_reports import NormalizedPaperFill,normalize_native_economics


def arithmetic_fill(*,side='BUY',fees=(),known=True,product=t.Product.SPOT):
    scope=t.ScopeKey(environment=t.Environment.RESEARCH,venue_account_id='math-only',product=product,
        instrument_uid='math-only-btc',venue_leg='BOTH')
    unit=FinancialUnit(native_unit='BTC',ledger_unit='BASE',ledger_units_per_native_unit=D(1),native_multiplier=D(1),ledger_multiplier=D(1))
    q=t.Quantity(value=D('.4'),unit='BTC');price=t.Price(value=D('81490'),currency='USDT')
    economics=normalize_native_economics(quantity=q,price=price,fees=fees,
        reported_gross=t.Money(amount=D('32596'),currency='USDT'),unit=unit)
    ref=t.EvidenceRef(id='math-only',hash='a'*64,environment=scope.environment,
        source='MATH_FIXTURE_NOT_EXECUTION_EVIDENCE',visible_at='2026-10-28T10:00:00Z')
    identity=t.OrderIdentity(scope=scope,logical_client_order_id='math-only',native_client_order_id=None,
        venue_client_order_id=None,venue_order_id=None)
    fill=t.Fill(fill_id='math-only',venue='BINANCE',scope=scope,native_trade_id='math-only',order=identity,
        quantity=q,price=price,fees=fees,occurred_at=ref.visible_at,correction_of=None,evidence=ref)
    return NormalizedPaperFill(fill=fill,side=side,economics=economics,raw=None,fees_known=known)


@pytest.mark.parametrize('side,expected,position',[
    ('BUY',dict(BTC=D('.4'),USDT=D('-32628.596')),D('.4')),
    ('SELL',dict(BTC=D('-.4'),USDT=D('32563.404')),D('-.4'))])
def test_spot_principal_and_exact_quote_fee(side,expected,position):
    from trading_system.execution.program_asset_effects import derive_spot_asset_effects
    normalized=arithmetic_fill(side=side,fees=(t.Money(amount=D('32.596'),currency='USDT'),))
    result=derive_spot_asset_effects(normalized,base_asset='BTC',quote_asset='USDT')
    assert {m.currency:m.amount for m in result.asset_deltas}==expected
    assert result.position_delta==t.Quantity(value=position,unit='BASE')
    assert not result.balance_credit_authorized and not result.execution_authorized


def test_base_and_third_asset_fees_keep_their_actual_assets():
    from trading_system.execution.program_asset_effects import derive_spot_asset_effects
    normalized=arithmetic_fill(fees=(t.Money(amount=D('.0004'),currency='BTC'),t.Money(amount=D('.01'),currency='BNB')))
    result=derive_spot_asset_effects(normalized,base_asset='BTC',quote_asset='USDT')
    assert {m.currency:m.amount for m in result.asset_deltas}==dict(BTC=D('.3996'),USDT=D('-32596'),BNB=D('-.01'))
    assert result.position_delta.value==D('.3996')


def test_zero_fee_requires_explicit_known_economics():
    from trading_system.execution.program_asset_effects import derive_spot_asset_effects
    result=derive_spot_asset_effects(arithmetic_fill(),base_asset='BTC',quote_asset='USDT')
    assert {m.currency:m.amount for m in result.asset_deltas}==dict(BTC=D('.4'),USDT=D('-32596'))
    with pytest.raises(ValueError,match='FEE_RECONCILIATION_REQUIRED'):
        derive_spot_asset_effects(arithmetic_fill(known=False),base_asset='BTC',quote_asset='USDT')


def test_futures_never_uses_spot_gross_principal_debits():
    from trading_system.execution.program_asset_effects import derive_spot_asset_effects
    with pytest.raises(ValueError,match='SPOT_ASSET_EFFECTS_REQUIRED'):
        derive_spot_asset_effects(arithmetic_fill(product=t.Product.USD_M_PERP),base_asset='BTC',quote_asset='USDT')


def test_reported_gross_discrepancy_does_not_become_certain_cash():
    from trading_system.execution.program_asset_effects import derive_spot_asset_effects
    normalized=arithmetic_fill()
    normalized=replace(normalized,economics=replace(normalized.economics,reported_gross=t.Money(amount=D(1),currency='USDT'),
        discrepancies=('GROSS_NOTIONAL_MISMATCH',)))
    with pytest.raises(ValueError,match='ECONOMICS_RECONCILIATION_REQUIRED'):
        derive_spot_asset_effects(normalized,base_asset='BTC',quote_asset='USDT')


def test_native_asset_and_unit_binding_cannot_be_relabelled():
    from trading_system.execution.program_asset_effects import derive_spot_asset_effects
    with pytest.raises(ValueError,match='CASH_NATIVE_UNIT_BINDING_MISMATCH'):
        derive_spot_asset_effects(arithmetic_fill(),base_asset='ETH',quote_asset='USDT')


def test_fee_asset_arithmetic_remains_exact_under_low_global_precision():
    from trading_system.execution.program_asset_effects import derive_spot_asset_effects
    normalized=arithmetic_fill(fees=(t.Money(amount=D('.0000000123456789'),currency='BTC'),))
    with localcontext() as arithmetic:
        arithmetic.prec=3
        result=derive_spot_asset_effects(normalized,base_asset='BTC',quote_asset='USDT')
    assert result.position_delta.value==D('.3999999876543211')
    assert {m.currency:m.amount for m in result.asset_deltas}['USDT']==D('-32596')
