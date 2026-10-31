"""One typed decision path for AI, RULE and HYBRID program automation.

The three modes may produce their proposal differently, but they converge on the
same ProgramDecision binding and the same admission callable. This module does
not call a model, create a command or send an order; admission remains the
existing financial kernel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_program import identifier


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramDecision:
    strategy_id: str
    strategy_revision: int
    mode: Literal['AI', 'RULE', 'HYBRID']
    context_hash: str
    capabilities_hash: str
    rules_hash: str
    expected_build_digest: str
    proposal: object
    source: Literal['MODEL', 'RULE', 'HYBRID']

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


def _binding(frame):
    codec = VersionedOrderCodec()
    return (frame.context_hash,
            codec.hash('order-capability-snapshot', frame.capabilities),
            codec.hash('order-rule-snapshot', frame.rules),
            frame.expected_build_digest)


def _decision(*, strategy_id, strategy_revision, mode, frame, proposal, source):
    identifier(strategy_id)
    if type(strategy_revision) is not int or strategy_revision < 1:
        raise ValueError('PROGRAM_STRATEGY_REVISION_REQUIRED')
    if proposal is None:
        raise ValueError('PROGRAM_PROPOSAL_REQUIRED')
    context_hash, capabilities_hash, rules_hash, build = _binding(frame)
    return ProgramDecision(strategy_id=strategy_id, strategy_revision=strategy_revision,
                           mode=mode, context_hash=context_hash,
                           capabilities_hash=capabilities_hash, rules_hash=rules_hash,
                           expected_build_digest=build, proposal=proposal, source=source)


def build_ai_decision(assessment, frame, *, strategy_id, strategy_revision) -> ProgramDecision:
    if getattr(assessment, 'context_hash', None) != frame.context_hash:
        raise ValueError('PROGRAM_CONTEXT_HASH_MISMATCH')
    proposal = getattr(assessment, 'proposal', None)
    if proposal is None or getattr(proposal, 'action', None) != 'SET_TARGET':
        raise ValueError('PROGRAM_AI_TARGET_REQUIRED')
    return _decision(strategy_id=strategy_id, strategy_revision=strategy_revision,
                     mode='AI', frame=frame, proposal=proposal, source='MODEL')


def build_rule_decision(proposal, frame, *, strategy_id, strategy_revision) -> ProgramDecision:
    if getattr(proposal, 'action', None) != 'SET_TARGET':
        raise ValueError('PROGRAM_RULE_TARGET_REQUIRED')
    return _decision(strategy_id=strategy_id, strategy_revision=strategy_revision,
                     mode='RULE', frame=frame, proposal=proposal, source='RULE')


def build_hybrid_decision(assessment, proposal, frame, *, strategy_id, strategy_revision,
                          gate: Callable) -> ProgramDecision:
    if not callable(gate) or gate(assessment, proposal) is not True:
        raise ValueError('PROGRAM_HYBRID_GATE_REQUIRED')
    if getattr(assessment, 'context_hash', None) != frame.context_hash:
        raise ValueError('PROGRAM_CONTEXT_HASH_MISMATCH')
    return _decision(strategy_id=strategy_id, strategy_revision=strategy_revision,
                     mode='HYBRID', frame=frame, proposal=proposal, source='HYBRID')


def require_current_binding(decision: ProgramDecision, frame) -> None:
    if type(decision) is not ProgramDecision:
        raise ValueError('PROGRAM_DECISION_REQUIRED')
    if (decision.context_hash, decision.capabilities_hash, decision.rules_hash,
            decision.expected_build_digest) != _binding(frame):
        raise ValueError('REPLAN_REQUIRED')


def admit_decision(decision: ProgramDecision, frame, *, admission: Callable):
    """The only execution handoff. An admitted receipt is still not a fill."""
    require_current_binding(decision, frame)
    if not callable(admission):
        raise ValueError('HOST_ADMISSION_NOT_REGISTERED')
    receipt = admission(decision.proposal, frame)
    if getattr(receipt, 'execution_authorized', None) is not False:
        raise ValueError('PROGRAM_RECEIPT_AUTHORITY_INVALID')
    return receipt


def decision_hash(decision: ProgramDecision) -> str:
    if type(decision) is not ProgramDecision:
        raise ValueError('PROGRAM_DECISION_REQUIRED')
    return canonical_hash({'strategy_id': decision.strategy_id,
                           'strategy_revision': decision.strategy_revision,
                           'mode': decision.mode, 'context_hash': decision.context_hash,
                           'capabilities_hash': decision.capabilities_hash,
                           'rules_hash': decision.rules_hash,
                           'expected_build_digest': decision.expected_build_digest,
                           'source': decision.source})
