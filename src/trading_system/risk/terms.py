"""Execution-neutral risk inputs; none of these records grants execution.

Source hashes bind content, not authenticity. Host readers must authenticate the
source and derive complete metrics under the existing collateral/owner lock.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec,TradeIntent
from trading_system.contracts.canonical import utc_timestamp,canonical_hash,domain_payload


@dataclass(frozen=True,slots=True,kw_only=True)
class RiskSource:
    kind:Literal['TRADE_INTENT','ORDER_PROPOSAL']
    version:str
    hash:str

    def __post_init__(self):
        versions={'TRADE_INTENT':('2026-10-20',),'ORDER_PROPOSAL':('2026-10-28','2026-10-29')}
        if self.version not in versions.get(self.kind,()) or len(self.hash)!=64 or any(c not in '0123456789abcdef' for c in self.hash):
            raise ValueError('RISK_SOURCE_VERSION_OR_HASH_INVALID')


@dataclass(frozen=True,slots=True,kw_only=True)
class RiskTerms:
    source:RiskSource
    decision_id:str
    scope:t.ScopeKey
    authorization_id:str
    owner_strategy_id:str
    ownership_epoch:int
    policy_revision:int
    collateral_group_id:str
    basis_fingerprint:str
    snapshot_id:str
    action:str
    target_side:str
    target:t.Quantity
    requested_leverage:int
    maximum_planned_loss:t.Money
    protection_policy:str
    stop_price:Decimal|None
    stop_trigger:str
    maximum_holding_seconds:int
    review_due_at:str
    as_of:str
    created_at:str
    valid_until:str
    submission_deadline:str

    @property
    def execution_authorized(self)->Literal[False]:return False


def legacy_risk_terms(intent:TradeIntent)->RiskTerms:
    """Strict content adapter, never fabricates a legacy intent for a program."""
    if type(intent) is not TradeIntent or intent.contract_version!='2026-10-20':
        raise ValueError('LEGACY_RISK_SOURCE_REQUIRED')
    codec=ContractCodec();codec.encode(intent)
    return RiskTerms(source=RiskSource(kind='TRADE_INTENT',version=intent.contract_version,hash=codec.hash(intent)),
        decision_id=intent.intent_id,scope=t.ScopeKey(environment=t.Environment(intent.environment),
            venue_account_id=intent.venue_account_id,product=t.Product(intent.product),instrument_uid=intent.instrument_uid,venue_leg=intent.venue_leg),
        authorization_id=intent.authorization_id,owner_strategy_id=intent.owner_strategy_id,
        ownership_epoch=intent.ownership_epoch,policy_revision=intent.risk_policy_revision,
        collateral_group_id=intent.collateral_group_id,basis_fingerprint=intent.basis_fingerprint,
        snapshot_id=intent.snapshot_id,action=intent.action,target_side=intent.target_side,
        target=t.Quantity(value=Decimal(intent.target_quantity),unit=intent.quantity_unit),
        requested_leverage=intent.requested_leverage,
        maximum_planned_loss=t.Money(amount=Decimal(intent.max_planned_loss.amount),currency=intent.max_planned_loss.currency),
        protection_policy=intent.exit_plan.protection_policy,
        stop_price=Decimal(intent.exit_plan.stop_price) if intent.exit_plan.stop_price is not None else None,
        stop_trigger=intent.exit_plan.stop_trigger,maximum_holding_seconds=intent.exit_plan.maximum_holding_seconds,
        review_due_at=intent.exit_plan.review_due_at,as_of=intent.as_of,created_at=intent.created_at,
        valid_until=intent.valid_until,submission_deadline=intent.execution.entry_deadline)


@dataclass(frozen=True,slots=True,kw_only=True)
class RiskMetrics:
    """None is missing; zero/empty requires actual bound, complete source facts.

