"""Durable offline proposal intake with server binding and revision CAS.

The host injects its local authority/snapshot readers and IP-03 UnitOfWork.
No credentials, networks, command outbox writes, or automatic authorization.
Accepted proposals remain VALIDATING; IP-07 owns atomic Intent/Risk/Reservation/
Command persistence and must recompute the delta there via compile_locked.
"""
from dataclasses import dataclass, replace
import json
from uuid import uuid4

from jsonschema.exceptions import ValidationError

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, domain_payload
from trading_system.contracts.wire import (
    ABSENT, ContractCodec, DecisionProposal, TradeIntent, TradeIntentExecution,
    TradeIntentExitPlan, TradeIntentMaxPlannedLoss, validate_transition,
)
from trading_system.storage.models import account_key
from .semantic_validation import BindingInputs, ProposalRejected, require, validate_proposal
from .target_compiler import TargetCompiler, TargetInputs


@dataclass(frozen=True, kw_only=True)
class IngressRevision:
    """Durable deployment fence maintained by the authorized control plane.

    Stored in ledger.audit_events at revision_key(context). No missing-state
    initialization is permitted in accept(). Control-plane changes must update
    this record in the same collateral/management lock and use its sequence CAS.
    This local record fills IP-01's missing strategy-revision persistence port.
    """
    sequence: int
    identity_id: str
    deployment_id: str
    authorization_id: str
    owner_strategy_id: str
    strategy_revision: int
    risk_policy_revision: int
    ownership_epoch: int
    enabled: bool


def revision_key(context: t.DecisionContext) -> str:
    return 'ip09:revision:' + canonical_hash({'deployment': context.deployment_id,
                                              'authorization': context.authorization_id})


@dataclass(frozen=True, kw_only=True)
class AcceptedProposal:
    proposal_hash: str
    context_hash: str
    proposal_json: str
    intent_json: str
    plan: t.DeltaPlan
    receipt: t.ProposalReceipt
    book_sequence: int
    instrument: t.InstrumentSpec
    context: t.DecisionContext


@dataclass(frozen=True, kw_only=True)
class IngressInputs:
    binding: BindingInputs
    target: TargetInputs


class IntentCompiler:
    def __init__(self, inputs: BindingInputs, *, new_id=None):
        self.inputs = inputs
        self.new_id = new_id or (lambda: str(uuid4()))

    def bind(self, proposal: DecisionProposal, context: t.DecisionContext,
             basis: t.BookSnapshot) -> TradeIntent:
        f = self.inputs
        quantity = validate_proposal(proposal, context, basis, f)
        previous = f.previous_intent
        grant, scope = f.authorization.authorization, basis.scope
        if previous is not None:
            require((previous.environment, previous.venue_account_id, previous.product,
                     previous.instrument_uid, previous.venue_leg, previous.owner_strategy_id,
                     previous.ownership_epoch, previous.authorization_id, previous.risk_policy_revision)
                    == (scope.environment, scope.venue_account_id, scope.product, scope.instrument_uid,
                        scope.venue_leg, grant.owner_strategy_id, context.ownership_epoch,
                        context.authorization_id, context.policy_revision), 'PREVIOUS_SCOPE_MISMATCH')
        # UPDATE_EXIT retains the normalized prior target; a changed quote must
        # never turn a quote-notional exit update into a new position target.
        target_side = previous.target_side if proposal.action != 'SET_TARGET' else proposal.target.side
        intent = TradeIntent(contract_version=proposal.contract_version, intent_id=self.new_id(),
            environment=context.environment.value, owner_strategy_id=grant.owner_strategy_id,
            strategy_revision=context.strategy_revision, decision_run_id=context.decision_run_id,
            authorization_id=context.authorization_id, venue_account_id=scope.venue_account_id,
            collateral_group_id=f.collateral_group_id, venue=f.instrument.venue,
            product=scope.product.value, instrument_uid=scope.instrument_uid, venue_leg=scope.venue_leg,
            ownership_epoch=context.ownership_epoch, risk_policy_revision=context.policy_revision,
            basis_fingerprint=basis.basis_fingerprint, snapshot_id=f.snapshot.ref.id,
            as_of=f.snapshot.quote.occurred_at, created_at=f.clock.utc_now,
            valid_until=proposal.valid_until, evidence_refs=proposal.evidence_refs,
            action=proposal.action, target_side=target_side, target_quantity=format(quantity, 'f'),
            quantity_unit=f.instrument.quantity_step.unit,
            execution=TradeIntentExecution(order_preference=proposal.entry.order_preference,
                limit_price=proposal.entry.limit_price,
                maximum_entry_slippage_bps=proposal.entry.maximum_entry_slippage_bps,
                entry_deadline=proposal.entry.entry_deadline, allow_partial=proposal.entry.allow_partial),
            max_planned_loss=TradeIntentMaxPlannedLoss(amount=format(f.max_planned_loss.amount, 'f'),
                currency=f.max_planned_loss.currency),
            requested_leverage=1 if proposal.requested_leverage is ABSENT else proposal.requested_leverage,
            exit_plan=TradeIntentExitPlan(protection_policy=f.protection_policy,
                stop_trigger=proposal.exit_plan.stop_trigger, stop_price=proposal.exit_plan.stop_price,
                review_due_at=proposal.exit_plan.review_due_at,
                maximum_holding_seconds=proposal.exit_plan.maximum_holding_seconds),
            proposal_hash=ContractCodec().hash(proposal), rationale_summary=proposal.rationale.summary,
            supersedes_intent_id=previous.intent_id if previous is not None else None)
        ContractCodec().encode(intent)
        if proposal.action in ('HOLD', 'UPDATE_EXIT'):
            try:
                validate_transition(previous, intent)
            except ValueError as exc:
                raise ProposalRejected('RESPONSIBILITY_CHANGED') from exc
        return intent


