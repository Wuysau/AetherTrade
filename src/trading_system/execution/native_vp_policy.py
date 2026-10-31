"""Explicit native VP child-generation policy.

No documented volume/participation authority exists for native VP children in
the locked adapter contract. Native VP therefore remains parent-only: a child
request is refused instead of being silently treated as TWAP or as a local VP.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True, kw_only=True)
class NativeChildPolicy:
    template: str
    child_generation: Literal['SUPPORTED', 'DENIED_NO_DOCUMENTED_VOLUME_SOURCE']
    code: str
    execution_authorized: Literal[False] = False

    @property
    def children_supported(self) -> bool:
        return self.child_generation == 'SUPPORTED'


def native_child_policy(template: str) -> NativeChildPolicy:
    if template == 'TWAP':
        return NativeChildPolicy(template='TWAP', child_generation='SUPPORTED', code='')
    if template == 'VP':
        return NativeChildPolicy(template='VP',
                                 child_generation='DENIED_NO_DOCUMENTED_VOLUME_SOURCE',
                                 code='PAPER_NATIVE_VP_CHILD_MODEL_UNAVAILABLE')
    raise ValueError('NATIVE_ALGORITHM_TEMPLATE_REQUIRED')
