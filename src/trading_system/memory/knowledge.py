"""Knowledge governance: reviewed experience, never model-declared permanent rules."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from trading_system.contracts.canonical import utc_timestamp

from .contracts import KnowledgeStatus, StrategyMemoryScope, ValidatedKnowledge


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeGovernor:
    def __init__(self, memory, *, minimum_sample_size: int = 10):
        if type(minimum_sample_size) is not int or minimum_sample_size < 1:
            raise ValueError("MEMORY_KNOWLEDGE_SAMPLE_THRESHOLD_INVALID")
        self.memory = memory
        self.minimum_sample_size = minimum_sample_size

    def propose(self, scope: StrategyMemoryScope, statement: str, *, evidence_refs,
                market: str = "", product: str = "", instrument: str = "",
                timeframe: str = "", strategy_version: int = 1,
                evaluation_window: dict | None = None, proposed_at: str | None = None,
                expires_at: str | None = None, knowledge_id: str | None = None) -> ValidatedKnowledge:
        if not evidence_refs:
            raise ValueError("MEMORY_KNOWLEDGE_EVIDENCE_REQUIRED")
        item = ValidatedKnowledge(
            knowledge_id=knowledge_id or "knowledge:" + uuid4().hex,
            scope=scope, statement=statement, status=KnowledgeStatus.PROPOSED,
            market=market, product=product or scope.product, instrument=instrument or scope.instrument,
            timeframe=timeframe, strategy_version=strategy_version,
            sample_size=0, evaluation_window=evaluation_window or {},
            evidence_refs=tuple(evidence_refs), proposed_at=proposed_at or _now(),
            expires_at=expires_at, version=1, provenance="MODEL_PROPOSAL")
        self.memory.propose_knowledge(item)
        return item

    def validate(self, item: ValidatedKnowledge, *, provenance: str, sample_size: int,
                 evidence_refs, validated_at: str | None = None) -> ValidatedKnowledge:
        if provenance not in ("DETERMINISTIC_STATISTICS", "USER_REVIEW"):
            raise ValueError("MEMORY_KNOWLEDGE_VALIDATION_PROVENANCE_REQUIRED")
        if sample_size < self.minimum_sample_size:
            raise ValueError("MEMORY_KNOWLEDGE_SAMPLE_TOO_SMALL")
        if not evidence_refs:
            raise ValueError("MEMORY_KNOWLEDGE_VALIDATION_EVIDENCE_REQUIRED")
        self.memory.validate_knowledge(item, provenance=provenance, sample_size=sample_size,
                                       evidence_refs=tuple(evidence_refs))
        state = self.memory.get_state(item.scope)
        return next(entry for entry in state.knowledge if entry.knowledge_id == item.knowledge_id)

    def expire(self, item: ValidatedKnowledge) -> None:
        from .contracts import EventType
        self.memory.append_event(
            item.scope, EventType.KNOWLEDGE_EXPIRED, {"knowledge_id": item.knowledge_id},
            source="CONTROL",
            idempotency_key=f"knowledge-expired:{item.scope.scope_id}:{item.knowledge_id}:{item.version}")

    def relevant(self, scope: StrategyMemoryScope, *, as_of: str | None = None) -> tuple[ValidatedKnowledge, ...]:
        at = as_of or _now()
        result = []
        for item in self.memory.repository.list_knowledge(scope, limit=200):
            if item.status != KnowledgeStatus.VALIDATED:
                continue
            if item.expires_at:
                try:
                    if utc_timestamp(item.expires_at) <= utc_timestamp(at):
                        continue
                except (ValueError, TypeError):
                    continue
            if item.product and item.product != scope.product:
                continue
            if item.instrument and item.instrument != scope.instrument:
                continue
            result.append(item)
        return tuple(result)
