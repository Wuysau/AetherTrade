"""Offline research artifacts. Hashes identify content, never authorize trading.

IP-01's compact DTOs remain unchanged. These local immutable records retain
the extra provenance needed for replay. Archives are process-local; callers
must export and durably archive artifact_json before claiming durable evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, domain_payload, utc_timestamp
from trading_system.contracts.wire import ContractCodec
from trading_system.market.provenance import is_digest
from trading_system.market.snapshots import snapshot_content_hash


class ResearchError(ValueError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ResearchError(reason)


def fingerprint(record: object) -> str:
    return canonical_hash(domain_payload(record))


def check_ref(ref: t.EvidenceRef, environment: t.Environment) -> None:
    require(isinstance(ref, t.EvidenceRef) and bool(ref.id) and bool(ref.source)
            and is_digest(ref.hash) and ref.environment == environment, 'INVALID_EVIDENCE_REFERENCE')
    utc_timestamp(ref.visible_at)


def check_offline(environment: t.Environment) -> None:
    require(isinstance(environment, t.Environment)
            and environment in (t.Environment.RESEARCH, t.Environment.PAPER), 'OFFLINE_ENVIRONMENT_REQUIRED')


@dataclass(frozen=True, slots=True, kw_only=True)
class TemporalRecord:
    """A revision of an untrusted fact, including membership and fee schedules.

