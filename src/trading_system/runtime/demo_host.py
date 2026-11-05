"""Demo admission is separate from legacy AI/PAPER automation authorization."""
from dataclasses import dataclass
from decimal import Decimal
from trading_system.engine.binance_demo.contracts import DemoError, decimal


@dataclass(frozen=True, kw_only=True)
class AdmissionContext:
    now: int
    quote_at: int
    account_at: int
    quote_source: str
    model_source: str
    decision_expires_at: int
    capability_state: str
    code_sha: str
    qualified_sha: str
    private_stream_healthy: bool
    account_state: str
    authorized: bool
    budget: str
    available: str
    risk_limit: str
    unresolved_unknown: int
    unprotected: int
    pending_inbox: int = 0


class DemoRuntimeHost:
    @staticmethod
    def admit(context: AdmissionContext) -> Decimal:
        if context.account_state != 'READY' or not context.authorized:
            raise DemoError('DEMO_NOT_AUTHORIZED')
        if context.capability_state != 'DEMO_VERIFIED' or len(context.code_sha) != 40 or context.qualified_sha != context.code_sha:
            raise DemoError('DEMO_CAPABILITY_NOT_QUALIFIED')
        if context.quote_source != 'BINANCE_DEMO' or not 0 <= context.now - context.quote_at <= 5 or not 0 <= context.now - context.account_at <= 15:
            raise DemoError('DEMO_SNAPSHOT_STALE')
        if context.model_source not in {'assessment_v1', 'verified_rule_v1'} or context.now >= context.decision_expires_at:
            raise DemoError('DEMO_DECISION_INVALID')
        if not context.private_stream_healthy or context.unresolved_unknown or context.unprotected or context.pending_inbox:
            raise DemoError('DEMO_RECOVERY_REQUIRED')
        return min(decimal(context.budget, positive=True), decimal(context.available, positive=True), decimal(context.risk_limit, positive=True), Decimal('10000'))

    @staticmethod
    def admit_management(context: AdmissionContext, *, cancel=False):
        if not context.authorized or context.now >= context.decision_expires_at:
            raise DemoError('DEMO_NOT_AUTHORIZED')
        if context.capability_state not in ({'DEMO_VERIFIED', 'INVALIDATED_BALANCE'} if cancel else {'DEMO_VERIFIED'}) or len(context.code_sha) != 40 or context.qualified_sha != context.code_sha:
            raise DemoError('DEMO_CAPABILITY_NOT_QUALIFIED')
        if not 0 <= context.now - context.account_at <= 15:
            raise DemoError('DEMO_SNAPSHOT_STALE')
        if not cancel and (context.quote_source != 'BINANCE_DEMO' or not 0 <= context.now - context.quote_at <= 5 or context.unresolved_unknown or context.pending_inbox):
            raise DemoError('DEMO_REDUCTION_EVIDENCE_REQUIRED')
        # PAUSED/RECOVERY and an unprotected position must not disable a
        # separately authorized cancellation or owned-exposure reduction.
        return True
