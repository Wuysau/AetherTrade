"""Offline tests for the real local-child current-facts composition.

No PostgreSQL. The reader is driven through its explicit trusted ports; the
tests prove stable durable hashes, fail-closed qualification/risk/funding and
that no clock or transaction digest leaks into the durable identity.
"""
from dataclasses import dataclass
from types import SimpleNamespace
from decimal import Decimal as D
import pytest

from test_local_child_request import setup
from trading_system.contracts import types as t
from trading_system.execution import local_child_current as module
from trading_system.execution.creation_requests import digest
from trading_system.execution.local_child_current import (
    LocalChildCurrentReader, LocalChildFundingSnapshot, LocalChildTargetSnapshot,
    derive_local_child_funding_candidate,
)
from trading_system.risk.program_exposure import AllocationEnvelope, ExistingConsumer
from trading_system.risk.program_financial_admission import ProgramFinancialBundle


NOW = '2026-10-28T10:00:00Z'
LATER = '2026-10-28T10:00:01Z'


def clock(now=NOW, monotonic=100):
    return t.ClockReading(utc_now=now, monotonic_ns=monotonic, clock_health='HEALTHY')


def lease(child, *, expires='2026-10-28T12:00:00Z'):
    scope = child.parent.scope
    return t.OwnerLease(
        shard=t.ExecutionShard(environment=scope.environment,
                               venue_account_id=scope.venue_account_id,
                               product=scope.product, node_id='node-1', execution_epoch=3),
        instance_id='owner-1', expires_at=expires, deadline_monotonic_ns=10 ** 12)


@dataclass(frozen=True, slots=True)
class Terms:
    value: str


@dataclass(frozen=True, slots=True)
class Delta:
    value: str


@dataclass(frozen=True, slots=True)
class Auth:
    scope: t.ScopeKey
    authorization_id: str
    submission_valid_until: str


@dataclass(frozen=True, slots=True)
class SourceFact:
    intake_record_id: str


@dataclass(frozen=True, slots=True)
class Activation:
    activation_id: str
    authority: Auth


class DummyArchive:
    pass


class DummyQualification:
    pass


def fixture(monkeypatch, *, clock_value=None, qualification_ok=True, risk_clear=True,
            blockers=()):
    child, rules, capabilities, context = setup()
    scope = child.parent.scope
    c = clock_value or clock()
    l = lease(child)
    evidence = (t.EvidenceRef(id='qualification', hash='q' * 64,
                              environment=scope.environment, source='TEST', visible_at=NOW),)
    archive = DummyArchive()
    archive.compilation = child.parent
    archive.target_facts = SimpleNamespace(valid_until='2026-10-28T13:00:00Z')
    archive.capabilities = SimpleNamespace(evidence=evidence)
    archive.budget_facts = SimpleNamespace(
        consumers=(ExistingConsumer(consumer_id='parent', scope=scope,
                                    collateral_group_id='group', kind='LOCAL_PARENT',
                                    requirements=None, gross_exposure=None,
                                    evidence_id='parent'),),
        allocations=(AllocationEnvelope(allocation_id='pool', consumer_ids=('parent',),
                                        reservation_ids=(), requirements=(),
                                        gross_exposure=t.Money(amount=D('90000'), currency='USDT'),
                                        evidence_id='pool',
                                        program_hash=child.parent.program_hash),),
        valid_until='2026-10-28T13:00:00Z')
    archive.risk_facts = SimpleNamespace(node_bounds=())
    archive.risk_terms = SimpleNamespace(submission_deadline='2026-10-28T13:00:00Z')
    archive.assessment = SimpleNamespace(risk_decision_id='risk-1', terms_hash='t' * 64,
                                         compilation_hash='c' * 64, allocation_hash='a' * 64,
                                         policy_hash='p' * 64, verdict='ALLOW')
    qualification = DummyQualification()
    qualification.scope = scope
    qualification.compilation_hash = 'comp' * 16
    qualification.capabilities_hash = 'cap' * 10 + 'aaaa'
    qualification.rules_hash = 'rule' * 10
    qualification.profile_hash = 'prof' * 10
    qualification.adapter_build_digest = child.parent.adapter_build_digest
    qualification.execution_mode = child.parent.original_program.execution_mode
    qualification.evidence = evidence if qualification_ok else ()
    qualification.valid_until = '2026-10-28T13:00:00Z'
    intent = SimpleNamespace(
        hashes=SimpleNamespace(compilation=qualification.compilation_hash,
                               capabilities=qualification.capabilities_hash,
                               rules=qualification.rules_hash, profile=qualification.profile_hash),
        valid_until='2026-10-28T13:00:00Z')
    inputs = SimpleNamespace(archive=archive)
    source = SourceFact(intake_record_id='intake')
    bundle = ProgramFinancialBundle(intent=SimpleNamespace(
        source=source, collateral_group_id='group'), basis=SimpleNamespace(), archive=archive,
        decision=SimpleNamespace(), reservations=())
    activation = Activation(activation_id='activation',
                            authority=Auth(scope=scope, authorization_id='auth',
                                           submission_valid_until='2026-10-28T13:00:00Z'))
    monkeypatch.setattr(module, 'ProgramFinancialAssessmentArchive', DummyArchive)
    monkeypatch.setattr(module, 'ProgramFinancialQualification', DummyQualification)
    monkeypatch.setattr(module, 'read_local_activation_source',
                        lambda _tx, key: (activation, bundle))
    import trading_system.intents.generic_program_intake as intake_module
    monkeypatch.setattr(intake_module, 'read_program_source', lambda _tx, key: (source, SimpleNamespace()))
    monkeypatch.setattr(module, 'require_funded_program_account_book', lambda _tx, _book: None)
    monkeypatch.setattr(module, 'validate_current_inputs',
                        lambda *a, **k: (intent, SimpleNamespace()))
    target = SimpleNamespace(compilation_hash=digest(child), book_hash='book',
                             terms=Terms('terms'), delta=Delta('delta'),
                             child_quantity=t.Quantity(value=D('0.1'), unit='BTC'),
                             unallocated_target_quantity=t.Quantity(value=D('0.9'), unit='BTC'))
    monkeypatch.setattr(module, 'compile_local_child_target', lambda *a: target)
    exposure = SimpleNamespace(blockers=blockers, additional_requirements=(),
                               execution_authorized=False)
    monkeypatch.setattr(module, 'preview_local_child_funding', lambda *a, **k: exposure)
    class Verifier:
        def verify_program(self, *a, **k):
            return 'duty' if risk_clear else None
    monkeypatch.setattr(module, 'LockedActionVerifier', lambda: Verifier())

    class Tx:
        active = True
        transaction_id = 'tx-1'
        projections = SimpleNamespace(book=lambda _scope: 'book')
    Tx.scope = scope
    reader = LocalChildCurrentReader(
        load_clock=lambda _tx: c, load_lease=lambda _tx: l,
        load_current_facts=lambda *a: inputs,
        load_qualification=lambda *a: qualification)
    return child, reader, Tx(), activation, archive


