"""Synthetic offline IP-14 inputs; SQL probes reuse the opt-in IP-10 runner.

No venue transport, native qualification, account discovery or provisioning.
The SQLite ID registry is not a substitute for durable PostgreSQL evidence.
"""
from dataclasses import replace
from datetime import timedelta

import pytest

from integration.support_ip_10 import DispatchCase, ip10_database
from unit.support_ip_09 import make_case, quantity, price, evidence, order
from trading_system.contracts import types as t
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.execution.children import ChildPlanner, ChildDispatcher
from trading_system.execution.cancel_amend import OrderActionFacts, OrderActionService
from trading_system.execution.batch_policy import BatchLeg, CompensationPolicy
from trading_system.intents.target_compiler import TargetCompiler


class PlanCase:
    def __init__(self):
        self.base = make_case()
        self.intent = self.base.bind()
        self.book = self.base.book
        self.now = self.base.target.clock
        self.codec = VenueIdCodec(self.book.scope)
        self.policy = t.ChildPolicy(expires_at=self.intent.valid_until,
            slice_quantity=quantity('0.004'), maximum_children=3,
            compensation_policy='REVIEW_ONLY', mutable_fields=('quantity', 'price'))
        self.planner = ChildPlanner(TargetCompiler(self.base.target), self.codec, 1,
                                    clock=lambda: self.now)

    def plan(self):
        return self.planner.plan(self.intent, self.book, self.policy)

    def leg(self, command, status, filled='0'):
        identity = self.codec.bind_venue_order(command.venue_client_order_id,
                                              'venue-order:' + command.command_id)
        return BatchLeg(intent=self.intent, command=command,
            order=t.OwnedOrderRef(identity=identity,
                owner_strategy_id=self.intent.owner_strategy_id, ownership_epoch=1),
            receipt=None if status is None else t.CommandReceipt(
                receipt_id='receipt:' + command.command_id, command_id=command.command_id,
                status=status, reason=None, existing_result_ref='synthetic-outcome'),
            filled=quantity(filled), fill_evidence=(evidence(),) if filled != '0' else (),
            held_budget=(t.Money(amount=price('200').value, currency='USDT'),))

    def compensation(self):
        return CompensationPolicy(policy_id='synthetic-approved-policy', approval_ref=evidence(),
            authorization_id=self.intent.authorization_id, owner_strategy_id=self.intent.owner_strategy_id,
            policy_revision=1, ownership_epoch=1, scopes=(self.book.scope,),
            expires_at=self.policy.expires_at, mode='CANCEL_OWNED_REMAINDERS')


@pytest.fixture
def plan_case():
    case = PlanCase()
    try:
        yield case
    finally:
        case.codec.close()


class ChildCase(DispatchCase):
    def __init__(self, dsn):
        super().__init__(dsn)
        base = make_case()
        self.codec = VenueIdCodec(self.scope)
        self.intent = replace(self.intent, target_quantity='0.010',
            execution=replace(self.intent.execution, entry_deadline=self.intent.valid_until))
        # The original IP-10 seed intent is immutable. Use a new intent identity.
        self.intent = replace(self.intent, intent_id='ip14:' + self.intent.intent_id)
        visible = (self.now - timedelta(seconds=1)).isoformat()
        self.book = replace(base.book, scope=self.scope, basis_fingerprint=self.intent.basis_fingerprint,
            coverage=replace(base.book.coverage, scope=self.scope),
            evidence=tuple(replace(ref, visible_at=visible) for ref in base.book.evidence))
        self.policy = t.ChildPolicy(expires_at=self.intent.valid_until, slice_quantity=quantity('0.004'),
            maximum_children=3, compensation_policy='REVIEW_ONLY', mutable_fields=('quantity', 'price'))
        self.target = replace(base.target, instrument=replace(base.target.instrument,
            instrument_uid=self.scope.instrument_uid), expected_instrument=replace(base.target.instrument,
            instrument_uid=self.scope.instrument_uid), context=None,
            session=replace(base.target.session, instrument_uid=self.scope.instrument_uid,
                observed_at=visible, evidence=replace(base.target.session.evidence, visible_at=visible)),
            deadline_monotonic_ns=self.lease.deadline_monotonic_ns)
        self.planner = ChildPlanner(TargetCompiler(self.target), self.codec, self.lease.shard.execution_epoch)
        with self.uow.transaction(self.scope) as tx:
            tx.intents.append(self.intent)
            tx.reservations.append(replace(self.ledger.reservation,
                reservation_id='ip14:' + self.ledger.reservation.reservation_id,
                intent_id=self.intent.intent_id, expires_at=self.intent.valid_until), 0)
            tx.projections.save_book(self.book, 0)
        self.dispatcher = ChildDispatcher(self.uow, self.scope, self.engine,
            load_facts=self.facts, owner_guard=self.guard)
        self.projections = {}
        self.action_service = OrderActionService(self.uow, self.scope, self.lease, self.codec,
            load_action_facts=self.action_facts, engine=self.engine,
            load_dispatch_facts=self.facts, owner_guard=self.guard)

    def persist(self):
        with self.uow.transaction(self.scope) as tx:
            return self.planner.persist(tx, self.intent, self.book, self.policy)

    def owned(self, identifier='owned_order', owner=None):
        client = self.codec.encode(identifier, self.scope.product, 'ORDER')
        identity = self.codec.bind_venue_order(client, 'venue:' + identifier)
        projection = order(self.scope, '0.010', identifier=identifier,
                           owner=owner or self.intent.owner_strategy_id)
        projection = replace(projection, order=replace(projection.order, identity=identity))
        self.projections[identifier] = projection
        return projection.order

    def context(self, key='cancel'):
        a = self.binding.authorization
        return t.ControlContext(identity_id=self.binding.identity_id, environment=self.scope.environment,
            deployment_id=a.deployment_id, authorization_id=a.authorization_id, policy_revision=1,
            ownership_epoch=1, scopes=(self.scope,), expected_sequence=1, idempotency_key=key)

    def action_facts(self, tx, owned, context):
        return OrderActionFacts(binding=self.binding, intent=self.intent,
            projection=self.projections[owned.identity.logical_client_order_id],
            policy=self.policy, instrument=self.target.instrument)

    def expire_windows(self):
        self.dispatcher.clock = lambda: t.ClockReading(
            utc_now=(self.now + timedelta(hours=2)).isoformat(),
            monotonic_ns=self.lease.deadline_monotonic_ns + 1, clock_health='HEALTHY')


@pytest.fixture
def child_case(ip10_database):
    case = ChildCase(ip10_database)
    try:
        yield case
    finally:
        case.codec.close()
