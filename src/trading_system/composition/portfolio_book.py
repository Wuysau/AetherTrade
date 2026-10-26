"""One offline book target through declared, authenticated stage contributions.

The host supplies CompositionBinding and SignalAttestation from its trusted
deployment/decision-run store. They are NOT model-controlled request DTOs.
DecisionSignal alone has no producer identity, epoch or revision and is rejected
without this binding. The host must authenticate a stage before attesting it.

Outputs remain DecisionProposal: pass them to IP-09 binding and the common IP-06
risk system, then recheck ownership under the execution-owner guard. This module
has no account client, command/permit creation, owner registration or handoff.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Callable

from jsonschema.exceptions import ValidationError

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.wire import ContractCodec, DecisionProposal, DecisionProposalRationale
from trading_system.intents.semantic_validation import complete, time_key
from trading_system.ownership.registry import OwnershipRegistry
from .arbiter import DecisionArbiter, Selection
from .policy import CompositionError, identifier, require, verify_compiled


@dataclass(frozen=True, slots=True, kw_only=True)
class CompositionBinding:
    scope: t.ScopeKey
    owner_book_id: str
    owner_strategy_id: str
    ownership_epoch: int
    decision_run_id: str
    book_sequence: int
    basis_fingerprint: str
    instrument_ref: str
    expires_at: str
    deadline_monotonic_ns: int
    evidence: tuple[t.EvidenceRef, ...]
    previous_proposal: DecisionProposal | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalAttestation:
    stage_id: str
    manager_id: str
    signal_hash: str
    composition_hash: str
    binding_hash: str


def signal_hash(signal: t.DecisionSignal) -> str:
    return canonical_hash(domain_payload(signal))


def binding_hash(binding: CompositionBinding) -> str:
    return canonical_hash(domain_payload(binding))


class PortfolioBook:
    def __init__(self, compiled: t.CompiledComposition, binding: CompositionBinding,
                 ownership: OwnershipRegistry, *, attestations: tuple[SignalAttestation, ...],
                 clock: Callable[[], t.ClockReading]):
        verify_compiled(compiled)
        require(type(binding) is CompositionBinding, 'INVALID_COMPOSITION_BINDING')
        require(type(attestations) is tuple and
                all(type(a) is SignalAttestation for a in attestations), 'INVALID_ATTESTATIONS')
        self.compiled, self.binding, self.ownership = compiled, binding, ownership
        self.attestations, self.clock = attestations, clock

    def compose(self, signals: list[t.DecisionSignal], book: t.BookSnapshot) -> DecisionProposal | None:
        verify_compiled(self.compiled)
        now = self.clock()
        try:
            self._validate_inputs(signals, book, now)
            selected = DecisionArbiter().select(self.compiled, tuple(signals))
            if selected is None:
                return None
            # Reuse the real IP-16 gate under its transaction; never keep an
            # independent owner registry or copy an owner out of a contribution.
            f = self.binding
            with self.ownership.transaction(f.scope) as tx:
                verdict = self.ownership.check_locked(tx, f.owner_book_id, f.owner_strategy_id,
                    f.ownership_epoch, 'EXIT' if selected.responsibility == 'EXIT' else 'DECISION')
                require(verdict.allowed, verdict.reason or 'OWNERSHIP_REJECTED')
                record = tx.owner()
                actual = record.position
                require(type(actual.actual_quantity.value) is Decimal
                        and actual.actual_quantity.value.is_finite(), 'INVALID_POSITION_QUANTITY')
                require((not book.positions and actual.actual_quantity.value == 0) or
                        book.positions == (actual,), 'OWNER_BOOK_MISMATCH')
                self._validate_effect(selected, actual)
                return self._proposal(selected)
        except CompositionError:
            raise
        except (ValidationError, ValueError, TypeError, AttributeError, UnicodeError) as exc:
            raise CompositionError('INVALID_COMPOSITION_INPUT') from exc

    def _validate_inputs(self, signals, book, now):
        f = self.binding
        require(type(f.scope) is t.ScopeKey and
                f.scope.environment in (t.Environment.PAPER, t.Environment.RESEARCH),
                'ENVIRONMENT_NOT_ENABLED')
        require(all(identifier(v) for v in (f.owner_book_id, f.owner_strategy_id,
                f.decision_run_id, f.instrument_ref)), 'INVALID_COMPOSITION_BINDING')
        require(type(f.ownership_epoch) is int and f.ownership_epoch > 0
                and type(f.book_sequence) is int and f.book_sequence >= 0
                and type(f.basis_fingerprint) is str and bool(f.basis_fingerprint),
                'INVALID_COMPOSITION_BINDING')
        require(self.compiled.ordinary_exit_owner == f.owner_strategy_id, 'OWNERSHIP_CONFLICT')
        require(type(now) is t.ClockReading and now.clock_health == 'HEALTHY'
                and type(now.monotonic_ns) is int and type(f.deadline_monotonic_ns) is int
                and 0 <= now.monotonic_ns < f.deadline_monotonic_ns, 'DECISION_DEADLINE')
        require(time_key(now.utc_now) < time_key(f.expires_at), 'COMPOSITION_EXPIRED')
        require(type(book) is t.BookSnapshot and book.scope == f.scope, 'BOOK_SCOPE_MISMATCH')
        require(type(book.sequence) is int and book.sequence == f.book_sequence
                and book.basis_fingerprint == f.basis_fingerprint, 'BOOK_DRIFT')
        require(type(book.coverage) is t.Coverage and complete(book.coverage, f.scope)
                and type(book.evidence) is tuple and bool(book.evidence), 'BOOK_INCOMPLETE')
        require(type(book.positions) is tuple and len(book.positions) <= 1
                and all(type(p) is t.ManagedPosition and p.scope == f.scope for p in book.positions),
                'MULTIPLE_REAL_POSITIONS')
        require(type(book.active_orders) is tuple and all(
            o.order.identity.scope == f.scope and o.order.owner_strategy_id == f.owner_strategy_id
            and o.order.ownership_epoch == f.ownership_epoch for o in book.active_orders),
            'ORDER_OWNERSHIP_CONFLICT')
        require(type(f.evidence) is tuple and bool(f.evidence), 'EVIDENCE_REQUIRED')
        refs = {}
        for ref in f.evidence:
            require(isinstance(ref, t.EvidenceRef) and identifier(ref.id) and ref.id not in refs
                    and ref.environment == f.scope.environment and bool(ref.source)
                    and type(ref.hash) is str and len(ref.hash) == 64
                    and all(c in '0123456789abcdef' for c in ref.hash)
                    and time_key(ref.visible_at) <= time_key(now.utc_now), 'EVIDENCE_BINDING')
            refs[ref.id] = ref
        require(type(signals) is list and len(signals) <= len(self.compiled.policy.stages),
                'INVALID_SIGNALS')
        stages = {s.stage_id: s for s in self.compiled.policy.stages}
        attestations = {}
        expected_binding_hash = binding_hash(f)
        for attestation in self.attestations:
            require(attestation.stage_id not in attestations, 'DUPLICATE_ATTESTATION')
            attestations[attestation.stage_id] = attestation
        codec = ContractCodec()
        for signal in signals:
            require(type(signal) is t.DecisionSignal, 'INVALID_SIGNAL')
            require(signal.scope == f.scope, 'SIGNAL_SCOPE_MISMATCH')
            stage = stages.get(signal.stage_id)
            require(stage is not None, 'UNDECLARED_STAGE')
            require(stage.responsibility != 'PROTECTION', 'PROTECTION_SIGNAL_FORBIDDEN')
            proposal = signal.proposal
            require(type(proposal) is DecisionProposal, 'INVALID_PROPOSAL')
            codec.encode(proposal)
            require(proposal.instrument_ref == f.instrument_ref, 'INSTRUMENT_BINDING')
            require(type(signal.evidence) is tuple and bool(signal.evidence)
                    and len({r.id for r in signal.evidence}) == len(signal.evidence)
                    and all(refs.get(r.id) == r for r in signal.evidence)
                    and set(proposal.evidence_refs) <= {r.id for r in signal.evidence},
                    'SIGNAL_EVIDENCE_BINDING')
            require(time_key(now.utc_now) < time_key(proposal.valid_until)
                    and time_key(now.utc_now) < time_key(proposal.entry.entry_deadline)
                    <= time_key(proposal.valid_until)
                    and time_key(now.utc_now) < time_key(proposal.exit_plan.review_due_at),
                    'SIGNAL_EXPIRED')
            attestation = attestations.get(signal.stage_id)
            require(attestation is not None and attestation.manager_id == stage.manager_id
                    and attestation.composition_hash == self.compiled.hash
                    and attestation.binding_hash == expected_binding_hash
                    and attestation.signal_hash == signal_hash(signal), 'SIGNAL_NOT_ATTESTED')

    def _validate_effect(self, selection: Selection, actual: t.ManagedPosition):
        proposal, previous = selection.proposal, self.binding.previous_proposal
        quantity = actual.actual_quantity.value
        side = 'FLAT' if quantity == 0 else 'SHORT' if quantity < 0 else 'LONG'
        if actual.scope.venue_leg == 'SHORT' and quantity:
            side = 'SHORT'
        require(actual.scope.venue_leg == 'BOTH' or proposal.target.side in
                (actual.scope.venue_leg, 'FLAT'), 'CROSS_LEG_TARGET')
        require(side == 'FLAT' or proposal.target.side in (side, 'FLAT'), 'REVERSAL_REQUIRES_FLAT')
        if selection.responsibility == 'EXIT':
            require(quantity != 0, 'NO_POSITION_TO_EXIT')
            if proposal.action == 'UPDATE_EXIT':
                require(previous is not None, 'PREVIOUS_RESPONSIBILITY_REQUIRED')
                require(proposal.target == previous.target and proposal.entry == previous.entry
                        and proposal.requested_leverage == previous.requested_leverage,
                        'UPDATE_EXIT_RESPONSIBILITY_CHANGED')
            else:
                require(proposal.target.sizing == 'BASE_QUANTITY', 'EXIT_BASE_QUANTITY_REQUIRED')
                require(Decimal(proposal.target.value) < quantity.copy_abs(), 'EXIT_MUST_REDUCE')
        elif quantity:
            # No quote conversion here: only the downstream IP-09 compiler may
            # convert notional. An ambiguous resize cannot masquerade as entry.
            require(proposal.target.sizing == 'BASE_QUANTITY', 'ENTRY_EFFECT_UNPROVEN')
            # Decimal abs() rounds under the ambient context. Comparing exact
            # quantities must not turn an unchanged target into a reduction.
            require(Decimal(proposal.target.value) >= quantity.copy_abs(), 'EXIT_AUTHORITY_REQUIRED')
            require(previous is not None, 'PREVIOUS_RESPONSIBILITY_REQUIRED')
            require(proposal.exit_plan == previous.exit_plan, 'EXIT_AUTHORITY_REQUIRED')

    def _proposal(self, selection: Selection) -> DecisionProposal:
        proposal, contributors = selection.proposal, selection.contributions
        expiry = min((self.binding.expires_at, *(s.proposal.valid_until for s in contributors)),
                     key=time_key)
        entry = proposal.entry
        if proposal.action == 'SET_TARGET':
            deadline = min((expiry, *(s.proposal.entry.entry_deadline for s in contributors)), key=time_key)
            entry = replace(entry, entry_deadline=deadline)
        else:
            require(time_key(entry.entry_deadline) <= time_key(expiry), 'UPDATE_EXIT_DEADLINE_CONFLICT')
        # IDs bind the complete immutable policy, book/run/owner context and all
        # used signals. Reordered delivery is identical; no accumulating targets.
        digest = canonical_hash({'composition': self.compiled.hash, 'binding': binding_hash(self.binding),
                                 'signals': [signal_hash(s) for s in contributors]})
        rationale = DecisionProposalRationale(
            summary='; '.join(f'{s.stage_id}: {s.proposal.rationale.summary}' for s in contributors),
            counter_evidence=tuple(sorted({v for s in contributors for v in s.proposal.rationale.counter_evidence})),
            missing_information=tuple(sorted({v for s in contributors for v in s.proposal.rationale.missing_information})))
        result = replace(proposal, proposal_id='composition:' + digest, entry=entry, valid_until=expiry,
            evidence_refs=tuple(sorted({r for s in contributors for r in s.proposal.evidence_refs})),
            rationale=rationale)
        ContractCodec().encode(result)  # Oversized evidence/rationale is rejected, never silently dropped.
        return result
