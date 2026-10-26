"""Application credentials only. Host composition supplies revocable session lookup."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from time import time
from typing import Callable

from trading_system.contracts.types import ActorContext, Environment, ScopeKey


class ControlError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True, kw_only=True)
class Session:
    actor: ActorContext
    expires_at: float
    revoked: bool = False


class SessionAuth:
    """Resolve a token hash afresh on every request; no cached revocation decisions.

    Tokens must be provisioned by a trusted login/TUI credential service. No
    identity, role, environment or scope is accepted from HTTP identity headers.
    TLS termination must set the ASGI scheme through trusted host configuration;
    forwarded headers are deliberately ignored here.
    """

    def __init__(self, lookup: Callable[[str], Session | None], *, clock=time):
        self.lookup = lookup
        self.clock = clock

    def authenticate(self, scheme: str, authorization: str) -> ActorContext:
        if scheme != 'https':
            raise ControlError(401, 'HTTPS_REQUIRED')
        parts = authorization.split(' ')
        if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1]:
            raise ControlError(401, 'SESSION_REQUIRED')
        session = self.lookup(sha256(parts[1].encode()).hexdigest())
        if session is None or session.revoked or session.expires_at <= self.clock():
            raise ControlError(401, 'SESSION_EXPIRED_OR_REVOKED')
        return session.actor


@dataclass(frozen=True, kw_only=True)
class ControlGrant:
    """Current server authority read under the inbox transaction's lock.

    The provider must resolve revocation, expiry, roles, current policy/owner,
    and account scope from trusted storage. A client cannot construct a grant.
    reviewed_hashes bind human confirmation to an exact operation/body digest.
    No review enables LIVE or execution.
    """
    actor: ActorContext
    role: str
    operations: tuple[str, ...]
    reviewed_hashes: tuple[str, ...] = ()


def require_grant(grant: ControlGrant | None, actor: ActorContext,
                  scope: ScopeKey, operation: str) -> ControlGrant:
    if grant is None:
        raise ControlError(403, 'AUTHORIZATION_UNAVAILABLE')
    current = grant.actor
    if (actor.identity_id != current.identity_id
            or actor.authorization_id != current.authorization_id
            or actor.deployment_id != current.deployment_id
            or actor.environment != current.environment
            or scope.environment != current.environment
            or scope not in actor.scopes or scope not in current.scopes):
        raise ControlError(403, 'AUTHORIZATION_SCOPE_MISMATCH')
    if current.environment == Environment.LIVE:
        raise ControlError(403, 'LIVE_NOT_QUALIFIED')
    if grant.role != 'USER' or operation not in grant.operations:
        raise ControlError(403, 'USER_CONTROL_REQUIRED')
    return grant
