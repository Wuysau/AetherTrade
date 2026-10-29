"""Typed generic risk evidence and pure assessment, never order authority.

Host readers must authenticate every input under the existing owner/collateral
lock. Dataclass construction, labels and hashes are not evidence of a venue order
or protection. No production reader/permit/command is registered by this module.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.order_snapshots import OrderRuleSnapshot,OrderCapabilitySnapshot
from trading_system.intents.product_rules import TrustedProductRules
from trading_system.intents.program_target_allocation import ProgramTargetFacts
from .evaluator import RiskInputs
from .program_exposure import ProgramBudgetFacts,FeeBound
from .terms import RiskMetrics,RiskSource


@dataclass(frozen=True,slots=True,kw_only=True)
class NodeRiskBound:
    node_id:str
    minimum_price:t.Price|None
    maximum_price:t.Price|None
    execution_fees:tuple[FeeBound,...]|None
    funding_reserve:tuple[t.Money,...]|None
    margin_reserve:tuple[t.Money,...]|None
    holding_horizon_seconds:int|None
    maximum_live_children:int|None
    evidence_refs:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class WorkingRiskBound:
    logical_client_order_id:str
    scope:t.ScopeKey
    side:Literal['BUY','SELL']
    quantity:t.Quantity
    minimum_price:t.Price|None
    maximum_price:t.Price|None
    evidence_refs:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class PendingRiskBound:
    obligation_id:str
    scope:t.ScopeKey
    source_family:Literal['LEGACY_COMMAND','APPLICATION_WRITE']
    service_namespace:Literal['ORDINARY','ORDER_LIST','CONDITIONAL_ALGO','EXECUTION_ALGO','EQUITY']
    canonical_command_refs:tuple[str,...]
    original_command_refs:tuple[str,...]
    minimum_price:t.Price|None
    maximum_price:t.Price|None
    evidence_refs:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class ProtectionCoverage:
    """Concrete phase/quantity duty reference, not a model-authored True flag.

NATIVE_FULL_FILL_ONLY can never claim PARTIAL_FILL, even if a payload lists it.
An authentic server-side archive reader must prove each referenced duty and its
activation/race handling before supplying this fact; no such reader is installed.
"""
    scope:t.ScopeKey
    owner_strategy_id:str
    ownership_epoch:int
    program_hash:str
    allocation_hash:str
    book_hash:str
    origin:Literal['NATIVE_PER_FILL_DUTY','HOSTED_CASH_DUTY','NATIVE_FULL_FILL_ONLY']
    phases:tuple[Literal['PARTIAL_FILL','FULL_FILL','RECEIPT_NET','RETAINED_POSITION'],...]
    node_ids:tuple[str,...]
    maximum_quantity:t.Quantity
    stop_price:t.Price
    stop_trigger:str
    duty_refs:tuple[t.EvidenceRef,...]
    valid_until:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramRiskFacts:
    inputs:RiskInputs
    market:t.SnapshotPacket
    authorization:t.AuthorizationDecision
    target_facts:ProgramTargetFacts
    budget_facts:ProgramBudgetFacts
    rules:OrderRuleSnapshot
    capabilities:OrderCapabilitySnapshot
    profile:TrustedProductRules
    expected_build_digest:str
    expected_instrument_ref:str
    node_bounds:tuple[NodeRiskBound,...]|None
    working_bounds:tuple[WorkingRiskBound,...]|None
    pending_bounds:tuple[PendingRiskBound,...]|None
    additional_exit_fees:tuple[FeeBound,...]|None
    protection:tuple[ProtectionCoverage,...]|None
    allowed_stop_triggers:tuple[str,...]|None
    maximum_live_orders:int|None
    holding_horizon_seconds:int|None
    entry_slippage_bps:Decimal|None
    evidence:tuple[t.EvidenceRef,...]
    as_of:str
    valid_until:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramRiskDecision:
    risk_decision_id:str
    source:RiskSource
    terms_hash:str
    compilation_hash:str
    allocation_hash:str
    exposure_hash:str
    facts_hash:str
    policy_hash:str
    evaluated_at:str
    expires_at:str
    verdict:Literal['ALLOW','DENY','REPLAN_REQUIRED']
    metrics:RiskMetrics|None
    rejection_codes:tuple[str,...]
    obligations:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False
