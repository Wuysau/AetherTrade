"""Deterministic selection of one proposal, never implicit netting or voting."""
from __future__ import annotations

from dataclasses import dataclass

from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec, DecisionProposal
from .policy import require, verify_compiled


@dataclass(frozen=True, slots=True)
class Selection:
    proposal: DecisionProposal
    responsibility: str
    contributions: tuple[t.DecisionSignal, ...]


def _economic_payload(proposal: DecisionProposal) -> dict:
    payload = ContractCodec().encode(proposal)
    for key in ('proposal_id', 'rationale', 'evidence_refs', 'valid_until'):
        payload.pop(key)
    # SET_TARGET deadlines may only be shortened when sources are combined.
    # UPDATE_EXIT must preserve prior execution fields verbatim (IP-09).
    if proposal.action == 'SET_TARGET':
        payload['entry'].pop('entry_deadline')
    return payload


class DecisionArbiter:
    def select(self, compiled: t.CompiledComposition,
               signals: tuple[t.DecisionSignal, ...]) -> Selection | None:
        """Caller verifies provenance/scope/time before invoking this pure selector.

        Every declared stage for the selected responsibility must participate.
        Research gates constrain entry selection/direction, not position sizing;
        user capital boundaries are enforced by the shared authorization/risk path.
        Missing or HOLD gates yield no proposal. Ordinary exits do not need research.
        """
        verify_compiled(compiled)
        stages = {s.stage_id: s for s in compiled.policy.stages}
        by_id: dict[str, t.DecisionSignal] = {}
        for signal in signals:
            require(signal.stage_id in stages, 'UNDECLARED_STAGE')
            require(signal.stage_id not in by_id, 'DUPLICATE_STAGE_SIGNAL')
            role = stages[signal.stage_id].responsibility
            require(role != 'PROTECTION', 'PROTECTION_SIGNAL_FORBIDDEN')
            proposal = signal.proposal
            if role in ('RESEARCH', 'ENTRY'):
                require(proposal.action == 'HOLD' or
                        (proposal.action == 'SET_TARGET' and proposal.target.side != 'FLAT'),
                        'STAGE_ACTION_FORBIDDEN')
            by_id[signal.stage_id] = signal

        def stage_ids(role):
            return tuple(s.stage_id for s in compiled.policy.stages if s.responsibility == role)

        def ready(ids):
            return all(i in by_id and by_id[i].proposal.action != 'HOLD' for i in ids)

        exits = stage_ids('EXIT')
        is_exit = any(i in by_id and by_id[i].proposal.action != 'HOLD' for i in exits)
        decision_ids = exits if is_exit else stage_ids('ENTRY')
        if not ready(decision_ids):
            return None
        candidates = tuple(by_id[i] for i in decision_ids)
        proposal = candidates[0].proposal
        expected = _economic_payload(proposal)
        require(all(_economic_payload(s.proposal) == expected for s in candidates), 'SIGNAL_CONFLICT')
        research = () if is_exit else stage_ids('RESEARCH')
        if not ready(research):
            return None
        require(all(by_id[i].proposal.target.side == proposal.target.side for i in research),
                'RESEARCH_DIRECTION_CONFLICT')
        contributions = tuple(sorted(candidates + tuple(by_id[i] for i in research),
                                     key=lambda s: s.stage_id))
        return Selection(proposal, 'EXIT' if is_exit else 'ENTRY', contributions)
