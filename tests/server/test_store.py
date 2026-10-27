from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_system.server.errors import ApiError
from trading_system.server.store import Store
from support.unified_console import provision_console


def market(price="50000", *, stale=False):
    return {"source": "test-public", "status": "CONNECTED", "last_updated": datetime.now(timezone.utc).isoformat(),
            "error": None, "quotes": [
                {"symbol": symbol, "price": value, "change_pct": "0", "as_of": datetime.now(timezone.utc).isoformat(),
                 "source": "test-public", "stale": stale}
                for symbol, value in [("BTCUSDT", price), ("ETHUSDT", "2000"), ("SOLUSDT", "100")]]}


@pytest.fixture
def console_harness(tmp_path):
    harness = provision_console(tmp_path)
    yield harness
    harness.close()


@pytest.fixture
def store(tmp_path, request):
    if request.node.get_closest_marker('paper_runtime'):
        yield request.getfixturevalue('console_harness').store
        return
    db = Store(f"sqlite:///{tmp_path / 'runtime.sqlite3'}")
    db.initialize()
    db.setup("owner", "a-long-password", "100000", 24)
    yield db
    db.close()


def buy(quantity="0.1", symbol="BTCUSDT"):
    return {"symbol": symbol, "side": "BUY", "quantity": quantity, "strategy_id": None}


def test_atomic_first_owner_and_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'race.sqlite3'}"
    stores = [Store(url), Store(url)]
    for db in stores:
        db.initialize()

    def setup(db):
        try:
            return db.setup("owner", "a-long-password", "12345.67", 24)
        except ApiError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(setup, stores))
    assert results.count("ALREADY_CONFIGURED") == 1
    for db in stores:
        db.close()
    restored = Store(url)
    restored.initialize()
    data = restored.dashboard(market(), "24H")
    assert data["account"]["cash"] == "12345.67"
    assert data["orders"] == data["positions"] == data["decisions"] == []
    assert len(data["history"]) == 1
    restored.close()


@pytest.mark.paper_runtime
def test_decimal_order_and_idempotency_survive_restart(store, console_harness):
    first = console_harness.execute(buy(), "order-1", market())
    assert first["order"]["notional"] == "5000"
    assert first["order"]["fee"] == "5"
    assert store.dashboard(market(), "24H")["account"]["cash"] == "94995"
    assert store.order(buy(), "order-1", market(stale=True), 90) == first['receipt']
    with pytest.raises(ApiError, match="IDEMPOTENCY_CONFLICT"):
        store.order(buy("0.2"), "order-1", market(), 90)
    restored = console_harness.reopen()
    assert restored.order(buy(), "order-1", market(), 90) == first['receipt']
    restored.close()


@pytest.mark.parametrize("price", ["NaN", "Infinity", "0", "-1", "1e5"])
def test_rejects_invalid_quotes_without_state_change(store, price):
    with pytest.raises(ApiError):
        store.order(buy(), "bad-quote", market(price), 90)
    assert store.dashboard(market(), "24H")["orders"] == []


def test_stale_future_missing_quotes_rejected(store):
    for quotes in [market(stale=True), {**market(), "quotes": []}, market()]:
        if quotes["quotes"] and not quotes["quotes"][0]["stale"]:
            quotes["quotes"][0]["as_of"] = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        with pytest.raises(ApiError):
            store.order(buy(), "unusable", quotes, 90)


def test_risk_pause_and_unconfigured_execution_guards(store):
    with pytest.raises(ApiError, match="POSITION_LIMIT"):
        store.order(buy("1"), "too-large", market(), 90)
    rev = store.dashboard(market(), "24H")["account"]["revision"]
    store.action({"action": "pause_new", "expected_revision": rev}, "pause", market(), 90)
    with pytest.raises(ApiError, match="ENTRIES_PAUSED"):
        store.order(buy(), "paused", market(), 90)
    # The actual no-short check is retained on real unified PostgreSQL in
    # test_real_unified_console_retains_pause_and_no_short_checks.
    with pytest.raises(ApiError, match="UNIFIED_PAPER_REQUIRED"):
        store.order({**buy(), "side": "SELL"}, "short", market(), 90)


