"""Read-only Docker operation allowlist.

The scheduler identity is permitted only to observe fixed services. Every
mutation, global cleanup, VHDX operation, arbitrary mount/exec and unknown
resource deletion is denied by policy. Policy denial is not OS isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str


_SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z')
_TAIL = re.compile(r'--tail=([1-9][0-9]{0,4})\Z')
_ALLOWED = frozenset({'version', 'info', 'ps', 'inspect', 'logs'})
_FORBIDDEN_OPERATIONS = frozenset({
    'build', 'commit', 'compose', 'container', 'context', 'cp', 'create', 'exec',
    'export', 'image', 'import', 'kill', 'load', 'login', 'logout', 'mount',
    'migrate', 'network', 'node', 'pause', 'plugin', 'prune', 'pull', 'push',
    'rename', 'reset', 'restart', 'rm', 'rmi', 'run', 'save', 'secret', 'service',
    'stack', 'start', 'stop', 'swarm', 'system', 'tag', 'top', 'unpause', 'update',
    'vhd', 'vhdx', 'volume', 'wait',
})


def _safe_id(value: str) -> bool:
    return (isinstance(value, str) and _SAFE_ID.fullmatch(value) is not None
            and '..' not in value and 'docker.sock' not in value)


def authorize(operation: str, arguments: tuple[str, ...] = ()) -> Decision:
    if not isinstance(operation, str) or operation.strip().lower() not in _ALLOWED:
        return Decision(False, 'DOCKER_OPERATION_NOT_ALLOWLISTED')
    op = operation.strip().lower()
    if not isinstance(arguments, tuple) or not all(isinstance(a, str) for a in arguments):
        return Decision(False, 'DOCKER_ARGUMENT_INVALID')
    if op in {'version', 'info', 'ps'}:
        return (Decision(True, '') if not arguments
                else Decision(False, 'DOCKER_ARGUMENT_NOT_ALLOWLISTED'))
    if op == 'inspect':
        if not 1 <= len(arguments) <= 8 or not all(_safe_id(a) for a in arguments):
            return Decision(False, 'DOCKER_ARGUMENT_NOT_ALLOWLISTED')
        return Decision(True, '')
    # logs: at most one bounded --tail, then exactly one fixed resource id.
    tail = tuple(a for a in arguments if a.startswith('--tail='))
    if len(tail) > 1 or (tail and _TAIL.fullmatch(tail[0]) is None):
        return Decision(False, 'DOCKER_ARGUMENT_NOT_ALLOWLISTED')
    rest = tuple(a for a in arguments if a not in tail)
    if len(rest) != 1 or not _safe_id(rest[0]):
        return Decision(False, 'DOCKER_ARGUMENT_NOT_ALLOWLISTED')
    return Decision(True, '')
