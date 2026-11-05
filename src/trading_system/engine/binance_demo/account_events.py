"""Conservative Spot account-position parsing; no ledger mutations.

The official event contains only assets possibly changed. It does not provide
an event ID or a documented REST/stream sequence barrier.
"""
import re
from .contracts import DemoError, decimal


def parse_account_position(event):
    if (not isinstance(event, dict) or event.get('e') != 'outboundAccountPosition' or
            type(event.get('E')) is not int or type(event.get('u')) is not int or
            not 0 <= event['u'] <= event['E'] or not isinstance(event.get('B'), list) or
            not 1 <= len(event['B']) <= 1000):
        raise DemoError('DEMO_ACCOUNT_POSITION_INVALID')
    result = {}
    for item in event['B']:
        if (not isinstance(item, dict) or not isinstance(item.get('a'), str) or
                not re.fullmatch(r'[A-Z0-9]{1,20}', item['a']) or item['a'] in result):
            raise DemoError('DEMO_ACCOUNT_POSITION_INVALID')
        result[item['a']] = (decimal(item.get('f')), decimal(item.get('l')))
    return result
