"""Exact owned-ID control actions, durably bound to authenticated server facts.

No symbol-wide cancellation or direct EnginePort call exists here. Readers and
owner guards are trusted composition dependencies, never request payload hooks.
"""
from dataclasses import dataclass
from typing import Callable

from trading_system.accounts.authorization import AuthorizationBinding, grant_errors
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.wire import (
    ABSENT, ExecutionCommandCanonicalPayload, TradeIntent,
)
from trading_system.intents.semantic_validation import quantity_on_grid, time_key
from trading_system.storage.models import MissingEvidence, SequenceConflict, atomic_write
from .children import ChildDispatcher, make_command, offline, save_window
from .commands import CommandRepository, IdempotencyConflict, command_hash
from .dispatcher import DispatchDenied, clock_now
from .projections import TERMINAL, VENUE


ACTION_PREFIX = 'ip14:action:'


@dataclass(frozen=True, kw_only=True)
class OrderActionFacts:
    binding: AuthorizationBinding
    intent: TradeIntent
    projection: t.OrderProjection
    policy: t.ChildPolicy
    instrument: t.InstrumentSpec


@dataclass(frozen=True, kw_only=True)
class ActionRecord:
    command_id: str
    request_hash: str
    command_hash: str
    order: t.OwnedOrderRef
    context: t.ControlContext
    changes: t.AmendRequest | None


@dataclass(frozen=True, kw_only=True)
class CancelItem:
    order: t.OwnedOrderRef
    context: t.ControlContext


@dataclass(frozen=True, kw_only=True)
class ActionResult:
    order: t.OwnedOrderRef
    receipt: t.CommandReceipt | None
    error: str | None

    @property
    def reconciliation_required(self) -> bool:
        # An exception can follow a committed send. It is never NOT_SENT proof.
        return self.receipt is None or self.receipt.status in (
            t.CommandState.UNKNOWN, t.CommandState.MAY_HAVE_SENT)


@dataclass(frozen=True, kw_only=True)
class CancelResults:
    results: tuple[ActionResult, ...]

    @property
    def all_confirmed(self) -> bool:
        return bool(self.results) and all(r.receipt is not None
            and r.receipt.status == t.CommandState.VENUE_CONFIRMED for r in self.results)


class _ActionDispatcher(ChildDispatcher):
    def __init__(self, *args, validate_action, **kwargs):
        super().__init__(*args, **kwargs)
        self.validate_action = validate_action

    def _check(self, tx, command, lease, permit=None):
        record = tx._latest('audit_events', ACTION_PREFIX + command.command_id, ActionRecord)
        if (record is None or record.command_id != command.command_id
                or record.command_hash != command_hash(command)):
            raise DispatchDenied('ACTION_BINDING_MISSING')
        self.validate_action(tx, record.order, record.context, record.changes)
        return super()._check(tx, command, lease, permit)


