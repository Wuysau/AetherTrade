"""PAPER atomic LIMIT admission through the existing IP-09/IP-07 transaction.

The legacy intent is an internal risk projection. The immutable v2 sidecar is
mandatory for program dispatch and preserves fields absent from that projection.
Qualification is independently resolved by a trusted local reader, never inferred
from constructing a snapshot, a compile result, or a caller supplied boolean.
"""
from dataclasses import dataclass
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_program import OrderProposal, identifier
from trading_system.contracts.order_snapshots import OrderRuleSnapshot, OrderCapabilitySnapshot
from trading_system.contracts.wire import (
    ContractCodec, DecisionProposal, DecisionProposalTarget, DecisionProposalEntry,
    DecisionProposalRationale,
)
from trading_system.execution.commands import command_hash
from trading_system.storage.order_programs import OrderProgramRepository
from .ingress import ProposalIngress, IngressInputs, accepted_intent
from .order_program_compiler import AtomicCompilation, compile_atomic_target
from .semantic_validation import require

COMMAND_PREFIX = 'order-program-command:'


@dataclass(frozen=True, kw_only=True)
class ExecutionPolicy:
    """Explicit server selected policy, since v2 has no slippage-cap field."""
    policy_id: str
    revision: int
    maximum_entry_slippage_bps: str

    def __post_init__(self):
        identifier(self.policy_id)
        require(type(self.revision) is int and self.revision > 0, 'EXECUTION_POLICY_REQUIRED')
        require(isinstance(self.maximum_entry_slippage_bps, str), 'EXECUTION_POLICY_REQUIRED')
        value = Decimal(self.maximum_entry_slippage_bps)
        require(value.is_finite() and 0 <= value <= 10000, 'EXECUTION_POLICY_REQUIRED')


@dataclass(frozen=True, kw_only=True)
class AdmissionInputs:
    ingress: IngressInputs
    rules: OrderRuleSnapshot
    capabilities: OrderCapabilitySnapshot
    expected_build_digest: str
    expected_rule_revision: int
    expected_capabilities_hash: str
    execution_policy: ExecutionPolicy


@dataclass(frozen=True, kw_only=True)
class PaperQualification:
    """Resolved by the host from immutable test evidence under the same tx.

    The host reader must check the artifacts/results, not echo snapshot claims.
    This port's presence alone is not qualification; no default reader exists.
    """
    scope: t.ScopeKey
    capabilities_hash: str
    rules_hash: str
    adapter_build_digest: str
    evidence: tuple[t.EvidenceRef, ...]
    valid_until: str


@dataclass(frozen=True, kw_only=True)
class OrderProgramCommandBinding:
    command_id: str
    command_hash: str
    intent_hash: str
    compilation: AtomicCompilation
    rules: OrderRuleSnapshot
    capabilities: OrderCapabilitySnapshot
    execution_policy: ExecutionPolicy


@dataclass(frozen=True, kw_only=True)
class ProgramAdmission:
    proposal_hash: str
    context_hash: str
    compilation: AtomicCompilation
    execution_policy: ExecutionPolicy
    outcome: t.ReservationOutcome


@dataclass(frozen=True, kw_only=True)
class ProgramAdmissionReceipt:
    receipt: t.ProposalReceipt
    reservation: t.ReservationOutcome | None


