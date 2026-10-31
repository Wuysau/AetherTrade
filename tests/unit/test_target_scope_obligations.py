"""Shared position/leaf reader for scalar and interval economic targets."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from unit.support_ip_09 import make_case,position,order
from trading_system.risk.exposure import WorkingOrder
from trading_system.intents.target_compiler import TargetCompiler


def terms(c):
    from trading_system.intents.target_compiler import TargetScopeTerms
    old=c.bind()
    return TargetScopeTerms(scope=c.book.scope,venue=old.venue,owner_strategy_id=old.owner_strategy_id,
        ownership_epoch=old.ownership_epoch,collateral_group_id=old.collateral_group_id,
        basis_fingerprint=old.basis_fingerprint,target_side='LONG',unit=old.quantity_unit,
        valid_until=old.valid_until,submission_deadline=old.execution.entry_deadline)


def test_scope_reader_preserves_actual_fee_dust_without_normalizing_a_fake_target():
    c=make_case();book=replace(c.book,positions=(position(c.book.scope,'.0000005'),))
    result=TargetCompiler(c.target).read_scope_terms(terms(c),book)
    assert result.actual.value==D('.0000005') and result.working.value==0
    assert result.obligations==() and not result.execution_authorized


def test_scope_reader_retains_each_direction_and_unknown_obligation_before_interval_projection():
    c=make_case();scope=c.book.scope
    book=replace(c.book,positions=(position(scope,'.7'),),active_orders=(
        order(scope,'.2',identifier='buy',certainty='UNKNOWN'),order(scope,'.1',identifier='sell')))
    inputs=replace(c.target,order_sides=(WorkingOrder(logical_client_order_id='buy',side='BUY'),
        WorkingOrder(logical_client_order_id='sell',side='SELL')))
    result=TargetCompiler(inputs).read_scope_terms(terms(c),book)
    assert result.actual.value==D('.7') and result.working.value==D('.1')
    assert tuple(x.signed_quantity.value for x in result.obligations)==(D('.2'),D('-.1'))
    assert result.obligations[0].uncertain and not result.obligations[1].uncertain


@pytest.mark.parametrize('fault',['foreign_owner','unknown_zero','unhealthy_clock'])
def test_scope_reader_keeps_existing_owner_evidence_and_clock_guards(fault):
    c=make_case();book=c.book;inputs=c.target
    if fault=='foreign_owner':book=replace(book,positions=(position(book.scope,'.1',owner='other'),))
    if fault=='unknown_zero':
        book=replace(book,active_orders=(order(book.scope,'0',certainty='UNKNOWN'),))
        inputs=replace(inputs,order_sides=(WorkingOrder(logical_client_order_id='order',side='BUY'),))
    if fault=='unhealthy_clock':inputs=replace(inputs,clock=replace(inputs.clock,clock_health='UNHEALTHY'))
    with pytest.raises(ValueError):TargetCompiler(inputs).read_scope_terms(terms(c),book)