class OrderActionService:
    def __init__(self, uow, scope: t.ScopeKey, lease: t.OwnerLease, id_codec, *,
                 load_action_facts: Callable | None = None, engine=None,
                 load_dispatch_facts=None, owner_guard=None, clock=clock_now):
        offline(scope)
        if id_codec.scope != scope:
            raise ValueError('ID registry scope mismatch')
        self.uow, self.scope, self.lease, self.id_codec = uow, scope, lease, id_codec
        self.load_action_facts, self.clock = load_action_facts, clock
        self.dispatcher = _ActionDispatcher(uow, scope, engine,
            load_facts=load_dispatch_facts, owner_guard=owner_guard, clock=clock,
            validate_action=self._validate)

    def _facts(self, tx, order, context):
        if self.load_action_facts is None:
            raise DispatchDenied('TRUSTED_ACTION_FACTS_REQUIRED')
        facts = self.load_action_facts(tx, order, context)
        if not isinstance(facts, OrderActionFacts):
            raise DispatchDenied('ACTION_FACTS_MISSING')
        return facts

    def _authorize(self, tx, order, context):
        scope, identity = self.scope, order.identity
        if (identity.scope != scope or scope not in context.scopes
                or context.environment != scope.environment
                or not all((identity.logical_client_order_id, identity.native_client_order_id,
                            identity.venue_client_order_id, identity.venue_order_id, order.owner_strategy_id,
                            context.identity_id, context.idempotency_key.strip()))
                or type(context.expected_sequence) is not int or context.expected_sequence < 1):
            raise DispatchDenied('OWNED_ID_SCOPE_REQUIRED')
        if self.id_codec.decode(identity.venue_client_order_id) != identity:
            raise DispatchDenied('TARGET_ID_REGISTRY_MISMATCH')
        reading = self.clock()
        if reading.clock_health != 'HEALTHY':
            raise DispatchDenied('CLOCK_UNHEALTHY')
        facts = self._facts(tx, order, context)
        b, intent = facts.binding, facts.intent
        a = b.authorization
        if (grant_errors(b, utc_timestamp(reading.utc_now))
                or b.identity_id != context.identity_id or a.deployment_id != context.deployment_id
                or a.authorization_id != context.authorization_id
                or a.environment != context.environment or scope not in a.scopes
                or a.policy_revision != context.policy_revision
                or b.ownership_epoch != context.ownership_epoch
                or order.ownership_epoch != context.ownership_epoch
                or a.owner_strategy_id != order.owner_strategy_id
                or facts.projection.order != order):
            raise DispatchDenied('OWNERSHIP_OR_AUTHORIZATION_MISMATCH')
        if (tx._latest('intents', intent.intent_id, TradeIntent) != intent
                or (intent.environment, intent.venue_account_id, intent.product,
                    intent.instrument_uid, intent.venue_leg, intent.owner_strategy_id,
                    intent.ownership_epoch, intent.risk_policy_revision, intent.authorization_id,
                    intent.strategy_revision) != (
                    scope.environment, scope.venue_account_id, scope.product,
                    scope.instrument_uid, scope.venue_leg, order.owner_strategy_id,
                    context.ownership_epoch, context.policy_revision, context.authorization_id,
                    b.strategy_revision)):
            raise DispatchDenied('ACTION_INTENT_BINDING_MISMATCH')
        return facts

    def _validate(self, tx, order, context, changes):
        facts = self._authorize(tx, order, context)
        p, spec = facts.projection, facts.instrument
        if (p.sequence != context.expected_sequence or p.lifecycle_state in TERMINAL
                or p.pending_operation != 'NONE' or p.command_certainty != 'CONFIRMED'
                or p.outcome_provenance not in VENUE):
            raise DispatchDenied('STALE_OR_UNRESOLVED_TARGET')
        if (time_key(self.clock().utc_now) >= time_key(facts.policy.expires_at)
                or time_key(self.clock().utc_now) >= time_key(facts.intent.valid_until)):
            raise DispatchDenied('ACTION_POLICY_EXPIRED')
        if (spec.instrument_uid != self.scope.instrument_uid or spec.product != self.scope.product
                or spec.rule_revision < 1):
            raise DispatchDenied('INSTRUMENT_BINDING_MISMATCH')
        unit = facts.intent.quantity_unit
        if (p.filled_quantity.unit != unit or p.leaves_quantity.unit != unit
                or p.filled_quantity.value < 0 or p.leaves_quantity.value <= 0):
            raise DispatchDenied('TARGET_QUANTITIES_INVALID')
        if changes is not None:
            if (changes.target_order != order or type(changes.expected_revision) is not int
                    or changes.expected_revision != p.lifecycle_revision):
                raise DispatchDenied('AMEND_TARGET_OR_REVISION_MISMATCH')
            supplied = {name for name in ('quantity', 'price', 'trigger_price')
                        if getattr(changes, name) is not None}
            if not supplied or not supplied <= set(facts.policy.mutable_fields):
                raise DispatchDenied('AMEND_FIELDS_NOT_ALLOWED')
            if changes.quantity is not None:
                q = changes.quantity
                if (q.unit != unit or spec.quantity_step.unit != unit
                        or spec.minimum_quantity.unit != unit
                        or q.value < spec.minimum_quantity.value or q.value <= p.filled_quantity.value):
                    raise DispatchDenied('AMEND_QUANTITY_INVALID')
                quantity_on_grid(q.value, spec.quantity_step.value, False)
            for price in (changes.price, changes.trigger_price):
                if price is not None:
                    if not price.value.is_finite() or price.value <= 0:
                        raise DispatchDenied('AMEND_PRICE_INVALID')
                    if price.currency != spec.quote_currency or spec.tick_size.currency != spec.quote_currency:
                        raise DispatchDenied('AMEND_PRICE_CURRENCY_MISMATCH')
                    quantity_on_grid(price.value, spec.tick_size.value, False)
        return facts

    def cancel(self, order: t.OwnedOrderRef, context: t.ControlContext) -> t.CommandReceipt:
        return self._execute(order, context, None)

    def amend(self, order: t.OwnedOrderRef, changes: t.AmendRequest,
              context: t.ControlContext) -> t.CommandReceipt:
        if not isinstance(changes, t.AmendRequest):
            raise ValueError('typed amend request required')
        return self._execute(order, context, changes)

    def _execute(self, order, context, changes):
        # Key does not include operation or payload: conflicting reuse is rejected.
        key = 'ip14:control:' + canonical_hash({'scope': domain_payload(self.scope),
            'identity': context.identity_id, 'deployment': context.deployment_id,
            'authorization': context.authorization_id, 'key': context.idempotency_key})
        fingerprint = canonical_hash({'order': domain_payload(order),
            'context': domain_payload(context),
            'changes': None if changes is None else domain_payload(changes)})
        with self.uow.transaction(self.scope) as tx:
            _ActionWriter(tx, self).persist(key, fingerprint, order, context, changes)
        # Every send goes through IP-10 owner/risk checks and its durable boundary.
        return self.dispatcher.dispatch(key, self.lease)

    def cancel_many(self, requests: tuple[CancelItem, ...]) -> CancelResults:
        results = []
        for item in requests:
            try:
                receipt = self.cancel(item.order, item.context)
                results.append(ActionResult(order=item.order, receipt=receipt, error=None))
            except Exception as exc:
                # Do not discard earlier results or call the batch successful.
                # No retry, release or guessed NOT_SENT result on an exception.
                results.append(ActionResult(order=item.order, receipt=None, error=type(exc).__name__))
        return CancelResults(results=tuple(results))


