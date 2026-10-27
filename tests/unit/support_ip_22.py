"""Synthetic inputs for production IP-22/IP-16/IP-09/IP-06 logic.

MemoryRegistry is the IP-16 transaction test double. Its reuse proves neither
database durability nor native protection/venue behavior. No T/Q registration.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from integration.support_ip_16 import MemoryRegistry
from unit.support_ip_06 import make_case as risk_case
from unit.support_ip_09 import make_case as target_case
from trading_system.composition.policy import CompositionCompiler, CompositionError, SYSTEM_PROTECTION
from trading_system.composition.portfolio_book import (
    CompositionBinding, PortfolioBook, SignalAttestation, binding_hash, signal_hash,
)
from trading_system.contracts import types as t
from trading_system.contracts.wire import DecisionProposalEntry
from trading_system.intents.ingress import IntentCompiler
from trading_system.ownership.transfers import OwnershipService
from trading_system.risk.evaluator import RiskEvaluator


@dataclass(frozen=True)
class CompileObservation:
    valid: bool
    reason: str | None


class CompositionCase:
    def __init__(self):
        self.risk = risk_case(t.Product.SPOT)
        self.base = target_case(t.Product.SPOT)
        self.scope = self.risk.book.scope
        self.book = self.risk.book
        self.clock = self.risk.inputs.clock
        self.owner = self.risk.intent.owner_strategy_id
        self.refs = (self.risk.market.ref, *self.risk.inputs.evidence)
        self.proposal = replace(self.base.proposal,
            instrument_ref=self.scope.instrument_uid,
            target=replace(self.base.proposal.target, side='LONG', sizing='BASE_QUANTITY', value='2'),
            entry=DecisionProposalEntry(order_preference='MARKET', limit_price=None,
                maximum_entry_slippage_bps='20', entry_deadline=self.risk.intent.valid_until, allow_partial=True),
            exit_plan=replace(self.base.proposal.exit_plan, stop_price='90',
                review_due_at=self.risk.intent.exit_plan.review_due_at),
            evidence_refs=tuple(r.id for r in self.refs), valid_until=self.risk.intent.valid_until)
        self.policy = self.policy_for()
        self.compiled = CompositionCompiler().compile(self.policy)
        self.binding = CompositionBinding(scope=self.scope, owner_book_id='book', owner_strategy_id=self.owner,
            ownership_epoch=1, decision_run_id='ip22-decision', book_sequence=self.book.sequence,
            basis_fingerprint=self.book.basis_fingerprint, instrument_ref=self.scope.instrument_uid,
            expires_at=self.risk.intent.valid_until, deadline_monotonic_ns=self.clock.monotonic_ns + 10000,
            evidence=self.refs)
        self.seed_position('0')

    def policy_for(self, research='ai', entry='rules'):
        return t.DecisionComposition(composition_id='ip22-composition', revision=1, stages=(
            t.CompositionStage(stage_id='selection', responsibility='RESEARCH', manager_id=research),
            t.CompositionStage(stage_id='entry', responsibility='ENTRY', manager_id=entry),
            t.CompositionStage(stage_id='exit', responsibility='EXIT', manager_id=self.owner),
            t.CompositionStage(stage_id='protection', responsibility='PROTECTION', manager_id=SYSTEM_PROTECTION)))

    def seed_position(self, value):
        position = t.ManagedPosition(position_id='ip22-position', scope=self.scope,
            actual_quantity=t.Quantity(value=Decimal(value), unit=self.risk.inputs.instrument.quantity_step.unit),
            owner_strategy_id=self.owner, owner_book_id='book', ownership_epoch=1,
            cost_evidence=self.book.coverage, realized_pnl=None, unrealized_valuation=None,
            active_exit_plan_id='preserved-exit-plan', risk_locks=())
        self.registry = MemoryRegistry()
        self.registry.owners[self.scope] = (self.owner, 1, 1)
        self.registry.register(position, 1, contributors=('ai', 'rules'))
        self.book = replace(self.book, positions=(position,) if Decimal(value) else ())
        self.binding = replace(self.binding, previous_proposal=self.proposal if Decimal(value) else None)
        return position

    def signal(self, stage_id='entry', *, value='2', side='LONG', action='SET_TARGET', proposal=None):
        proposed = proposal or replace(self.proposal, proposal_id='source:' + stage_id, action=action,
                                      target=replace(self.proposal.target, side=side, value=value))
        return t.DecisionSignal(stage_id=stage_id, scope=self.scope, proposal=proposed, evidence=self.refs)

    def entries(self):
        return [self.signal('selection'), self.signal('entry')]

    def attest(self, signals, *, compiled=None, binding=None):
        compiled = compiled or self.compiled
        binding = binding or self.binding
        stages = {s.stage_id: s for s in compiled.policy.stages}
        return tuple(SignalAttestation(stage_id=s.stage_id, manager_id=stages[s.stage_id].manager_id,
            signal_hash=signal_hash(s), composition_hash=compiled.hash, binding_hash=binding_hash(binding))
            for s in signals)

    def portfolio(self, signals, *, compiled=None, binding=None, attestations=None):
        compiled = compiled or self.compiled
        binding = binding or self.binding
        return PortfolioBook(compiled, binding, self.registry, clock=lambda: self.clock,
            attestations=self.attest(signals, compiled=compiled, binding=binding)
            if attestations is None else attestations)

    def compose(self, signals=None, **kwargs):
        signals = self.entries() if signals is None else signals
        return self.portfolio(signals, **kwargs).compose(signals, self.book)

    def compile_with_competing_exit_owners(self):
        competing = t.CompositionStage(stage_id='other-exit', responsibility='EXIT', manager_id='other')
        try:
            CompositionCompiler().compile(replace(self.policy, stages=self.policy.stages + (competing,)))
        except CompositionError as exc:
            return CompileObservation(False, exc.reason)
        return CompileObservation(True, None)

    def transfer(self, mode):
        service = OwnershipService(self.registry, authorize=lambda tx, request, context: True,
                                   clock=lambda: self.clock.utc_now)
        request = t.TransferRequest(transfer_id='ip22-transfer', scope=self.scope, mode=mode,
                                   old_owner=self.owner, new_owner='new-manager', ownership_epoch=1)
        context = t.ControlContext(identity_id='synthetic-user', environment=self.scope.environment,
            deployment_id='synthetic-deployment', authorization_id='synthetic-authorization',
            policy_revision=1, ownership_epoch=1, scopes=(self.scope,), expected_sequence=1,
            idempotency_key='ip22-transfer-key')
        return service.request_transfer(request, context)

    def through_risk(self, proposal, *, maximum_order_notional=None):
        """Real binding and hard risk calculator, using only synthetic evidence."""
        risk, base = self.risk, self.base
        grant = replace(base.binding.authorization.authorization,
            authorization_id=risk.intent.authorization_id, owner_strategy_id=self.owner,
            scopes=(self.scope,), valid_from=risk.market.ref.visible_at,
            expires_at=risk.intent.valid_until)
        auth = replace(base.binding.authorization, authorization=grant,
            evidence=tuple(replace(ref, visible_at=risk.market.ref.visible_at)
                           for ref in base.binding.authorization.evidence))
        binding = replace(base.binding, authorization=auth, instrument_ref=self.scope.instrument_uid,
            instrument=risk.inputs.instrument, collateral_group_id=risk.intent.collateral_group_id,
            snapshot=risk.market, evidence=self.refs, clock=self.clock,
            deadline_monotonic_ns=self.binding.deadline_monotonic_ns,
            maximum_valid_until=risk.intent.valid_until,
            max_planned_loss=t.Money(amount=Decimal('100'), currency='USDT'))
        context = replace(base.context, authorization_id=grant.authorization_id, scopes=(self.scope,),
            snapshot_ref=risk.market.ref, decision_run_id=self.binding.decision_run_id)
        intent = IntentCompiler(binding, new_id=lambda: 'ip22-intent').bind(proposal, context, self.book)
        inputs = replace(risk.inputs, intent_id=intent.intent_id, authorization_id=intent.authorization_id,
            owner_strategy_id=intent.owner_strategy_id, collateral_group_id=intent.collateral_group_id,
            basis_fingerprint=self.book.basis_fingerprint, book_sequence=self.book.sequence)
        policy = risk.policy if maximum_order_notional is None else replace(risk.policy,
            maximum_order_notional=maximum_order_notional)
        decision = RiskEvaluator(inputs).evaluate(intent, self.book, risk.market, policy)
        return intent, decision


@pytest.fixture
def composition_case():
    return CompositionCase()
