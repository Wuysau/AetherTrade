"""Offline OP-F wiring: host status, mode routing and program admission branch."""
from types import SimpleNamespace
from dataclasses import dataclass
import pytest

from trading_system.server import program_decision as decision_module
from trading_system.server.automation import AutomationService
from trading_system.server.program_automation import ProgramAutomationHost
from trading_system.contracts.canonical import canonical_hash


def frame():
    return SimpleNamespace(context_hash='ctx', capabilities='caps', rules='rules',
                           expected_build_digest='build')


@pytest.fixture(autouse=True)
def fixed_binding(monkeypatch):
    monkeypatch.setattr(decision_module, '_binding',
                        lambda f: (f.context_hash, 'cap', 'rule', 'build'))


def test_host_requires_explicit_composition():
    with pytest.raises(ValueError, match='COMPOSITION_REQUIRED'):
        ProgramAutomationHost(frame_builder=None, admission=None)
    host = ProgramAutomationHost(frame_builder=lambda s, c: frame(),
                                 admission=lambda p, f: 'receipt')
    assert host.status().configured and host.status().admission
    assert not host.status().rule_proposal and not host.status().hybrid_gate
    with pytest.raises(ValueError, match='RULE_PROGRAM_PROPOSAL_BUILDER_REQUIRED'):
        host.rule_decision({'id': 'S1', 'revision': 1, 'mode': 'RULE'}, frame())


def test_ai_and_hybrid_runs_converge_on_the_same_decision():
    calls = []

    def admission(proposal, frame_value):
        calls.append(proposal)
        return SimpleNamespace(execution_authorized=False, status='COMMITTED')

    host = ProgramAutomationHost(
        frame_builder=lambda s, c: frame(), admission=admission,
        rule_proposal=lambda s, f: SimpleNamespace(action='SET_TARGET'),
        hybrid_gate=lambda a, p: True)
    strategy = {'id': 'S1', 'revision': 1, 'mode': 'AI'}

    async def analyze(frame_value):
        return {'decision': {'context_hash': 'ctx',
                             'proposal': {'action': 'SET_TARGET'}}}

    import asyncio
    run = asyncio.run(host.run(strategy, {}, analyze))
    assert run.decision.mode == 'AI' and run.decision.source == 'MODEL'
    assert run.result['decision']['schema_revision'] == 'program_v1'
    assert run.result['decision']['execution_authorized'] is False
    assert host.admit(run.decision, run.frame).status == 'COMMITTED'

    strategy = {'id': 'S1', 'revision': 1, 'mode': 'HYBRID'}
    hybrid = asyncio.run(host.run(strategy, {}, analyze))
    assert hybrid.decision.mode == 'HYBRID' and hybrid.decision.source == 'HYBRID'


def test_rule_run_does_not_call_analyze():
    host = ProgramAutomationHost(
        frame_builder=lambda s, c: frame(),
        admission=lambda p, f: SimpleNamespace(execution_authorized=False),
        rule_proposal=lambda s, f: SimpleNamespace(action='SET_TARGET'))
    async def analyze(frame_value):
        raise AssertionError('RULE must not call a model')
    import asyncio
    run = asyncio.run(host.run({'id': 'S1', 'revision': 1, 'mode': 'RULE'}, {}, analyze))
    assert run.decision.source == 'RULE' and run.result['decision']['source'] == 'RULE'


def test_automation_program_branch_refuses_unregistered_host_and_admits_once():
    service = object.__new__(AutomationService)
    service.program_automation = None
    with pytest.raises(Exception) as error:
        service._execute_program_decision({}, {}, {'program_frame': frame()})
    assert 'HOST_ADMISSION_NOT_REGISTERED' in str(error.value)

    admitted = []
    @dataclass(frozen=True)
    class Receipt:
        status: str = 'COMMITTED'
        execution_authorized: bool = False
    receipt = Receipt()

    class Host:
        def admit(self, decision, frame_value):
            admitted.append((decision, frame_value))
            return receipt

    service.program_automation = Host()
    decision = {'program_decision': SimpleNamespace(), 'program_frame': frame()}
    status, reason = service._execute_program_decision({}, {}, decision)
    assert status == 'PENDING' and admitted == [(decision['program_decision'], decision['program_frame'])]
    assert decision['program_receipt_status'] == 'COMMITTED'
    assert decision['execution_status'] == 'PENDING_PROGRAM_ADMISSION'
    assert len(decision['program_receipt_hash']) == 64