class ProposalIngress:
    """One scope per service; caller context comes from authenticated middleware.

    load_inputs(tx, proposal, context, book) must be local-only and reload current
    authority, freshness, rules, responsibilities and order/reservation mappings.
    Database errors propagate: a failed commit must never return an acceptance.
    """
    def __init__(self, uow, scope: t.ScopeKey, load_inputs):
        require(scope.environment != t.Environment.LIVE, 'LIVE_DISABLED')
        self.uow, self.scope, self.load_inputs = uow, scope, load_inputs

    def accept(self, proposal: DecisionProposal, context: t.DecisionContext) -> t.ProposalReceipt:
        try:
            codec = ContractCodec()
            payload = codec.encode(proposal)
            require(self.scope in context.scopes and context.environment == self.scope.environment,
                    'CONTEXT_SCOPE_MISMATCH')
            proposal_hash = canonical_hash(payload)
            context_hash = canonical_hash(domain_payload(context))
            key = 'ip09:proposal:' + canonical_hash({'identity': context.identity_id,
                'deployment': context.deployment_id, 'proposal_id': proposal.proposal_id})
            with self.uow.transaction(self.scope) as tx:
                owner = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
                    'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR UPDATE',
                    (*account_key(self.scope), self.scope.instrument_uid, self.scope.venue_leg)).fetchone()
                fence = tx._latest('audit_events', revision_key(context), IngressRevision)
                require(fence is not None and fence.enabled is True and fence.sequence >= 1,
                        'DEPLOYMENT_FENCE_REQUIRED')
                require((fence.identity_id, fence.deployment_id, fence.authorization_id,
                         fence.strategy_revision, fence.risk_policy_revision, fence.ownership_epoch)
                        == (context.identity_id, context.deployment_id, context.authorization_id,
                            context.strategy_revision, context.policy_revision, context.ownership_epoch), 'STALE_REVISION')
                require(owner == (fence.owner_strategy_id, fence.ownership_epoch, fence.risk_policy_revision), 'STALE_OWNER')
                existing = tx._latest('proposals', key, AcceptedProposal)
                if existing is not None:
                    require(existing.proposal_hash == proposal_hash and existing.context_hash == context_hash,
                            'PROPOSAL_ID_CONFLICT')
                    # A duplicate reports an earlier intake only; it is not a
                    # renewed validation, expiry extension, or send permission.
                    receipt = replace(existing.receipt, existing_result_ref=existing.receipt.receipt_id)
                else:
                    book = tx.projections.book(self.scope)
                    inputs = self.load_inputs(tx, proposal, context, book)
                    require(inputs.binding.collateral_group_id == tx.group_id
                            and inputs.binding.authorization.authorization.owner_strategy_id == fence.owner_strategy_id,
                            'DEPLOYMENT_BINDING_MISMATCH')
                    require(inputs.target.instrument == inputs.binding.instrument
                            and inputs.target.clock == inputs.binding.clock
                            and inputs.target.context == context
                            and inputs.target.allow_round_down == inputs.binding.allow_round_down
                            and inputs.target.previous_intent == inputs.binding.previous_intent
                            and inputs.target.deadline_monotonic_ns == inputs.binding.deadline_monotonic_ns,
                            'COMPILER_INPUT_MISMATCH')
                    intent = IntentCompiler(inputs.binding).bind(proposal, context, book)
                    plan = TargetCompiler(inputs.target).compile(intent, book)
                    reason = ('HOLD_NO_ACTION' if proposal.action == 'HOLD' else
                              'EXIT_RISK_REVIEW_REQUIRED' if proposal.action == 'UPDATE_EXIT' else
                              'CANCEL_AND_RECONCILE_REQUIRED' if plan.cancel_first else 'RISK_VALIDATION_REQUIRED')
                    receipt = t.ProposalReceipt(receipt_id=str(uuid4()), status=t.IntentState.VALIDATING,
                        reason=reason, existing_result_ref=None, intent_id=intent.intent_id)
                    record = AcceptedProposal(proposal_hash=proposal_hash, context_hash=context_hash,
                        proposal_json=canonical_json(payload), intent_json=canonical_json(codec.encode(intent)),
                        plan=plan, receipt=receipt, book_sequence=book.sequence,
                        instrument=inputs.binding.instrument, context=context)
                    # Both revisions and the intake record commit atomically.
                    tx._append('audit_events', revision_key(context), replace(fence, sequence=fence.sequence + 1),
                               fence.sequence)
                    tx._append('proposals', key, record, 0)
            return receipt  # only after the UnitOfWork commit barrier
        except (ProposalRejected, ValidationError) as exc:
            reason = str(exc) if isinstance(exc, ProposalRejected) else 'INVALID_PROPOSAL_SCHEMA'
            return t.ProposalReceipt(receipt_id=str(uuid4()), status=t.IntentState.REJECTED,
                                     reason=reason, existing_result_ref=None, intent_id=None)


def accepted_intent(record: AcceptedProposal) -> TradeIntent:
    """Decode immutable intake for revalidation, never directly for submission."""
    return ContractCodec().decode('trade-intent', json.loads(record.intent_json))
