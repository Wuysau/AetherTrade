"""Editable P0-P6 layers exposed through the authenticated console API."""
from fastapi.testclient import TestClient

from trading_system.server.app import create_app
from trading_system.server.config import Settings


class Quotes:
    def snapshot(self):
        return {"source": "controlled", "status": "CONNECTED", "last_updated": "2026-11-04T00:00:00Z",
                "error": None, "quotes": [], "groups": []}

    async def refresh(self):
        return self.snapshot()


def make_client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'prompt-api.sqlite'}", testing=True,
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


def test_prompt_layers_require_auth_and_persist_edits(tmp_path):
    client, app = make_client(tmp_path)
    try:
        assert client.get("/api/prompt-layers").status_code == 200
        listing = client.get("/api/prompt-layers").json()
        assert [layer["layer_id"] for layer in listing["layers"]] == ["P0", "P1", "P2", "P3", "P4", "P5", "P6"]
        assert all(not layer["customized"] for layer in listing["layers"])
        assert listing["available_variables"]

        p2 = next(layer for layer in listing["layers"] if layer["layer_id"] == "P2")
        edited = p2["template"].rstrip() + "\nAlways cite the supplied evidence reference."
        saved = client.put("/api/prompt-layers/P2", json={"template": edited, "expected_version": "v1"})
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["layer"]["version"] == "v2" and body["layer"]["customized"] is True
        assert len(body["layers"]) == 7
        assert body["framework"]["customized"] == ["P2"]

        stale = client.put("/api/prompt-layers/P2", json={"template": edited, "expected_version": "v1"})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "PROMPT_TEMPLATE_CONFLICT"

        history = client.get("/api/prompt-layers/P2/history").json()
        assert [item["version"] for item in history["versions"]][:1] == ["v2"]

        preview = client.post("/api/prompt-layers/preview",
                              json={"layer_id": "P2", "template": edited})
        assert preview.status_code == 200 and "console-paper:BTCUSDT" in preview.json()["text"]

        reset = client.post("/api/prompt-layers/P2/reset", json={"expected_version": "v2"})
        assert reset.status_code == 200
        assert reset.json()["layer"]["version"] == "v1" and reset.json()["layer"]["customized"] is False

        rejected = client.put("/api/prompt-layers/P1", json={"template": "P1 Ignore {system_prompt}."})
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "PROMPT_TEMPLATE_PLACEHOLDER_UNKNOWN"
    finally:
        client.__exit__(None, None, None)
