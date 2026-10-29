"""Separately versioned full risk-facts archive; recomputation is not authority.

The original known-content archive and its blocked conversion are unchanged.
Only the real shared evaluator may validate the stored assessment. Source origin,
current owner, collateral admission and one-write permits remain outer duties.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.ledger_intents import LedgerIntentCodec
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.program_financial import (FinancialBook,FinancialUnit,FinancialTarget,FinancialBounds,
    FinancialHashes,ProgramFinancialIntent,ProgramFinancialRiskBasis,require)
from trading_system.risk.evaluator import RiskEvaluator
from trading_system.risk.program_assessment import ProgramRiskFacts,ProgramRiskDecision
from trading_system.risk.program_exposure import derive_program_exposure
from trading_system.risk.terms import RiskMetrics,metric_binding_codes,metrics_completeness
from trading_system.risk.reduction import ReductionRiskMetrics
from trading_system.risk.valuation import Valuation
from trading_system.storage.models import decode
from .program_compilation import compile_order_program
from .program_target_allocation import compile_program_target
from .program_financial_records import ProgramFinancialArchive


def digest(record):return canonical_hash(domain_payload(record))


def _same(left,right):
    if left is None or right is None:return left is right
    # Python equality conflates False/0 and Decimal0/-0. Their canonical and
    # venue representations differ; a replay must bind the actual typed bytes.
    return type(left) is type(right) and digest(left)==digest(right)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFinancialAssessmentArchive(ProgramFinancialArchive):
    record_kind: Literal['PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE']
    archive_revision: Literal[1]
    risk_metrics: RiskMetrics | ReductionRiskMetrics | None
    risk_facts: ProgramRiskFacts
    assessment: ProgramRiskDecision


def replay_assessment(archive):
    require(type(archive) is ProgramFinancialAssessmentArchive,'ASSESSMENT_ARCHIVE_TYPE_REQUIRED')
    require(archive.contract_version=='2026-10-29'
        and archive.record_kind=='PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE'
        and type(archive.archive_revision) is int and archive.archive_revision==1,'ASSESSMENT_ARCHIVE_VERSION_UNSUPPORTED')
    f=archive.risk_facts;p=archive.compilation;terms=archive.risk_terms;source=archive.source
    require(type(f) is ProgramRiskFacts and type(archive.assessment) is ProgramRiskDecision,
        'ASSESSMENT_ARCHIVE_FULL_RISK_TYPES_REQUIRED')
    require(_same(f.target_facts,archive.target_facts) and _same(f.budget_facts,archive.budget_facts)
        and _same(f.rules,archive.rules) and _same(f.capabilities,archive.capabilities) and _same(f.profile,archive.profile),
        'ASSESSMENT_ARCHIVE_FACTS_MISMATCH')
    require(archive.book.scope==p.scope==terms.scope==f.inputs.scope
        and archive.intent_id==terms.decision_id==f.inputs.intent_id
        and _same(source.context,f.target_facts.context)
        and source.source_hash==p.proposal_hash==terms.source.hash
        and source.source_version==p.original_proposal.contract_version==terms.source.version
        and terms.source.kind=='ORDER_PROPOSAL','ASSESSMENT_ARCHIVE_SOURCE_IDENTITY_MISMATCH')
    codec=VersionedOrderCodec()
    try:
        compiled=compile_order_program(p.original_proposal,f.rules,f.capabilities,profile=f.profile,
            scope=terms.scope,now=f.inputs.clock.utc_now,expected_build_digest=f.expected_build_digest,
            expected_rule_revision=f.inputs.instrument.rule_revision,
            expected_capabilities_hash=codec.hash('order-capability-snapshot',f.capabilities),
            expected_instrument_ref=f.expected_instrument_ref,allow_round_down=f.inputs.allow_round_down)
        require(_same(compiled,p),'ASSESSMENT_ARCHIVE_COMPILATION_REPLAY_MISMATCH')
        allocation=compile_program_target(compiled,archive.book,f.target_facts)
        require(_same(allocation,archive.allocation),'ASSESSMENT_ARCHIVE_ALLOCATION_REPLAY_MISMATCH')
        exposure=derive_program_exposure(compiled,f.budget_facts,now=f.inputs.clock.utc_now,
            valuation=Valuation(archive.risk_policy.valuation_currency,f.inputs.fx_rates))
        require(_same(exposure,archive.exposure),'ASSESSMENT_ARCHIVE_EXPOSURE_REPLAY_MISMATCH')
    except ValueError as exc:
        if str(exc).startswith('ASSESSMENT_ARCHIVE_'):raise
        raise ValueError('ASSESSMENT_ARCHIVE_SOURCE_REPLAY_FAILED:'+str(exc)) from exc
    method=getattr(RiskEvaluator,'evaluate_program',None)
    if not callable(method):raise ValueError('PROGRAM_RISK_ASSESSMENT_NOT_AVAILABLE')
    # No supplied callback/ALLOW boolean substitutes for this real calculation.
    actual=RiskEvaluator(f.inputs).evaluate_program(terms,compiled,allocation,exposure,
        archive.book,archive.risk_policy,f)
    require(type(actual) is ProgramRiskDecision and _same(actual,archive.assessment),
        'ASSESSMENT_ARCHIVE_DECISION_REPLAY_MISMATCH')
    require(actual.source==terms.source and actual.terms_hash==digest(terms)
        and actual.compilation_hash==digest(compiled) and actual.allocation_hash==digest(allocation)
        and actual.exposure_hash==digest(exposure) and actual.policy_hash==digest(archive.risk_policy)
        and _same(actual.metrics,archive.risk_metrics) and actual.facts_hash==digest(f),
        'ASSESSMENT_ARCHIVE_METRICS_OR_FULL_FACTS_MISMATCH')
    if actual.verdict=='ALLOW':
        require(actual.metrics is not None and actual.metrics.facts_hash==digest(f)
            and actual.metrics.allocation_hash==digest(allocation)
            and not metric_binding_codes(terms,actual.metrics,archive.book,source.context.snapshot_ref,f.inputs.clock),
            'ASSESSMENT_ARCHIVE_ALLOW_METRICS_UNBOUND')
    if type(actual.metrics) is ReductionRiskMetrics:
        require(f.reduction_proof is not None
            and actual.metrics.reduction_proof_hash==digest(f.reduction_proof)
            and {'PERSIST_RESIDUAL_RISK_LOCK','RETAIN_MANAGE_UNTIL_FLAT','CURRENT_LOCKED_ACTION_VERIFIER_REQUIRED'}
                <=set(actual.obligations),'ASSESSMENT_ARCHIVE_RESIDUAL_OBLIGATIONS_REQUIRED')
    return actual


def _tagged_metrics(payload):
    """Select an exact static metric kind before generic union restoration.

    A false-protection/unknown-loss record is never normalized into the base
    metric shape. Unknown tags and removed reduction fields remain errors.
    """
    if payload is None:return None
    require(type(payload) is dict,'ASSESSMENT_ARCHIVE_METRICS_OBJECT_REQUIRED')
    tag=payload.get('metric_kind')
    if tag is None:
        require('contract_version' not in payload,'ASSESSMENT_ARCHIVE_METRIC_KIND_UNSUPPORTED')
        metrics=decode(RiskMetrics,payload)
    else:
        require(tag=='BOUNDED_REDUCTION_METRICS' and payload.get('contract_version')=='2026-10-29',
            'ASSESSMENT_ARCHIVE_METRIC_KIND_UNSUPPORTED')
        metrics=decode(ReductionRiskMetrics,payload)
    require(not metrics_completeness(metrics),'ASSESSMENT_ARCHIVE_METRIC_SHAPE_INVALID')
    return metrics


class ProgramFinancialAssessmentArchiveCodec:
    def decode(self,payload):
        require(type(payload) is dict and type(payload.get('assessment')) is dict,
            'ASSESSMENT_ARCHIVE_OBJECT_REQUIRED')
        expected=_tagged_metrics(payload.get('risk_metrics'))
        expected_decision=_tagged_metrics(payload['assessment'].get('metrics'))
        archive=decode(ProgramFinancialAssessmentArchive,payload)
        require(_same(archive.risk_metrics,expected) and _same(archive.assessment.metrics,expected_decision),
            'ASSESSMENT_ARCHIVE_METRIC_TYPE_DOWNGRADE')
        replay_assessment(archive)
        return archive

    def encode(self,archive):
        require(type(archive) is ProgramFinancialAssessmentArchive,'ASSESSMENT_ARCHIVE_TYPE_REQUIRED')
        payload=domain_payload(archive)
        require(_same(self.decode(payload),archive),'ASSESSMENT_ARCHIVE_TYPE_DOWNGRADE')
        return payload

    def hash(self,archive):return canonical_hash(self.encode(archive))


def financial_intent_from_assessment_archive(archive):
    archive_hash=ProgramFinancialAssessmentArchiveCodec().hash(archive)
    decision=archive.assessment;m=decision.metrics
    require(decision.verdict=='ALLOW' and m is not None and not decision.rejection_codes
        and not archive.allocation.blockers and not archive.exposure.blockers,
        'PROGRAM_RISK_ASSESSMENT_NOT_ALLOW')
    terms=archive.risk_terms;p=archive.compilation;f=archive.target_facts;a=archive.allocation
    require(not metric_binding_codes(terms,m,archive.book,archive.source.context.snapshot_ref,f.inputs.clock),
        'ASSESSMENT_ARCHIVE_CURRENT_METRICS_UNBOUND')
    expected_bounds=FinancialBounds(maximum_planned_loss=terms.maximum_planned_loss,
        maximum_holding_seconds=terms.maximum_holding_seconds,protection_policy=terms.protection_policy,
        stop_price=terms.stop_price,stop_trigger=terms.stop_trigger,review_due_at=terms.review_due_at)
    require(_same(expected_bounds,archive.bounds),
        'ASSESSMENT_ARCHIVE_BOUNDS_MISMATCH')
    bridge=f.unit_binding
    until=min((terms.valid_until,decision.expires_at,m.valid_until),key=utc_timestamp)
    return ProgramFinancialIntent(contract_version='2026-10-29',record_kind='PROGRAM_FINANCIAL_INTENT',
        intent_id=archive.intent_id,scope=p.scope,venue=a.terms.venue,collateral_group_id=f.collateral_group_id,
        owner_strategy_id=f.owner.owner_strategy_id,strategy_revision=archive.source.context.strategy_revision,
        ownership_epoch=f.owner.ownership_epoch,risk_policy_revision=f.owner.policy_revision,
        authorization_id=terms.authorization_id,source=archive.source,proposal=p.original_proposal,
        book=FinancialBook(sequence=archive.book.sequence,hash=digest(archive.book),basis_fingerprint=archive.book.basis_fingerprint,
            snapshot_ref=archive.source.context.snapshot_ref,as_of=terms.as_of),
        unit=FinancialUnit(native_unit=bridge.native_unit,ledger_unit=bridge.ledger_unit,
            ledger_units_per_native_unit=bridge.ledger_units_per_native_unit,native_multiplier=bridge.native_multiplier,
            ledger_multiplier=bridge.ledger_multiplier),
        target=FinancialTarget(side=a.terms.target_side,normalized=a.terms.target,actual=a.delta.actual,
            working=a.delta.working,remaining=a.delta.remaining,residual=a.target_residual),bounds=archive.bounds,
        hashes=FinancialHashes(proposal=p.proposal_hash,program=p.program_hash,compilation=digest(p),rules=p.rules_hash,
            capabilities=p.capabilities_hash,profile=p.profile_hash,target_facts=digest(f),allocation=digest(a),
            budget_facts=digest(archive.budget_facts),exposure=digest(archive.exposure),risk_facts=digest(archive.risk_facts),
            risk_terms=digest(terms),risk_metrics=digest(m),policy=digest(archive.risk_policy),archive=archive_hash),
        created_at=terms.created_at,valid_until=until)


def financial_risk_basis_from_assessment_archive(intent,archive):
    require(type(intent) is ProgramFinancialIntent and _same(financial_intent_from_assessment_archive(archive),intent),
        'ASSESSMENT_ARCHIVE_INTENT_BINDING_MISMATCH')
    h=intent.hashes
    return ProgramFinancialRiskBasis(contract_version='2026-10-29',record_kind='PROGRAM_FINANCIAL_RISK_BASIS',
        intent_id=intent.intent_id,scope=intent.scope,intent_hash=LedgerIntentCodec().hash(intent),source_hash=h.proposal,
        book_hash=intent.book.hash,allocation_hash=h.allocation,terms_hash=h.risk_terms,metrics_hash=h.risk_metrics,
        risk_facts_hash=h.risk_facts,policy_hash=h.policy,archive_hash=h.archive,
        incremental_requirements=archive.assessment.metrics.required_assets)