def test_real_reader_returns_stable_hashes_across_polls(monkeypatch):
    child, reader, tx, activation, archive = fixture(monkeypatch)
    first = reader.load_current(tx, activation, child, (), 'child-1')
    assert first.clock.utc_now == NOW and first.target_hash and first.funding_hash
    later_clock = clock(LATER, 200)
    reader._load_clock = lambda _tx: later_clock

    class Tx2:
        active = True
        transaction_id = 'tx-2'
        projections = tx.projections
    Tx2.scope = tx.scope
    second = reader.load_current(Tx2(), activation, child, (), 'child-1')
    assert second.clock.utc_now == LATER
    assert (second.target_hash, second.funding_hash, second.risk_hash) == (
        first.target_hash, first.funding_hash, first.risk_hash)
    assert type(first.target) is LocalChildTargetSnapshot
    assert type(first.funding) is LocalChildFundingSnapshot


@pytest.mark.parametrize('fault', ['qualification', 'risk', 'blockers'])
def test_reader_fails_closed_on_changed_current_facts(monkeypatch, fault):
    child, reader, tx, activation, archive = fixture(
        monkeypatch,
        qualification_ok=fault != 'qualification',
        risk_clear=fault != 'risk',
        blockers=('ALLOCATION_FUNDING_UNDERBOUND',) if fault == 'blockers' else ())
    if fault == 'qualification':
        with pytest.raises(ValueError, match='QUALIFICATION_REQUIRED'):
            reader.load_current(tx, activation, child, (), 'child-1')
    else:
        facts = reader.load_current(tx, activation, child, (), 'child-1')
        if fault == 'risk':
            assert facts.risk_clear is False
        else:
            assert facts.funding.blockers == ('ALLOCATION_FUNDING_UNDERBOUND',)


def test_candidate_uses_worst_case_node_notional_and_execution_fees():
    child, _, _, _ = setup()
    fee = SimpleNamespace(amount=t.Money(amount=D('1.5'), currency='USDT'))
    bundle = SimpleNamespace(
        intent=SimpleNamespace(collateral_group_id='group'),
        archive=SimpleNamespace(risk_facts=SimpleNamespace(
            node_bounds=(SimpleNamespace(node_id=child.node.original.node_id,
                                         execution_fees=(fee,)),))))
    candidate = derive_local_child_funding_candidate(child, bundle)
    assert candidate.kind == 'LOCAL_CHILD' and candidate.scope == child.parent.scope
    assert candidate.gross_exposure.currency == child.node.notional_asset
    assert dict((m.currency, m.amount) for m in candidate.requirements)['USDT'] > 0
