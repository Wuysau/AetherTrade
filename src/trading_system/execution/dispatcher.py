"""Offline owner-side dispatch boundary. No real transport is constructed.

Trusted composition must inject local authority readers, an owner guard and an
EnginePort. The guard must hold process exclusivity AND serialize authority/epoch
revocation through the write call. A lease alone is not an external fence.
IP-11/17 must supply and independently qualify that integration. Callbacks never
come from model/user payloads, and must not perform network I/O inside a ledger tx.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic_ns

from trading_system.accounts.authorization import AuthorizationBinding, grant_errors
from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.ports import EnginePort
from trading_system.storage.models import MissingEvidence
from .commands import CommandRepository, command_hash


REQUIRED_CHECKS = frozenset({'ACCOUNT_IDENTITY', 'CAPABILITY', 'RISK', 'RESERVATION',
                             'INSTRUMENT_RULES', 'PROTECTION'})


@dataclass(frozen=True, kw_only=True)
class DispatchFacts:
    """Fresh server-held facts for this exact immutable request, not a permit.

The reader must re-evaluate limits/market metadata/quantity/target ownership and
reservations, rather than reuse an old risk approval. Missing checks fail closed.
    """
    binding: AuthorizationBinding
    lease: t.OwnerLease
    request_hash: str
    valid_until: str
    deadline_monotonic_ns: int
    checks: frozenset[str]


class DispatchDenied(ValueError):
    pass


def clock_now() -> t.ClockReading:
    return t.ClockReading(utc_now=datetime.now(timezone.utc).isoformat(),
                          monotonic_ns=monotonic_ns(), clock_health='HEALTHY')


class CommandDispatcher:
    def __init__(self, uow, scope: t.ScopeKey, engine: EnginePort | None = None, *,
                 load_facts=None, owner_guard=None, clock=clock_now):
        if scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH):
            raise DispatchDenied('only offline PAPER/RESEARCH composition is enabled')
        self.uow, self.scope, self.engine = uow, scope, engine
        self.load_facts, self.owner_guard, self.clock = load_facts, owner_guard, clock

    def _clock_check(self, lease, facts=None, permit=None):
        reading = self.clock()
        if (reading.clock_health != 'HEALTHY' or type(reading.monotonic_ns) is not int
                or reading.monotonic_ns < 0):
            raise DispatchDenied('CLOCK_UNHEALTHY')
        now = utc_timestamp(reading.utc_now)
        for deadline, expiry in [(lease.deadline_monotonic_ns, lease.expires_at)] + (
                [] if facts is None else [(facts.deadline_monotonic_ns, facts.valid_until)]
        ) + ([] if permit is None else [(permit.deadline_monotonic_ns, permit.expires_at)]):
            if type(deadline) is not int or reading.monotonic_ns >= deadline or now >= utc_timestamp(expiry):
                raise DispatchDenied('LEASE_AUTHORITY_OR_PERMIT_EXPIRED')
        return now

    def _check(self, tx, command, lease, permit=None):
        repo = CommandRepository(tx)
        intent = repo.validate_binding(command)
        facts = self.load_facts(tx, command, intent)
        if not isinstance(facts, DispatchFacts):
            raise DispatchDenied('DISPATCH_FACTS_MISSING')
        now = self._clock_check(lease, facts, permit)
        shard, scope, a = lease.shard, self.scope, facts.binding.authorization
        if (facts.lease != lease or not lease.instance_id or not shard.node_id
                or shard.environment != scope.environment or shard.venue_account_id != scope.venue_account_id
                or shard.product != scope.product or shard.execution_epoch != command.execution_epoch
                or type(shard.execution_epoch) is not int or shard.execution_epoch < 1):
            raise DispatchDenied('EXECUTION_OWNER_UNCONFIRMED')
        if (grant_errors(facts.binding, now) or a.authorization_id != intent.authorization_id
                or a.owner_strategy_id != intent.owner_strategy_id or scope not in a.scopes
                or a.policy_revision != command.policy_revision
                or facts.binding.ownership_epoch != command.ownership_epoch
                or facts.binding.strategy_revision != intent.strategy_revision):
            raise DispatchDenied('AUTHORIZATION_INVALID')
        if now >= utc_timestamp(intent.valid_until) or now < utc_timestamp(command.created_at):
            raise DispatchDenied('COMMAND_OR_INTENT_OUTSIDE_VALIDITY')
        required = REQUIRED_CHECKS | ({'OWNED_TARGET'} if command.operation in ('CANCEL', 'AMEND') else set())
        if facts.request_hash != command_hash(command) or not required <= facts.checks:
            raise DispatchDenied('CURRENT_RISK_RULES_OR_TARGET_NOT_VERIFIED')
        if tx.risk_locks.active(scope) or repo.unresolved_in_group(command.command_id):
            raise DispatchDenied('RISK_LOCK_OR_GROUP_COMMAND_UNRESOLVED')
        if permit is not None and (
                command.state != 'MAY_HAVE_SENT' or permit.command_id != command.command_id
                or permit.payload_hash != command.payload_hash or permit.scope != scope
                or permit.durable_record_ref != command.durable_record_ref
                or permit.ownership_epoch != command.ownership_epoch
                or permit.execution_epoch != command.execution_epoch
                or permit.policy_revision != command.policy_revision):
            raise DispatchDenied('DURABLE_PERMIT_BINDING_MISMATCH')
        return facts

    def dispatch(self, command_id: str, lease: t.OwnerLease) -> t.CommandReceipt:
        if self.engine is None or self.load_facts is None or self.owner_guard is None:
            raise DispatchDenied('TRUSTED_OWNER_COMPOSITION_REQUIRED')
        # No queue-supplied permit is accepted; one is minted inside this call.
        with self.owner_guard(lease):
            with self.uow.transaction(self.scope) as tx:
                repo = CommandRepository(tx)
                command = repo.get(command_id)
                if command is None:
                    raise MissingEvidence('command not found in dispatcher scope')
                if command.state in ('MAY_HAVE_SENT', 'UNKNOWN'):
                    result = repo.recover_unknown(command_id, self.clock().utc_now)
                    ready = False
                elif command.state != 'DURABLE_READY':
                    result = repo.receipt(command_id)
                    ready = False
                else:
                    self._check(tx, command, lease)
                    permit = repo.mark_may_have_sent(tx, command_id, 'DURABLE_READY')
                    ready = True
            if not ready:
                return result
            # The send transaction is closed. Reload revocation/epoch/rules after
            # the durable boundary, with a second transaction closed before I/O.
            try:
                with self.uow.transaction(self.scope) as tx:
                    repo = CommandRepository(tx)
                    command = repo.get(command_id)
                    if command is None:
                        raise MissingEvidence('durable command disappeared')
                    facts = self._check(tx, command, lease, permit)
                self._clock_check(lease, facts, permit)
                operation = {'SUBMIT': self.engine.submit, 'CLOSE': self.engine.submit,
                    'SET_PROTECTION': self.engine.submit, 'CANCEL': self.engine.cancel,
                    'AMEND': self.engine.amend}[command.operation]
                # Signing/authentication timestamps, if ever qualified, belong
                # exclusively to the controlled EnginePort at this boundary.
                receipt = operation(command, permit)
                if (not isinstance(receipt, t.CommandReceipt) or receipt.command_id != command_id
                        or not receipt.receipt_id
                        or receipt.status not in (t.CommandState.VENUE_CONFIRMED, t.CommandState.VENUE_REJECTED)
                        or not receipt.existing_result_ref):
                    receipt = None
            except Exception:
                # No local error/timeout proves NOT_SENT, even if it precedes I/O.
                # Failure to persist this result propagates; durable MAY_HAVE_SENT
                # still prevents retry and blocks other group commands on restart.
                with self.uow.transaction(self.scope) as tx:
                    return CommandRepository(tx).recover_unknown(command_id, self.clock().utc_now)
            with self.uow.transaction(self.scope) as tx:
                repo = CommandRepository(tx)
                if receipt is None:
                    return repo.recover_unknown(command_id, self.clock().utc_now)
                return repo.record_outcome(receipt)
