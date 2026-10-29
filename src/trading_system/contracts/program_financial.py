"""Execution-neutral financial records; content binding is never authority."""
from dataclasses import dataclass, fields
from decimal import Decimal, Inexact, Rounded, localcontext
from typing import Literal

from . import types as t
from .canonical import canonical_hash, domain_payload, utc_timestamp, decimal_string
from .order_program import OrderProposal, identifier
from .order_program_v3 import OrderProposalV3


def require(condition, reason):
    if not condition:raise ValueError(reason)


def digest(value):
    require(type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value),
        'FINANCIAL_HASH_REQUIRED')


@dataclass(frozen=True, slots=True, kw_only=True)
class FinancialSource:
    source_kind: Literal['ORDER_PROPOSAL']
    source_version: Literal['2026-10-28','2026-10-29']
    source_hash: str
    intake_record_id: str
    intake_hash: str
    intake_transaction_id: str
    context: t.DecisionContext
    context_hash: str

    def __post_init__(self):
        require(self.source_kind=='ORDER_PROPOSAL' and self.source_version in ('2026-10-28','2026-10-29'),
            'FINANCIAL_SOURCE_VERSION_UNSUPPORTED')
        for value in (self.source_hash,self.intake_hash,self.context_hash):digest(value)
        identifier(self.intake_record_id);identifier(self.intake_transaction_id)
        require(self.context_hash==canonical_hash(domain_payload(self.context)),'FINANCIAL_CONTEXT_HASH_MISMATCH')


@dataclass(frozen=True, slots=True, kw_only=True)
class FinancialUnit:
    native_unit: str
    ledger_unit: Literal['BASE','CONTRACT','SHARE']
    ledger_units_per_native_unit: Decimal
    native_multiplier: Decimal
    ledger_multiplier: Decimal

    def __post_init__(self):
        identifier(self.native_unit)
        require(all(type(v) is Decimal and v.is_finite() and v>0 for v in
            (self.ledger_units_per_native_unit,self.native_multiplier,self.ledger_multiplier)),
            'FINANCIAL_UNIT_MULTIPLIER_REQUIRED')
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            require(self.native_multiplier==self.ledger_units_per_native_unit*self.ledger_multiplier,
                'FINANCIAL_UNIT_MULTIPLIER_MISMATCH')
        require(self.native_unit!=self.ledger_unit or self.ledger_units_per_native_unit==1,
            'FINANCIAL_IDENTICAL_UNIT_SCALE_MISMATCH')


@dataclass(frozen=True, slots=True, kw_only=True)
class FinancialTarget:
    side: Literal['LONG','SHORT','FLAT']
    normalized: t.Quantity
    actual: t.Quantity
    working: t.Quantity
    remaining: t.Quantity
    residual: t.Quantity

    def __post_init__(self):
        quantities=(self.normalized,self.actual,self.working,self.remaining,self.residual)
        require(len({q.unit for q in quantities})==1 and all(q.unit for q in quantities),
            'FINANCIAL_TARGET_UNIT_MISMATCH')
        require(self.normalized.value>=0 and ((self.side=='FLAT')==(self.normalized.value==0))
            and self.side in ('LONG','SHORT','FLAT'),'FINANCIAL_TARGET_SIDE_MISMATCH')
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
            signed=self.normalized.value*(-1 if self.side=='SHORT' else 1)
            require(signed-self.actual.value-self.working.value-self.remaining.value==self.residual.value,
                'FINANCIAL_TARGET_RESIDUAL_MISMATCH')


@dataclass(frozen=True, slots=True, kw_only=True)
class FinancialBook:
    sequence: int
    hash: str
    basis_fingerprint: str
    snapshot_ref: t.SnapshotRef
    as_of: str

    def __post_init__(self):
        require(type(self.sequence) is int and self.sequence>0,'FINANCIAL_BOOK_REQUIRED')
        digest(self.hash);identifier(self.basis_fingerprint);utc_timestamp(self.as_of)


@dataclass(frozen=True, slots=True, kw_only=True)
class FinancialBounds:
    maximum_planned_loss: t.Money
    maximum_holding_seconds: int
    protection_policy: Literal['NATIVE_REQUIRED','HOSTED_ALLOWED_CASH_ONLY']
    stop_trigger: str
    stop_price: Decimal | None
    review_due_at: str

    def __post_init__(self):
        require(self.maximum_planned_loss.amount>=0 and self.maximum_planned_loss.currency,
            'FINANCIAL_LOSS_BOUND_REQUIRED')
        require(type(self.maximum_holding_seconds) is int and self.maximum_holding_seconds>0,
            'FINANCIAL_HOLDING_BOUND_REQUIRED')
        require(self.stop_price is None or (type(self.stop_price) is Decimal and self.stop_price.is_finite()
            and self.stop_price>0),'FINANCIAL_STOP_BOUND_INVALID')
        identifier(self.stop_trigger);utc_timestamp(self.review_due_at)


