"""Effective authority TTL narrows; original proposal/order times stay intact."""
from dataclasses import replace

import pytest

from unit.support_program_financial import financial_case
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.intents.program_compilation import compile_order_program
from trading_system.intents.program_target_allocation import compile_program_target
from trading_system.intents.program_financial_records import ProgramFinancialArchiveCodec,financial_intent_from_archive
from trading_system.risk.program_exposure import derive_program_exposure

SHORT='2026-10-28T10:00:20Z'
STRICTER='2026-10-28T10:00:10Z'


def narrowed_archive(bound):
    c=financial_case();a=c.archive;plan=a.compilation;rules=a.rules;facts=a.target_facts
    digest=lambda value:canonical_hash(domain_payload(value))
    original_hash=plan.proposal_hash
    if bound=='rules':
        # Fresh synthetic qualification with a shorter rule lifetime; all
        # derived plan/facts/allocation hashes are recomputed by real compilers.
        rules=replace(rules,valid_until=SHORT)
        from test_program_compilation import inputs
        *_,kwargs=inputs(plan.scope.product)
        plan=compile_order_program(plan.original_proposal,rules,a.capabilities,**kwargs)
        facts=replace(facts,rules=rules,compilation_hash=digest(plan),
            unit_binding=replace(facts.unit_binding,compilation_hash=digest(plan)))
    elif bound=='bridge':facts=replace(facts,unit_binding=replace(facts.unit_binding,valid_until=SHORT))
    elif bound=='facts':facts=replace(facts,valid_until=SHORT)
    else:raise AssertionError(bound)
    allocation=compile_program_target(plan,a.book,facts)
    budget=replace(a.budget_facts,compilation_hash=digest(plan))
    exposure=derive_program_exposure(plan,budget,now=facts.inputs.clock.utc_now)
    metrics=replace(a.risk_metrics,allocation_hash=digest(allocation),valid_until=SHORT)
    result=replace(a,compilation=plan,rules=rules,target_facts=facts,allocation=allocation,
        budget_facts=budget,exposure=exposure,risk_terms=replace(a.risk_terms,valid_until=SHORT),risk_metrics=metrics)
    assert plan.proposal_hash==original_hash and plan.original_proposal==a.compilation.original_proposal
    assert utc_timestamp(allocation.terms.valid_until)==utc_timestamp(SHORT)
    return result


@pytest.mark.parametrize('bound',['bridge','rules','facts'])
def test_effective_ttl_accepts_real_narrower_qualified_allocation(bound):
    a=narrowed_archive(bound)
    codec=ProgramFinancialArchiveCodec()
    assert codec.decode(codec.encode(a))==a
    assert a.risk_terms.submission_deadline==a.compilation.original_program.submit_valid_until
    with pytest.raises(ValueError,match='PROGRAM_RISK_FACTS_ARCHIVE_REQUIRED'):
        financial_intent_from_archive(a)


def test_effective_ttl_may_be_stricter_than_qualified_allocation():
    a=narrowed_archive('bridge')
    a=replace(a,risk_terms=replace(a.risk_terms,valid_until=STRICTER),
        risk_metrics=replace(a.risk_metrics,valid_until=STRICTER))
    ProgramFinancialArchiveCodec().encode(a)


@pytest.mark.parametrize('extended',['2026-10-28T10:00:21Z','2026-10-28T12:00:00Z'])
def test_effective_ttl_never_extends_qualification_or_original_proposal(extended):
    a=narrowed_archive('bridge')
    a=replace(a,risk_terms=replace(a.risk_terms,valid_until=extended),
        risk_metrics=replace(a.risk_metrics,valid_until=extended))
    with pytest.raises(ValueError):ProgramFinancialArchiveCodec().encode(a)


def test_even_consistent_rewritten_hashes_cannot_extend_allocation_ttl():
    a=narrowed_archive('bridge')
    allocation=replace(a.allocation,terms=replace(a.allocation.terms,valid_until='2026-10-28T10:00:30Z'))
    a=replace(a,allocation=allocation,risk_terms=replace(a.risk_terms,valid_until=allocation.terms.valid_until),
        risk_metrics=replace(a.risk_metrics,allocation_hash=canonical_hash(domain_payload(allocation)),
            valid_until=allocation.terms.valid_until))
    with pytest.raises(ValueError):ProgramFinancialArchiveCodec().encode(a)


def test_effective_ttl_requires_remaining_valid_captured_clock():
    a=narrowed_archive('bridge')
    now=a.target_facts.inputs.clock.utc_now
    a=replace(a,risk_terms=replace(a.risk_terms,valid_until=now))
    with pytest.raises(ValueError):ProgramFinancialArchiveCodec().encode(a)
