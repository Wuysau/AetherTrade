"""Typed financial content archive, not admission or a trusted source reader.

Only static contract types are restored. A future bundle must independently
authenticate the bounded intake and every source under owner/collateral locks;
these hashes and constructor checks never issue a permit or financial ALLOW.
The full independent ProgramRiskFacts type is not yet present. Conversion to a
financial bundle remains explicitly denied rather than aliasing budget facts.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.wire import RiskPolicy
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_snapshots import OrderRuleSnapshot, OrderCapabilitySnapshot
from trading_system.contracts.program_financial import (FinancialSource,FinancialBounds,FinancialUnit,FinancialTarget,
    ProgramFinancialIntent,require)
from trading_system.risk.program_exposure import ProgramBudgetFacts,ProgramExposure
from trading_system.risk.terms import RiskTerms,RiskMetrics,metric_binding_codes
from trading_system.storage.models import decode
from .program_compilation import ProgramCompilation
from .program_target_allocation import ProgramTargetFacts,ProgramTargetAllocation
from .product_rules import TrustedProductRules


def digest(record):return canonical_hash(domain_payload(record))


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFinancialArchive:
    contract_version: Literal['2026-10-29']
    record_kind: Literal['PROGRAM_FINANCIAL_ARCHIVE']
    intent_id: str
    source: FinancialSource
    bounds: FinancialBounds
    compilation: ProgramCompilation
    rules: OrderRuleSnapshot
    capabilities: OrderCapabilitySnapshot
    profile: TrustedProductRules
    book: t.BookSnapshot
    target_facts: ProgramTargetFacts
    allocation: ProgramTargetAllocation
    budget_facts: ProgramBudgetFacts
    exposure: ProgramExposure
    risk_terms: RiskTerms
    risk_metrics: RiskMetrics
    risk_policy: RiskPolicy

    @property
    def execution_authorized(self)->Literal[False]:return False


def validate_archive(archive):
    require(type(archive) is ProgramFinancialArchive,'FINANCIAL_ARCHIVE_TYPE_REQUIRED')
    require(archive.contract_version=='2026-10-29' and archive.record_kind=='PROGRAM_FINANCIAL_ARCHIVE',
        'FINANCIAL_ARCHIVE_VERSION_UNSUPPORTED')
    p=archive.compilation;a=archive.allocation;f=archive.target_facts;e=archive.exposure;m=archive.risk_metrics
    terms=archive.risk_terms;source=archive.source;codec=VersionedOrderCodec();context=source.context
    require(p.original_proposal.program==p.original_program
        and p.proposal_hash==codec.hash('order-proposal',p.original_proposal)==source.source_hash
        and p.program_hash==codec.hash('order-program',p.original_program)
        and source.source_version==p.original_proposal.contract_version,'FINANCIAL_ARCHIVE_SOURCE_MISMATCH')
    require(p.scope==archive.book.scope==f.unit_binding.scope==archive.rules.scope==archive.capabilities.scope
        ==archive.profile.scope==terms.scope==a.terms.scope==archive.budget_facts.scope,
        'FINANCIAL_ARCHIVE_SCOPE_MISMATCH')
    require(digest(p)==f.compilation_hash==a.compilation_hash==archive.budget_facts.compilation_hash==e.compilation_hash
        and a.proposal_hash==p.proposal_hash and a.program_hash==p.program_hash,'FINANCIAL_ARCHIVE_COMPILATION_MISMATCH')
    require(p.rules_hash==codec.hash('order-rule-snapshot',archive.rules)==a.rules_hash
        and f.rules==archive.rules and p.capabilities_hash==codec.hash('order-capability-snapshot',archive.capabilities)
        and p.profile_hash==digest(archive.profile),'FINANCIAL_ARCHIVE_SNAPSHOT_MISMATCH')
    require(digest(archive.book)==f.book_hash==a.book_hash and archive.book.sequence==f.book_sequence
        and context==f.context==f.inputs.context and digest(context)==a.context_hash
        and digest(f)==a.facts_hash and digest(archive.budget_facts)==e.facts_hash,
        'FINANCIAL_ARCHIVE_FACTS_MISMATCH')
    require(archive.intent_id==terms.decision_id and terms.source.kind=='ORDER_PROPOSAL'
        and terms.source.version==source.source_version and terms.source.hash==source.source_hash
        and terms.authorization_id==context.authorization_id and terms.owner_strategy_id==f.owner.owner_strategy_id
        ==a.terms.owner_strategy_id and terms.ownership_epoch==f.owner.ownership_epoch==context.ownership_epoch
        and terms.policy_revision==f.owner.policy_revision==context.policy_revision
        and terms.collateral_group_id==f.collateral_group_id==a.terms.collateral_group_id==archive.budget_facts.collateral_group_id,
        'FINANCIAL_ARCHIVE_OWNER_OR_TERMS_MISMATCH')
    original=p.original_proposal
    require(terms.action==original.action and terms.target_side==a.terms.target_side==original.target_side
        and terms.requested_leverage==original.requested_leverage
        and terms.submission_deadline==original.program.submit_valid_until
        and a.terms.submission_deadline==terms.submission_deadline,
        'FINANCIAL_ARCHIVE_SOURCE_TERMS_MISMATCH')
    bridge=f.unit_binding
    # Effective authority may be shorter than the immutable proposal. Bind the
    # actual qualified windows, not an equality that rejects fresh short facts
    # or a self-consistent allocation hash that hides a longer forged deadline.
    target_deadlines=[original.valid_until,f.valid_until,bridge.valid_until,archive.rules.valid_until]
    if f.normalization_price is not None:target_deadlines.append(f.normalization_price.valid_until)
    target_ceiling=min(map(utc_timestamp,target_deadlines))
    now=utc_timestamp(f.inputs.clock.utc_now);allocation_until=utc_timestamp(a.terms.valid_until)
    require(now<allocation_until<=target_ceiling,'FINANCIAL_ARCHIVE_ALLOCATION_TTL_UNBOUND')
    effective_ceiling=min(target_ceiling,allocation_until,*map(utc_timestamp,(
        archive.capabilities.valid_until,archive.profile.valid_until,archive.budget_facts.valid_until,m.valid_until)))
    require(now<utc_timestamp(terms.valid_until)<=effective_ceiling,'FINANCIAL_ARCHIVE_EFFECTIVE_TTL_UNBOUND')
    FinancialUnit(native_unit=bridge.native_unit,ledger_unit=bridge.ledger_unit,
        ledger_units_per_native_unit=bridge.ledger_units_per_native_unit,
        native_multiplier=bridge.native_multiplier,ledger_multiplier=bridge.ledger_multiplier)
    require(bridge.compilation_hash==digest(p) and bridge.ledger_unit==
        {t.Product.SPOT:'BASE',t.Product.USD_M_PERP:'CONTRACT',t.Product.US_EQUITY:'SHARE'}[p.scope.product]
        and a.terms.target.unit==bridge.ledger_unit
        and all(n.quantity_unit==bridge.native_unit and Decimal(n.quantity_multiplier)==bridge.native_multiplier for n in p.nodes),
        'FINANCIAL_ARCHIVE_UNIT_BINDING_MISMATCH')
    FinancialTarget(side=a.terms.target_side,normalized=a.terms.target,actual=a.delta.actual,
        working=a.delta.working,remaining=a.delta.remaining,residual=a.target_residual)
    require(terms.target==a.terms.target and terms.target_side==a.terms.target_side
        and terms.maximum_planned_loss==archive.bounds.maximum_planned_loss
        and terms.maximum_holding_seconds==archive.bounds.maximum_holding_seconds
        and terms.protection_policy==archive.bounds.protection_policy
        and terms.stop_price==archive.bounds.stop_price and terms.stop_trigger==archive.bounds.stop_trigger
        and terms.review_due_at==archive.bounds.review_due_at,'FINANCIAL_ARCHIVE_BOUNDS_MISMATCH')
    require(m.allocation_hash==digest(a)
        and archive.risk_policy.revision==terms.policy_revision
        and archive.risk_policy.collateral_group_id==terms.collateral_group_id,
        'FINANCIAL_ARCHIVE_RISK_BASIS_MISMATCH')
    require(not metric_binding_codes(terms,m,archive.book,context.snapshot_ref,f.inputs.clock),
        'FINANCIAL_ARCHIVE_RISK_METRICS_INCOMPLETE_OR_UNBOUND')


class ProgramFinancialArchiveCodec:
    def decode(self,payload):
        archive=decode(ProgramFinancialArchive,payload)
        validate_archive(archive)
        return archive

    def encode(self,archive):
        require(type(archive) is ProgramFinancialArchive,'FINANCIAL_ARCHIVE_TYPE_REQUIRED')
        payload=domain_payload(archive)
        require(self.decode(payload)==archive,'FINANCIAL_ARCHIVE_TYPE_DOWNGRADE')
        return payload

    def hash(self,archive):return canonical_hash(self.encode(archive))


def financial_intent_from_archive(archive):
    ProgramFinancialArchiveCodec().hash(archive)
    # The forthcoming independent ProgramRiskFacts includes funding/holding/
    # protection/count/price inputs absent from ProgramBudgetFacts. Retain the
    # existing typed evidence, but never relabel its budget digest as risk proof.
    raise ValueError('PROGRAM_RISK_FACTS_ARCHIVE_REQUIRED')


def validate_financial_archive(intent,archive):
    require(type(intent) is ProgramFinancialIntent and financial_intent_from_archive(archive)==intent,
        'FINANCIAL_INTENT_ARCHIVE_BINDING_MISMATCH')


def financial_risk_basis(intent,archive):
    validate_financial_archive(intent,archive)
    raise ValueError('PROGRAM_RISK_FACTS_ARCHIVE_REQUIRED')
