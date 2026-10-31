"""A position-value band is explicit new source semantics, not scalar tolerance."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.order_codec import VersionedOrderCodec
from unit.support_program_financial import financial_case


def source():
    from trading_system.contracts.quote_position_v1 import (
        PositionValueBasisV1,AbsolutePositionValueBandV1,QuotePositionProposalV1)
    old=financial_case().intent.proposal
    basis=PositionValueBasisV1(kind='DECLARED_REFERENCE_PRICE',
        reference_price=t.Price(currency='USDT',value=D('50000')),notional_multiplier=D(1))
    goal=AbsolutePositionValueBandV1(kind='ABSOLUTE_POSITION_VALUE_BAND',side='LONG',
        minimum_quote=t.Money(currency='USDT',amount=D('3500')),
        maximum_quote=t.Money(currency='USDT',amount=D('4500')),valuation_basis=basis)
    return QuotePositionProposalV1(contract_version='2026-10-30',record_kind='POSITION_VALUE_BAND_PROPOSAL_V1',
        proposal_id=old.proposal_id,instrument_ref=old.instrument_ref,action='SET_TARGET',goal=goal,
        requested_leverage=old.requested_leverage,program=old.program,protection_policy=old.protection_policy,
        exit_plan=old.exit_plan,valid_until=old.valid_until,evidence_refs=old.evidence_refs,summary=old.summary)


def test_new_source_roundtrips_explicit_band_basis_and_immutable_program():
    p=source();codec=VersionedOrderCodec();payload=codec.encode('order-proposal',p)
    assert codec.decode('order-proposal',payload)==p
    assert payload['goal']['kind']=='ABSOLUTE_POSITION_VALUE_BAND'
    assert payload['goal']['valuation_basis']['reference_price']['value']=='50000'
    assert 'target_value' not in payload and 'target_sizing' not in payload
    assert not p.execution_authorized


def test_existing_scalar_source_cannot_acquire_silent_interval_tolerance():
    new=source();codec=VersionedOrderCodec();old=financial_case().intent.proposal
    original=codec.encode('order-proposal',old);original_hash=codec.hash('order-proposal',old)
    with pytest.raises(Exception):codec.decode('order-proposal',dict(original,goal=codec.encode('order-proposal',new)['goal']))
    assert codec.hash('order-proposal',old)==original_hash


@pytest.mark.parametrize('fault',['inverted','currency','valuation','zero_long'])
def test_source_band_has_real_nonnegative_matching_currency_and_valuation(fault):
    p=source();goal=p.goal
    with pytest.raises(ValueError):
        if fault=='inverted':replace(goal,minimum_quote=replace(goal.minimum_quote,amount=D('5000')))
        if fault=='currency':replace(goal,maximum_quote=replace(goal.maximum_quote,currency='EUR'))
        if fault=='valuation':replace(goal.valuation_basis,reference_price=replace(goal.valuation_basis.reference_price,value=D(0)))
        if fault=='zero_long':replace(goal,minimum_quote=replace(goal.minimum_quote,amount=D(0)),
            maximum_quote=replace(goal.maximum_quote,amount=D(0)))


def test_desired_band_minimum_does_not_imply_any_fill_or_completion():
    p=source()
    assert p.goal.minimum_quote.amount>0
    assert not hasattr(p.goal,'filled_quantity') and not hasattr(p.goal,'goal_reached')
    assert not p.execution_authorized
    assert p.goal.minimum_quote.amount==D('3500') and p.goal.maximum_quote.amount==D('4500')