logical_key identifies a fact within a source/category. Membership should use
one key per universe/instrument and content must include additions/removals.
effective_at is separate from when the fact became known. All four clocks
must be no later than the decision; later downloads are not backdated.
"""
    logical_key: str
    category: Literal['NEWS', 'CONSTITUENT', 'FEE_SCHEDULE']
    source: str
    revision: int
    published_at: str
    revised_at: str
    downloaded_at: str
    effective_at: str
    content: str

    def reference(self, environment: t.Environment) -> t.EvidenceRef:
        visible = max((self.published_at, self.revised_at, self.downloaded_at,
                       self.effective_at), key=utc_timestamp)
        digest = fingerprint(self)
        return t.EvidenceRef(id='research-fact:' + digest, hash=digest,
            environment=environment, source=self.source, visible_at=visible)


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionView:
    as_of: str
    snapshots: tuple[t.SnapshotPacket, ...]
    records: tuple[TemporalRecord, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordedDecision:
    decision_at: str
    recorded_at: str
    kind: Literal['FORWARD_RECORDED', 'NEW_MODEL_INFERENCE']
    view_hash: str
    proposal: t.DecisionProposal
    model_fingerprint: str
    prompt_hash: str
    rule_hash: str
    cost: t.Money | None

    def reference(self, environment: t.Environment) -> t.EvidenceRef:
        digest = fingerprint(self)
        return t.EvidenceRef(id='research-decision:' + digest, hash=digest,
            environment=environment, source=self.kind, visible_at=self.recorded_at)


@dataclass(frozen=True, slots=True, kw_only=True)
class Dataset:
    scope: t.ScopeKey
    start: str
    end: str
    decision_times: tuple[str, ...]
    records: tuple[TemporalRecord, ...]
    snapshots: tuple[t.SnapshotPacket, ...]
    recorded_outputs: tuple[RecordedDecision, ...]
    evidence: tuple[t.EvidenceRef, ...]


def check_dataset(dataset: Dataset) -> None:
    check_offline(dataset.scope.environment)
    require(isinstance(dataset.scope.product, t.Product) and bool(dataset.scope.venue_account_id)
            and bool(dataset.scope.instrument_uid) and dataset.scope.venue_leg in ('BOTH', 'LONG', 'SHORT'),
            'INVALID_DATASET_SCOPE')
    start, end = utc_timestamp(dataset.start), utc_timestamp(dataset.end)
    require(start < end, 'INVALID_DATASET_INTERVAL')
    for sequence in (dataset.decision_times, dataset.records, dataset.snapshots,
                     dataset.recorded_outputs, dataset.evidence):
        require(isinstance(sequence, tuple), 'IMMUTABLE_DATASET_REQUIRED')
    times = tuple(utc_timestamp(value) for value in dataset.decision_times)
    require(bool(times) and times == tuple(sorted(set(times)))
            and all(start <= value <= end for value in times), 'INVALID_DECISION_TIMES')
    require(bool(dataset.evidence), 'DATASET_PROVENANCE_REQUIRED')
    for ref in dataset.evidence:
        check_ref(ref, dataset.scope.environment)
    seen = set()
    for record in dataset.records:
        require(isinstance(record, TemporalRecord), 'TYPED_TEMPORAL_RECORD_REQUIRED')
        require(record.category in ('NEWS', 'CONSTITUENT', 'FEE_SCHEDULE')
                and bool(record.logical_key) and bool(record.source)
                and type(record.revision) is int and record.revision >= 1
                and type(record.content) is str, 'INVALID_TEMPORAL_RECORD')
        published, revised, downloaded = map(utc_timestamp,
            (record.published_at, record.revised_at, record.downloaded_at))
        utc_timestamp(record.effective_at)
        require(published <= revised <= downloaded, 'INCONSISTENT_VISIBILITY_CLOCKS')
        key = record.source, record.category, record.logical_key, record.revision
        require(key not in seen, 'DUPLICATE_FACT_REVISION')
        seen.add(key)
    seen.clear()
    for snapshot in dataset.snapshots:
        require(snapshot.scope == dataset.scope and snapshot.coverage.scope == dataset.scope,
                'SNAPSHOT_SCOPE_MISMATCH')
        check_ref(snapshot.ref, dataset.scope.environment)
        require(snapshot.ref.hash == snapshot_content_hash(snapshot), 'SNAPSHOT_HASH_MISMATCH')
        require(snapshot.ref.id not in seen, 'DUPLICATE_SNAPSHOT')
        seen.add(snapshot.ref.id)
        occurred, received, published, revised = map(utc_timestamp, (
            snapshot.quote.occurred_at, snapshot.quote.received_at,
            snapshot.published_at, snapshot.revision_visible_at))
        require(occurred <= received and occurred <= published <= revised,
                'INCONSISTENT_SNAPSHOT_CLOCKS')
        for ref in snapshot.private_refs + snapshot.economic_refs:
            check_ref(ref, dataset.scope.environment)
    seen.clear()
    for output in dataset.recorded_outputs:
        validate_decision(output)
        at = utc_timestamp(output.decision_at)
        require(at in times and at not in seen, 'DUPLICATE_OR_UNSCHEDULED_DECISION')
        seen.add(at)


def validate_decision(output: RecordedDecision) -> None:
    require(isinstance(output, RecordedDecision), 'TYPED_DECISION_REQUIRED')
    require(output.kind in ('FORWARD_RECORDED', 'NEW_MODEL_INFERENCE'), 'INVALID_DECISION_KIND')
    require(utc_timestamp(output.recorded_at) >= utc_timestamp(output.decision_at),
            'DECISION_RECORDED_BEFORE_INPUT')
    require(all(is_digest(value) for value in (output.view_hash, output.prompt_hash, output.rule_hash))
            and bool(output.model_fingerprint), 'DECISION_FINGERPRINT_REQUIRED')
    require(output.cost is None or (isinstance(output.cost, t.Money) and output.cost.amount >= 0),
            'INVALID_MODEL_COST')
    ContractCodec().encode(output.proposal)


class DatasetArchive:
    """Content-addressed offline archive, no file paths or network resolution."""
    def __init__(self):
        self._datasets: dict[str, tuple[t.DatasetRef, Dataset]] = {}

    def put(self, dataset: Dataset) -> t.DatasetRef:
        check_dataset(dataset)
        digest = fingerprint(dataset)
        ref = t.DatasetRef(id='dataset:' + digest, hash=digest,
            environment=dataset.scope.environment, start=dataset.start, end=dataset.end,
            evidence=dataset.evidence)
        self._datasets[ref.id] = (ref, dataset)
        return ref

    def load(self, ref: t.DatasetRef) -> Dataset:
        check_offline(ref.environment)
        stored = self._datasets.get(ref.id)
        require(stored is not None and stored[0] == ref, 'DATASET_REFERENCE_MISMATCH')
        dataset = stored[1]
        require(fingerprint(dataset) == ref.hash, 'DATASET_HASH_MISMATCH')
        return dataset

    def artifact_json(self, ref: t.DatasetRef) -> str:
        dataset = self.load(ref)
        return canonical_json({'dataset': domain_payload(dataset), 'sha256': ref.hash})


@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentMetadata:
    prompt_hash: str
    rule_hash: str
    cost_assumptions: t.MetricAssumptions


@dataclass(frozen=True, slots=True, kw_only=True)
class ExperimentArtifact:
    experiment_id: str
    kind: Literal['RECORDED_DECISION_REPLAY', 'NEW_MODEL_INFERENCE']
    report: t.ExperimentReport
    metadata: ExperimentMetadata
    views: tuple[DecisionView, ...]
    outputs: tuple[RecordedDecision, ...]
    limitations: tuple[str, ...]
    native_matching: Literal['NOT_RUN'] = 'NOT_RUN'


class ExperimentArchive:
    def __init__(self):
        self._artifacts: dict[str, ExperimentArtifact] = {}

    def put(self, artifact: ExperimentArtifact) -> None:
        existing = self._artifacts.get(artifact.experiment_id)
        require(existing is None or existing == artifact, 'EXPERIMENT_ID_CONFLICT')
        self._artifacts[artifact.experiment_id] = artifact

    def get(self, run_id: str) -> ExperimentArtifact:
        return self._artifacts[run_id]

    def artifact_json(self, run_id: str) -> str:
        artifact = self.get(run_id)
        return canonical_json({'artifact': domain_payload(artifact), 'sha256': fingerprint(artifact)})


@dataclass(frozen=True, slots=True, kw_only=True)
class NativeBuildMapping:
    """Descriptive lock, not a qualification verdict or runnable adapter."""
    build_digest: str
    compatibility_digest: str
    environment_digest: str
    evidence: t.EvidenceRef

    def validate(self, build_digest: str, environment: t.Environment) -> None:
        require(self.build_digest == build_digest and all(is_digest(value) for value in (
            self.build_digest, self.compatibility_digest, self.environment_digest)), 'NATIVE_BUILD_LOCK_MISMATCH')
        check_ref(self.evidence, environment)


def native_matching_unavailable(mapping: NativeBuildMapping | None, build_digest: str,
                                environment: t.Environment) -> None:
    if mapping is None:
        raise ResearchError('NATIVE_BUILD_MAPPING_REQUIRED')
    mapping.validate(build_digest, environment)
    raise ResearchError('NATIVE_MATCHING_NOT_RUN')
