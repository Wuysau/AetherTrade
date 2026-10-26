"""Deterministic output replay and explicitly separate offline re-inference.

The injected inference function is trusted host code, not a sandbox. It only
receives a point-in-time view; the runtime supplies no SDK, credentials or
network client. Paid providers require the IP-18/IP-19 host integration.
"""
from collections.abc import Callable
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.market.provenance import is_digest

from .experiments import (Dataset, DatasetArchive, DecisionView, ExperimentArchive,
    ExperimentArtifact, ExperimentMetadata, RecordedDecision, check_dataset, check_ref, fingerprint,
    require, validate_decision)
from .metrics import MetricService


def point_in_time(dataset: Dataset, as_of: str) -> DecisionView:
    check_dataset(dataset)
    cutoff = utc_timestamp(as_of)
    require(utc_timestamp(dataset.start) <= cutoff <= utc_timestamp(dataset.end), 'AS_OF_OUTSIDE_DATASET')
    latest = {}
    for record in dataset.records:
        if utc_timestamp(record.reference(dataset.scope.environment).visible_at) > cutoff:
            continue
        key = record.source, record.category, record.logical_key
        previous = latest.get(key)
        if previous is None or (utc_timestamp(record.revised_at), record.revision) > (
                utc_timestamp(previous.revised_at), previous.revision):
            latest[key] = record
    records = tuple(latest[key] for key in sorted(latest))
    snapshots = []
    missing = []
    for packet in dataset.snapshots:
        times = (packet.ref.visible_at, packet.quote.occurred_at, packet.quote.received_at,
                 packet.published_at, packet.revision_visible_at,
                 *(ref.visible_at for ref in packet.private_refs + packet.economic_refs))
        if any(utc_timestamp(value) > cutoff for value in times):
            continue
        snapshots.append(packet)
        if not (packet.coverage.query_succeeded and packet.coverage.pagination_complete
                and packet.coverage.supported) or packet.coverage.missing:
            missing.append('SNAPSHOT_COVERAGE_INCOMPLETE:' + packet.ref.id)
    snapshots.sort(key=lambda packet: (utc_timestamp(packet.quote.occurred_at), packet.ref.id))
    if not snapshots:
        missing.append('NO_VISIBLE_SNAPSHOT')
    return DecisionView(as_of=as_of, snapshots=tuple(snapshots), records=records, missing=tuple(missing))


class ReplayService:
    def __init__(self, datasets: DatasetArchive, artifacts: ExperimentArchive,
                 metadata: ExperimentMetadata, *,
                 infer: Callable[[DecisionView], RecordedDecision] | None = None):
        self.datasets, self.artifacts, self.metadata, self.infer = datasets, artifacts, metadata, infer

    def run(self, config: t.ExperimentConfig, dataset: t.DatasetRef) -> t.ExperimentReport:
        require(config.mode in ('REPLAY_OUTPUTS', 'REINFER'), 'UNKNOWN_EXPERIMENT_MODE')
        require(is_digest(config.build_digest) and is_digest(config.config_hash)
                and bool(config.model_fingerprint), 'EXPERIMENT_FINGERPRINT_REQUIRED')
        require(is_digest(self.metadata.prompt_hash) and is_digest(self.metadata.rule_hash),
                'PROMPT_AND_RULE_FINGERPRINT_REQUIRED')
        data = self.datasets.load(dataset)
        for ref in self.metadata.cost_assumptions.evidence:
            check_ref(ref, dataset.environment)
        views = tuple(point_in_time(data, at) for at in data.decision_times)
        outputs = []
        if config.mode == 'REPLAY_OUTPUTS':
            require(len(data.recorded_outputs) == len(views), 'RECORDED_OUTPUTS_INCOMPLETE')
            by_time = {utc_timestamp(item.decision_at): item for item in data.recorded_outputs}
            for view in views:
                output = by_time[utc_timestamp(view.as_of)]
                require(output.kind == 'FORWARD_RECORDED', 'REINFERENCE_IS_NOT_FORWARD_RECORDING')
                outputs.append(output)
            require(config.proposal_refs == tuple(output.reference(dataset.environment) for output in outputs),
                    'RECORDED_OUTPUT_REFERENCE_MISMATCH')
            kind = 'RECORDED_DECISION_REPLAY'
        else:
            require(not config.proposal_refs, 'REINFERENCE_CANNOT_REUSE_OUTPUT_REFS')
            require(self.infer is not None, 'OFFLINE_INFERENCE_ADAPTER_REQUIRED')
            kind = 'NEW_MODEL_INFERENCE'
            for view in views:
                output = self.infer(view)
                require(output.kind == 'NEW_MODEL_INFERENCE', 'INFERENCE_KIND_MISMATCH')
                outputs.append(output)
        for view, output in zip(views, outputs):
            validate_decision(output)
            require(output.decision_at == view.as_of and output.view_hash == fingerprint(view),
                    'DECISION_VISIBLE_INPUT_MISMATCH')
            require(output.model_fingerprint == config.model_fingerprint
                    and output.prompt_hash == self.metadata.prompt_hash
                    and output.rule_hash == self.metadata.rule_hash, 'DECISION_FINGERPRINT_MISMATCH')
            visible_ids = {packet.ref.id for packet in view.snapshots}
            visible_ids.update(record.reference(dataset.environment).id for record in view.records)
            require(bool(view.snapshots) and bool(output.proposal.evidence_refs)
                    and set(output.proposal.evidence_refs) <= visible_ids,
                    'PROPOSAL_REFERENCES_NONVISIBLE_INPUT')
            require(output.proposal.instrument_ref == data.scope.instrument_uid,
                    'PROPOSAL_INSTRUMENT_MISMATCH')
        # A replay of proposals is not fill/equity evidence. No fabricated P&L.
        metrics = MetricService().evaluate(t.EventRange(scope=data.scope,
            first_sequence=0, last_sequence=0, events=()), self.metadata.cost_assumptions)
        missing = tuple(sorted(set(metrics.missing + tuple(
            reason for view in views for reason in view.missing)
            + (('MODEL_CALL_COST_UNKNOWN',) if any(output.cost is None for output in outputs) else ()))))
        identity = canonical_hash({'kind': kind, 'config': domain_payload(config),
            'dataset': domain_payload(dataset), 'metadata': domain_payload(self.metadata),
            'outputs': [fingerprint(item) for item in outputs]})
        run_id = 'replay:' + identity if config.mode == 'REPLAY_OUTPUTS' else 'reinfer:' + str(uuid4())
        report = t.ExperimentReport(run_id=run_id, config=config, dataset=dataset,
            metrics=metrics, missing=missing)
        limitations = ('POSSIBLE_MODEL_HISTORICAL_KNOWLEDGE_LEAKAGE',
            'OFFLINE_ARTIFACT_NOT_TRADING_EVIDENCE', 'FORWARD_RECORD_PROVENANCE_REQUIRES_TRUSTED_ARCHIVE',
            'NATIVE_MATCHING_NOT_RUN', 'NO_LIVE_OR_CAPITAL_ESCALATION')
        if config.mode == 'REINFER':
            limitations += ('NEW_INFERENCE_IS_NOT_A_CONTEMPORANEOUS_PREDICTION',)
        self.artifacts.put(ExperimentArtifact(experiment_id=run_id, kind=kind, report=report,
            metadata=self.metadata, views=views, outputs=tuple(outputs), limitations=limitations))
        return report