@pytest.mark.paper_runtime
def test_exit_is_all_or_nothing_and_confirmed(store, console_harness):
    console_harness.execute(buy(), "btc", market())
    console_harness.execute(buy("1", "ETHUSDT"), "eth", market())
    before = store.dashboard(market(), "24H")
    action = {"action": "exit_owned", "expected_revision": before["account"]["revision"], "confirmation": "EXIT PAPER"}
    missing = market()
    missing["quotes"] = missing["quotes"][:1]
    with pytest.raises(ApiError):
        store.action(action, "exit", missing, 90)
    after = store.dashboard(market(), "24H")
    assert after["orders"] == before["orders"]
    assert after["account"]["cash"] == before["account"]["cash"]
    with pytest.raises(ApiError, match="CONFIRMATION_REQUIRED"):
        store.action({**action, "confirmation": None}, "exit", market(), 90)
    receipt = store.action(action, "exit", market(), 90)
    assert receipt["affected_count"] == 2
    # Control acknowledgement queues durable exits; only explicit controller
    # fills can change the real unified positions.
    assert len(store.dashboard(market(), "24H")["positions"]) == 2
    assert len(console_harness.fill_pending(market())) == 2
    assert store.dashboard(market(), "24H")["positions"] == []
    assert store.action(action, "exit", market(stale=True), 90) == receipt


def test_strategy_and_risk_revision_conflicts(store):
    created = store.create_strategy({"name": "观察 BTC", "symbol": "BTCUSDT", "mode": "AI", "budget": "10000", "target_pct": "10"})
    updated = store.update_strategy(created["id"], {"name": "继续观察", "expected_revision": created["revision"]})
    assert updated["revision"] == created["revision"] + 1
    with pytest.raises(ApiError, match="REVISION_CONFLICT"):
        store.update_strategy(created["id"], {"name": "旧修改", "expected_revision": created["revision"]})
    risk = store.update_risk({"max_position_pct": "40", "max_daily_loss": "1000", "expected_revision": 1})
    assert risk["revision"] == 2
    with pytest.raises(ApiError, match="REVISION_CONFLICT"):
        store.update_risk({"max_position_pct": "50", "max_daily_loss": "2000", "expected_revision": 1})


@pytest.mark.paper_runtime
def test_strategy_symbol_change_requires_exiting_attributed_holdings(store, console_harness):
    strategy = store.create_strategy({"name": "观察 BTC", "symbol": "BTCUSDT", "mode": "AI", "budget": "10000", "target_pct": "10"})
    entry = {**buy(), "strategy_id": strategy["id"]}
    console_harness.execute(entry, "strategy-buy", market())
    before = store.dashboard(market(), "24H")
    with pytest.raises(ApiError, match="STRATEGY_HAS_POSITION") as rejected:
        store.update_strategy(strategy["id"], {"symbol": "ETHUSDT", "expected_revision": strategy["revision"]})
    assert rejected.value.status == 409
    assert "请先退出该策略持仓，再修改交易标的" in rejected.value.message
    after = store.dashboard(market(), "24H")
    assert after["strategies"] == before["strategies"]
    assert after["positions"] == before["positions"]
    assert after["orders"] == before["orders"]
    assert after["account"] == before["account"]
    edited = store.update_strategy(strategy["id"], {"name": "继续观察 BTC", "symbol": "BTCUSDT", "budget": "12000", "mode": "HYBRID", "expected_revision": strategy["revision"]})
    assert (edited["name"], edited["budget"], edited["mode"]) == ("继续观察 BTC", "12000", "HYBRID")
    sell = console_harness.execute({**entry, "side": "SELL"}, "strategy-sell", market())
    assert sell["order"]["strategy_id"] == strategy["id"]
    assert store.dashboard(market(), "24H")["positions"] == []
    moved = store.update_strategy(strategy["id"], {"symbol": "ETHUSDT", "expected_revision": edited["revision"]})
    assert moved["symbol"] == "ETHUSDT"
    assert moved["revision"] == edited["revision"] + 1


@pytest.mark.paper_runtime
def test_concurrent_duplicate_order_charges_once(store, console_harness):
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: store.order(buy(), "same-request", market(), 90), range(4)))
    assert all(reply == replies[0] for reply in replies)
    assert store.dashboard(market(), "24H")['account']['cash'] == '100000'
    console_harness.fill(replies[0], market())
    assert len(store.dashboard(market(), "24H")["orders"]) == 1
    assert store.dashboard(market(), "24H")['account']['cash'] == '94995'


def test_daily_loss_limit_includes_prospective_fee(store):
    store.update_risk({"max_position_pct": "100", "max_daily_loss": "1", "expected_revision": 1})
    with pytest.raises(ApiError, match="DAILY_LOSS_LIMIT"):
        store.order(buy("0.1"), "fee-over-limit", market(), 90)
    assert store.dashboard(market(), "24H")["account"]["cash"] == "100000"


