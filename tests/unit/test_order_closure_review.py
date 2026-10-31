"""Default deny proof contract tests; no account or database access."""
import pytest
from trading_system.risk.order_program_closure import validate_review, ClosureReview

def test_scalar_true_never_approves_closure():
    with pytest.raises(ValueError):
        validate_review(True, {}, '2026-10-29T00:00:00Z')
