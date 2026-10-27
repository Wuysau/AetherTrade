"""Synthetic handoff checks; no PostgreSQL/CAS-role or native qualification claim."""
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from integration.support_ip_16 import OwnershipCase
from trading_system.contracts import types as t


@pytest.mark.parametrize('product,negative', [
    (t.Product.US_EQUITY, False), (t.Product.SPOT, False),
    (t.Product.USD_M_PERP, False), (t.Product.USD_M_PERP, True),
])
@pytest.mark.parametrize('precision,amount', [
    (2, '1.001'), (28, '1.00000000000000000000000000001'),
])
@pytest.mark.parametrize('phase', [t.TransferState.VERIFY_PROTECTION, t.TransferState.COMMIT_OWNER_EPOCH])
@pytest.mark.parametrize('covered', [False, True])
def test_owner_handoff_requires_exact_protection_coverage(product, negative, precision, amount, phase, covered):
    unit = {t.Product.US_EQUITY: 'SHARE', t.Product.SPOT: 'BASE', t.Product.USD_M_PERP: 'CONTRACT'}[product]
    scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='synthetic-precision',
        product=product, instrument_uid='synthetic-precision-instrument',
        venue_leg='SHORT' if negative else 'BOTH')
    c = OwnershipCase(scope=scope, seed=False)
    c.quantity = lambda value: t.Quantity(value=Decimal(value), unit=unit)
    actual = Decimal(amount)
    c.position = replace(c.position, actual_quantity=t.Quantity(
        value=actual.copy_negate() if negative else actual, unit=unit))
    # Seed the same synthetic owner ledger used by OwnershipCase, with product units.
    c.registry.owners[scope] = ('old', 1, 1)
    c.registry.register(c.position, 1)
    c.start()
    c.advance_to(phase)
    evidence = c.evidence()
    evidence = replace(evidence, protection=replace(evidence.protection,
        covered_quantity=c.quantity(amount if covered else '1.00')))
    with localcontext() as ctx:
        ctx.prec = precision
        result = c.service.advance(c.transfer_id, evidence)
    owner = c.registry.read(scope)
    if covered:
        expected = t.TransferState.COMMIT_OWNER_EPOCH if phase == t.TransferState.VERIFY_PROTECTION else t.TransferState.ACTIVE
        assert result.state == expected and not result.blocked_reasons
        assert owner.position.owner_strategy_id == ('old' if phase == t.TransferState.VERIFY_PROTECTION else 'new')
    else:
        assert result.state == t.TransferState.TRANSFER_BLOCKED
        assert result.blocked_reasons == ('PROTECTION_OR_ACCEPTED_EXIT_PLAN_REQUIRED',)
        assert owner.position.owner_strategy_id == 'old' and owner.position.ownership_epoch == 1
        assert c.registry.owners[scope] == ('old', 1, 1)
        assert c.service.advance(c.transfer_id, c.evidence()).state == t.TransferState.TRANSFER_BLOCKED
    assert owner.position.actual_quantity == c.position.actual_quantity
    assert owner.position.active_exit_plan_id == 'preserved-exit-plan'
    assert owner.protection_responsibility == 'SYSTEM_PROTECTION'
    assert not c.probe().allowed
    with c.registry.transaction(scope) as tx:
        assert any(lock.cause == 'OWNERSHIP_TRANSFER' and lock.state == 'ACTIVE' for lock in tx.locks())
