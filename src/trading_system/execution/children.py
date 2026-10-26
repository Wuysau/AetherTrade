"""Offline controlled child plans. Planning never issues a durable permit.

Persist under the collateral transaction; consume through ChildDispatcher, not
the generic IP-10 dispatcher. Its fresh authority reader must include the usual
risk/reservation/protection checks for each child. Native algorithms stay off.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.wire import (
    ABSENT, ContractCodec, ExecutionCommand, ExecutionCommandCanonicalPayload, TradeIntent,
)
from trading_system.intents.semantic_validation import quantity_on_grid, require, time_key
from trading_system.intents.target_compiler import TargetCompiler
from trading_system.storage.models import MissingEvidence, SequenceConflict, atomic_write
from .commands import CommandRepository, command_hash
from .dispatcher import CommandDispatcher, DispatchDenied, clock_now
from .projections import subtract_exact


WINDOW_PREFIX = 'ip14:window:'


def offline(scope: t.ScopeKey) -> None:
    if scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH):
        raise DispatchDenied('IP14_OFFLINE_ONLY')


def make_command(intent, codec, execution_epoch, key, operation, payload, *,
                 parent=None, target=None):
    """An immutable request; pending ref is explicitly not a committed permit."""
    scope = codec.scope
    offline(scope)
    if (intent.environment, intent.venue_account_id, intent.product,
            intent.instrument_uid, intent.venue_leg) != (
            scope.environment, scope.venue_account_id, scope.product,
            scope.instrument_uid, scope.venue_leg):
        raise ValueError('command ID registry scope mismatch')
    if type(execution_epoch) is not int or execution_epoch < 1:
        raise ValueError('execution epoch required')
    # Controlled slices are ordinary independent orders. CHILD/ALGORITHM are
    # reserved by IP-11 for disabled native-generated writes.
    role = {'SUBMIT': 'ORDER', 'SET_PROTECTION': 'PROTECTION'}.get(operation, operation)
    client = codec.encode(key, scope.product, role)
    identity = codec.decode(client)
    if identity.scope != scope or identity.logical_client_order_id != key:
        raise ValueError('ID registry roundtrip mismatch')
    command = ExecutionCommand(contract_version=intent.contract_version,
        command_id=key, attempt_id=key + ':a1', intent_id=intent.intent_id,
        parent_command_id=parent, environment=intent.environment,
        venue_account_id=intent.venue_account_id, product=intent.product,
        instrument_uid=intent.instrument_uid, operation=operation,
        logical_client_order_id=key, venue_client_order_id=client,
        # The action's identity is distinct from the target's venue identity.
        venue_order_id=identity.venue_order_id,
        target_order_id=None if target is None else target.identity.venue_order_id,
        canonical_payload=payload, payload_hash='0' * 64,
        ownership_epoch=intent.ownership_epoch, execution_epoch=execution_epoch,
        policy_revision=intent.risk_policy_revision, state='DURABLE_READY',
        outcome_provenance='NONE', durable_record_ref='pending:' + key,
        created_at=intent.created_at)
    body = ContractCodec().encode(command)
    return replace(command, payload_hash=canonical_hash(body['canonical_payload']))


@dataclass(frozen=True, kw_only=True)
class CommandWindow:
    command_id: str
    request_hash: str
    expires_at: str


@dataclass(frozen=True, kw_only=True)
class ChildPlanRecord:
    plan_id: str
    request_hash: str
    command_ids: tuple[str, ...]
    expires_at: str


def save_window(tx, command, expires_at):
    record = CommandWindow(command_id=command.command_id,
        request_hash=command_hash(command), expires_at=expires_at)
    key = WINDOW_PREFIX + command.command_id
    old = tx._latest('audit_events', key, CommandWindow)
    if old is not None and old != record:
        raise SequenceConflict('immutable command window conflict')
    if old is None:
        tx._append('audit_events', key, record, 0)


class ChildPlanner:
    def __init__(self, compiler: TargetCompiler, id_codec, execution_epoch: int, *,
                 clock=clock_now, algorithm='CONTROLLED_SLICES'):
        if algorithm != 'CONTROLLED_SLICES':
            raise ValueError('uncontrolled native algorithms are disabled')
        self.compiler, self.id_codec = compiler, id_codec
        self.execution_epoch, self.clock = execution_epoch, clock

    def plan(self, intent: TradeIntent, book: t.BookSnapshot,
             policy: t.ChildPolicy) -> list[ExecutionCommand]:
        offline(book.scope)
        now = self.clock()
        require(now.clock_health == 'HEALTHY', 'CLOCK_UNHEALTHY')
        expiry = min((policy.expires_at, intent.valid_until,
                      intent.execution.entry_deadline), key=time_key)
        require(time_key(now.utc_now) < time_key(expiry), 'CHILD_PLAN_EXPIRED')
        require(type(policy.maximum_children) is int and 1 <= policy.maximum_children <= 10000,
                'INVALID_CHILD_LIMIT')
        require(policy.slice_quantity.unit == intent.quantity_unit
                and policy.slice_quantity.value > 0, 'INVALID_SLICE_QUANTITY')
        require(bool(policy.compensation_policy), 'COMPENSATION_POLICY_REQUIRED')
        # Refresh clock without changing the externally bound monotonic deadline.
        compiler = TargetCompiler(replace(self.compiler.inputs, clock=now))
        delta = compiler.compile(intent, book)
        require(not delta.cancel_first, 'CANCEL_OR_RECONCILE_BEFORE_CHILDREN')
        require(delta.classification != 'REVERSAL_REQUIRES_FLAT', 'REVERSAL_REQUIRES_FLAT')
        if not delta.remaining.value:
            return []
        spec = compiler.inputs.instrument
        size = quantity_on_grid(policy.slice_quantity.value, spec.quantity_step.value, False)
        require(size >= spec.minimum_quantity.value, 'SLICE_BELOW_MINIMUM')
        remaining = delta.remaining.value.copy_abs()
        # Integer ratios avoid rounding near the maximum child boundary.
        rn, rd = remaining.as_integer_ratio()
        sn, sd = size.as_integer_ratio()
        count = (rn * sd + rd * sn - 1) // (rd * sn)
        require(count <= policy.maximum_children, 'MAXIMUM_CHILDREN_EXCEEDED')
        require(count == 1 or intent.execution.allow_partial, 'PARTIAL_EXECUTION_NOT_ALLOWED')
        parent = 'ip14:plan:' + canonical_hash({'intent_id': intent.intent_id})
        commands = []
        for index in range(count):
            quantity = min(size, remaining)
            require(quantity >= spec.minimum_quantity.value, 'TAIL_BELOW_MINIMUM')
            quantity_on_grid(quantity, spec.quantity_step.value, False)
            key = 'ip14:child:' + canonical_hash({'intent_id': intent.intent_id, 'index': index})
            payload = ExecutionCommandCanonicalPayload(
                side='BUY' if delta.remaining.value > 0 else 'SELL',
                order_type=intent.execution.order_preference, quantity=format(quantity, 'f'),
                price=intent.execution.limit_price if intent.execution.order_preference == 'LIMIT' else ABSENT,
                reduce_only=delta.classification == 'REDUCE', full_position_exit=False,
                position_side=intent.venue_leg,
                time_in_force='GTC' if intent.execution.order_preference == 'LIMIT' else ABSENT,
                tokenize=False if intent.product == 'US_EQUITY' else ABSENT)
            commands.append(make_command(intent, self.id_codec, self.execution_epoch, key,
                'CLOSE' if delta.classification == 'REDUCE' else 'SUBMIT', payload, parent=parent))
            remaining = subtract_exact(remaining, quantity)
        return commands

    def persist(self, tx, intent: TradeIntent, book: t.BookSnapshot,
                policy: t.ChildPolicy) -> tuple[str, ...]:
        """Atomically enqueue one immutable plan per intent, never replan an UNKNOWN.

        The caller must reserve the full delta in this transaction and supply
        TargetInputs including all other committed in-flight quantities.
        No parent write is enqueued; parent_command_id names the audit plan.
        """
        return _PlanWriter(tx, self).persist(intent, book, policy)


class _PlanWriter:
    def __init__(self, tx, planner):
        self.tx, self.planner = tx, planner

    @atomic_write
    def persist(self, intent, book, policy):
        tx = self.tx
        tx.require_scope(book.scope)
        if tx._latest('intents', intent.intent_id, TradeIntent) != intent:
            raise MissingEvidence('persisted intent required')
        key = 'ip14:plan:' + canonical_hash({'intent_id': intent.intent_id})
        fingerprint = canonical_hash({'intent': ContractCodec().encode(intent),
            'book': domain_payload(book), 'policy': domain_payload(policy),
            'execution_epoch': self.planner.execution_epoch})
        old = tx._latest('audit_events', key, ChildPlanRecord)
        if old is not None:
            if old.request_hash != fingerprint:
                raise SequenceConflict('child plan already exists; reconcile original IDs')
            return old.command_ids
        if tx.projections.book(book.scope) != book:
            raise SequenceConflict('book changed before child persistence')
        commands = self.planner.plan(intent, book, policy)
        expiry = min((policy.expires_at, intent.valid_until, intent.execution.entry_deadline), key=time_key)
        for command in commands:
            CommandRepository(tx).append(command)
            save_window(tx, command, expiry)
        record = ChildPlanRecord(plan_id=key, request_hash=fingerprint,
            command_ids=tuple(c.command_id for c in commands), expires_at=expiry)
        tx._append('audit_events', key, record, 0)
        return record.command_ids


class ChildDispatcher(CommandDispatcher):
    """Mandatory IP-14 outbox consumer. Preserves IP-10's UNKNOWN recovery.

    Expired READY commands remain unsent and held (the IP-03 SQL guard has no
    ABORTED_UNSENT transition). MAY_HAVE_SENT/UNKNOWN still reconcile the same ID.
    An instance keeps the earliest monotonic cutoff even if wall time rolls back.
    Restart requires a healthy clock and renewed external authority facts.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._deadlines = {}

    def _check(self, tx, command, lease, permit=None):
        window = tx._latest('audit_events', WINDOW_PREFIX + command.command_id, CommandWindow)
        if (window is None or window.command_id != command.command_id
                or window.request_hash != command_hash(command)):
            raise DispatchDenied('IP14_COMMAND_WINDOW_MISSING')
        reading = self.clock()
        if (reading.clock_health != 'HEALTHY' or type(reading.monotonic_ns) is not int
                or reading.monotonic_ns < 0
                or time_key(reading.utc_now) >= time_key(window.expires_at)):
            raise DispatchDenied('IP14_WINDOW_EXPIRED_OR_CLOCK_UNHEALTHY')
        remaining = utc_timestamp(window.expires_at) - utc_timestamp(reading.utc_now)
        ns = ((remaining.days * 86400 + remaining.seconds) * 1000000 + remaining.microseconds) * 1000
        deadline = min(self._deadlines.get(command.command_id, reading.monotonic_ns + ns),
                       reading.monotonic_ns + ns)
        self._deadlines[command.command_id] = deadline
        if reading.monotonic_ns >= deadline:
            raise DispatchDenied('IP14_WINDOW_EXPIRED')
        facts = super()._check(tx, command, lease, permit)
        facts = replace(facts, valid_until=min((facts.valid_until, window.expires_at), key=time_key),
            deadline_monotonic_ns=min(facts.deadline_monotonic_ns, deadline))
        self._clock_check(lease, facts, permit)
        return facts
