"""Exact inventory bounds with synthetic lifecycle proofs, never venue evidence."""
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from integration.support_ip_15 import ProtectionCase
from trading_system.contracts import types as t
from trading_system.protection.native import digest


PRODUCTS = [(t.Product.US_EQUITY, False), (t.Product.SPOT, False),
            (t.Product.USD_M_PERP, False), (t.Product.USD_M_PERP, True)]


@pytest.fixture(params=PRODUCTS)
def case(request):
    product, negative = request.param
    c = ProtectionCase(product=product, amount='0.999')
    unit = {t.Product.US_EQUITY: 'SHARE', t.Product.SPOT: 'BASE', t.Product.USD_M_PERP: 'CONTRACT'}[product]
    c.q = lambda value: t.Quantity(value=Decimal(value), unit=unit)
    c.position = replace(c.position, actual_quantity=c.q('-0.999' if negative else '0.999'))
    c.fill = replace(c.fill, quantity=c.q('0.999'))
    c.plan = replace(c.plan, coverage_quantity=c.q('0.999'))
    c.capability = replace(c.capability, minimum_quantity=c.q('0.001'), quantity_step=c.q('0.001'))
    try:
        yield c
    finally:
        c.close()


def test_fill_protection_preserves_exact_owned_quantity(case):
    with localcontext() as ctx:
        ctx.prec = 2
        result = case.protect()
    assert result.plan.coverage_quantity == case.q('0.999')
    command, = result.commands
    assert command.canonical_payload.quantity == '0.999'
    assert command.canonical_payload.side == ('BUY' if case.position.actual_quantity.value < 0 else 'SELL')
    assert case.record().position == case.position


def test_off_grid_fill_cannot_round_into_a_tradable_quantity(case):
    value = Decimal('-0.9999' if case.position.actual_quantity.value < 0 else '0.9999')
    case.position = replace(case.position, actual_quantity=t.Quantity(value=value, unit=case.q('1').unit))
    case.fill = replace(case.fill, quantity=case.q('0.9999'))
    with localcontext() as ctx:
        ctx.prec = 2
        result = case.protect()
    assert result.plan.coverage_quantity == case.q('0.9999')
    assert 'BELOW_VENUE_MINIMUM_OR_STEP' in result.missing
    assert not result.commands and not case.dispatcher.calls
    assert result.plan.state == t.ProtectionState.GAP and result.locks


def test_replacement_cannot_round_owned_quantity_up(case):
    case.protect()
    new = case.next_plan(coverage_quantity=case.q('1'))
    before = tuple(case.dispatcher.calls)
    with localcontext() as ctx:
        ctx.prec = 2
        result = case.manager.replace_protection(case.position, new, case.capability,
                                                approved_flow='CANCEL_CONFIRM_REPLACE')
    assert 'REPLACEMENT_PARAMETERS_INVALID' in result.missing
    assert not result.commands and tuple(case.dispatcher.calls) == before


def test_native_verifier_cannot_accept_rounded_coverage(case):
    ref = case.ref_order('oversized-stop')
    plan = replace(case.plan, coverage_quantity=case.q('1'), native_order_ids=(ref,))
    projection = case.projection(ref, leaves='1')
    projection = replace(projection, filled_quantity=case.q('0'), leaves_quantity=case.q('1'))
    report = case.report(orders=(projection,))
    case.attest_native(plan, report)
    with localcontext() as ctx:
        ctx.prec = 2
        verdict = case.verifier.verify(plan, report, case.position)
    assert verdict.state == t.ProtectionState.GAP
    assert verdict.missing == ('PLAN_OR_EXPOSURE_MISMATCH',)
    assert verdict.covered_quantity == case.q('0')


def test_exit_authority_cannot_round_reducible_quantity_up(case):
    context = replace(case.prepare_exit(), reducible_quantity=case.q('1'))
    case.exit_facts = replace(case.exit_facts, context_hash=digest(context))
    with localcontext() as ctx:
        ctx.prec = 2
        receipt = case.exits.request(case.position, context)
    assert 'REDUCIBLE_QUANTITY_INVALID' in receipt.incomplete_reasons
    assert not receipt.command_ids and not case.dispatcher.calls


def test_valid_native_stop_remains_exact_under_low_precision(case):
    result = case.protect()
    plan = result.plan
    projection = case.projection(plan.native_order_ids[0], leaves='0.999')
    projection = replace(projection, filled_quantity=case.q('0'), leaves_quantity=case.q('0.999'))
    report = case.report(orders=(projection,))
    case.attest_native(plan, report)
    with localcontext() as ctx:
        ctx.prec = 2
        verdict = case.verifier.verify(plan, report, case.position)
    assert verdict.state == t.ProtectionState.VERIFIED_NATIVE
    assert verdict.covered_quantity == case.q('0.999') and not verdict.missing