def project_proposal(proposal: OrderProposal, policy: ExecutionPolicy) -> DecisionProposal:
    """Lossless risk projection for the supported subset; sidecar retains TIF."""
    VersionedOrderCodec().encode('order-proposal', proposal)
    require(type(policy) is ExecutionPolicy, 'EXECUTION_POLICY_REQUIRED')
    p = proposal.program
    require(proposal.action == 'SET_TARGET' and p is not None and p.template == 'ATOMIC', 'ATOMIC_TARGET_REQUIRED')
    node = p.nodes[0]
    require(node.role == 'ENTRY' and node.child_origin == 'APPLICATION_GENERATED_CHILD',
            'NODE_COMPILER_NOT_IMPLEMENTED')
    o = node.order
    require(p.execution_mode == 'PAPER_MODEL', 'PAPER_MODEL_REQUIRED')
    require(o.side == 'BUY' and proposal.target_side == 'LONG' and o.price.kind == 'FIXED_LIMIT'
            and o.trigger.kind == 'NONE', 'ORDER_COMPILER_NOT_IMPLEMENTED')
    require(o.quantity.kind == proposal.target_sizing and o.quantity.value == proposal.target_value,
            'TARGET_SPEC_MISMATCH')
    require(o.tif.kind in ('GTC', 'IOC', 'FOK') and o.tif.expires_at is None, 'TIF_COMPILER_NOT_IMPLEMENTED')
    require(not o.post_only and o.iceberg_quantity is None and o.stp == 'NONE' and o.routing == 'STANDARD'
            and o.position_side == 'BOTH' and not o.reduce_only and o.trading_session is None
            and o.tokenize is None, 'MODIFIER_COMPILER_NOT_IMPLEMENTED')
    return DecisionProposal(contract_version='2026-10-20', proposal_id=proposal.proposal_id,
        instrument_ref=proposal.instrument_ref, action=proposal.action,
        target=DecisionProposalTarget(side=proposal.target_side, sizing=proposal.target_sizing, value=proposal.target_value),
        requested_leverage=proposal.requested_leverage,
        entry=DecisionProposalEntry(order_preference='LIMIT', limit_price=o.price.value,
            maximum_entry_slippage_bps=policy.maximum_entry_slippage_bps,
            entry_deadline=p.submit_valid_until, allow_partial=o.tif.kind != 'FOK'),
        exit_plan=proposal.exit_plan, evidence_refs=proposal.evidence_refs,
        rationale=DecisionProposalRationale(summary=proposal.summary, counter_evidence=(), missing_information=()),
        valid_until=proposal.valid_until)


def validate_compilation(tx, proposal, compilation, facts, load_qualification):
    scope = tx.scope
    require(scope.environment == t.Environment.PAPER, 'ENVIRONMENT_DISABLED')
    f, rules, caps = facts.ingress.binding, facts.rules, facts.capabilities
    now = f.clock.utc_now
    expected = compile_atomic_target(proposal, rules, caps, scope=scope, now=now,
        expected_build_digest=facts.expected_build_digest, expected_rule_revision=facts.expected_rule_revision,
        expected_capabilities_hash=facts.expected_capabilities_hash, expected_instrument_ref=f.instrument_ref,
        allow_round_down=f.allow_round_down)
    require(compilation == expected, 'COMPILATION_CHANGED')
    require(caps.transport == 'PAPER' and caps.qualification == 'PAPER_VERIFIED', 'PAPER_QUALIFICATION_REQUIRED')
    proof = None if load_qualification is None else load_qualification(tx, facts, now)
    require(type(proof) is PaperQualification and proof.scope == scope
            and proof.capabilities_hash == expected.capabilities_hash and proof.rules_hash == expected.rules_hash
            and proof.adapter_build_digest == facts.expected_build_digest and proof.evidence == caps.evidence
            and bool(proof.evidence) and utc_timestamp(now) < utc_timestamp(proof.valid_until),
            'PAPER_QUALIFICATION_REQUIRED')
    require(all(e.environment == scope.environment and bool(e.source) and len(e.hash) == 64
                and all(c in '0123456789abcdef' for c in e.hash)
                and utc_timestamp(e.visible_at) <= utc_timestamp(now) for e in proof.evidence),
            'PAPER_QUALIFICATION_REQUIRED')
    spec = f.instrument
    require(spec.rule_revision == rules.revision and spec.venue == rules.venue
            and spec.instrument_uid == scope.instrument_uid and spec.product == scope.product
            and spec.tick_size.value == Decimal(rules.tick_size)
            and spec.quantity_step.value == Decimal(rules.quantity_step)
            and spec.minimum_quantity.value == Decimal(rules.minimum_quantity)
            and spec.minimum_notional.amount == Decimal(rules.minimum_notional)
            and spec.quote_currency == rules.quote_asset and spec.tick_size.currency == rules.quote_asset
            and spec.minimum_notional.currency == rules.quote_asset
            and spec.settlement_currency == rules.quote_asset, 'RULE_PROJECTION_MISMATCH')
    require(f.protection_policy == proposal.protection_policy, 'PROTECTION_POLICY_MISMATCH')
    return expected


