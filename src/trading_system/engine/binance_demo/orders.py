"""Pure SPOT LIMIT/MARKET compilation; compiling never grants send authority."""
import re
from .contracts import DemoError, decimal


def _within(value, rule, minimum, maximum, step):
    low, high, tick = (decimal(rule.get(name, '0')) for name in (minimum, maximum, step))
    if (low and value < low) or (high and value > high) or (tick and value % tick):
        raise DemoError('DEMO_FILTER_REJECTED')


def compile_spot_order(rules, *, side, order_type, quantity, client_id, reference_price, price=None, filter_context=None):
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
    context = filter_context or {}
    for name in ('PERCENT_PRICE', 'PERCENT_PRICE_BY_SIDE'):
        rule = filters.get(name)
        if rule:
            minutes = rule['avgPriceMins']
            if minutes == 0:
                average = decimal(context.get('last_price'), positive=True)
            else:
                if context.get('average_minutes') != minutes:
                    raise DemoError('DEMO_AVERAGE_PRICE_EVIDENCE_REQUIRED')
                average = decimal(context.get('average_price'), positive=True)
            prefix = ('bid' if side == 'BUY' else 'ask') if name == 'PERCENT_PRICE_BY_SIDE' else ''
            low = rule[prefix + 'MultiplierDown'] if prefix else rule['multiplierDown']
            high = rule[prefix + 'MultiplierUp'] if prefix else rule['multiplierUp']
            if not average * decimal(low) <= px <= average * decimal(high):
                raise DemoError('DEMO_PERCENT_PRICE_REJECTED')
    for rule in list(filters.values()) + rules.get('exchangeFilters', []):
        name = rule['filterType']
        if name in {'MAX_NUM_ORDERS', 'EXCHANGE_MAX_NUM_ORDERS'}:
            count = context.get('open_order_count' if name == 'MAX_NUM_ORDERS' else 'exchange_open_order_count')
            if type(count) is not int or count < 0 or count >= rule['maxNumOrders']:
                raise DemoError('DEMO_ORDER_CAPACITY_REJECTED')
        elif name == 'MAX_POSITION' and side == 'BUY':
            if decimal(context.get('account_base_quantity')) + decimal(context.get('pending_buy_quantity')) + qty > decimal(rule['maxPosition']):
                raise DemoError('DEMO_MAX_POSITION_REJECTED')
    if any(item['filterType'] not in {'EXCHANGE_MAX_NUM_ORDERS', 'EXCHANGE_MAX_ALGO_ORDERS', 'EXCHANGE_MAX_NUM_ICEBERG_ORDERS'} for item in rules.get('exchangeFilters', [])):
        raise DemoError('DEMO_EXCHANGE_FILTER_NOT_QUALIFIED')
    # Unknown filters can change admission semantics. Reject rather than guess.
    supported = {'LOT_SIZE', 'MARKET_LOT_SIZE', 'PRICE_FILTER', 'MIN_NOTIONAL', 'NOTIONAL', 'MAX_NUM_ORDERS', 'MAX_NUM_ALGO_ORDERS', 'PERCENT_PRICE', 'PERCENT_PRICE_BY_SIDE', 'MAX_POSITION', 'ICEBERG_PARTS', 'TRAILING_DELTA', 'MAX_NUM_ORDER_AMENDS', 'MAX_NUM_ICEBERG_ORDERS'}
    if filters.keys() - supported:
        raise DemoError('DEMO_FILTER_NOT_QUALIFIED')
    return result
