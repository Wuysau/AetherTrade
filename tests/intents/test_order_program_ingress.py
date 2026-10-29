from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from intents.test_order_program_compiler import fixture
from trading_system.contracts.wire import ContractCodec
from trading_system.intents.order_program_ingress import (
    ExecutionPolicy, project_proposal, AdmissionInputs, PaperQualification,
    validate_compilation, OrderProgramIngress,
    OrderProgramCommandBinding, validate_program_command,
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


@pytest.mark.parametrize('role', ['PENDING', 'LEG', 'ALGORITHM_PARENT'])
def test_atomic_nonentry_node_is_not_reinterpreted_as_immediate_entry(role):
    proposal, _, _, _ = fixture()
    node = replace(proposal.program.nodes[0], role=role)
    proposal = replace(proposal, program=replace(proposal.program, nodes=(node,)))
    with pytest.raises(ProposalRejected, match='NODE_COMPILER_NOT_IMPLEMENTED'):
        project_proposal(proposal, ExecutionPolicy(policy_id='policy', revision=1, maximum_entry_slippage_bps='0'))


@pytest.mark.parametrize('slippage', ['NaN', '-1', 'Infinity', '10001', '', '1e1', '+1'])
def test_execution_policy_requires_explicit_finite_cap(slippage):
    with pytest.raises(ValueError):
        ExecutionPolicy(policy_id='policy', revision=1, maximum_entry_slippage_bps=slippage)


def test_subclass_version_cannot_enter_a_v2_only_sidecar():
    from dataclasses import fields
    from trading_system.contracts.order_program import OrderProposal
    class NewVersion(OrderProposal):
        pass
    p, _, _, _ = fixture()
    values = {f.name: getattr(p, f.name) for f in fields(p)}
    values['contract_version'] = '2026-10-29'
    p = NewVersion(**values)
    with pytest.raises(ProposalRejected, match='ORDER_PROPOSAL_VERSION_UNSUPPORTED'):
        project_proposal(p, ExecutionPolicy(policy_id='policy', revision=1, maximum_entry_slippage_bps='0'))


def test_public_reservation_guard_cannot_be_disabled_by_request():
    from trading_system.risk.reservations import ReservationService
    from trading_system.contracts import types as t
    tx = SimpleNamespace(_latest=lambda *args: object(),
        projections=SimpleNamespace(book=lambda scope: SimpleNamespace(sequence=7)))
    service = ReservationService(None, t.ScopeKey(environment=t.Environment.PAPER,
        venue_account_id='test', product=t.Product.SPOT, instrument_uid='test', venue_leg='BOTH'),
        None, clock=lambda: NOW)
    intent = SimpleNamespace(intent_id='program-intent')
    result = service._reserve_legacy(tx, intent, 1)
    assert not result.accepted and result.rejection_codes == ('PROGRAM_ADMISSION_REQUIRED',)
    assert result.book_sequence == 7


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


def command_case():
    import json
    from pathlib import Path
    from trading_system.intents.ingress import IntentCompiler
    from trading_system.execution.commands import command_hash
    case, p, compiled, facts, proof = admission_case()
    intent = IntentCompiler(case.binding).bind(project_proposal(p, facts.execution_policy), case.context, case.book)
    codec = ContractCodec()
    command = codec.decode('execution-command', json.loads(Path('tests/fixtures/contracts/examples/execution-command.paper.json').read_text()))
    payload = replace(command.canonical_payload, order_type='LIMIT', price='81490.00', side='BUY',
        quantity=compiled.normalized_target_quantity, time_in_force='GTC')
    command = replace(command, intent_id=intent.intent_id, canonical_payload=payload,
        payload_hash=canonical_hash(domain_payload(payload)))
    sidecar = OrderProgramCommandBinding(command_id=command.command_id, command_hash=command_hash(command),
        intent_hash=codec.hash(intent), compilation=compiled, rules=facts.rules, capabilities=facts.capabilities,
        execution_policy=facts.execution_policy)
    tx = SimpleNamespace(scope=case.book.scope, _latest=lambda *args: sidecar)
    return tx, command, intent, facts, proof, sidecar


def test_binding_roundtrip_retains_entire_original_program():
    from trading_system.storage.models import encode, decode
    _, _, _, _, _, sidecar = command_case()
    assert decode(OrderProgramCommandBinding, encode(sidecar)) == sidecar


def test_dispatch_revalidates_actual_qualification_and_exact_tif():
    tx, command, intent, facts, proof, sidecar = command_case()
    assert validate_program_command(tx, command, intent, lambda *args: facts, lambda *args: proof) == sidecar
    with pytest.raises(ProposalRejected, match='PAPER_QUALIFICATION_REQUIRED'):
        validate_program_command(tx, command, intent, lambda *args: facts, None)
    changed = replace(command, canonical_payload=replace(command.canonical_payload, time_in_force='FOK'))
    with pytest.raises(ProposalRejected, match='PROGRAM_COMMAND_BINDING_MISMATCH'):
        validate_program_command(tx, changed, intent, lambda *args: facts, lambda *args: proof)


def test_dispatch_without_program_readers_fails_before_mark(monkeypatch):
    from trading_system.execution.commands import CommandRepository
    from trading_system.execution.dispatcher import CommandDispatcher, DispatchDenied
    tx, command, intent, facts, proof, sidecar = command_case()
    monkeypatch.setattr(CommandRepository, 'validate_binding', lambda *args: intent)
    dispatcher = CommandDispatcher(None, tx.scope)
    with pytest.raises(DispatchDenied, match='PROGRAM_DISPATCH_NOT_CONFIGURED'):
        dispatcher._check(tx, command, None)


def test_followon_command_cannot_drop_original_program_binding(monkeypatch):
    from trading_system.execution.commands import CommandRepository
    from trading_system.execution.dispatcher import CommandDispatcher, DispatchDenied
    tx, command, intent, facts, proof, sidecar = command_case()
    tx._latest = lambda table, key, cls: None if key.startswith('order-program-command:') else object()
    monkeypatch.setattr(CommandRepository, 'validate_binding', lambda *args: intent)
    dispatcher = CommandDispatcher(None, tx.scope)
    with pytest.raises(DispatchDenied, match='PROGRAM_COMMAND_BINDING_REQUIRED'):
        dispatcher._check(tx, command, None)


def intake_with_denial(monkeypatch):
    from trading_system.contracts import types as t
    from trading_system.storage.order_programs import OrderProgramRepository
    case, p, compiled, facts, proof = admission_case()
    ledger = MemoryLedger(case)
    calls = []
    def deny(tx, intent, sequence, *, accepted):
        from trading_system.intents.order_program_ingress import program_intake
        assert tx is ledger and ledger.active
        original = program_intake(tx, intent)
        assert original.compilation.original_proposal == p
        assert original.compilation.time_in_force == 'GTC'
        calls.append(intent)
        return t.ReservationOutcome(accepted=False, reservations=(), book_sequence=sequence, rejection_codes=('BUDGET',))
    def save(repo, program):
        repo.tx._append('order_programs', program.program_id, program, 0)
    monkeypatch.setattr(OrderProgramRepository, 'save_program', save)
    reservations = SimpleNamespace(uow=ledger, scope=case.book.scope, _reserve=deny)
    service = OrderProgramIngress(ledger, case.book.scope, lambda *args: facts, reservations,
        execution_policy=facts.execution_policy, load_qualification=lambda *args: proof)
    return ledger, service, case, p, compiled, calls


def test_replay_returns_original_denial_without_new_admission(monkeypatch):
    ledger, service, case, p, compiled, calls = intake_with_denial(monkeypatch)
    first = service.admit(p, case.context, compiled)
    assert not first.reservation.accepted
    assert first.reservation.rejection_codes == ('BUDGET',)
    before = ledger.records.copy()
    replay = service.admit(p, case.context, compiled)
    assert replay.reservation == first.reservation
    assert replay.receipt.existing_result_ref == first.receipt.receipt_id
    assert ledger.records == before and len(calls) == 1


def test_same_legacy_projection_cannot_hide_changed_native_tif(monkeypatch):
    ledger, service, case, p, compiled, calls = intake_with_denial(monkeypatch)
    service.admit(p, case.context, compiled)
    node = p.program.nodes[0]
    changed = replace(p, program=replace(p.program, nodes=(replace(node,
        order=replace(node.order, tif=replace(node.order.tif, kind='IOC'))),)))
    changed_compilation = replace(compiled, original_proposal=changed, original_program=changed.program,
        time_in_force='IOC')
    result = service.admit(changed, case.context, changed_compilation)
    assert result.receipt.reason == 'PROGRAM_REPLAY_CONFLICT'
    assert result.reservation is None and len(calls) == 1


def test_failed_outer_commit_cannot_return_any_admission_receipt(monkeypatch):
    ledger, service, case, p, compiled, calls = intake_with_denial(monkeypatch)
    before = ledger.records.copy()
    ledger.fail_commit = True
    with pytest.raises(OSError, match='injected commit failure'):
        service.admit(p, case.context, compiled)
    assert ledger.records == before
