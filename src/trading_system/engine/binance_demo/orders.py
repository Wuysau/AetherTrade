"""Pure SPOT LIMIT/MARKET compilation; compiling never grants send authority."""
import re
from .contracts import DemoError, decimal


def _within(value, rule, minimum, maximum, step):
    low, high, tick = (decimal(rule.get(name, '0')) for name in (minimum, maximum, step))
    if (low and value < low) or (high and value > high) or (tick and value % tick):
        raise DemoError('DEMO_FILTER_REJECTED')


def compile_spot_order(rules, *, side, order_type, quantity, client_id, reference_price, price=None):
    if side not in {'BUY', 'SELL'} or order_type not in {'LIMIT', 'MARKET'}:
        raise DemoError('DEMO_CAPABILITY_NOT_SUPPORTED')
    if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,36}', client_id):
        raise DemoError('DEMO_CLIENT_ID_INVALID')
    if rules.get('status') != 'TRADING' or order_type not in rules.get('orderTypes', []):
        raise DemoError('DEMO_SYMBOL_UNAVAILABLE')
    symbol = rules.get('symbol', '')
    if not re.fullmatch(r'[A-Z0-9]{5,20}', symbol):
        raise DemoError('DEMO_SYMBOL_INVALID')
    qty, reference = decimal(quantity, positive=True), decimal(reference_price, positive=True)
    filters = {item['filterType']: item for item in rules.get('filters', [])}
    if 'LOT_SIZE' not in filters or 'PRICE_FILTER' not in filters or not ({'MIN_NOTIONAL', 'NOTIONAL'} & filters.keys()):
        raise DemoError('DEMO_RULES_INCOMPLETE')
    _within(qty, filters['LOT_SIZE'], 'minQty', 'maxQty', 'stepSize')
    if order_type == 'MARKET' and 'MARKET_LOT_SIZE' in filters:
        _within(qty, filters['MARKET_LOT_SIZE'], 'minQty', 'maxQty', 'stepSize')
    result = {'symbol': symbol, 'side': side, 'type': order_type, 'quantity': str(qty), 'newClientOrderId': client_id}
    px = decimal(price, positive=True) if order_type == 'LIMIT' else reference
    if order_type == 'LIMIT':
        _within(px, filters['PRICE_FILTER'], 'minPrice', 'maxPrice', 'tickSize')
        result.update(price=str(px), timeInForce='GTC')
    elif price is not None:
        raise DemoError('DEMO_MARKET_PRICE_FORBIDDEN')
    for name in ('MIN_NOTIONAL', 'NOTIONAL'):
        rule = filters.get(name)
        if rule:
            if px * qty < decimal(rule['minNotional']) or (decimal(rule.get('maxNotional', '0')) and px * qty > decimal(rule['maxNotional'])):
                raise DemoError('DEMO_NOTIONAL_REJECTED')
    # Unknown filters can change admission semantics. Reject rather than guess.
    supported = {'LOT_SIZE', 'MARKET_LOT_SIZE', 'PRICE_FILTER', 'MIN_NOTIONAL', 'NOTIONAL', 'MAX_NUM_ORDERS', 'MAX_NUM_ALGO_ORDERS'}
    if filters.keys() - supported:
        raise DemoError('DEMO_FILTER_NOT_QUALIFIED')
    return result
