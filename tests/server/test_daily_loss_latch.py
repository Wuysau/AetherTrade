from datetime import datetime, timedelta, timezone

import pytest

from trading_system.server.errors import ApiError
from trading_system.server.store import Store
from server.test_store import market, buy


def test_loss_lock_survives_denial_rebound_restart_and_day_rollover(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'loss.sqlite3'}"
    db = Store(url)
    db.initialize()
    db.setup('owner', 'a-long-password', '100000', 24)
    db.order(buy('0.1'), 'entry', market(), 90)
    with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
        db.order(buy('0.01'), 'loss', market('10000'), 90)
    db.close()
    db = Store(url)
    db.initialize()
    with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
        db.order(buy('0.01'), 'rebound', market('50000'), 90)
    snapshot = db.dashboard(market())
    lock = snapshot['risk_locks'][0]
    assert lock['cause'] == 'DAILY_LOSS' and lock['permission'] == 'NO_NEW_RISK'
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    monkeypatch.setattr('trading_system.server.store.now', lambda: tomorrow)
    quotes = market()
    for quote in quotes['quotes']:
        quote['as_of'] = tomorrow.isoformat()
    with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
        db.order(buy('0.01'), 'next-day', quotes, 90)
    db.order(dict(symbol='BTCUSDT', side='SELL', quantity='0.1', strategy_id=None), 'reduce', quotes, 90)
    assert db.dashboard(quotes)['risk_locks'][0]['state'] == 'ACTIVE'
    db.close()


def test_observation_alone_latches_loss_and_pause_resume_cannot_clear_it(tmp_path):
    db = Store(f"sqlite:///{tmp_path / 'observe.sqlite3'}")
    db.initialize()
    db.setup('owner', 'a-long-password', '100000', 24)
    db.order(buy('0.1'), 'entry', market(), 90)
    db.dashboard(market('10000'))
    db.action(dict(action='pause_new', expected_revision=2), 'pause', market(), 90)
    db.action(dict(action='resume_new', expected_revision=3), 'resume', market(), 90)
    with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
        db.order(buy('0.01'), 'rebound', market(), 90)
    db.close()
