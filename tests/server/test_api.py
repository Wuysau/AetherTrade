from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import update

from trading_system.server.app import create_app
from trading_system.server.config import Settings
from trading_system.server.store import sessions


class Feed:
    def snapshot(self):
        return {"source": "test-public", "status": "CONNECTED", "last_updated": datetime.now(timezone.utc).isoformat(), "error": None,
                "quotes": [{"symbol": "BTCUSDT", "price": "50000", "change_pct": "0", "as_of": datetime.now(timezone.utc).isoformat(), "source": "test-public", "stale": False}]}

    async def refresh(self):
        return self.snapshot()


@pytest.fixture
def client(tmp_path, request):
    runtime_options = {'database_url': f"sqlite:///{tmp_path / 'api.sqlite3'}"}
    if request.node.get_closest_marker('paper_runtime'):
        from support.unified_console import provision_console
        runtime_options = provision_console(tmp_path, setup=False).settings_kwargs
    settings = Settings(**runtime_options, market_enabled=False, testing=True,
                        allowed_origins=("http://testserver",), web_dist=tmp_path / "dist")
    app = create_app(settings, quote_provider=Feed())
    with TestClient(app) as test_client:
        test_client.headers["Origin"] = "http://testserver"
        yield test_client


def setup(client):
    response = client.post("/api/setup", json={"username": "owner", "password": "a-long-password", "initial_cash": "100000"})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


def test_setup_session_csrf_logout_replay(client):
    assert client.get("/api/bootstrap").json()["needs_setup"] is True
    assert client.get("/api/dashboard").status_code == 401
    response = setup(client)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/bootstrap").json()["needs_setup"] is False
    assert client.get("/api/session").json()["username"] == "owner"
    old_cookie = client.cookies.get("aethertrade_session")
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/market/refresh").status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post("/api/session/logout").status_code == 200
    client.cookies.set("aethertrade_session", old_cookie)
    assert client.get("/api/session").status_code == 401


@pytest.mark.parametrize("origin", ["http://evil.example", "http://testserver.evil.example", "null", ""])
def test_setup_requires_exact_allowed_origin(client, origin):
    client.headers["Origin"] = origin
    response = client.post("/api/setup", json={"username": "owner", "password": "a-long-password", "initial_cash": "100000"})
    assert response.status_code == 403
    assert client.get("/api/bootstrap").json()["needs_setup"] is True


def test_login_rate_limit_and_expiry(client):
    setup(client)
    client.post("/api/session/logout")
    responses = [client.post("/api/session/login", json={"username": "owner", "password": "wrong-password"}) for _ in range(12)]
    assert responses[-1].status_code == 429
    assert all("wrong-password" not in result.text for result in responses)


def test_expired_session_rejected(client):
    setup(client)
    with client.app.state.store.engine.begin() as connection:
        connection.execute(update(sessions).values(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()))
    assert client.get("/api/session").status_code == 401


@pytest.mark.parametrize("quantity", ["NaN", "Infinity", "1e6", "-1", "0", 0.1])
def test_invalid_money_has_safe_error(client, quantity):
    setup(client)
    response = client.post("/api/paper/orders", json={"symbol": "BTCUSDT", "side": "BUY", "quantity": quantity}, headers={"Idempotency-Key": "invalid-money"})
    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert client.get("/api/dashboard").json()["orders"] == []


@pytest.mark.paper_runtime
def test_real_data_roundtrip_and_export(client):
    setup(client)
    response = client.post("/api/strategies", json={"name": "BTC 观察", "symbol": "BTCUSDT", "mode": "RULE", "budget": "5000", "target_pct": "10"})
    assert response.status_code == 200, response.text
    strategy = response.json()
    order = {"symbol": "BTCUSDT", "side": "BUY", "quantity": "0.01", "strategy_id": strategy["id"]}
    response = client.post("/api/paper/orders", json=order, headers={"Idempotency-Key": "order-one"})
    assert response.status_code == 200, response.text
    data = client.get("/api/dashboard?range=7D").json()
    assert data['account']['cash'] == '100000'
    assert data['research']['trade_count'] == 0
    assert data['orders'][0]['status'] == 'PENDING' and data['positions'] == []
    command_id = response.json()['order']['command_id']
    client.app.state.store.execution.dispatch(command_id)
    client.app.state.store.execution.inject(command_id, dict(event_id='api-explicit-fill', status='FILLED',
        cumulative_quantity='0.01', price='50000'))
    data = client.get("/api/dashboard?range=7D").json()
    assert data["account"]["cash"] == "99499.5"
    assert data["research"]["trade_count"] == 1
    assert data["decisions"] == []
    assert data["positions"][0]["protection"] == "GAP"
    report = client.get("/api/research/export")
    assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
    assert "PAPER" in report.text and "BTCUSDT" in report.text


def test_static_files_and_unknown_api_are_not_spa_fallback(client):
    web = client.app.state.settings.web_dist
    web.mkdir()
    (web / "index.html").write_text("<html>real build</html>", encoding="utf-8")
    assert client.get("/").status_code == 200
    assert client.get("/positions").text == "<html>real build</html>"
    assert client.get("/api/no-such-endpoint").status_code == 404
    assert client.get("/%2e%2e%2fpyproject.toml").status_code == 404


def test_provider_failure_flags_cached_prices_and_prevents_execution(client):
    setup(client)
    async def failed():
        raise RuntimeError("secret provider detail must not escape")
    client.app.state.market.refresh = failed
    response = client.post("/api/market/refresh")
    assert response.status_code == 200
    assert response.json()["status"] == "STALE"
    assert "secret provider" not in response.text
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["market"]["quotes"][0]["stale"] is True
    response = client.post("/api/paper/orders", json={"symbol": "BTCUSDT", "side": "BUY", "quantity": "0.01"}, headers={"Idempotency-Key": "stale-after-failure"})
    assert response.status_code == 409


def test_oversized_body_and_missing_origin_are_rejected(client):
    response = client.post("/api/setup", content="x" * 20_000, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    client.headers.pop("Origin")
    response = client.post("/api/setup", json={"username": "owner", "password": "a-long-password", "initial_cash": "100000"})
    assert response.status_code == 403


def test_login_works_after_logout_and_api_errors_hide_password(client):
    setup(client)
    client.post("/api/session/logout")
    response = client.post("/api/session/login", json={"username": "owner", "password": "a-long-password"})
    assert response.status_code == 200
    assert "token" not in response.json()
    response = client.post("/api/session/login", json={"username": "owner", "password": "a-long-password", "extra": "secret"})
    assert response.status_code == 422 and "a-long-password" not in response.text
