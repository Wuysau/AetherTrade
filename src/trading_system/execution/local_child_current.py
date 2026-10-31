"""Trusted current-fact composition for protected local-child admission.

This is the real adapter between an activated local TWAP/VP programme and the
existing V3 financial/risk/target/funding ports. It adds no ledger, cash
derivation, risk override or execution authority. Every input is re-read inside
the caller's owner transaction, which already holds the collateral mutex.

Stable hashes are decision identities, not per-poll timestamps: the current
target/funding/risk are re-derived on every permit check, but the durable
command binds only the exact allocation terms, pool, candidate and risk
decision. A changed book, clock window, risk decision or funding result fails
closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.contracts.order_program import amount
from trading_system.intents.local_child_compilation import LocalChildCompilation
from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
from trading_system.intents.program_target_allocation import (
    ProgramScopedTargetFactsV2, compile_local_child_target,
)
from trading_system.risk.locked_actions import LockedActionVerifier
from trading_system.risk.program_cash_guard import require_funded_program_account_book
from trading_system.risk.program_exposure import ExistingConsumer, preview_local_child_funding
from trading_system.risk.program_financial_admission import (
    ProgramFinancialBundle, ProgramFinancialQualification, validate_current_inputs,
)
from trading_system.risk.program_quantity_scope import current_scoped_program_financial_book
from trading_system.risk.policy_checks import exact_risk_arithmetic

from .creation_requests import digest, require
from .local_child_admission import LocalChildCurrentFacts
from .local_program_activation import read_local_activation_source


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildTargetSnapshot:
    compilation_hash: str
    facts_hash: str
    book_hash: str
    unallocated_target_quantity: t.Quantity

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildFundingSnapshot:
    blockers: tuple[str, ...]
    facts_hash: str
    allocation_id: str
    requirements: tuple[t.Money, ...]

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


def _money(value, currency):
    return t.Money(amount=Decimal(value), currency=currency)


def _evidence_hash(evidence):
    return canonical_hash({'evidence': [domain_payload(item) for item in evidence]})


def derive_local_child_funding_candidate(child: LocalChildCompilation,
                                         bundle: ProgramFinancialBundle) -> ExistingConsumer:
    """Worst-case child share of the already funded parent pool.

    Uses the compiled node's declared worst-case notional and the archived node
    execution fees. Never uses a current mark, midpoint or target estimate.
    """
    node = child.node
    notional = node.worst_case_quote_notional
    if notional is None:
        require(node.sizing_price_bound is not None,
                'LOCAL_CHILD_FUNDING_NOTIONAL_REQUIRED')
        with exact_risk_arithmetic():
            notional = format(Decimal(amount(node.quantity.value))
                              * Decimal(amount(node.sizing_price_bound))
                              * Decimal(amount(node.quantity_multiplier)), 'f')
    require(notional is not None and Decimal(notional) > 0,
            'LOCAL_CHILD_FUNDING_NOTIONAL_REQUIRED')
    requirements = {node.notional_asset: Decimal(notional)}
    for bound in bundle.archive.risk_facts.node_bounds or ():
        if bound.node_id != node.original.node_id:
            continue
        for fee in bound.execution_fees or ():
            requirements[fee.amount.currency] = (requirements.get(fee.amount.currency, Decimal(0))
                                                 + fee.amount.amount)
    return ExistingConsumer(
        consumer_id='local-child-' + digest(child), scope=child.parent.scope,
        collateral_group_id=bundle.intent.collateral_group_id, kind='LOCAL_CHILD',
        requirements=tuple(_money(value, currency)
                           for currency, value in sorted(requirements.items())),
        gross_exposure=_money(notional, node.notional_asset),
        evidence_id='local-child:' + digest(child))


def _parent_allocation(plan, budget_facts):
    allocations = budget_facts.allocations
    consumers = budget_facts.consumers
    require(type(allocations) is tuple and type(consumers) is tuple,
            'LOCAL_CHILD_COMPLETE_ALLOCATION_FACTS_REQUIRED')
    parents = {c.consumer_id for c in consumers
               if c.kind == 'LOCAL_PARENT' and c.scope == plan.scope}
    matches = [a for a in allocations
               if a.program_hash == plan.program_hash
               and parents.intersection(a.consumer_ids)]
    require(len(matches) == 1, 'LOCAL_CHILD_PARENT_ALLOCATION_REQUIRED')
    return matches[0]


class LocalChildCurrentReader:
    """Owner-transaction reader with explicit trusted ports.

    `load_clock`/`load_lease` are memoized for the active transaction so the
    admission guard sees the exact same objects it validates. A later poll in
    the same transaction reuses them; a new transaction reads fresh facts.
    """

    def __init__(self, *, load_clock, load_lease, load_current_facts, load_qualification,
                 load_action_proof=None, load_released_balances=None,
                 load_execution_bounds=None, load_funding_candidate=None,
                 load_schedule_policy=None, load_volume=None):
        self._load_clock, self._load_lease = load_clock, load_lease
        self._load_current_facts = load_current_facts
        self._load_qualification = load_qualification
        self._load_action_proof = load_action_proof
        self._load_released_balances = load_released_balances
        self._load_execution_bounds = load_execution_bounds
        self._load_funding_candidate = load_funding_candidate
        self._load_schedule_policy = load_schedule_policy
        self._load_volume = load_volume
        self._tx_key = None
        self._clock = None
        self._lease = None

    def _remember(self, tx):
        key = (id(tx), tx.transaction_id)
        if self._tx_key != key:
            self._tx_key = key
            self._clock = self._load_clock(tx)
            self._lease = self._load_lease(tx)
        return self._clock, self._lease

    def load_clock(self, tx):
        return self._remember(tx)[0]

    def load_lease(self, tx):
        return self._remember(tx)[1]

    def load_current(self, tx, activation, child, committed, command_id):
        require(tx.active is True, 'LOCAL_CHILD_ACTIVE_TRANSACTION_REQUIRED')
        record, bundle = read_local_activation_source(tx, activation.activation_id)
        require(digest(record) == digest(activation)
                and type(bundle) is ProgramFinancialBundle,
                'LOCAL_CHILD_PARENT_BUNDLE_REQUIRED')
        clock, lease = self._remember(tx)
        from trading_system.intents.generic_program_intake import read_program_source
        source, intake = read_program_source(tx, bundle.intent.source.intake_record_id)
        book = tx.projections.book(tx.scope)
        require_funded_program_account_book(tx, book)
        archive = bundle.archive
        require(type(archive) is ProgramFinancialAssessmentArchive,
                'LOCAL_CHILD_ASSESSMENT_ARCHIVE_REQUIRED')
        scoped = type(archive.target_facts) is ProgramScopedTargetFactsV2
        financial_book = (current_scoped_program_financial_book(
            tx, bundle, book, load_released_balances=self._load_released_balances)
            if scoped else book)
        inputs = self._load_current_facts(tx, source, intake, financial_book, clock)
        intent, basis = validate_current_inputs(tx, source, intake, financial_book, inputs,
                                                clock, self._load_execution_bounds)
        require(digest(inputs.archive.compilation) == digest(child.parent)
                and digest(source) == digest(bundle.intent.source),
                'LOCAL_CHILD_PARENT_SOURCE_CHANGED')
        target = compile_local_child_target(child, book, inputs.archive.target_facts)
        allocation = _parent_allocation(child.parent, inputs.archive.budget_facts)
        candidate = (self._load_funding_candidate(tx, activation, bundle, child, allocation)
                     if self._load_funding_candidate is not None
                     else derive_local_child_funding_candidate(child, bundle))
        exposure = preview_local_child_funding(
            child.parent, inputs.archive.budget_facts, allocation.allocation_id,
            candidate, now=clock.utc_now)
        qualification = self._load_qualification(tx, inputs.archive, clock)
        require(type(qualification) is ProgramFinancialQualification
                and qualification.scope == tx.scope
                and qualification.compilation_hash == intent.hashes.compilation
                and qualification.capabilities_hash == intent.hashes.capabilities
                and qualification.rules_hash == intent.hashes.rules
                and qualification.profile_hash == intent.hashes.profile
                and qualification.adapter_build_digest == inputs.archive.compilation.adapter_build_digest
                and qualification.execution_mode == inputs.archive.compilation.original_program.execution_mode
                and qualification.evidence
                and _evidence_hash(qualification.evidence)
                    == _evidence_hash(inputs.archive.capabilities.evidence)
                and utc_timestamp(clock.utc_now) < utc_timestamp(qualification.valid_until),
                'LOCAL_CHILD_QUALIFICATION_REQUIRED')
        duty = LockedActionVerifier().verify_program(
            tx, intent, inputs.archive, clock.utc_now, book=financial_book,
            read_proof=self._load_action_proof)
        assessment = inputs.archive.assessment
        risk_hash = canonical_hash({'decision_id': assessment.risk_decision_id,
                                    'terms': assessment.terms_hash,
                                    'compilation': assessment.compilation_hash,
                                    'allocation': assessment.allocation_hash,
                                    'policy': assessment.policy_hash,
                                    'verdict': assessment.verdict})
        target_hash = canonical_hash({
            'compilation': digest(child), 'book': target.book_hash,
            'terms': digest(target.terms), 'delta': digest(target.delta),
            'child_quantity': domain_payload(target.child_quantity),
            'unallocated': domain_payload(target.unallocated_target_quantity),
            'target_valid_until': inputs.archive.target_facts.valid_until})
        funding_hash = canonical_hash({
            'allocation_id': allocation.allocation_id,
            'program_hash': child.parent.program_hash,
            'candidate': domain_payload(candidate),
            'blockers': list(exposure.blockers)})
        deadlines = [intent.valid_until, inputs.archive.risk_terms.submission_deadline,
                     inputs.archive.target_facts.valid_until, inputs.archive.budget_facts.valid_until,
                     qualification.valid_until, lease.expires_at,
                     activation.authority.submission_valid_until]
        valid_until = min(deadlines, key=utc_timestamp)
        policy = (self._load_schedule_policy(tx, activation, child, inputs)
                  if self._load_schedule_policy is not None else None)
        volume = (self._load_volume(tx, activation, clock.utc_now)
                  if self._load_volume is not None else None)
        return LocalChildCurrentFacts(
            clock=clock, lease=lease,
            target=LocalChildTargetSnapshot(
                compilation_hash=target.compilation_hash, facts_hash=target_hash,
                book_hash=target.book_hash,
                unallocated_target_quantity=target.unallocated_target_quantity),
            funding=LocalChildFundingSnapshot(
                blockers=exposure.blockers, facts_hash=funding_hash,
                allocation_id=allocation.allocation_id,
                requirements=tuple(exposure.additional_requirements or ())),
            target_hash=target_hash, funding_hash=funding_hash,
            authorization_id=activation.authority.authorization_id,
            risk_clear=duty is not None, risk_hash=risk_hash, valid_until=valid_until,
            volume=volume,
            interval_seconds=getattr(policy, 'interval_seconds', None),
            quantity_step=getattr(policy, 'quantity_step', None),
            participation_bps=getattr(policy, 'participation_bps', None))