@pytest.mark.paper_runtime
def test_daily_loss_and_position_limits_apply_to_current_prices(store, console_harness):
    console_harness.execute(buy("0.1"), "initial", market())
    with pytest.raises(ApiError, match="DAILY_LOSS_LIMIT"):
        store.order(buy("0.01"), "falling", market("20000"), 90)


@pytest.mark.paper_runtime
def test_sell_partial_and_full_preserve_exact_cash(store, console_harness):
    console_harness.execute(buy("0.1"), "buy", market())
    console_harness.execute({**buy("0.04"), "side": "SELL"}, "sell-part", market("60000"))
    partial = store.dashboard(market("60000"), "24H")
    assert partial["positions"][0]["quantity"] == "0.06"
    assert partial["account"]["cash"] == "97392.6"
    console_harness.execute({**buy("0.06"), "side": "SELL"}, "sell-rest", market("60000"))
    final = store.dashboard(market(), "24H")
    assert final["positions"] == []
    assert final["account"]["cash"] == "100989"
    assert final["research"]["fees"] == "11"


@pytest.mark.paper_runtime
def test_failed_execution_rolls_back_cash_positions_order_receipt(store, monkeypatch, console_harness):
    original = store._event
    def fail_after_fill(*args, **kwargs):
        raise RuntimeError("simulated persistence failure")
    monkeypatch.setattr(store, "_event", fail_after_fill)
    with pytest.raises(RuntimeError):
        store.order(buy(), "retry-after-rollback", market(), 90)
    unchanged = store.dashboard(market(), "24H")
    assert unchanged["account"]["cash"] == "100000"
    assert unchanged['positions'] == []
    assert all(order['status'] == 'PENDING' and order['filled_quantity'] == '0' for order in unchanged['orders'])
    # The domain transaction already committed before console persistence failed.
    # Keep that recoverable PENDING identity; never fabricate a rolled-back fill.
    from sqlalchemy import select, func
    from trading_system.server.store import orders, receipts
    with store.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(orders)).scalar_one() == 0
        assert connection.execute(select(func.count()).select_from(receipts)).scalar_one() == 0
    monkeypatch.setattr(store, "_event", original)
    retried = console_harness.execute(buy(), "retry-after-rollback", market())
    assert retried['order']['fee'] == '5'
    assert len(store.execution.projection()['commands']) == 1


@pytest.mark.paper_runtime
def test_empty_cancel_and_exit_receipts_disclose_no_effect(store):
    for index, action in enumerate(["cancel_entries", "exit_owned"]):
        receipt = store.action({"action": action, "expected_revision": 1, "confirmation": "EXIT PAPER"}, f"empty-{index}", market(), 90)
        assert receipt["affected_count"] == 0
        assert '0' in receipt['detail']
    assert store.dashboard(market(), "24H")["orders"] == []


def test_password_and_session_secrets_are_hashed_in_storage(store):
    from trading_system.server.store import owners, sessions
    from sqlalchemy import select
    with store.engine.connect() as connection:
        password = connection.execute(select(owners.c.password_hash)).scalar_one()
        token = connection.execute(select(sessions.c.token_hash)).scalar_one()
    assert password.startswith("pbkdf2_sha256$600000$")
    assert "a-long-password" not in password
    assert len(token) == 64


@pytest.mark.paper_runtime
def test_concurrent_orders_enforce_combined_exposure(store):
    def submit(index):
        try:
            return store.order(buy("0.4"), f"concurrent-{index}", market(), 90)
        except ApiError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, range(2)))
    assert sum(isinstance(result, str) for result in outcomes) == 1
    assert all(result in ('POSITION_LIMIT', 'PAPER_RISK_REJECTED') for result in outcomes if isinstance(result, str))
    assert len(store.dashboard(market(), "24H")["orders"]) == 1


@pytest.mark.paper_runtime
def test_win_rate_counts_both_entry_and_exit_fees(store, console_harness):
    console_harness.execute(buy("0.1"), "entry", market())
    console_harness.execute({**buy("0.1"), "side": "SELL"}, "exit", market("50075"))
    result = store.dashboard(market(), "24H")
    assert result["research"]["win_rate"] == "0"
    assert Decimal(result["account"]["cash"]) < Decimal("100000")


def test_missing_malformed_quote_container_is_a_domain_rejection(store):
    for payload in [{}, {"quotes": None}, {"quotes": [None]}]:
        with pytest.raises(ApiError, match="QUOTE_UNAVAILABLE"):
            store.order(buy(), "malformed", payload, 90)
