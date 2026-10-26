"""Exact-scope four-way intersection of locally supplied capability evidence.

Profiles must be resolved by trusted composition. This logical check is neither
venue qualification nor an execution permit. No symbol/default-venue fallback.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from .authorization import AuthorizationBinding, grant_errors, utc_now, valid_evidence


class CapabilityCatalog:
    def __init__(self, build_digest: str,
                 lookup: Callable[[str], AuthorizationBinding | None], *,
                 clock: Callable[[], datetime] = utc_now):
        if not build_digest:
            raise ValueError('locked build required')
        self._build = build_digest
        self._lookup = lookup
        self._clock = clock

    def intersect(self, query: t.CapabilityQuery) -> t.CapabilityDecision:
        now = self._clock()
        errors: list[str] = []
        evidence: list[t.EvidenceRef] = []
        binding = self._lookup(query.authorization.authorization_id)
        if binding is None or binding.authorization != query.authorization:
            errors.append('AUTHORIZATION_UNKNOWN_OR_CHANGED')
        else:
            errors.extend(grant_errors(binding, now))
            evidence.extend(binding.evidence)
            if query.scope not in binding.authorization.scopes:
                errors.append('SCOPE_NOT_AUTHORIZED')
        for name in ('platform', 'adapter', 'account'):
            profile = getattr(query, name)
            if profile.scope != query.scope:
                errors.append(f'{name}:SCOPE_MISMATCH')
            if profile.build_digest != self._build:
                errors.append(f'{name}:BUILD_MISMATCH')
            if profile.status != t.CapabilityStatus.VERIFIED:
                errors.append(f'{name}:NOT_VERIFIED')
            try:
                if not utc_timestamp(profile.valid_from) <= now < utc_timestamp(profile.valid_until):
                    errors.append(f'{name}:OUTSIDE_VALIDITY')
            except (ValueError, TypeError):
                errors.append(f'{name}:INVALID_TIME')
            if not profile.evidence or not all(valid_evidence(e, query.scope.environment, now)
                                               for e in profile.evidence):
                errors.append(f'{name}:INVALID_EVIDENCE')
            evidence.extend(profile.evidence)
        allowed = []
        if not errors:
            for capability in dict.fromkeys(query.requested):
                if (capability and capability in binding.capabilities
                        and all(capability in p.capabilities
                                for p in (query.platform, query.adapter, query.account))):
                    allowed.append(capability)
                else:
                    errors.append(f'CAPABILITY_NOT_AUTHORIZED:{capability}')
        if not query.requested:
            errors.append('EMPTY_REQUEST')
        status = (t.CapabilityStatus.VERIFIED if not errors else
                  t.CapabilityStatus.DEGRADED if allowed else t.CapabilityStatus.UNAVAILABLE)
        return t.CapabilityDecision(status=status, allowed=tuple(allowed),
                                    rejection_codes=tuple(errors), evidence=tuple(evidence))
