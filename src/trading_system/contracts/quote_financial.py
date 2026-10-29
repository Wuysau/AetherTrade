"""Distinct interval financial kinds in the existing immutable intent registry.

These records bind content. Only the SAME financial writer may authenticate
bounded source, current balances, owner, enforcement and real reservations.
"""
from dataclasses import dataclass,fields
from decimal import Decimal
from typing import Literal

from . import types as t
from .canonical import canonical_hash,domain_payload,utc_timestamp,decimal_string
from .program_financial import FinancialSource,FinancialBook,FinancialUnit,FinancialBounds,require,digest
from .order_program import identifier
from .quote_position_v1 import QuotePositionProposalV1
from trading_system.intents.position_value_band import PositionValueBandAssessmentV1


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteFinancialSourceV1(FinancialSource):
    source_kind:Literal['POSITION_VALUE_BAND_PROPOSAL']
    source_version:Literal['2026-10-30']

    def __post_init__(self):
        require(self.source_kind=='POSITION_VALUE_BAND_PROPOSAL' and self.source_version=='2026-10-30',
            'QUOTE_FINANCIAL_SOURCE_VERSION_REQUIRED')
        for value in (self.source_hash,self.intake_hash,self.context_hash):digest(value)
        identifier(self.intake_record_id);identifier(self.intake_transaction_id)
        require(self.context_hash==canonical_hash(domain_payload(self.context)),'FINANCIAL_CONTEXT_HASH_MISMATCH')


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteFinancialHashesV1:
    proposal:str
    program:str
    compilation:str
    rules:str
    capabilities:str
    profile:str
    target_facts:str
    allocation:str
    execution_bounds:str
    cash_requirements:str
    position_band:str
    risk_facts:str
    risk_terms:str
    risk_metrics:str
    policy:str
    archive:str

    def __post_init__(self):
        for field in fields(self):digest(getattr(self,field.name))


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteFinancialIntentV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_FINANCIAL_INTENT_V1']
    intent_id:str
    scope:t.ScopeKey
    venue:str
    collateral_group_id:str
    owner_strategy_id:str
    strategy_revision:int
    ownership_epoch:int
    risk_policy_revision:int
    authorization_id:str
    source:QuoteFinancialSourceV1
    proposal:QuotePositionProposalV1
    book:FinancialBook
    unit:FinancialUnit
    native_quote_amount:t.Money
    position:PositionValueBandAssessmentV1
    bounds:FinancialBounds
    hashes:QuoteFinancialHashesV1
    created_at:str
    valid_until:str

    def __post_init__(self):
        require(self.contract_version=='2026-10-30' and self.record_kind=='QUOTE_FINANCIAL_INTENT_V1',
            'QUOTE_FINANCIAL_INTENT_VERSION_REQUIRED')
        for value in (self.intent_id,self.venue,self.collateral_group_id,self.owner_strategy_id,self.authorization_id):identifier(value)
        require(self.scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),'FINANCIAL_ENVIRONMENT_DISABLED')
        require(all(type(v) is int and v>0 for v in (self.strategy_revision,self.ownership_epoch,self.risk_policy_revision)),
            'FINANCIAL_OWNER_REVISIONS_REQUIRED')
        c=self.source.context;p=self.proposal
        require(self.scope in c.scopes and c.environment==self.scope.environment
            and c.authorization_id==self.authorization_id and c.strategy_revision==self.strategy_revision
            and c.ownership_epoch==self.ownership_epoch and c.policy_revision==self.risk_policy_revision
            and c.snapshot_ref==self.book.snapshot_ref,'FINANCIAL_CONTEXT_BINDING_MISMATCH')
        require(type(p) is QuotePositionProposalV1 and type(self.source) is QuoteFinancialSourceV1,
            'QUOTE_FINANCIAL_PROPOSAL_REQUIRED')
        from .order_codec import VersionedOrderCodec
        codec=VersionedOrderCodec()
        require(codec.hash('order-proposal',p)==self.source.source_hash==self.hashes.proposal
            and codec.hash('order-program',p.program)==self.hashes.program,'FINANCIAL_PROPOSAL_HASH_MISMATCH')
        require(type(self.position) is PositionValueBandAssessmentV1 and self.position.scope==self.scope
            and self.position.goal==p.goal and canonical_hash(domain_payload(self.position))==self.hashes.position_band,
            'QUOTE_FINANCIAL_POSITION_BINDING_MISMATCH')
        require(len(p.program.nodes)==1 and p.program.nodes[0].order.quantity.kind=='QUOTE_NOTIONAL'
            and self.native_quote_amount.amount==Decimal(p.program.nodes[0].order.quantity.value)
            and self.native_quote_amount.currency==p.goal.maximum_quote.currency,'QUOTE_NATIVE_BUDGET_CHANGED')
        require(self.unit.ledger_unit=={t.Product.SPOT:'BASE',t.Product.US_EQUITY:'SHARE'}.get(self.scope.product)
            and self.position.delta_maximum.unit==self.unit.ledger_unit,'FINANCIAL_LEDGER_UNIT_MISMATCH')
        require(self.bounds.protection_policy==p.protection_policy and self.bounds.stop_trigger==p.exit_plan.stop_trigger
            and self.bounds.stop_price==(None if p.exit_plan.stop_price is None else decimal_string(p.exit_plan.stop_price))
            and self.bounds.maximum_holding_seconds==p.exit_plan.maximum_holding_seconds
            and self.bounds.review_due_at==p.exit_plan.review_due_at,'FINANCIAL_RESPONSIBILITY_MISMATCH')
        require(utc_timestamp(self.book.as_of)<=utc_timestamp(self.created_at)<utc_timestamp(self.valid_until)
            <=utc_timestamp(p.valid_until),'FINANCIAL_VALIDITY_MISMATCH')

    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteFinancialRiskBasisV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_FINANCIAL_RISK_BASIS_V1']
    intent_id:str
    scope:t.ScopeKey
    intent_hash:str
    source_hash:str
    book_hash:str
    allocation_hash:str
    terms_hash:str
    metrics_hash:str
    risk_facts_hash:str
    policy_hash:str
    archive_hash:str
    incremental_requirements:tuple[t.Money,...]

    def __post_init__(self):
        require(self.contract_version=='2026-10-30' and self.record_kind=='QUOTE_FINANCIAL_RISK_BASIS_V1',
            'QUOTE_FINANCIAL_BASIS_VERSION_REQUIRED')
        identifier(self.intent_id)
        for name in ('intent_hash','source_hash','book_hash','allocation_hash','terms_hash','metrics_hash','risk_facts_hash','policy_hash','archive_hash'):
            digest(getattr(self,name))
        require(len({m.currency for m in self.incremental_requirements})==len(self.incremental_requirements)
            and all(m.currency and m.amount>=0 for m in self.incremental_requirements),'FINANCIAL_INCREMENTAL_ASSETS_INVALID')

    @property
    def execution_authorized(self)->Literal[False]:return False