This is a future semantic adapter boundary, not an API payload or an ALLOW.
ProgramExposure alone lacks the independent fields required here.
"""
    source:RiskSource|None=None
    allocation_hash:str|None=None
    facts_hash:str|None=None
    book_hash:str|None=None
    book_sequence:int|None=None
    basis_fingerprint:str|None=None
    snapshot_ref:t.SnapshotRef|None=None
    evaluated_at:str|None=None
    valid_until:str|None=None
    maximum_order_notional:t.Money|None=None
    planned_loss:t.Money|None=None
    fees:t.Money|None=None
    funding:t.Money|None=None
    capital:t.Money|None=None
    required_assets:tuple[t.Money,...]|None=None
    exposure:t.ExposureReport|None=None
    related_groups:tuple[t.ExposureGroup,...]|None=None
    protection_complete:bool|None=None
    holding_horizon_seconds:int|None=None
    open_position_count:int|None=None
    active_order_count:int|None=None
    financial_coverage:t.Coverage|None=None
    evidence:tuple[t.EvidenceRef,...]|None=None

    @property
    def execution_authorized(self)->Literal[False]:return False


def metrics_completeness(metrics)->tuple[str,...]:
    """Only missing/invalid reasons, never an authorization or permit."""
    if type(metrics) is not RiskMetrics:return ('RISK_METRICS_REQUIRED',)
    missing=[]
    def need(condition,name):
        if not condition:missing.append('RISK_METRIC_MISSING:'+name)
    need(type(metrics.source) is RiskSource,'SOURCE')
    digest=lambda value:isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    need(digest(metrics.allocation_hash) and digest(metrics.facts_hash) and digest(metrics.book_hash),'BINDING')
    need(type(metrics.book_sequence) is int and metrics.book_sequence>0 and bool(metrics.basis_fingerprint)
        and type(metrics.snapshot_ref) is t.SnapshotRef,'BOOK_AND_SNAPSHOT')
    try:validity=utc_timestamp(metrics.evaluated_at)<utc_timestamp(metrics.valid_until)
    except (TypeError,ValueError):validity=False
    need(validity,'VALIDITY')
    for name,field in (('ORDER_NOTIONAL','maximum_order_notional'),('PLANNED_LOSS','planned_loss'),
        ('FEES','fees'),('FUNDING','funding'),('CAPITAL','capital')):
        value=getattr(metrics,field);need(type(value) is t.Money and value.amount>=0,name)
    need(type(metrics.required_assets) is tuple and all(type(v) is t.Money and v.amount>=0 for v in metrics.required_assets)
        and len({v.currency for v in metrics.required_assets})==len(metrics.required_assets),'REQUIRED_ASSETS')
    report=metrics.exposure
    need(type(report) is t.ExposureReport and type(report.gross) is t.Money and report.gross.amount>=0
        and not report.missing,'GROSS_EXPOSURE')
    need(type(report) is t.ExposureReport and type(report.net) is t.Money,'NET_EXPOSURE')
    need(type(metrics.related_groups) is tuple and type(report) is t.ExposureReport
        and metrics.related_groups==report.groups and all(type(g) is t.ExposureGroup for g in metrics.related_groups),'RELATED_GROUPS')
    need(metrics.protection_complete is True,'PROTECTION')
    need(type(metrics.holding_horizon_seconds) is int and metrics.holding_horizon_seconds>0,'HOLDING_HORIZON')
    need(type(metrics.open_position_count) is int and metrics.open_position_count>=0
        and type(metrics.active_order_count) is int and metrics.active_order_count>=0,'COUNTS')
    coverage=metrics.financial_coverage
    need(type(coverage) is t.Coverage and coverage.query_succeeded is True and coverage.supported is True
        and coverage.pagination_complete is True and not coverage.missing
        and type(metrics.evidence) is tuple and bool(metrics.evidence)
        and all(type(e) is t.EvidenceRef for e in metrics.evidence),'FINANCIAL_COVERAGE')
    return tuple(missing)


def metric_binding_codes(terms,metrics,book,snapshot,clock)->tuple[str,...]:
    """Validate current content bindings; authentic readers remain mandatory.

No reader/permit is installed by this predicate. Full program metrics still
require reviewed scenario, protection and funding derivation before its use.
"""
    codes=list(metrics_completeness(metrics))
    if type(terms) is not RiskTerms:return tuple(codes+['RISK_TERMS_REQUIRED'])
    if codes:return tuple(codes)
    if metrics.source!=terms.source:codes.append('RISK_SOURCE_BINDING_MISMATCH')
    if (book.scope!=terms.scope or metrics.financial_coverage.scope!=terms.scope
        or metrics.book_hash!=canonical_hash(domain_payload(book)) or metrics.book_sequence!=book.sequence
        or metrics.basis_fingerprint!=book.basis_fingerprint or terms.basis_fingerprint!=book.basis_fingerprint):
        codes.append('RISK_BOOK_BINDING_MISMATCH')
    if metrics.snapshot_ref!=snapshot or terms.snapshot_id!=snapshot.id or snapshot.environment!=terms.scope.environment:
        codes.append('RISK_SNAPSHOT_BINDING_MISMATCH')
    try:
        now=utc_timestamp(clock.utc_now)
        if (clock.clock_health!='HEALTHY' or not utc_timestamp(metrics.evaluated_at)<=now<utc_timestamp(metrics.valid_until)
            or now>=min(map(utc_timestamp,(terms.valid_until,terms.submission_deadline,terms.review_due_at)))
            or utc_timestamp(terms.as_of)>utc_timestamp(terms.created_at) or utc_timestamp(terms.created_at)>now
            or any(e.environment!=terms.scope.environment or not e.source or not e.id or len(e.hash)!=64
                   or utc_timestamp(e.visible_at)>now for e in metrics.evidence)):
            codes.append('RISK_FACTS_EXPIRED_OR_UNBOUND')
        if metrics.holding_horizon_seconds<terms.maximum_holding_seconds:codes.append('RISK_HOLDING_HORIZON_UNDERBOUND')
    except (TypeError,ValueError):codes.append('RISK_FACTS_EXPIRED_OR_UNBOUND')
    currency=metrics.exposure.gross.currency
    if (metrics.exposure.net.currency!=currency or abs(metrics.exposure.net.amount)>metrics.exposure.gross.amount
        or any(getattr(metrics,name).currency!=currency for name in ('maximum_order_notional','planned_loss','fees','funding','capital'))):
        codes.append('RISK_METRIC_CURRENCY_OR_EXPOSURE_INVALID')
    return tuple(codes)
