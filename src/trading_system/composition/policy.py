"""Compile declared responsibilities, without issuing financial authority.

The IP-01 graph has no weights, edges or voting fields. Its supported semantics
are research gates, unanimous entry stages, and unanimous ordinary exit stages
with exit priority. Protection belongs to the independent IP-15/IP-16 path.
"""
from __future__ import annotations

from dataclasses import replace
import re

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload

SYSTEM_PROTECTION = 'SYSTEM_PROTECTION'
COMPILER_REVISION = 'ip-22.1'
MAX_STAGES = 32
_IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z', re.ASCII)


class CompositionError(ValueError):
    """Stable local rejection; never a venue outcome or a risk permission."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CompositionError(reason)


def identifier(value: object) -> bool:
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


class CompositionCompiler:
    def compile(self, policy: t.DecisionComposition) -> t.CompiledComposition:
        require(type(policy) is t.DecisionComposition, 'INVALID_COMPOSITION')
        require(identifier(policy.composition_id) and type(policy.revision) is int
                and policy.revision > 0, 'INVALID_COMPOSITION_REVISION')
        require(type(policy.stages) is tuple and 1 <= len(policy.stages) <= MAX_STAGES,
                'INVALID_STAGES')
        seen: set[str] = set()
        managers: dict[str, set[str]] = {role: set() for role in
                                       ('RESEARCH', 'ENTRY', 'EXIT', 'PROTECTION')}
        for stage in policy.stages:
            require(type(stage) is t.CompositionStage, 'INVALID_STAGE')
            require(identifier(stage.stage_id) and identifier(stage.manager_id), 'INVALID_STAGE_ID')
            require(stage.stage_id not in seen, 'DUPLICATE_STAGE')
            require(type(stage.responsibility) is str and stage.responsibility in managers,
                    'UNKNOWN_RESPONSIBILITY')
            seen.add(stage.stage_id)
            if stage.responsibility == 'PROTECTION':
                require(stage.manager_id == SYSTEM_PROTECTION, 'SYSTEM_PROTECTION_REQUIRED')
            else:
                require(stage.manager_id != SYSTEM_PROTECTION, 'PROTECTION_AUTHORITY_RESERVED')
            managers[stage.responsibility].add(stage.manager_id)
        require(len(managers['EXIT']) <= 1, 'OWNERSHIP_CONFLICT')
        require(len(managers['ENTRY']) <= 1, 'ENTRY_AUTHORITY_CONFLICT')
        require(bool(managers['ENTRY']) and bool(managers['EXIT']), 'MISSING_RESPONSIBILITY')
        require(sum(s.responsibility == 'PROTECTION' for s in policy.stages) <= 1,
                'DUPLICATE_PROTECTION_STAGE')
        normalized = replace(policy, stages=tuple(sorted(policy.stages, key=lambda s: s.stage_id)))
        digest = canonical_hash({'compiler_revision': COMPILER_REVISION,
                                 'policy': domain_payload(normalized)})
        return t.CompiledComposition(policy=normalized, hash=digest,
                                    ordinary_exit_owner=next(iter(managers['EXIT'])))


def verify_compiled(compiled: t.CompiledComposition) -> None:
    require(type(compiled) is t.CompiledComposition, 'INVALID_COMPILED_COMPOSITION')
    require(CompositionCompiler().compile(compiled.policy) == compiled, 'COMPOSITION_HASH_MISMATCH')
