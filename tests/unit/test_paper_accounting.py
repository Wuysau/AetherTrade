from decimal import Decimal, localcontext

import pytest

from trading_system.server.paper_accounting import realized_by_order


def row(side, quantity, price, command_id):
    return dict(side=side, quantity=Decimal(quantity), price=Decimal(price), command_id=command_id)


def test_realized_result_allocates_both_entry_and_exit_fees_exactly():
    events = [row('BUY', '0.1', '50000', 'entry'), row('SELL', '0.04', '60000', 'partial'),
              row('SELL', '0.06', '60000', 'rest')]
    with localcontext() as context:
        context.prec = 2
        result = realized_by_order(events)
    assert result == {'partial': Decimal('395.6'), 'rest': Decimal('593.4')}


def test_price_gain_can_still_be_a_loss_after_both_fees():
    result = realized_by_order([row('BUY', '0.1', '50000', 'buy'), row('SELL', '0.1', '50075', 'sell')])
    assert result['sell'] == Decimal('-2.5075')


def test_multiple_partial_fills_of_one_exit_have_one_realized_result():
    result = realized_by_order([row('BUY', '1', '100', 'buy'), row('SELL', '0.4', '110', 'sell'),
        row('SELL', '0.6', '110', 'sell')])
    assert result == {'sell': Decimal('9.79')}


def test_weighted_cost_uses_all_entries_and_rejects_uncovered_short():
    events = [row('BUY', '1', '100', 'buy-a'), row('BUY', '1', '200', 'buy-b'), row('SELL', '1', '150', 'sell')]
    assert realized_by_order(events)['sell'] == Decimal('-0.3')
    with pytest.raises(ValueError, match='uncovered'):
        realized_by_order([row('SELL', '1', '100', 'invalid')])
