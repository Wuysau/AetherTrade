from datetime import datetime, timezone

import pytest

from trading_system.server.store import Store
from trading_system.server.errors import ApiError


def test_sqlite_order_fails_closed_without_creating_legacy_fill():
    store = Store('sqlite:///:memory:')
    store.initialize()
    store.setup('owner', 'long-enough-password', '10000', 12)
    market = {'status': 'CONNECTED', 'quotes': [{'symbol': 'BTCUSDT', 'price': '100',
        'as_of': datetime.now(timezone.utc).isoformat(), 'stale': False}]}
    with pytest.raises(ApiError) as error:
        store.order({'symbol': 'BTCUSDT', 'side': 'BUY', 'quantity': '1'}, 'new-order', market, 90)
    assert error.value.code == 'UNIFIED_PAPER_REQUIRED'
    dashboard = store.dashboard(market)
    assert dashboard['orders'] == [] and dashboard['positions'] == []
    assert dashboard['execution']['mode'] == 'UNAVAILABLE'


def test_rule_activates_without_model_cost_and_requires_explicit_automation_grant():
    store = Store('sqlite:///:memory:')
    store.initialize()
    store.setup('owner', 'long-enough-password', '10000', 12)
    payload = dict(name='Threshold', symbol='BTCUSDT', mode='RULE', budget='100', target_pct='1',
        status='ACTIVE', auto_execute=True, model_profile_ids=[],
        rule=dict(kind='PRICE_THRESHOLD', operator='LTE', price='100', stop_loss_pct='5'))
    with pytest.raises(ApiError) as error:
        store.create_strategy(payload)
    assert error.value.code == 'PAPER_AUTOMATION_ACK_REQUIRED'
    result = store.create_strategy({**payload, 'acknowledge_paper_automation': True})
    assert result['status'] == 'ACTIVE' and result['mode'] == 'RULE'
