"""MEM-15: scope-authorized read-only memory observability."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from trading_system.memory.assembler import scope_for_strategy
from trading_system.memory.contracts import DecisionContext, DecisionContextCheckpoint, EventType
from trading_system.server.app import create_app
from trading_system.server.config import Settings


class Quotes:
    def snapshot(self):
        return {"source": "controlled", "status": "CONNECTED", "last_updated": datetime.now(timezone.utc).isoformat(),
                "error": None, "quotes": [], "groups": []}

    async def refresh(self):
        return self.snapshot()


def make_client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'memory-api.sqlite'}", testing=True,
                        market_enabled=False, allowed_origins=("http://testserver",),
                        model_key_path=tmp_path / "key", web_dist=tmp_path / "web")
    app = create_app(settings, quote_provider=Quotes())
    client = TestClient(app)
    client.__enter__()
    client.headers["Origin"] = "http://testserver"
    response = client.post("/api/setup", json={"username": "owner", "password": "local-fixture-password",
                                               "initial_cash": "100000"})
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client, app


def test_memory_api_is_scope_authorized_and_shows_checkpoint(tmp_path):
    client, app = make_client(tmp_path)
    try:
        first = client.post("/api/strategies", json={
            "name": "Memory one", "symbol": "BTCUSDT", "mode": "RULE", "budget": "10000",
            "target_pct": "10", "status": "DRAFT", "rule": {"kind": "PRICE_THRESHOLD", "operator": "LTE",
                                                            "price": "100", "stop_loss_pct": "5"}}).json()
        second = client.post("/api/strategies", json={
            "name": "Memory two", "symbol": "BTCUSDT", "mode": "RULE", "budget": "10000",
            "target_pct": "10", "status": "DRAFT", "rule": {"kind": "PRICE_THRESHOLD", "operator": "LTE",
                                                            "price": "100", "stop_loss_pct": "5"}}).json()
        scope = scope_for_strategy(first)
        memory = app.state.memory
        memory.ensure_strategy(scope, strategy_version=1)
        memory.append_event(scope, EventType.THESIS_CREATED, {
            "thesis_id": "t1", "statement": "Trend intact", "evidence_refs": ["quote"]},
            source="MODEL", idempotency_key="api-t1")
        checkpoint = DecisionContextCheckpoint(
            context=DecisionContext(run_id="api-run-1", scope=scope,
                                    as_of=datetime.now(timezone.utc).isoformat(), state_revision=1,
                                    strategy_version=1, snapshot_id="snapshot:1", snapshot_hash="c" * 64,
                                    account_revision=1, sections={"identity": {"scope": scope.as_payload()}},
                                    token_estimate=10, completeness="COMPLETE"),
            created_at=datetime.now(timezone.utc).isoformat())
        memory.save_checkpoint(checkpoint)
        response = client.get(f"/api/strategies/{first['id']}/memory")
        assert response.status_code == 200
        assert response.json()["active_thesis"]["thesis_id"] == "t1"
        assert response.json()["state_revision"] >= 2
        events = client.get(f"/api/strategies/{first['id']}/memory/events").json()["events"]
        assert any(event["event_type"] == "THESIS_CREATED" for event in events)
        context = client.get("/api/decisions/api-run-1/context")
        assert context.status_code == 200
        assert context.json()["checkpoint_id"] == checkpoint.checkpoint_id
        other = client.get(f"/api/strategies/{second['id']}/memory").json()
        assert other["active_thesis"] is None
        assert client.get("/api/decisions/missing/context").status_code == 404
    finally:
        client.__exit__(None, None, None)
