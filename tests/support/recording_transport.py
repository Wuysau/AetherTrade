"""In-memory fictional action recorder. This is never a venue adapter."""
from copy import deepcopy


class RecordingTransport:
    def __init__(self) -> None:
        self.writes: list[dict] = []

    def send(self, payload: dict) -> dict:
        if payload.get("environment") not in {"PAPER", "RESEARCH"}:
            raise ValueError("RecordingTransport requires an explicit fictional environment")
        self.writes.append(deepcopy(payload))
        return {"status": "RECORDED", "index": len(self.writes) - 1, "venue_requests_sent": 0}