@dataclass(frozen=True, slots=True, kw_only=True)
class FinancialHashes:
    proposal: str
    program: str
    compilation: str
    rules: str
    capabilities: str
    profile: str
    target_facts: str
    allocation: str
    budget_facts: str
    exposure: str
    risk_facts: str
    risk_terms: str
    risk_metrics: str
    policy: str
    archive: str

    def __post_init__(self):
        for field in fields(self):digest(getattr(self,field.name))


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramFinancialIntent:
    contract_version: Literal['2026-10-29']
    record_kind: Literal['PROGRAM_FINANCIAL_INTENT']
    intent_id: str
    scope: t.ScopeKey
    venue: str
    collateral_group_id: str
    owner_strategy_id: str
    strategy_revision: int
    ownership_epoch: int
    risk_policy_revision: int
    authorization_id: str
    source: FinancialSource
    proposal: OrderProposal | OrderProposalV3
    book: FinancialBook
    unit: FinancialUnit
    target: FinancialTarget
    bounds: FinancialBounds
    hashes: FinancialHashes
    created_at: str
    valid_until: str

    def __post_init__(self):
        require(self.contract_version=='2026-10-29' and self.record_kind=='PROGRAM_FINANCIAL_INTENT',
            'FINANCIAL_INTENT_VERSION_UNSUPPORTED')
        for value in (self.intent_id,self.venue,self.collateral_group_id,self.owner_strategy_id,self.authorization_id):identifier(value)
        require(self.scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),'FINANCIAL_ENVIRONMENT_DISABLED')
        require(all(type(v) is int and v>0 for v in (self.strategy_revision,self.ownership_epoch,self.risk_policy_revision)),
            'FINANCIAL_OWNER_REVISIONS_REQUIRED')
        context=self.source.context
        require(self.scope in context.scopes and context.environment==self.scope.environment
            and context.authorization_id==self.authorization_id and context.strategy_revision==self.strategy_revision
            and context.ownership_epoch==self.ownership_epoch and context.policy_revision==self.risk_policy_revision
            and context.snapshot_ref==self.book.snapshot_ref,'FINANCIAL_CONTEXT_BINDING_MISMATCH')
        require(self.book.snapshot_ref.environment==self.scope.environment,'FINANCIAL_SNAPSHOT_SCOPE_MISMATCH')
        require(self.unit.ledger_unit=={t.Product.SPOT:'BASE',t.Product.USD_M_PERP:'CONTRACT',t.Product.US_EQUITY:'SHARE'}[self.scope.product]
            and self.target.normalized.unit==self.unit.ledger_unit,'FINANCIAL_LEDGER_UNIT_MISMATCH')
        from .order_codec import VersionedOrderCodec
        codec=VersionedOrderCodec();p=self.proposal
        require(type(p) in (OrderProposal,OrderProposalV3) and p.action=='SET_TARGET' and p.program is not None,
            'FINANCIAL_TARGET_PROPOSAL_REQUIRED')
        require(self.source.source_version==p.contract_version
            and codec.hash('order-proposal',p)==self.source.source_hash==self.hashes.proposal
            and codec.hash('order-program',p.program)==self.hashes.program,'FINANCIAL_PROPOSAL_HASH_MISMATCH')
        require(self.target.side==p.target_side and self.bounds.protection_policy==p.protection_policy
            and self.bounds.stop_trigger==p.exit_plan.stop_trigger
            and self.bounds.stop_price==(None if p.exit_plan.stop_price is None else decimal_string(p.exit_plan.stop_price))
            and self.bounds.maximum_holding_seconds==p.exit_plan.maximum_holding_seconds
            and self.bounds.review_due_at==p.exit_plan.review_due_at,'FINANCIAL_RESPONSIBILITY_MISMATCH')
        require(utc_timestamp(self.book.as_of)<=utc_timestamp(self.created_at)<utc_timestamp(self.valid_until)
            <=utc_timestamp(p.valid_until),'FINANCIAL_VALIDITY_MISMATCH')

    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramFinancialRiskBasis:
    contract_version: Literal['2026-10-29']
    record_kind: Literal['PROGRAM_FINANCIAL_RISK_BASIS']
    intent_id: str
    scope: t.ScopeKey
    intent_hash: str
    source_hash: str
    book_hash: str
    allocation_hash: str
    terms_hash: str
    metrics_hash: str
    risk_facts_hash: str
    policy_hash: str
    archive_hash: str
    incremental_requirements: tuple[t.Money, ...]

    def __post_init__(self):
        require(self.contract_version=='2026-10-29' and self.record_kind=='PROGRAM_FINANCIAL_RISK_BASIS',
            'FINANCIAL_BASIS_VERSION_UNSUPPORTED')
        identifier(self.intent_id)
        for name in ('intent_hash','source_hash','book_hash','allocation_hash','terms_hash','metrics_hash','risk_facts_hash','policy_hash','archive_hash'):
            digest(getattr(self,name))
        require(len({m.currency for m in self.incremental_requirements})==len(self.incremental_requirements)
            and all(m.currency and m.amount>=0 for m in self.incremental_requirements),'FINANCIAL_INCREMENTAL_ASSETS_INVALID')

    @property
    def execution_authorized(self)->Literal[False]:return False
