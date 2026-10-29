"""Versioned full explicit-band assessment replay, never financial authority."""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.wire import RiskPolicy
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.program_financial import require
from trading_system.contracts.program_financial import FinancialBounds,FinancialBook,FinancialUnit
from trading_system.contracts.quote_financial import (QuoteFinancialSourceV1,QuoteFinancialIntentV1,
    QuoteFinancialHashesV1,QuoteFinancialRiskBasisV1)
from trading_system.contracts.ledger_intents import LedgerIntentCodec
from trading_system.contracts.canonical import utc_timestamp
from trading_system.storage.models import decode
from trading_system.risk.evaluator import RiskEvaluator
from trading_system.risk.quote_assessment import QuoteRiskTermsV1,QuoteRiskFactsV1,QuoteRiskDecisionV1
from .quote_program_compilation import QuoteProgramCompilationV1
from .quote_target_allocation import QuoteTargetAllocationV1

def digest(value):return canonical_hash(domain_payload(value))


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteAssessmentArchiveV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_ASSESSMENT_ARCHIVE_V1']
    intent_id:str
    source:QuoteFinancialSourceV1
    compilation:QuoteProgramCompilationV1
    book:t.BookSnapshot
    allocation:QuoteTargetAllocationV1
    risk_terms:QuoteRiskTermsV1
    risk_facts:QuoteRiskFactsV1
    risk_policy:RiskPolicy
    assessment:QuoteRiskDecisionV1

    @property
    def execution_authorized(self)->Literal[False]:return False

    @property
    def capabilities(self):return self.risk_facts.target_facts.capabilities

    @property
    def profile(self):return self.risk_facts.target_facts.profile

    @property
    def rules(self):return self.risk_facts.target_facts.scope_facts.rules


def replay_quote_assessment(archive):
    require(type(archive) is QuoteAssessmentArchiveV1 and archive.contract_version=='2026-10-30'
        and archive.record_kind=='QUOTE_ASSESSMENT_ARCHIVE_V1','QUOTE_ARCHIVE_TYPE_OR_VERSION_REQUIRED')
    require(archive.intent_id==archive.risk_terms.decision_id==archive.risk_facts.inputs.intent_id,
        'QUOTE_ARCHIVE_IDENTITY_MISMATCH')
    p=archive.compilation;s=archive.source;f=archive.risk_facts.target_facts.scope_facts
    require(type(s) is QuoteFinancialSourceV1 and s.source_hash==p.proposal_hash
        and s.source_version==p.original_proposal.contract_version and digest(s.context)==digest(f.context),
        'QUOTE_ARCHIVE_SOURCE_MISMATCH')
    actual=RiskEvaluator(archive.risk_facts.inputs).evaluate_quote_program(archive.risk_terms,
        archive.compilation,archive.allocation,archive.book,archive.risk_policy,archive.risk_facts)
    require(type(archive.assessment) is QuoteRiskDecisionV1 and digest(actual)==digest(archive.assessment),
        'QUOTE_ARCHIVE_DECISION_REPLAY_MISMATCH')
    return actual


class QuoteAssessmentArchiveCodec:
    def decode(self,payload):
        archive=decode(QuoteAssessmentArchiveV1,payload)
        replay_quote_assessment(archive)
        return archive

    def encode(self,archive):
        replay_quote_assessment(archive)
        payload=domain_payload(archive)
        require(digest(self.decode(payload))==digest(archive),'QUOTE_ARCHIVE_TYPE_DOWNGRADE')
        return payload

    def hash(self,archive):return canonical_hash(self.encode(archive))


def quote_financial_intent(archive):
    archive_hash=QuoteAssessmentArchiveCodec().hash(archive);d=archive.assessment;m=d.metrics
    require(d.verdict=='ALLOW' and m is not None and not d.rejection_codes and not archive.allocation.blockers,
        'QUOTE_ASSESSMENT_NOT_ALLOW')
    p=archive.compilation;a=archive.allocation;f=archive.risk_facts;tms=archive.risk_terms
    s=f.target_facts.scope_facts;b=s.unit_binding
    return QuoteFinancialIntentV1(contract_version='2026-10-30',record_kind='QUOTE_FINANCIAL_INTENT_V1',
        intent_id=archive.intent_id,scope=p.scope,venue=s.rules.venue,collateral_group_id=tms.collateral_group_id,
        owner_strategy_id=tms.owner_strategy_id,strategy_revision=archive.source.context.strategy_revision,
        ownership_epoch=tms.ownership_epoch,risk_policy_revision=tms.policy_revision,authorization_id=tms.authorization_id,
        source=archive.source,proposal=p.original_proposal,
        book=FinancialBook(sequence=archive.book.sequence,hash=digest(archive.book),basis_fingerprint=archive.book.basis_fingerprint,
            snapshot_ref=archive.source.context.snapshot_ref,as_of=tms.as_of),
        unit=FinancialUnit(native_unit=b.native_unit,ledger_unit=b.ledger_unit,
            ledger_units_per_native_unit=b.ledger_units_per_native_unit,native_multiplier=b.native_multiplier,
            ledger_multiplier=b.ledger_multiplier),native_quote_amount=a.native_quote_amount,position=a.position,
        bounds=FinancialBounds(maximum_planned_loss=tms.maximum_planned_loss,maximum_holding_seconds=tms.maximum_holding_seconds,
            protection_policy=tms.protection_policy,stop_price=tms.stop_price,stop_trigger=tms.stop_trigger,review_due_at=tms.review_due_at),
        hashes=QuoteFinancialHashesV1(proposal=p.proposal_hash,program=p.program_hash,compilation=digest(p),rules=p.rules_hash,
            capabilities=p.capabilities_hash,profile=p.profile_hash,target_facts=digest(f.target_facts),allocation=digest(a),
            execution_bounds=digest(f.target_facts.bounds),cash_requirements=digest(a.cash),position_band=digest(a.position),
            risk_facts=digest(f),risk_terms=digest(tms),risk_metrics=digest(m),policy=digest(archive.risk_policy),archive=archive_hash),
        created_at=tms.created_at,valid_until=min((tms.valid_until,d.expires_at,m.valid_until),key=utc_timestamp))


def quote_financial_basis(intent,archive):
    require(type(intent) is QuoteFinancialIntentV1 and digest(quote_financial_intent(archive))==digest(intent),
        'QUOTE_FINANCIAL_INTENT_BINDING_MISMATCH')
    h=intent.hashes
    return QuoteFinancialRiskBasisV1(contract_version='2026-10-30',record_kind='QUOTE_FINANCIAL_RISK_BASIS_V1',
        intent_id=intent.intent_id,scope=intent.scope,intent_hash=LedgerIntentCodec().hash(intent),source_hash=h.proposal,
        book_hash=intent.book.hash,allocation_hash=h.allocation,terms_hash=h.risk_terms,metrics_hash=h.risk_metrics,
        risk_facts_hash=h.risk_facts,policy_hash=h.policy,archive_hash=h.archive,
        incremental_requirements=archive.assessment.metrics.required_assets)
