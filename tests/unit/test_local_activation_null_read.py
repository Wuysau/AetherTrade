"""A missing local activation is absence, never a None subscript."""
from trading_system.contracts import types as t
from trading_system.execution.local_program_activation import read_local_activation


class _EmptyCursor:
    def fetchone(self):
        return None


class _EmptyTx:
    scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='fixture-account',
        product=t.Product.SPOT, instrument_uid='fixture-instrument', venue_leg='BOTH')

    def execute(self, statement, parameters=None):
        return _EmptyCursor()


def test_absent_local_activation_returns_none():
    assert read_local_activation(_EmptyTx(), 'local-program:missing') is None
