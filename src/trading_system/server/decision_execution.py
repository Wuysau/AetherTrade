"""Deterministic target and observation helpers; these never authorize orders."""
from decimal import Decimal
from .errors import ApiError


def proposal_target(target, price):
    from trading_system.intents.semantic_validation import quantity_from_notional, quantity_on_grid
    if target['side'] == 'SHORT':
        raise ApiError('CASH_SHORT', '现货不允许卖空。', 409)
    value = Decimal(target['value'])
    if target['side'] == 'FLAT' and value != 0:
        raise ApiError('INVALID_TARGET', 'FLAT 目标必须为零。', 409)
    step = Decimal('.000000000001')
    return (quantity_from_notional(value, price, step, True) if target['sizing'] == 'QUOTE_NOTIONAL'
            else quantity_on_grid(value, step, False))


def watch_triggered(conditions, previous, current):
    for condition in conditions:
        kind = condition.get('kind')
        if kind == 'NEW_BAR':
            interval = condition.get('evidence_ref', '').removeprefix('candles:')
            before = previous.get('closed_by_interval', {}).get(interval, previous.get('closed_until'))
            after = current.get('closed_by_interval', {}).get(interval, current.get('closed_until'))
            if before and after and after > before:
                return True
        elif kind in ('PRICE_ABOVE', 'PRICE_BELOW') and previous.get('price') and current.get('price'):
            before, after, threshold = map(Decimal, (previous['price'], current['price'], condition['value']))
            if (before <= threshold < after if kind == 'PRICE_ABOVE' else before >= threshold > after):
                return True
    return False
