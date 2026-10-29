"""Explicit quote-band allocation reuses current scope and per-asset engines."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from test_quote_program_compilation import quote_source
from test_program_compilation import inputs,change,price
from test_program_target_allocation import scenario
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_program import TimeInForceSpec
from trading_system.intents.program_compilation import compile_order_program
from trading_system.intents.quote_program_compilation import compile_quote_program


def digest(value):return canonical_hash(domain_payload(value))


def allocation_case(*,market_step=None):
    from trading_system.intents.quote_target_allocation import QuoteTargetFactsV1,QuoteExecutionBoundsV1
    from trading_system.intents.product_rules import DecimalRange
    p,r,c,k=quote_source()
    p=replace(p,exit_plan=replace(p.exit_plan,stop_price='40000'))
    k={**k,'profile':replace(k['profile'],execution_price_envelope=DecimalRange(minimum='50000',maximum='60000'))}
    if market_step is not None:k={**k,'profile':replace(k['profile'],market_quantity_step=market_step)}
    compiled=compile_quote_program(p,r,c,**k)
    old,_,_,_=inputs();old=change(old,price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None))
    old=replace(old,exit_plan=p.exit_plan)
    legacy=compile_order_program(old,r,c,**k);book,scope=scenario(legacy,r)
    scope=replace(scope,compilation_hash=digest(compiled),normalization_price=None,
        unit_binding=replace(scope.unit_binding,compilation_hash=digest(compiled)))
    bounds=QuoteExecutionBoundsV1(contract_version='2026-10-30',record_kind='QUOTE_EXECUTION_BOUNDS_V1',
        scope=compiled.scope,compilation_hash=digest(compiled),rules_hash=compiled.rules_hash,profile_hash=compiled.profile_hash,
        node_id=compiled.nodes[0].original.node_id,enforcement_kind='PAPER_INTERNAL_ENVELOPE',model_hash='d'*64,
        minimum_price=t.Price(currency=r.quote_asset,value=D('50000')),
        maximum_price=t.Price(currency=r.quote_asset,value=D('60000')),
        maximum_gross_quote=t.Money(currency=r.quote_asset,amount=D('4000')),fees=(),
        evidence=scope.evidence,valid_until=scope.valid_until)
    facts=QuoteTargetFactsV1(contract_version='2026-10-30',record_kind='QUOTE_TARGET_FACTS_V1',
        scope_facts=scope,profile=k['profile'],capabilities=c,expected_build_digest=k['expected_build_digest'],
        expected_instrument_ref=k['expected_instrument_ref'],quantity_reservation_ids=(),bounds=bounds)
    return compiled,book,facts


def test_real_quote_allocation_preserves_native_budget_and_unconditional_position_interval():
    from trading_system.intents.quote_target_allocation import compile_quote_target
    plan,book,facts=allocation_case();result=compile_quote_target(plan,book,facts)
    assert result.native_quote_amount.amount==D('4000') and not result.blockers
    assert result.interval.minimum.value==0 and result.interval.maximum.value==D('.08')
    assert result.position.unmet_goal_possible and result.position.projected_maximum.value==D('.08')
    assert result.requests==plan.requests and result.compilation_hash==digest(plan)
    assert result.cash.required_assets==(t.Money(currency='USDT',amount=D('4000')),)
    assert not result.execution_authorized


def test_quote_allocation_keeps_foreign_cash_hold_in_financial_book_without_inventing_local_quantity():
    from trading_system.intents.quote_target_allocation import compile_quote_target
    from unit.support_ip_09 import make_case,reservation
    plan,book,facts=allocation_case();other=reservation(make_case())
    foreign=replace(other,reservation=replace(other.reservation,venue_account_id='other-account',amount=D('500')),
        held=D('500'))
    book=replace(book,reservations=(foreign,))
    facts=replace(facts,scope_facts=replace(facts.scope_facts,book_hash=digest(book)))
    result=compile_quote_target(plan,book,facts)
    assert result.book_hash==digest(book) and result.scope_state.working.value==0
    assert result.interval.maximum.value==D('.08') and not result.blockers
    assert book.reservations[0].held==500


@pytest.mark.parametrize('fault',['wrong_build','wrong_envelope_hash','expired','understated_quote','mapping'])
def test_quote_allocation_rejects_changed_execution_or_scope_bindings(fault):
    from trading_system.intents.quote_target_allocation import compile_quote_target
    plan,book,facts=allocation_case()
    if fault=='wrong_build':facts=replace(facts,expected_build_digest='e'*64)
    if fault=='wrong_envelope_hash':facts=replace(facts,bounds=replace(facts.bounds,compilation_hash='f'*64))
    if fault=='expired':facts=replace(facts,bounds=replace(facts.bounds,valid_until=facts.scope_facts.inputs.clock.utc_now))
    if fault=='understated_quote':facts=replace(facts,bounds=replace(facts.bounds,
        maximum_gross_quote=replace(facts.bounds.maximum_gross_quote,amount=D('3999'))))
    if fault=='mapping':facts=replace(facts,quantity_reservation_ids=('missing-hold',))
    with pytest.raises(ValueError):compile_quote_target(plan,book,facts)


def test_quote_scope_bridge_never_rounds_a_declared_market_step_at_ambient_precision():
    from trading_system.intents.quote_target_allocation import compile_quote_target
    step='0.00012345678901234567890123456789'
    plan,book,facts=allocation_case(market_step=step)
    result=compile_quote_target(plan,book,facts)
    assert result.interval.quantity_step.value==D(step)