class OrderProgramIngress:
    """Trusted host composition; callbacks must read local evidence, never venue/LLM.

    execution_policy is selected explicitly by the server, then checked against
    current locked policy facts. It is hashed in the admission/command sidecars.
    ReservationService is the existing service instance using this same UoW.
    """
    def __init__(self, uow, scope, load_inputs, reservation_service, *, execution_policy, load_qualification=None):
        require(scope.environment == t.Environment.PAPER, 'ENVIRONMENT_DISABLED')
        require(reservation_service.uow is uow and reservation_service.scope == scope, 'RESERVATION_BINDING_MISMATCH')
        self.uow, self.scope, self.load_inputs = uow, scope, load_inputs
        self.reservations, self.policy, self.load_qualification = reservation_service, execution_policy, load_qualification

    def admit(self, proposal, context, compilation):
        projected = project_proposal(proposal, self.policy)
        ph = VersionedOrderCodec().hash('order-proposal', proposal)
        ch = canonical_hash(domain_payload(context))
        captured = {}

        def load(tx, legacy, ctx, book):
            facts = self.load_inputs(tx, proposal, ctx, book)
            require(type(facts) is AdmissionInputs and facts.execution_policy == self.policy, 'EXECUTION_POLICY_CHANGED')
            validate_compilation(tx, proposal, compilation, facts, self.load_qualification)
            captured['facts'] = facts
            return facts.ingress

        def accepted(tx, record):
            intent = accepted_intent(record)
            require(intent.target_quantity == compilation.normalized_target_quantity, 'NORMALIZED_TARGET_MISMATCH')
            outcome = self.reservations._reserve(tx, intent, record.book_sequence, accepted=record)
            OrderProgramRepository(tx).save_program(proposal.program)
            admission = ProgramAdmission(proposal_hash=ph, context_hash=ch, compilation=compilation,
                execution_policy=self.policy, outcome=outcome)
            tx._append('audit_events', 'order-program-admission:' + intent.intent_id, admission, 0)
            if outcome.accepted:
                # IP-07's initial command is one immutable durable request.
                from trading_system.risk.reservation_repository import ReservationRepository
                originals = ReservationRepository(tx).first_commands(intent.intent_id)
                require(len(originals) == 1, 'PROGRAM_COMMAND_REQUIRED')
                command = originals[0]
                require(command.canonical_payload.time_in_force == compilation.time_in_force,
                        'PROGRAM_TIF_MISMATCH')
                binding = OrderProgramCommandBinding(command_id=command.command_id, command_hash=command_hash(command),
                    intent_hash=ContractCodec().hash(intent), compilation=compilation,
                    rules=captured['facts'].rules, capabilities=captured['facts'].capabilities,
                    execution_policy=self.policy)
                tx._append('audit_events', COMMAND_PREFIX + command.command_id, binding, 0)
            captured['outcome'] = outcome

        def replay(tx, record):
            previous = tx._latest('audit_events', 'order-program-admission:' + record.receipt.intent_id, ProgramAdmission)
            require(previous is not None and previous.proposal_hash == ph and previous.context_hash == ch
                    and previous.compilation == compilation and previous.execution_policy == self.policy, 'PROGRAM_REPLAY_CONFLICT')
            captured['outcome'] = previous.outcome

        receipt = ProposalIngress(self.uow, self.scope, load, on_accept=accepted, on_replay=replay).accept(projected, context)
        return ProgramAdmissionReceipt(receipt=receipt, reservation=captured.get('outcome') if receipt.status != t.IntentState.REJECTED else None)


def validate_program_command(tx, command, intent, load_current_program_facts, load_qualification):
    """Recheck exact program, rules, build and qualification before each send gate."""
    binding = tx._latest('audit_events', COMMAND_PREFIX + command.command_id, OrderProgramCommandBinding)
    require(binding is not None and binding.command_id == command.command_id
            and binding.command_hash == command_hash(command) and binding.intent_hash == ContractCodec().hash(intent),
            'PROGRAM_COMMAND_BINDING_MISMATCH')
    codec = VersionedOrderCodec()
    require(codec.hash('order-rule-snapshot', binding.rules) == binding.compilation.rules_hash
            and codec.hash('order-capability-snapshot', binding.capabilities) == binding.compilation.capabilities_hash,
            'PROGRAM_SNAPSHOT_BINDING_MISMATCH')
    proposal = binding.compilation.original_proposal
    facts = load_current_program_facts(tx, proposal, intent)
    require(type(facts) is AdmissionInputs and facts.execution_policy == binding.execution_policy,
            'EXECUTION_POLICY_CHANGED')
    validate_compilation(tx, proposal, binding.compilation, facts, load_qualification)
    expected = project_proposal(proposal, facts.execution_policy)
    require(intent.proposal_hash == ContractCodec().hash(expected), 'PROGRAM_INTENT_MISMATCH')
    payload = command.canonical_payload
    require(payload.order_type == 'LIMIT' and payload.price == binding.compilation.limit_price
            and payload.side == 'BUY' and payload.time_in_force == binding.compilation.time_in_force,
            'PROGRAM_PAYLOAD_MISMATCH')
    return binding
