"""Per-leg evidence and compensation proposals; never an atomic venue batch.

This evaluator neither sends commands nor releases reservations. A trusted
composition may supply a previously approved, scoped compensation policy.
Proposed cancellations still require OrderActionService and fresh authority.
There is deliberately no policy that creates a new hedge leg.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.wire import ABSENT, ExecutionCommand, TradeIntent
from .children import offline
from .projections import subtract_exact


@dataclass(frozen=True, kw_only=True)
class BatchLeg:
    intent: TradeIntent
    command: ExecutionCommand
    order: t.OwnedOrderRef
    receipt: t.CommandReceipt | None
    filled: t.Quantity
    fill_evidence: tuple[t.EvidenceRef, ...]
    held_budget: tuple[t.Money, ...]


@dataclass(frozen=True, kw_only=True)
class CompensationPolicy:
    policy_id: str
    approval_ref: t.EvidenceRef
    authorization_id: str
    owner_strategy_id: str
    policy_revision: int
    ownership_epoch: int
    scopes: tuple[t.ScopeKey, ...]
    expires_at: str
    mode: Literal['REVIEW_ONLY', 'CANCEL_OWNED_REMAINDERS']


@dataclass(frozen=True, kw_only=True)
class LegRisk:
    command_id: str
    order: t.OwnedOrderRef
    outcome: Literal['ACCEPTED', 'REJECTED', 'UNSENT', 'UNKNOWN']
    receipt: t.CommandReceipt | None
    observed_filled: t.Quantity
    possible_remaining: t.Quantity
    retained_budget: tuple[t.Money, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class BatchResult:
    legs: tuple[LegRisk, ...]
    partial_success: bool
    policy_applied: str | None
    requires_attention: bool
    atomic_success: Literal[False] = False


class BatchPolicy:
    def evaluate(self, legs: tuple[BatchLeg, ...], now: t.ClockReading,
                 policy: CompensationPolicy | None = None) -> BatchResult:
        if not legs or len({leg.command.command_id for leg in legs}) != len(legs):
            raise ValueError('nonempty batch with unique command IDs required')
        if now.clock_health != 'HEALTHY':
            raise ValueError('healthy clock required')
        timestamp = utc_timestamp(now.utc_now)
        policy_valid = policy is not None
        if policy is not None:
            if policy.mode not in ('REVIEW_ONLY', 'CANCEL_OWNED_REMAINDERS'):
                raise ValueError('unsupported compensation; no automatic hedge leg')
            ref = policy.approval_ref
            policy_valid = (bool(policy.policy_id and ref.id and ref.source)
                and len(ref.hash) == 64 and all(c in '0123456789abcdef' for c in ref.hash)
                and utc_timestamp(ref.visible_at) <= timestamp < utc_timestamp(policy.expires_at))
        outcomes = []
        for leg in legs:
            command, intent, order = leg.command, leg.intent, leg.order
            scope = order.identity.scope
            offline(scope)
            if (command.operation not in ('SUBMIT', 'CLOSE', 'SET_PROTECTION')
                    or command.intent_id != intent.intent_id
                    or (command.environment, command.venue_account_id, command.product,
                        command.instrument_uid, command.ownership_epoch, command.policy_revision) != (
                        scope.environment, scope.venue_account_id, scope.product, scope.instrument_uid,
                        order.ownership_epoch, intent.risk_policy_revision)
                    or (intent.environment, intent.venue_account_id, intent.product,
                        intent.instrument_uid, intent.venue_leg, intent.owner_strategy_id,
                        intent.ownership_epoch) != (scope.environment, scope.venue_account_id,
                        scope.product, scope.instrument_uid, scope.venue_leg,
                        order.owner_strategy_id, order.ownership_epoch)
                    or command.logical_client_order_id != order.identity.logical_client_order_id
                    or command.venue_client_order_id != order.identity.venue_client_order_id
                    or command.canonical_payload.position_side != scope.venue_leg
                    or command.canonical_payload.quantity is ABSENT):
                raise ValueError('batch command/intent/order binding mismatch')
            quantity = Decimal(command.canonical_payload.quantity)
            if (not quantity.is_finite() or quantity <= 0 or leg.filled.unit != intent.quantity_unit
                    or not leg.filled.value.is_finite() or not 0 <= leg.filled.value <= quantity
                    or (leg.filled.value > 0 and not leg.fill_evidence)
                    or any(e.environment != scope.environment or not e.id or not e.source
                           or len(e.hash) != 64 or any(c not in '0123456789abcdef' for c in e.hash)
                           or utc_timestamp(e.visible_at) > timestamp for e in leg.fill_evidence)
                    or not leg.held_budget or any(not m.amount.is_finite() or m.amount < 0
                                                  or not m.currency for m in leg.held_budget)):
                raise ValueError('batch quantity/evidence/budget invalid')
            receipt = leg.receipt
            if receipt is not None and (receipt.command_id != command.command_id or not receipt.receipt_id):
                raise ValueError('receipt belongs to a different command')
            if receipt is not None and receipt.status in (
                    t.CommandState.VENUE_CONFIRMED, t.CommandState.VENUE_REJECTED):
                if not receipt.existing_result_ref:
                    raise ValueError('venue outcome evidence reference missing')
                outcome = 'ACCEPTED' if receipt.status == t.CommandState.VENUE_CONFIRMED else 'REJECTED'
            elif (receipt is not None and receipt.status in (
                    t.CommandState.NOT_SENT, t.CommandState.ABORTED_UNSENT)
                    and command.state == receipt.status and receipt.existing_result_ref):
                outcome = 'UNSENT'
            else:
                outcome = 'UNKNOWN'
            if outcome in ('REJECTED', 'UNSENT') and leg.filled.value > 0:
                outcome = 'UNKNOWN'  # contradictory evidence is not a zero-risk leg
            outcomes.append(outcome)
            if policy is not None:
                policy_valid = policy_valid and (
                    scope in policy.scopes and policy.approval_ref.environment == scope.environment
                    and policy.authorization_id == intent.authorization_id
                    and policy.owner_strategy_id == order.owner_strategy_id
                    and policy.policy_revision == intent.risk_policy_revision
                    and policy.ownership_epoch == order.ownership_epoch)
        partial = 'ACCEPTED' in outcomes and any(o != 'ACCEPTED' for o in outcomes)
        risks = []
        for leg, outcome in zip(legs, outcomes):
            remaining = (Decimal(0) if outcome in ('REJECTED', 'UNSENT') else
                subtract_exact(Decimal(leg.command.canonical_payload.quantity), leg.filled.value))
            actions = []
            if leg.filled.value > 0:
                actions.append('VERIFY_PROTECTION_FOR_OBSERVED_FILLS')
            if outcome == 'UNKNOWN':
                actions.append('RECONCILE_ORIGINAL_COMMAND')
            if partial:
                if (outcome == 'ACCEPTED' and remaining > 0 and policy_valid
                        and policy.mode == 'CANCEL_OWNED_REMAINDERS'):
                    actions.append('REQUEST_OWNED_CANCEL')
                else:
                    actions.append('REVIEW_RESIDUAL_RISK')
            elif outcome != 'ACCEPTED':
                actions.append('REVIEW_RESIDUAL_RISK')
            # An accepted command is not a fill or a budget release. Amounts
            # remain itemized by currency and scope, including rejected legs.
            risks.append(LegRisk(command_id=leg.command.command_id, order=leg.order,
                outcome=outcome, receipt=leg.receipt, observed_filled=leg.filled,
                possible_remaining=t.Quantity(value=remaining, unit=leg.filled.unit),
                retained_budget=leg.held_budget, actions=tuple(actions)))
        return BatchResult(legs=tuple(risks), partial_success=partial,
            policy_applied=policy.policy_id if policy_valid else None,
            requires_attention=any(r.actions or r.possible_remaining.value > 0 for r in risks))
