"""Offline authorization checks, never an issuer of execution authority.

Lookups must be supplied by trusted server composition, not proposal payloads.
The local binding fills fields absent from the IP-01 Authorization contract.
No configuration-to-authorization conversion or LIVE activation exists here.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def valid_evidence(ref: t.EvidenceRef, environment: t.Environment, now: datetime) -> bool:
    try:
        return (bool(ref.id and ref.source) and len(ref.hash) == 64
                and all(c in '0123456789abcdef' for c in ref.hash)
                and ref.environment == environment
                and utc_timestamp(ref.visible_at) <= now)
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorizationBinding:
    """Explicit user grant resolved from the server's current authority store.

    identity_id is the authenticated caller; owner_strategy_id is not a user ID.
    Queue environment is deployment metadata, never parsed from a queue name.
    Capabilities are explicit grants, never inferred from capital or examples.
    """
    authorization: t.Authorization
    identity_id: str
    strategy_revision: int
    ownership_epoch: int
    queue_environment: t.Environment
    capabilities: tuple[str, ...]
    evidence: tuple[t.EvidenceRef, ...]


def grant_errors(binding: AuthorizationBinding, now: datetime) -> tuple[str, ...]:
    a = binding.authorization
    errors = []
    if a.environment == t.Environment.LIVE:
        errors.append('LIVE_NOT_QUALIFIED')
    if (not isinstance(a.environment, t.Environment)
            or binding.queue_environment != a.environment
            or not a.scopes or any(s.environment != a.environment for s in a.scopes)):
        errors.append('ENVIRONMENT_MISMATCH')
    if (not all((a.authorization_id, a.owner_strategy_id, a.deployment_id, binding.identity_id))
            or type(a.policy_revision) is not int or a.policy_revision < 1
            or type(binding.strategy_revision) is not int or binding.strategy_revision < 1
            or type(binding.ownership_epoch) is not int or binding.ownership_epoch < 1):
        errors.append('INVALID_BINDING')
    if any(not s.venue_account_id or not s.instrument_uid
           or not isinstance(s.product, t.Product) or s.venue_leg not in ('BOTH', 'LONG', 'SHORT')
           for s in a.scopes):
        errors.append('INVALID_SCOPE')
    try:
        if not utc_timestamp(a.valid_from) <= now < utc_timestamp(a.expires_at):
            errors.append('AUTHORIZATION_OUTSIDE_VALIDITY')
    except (ValueError, TypeError):
        errors.append('INVALID_AUTHORIZATION_TIME')
    # A revocation marker is terminal, even if it carries a malformed/future time.
    if a.revoked_at is not None:
        errors.append('AUTHORIZATION_REVOKED')
    if a.maximum_capital.amount <= 0 or a.model_cost_budget.amount < 0:
        errors.append('INVALID_BUDGET')
    if (not a.protection_options
            or any(p not in ('NATIVE_REQUIRED', 'HOSTED_ALLOWED_CASH_ONLY') for p in a.protection_options)
            or (any(s.product == t.Product.USD_M_PERP for s in a.scopes)
                and a.protection_options != ('NATIVE_REQUIRED',))):
        errors.append('INVALID_PROTECTION')
    if not binding.evidence or not all(valid_evidence(e, a.environment, now) for e in binding.evidence):
        errors.append('AUTHORIZATION_EVIDENCE_MISSING')
    return tuple(errors)


class AuthorizationService:
    def __init__(self, lookup: Callable[[str], AuthorizationBinding | None], *,
                 clock: Callable[[], datetime] = utc_now):
        self._lookup = lookup
        self._clock = clock

    def validate(self, context: t.DecisionContext) -> t.AuthorizationDecision:
        binding = self._lookup(context.authorization_id)
        if binding is None:
            return t.AuthorizationDecision(allowed=False, authorization_id=context.authorization_id,
                                           rejection_codes=('AUTHORIZATION_UNKNOWN',), evidence=())
        now = self._clock()
        a = binding.authorization
        errors = list(grant_errors(binding, now))
        if any(type(value) is not int or value < 1 for value in
               (context.policy_revision, context.ownership_epoch, context.strategy_revision)):
            errors.append('INVALID_CONTEXT_REVISION')
        for actual, expected, code in (
            (context.authorization_id, a.authorization_id, 'AUTHORIZATION_MISMATCH'),
            (context.identity_id, binding.identity_id, 'IDENTITY_MISMATCH'),
            (context.environment, a.environment, 'ENVIRONMENT_MISMATCH'),
            (context.deployment_id, a.deployment_id, 'DEPLOYMENT_MISMATCH'),
            (context.policy_revision, a.policy_revision, 'POLICY_REVISION_MISMATCH'),
            (context.ownership_epoch, binding.ownership_epoch, 'OWNERSHIP_EPOCH_MISMATCH'),
            (context.strategy_revision, binding.strategy_revision, 'STRATEGY_REVISION_MISMATCH'),
        ):
            if actual != expected:
                errors.append(code)
        if not context.scopes or any(s not in a.scopes for s in context.scopes):
            errors.append('SCOPE_NOT_AUTHORIZED')
        if not context.decision_run_id or not valid_evidence(context.snapshot_ref, a.environment, now):
            errors.append('INVALID_SNAPSHOT_CONTEXT')
        return t.AuthorizationDecision(allowed=not errors, authorization_id=context.authorization_id,
                                       rejection_codes=tuple(dict.fromkeys(errors)), evidence=binding.evidence)
