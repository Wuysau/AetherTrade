"""Native economics are source facts, not a Spot q*price assumption or permit."""
from decimal import Decimal as D,localcontext
from hashlib import sha256

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


def test_missing_host_readers_return_no_verified_facts_or_financial_permissions():
    from trading_system.execution.paper_program_reports import PaperProgramReportVerifier
    scope=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='account',product=t.Product.SPOT,
        instrument_uid='instrument',venue_leg='BOTH')
    result=PaperProgramReportVerifier(None,scope).verify('original-command')
    assert result.status=='BLOCKED' and not result.source_verified and not result.fills and not result.orders
    assert not result.execution_authorized and not result.funds_release_authorized
    assert 'QUERY_ORIGINAL_IDS' in result.retained_duties


def test_verifier_rejects_external_environments_before_any_reader_is_called():
    from trading_system.execution.paper_program_reports import PaperProgramReportVerifier
    scope=t.ScopeKey(environment=t.Environment.LIVE,venue_account_id='account',product=t.Product.SPOT,
        instrument_uid='instrument',venue_leg='BOTH')
    with pytest.raises(ValueError,match='PAPER_ONLY'):
        PaperProgramReportVerifier(None,scope)


@pytest.mark.parametrize('constant',['NaN','Infinity','-Infinity'])
def test_raw_source_is_strict_json_even_in_unrecognized_fields(constant):
    from trading_system.execution.paper_program_reports import _raw_object
    raw='{"source":"PAPER_MARKET_REPLAY_SYNTHETIC","extra":'+constant+'}'
    ref=t.EvidenceRef(id='raw',hash=sha256(raw.encode()).hexdigest(),environment=t.Environment.PAPER,
        source='PAPER_MARKET_REPLAY_SYNTHETIC',visible_at='2026-10-28T10:00:00Z')
    with pytest.raises(ValueError):_raw_object(raw,ref)
