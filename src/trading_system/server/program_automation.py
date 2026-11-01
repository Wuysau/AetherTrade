"""Server composition for the unified program decision path.

The host is explicit: no default frame builder, admission, rule proposal or
model research grant exists. Missing dependencies keep automation blocked.
The local-child host is exposed for status/observation only; no HTTP write path.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable, Literal

from .program_decision import (
    ProgramDecision, admit_decision, build_ai_decision, build_hybrid_decision,
    build_rule_decision,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramAutomationStatus:
    configured: bool
    frame_builder: bool
    admission: bool
    rule_proposal: bool
    hybrid_gate: bool
    local_child_host: bool
    execution_authorized: Literal[False] = False


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramAutomationRun:
    result: dict
    decision: ProgramDecision
    frame: object


class ProgramAutomationHost:
    def __init__(self, *, frame_builder: Callable, admission: Callable,
                 rule_proposal: Callable | None = None,
                 hybrid_gate: Callable | None = None,
                 research_request: Callable | None = None,
                 local_child_host=None):
        if not callable(frame_builder) or not callable(admission):
            raise ValueError('PROGRAM_AUTOMATION_COMPOSITION_REQUIRED')
        self.frame_builder = frame_builder
        self.admission = admission
        self.rule_proposal = rule_proposal
        self.hybrid_gate = hybrid_gate
        self.research_request = research_request
        self.local_child_host = local_child_host

    def build_frame(self, strategy, context):
        frame = self.frame_builder(strategy, context)
        if frame is None:
            raise ValueError('PROGRAM_FRAME_REQUIRED')
        return frame

    def ai_decision(self, strategy, frame, assessment) -> ProgramDecision:
        return build_ai_decision(assessment, frame,
                                 strategy_id=strategy['id'],
                                 strategy_revision=strategy['revision'])

    def rule_decision(self, strategy, frame) -> ProgramDecision:
        if not callable(self.rule_proposal):
            raise ValueError('RULE_PROGRAM_PROPOSAL_BUILDER_REQUIRED')
        return build_rule_decision(self.rule_proposal(strategy, frame), frame,
                                   strategy_id=strategy['id'],
                                   strategy_revision=strategy['revision'])

    def hybrid_decision(self, strategy, frame, assessment) -> ProgramDecision:
        if not callable(self.rule_proposal) or not callable(self.hybrid_gate):
            raise ValueError('HYBRID_PROGRAM_GATE_REQUIRED')
        return build_hybrid_decision(assessment, self.rule_proposal(strategy, frame),
                                     frame, strategy_id=strategy['id'],
                                     strategy_revision=strategy['revision'],
                                     gate=self.hybrid_gate)

    def admit(self, decision: ProgramDecision, frame):
        return admit_decision(decision, frame, admission=self.admission)

    async def run(self, strategy, context, analyze):
        frame = self.build_frame(strategy, context)
        mode = strategy.get('mode')
        if mode == 'RULE':
            decision = self.rule_decision(strategy, frame)
            payload = {'schema_revision': 'program_v1', 'source': 'RULE',
                       'context_hash': decision.context_hash,
                       'capabilities_hash': decision.capabilities_hash,
                       'rules_hash': decision.rules_hash,
                       'expected_build_digest': decision.expected_build_digest,
                       'strategy_revision': decision.strategy_revision,
                       'action': getattr(decision.proposal, 'action', None),
                       'execution_authorized': False}
            return ProgramAutomationRun(result={'decision': payload, 'usage': None},
                                        decision=decision, frame=frame)
        result = await analyze(frame)
        payload = dict(result.get('decision') or {})
        proposal = payload.get('proposal')
        if isinstance(proposal, dict):
            proposal = SimpleNamespace(**proposal)
        assessment = SimpleNamespace(context_hash=payload.get('context_hash', frame.context_hash),
                                     proposal=proposal)
        if mode == 'AI':
            decision = self.ai_decision(strategy, frame, assessment)
        elif mode == 'HYBRID':
            decision = self.hybrid_decision(strategy, frame, assessment)
        else:
            raise ValueError('PROGRAM_AUTOMATION_MODE_REQUIRED')
        payload.update(schema_revision='program_v1', context_hash=decision.context_hash,
                       capabilities_hash=decision.capabilities_hash,
                       rules_hash=decision.rules_hash,
                       expected_build_digest=decision.expected_build_digest,
                       strategy_revision=decision.strategy_revision,
                       execution_authorized=False)
        result = {**result, 'decision': payload}
        return ProgramAutomationRun(result=result, decision=decision, frame=frame)

    def status(self) -> ProgramAutomationStatus:
        return ProgramAutomationStatus(
            configured=True, frame_builder=True, admission=True,
            rule_proposal=callable(self.rule_proposal),
            hybrid_gate=callable(self.hybrid_gate),
            local_child_host=self.local_child_host is not None)
