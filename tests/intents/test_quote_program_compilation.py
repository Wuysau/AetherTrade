"""New economic goal keeps the original native quote request unchanged."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from test_program_compilation import inputs,change,price
from trading_system.contracts import types as t
from trading_system.contracts.order_program import TimeInForceSpec
from trading_system.contracts.quote_position_v1 import (
    PositionValueBasisV1,AbsolutePositionValueBandV1,QuotePositionProposalV1)


def quote_source(product=t.Product.SPOT,side='BUY'):
    p,r,c,k=inputs(product)
    p=change(p,price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None))
    p=change(p,side=side)
    if product==t.Product.US_EQUITY:p=change(p,tokenize=False,trading_session=None,
        tif=TimeInForceSpec(kind='DAY',expires_at=None))
    goal=AbsolutePositionValueBandV1(kind='ABSOLUTE_POSITION_VALUE_BAND',side='LONG',
        minimum_quote=t.Money(currency=r.quote_asset,amount=D('3500')),
        maximum_quote=t.Money(currency=r.quote_asset,amount=D('4500')),
        valuation_basis=PositionValueBasisV1(kind='DECLARED_REFERENCE_PRICE',
            reference_price=t.Price(currency=r.quote_asset,value=D('50000')),notional_multiplier=D(1)))
    source=QuotePositionProposalV1(contract_version='2026-10-30',record_kind='POSITION_VALUE_BAND_PROPOSAL_V1',
        proposal_id=p.proposal_id,instrument_ref=p.instrument_ref,action='SET_TARGET',goal=goal,
        requested_leverage=p.requested_leverage,program=p.program,protection_policy=p.protection_policy,
        exit_plan=p.exit_plan,valid_until=p.valid_until,evidence_refs=p.evidence_refs,summary=p.summary)
    return source,r,c,k


@pytest.mark.parametrize('product,side',[(t.Product.SPOT,'BUY'),(t.Product.SPOT,'SELL'),(t.Product.US_EQUITY,'BUY')])
def test_new_band_compiler_preserves_native_quote_body_and_never_sizes_at_reference_mark(product,side):
    from trading_system.intents.quote_program_compilation import compile_quote_program,QuoteProgramCompilationV1
    from trading_system.execution.creation_requests import materialize_creation,assign_client_ids,EquityWalletSelection
    p,r,c,k=quote_source(product,side);compiled=compile_quote_program(p,r,c,**k)
    assert type(compiled) is QuoteProgramCompilationV1 and compiled.original_proposal==p
    wallet=None
    if product==t.Product.US_EQUITY:
        wallet=EquityWalletSelection(scope=k['scope'],profile_hash=compiled.profile_hash,quote_asset=r.quote_asset,
            wallet='MAIN',evidence=k['profile'].evidence[0],valid_until=k['profile'].valid_until)
    request=materialize_creation(compiled,0,k['profile'],assign_client_ids(compiled,0,'quote-original'),
        wallet_selection=wallet,now=k['now'])
    body=dict(request.form_parameters())
    assert body['quoteOrderQty' if product==t.Product.SPOT else 'notional']=='4000'
    assert 'quantity' not in body and 'price' not in body and body['type' if product==t.Product.SPOT else 'orderType']=='MARKET'
    assert not compiled.execution_authorized


def test_real_equity_sell_cannot_invent_a_notional_request_field():
    from trading_system.intents.quote_program_compilation import compile_quote_program
    p,r,c,k=quote_source(t.Product.US_EQUITY,'SELL')
    with pytest.raises(ValueError):compile_quote_program(p,r,c,**k)


def test_quote_value_basis_currency_and_multiplier_must_match_actual_product():
    from trading_system.intents.quote_program_compilation import compile_quote_program
    p,r,c,k=quote_source()
    wrong=replace(p,goal=replace(p.goal,valuation_basis=replace(p.goal.valuation_basis,notional_multiplier=D(2))))
    with pytest.raises(ValueError):compile_quote_program(wrong,r,c,**k)
