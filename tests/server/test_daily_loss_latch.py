from datetime import datetime, timedelta, timezone

import pytest

from trading_system.server.errors import ApiError
from support.unified_console import provision_console
from server.test_store import market, buy


@pytest.mark.paper_runtime
def test_loss_lock_survives_denial_rebound_restart_and_day_rollover(tmp_path, monkeypatch):
    harness = provision_console(tmp_path)
    db = harness.store
    harness.execute(buy('0.1'), 'entry', market())
    with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
        db.order(buy('0.01'), 'loss', market('10000'), 90)
    db.close()
    db = harness.reopen()
    harness.store = db
    with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
        db.order(buy('0.01'), 'rebound', market('50000'), 90)
    snapshot = db.dashboard(market())
    lock = next(item for item in snapshot['risk_locks'] if item['cause'] == 'DAILY_LOSS')
    assert lock['cause'] == 'DAILY_LOSS' and lock['permission'] == 'NO_NEW_RISK'
    # Reduce against real service/DB time, before advancing only the local risk
    # day clock. The clock change must not expire an already committed cause.
    harness.execute(dict(symbol='BTCUSDT', side='SELL', quantity='0.1', strategy_id=None), 'reduce', market())
    assert db.dashboard(market())['positions'] == []
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    monkeypatch.setattr('trading_system.server.store.now', lambda: tomorrow)
    quotes = market()
    for quote in quotes['quotes']:
        quote['as_of'] = tomorrow.isoformat()
    with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
        db.order(buy('0.01'), 'next-day', quotes, 90)
    assert next(item for item in db.dashboard(quotes)['risk_locks'] if item['lock_id'] == lock['lock_id'])['state'] == 'ACTIVE'
    harness.close()


@pytest.mark.paper_runtime
def test_observation_alone_latches_loss_and_pause_resume_cannot_clear_it(tmp_path):
    harness = provision_console(tmp_path)
    db = harness.store
    harness.execute(buy('0.1'), 'entry', market())
    db.dashboard(market('10000'))
    revision = db.dashboard(market())['account']['revision']
    db.action(dict(action='pause_new', expected_revision=revision), 'pause', market(), 90)
    db.action(dict(action='resume_new', expected_revision=revision + 1), 'resume', market(), 90)
    with pytest.raises(ApiError, match='DAILY_LOSS_LIMIT'):
        db.order(buy('0.01'), 'rebound', market(), 90)
    harness.close()
