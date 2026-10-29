"""A small financial credit cannot disappear in a caller's decimal context."""
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext

import pytest

from trading_system.risk.reservation_repository import increases_program_financial_credit


@dataclass(frozen=True)
class Reservation:
    amount: Decimal
    reflected_amount: Decimal
    evidence_watermark: str = 'unchanged'


@dataclass(frozen=True)
class Projection:
    held: Decimal
    reflected: Decimal
    release_pending: Decimal
    reservation: Reservation


@pytest.mark.parametrize('caller_precision', [6, 28, 80])
def test_exact_credit_comparison_ignores_caller_precision(caller_precision):
    held = Decimal('1' + '0' * 48)
    before = Projection(held=held, reflected=Decimal(1), release_pending=held,
        reservation=Reservation(amount=held, reflected_amount=Decimal(1)))
    after = replace(before, release_pending=Decimal('9' * 48))
    # True debit falls from 10**48 to 10**48-1. In a low precision context,
    # rounding held-reflected upward used to conceal that exact difference.
    with localcontext() as arithmetic:
        arithmetic.prec = caller_precision
        assert increases_program_financial_credit(before, after)
