"""Versioned, provider-neutral strategy memory contracts.

This module is deliberately independent of any model provider, HTTP client or
database. Model-authored values are untrusted proposals: the contracts reject
model writes to authorization, risk, ownership and execution-authority fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace, fields, is_dataclass
from decimal import Decimal
from enum import Enum, StrEnum
import re

from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp

MEMORY_SCHEMA_REVISION = "strategy-memory-v1"
CONTEXT_SCHEMA_REVISION = "decision-context-v1"
CHECKPOINT_SCHEMA_REVISION = "decision-context-checkpoint-v1"

_TOKEN = re.compile(r"[A-Za-z0-9_.:+\-]{1,160}\Z", re.ASCII)
_INSTRUMENT = re.compile(r"[A-Za-z0-9_.:+\-]{1,160}\Z", re.ASCII)


def _token(value: str, code: str, *, maximum: int = 160) -> str:
    if type(value) is not str or not value or len(value) > maximum or not _TOKEN.fullmatch(value):
        raise ValueError(code)
    return value


def _text(value: str, code: str, *, maximum: int = 4000) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(code)
    return value


def _timestamp(value: str | None, code: str) -> str | None:
    if value is None or value == "":
        return value
    utc_timestamp(value)
    return value


def _decimal(value: str | Decimal | int, code: str) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(code)
        return format(value, "f")
    if type(value) is int:
        return str(value)
    if type(value) is not str or not value or len(value) > 100:
        raise ValueError(code)
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError(code)
    return value


def _refs(values, code: str, *, maximum: int = 64) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (tuple, list)) or len(values) > maximum:
        raise ValueError(code)
    result = tuple(values)
    if any(type(item) is not str or not item or len(item) > 256 for item in result):
        raise ValueError(code)
    return result


MODEL_UNWRITABLE_FIELDS = frozenset({
    "authorization", "authorization_id", "permissions", "owner_epoch", "account_scope",
    "risk_limits", "max_position_pct", "max_daily_loss", "maximum_target_notional",
    "execution_authorized", "execution_authority", "budget", "account_id",
    "venue_account_id", "tenant_id", "user_id", "strategy_id",
})

_SECRET_MARKERS = ("api_key", "apikey", "authorization:", "bearer ", "secret", "password", "token=")


def json_safe(value):
    """Convert typed domain values to lossless JSON values (tuples become lists)."""
    if value is None or type(value) in (str, bool, int):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("MEMORY_NONFINITE_DECIMAL")
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: json_safe(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    raise ValueError("MEMORY_JSON_VALUE_INVALID")


def reject_model_authority(payload: dict, *, source: str) -> None:
    """Model output is data; it may never carry authority or credential fields."""
    if type(payload) is not dict:
        raise ValueError("MEMORY_PAYLOAD_OBJECT_REQUIRED")
    keys = {str(key).lower() for key in payload}
    if source == "MODEL":
        forbidden = keys & {item.lower() for item in MODEL_UNWRITABLE_FIELDS}
        if forbidden:
            raise ValueError("MODEL_MAY_NOT_WRITE_AUTHORITY_FIELDS:" + ",".join(sorted(forbidden)))
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in _SECRET_MARKERS):
        raise ValueError("MEMORY_SECRET_FIELD_REJECTED")


class ThesisStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WEAKENED = "WEAKENED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class PlanStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    ACTIVE = "ACTIVE"
    REVISED = "REVISED"
    CANCEL_PENDING = "CANCEL_PENDING"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class KnowledgeStatus(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Completeness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CONTEXT_INCOMPLETE = "CONTEXT_INCOMPLETE"


class EventType(StrEnum):
    STRATEGY_CREATED = "STRATEGY_CREATED"
    STRATEGY_CONFIG_CHANGED = "STRATEGY_CONFIG_CHANGED"
    STRATEGY_PAUSED = "STRATEGY_PAUSED"
    STRATEGY_RESUMED = "STRATEGY_RESUMED"
    THESIS_CREATED = "THESIS_CREATED"
    THESIS_UPDATED = "THESIS_UPDATED"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    THESIS_EXPIRED = "THESIS_EXPIRED"
    PLAN_PROPOSED = "PLAN_PROPOSED"
    PLAN_ACCEPTED = "PLAN_ACCEPTED"
    PLAN_ACTIVATED = "PLAN_ACTIVATED"
    PLAN_REVISED = "PLAN_REVISED"
    PLAN_CANCEL_PENDING = "PLAN_CANCEL_PENDING"
    PLAN_REJECTED = "PLAN_REJECTED"
    PLAN_EXPIRED = "PLAN_EXPIRED"
    PLAN_COMPLETED = "PLAN_COMPLETED"
    WATCH_CONDITION_TRIGGERED = "WATCH_CONDITION_TRIGGERED"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"
    DECISION_RECORDED = "DECISION_RECORDED"
    EXECUTION_FACT_LINKED = "EXECUTION_FACT_LINKED"
    MODEL_SWITCHED = "MODEL_SWITCHED"
    KNOWLEDGE_PROPOSED = "KNOWLEDGE_PROPOSED"
    KNOWLEDGE_VALIDATED = "KNOWLEDGE_VALIDATED"
    KNOWLEDGE_REJECTED = "KNOWLEDGE_REJECTED"
    KNOWLEDGE_EXPIRED = "KNOWLEDGE_EXPIRED"
    CHECKPOINT_RECORDED = "CHECKPOINT_RECORDED"
    PROJECTION_REBUILT = "PROJECTION_REBUILT"


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyMemoryScope:
    tenant_id: str
    user_id: str
    account_scope: str
    strategy_id: str
    product: str
    instrument: str
    environment: str = "PAPER"

    def __post_init__(self):
        for name in ("tenant_id", "user_id", "account_scope", "strategy_id", "product", "environment"):
            _token(getattr(self, name), "MEMORY_SCOPE_INVALID")
        if self.environment not in ("PAPER", "RESEARCH"):
            raise ValueError("MEMORY_ENVIRONMENT_UNSUPPORTED")
        if self.product not in ("SPOT",):
            raise ValueError("MEMORY_PRODUCT_UNSUPPORTED")
        if type(self.instrument) is not str or not _INSTRUMENT.fullmatch(self.instrument):
            raise ValueError("MEMORY_INSTRUMENT_INVALID")

    @property
    def scope_id(self) -> str:
        return "sms:" + canonical_hash(domain_payload(self))[:40]

    def as_payload(self) -> dict:
        return domain_payload(self)

    @classmethod
    def from_payload(cls, value: dict) -> "StrategyMemoryScope":
        if type(value) is not dict:
            raise ValueError("MEMORY_SCOPE_INVALID")
        return cls(**value)


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyIdentity:
    scope: StrategyMemoryScope
    strategy_version: int
    authorization_summary: dict = field(default_factory=dict)
    risk_limits: dict = field(default_factory=dict)
    owner_epoch: int = 1
    status: str = "DRAFT"

    def __post_init__(self):
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        if type(self.strategy_version) is not int or self.strategy_version < 1:
            raise ValueError("MEMORY_STRATEGY_VERSION_INVALID")
        if type(self.owner_epoch) is not int or self.owner_epoch < 0:
            raise ValueError("MEMORY_OWNER_EPOCH_INVALID")
        if self.status not in ("DRAFT", "ACTIVE", "PAUSED"):
            raise ValueError("MEMORY_STRATEGY_STATUS_INVALID")
        for name in ("authorization_summary", "risk_limits"):
            if type(getattr(self, name)) is not dict:
                raise ValueError("MEMORY_IDENTITY_FIELD_INVALID")


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketThesis:
    thesis_id: str
    scope: StrategyMemoryScope
    statement: str
    status: ThesisStatus = ThesisStatus.ACTIVE
    evidence_refs: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    expires_at: str | None = None
    revision: int = 1
    source: str = "MODEL"
    model_confidence: str | None = None

    def __post_init__(self):
        _token(self.thesis_id, "MEMORY_THESIS_ID_INVALID")
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        _text(self.statement, "MEMORY_THESIS_STATEMENT_INVALID")
        object.__setattr__(self, "status", ThesisStatus(self.status))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "MEMORY_THESIS_EVIDENCE_INVALID"))
        object.__setattr__(self, "invalidation_conditions", _refs(
            self.invalidation_conditions, "MEMORY_THESIS_INVALIDATION_INVALID", maximum=32))
        if not self.evidence_refs:
            raise ValueError("MEMORY_THESIS_EVIDENCE_REQUIRED")
        _timestamp(self.created_at, "MEMORY_THESIS_TIME_INVALID")
        _timestamp(self.updated_at, "MEMORY_THESIS_TIME_INVALID")
        _timestamp(self.expires_at, "MEMORY_THESIS_TIME_INVALID")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("MEMORY_THESIS_REVISION_INVALID")
        if self.source not in ("MODEL", "CONTROL", "EXECUTION_LEDGER", "MANUAL", "SYSTEM"):
            raise ValueError("MEMORY_THESIS_SOURCE_INVALID")
        if self.model_confidence is not None:
            _decimal(self.model_confidence, "MEMORY_CONFIDENCE_INVALID")
        reject_model_authority(domain_payload(self), source=self.source)

    def with_status(self, status: ThesisStatus, *, updated_at: str, reason: str = "") -> "MarketThesis":
        return replace(self, status=ThesisStatus(status), updated_at=updated_at, revision=self.revision + 1,
                       invalidation_conditions=self.invalidation_conditions + ((reason,) if reason else ()))


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyPlan:
    plan_id: str
    scope: StrategyMemoryScope
    thesis_id: str | None
    status: PlanStatus = PlanStatus.PROPOSED
    action: str = "HOLD"
    target_side: str = "FLAT"
    target_sizing: str = "BASE_QUANTITY"
    target_value: str = "0"
    entry: dict = field(default_factory=dict)
    exit_plan: dict = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    valid_until: str | None = None
    created_at: str = ""
    updated_at: str = ""
    revision: int = 1
    source: str = "MODEL"
    supersedes_plan_id: str | None = None
    authorization_ref: str | None = None
    execution_refs: tuple[str, ...] = ()
    source_proposal: dict = field(default_factory=dict)

    def __post_init__(self):
        _token(self.plan_id, "MEMORY_PLAN_ID_INVALID")
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        if self.thesis_id is not None:
            _token(self.thesis_id, "MEMORY_PLAN_THESIS_INVALID")
        object.__setattr__(self, "status", PlanStatus(self.status))
        if self.action not in ("HOLD", "SET_TARGET", "UPDATE_EXIT", "REDUCE", "EXIT"):
            raise ValueError("MEMORY_PLAN_ACTION_INVALID")
        if self.target_side not in ("LONG", "FLAT", "SHORT"):
            raise ValueError("MEMORY_PLAN_SIDE_INVALID")
        if self.scope.product == "SPOT" and self.target_side == "SHORT":
            raise ValueError("MEMORY_PLAN_SHORT_UNSUPPORTED")
        if self.target_sizing not in ("BASE_QUANTITY", "QUOTE_NOTIONAL"):
            raise ValueError("MEMORY_PLAN_SIZING_INVALID")
        _decimal(self.target_value, "MEMORY_PLAN_TARGET_INVALID")
        object.__setattr__(self, "target_value", _decimal(self.target_value, "MEMORY_PLAN_TARGET_INVALID"))
        if Decimal(self.target_value) < 0:
            raise ValueError("MEMORY_PLAN_TARGET_INVALID")
        for name in ("entry", "exit_plan", "source_proposal"):
            if type(getattr(self, name)) is not dict:
                raise ValueError("MEMORY_PLAN_TERMS_INVALID")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "MEMORY_PLAN_EVIDENCE_INVALID"))
        object.__setattr__(self, "invalidation_conditions", _refs(
            self.invalidation_conditions, "MEMORY_PLAN_INVALIDATION_INVALID", maximum=32))
        object.__setattr__(self, "execution_refs", _refs(self.execution_refs, "MEMORY_PLAN_EXECUTION_REF_INVALID"))
        if not self.evidence_refs:
            raise ValueError("MEMORY_PLAN_EVIDENCE_REQUIRED")
        _timestamp(self.valid_until, "MEMORY_PLAN_TIME_INVALID")
        _timestamp(self.created_at, "MEMORY_PLAN_TIME_INVALID")
        _timestamp(self.updated_at, "MEMORY_PLAN_TIME_INVALID")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("MEMORY_PLAN_REVISION_INVALID")
        if self.source not in ("MODEL", "CONTROL", "EXECUTION_LEDGER", "MANUAL", "SYSTEM"):
            raise ValueError("MEMORY_PLAN_SOURCE_INVALID")
        if self.supersedes_plan_id is not None:
            _token(self.supersedes_plan_id, "MEMORY_PLAN_SUPERSEDES_INVALID")
        if self.authorization_ref is not None:
            _token(self.authorization_ref, "MEMORY_PLAN_AUTH_REF_INVALID")
        reject_model_authority(domain_payload(self), source=self.source)

    def with_status(self, status: PlanStatus, *, updated_at: str, authorization_ref: str | None = None,
                    execution_refs: tuple[str, ...] | None = None) -> "StrategyPlan":
        return replace(self, status=PlanStatus(status), updated_at=updated_at, revision=self.revision + 1,
                       authorization_ref=authorization_ref if authorization_ref is not None else self.authorization_ref,
                       execution_refs=self.execution_refs + (execution_refs or ()))


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyMemoryEvent:
    event_id: str
    scope: StrategyMemoryScope
    event_type: EventType
    source: str
    occurred_at: str
    recorded_at: str
    idempotency_key: str
    state_revision: int
    payload: dict
    schema_revision: str = MEMORY_SCHEMA_REVISION
    sequence: int = 0
    event_hash: str = ""

    def __post_init__(self):
        _token(self.event_id, "MEMORY_EVENT_ID_INVALID")
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        object.__setattr__(self, "event_type", EventType(self.event_type))
        if self.source not in ("MODEL", "CONTROL", "EXECUTION_LEDGER", "MANUAL", "SYSTEM"):
            raise ValueError("MEMORY_EVENT_SOURCE_INVALID")
        _timestamp(self.occurred_at, "MEMORY_EVENT_TIME_INVALID")
        _timestamp(self.recorded_at, "MEMORY_EVENT_TIME_INVALID")
        if not self.occurred_at or not self.recorded_at:
            raise ValueError("MEMORY_EVENT_TIME_REQUIRED")
        _token(self.idempotency_key, "MEMORY_EVENT_IDEMPOTENCY_INVALID", maximum=200)
        if type(self.state_revision) is not int or self.state_revision < 0:
            raise ValueError("MEMORY_EVENT_REVISION_INVALID")
        if self.schema_revision != MEMORY_SCHEMA_REVISION:
            raise ValueError("MEMORY_EVENT_SCHEMA_UNSUPPORTED")
        if type(self.payload) is not dict:
            raise ValueError("MEMORY_EVENT_PAYLOAD_INVALID")
        object.__setattr__(self, "payload", json_safe(self.payload))
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("MEMORY_EVENT_SEQUENCE_INVALID")
        reject_model_authority(self.payload, source=self.source)
        hash_payload = domain_payload(self)
        hash_payload.pop("event_hash", None)
        expected = canonical_hash(hash_payload)
        if self.event_hash and self.event_hash != expected:
            raise ValueError("MEMORY_EVENT_HASH_MISMATCH")
        if not self.event_hash:
            object.__setattr__(self, "event_hash", expected)

    @property
    def payload_hash(self) -> str:
        return canonical_hash(self.payload)


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionHistoryRef:
    run_id: str
    scope: StrategyMemoryScope
    created_at: str
    decision_status: str
    action: str | None = None
    model_provider: str | None = None
    model_name: str | None = None
    prompt_revision: str | None = None
    schema_revision: str | None = None
    policy_revision: str | None = None
    snapshot_id: str | None = None
    snapshot_hash: str | None = None
    memory_state_revision: int = 0
    checkpoint_id: str | None = None
    thesis_id: str | None = None
    plan_id: str | None = None
    execution_status: str | None = None

    def __post_init__(self):
        _token(self.run_id, "MEMORY_DECISION_RUN_INVALID")
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        _timestamp(self.created_at, "MEMORY_DECISION_TIME_INVALID")
        if not self.created_at:
            raise ValueError("MEMORY_DECISION_TIME_REQUIRED")
        _text(self.decision_status, "MEMORY_DECISION_STATUS_INVALID", maximum=80)
        for name in ("model_provider", "model_name", "prompt_revision", "schema_revision", "policy_revision",
                     "snapshot_id", "snapshot_hash", "checkpoint_id", "thesis_id", "plan_id", "execution_status"):
            value = getattr(self, name)
            if value is not None:
                if name in ("model_provider", "model_name"):
                    if type(value) is not str or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
                        raise ValueError("MEMORY_DECISION_FIELD_INVALID")
                else:
                    _token(value, "MEMORY_DECISION_FIELD_INVALID", maximum=256)
        if self.action is not None and self.action not in ("BUY", "SELL", "HOLD", "SET_TARGET", "UPDATE_EXIT", "REDUCE", "EXIT", None):
            raise ValueError("MEMORY_DECISION_ACTION_INVALID")
        if type(self.memory_state_revision) is not int or self.memory_state_revision < 0:
            raise ValueError("MEMORY_DECISION_REVISION_INVALID")


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionFactRef:
    fact_type: str
    source_system: str
    source_id: str
    scope: StrategyMemoryScope
    occurred_at: str
    recorded_at: str
    status: str
    order_id: str | None = None
    command_id: str | None = None
    fill_id: str | None = None
    allocation_id: str | None = None
    plan_id: str | None = None
    authority: str = "EXECUTION_LEDGER"
    stale: bool = False
    unknown: bool = False

    def __post_init__(self):
        if self.fact_type not in ("COMMAND", "ORDER", "FILL", "FEE", "PROTECTION", "CANCEL",
                                  "RECONCILIATION", "UNKNOWN", "MANUAL", "POSITION"):
            raise ValueError("MEMORY_EXECUTION_FACT_TYPE_INVALID")
        if self.source_system not in ("EXECUTION_LEDGER", "CONTROL", "MANUAL"):
            raise ValueError("MEMORY_EXECUTION_FACT_SOURCE_INVALID")
        if self.authority != "EXECUTION_LEDGER":
            raise ValueError("MEMORY_EXECUTION_AUTHORITY_INVALID")
        _token(self.source_id, "MEMORY_EXECUTION_SOURCE_ID_INVALID", maximum=256)
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        _timestamp(self.occurred_at, "MEMORY_EXECUTION_TIME_INVALID")
        _timestamp(self.recorded_at, "MEMORY_EXECUTION_TIME_INVALID")
        if not self.occurred_at or not self.recorded_at:
            raise ValueError("MEMORY_EXECUTION_TIME_REQUIRED")
        _text(self.status, "MEMORY_EXECUTION_STATUS_INVALID", maximum=80)
        for name in ("order_id", "command_id", "fill_id", "allocation_id", "plan_id"):
            value = getattr(self, name)
            if value is not None:
                _token(value, "MEMORY_EXECUTION_ID_INVALID", maximum=256)
        if type(self.stale) is not bool or type(self.unknown) is not bool:
            raise ValueError("MEMORY_EXECUTION_FLAG_INVALID")


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidatedKnowledge:
    knowledge_id: str
    scope: StrategyMemoryScope
    statement: str
    status: KnowledgeStatus = KnowledgeStatus.PROPOSED
    market: str = ""
    product: str = ""
    instrument: str = ""
    timeframe: str = ""
    strategy_version: int = 1
    sample_size: int = 0
    evaluation_window: dict = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    proposed_at: str = ""
    validated_at: str | None = None
    expires_at: str | None = None
    version: int = 1
    provenance: str = "MODEL_PROPOSAL"

    def __post_init__(self):
        _token(self.knowledge_id, "MEMORY_KNOWLEDGE_ID_INVALID")
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        _text(self.statement, "MEMORY_KNOWLEDGE_STATEMENT_INVALID")
        object.__setattr__(self, "status", KnowledgeStatus(self.status))
        for name in ("market", "product", "instrument", "timeframe"):
            value = getattr(self, name)
            if type(value) is not str or len(value) > 160 or any(ord(c) < 32 for c in value):
                raise ValueError("MEMORY_KNOWLEDGE_SCOPE_TEXT_INVALID")
        if type(self.strategy_version) is not int or self.strategy_version < 1:
            raise ValueError("MEMORY_KNOWLEDGE_STRATEGY_VERSION_INVALID")
        if type(self.sample_size) is not int or self.sample_size < 0:
            raise ValueError("MEMORY_KNOWLEDGE_SAMPLE_INVALID")
        if type(self.evaluation_window) is not dict:
            raise ValueError("MEMORY_KNOWLEDGE_WINDOW_INVALID")
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "MEMORY_KNOWLEDGE_EVIDENCE_INVALID"))
        _timestamp(self.proposed_at, "MEMORY_KNOWLEDGE_TIME_INVALID")
        _timestamp(self.validated_at, "MEMORY_KNOWLEDGE_TIME_INVALID")
        _timestamp(self.expires_at, "MEMORY_KNOWLEDGE_TIME_INVALID")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("MEMORY_KNOWLEDGE_VERSION_INVALID")
        if self.status == KnowledgeStatus.VALIDATED:
            if self.provenance not in ("DETERMINISTIC_STATISTICS", "USER_REVIEW"):
                raise ValueError("MEMORY_KNOWLEDGE_VALIDATION_PROVENANCE_REQUIRED")
            if not self.evidence_refs or not self.validated_at:
                raise ValueError("MEMORY_KNOWLEDGE_VALIDATION_EVIDENCE_REQUIRED")
        reject_model_authority(domain_payload(self), source="MODEL" if self.provenance == "MODEL_PROPOSAL" else "CONTROL")


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionContext:
    run_id: str
    scope: StrategyMemoryScope
    as_of: str
    state_revision: int
    strategy_version: int
    snapshot_id: str
    snapshot_hash: str
    account_revision: int
    sections: dict
    token_estimate: int
    completeness: Completeness
    missing: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    prompt_versions: dict = field(default_factory=dict)
    schema_versions: dict = field(default_factory=dict)
    model_versions: dict = field(default_factory=dict)
    previous_plan_id: str | None = None
    previous_thesis_id: str | None = None
    manual_intervention_at: str | None = None
    input_hash: str = ""
    schema_revision: str = CONTEXT_SCHEMA_REVISION

    def __post_init__(self):
        _token(self.run_id, "MEMORY_CONTEXT_RUN_INVALID")
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        _timestamp(self.as_of, "MEMORY_CONTEXT_TIME_INVALID")
        if type(self.state_revision) is not int or self.state_revision < 0:
            raise ValueError("MEMORY_CONTEXT_REVISION_INVALID")
        if type(self.strategy_version) is not int or self.strategy_version < 1:
            raise ValueError("MEMORY_CONTEXT_STRATEGY_VERSION_INVALID")
        for name in ("snapshot_id", "snapshot_hash"):
            _token(getattr(self, name), "MEMORY_CONTEXT_SNAPSHOT_INVALID", maximum=256)
        if type(self.account_revision) is not int or self.account_revision < 0:
            raise ValueError("MEMORY_CONTEXT_ACCOUNT_REVISION_INVALID")
        if type(self.sections) is not dict:
            raise ValueError("MEMORY_CONTEXT_SECTIONS_INVALID")
        object.__setattr__(self, "sections", json_safe(self.sections))
        object.__setattr__(self, "prompt_versions", json_safe(self.prompt_versions))
        object.__setattr__(self, "schema_versions", json_safe(self.schema_versions))
        object.__setattr__(self, "model_versions", json_safe(self.model_versions))
        if type(self.token_estimate) is not int or self.token_estimate < 0:
            raise ValueError("MEMORY_CONTEXT_TOKEN_INVALID")
        object.__setattr__(self, "completeness", Completeness(self.completeness))
        object.__setattr__(self, "missing", _refs(self.missing, "MEMORY_CONTEXT_MISSING_INVALID", maximum=128))
        object.__setattr__(self, "conflicts", _refs(self.conflicts, "MEMORY_CONTEXT_CONFLICTS_INVALID", maximum=128))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "MEMORY_CONTEXT_EVIDENCE_INVALID", maximum=256))
        for name in ("prompt_versions", "schema_versions", "model_versions"):
            if type(getattr(self, name)) is not dict:
                raise ValueError("MEMORY_CONTEXT_VERSION_INVALID")
        _timestamp(self.manual_intervention_at, "MEMORY_CONTEXT_TIME_INVALID")
        if self.schema_revision != CONTEXT_SCHEMA_REVISION:
            raise ValueError("MEMORY_CONTEXT_SCHEMA_UNSUPPORTED")
        reject_model_authority(self.sections, source="CONTROL")
        expected_payload = domain_payload(self)
        expected_payload.pop("input_hash", None)
        expected = canonical_hash(expected_payload)
        if self.input_hash and self.input_hash != expected:
            raise ValueError("MEMORY_CONTEXT_HASH_MISMATCH")
        if not self.input_hash:
            object.__setattr__(self, "input_hash", expected)

    @property
    def context_hash(self) -> str:
        payload = domain_payload(self)
        payload.pop("input_hash", None)
        return canonical_hash(payload)


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionContextCheckpoint:
    context: DecisionContext
    created_at: str
    checkpoint_id: str = ""
    schema_revision: str = CHECKPOINT_SCHEMA_REVISION

    def __post_init__(self):
        if type(self.context) is not DecisionContext:
            raise ValueError("MEMORY_CONTEXT_REQUIRED")
        _timestamp(self.created_at, "MEMORY_CHECKPOINT_TIME_INVALID")
        if self.schema_revision != CHECKPOINT_SCHEMA_REVISION:
            raise ValueError("MEMORY_CHECKPOINT_SCHEMA_UNSUPPORTED")
        expected = "mctx:" + self.context.context_hash[:40]
        if self.checkpoint_id and self.checkpoint_id != expected:
            raise ValueError("MEMORY_CHECKPOINT_ID_MISMATCH")
        object.__setattr__(self, "checkpoint_id", expected)

    def as_payload(self) -> dict:
        return {"checkpoint_id": self.checkpoint_id, "schema_revision": self.schema_revision,
                "created_at": self.created_at, "context": domain_payload(self.context)}


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyState:
    scope: StrategyMemoryScope
    state_revision: int = 0
    schema_revision: str = MEMORY_SCHEMA_REVISION
    strategy_version: int = 1
    status: str = "DRAFT"
    active_thesis_id: str | None = None
    active_plan_id: str | None = None
    last_event_id: str | None = None
    last_decision_id: str | None = None
    event_cursor: int = 0
    updated_at: str = ""
    manual_intervention_at: str | None = None
    manual_action: str | None = None
    theses: tuple[MarketThesis, ...] = ()
    plans: tuple[StrategyPlan, ...] = ()
    knowledge: tuple[ValidatedKnowledge, ...] = ()
    state_hash: str = ""

    def __post_init__(self):
        if type(self.scope) is not StrategyMemoryScope:
            raise ValueError("MEMORY_SCOPE_REQUIRED")
        if type(self.state_revision) is not int or self.state_revision < 0:
            raise ValueError("MEMORY_STATE_REVISION_INVALID")
        if self.schema_revision != MEMORY_SCHEMA_REVISION:
            raise ValueError("MEMORY_STATE_SCHEMA_UNSUPPORTED")
        if type(self.strategy_version) is not int or self.strategy_version < 1:
            raise ValueError("MEMORY_STATE_STRATEGY_VERSION_INVALID")
        if self.status not in ("DRAFT", "ACTIVE", "PAUSED"):
            raise ValueError("MEMORY_STATE_STATUS_INVALID")
        if type(self.event_cursor) is not int or self.event_cursor < 0:
            raise ValueError("MEMORY_STATE_CURSOR_INVALID")
        _timestamp(self.updated_at, "MEMORY_STATE_TIME_INVALID")
        _timestamp(self.manual_intervention_at, "MEMORY_STATE_TIME_INVALID")
        if type(self.theses) is not tuple or any(type(item) is not MarketThesis for item in self.theses):
            raise ValueError("MEMORY_STATE_THESES_INVALID")
        if type(self.plans) is not tuple or any(type(item) is not StrategyPlan for item in self.plans):
            raise ValueError("MEMORY_STATE_PLANS_INVALID")
        if type(self.knowledge) is not tuple or any(type(item) is not ValidatedKnowledge for item in self.knowledge):
            raise ValueError("MEMORY_STATE_KNOWLEDGE_INVALID")
        for item in self.theses + self.plans + self.knowledge:
            if item.scope != self.scope:
                raise ValueError("MEMORY_STATE_SCOPE_MISMATCH")
        if self.active_thesis_id is not None and self.thesis(self.active_thesis_id) is None:
            raise ValueError("MEMORY_STATE_ACTIVE_THESIS_MISSING")
        if self.active_plan_id is not None and self.plan(self.active_plan_id) is None:
            raise ValueError("MEMORY_STATE_ACTIVE_PLAN_MISSING")
        expected_payload = domain_payload(self)
        expected_payload.pop("state_hash", None)
        expected = canonical_hash(expected_payload)
        if self.state_hash and self.state_hash != expected:
            raise ValueError("MEMORY_STATE_HASH_MISMATCH")
        if not self.state_hash:
            object.__setattr__(self, "state_hash", expected)

    def thesis(self, thesis_id: str | None) -> MarketThesis | None:
        return next((item for item in self.theses if item.thesis_id == thesis_id), None)

    def plan(self, plan_id: str | None) -> StrategyPlan | None:
        return next((item for item in self.plans if item.plan_id == plan_id), None)

    @property
    def active_thesis(self) -> MarketThesis | None:
        return self.thesis(self.active_thesis_id)

    @property
    def active_plan(self) -> StrategyPlan | None:
        return self.plan(self.active_plan_id)


def state_from_payload(payload: dict) -> StrategyState:
    if type(payload) is not dict:
        raise ValueError("MEMORY_STATE_PAYLOAD_INVALID")
    data = dict(payload)
    data["scope"] = StrategyMemoryScope.from_payload(data["scope"])
    data["theses"] = tuple(_thesis_from_payload(item) for item in data.get("theses", []))
    data["plans"] = tuple(_plan_from_payload(item) for item in data.get("plans", []))
    data["knowledge"] = tuple(_knowledge_from_payload(item) for item in data.get("knowledge", []))
    return StrategyState(**data)


def _thesis_from_payload(payload: dict) -> MarketThesis:
    data = dict(payload)
    data["scope"] = StrategyMemoryScope.from_payload(data["scope"])
    return MarketThesis(**data)


def _plan_from_payload(payload: dict) -> StrategyPlan:
    data = dict(payload)
    data["scope"] = StrategyMemoryScope.from_payload(data["scope"])
    return StrategyPlan(**data)


def _knowledge_from_payload(payload: dict) -> ValidatedKnowledge:
    data = dict(payload)
    data["scope"] = StrategyMemoryScope.from_payload(data["scope"])
    return ValidatedKnowledge(**data)


def event_from_payload(payload: dict) -> StrategyMemoryEvent:
    data = dict(payload)
    data["scope"] = StrategyMemoryScope.from_payload(data["scope"])
    return StrategyMemoryEvent(**data)


def decision_ref_from_payload(payload: dict) -> DecisionHistoryRef:
    data = dict(payload)
    data["scope"] = StrategyMemoryScope.from_payload(data["scope"])
    return DecisionHistoryRef(**data)


def execution_fact_from_payload(payload: dict) -> ExecutionFactRef:
    data = dict(payload)
    data["scope"] = StrategyMemoryScope.from_payload(data["scope"])
    return ExecutionFactRef(**data)


def knowledge_from_payload(payload: dict) -> ValidatedKnowledge:
    return _knowledge_from_payload(payload)


def checkpoint_from_payload(payload: dict) -> DecisionContextCheckpoint:
    if type(payload) is not dict:
        raise ValueError("MEMORY_CHECKPOINT_PAYLOAD_INVALID")
    context_data = dict(payload["context"])
    context_data["scope"] = StrategyMemoryScope.from_payload(context_data["scope"])
    return DecisionContextCheckpoint(
        checkpoint_id=payload.get("checkpoint_id", ""), schema_revision=payload.get("schema_revision", CHECKPOINT_SCHEMA_REVISION),
        created_at=payload["created_at"], context=DecisionContext(**context_data))


def context_from_payload(payload: dict) -> DecisionContext:
    data = dict(payload)
    data["scope"] = StrategyMemoryScope.from_payload(data["scope"])
    return DecisionContext(**data)
