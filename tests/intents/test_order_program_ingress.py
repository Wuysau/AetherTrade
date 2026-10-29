from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from intents.test_order_program_compiler import fixture
from trading_system.contracts.wire import ContractCodec
from trading_system.intents.order_program_ingress import (
    ExecutionPolicy, project_proposal, AdmissionInputs, PaperQualification,
    validate_compilation, OrderProgramIngress,
)
from trading_system.intents.order_program_compiler import compile_atomic_target
from trading_system.intents.ingress import IngressInputs
from trading_system.contracts.canonical import canonical_hash, domain_payload
from unit.support_ip_09 import make_case, MemoryLedger, NOW, BEFORE, END
from trading_system.intents.semantic_validation import ProposalRejected


def test_projection_preserves_original_price_stop_target_and_deadlines():
    proposal, _, _, _ = fixture()
    policy = ExecutionPolicy(policy_id='trusted-policy', revision=3, maximum_entry_slippage_bps='12')
    projected = project_proposal(proposal, policy)
    ContractCodec().encode(projected)
    assert projected.target.value == '4000'
    assert projected.target.sizing == 'QUOTE_NOTIONAL'
    assert projected.entry.limit_price == '81490.00'
    assert projected.exit_plan == proposal.exit_plan
    assert projected.exit_plan.stop_price == '81330.00'
    assert projected.entry.entry_deadline == proposal.program.submit_valid_until
    assert projected.entry.maximum_entry_slippage_bps == '12'
    assert projected.evidence_refs == proposal.evidence_refs
    assert projected.rationale.summary == proposal.summary


@pytest.mark.parametrize('tif,partial', [('GTC', True), ('IOC', True), ('FOK', False)])
def test_partial_projection_does_not_replace_original_tif(tif, partial):
    proposal, _, _, _ = fixture()
    node = proposal.program.nodes[0]
    node = replace(node, order=replace(node.order, tif=replace(node.order.tif, kind=tif)))
    proposal = replace(proposal, program=replace(proposal.program, nodes=(node,)))
    projected = project_proposal(proposal, ExecutionPolicy(policy_id='policy', revision=1, maximum_entry_slippage_bps='0'))
    assert projected.entry.allow_partial is partial
    assert proposal.program.nodes[0].order.tif.kind == tif


@pytest.mark.parametrize('change', [dict(post_only=True), dict(routing='SOR'), dict(iceberg_quantity='0.001'), dict(stp='EXPIRE_TAKER')])
def test_unsupported_modifier_never_silently_disappears(change):
    proposal, _, _, _ = fixture()
    node = proposal.program.nodes[0]
    proposal = replace(proposal, program=replace(proposal.program, nodes=(replace(node, order=replace(node.order, **change)),)))
    with pytest.raises(ProposalRejected):
        project_proposal(proposal, ExecutionPolicy(policy_id='policy', revision=1, maximum_entry_slippage_bps='0'))


@pytest.mark.parametrize('slippage', ['NaN', '-1', 'Infinity', '10001'])
def test_execution_policy_requires_explicit_finite_cap(slippage):
    with pytest.raises(ValueError):
        ExecutionPolicy(policy_id='policy', revision=1, maximum_entry_slippage_bps=slippage)


