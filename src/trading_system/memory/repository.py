"""SQLAlchemy persistence for strategy memory (console database).

The tables are registered on ``trading_system.server.store.metadata`` so existing
SQLite/PAPER setups get them through ``Store.initialize``. A matching PostgreSQL
migration exists at ``migrations/0032_strategy_memory.sql``. Events are append-only;
the state row is a rebuildable projection, never the authority.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (BigInteger, Column, Index, Integer, JSON, String, Table, UniqueConstraint,
                        func, insert, select, update)

from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.server.store import metadata

from .contracts import (
    DecisionContextCheckpoint, EventType, StrategyMemoryEvent, StrategyMemoryScope,
    StrategyState, ValidatedKnowledge, checkpoint_from_payload, decision_ref_from_payload,
    knowledge_from_payload, state_from_payload,
)
from .reducer import MemoryRevisionConflict, RebuildReport, StrategyStateReducer


events = Table(
    "console_strategy_memory_events", metadata,
    Column("scope_id", String(64), primary_key=True),
    Column("sequence", BigInteger, primary_key=True),
    Column("event_id", String(128), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("source", String(32), nullable=False),
    Column("occurred_at", String(40), nullable=False),
    Column("recorded_at", String(40), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("state_revision", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("event_hash", String(64), nullable=False),
    Column("schema_revision", String(64), nullable=False),
    UniqueConstraint("scope_id", "event_id", name="uq_strategy_memory_event_id"),
    UniqueConstraint("scope_id", "idempotency_key", name="uq_strategy_memory_idempotency"),
    Index("ix_strategy_memory_event_time", "scope_id", "recorded_at"),
    Index("ix_strategy_memory_event_type", "scope_id", "event_type"),
)

state = Table(
    "console_strategy_memory_state", metadata,
    Column("scope_id", String(64), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("state_hash", String(64), nullable=False),
    Column("data", JSON, nullable=False),
    Column("updated_at", String(40), nullable=False),
)

theses = Table(
    "console_market_theses", metadata,
    Column("scope_id", String(64), primary_key=True),
    Column("thesis_id", String(128), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("data", JSON, nullable=False),
    Column("updated_at", String(40), nullable=False),
    Index("ix_market_theses_status", "scope_id", "status"),
)

plans = Table(
    "console_strategy_plans", metadata,
    Column("scope_id", String(64), primary_key=True),
    Column("plan_id", String(128), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("data", JSON, nullable=False),
    Column("updated_at", String(40), nullable=False),
    Index("ix_strategy_plans_status", "scope_id", "status"),
)

checkpoints = Table(
    "console_decision_checkpoints", metadata,
    Column("checkpoint_id", String(80), primary_key=True),
    Column("run_id", String(128), nullable=False),
    Column("scope_id", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("completeness", String(32), nullable=False),
    Column("data", JSON, nullable=False),
    Index("ix_decision_checkpoint_scope_time", "scope_id", "created_at"),
    Index("ix_decision_checkpoint_run", "run_id"),
)

knowledge = Table(
    "console_memory_knowledge", metadata,
    Column("scope_id", String(64), primary_key=True),
    Column("knowledge_id", String(128), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("data", JSON, nullable=False),
    Column("updated_at", String(40), nullable=False),
    Index("ix_memory_knowledge_status", "scope_id", "status"),
)

prompt_templates = Table(
    "console_prompt_templates", metadata,
    Column("template_id", String(64), primary_key=True),
    Column("version", String(64), primary_key=True),
    Column("role", String(32), nullable=False),
    Column("template_hash", String(64), nullable=False),
    Column("data", JSON, nullable=False),
    Column("active", Integer, nullable=False, default=1),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class EventReceipt:
    event_id: str
    scope_id: str
    sequence: int
    state_revision: int
    event_hash: str
    duplicate: bool = False


class MemoryRepository:
    def __init__(self, store, *, reducer: StrategyStateReducer | None = None):
        self.store = store
        self.reducer = reducer or StrategyStateReducer()

    def _load_state(self, connection, scope: StrategyMemoryScope) -> StrategyState:
        row = connection.execute(select(state.c.data).where(state.c.scope_id == scope.scope_id)).scalar_one_or_none()
        if row is None:
            return self.reducer.initial(scope)
        loaded = state_from_payload(row)
        if loaded.scope != scope:
            raise ValueError("MEMORY_STATE_SCOPE_MISMATCH")
        return loaded

    def load_state(self, scope: StrategyMemoryScope) -> StrategyState | None:
        with self.store.transaction() as connection:
            row = connection.execute(select(state.c.data).where(state.c.scope_id == scope.scope_id)).scalar_one_or_none()
        return None if row is None else state_from_payload(row)

    def append_event(self, scope: StrategyMemoryScope, event: StrategyMemoryEvent,
                     idempotency_key: str | None = None) -> EventReceipt:
        if type(event) is not StrategyMemoryEvent or event.scope != scope:
            raise ValueError("MEMORY_EVENT_SCOPE_MISMATCH")
        key = idempotency_key or event.idempotency_key
        if key != event.idempotency_key:
            raise ValueError("MEMORY_IDEMPOTENCY_KEY_MISMATCH")
        with self.store.transaction() as connection:
            existing = connection.execute(
                select(events.c.event_id, events.c.sequence, events.c.state_revision,
                       events.c.event_hash, events.c.payload, events.c.event_type, events.c.source)
                .where(events.c.scope_id == scope.scope_id, events.c.idempotency_key == key)
            ).mappings().first()
            if existing is not None:
                same = (existing["event_type"] == event.event_type.value
                        and existing["source"] == event.source
                        and canonical_hash(existing["payload"]) == event.payload_hash)
                if not same:
                    raise ValueError("MEMORY_IDEMPOTENCY_CONFLICT")
                return EventReceipt(event_id=existing["event_id"], scope_id=scope.scope_id,
                                    sequence=existing["sequence"], state_revision=existing["state_revision"],
                                    event_hash=existing["event_hash"], duplicate=True)
            current = self._load_state(connection, scope)
            if event.state_revision != current.state_revision:
                raise MemoryRevisionConflict("MEMORY_STATE_REVISION_CONFLICT")
            sequence = (connection.execute(
                select(func.max(events.c.sequence)).where(events.c.scope_id == scope.scope_id)
            ).scalar_one() or 0) + 1
            stored = StrategyMemoryEvent(**{
                **{field: getattr(event, field) for field in (
                    "event_id", "scope", "event_type", "source", "occurred_at", "recorded_at",
                    "idempotency_key", "state_revision", "payload", "schema_revision")},
                "sequence": sequence,
            })
            projected = self.reducer.apply(current, stored)
            connection.execute(insert(events).values(
                scope_id=scope.scope_id, sequence=sequence, event_id=stored.event_id,
                event_type=stored.event_type.value, source=stored.source,
                occurred_at=stored.occurred_at, recorded_at=stored.recorded_at,
                idempotency_key=stored.idempotency_key, state_revision=stored.state_revision,
                payload=stored.payload, event_hash=stored.event_hash,
                schema_revision=stored.schema_revision))
            self._write_projection(connection, projected)
            return EventReceipt(event_id=stored.event_id, scope_id=scope.scope_id, sequence=sequence,
                                state_revision=projected.state_revision, event_hash=stored.event_hash)

    def _write_projection(self, connection, projected: StrategyState) -> None:
        payload = domain_payload(projected)
        existing = connection.execute(select(state.c.revision).where(state.c.scope_id == projected.scope.scope_id)).first()
        if existing is None:
            connection.execute(insert(state).values(
                scope_id=projected.scope.scope_id, revision=projected.state_revision,
                state_hash=projected.state_hash, data=payload, updated_at=projected.updated_at))
        else:
            connection.execute(update(state).where(state.c.scope_id == projected.scope.scope_id).values(
                revision=projected.state_revision, state_hash=projected.state_hash,
                data=payload, updated_at=projected.updated_at))
        for thesis in projected.theses:
            self._upsert(connection, theses, {"scope_id": thesis.scope.scope_id, "thesis_id": thesis.thesis_id},
                         dict(revision=thesis.revision, status=thesis.status.value,
                              data=domain_payload(thesis), updated_at=thesis.updated_at))
        for plan in projected.plans:
            self._upsert(connection, plans, {"scope_id": plan.scope.scope_id, "plan_id": plan.plan_id},
                         dict(revision=plan.revision, status=plan.status.value,
                              data=domain_payload(plan), updated_at=plan.updated_at))
        for item in projected.knowledge:
            self._upsert(connection, knowledge, {"scope_id": item.scope.scope_id, "knowledge_id": item.knowledge_id},
                         dict(version=item.version, status=item.status.value,
                              data=domain_payload(item), updated_at=item.validated_at or item.proposed_at))

    @staticmethod
    def _upsert(connection, table, keys: dict, values: dict) -> None:
        where = [getattr(table.c, key) == value for key, value in keys.items()]
        if connection.execute(select(table.c[next(iter(keys))]).where(*where)).first() is None:
            connection.execute(insert(table).values(**keys, **values))
        else:
            connection.execute(update(table).where(*where).values(**values))

    def list_events_since(self, scope: StrategyMemoryScope, cursor: int = 0) -> tuple[StrategyMemoryEvent, ...]:
        with self.store.transaction() as connection:
            rows = connection.execute(
                select(events.c.payload, events.c.sequence, events.c.event_id, events.c.event_type,
                       events.c.source, events.c.occurred_at, events.c.recorded_at,
                       events.c.idempotency_key, events.c.state_revision, events.c.schema_revision)
                .where(events.c.scope_id == scope.scope_id, events.c.sequence > cursor)
                .order_by(events.c.sequence)
            ).mappings().all()
        return tuple(StrategyMemoryEvent(
            event_id=row["event_id"], scope=scope, event_type=row["event_type"], source=row["source"],
            occurred_at=row["occurred_at"], recorded_at=row["recorded_at"],
            idempotency_key=row["idempotency_key"], state_revision=row["state_revision"],
            payload=row["payload"], schema_revision=row["schema_revision"], sequence=row["sequence"],
        ) for row in rows)

    def rebuild_projection(self, scope: StrategyMemoryScope, from_event_id: str | None = None) -> RebuildReport:
        with self.store.transaction() as connection:
            rows = connection.execute(
                select(events.c.payload, events.c.sequence, events.c.event_id, events.c.event_type,
                       events.c.source, events.c.occurred_at, events.c.recorded_at,
                       events.c.idempotency_key, events.c.state_revision, events.c.schema_revision)
                .where(events.c.scope_id == scope.scope_id).order_by(events.c.sequence)
            ).mappings().all()
            stored = connection.execute(select(state.c.data, state.c.state_hash, state.c.revision)
                                        .where(state.c.scope_id == scope.scope_id)).first()
        typed = tuple(StrategyMemoryEvent(
            event_id=row["event_id"], scope=scope, event_type=row["event_type"], source=row["source"],
            occurred_at=row["occurred_at"], recorded_at=row["recorded_at"],
            idempotency_key=row["idempotency_key"], state_revision=row["state_revision"],
            payload=row["payload"], schema_revision=row["schema_revision"], sequence=row["sequence"],
        ) for row in rows)
        report = self.reducer.rebuild(scope, typed)
        if stored is not None and (stored[1] != report.state.state_hash
                                   or stored[0].get("state_hash") != report.state.state_hash
                                   or stored[0].get("state_revision") != report.state.state_revision
                                   or stored[2] != report.state.state_revision):
            with self.store.transaction() as connection:
                self._write_projection(connection, report.state)
            return RebuildReport(scope=scope, events_applied=report.events_applied,
                                 state=report.state, verified=False, reason="PROJECTION_REPAIRED")
        return report

    def save_checkpoint(self, checkpoint: DecisionContextCheckpoint) -> DecisionContextCheckpoint:
        with self.store.transaction() as connection:
            existing = connection.execute(select(checkpoints.c.data).where(
                checkpoints.c.checkpoint_id == checkpoint.checkpoint_id)).scalar_one_or_none()
            if existing is not None:
                if canonical_hash(existing) != canonical_hash(checkpoint.as_payload()):
                    raise ValueError("MEMORY_CHECKPOINT_CONFLICT")
                return checkpoint_from_payload(existing)
            connection.execute(insert(checkpoints).values(
                checkpoint_id=checkpoint.checkpoint_id, run_id=checkpoint.context.run_id,
                scope_id=checkpoint.context.scope.scope_id, created_at=checkpoint.created_at,
                input_hash=checkpoint.context.input_hash, completeness=checkpoint.context.completeness.value,
                data=checkpoint.as_payload()))
        return checkpoint

    def load_checkpoint(self, checkpoint_id: str) -> DecisionContextCheckpoint | None:
        with self.store.transaction() as connection:
            row = connection.execute(select(checkpoints.c.data).where(
                checkpoints.c.checkpoint_id == checkpoint_id)).scalar_one_or_none()
        return None if row is None else checkpoint_from_payload(row)

    def find_checkpoint_by_run(self, run_id: str) -> DecisionContextCheckpoint | None:
        with self.store.transaction() as connection:
            row = connection.execute(select(checkpoints.c.data).where(
                checkpoints.c.run_id == run_id).order_by(checkpoints.c.created_at.desc()).limit(1)
            ).scalar_one_or_none()
        return None if row is None else checkpoint_from_payload(row)

    def list_checkpoints(self, scope: StrategyMemoryScope, *, limit: int = 20, since: str | None = None):
        statement = select(checkpoints.c.data).where(checkpoints.c.scope_id == scope.scope_id)
        if since:
            statement = statement.where(checkpoints.c.created_at >= since)
        with self.store.transaction() as connection:
            rows = connection.execute(statement.order_by(checkpoints.c.created_at.desc()).limit(limit)).scalars().all()
        return tuple(checkpoint_from_payload(row) for row in rows)

    def list_decisions(self, scope: StrategyMemoryScope, *, limit: int = 20, since: str | None = None):
        statement = select(events.c.payload, events.c.recorded_at).where(
            events.c.scope_id == scope.scope_id,
            events.c.event_type == EventType.DECISION_RECORDED.value)
        if since:
            statement = statement.where(events.c.recorded_at >= since)
        with self.store.transaction() as connection:
            rows = connection.execute(statement.order_by(events.c.sequence.desc()).limit(limit)).all()
        result = []
        for payload, _ in rows:
            decision = payload.get("decision")
            if isinstance(decision, dict):
                result.append(decision_ref_from_payload(decision))
        return tuple(result)

    def save_knowledge(self, item: ValidatedKnowledge) -> None:
        with self.store.transaction() as connection:
            self._upsert(connection, knowledge,
                         {"scope_id": item.scope.scope_id, "knowledge_id": item.knowledge_id},
                         dict(version=item.version, status=item.status.value,
                              data=domain_payload(item), updated_at=item.validated_at or item.proposed_at))

    def list_knowledge(self, scope: StrategyMemoryScope, *, limit: int = 50):
        with self.store.transaction() as connection:
            rows = connection.execute(select(knowledge.c.data)
                                      .where(knowledge.c.scope_id == scope.scope_id)
                                      .order_by(knowledge.c.updated_at.desc()).limit(limit)).scalars().all()
        return tuple(knowledge_from_payload(row) for row in rows)

    def save_prompt_template(self, template_id: str, version: str, role: str, data: dict) -> None:
        template_hash = canonical_hash(data)
        with self.store.transaction() as connection:
            self._upsert(connection, prompt_templates, {"template_id": template_id, "version": version},
                         dict(role=role, template_hash=template_hash, data=data, active=1))

    def list_prompt_templates(self, template_id: str | None = None):
        statement = select(prompt_templates.c.data, prompt_templates.c.template_id, prompt_templates.c.version)
        if template_id:
            statement = statement.where(prompt_templates.c.template_id == template_id)
        with self.store.transaction() as connection:
            return connection.execute(statement.order_by(prompt_templates.c.template_id,
                                                         prompt_templates.c.version)).all()