class _ActionWriter:
    def __init__(self, tx, service):
        self.tx, self.service = tx, service

    @atomic_write
    def persist(self, key, fingerprint, order, context, changes):
        tx, service = self.tx, self.service
        old = tx._latest('audit_events', ACTION_PREFIX + key, ActionRecord)
        if old is not None:
            if old.request_hash != fingerprint:
                raise IdempotencyConflict('control key reused with different request')
            # Replays still need current caller/target ownership authorization;
            # a confirmed/unknown command does not require an active lifecycle.
            service._authorize(tx, order, context)
            if CommandRepository(tx).get(key) is None:
                raise MissingEvidence('action command missing')
            return
        facts = service._validate(tx, order, context, changes)
        payload = ExecutionCommandCanonicalPayload(position_side=service.scope.venue_leg,
            quantity=ABSENT if changes is None or changes.quantity is None else format(changes.quantity.value, 'f'),
            price=ABSENT if changes is None or changes.price is None else format(changes.price.value, 'f'),
            trigger_price=ABSENT if changes is None or changes.trigger_price is None else format(changes.trigger_price.value, 'f'),
            tokenize=False if service.scope.product == t.Product.US_EQUITY else ABSENT)
        command = make_command(facts.intent, service.id_codec, service.lease.shard.execution_epoch,
            key, 'CANCEL' if changes is None else 'AMEND', payload, target=order)
        CommandRepository(tx).append(command)
        save_window(tx, command, min((facts.policy.expires_at, facts.intent.valid_until), key=time_key))
        tx._append('audit_events', ACTION_PREFIX + key, ActionRecord(command_id=key,
            request_hash=fingerprint, command_hash=command_hash(command), order=order,
            context=context, changes=changes), 0)