def admission_case():
    case = make_case()
    p, r, c, _ = fixture()
    scope = case.book.scope
    spec = case.binding.instrument
    r = replace(r, scope=scope, as_of=BEFORE, valid_until=END,
        quantity_step=str(spec.quantity_step.value), minimum_quantity=str(spec.minimum_quantity.value),
        minimum_notional=str(spec.minimum_notional.amount), evidence=case.book.evidence)
    c = replace(c, scope=scope, as_of=BEFORE, valid_until=END, qualification='PAPER_VERIFIED', evidence=case.book.evidence)
    p = replace(p, instrument_ref=scope.instrument_uid, evidence_refs=(case.binding.snapshot.ref.id,),
        valid_until=END, exit_plan=replace(p.exit_plan, review_due_at=END),
        program=replace(p.program, instrument_ref=scope.instrument_uid, submit_valid_until=END))
    case.binding = replace(case.binding, allow_round_down=True)
    case.target = replace(case.target, allow_round_down=True)
    policy = ExecutionPolicy(policy_id='test-only', revision=1, maximum_entry_slippage_bps='20')
    facts = AdmissionInputs(ingress=IngressInputs(binding=case.binding, target=case.target), rules=r, capabilities=c,
        expected_build_digest=c.adapter_build_digest, expected_rule_revision=1,
        expected_capabilities_hash=canonical_hash(domain_payload(c)), execution_policy=policy)
    compilation = compile_atomic_target(p, r, c, scope=scope, now=NOW, expected_build_digest=c.adapter_build_digest,
        expected_rule_revision=1, expected_capabilities_hash=facts.expected_capabilities_hash,
        expected_instrument_ref=scope.instrument_uid, allow_round_down=True)
    proof = PaperQualification(scope=scope, capabilities_hash=compilation.capabilities_hash,
        rules_hash=compilation.rules_hash, adapter_build_digest=c.adapter_build_digest,
        evidence=c.evidence, valid_until=END)
    return case, p, compilation, facts, proof


def test_snapshot_label_does_not_prove_actual_qualification():
    case, p, compiled, facts, _ = admission_case()
    with pytest.raises(ProposalRejected, match='PAPER_QUALIFICATION_REQUIRED'):
        validate_compilation(SimpleNamespace(scope=case.book.scope), p, compiled, facts, None)


@pytest.mark.parametrize('fault', ['build', 'rules', 'hash', 'proof', 'normalization', 'protection'])
def test_changed_trusted_bindings_reject(fault):
    case, p, compiled, facts, proof = admission_case()
    if fault == 'build': facts = replace(facts, expected_build_digest='f' * 64)
    if fault == 'rules': facts = replace(facts, expected_rule_revision=2)
    if fault == 'hash': facts = replace(facts, expected_capabilities_hash='f' * 64)
    if fault == 'proof': proof = replace(proof, adapter_build_digest='f' * 64)
    if fault == 'normalization': compiled = replace(compiled, normalized_target_quantity='0.080')
    if fault == 'protection': facts = replace(facts, ingress=replace(facts.ingress,
        binding=replace(facts.ingress.binding, protection_policy='HOSTED_ALLOWED_CASH_ONLY')))
    with pytest.raises(ProposalRejected):
        validate_compilation(SimpleNamespace(scope=case.book.scope), p, compiled, facts, lambda *args: proof)


def test_qualified_fixture_reuses_legacy_intent_compiler_exactly():
    case, p, compiled, facts, proof = admission_case()
    validate_compilation(SimpleNamespace(scope=case.book.scope), p, compiled, facts, lambda *args: proof)
    from trading_system.intents.ingress import IntentCompiler
    intent = IntentCompiler(case.binding).bind(project_proposal(p, facts.execution_policy), case.context, case.book)
    assert intent.target_quantity == compiled.normalized_target_quantity == '0.049'
    assert intent.execution.limit_price == '81490.00'
    assert intent.exit_plan.stop_price == '81330.00'


def test_qualification_failure_rolls_back_intake_and_fence():
    case, p, compiled, facts, proof = admission_case()
    ledger = MemoryLedger(case)
    reservations = SimpleNamespace(uow=ledger, scope=case.book.scope)
    service = OrderProgramIngress(ledger, case.book.scope, lambda *args: facts, reservations,
        execution_policy=facts.execution_policy)
    before = ledger.records.copy()
    result = service.admit(p, case.context, compiled)
    assert result.receipt.status == 'REJECTED'
    assert result.receipt.reason == 'PAPER_QUALIFICATION_REQUIRED'
    assert ledger.records == before
