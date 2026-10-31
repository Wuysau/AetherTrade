"""Offline regressions for unified decisions, native VP refusal and source auth."""
from types import SimpleNamespace
import pytest

from trading_system.execution.native_vp_policy import native_child_policy
from trading_system.market.source_auth import (
    AggregateSourceEvidence, TransportProvenance, authenticate_aggregate_receipt,
    provenance_hash,
)
from trading_system.server import program_decision as module
from trading_system.server.program_decision import (
    admit_decision, build_ai_decision, build_hybrid_decision, build_rule_decision,
    decision_hash, require_current_binding,
)


def test_twap_supports_children_but_vp_is_explicitly_parent_only():
    assert native_child_policy('TWAP').children_supported
    vp = native_child_policy('VP')
    assert not vp.children_supported
    assert vp.code == 'PAPER_NATIVE_VP_CHILD_MODEL_UNAVAILABLE'
    assert vp.execution_authorized is False
    with pytest.raises(ValueError):
        native_child_policy('UNKNOWN')


def test_three_modes_converge_on_one_binding(monkeypatch):
    monkeypatch.setattr(module, '_binding', lambda frame: (frame.context_hash, 'cap', 'rule', 'build'))
    frame = SimpleNamespace(context_hash='ctx')
    proposal = SimpleNamespace(action='SET_TARGET')
    assessment = SimpleNamespace(context_hash='ctx', proposal=proposal)
    ai = build_ai_decision(assessment, frame, strategy_id='S1', strategy_revision=1)
    rule = build_rule_decision(proposal, frame, strategy_id='S1', strategy_revision=1)
    hybrid = build_hybrid_decision(assessment, proposal, frame, strategy_id='S1',
                                   strategy_revision=1, gate=lambda a, p: True)
    for decision in (ai, rule, hybrid):
        assert (decision.context_hash, decision.capabilities_hash, decision.rules_hash,
                decision.expected_build_digest) == ('ctx', 'cap', 'rule', 'build')
        assert decision.execution_authorized is False
        assert len(decision_hash(decision)) == 64
    assert (ai.source, rule.source, hybrid.source) == ('MODEL', 'RULE', 'HYBRID')


@pytest.mark.parametrize('fault', ['context', 'no_proposal', 'gate'])
def test_invalid_decision_never_reaches_admission(monkeypatch, fault):
    monkeypatch.setattr(module, '_binding', lambda frame: (frame.context_hash, 'cap', 'rule', 'build'))
    frame = SimpleNamespace(context_hash='ctx')
    proposal = SimpleNamespace(action='SET_TARGET')
    assessment = SimpleNamespace(context_hash='other' if fault == 'context' else 'ctx',
                                 proposal=None if fault == 'no_proposal' else proposal)
    with pytest.raises(ValueError):
        if fault == 'gate':
            build_hybrid_decision(assessment, proposal, frame, strategy_id='S1',
                                  strategy_revision=1, gate=lambda a, p: False)
        else:
            build_ai_decision(assessment, frame, strategy_id='S1', strategy_revision=1)


def test_admission_is_called_once_and_cannot_return_authority(monkeypatch):
    monkeypatch.setattr(module, '_binding', lambda frame: (frame.context_hash, 'cap', 'rule', 'build'))
    frame = SimpleNamespace(context_hash='ctx')
    proposal = SimpleNamespace(action='SET_TARGET')
    decision = build_rule_decision(proposal, frame, strategy_id='S1', strategy_revision=1)
    calls = []
    receipt = admit_decision(decision, frame,
                             admission=lambda p, f: calls.append((p, f)) or
                             SimpleNamespace(execution_authorized=False))
    assert calls == [(proposal, frame)] and receipt.execution_authorized is False
    with pytest.raises(ValueError, match='RECEIPT_AUTHORITY_INVALID'):
        admit_decision(decision, frame,
                       admission=lambda p, f: SimpleNamespace(execution_authorized=True))
    with pytest.raises(ValueError, match='HOST_ADMISSION_NOT_REGISTERED'):
        admit_decision(decision, frame, admission=None)
    with pytest.raises(ValueError, match='REPLAN_REQUIRED'):
        require_current_binding(decision, SimpleNamespace(context_hash='changed'))


def provenance(**changes):
    values = dict(origin='https://data-api.binance.vision',
                  resolved_ip='1.2.3.4', tls_verified=True,
                  tls_peer_certificate_sha256='c' * 64, http_status=200,
                  response_headers_hash='h' * 64)
    values.update(changes)
    return TransportProvenance(**values)


def test_aggregate_source_auth_stays_candidate_without_verifier():
    receipt = SimpleNamespace(request_hash='r', manifest_hash='m', payload_hash='p')
    evidence = authenticate_aggregate_receipt(receipt, provenance())
    assert type(evidence) is AggregateSourceEvidence
    assert evidence.source_verified is False and evidence.execution_authorized is False
    assert len(provenance_hash(provenance())) == 64


def test_aggregate_source_auth_requires_explicit_verifier():
    receipt = SimpleNamespace(request_hash='r', manifest_hash='m', payload_hash='p')
    evidence = authenticate_aggregate_receipt(receipt, provenance(),
                                              verifier=lambda **kw: 'tls-policy-v1')
    assert evidence.source_verified is True and evidence.verifier_id == 'tls-policy-v1'
    with pytest.raises(ValueError, match='VERIFIER_REFUSED'):
        authenticate_aggregate_receipt(receipt, provenance(), verifier=lambda **kw: '')
    for bad in (provenance(tls_verified=False), provenance(http_status=500),
                provenance(response_headers_hash='')):
        with pytest.raises(ValueError, match='PROVENANCE_INVALID'):
            authenticate_aggregate_receipt(receipt, bad)
