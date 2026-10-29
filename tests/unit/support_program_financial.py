"""Synthetic complete content records, explicitly not independent approval."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
import sys
import hashlib


def financial_case(template='ATOMIC', *, intent_id=None):
    # Existing standalone fixture modules use sibling imports. This test-only
    # path is fixed within this checkout, never a payload-provided module path.
    directory=str(Path(__file__).parents[1]/'intents')
    if directory not in sys.path:sys.path.insert(0,directory)
    from test_program_target_allocation import plan_for,scenario,NOW,END
    from test_program_compilation import inputs
    from test_program_exposure import facts_for
    from unit.support_ip_06 import make_case,money
    from trading_system.contracts import types as t
    from trading_system.contracts.canonical import canonical_hash,domain_payload
    from trading_system.contracts.ledger_intents import LedgerIntentCodec
    from trading_system.contracts.program_financial import (FinancialSource,FinancialBounds,FinancialBook,
        FinancialUnit,FinancialTarget,FinancialHashes,ProgramFinancialIntent,ProgramFinancialRiskBasis)
    from trading_system.intents.program_target_allocation import compile_program_target
    from trading_system.intents.program_financial_records import ProgramFinancialArchive,ProgramFinancialArchiveCodec
    from trading_system.risk.program_exposure import derive_program_exposure
    from trading_system.risk.terms import RiskSource,RiskTerms,RiskMetrics
    digest=lambda value:canonical_hash(domain_payload(value))
    plan,rules=plan_for(template=template)
    book,target_facts=scenario(plan,rules)
    allocation=compile_program_target(plan,book,target_facts)
    budget=replace(facts_for(plan),collateral_group_id=target_facts.collateral_group_id)
    exposure=derive_program_exposure(plan,budget,now=NOW)
    assert not allocation.blockers and not exposure.blockers
    _,_,caps,kw=inputs(plan.scope.product)
    p=plan.original_proposal;identifier=intent_id or 'financial-'+template
    source=FinancialSource(source_kind='ORDER_PROPOSAL',source_version=p.contract_version,
        source_hash=plan.proposal_hash,intake_record_id='bounded-intake-'+template,intake_hash='a'*64,
        intake_transaction_id='source-intake-transaction',context=target_facts.context,
        context_hash=digest(target_facts.context))
    bounds=FinancialBounds(maximum_planned_loss=money('1000'),
        maximum_holding_seconds=p.exit_plan.maximum_holding_seconds,protection_policy=p.protection_policy,
        stop_trigger=p.exit_plan.stop_trigger,stop_price=D(p.exit_plan.stop_price),review_due_at=p.exit_plan.review_due_at)
    terms=RiskTerms(source=RiskSource(kind='ORDER_PROPOSAL',version=p.contract_version,hash=plan.proposal_hash),
        decision_id=identifier,scope=plan.scope,authorization_id=source.context.authorization_id,
        owner_strategy_id=target_facts.owner.owner_strategy_id,ownership_epoch=target_facts.owner.ownership_epoch,
        policy_revision=target_facts.owner.policy_revision,collateral_group_id=target_facts.collateral_group_id,
        basis_fingerprint=book.basis_fingerprint,snapshot_id=source.context.snapshot_ref.id,action=p.action,
        target_side=p.target_side,target=allocation.terms.target,requested_leverage=p.requested_leverage,
        maximum_planned_loss=bounds.maximum_planned_loss,protection_policy=p.protection_policy,
        stop_price=bounds.stop_price,stop_trigger=bounds.stop_trigger,maximum_holding_seconds=bounds.maximum_holding_seconds,
        review_due_at=bounds.review_due_at,as_of=NOW,created_at=NOW,valid_until=p.valid_until,
        submission_deadline=p.program.submit_valid_until)
    from trading_system.risk.exposure import ExposureCalculator,ExposureMark
    from trading_system.risk.valuation import Valuation
    mark=ExposureMark(scope=plan.scope,price=t.Price(value=D(plan.nodes[0].sizing_price_bound),currency=rules.quote_asset),
        multiplier=target_facts.unit_binding.ledger_multiplier,unit=allocation.terms.target.unit,related_group='synthetic-fixture')
    report=ExposureCalculator(Valuation(rules.quote_asset),(mark,),()).project(book,allocation.delta)
    # This is only a declared hash for storage/codec negatives, never a full
    # ProgramRiskFacts value or a producer-authenticated risk proof.
    declared_risk_hash=hashlib.sha256(b'UNVERIFIED_TEST_DECLARATION_NOT_RISK_PROOF').hexdigest()
    metrics=RiskMetrics(source=terms.source,allocation_hash=digest(allocation),facts_hash=declared_risk_hash,
        book_hash=digest(book),book_sequence=book.sequence,basis_fingerprint=book.basis_fingerprint,
        snapshot_ref=source.context.snapshot_ref,evaluated_at=NOW,valid_until=END,
        maximum_order_notional=exposure.gross_program,planned_loss=money('100'),fees=money('0'),funding=money('0'),
        capital=exposure.gross_program,required_assets=exposure.additional_requirements,exposure=report,
        related_groups=report.groups,protection_complete=True,holding_horizon_seconds=terms.maximum_holding_seconds,
        open_position_count=1,active_order_count=1,financial_coverage=book.coverage,evidence=book.evidence)
    policy=replace(make_case(plan.scope.product).policy,collateral_group_id=target_facts.collateral_group_id)
    archive=ProgramFinancialArchive(contract_version='2026-10-29',record_kind='PROGRAM_FINANCIAL_ARCHIVE',
        intent_id=identifier,source=source,bounds=bounds,compilation=plan,rules=rules,capabilities=caps,
        profile=kw['profile'],book=book,target_facts=target_facts,allocation=allocation,
        budget_facts=budget,exposure=exposure,risk_terms=terms,risk_metrics=metrics,risk_policy=policy)
    bridge=target_facts.unit_binding
    hashes=FinancialHashes(proposal=plan.proposal_hash,program=plan.program_hash,compilation=digest(plan),
        rules=plan.rules_hash,capabilities=plan.capabilities_hash,profile=plan.profile_hash,target_facts=digest(target_facts),
        allocation=digest(allocation),budget_facts=digest(budget),exposure=digest(exposure),risk_facts=declared_risk_hash,
        risk_terms=digest(terms),risk_metrics=digest(metrics),policy=digest(policy),archive=ProgramFinancialArchiveCodec().hash(archive))
    # A public DTO can carry unverified declarations and be stored for review.
    # The production archive->financial builder explicitly refuses this proof.
    intent=ProgramFinancialIntent(contract_version='2026-10-29',record_kind='PROGRAM_FINANCIAL_INTENT',
        intent_id=identifier,scope=plan.scope,venue=allocation.terms.venue,collateral_group_id=target_facts.collateral_group_id,
        owner_strategy_id=target_facts.owner.owner_strategy_id,strategy_revision=source.context.strategy_revision,
        ownership_epoch=target_facts.owner.ownership_epoch,risk_policy_revision=target_facts.owner.policy_revision,
        authorization_id=terms.authorization_id,source=source,proposal=p,
        book=FinancialBook(sequence=book.sequence,hash=digest(book),basis_fingerprint=book.basis_fingerprint,
            snapshot_ref=source.context.snapshot_ref,as_of=terms.as_of),
        unit=FinancialUnit(native_unit=bridge.native_unit,ledger_unit=bridge.ledger_unit,
            ledger_units_per_native_unit=bridge.ledger_units_per_native_unit,native_multiplier=bridge.native_multiplier,
            ledger_multiplier=bridge.ledger_multiplier),
        target=FinancialTarget(side=allocation.terms.target_side,normalized=allocation.terms.target,actual=allocation.delta.actual,
            working=allocation.delta.working,remaining=allocation.delta.remaining,residual=allocation.target_residual),
        bounds=bounds,hashes=hashes,created_at=terms.created_at,valid_until=terms.valid_until)
    basis=ProgramFinancialRiskBasis(contract_version='2026-10-29',record_kind='PROGRAM_FINANCIAL_RISK_BASIS',
        intent_id=identifier,scope=plan.scope,intent_hash=LedgerIntentCodec().hash(intent),source_hash=hashes.proposal,
        book_hash=intent.book.hash,allocation_hash=hashes.allocation,terms_hash=hashes.risk_terms,metrics_hash=hashes.risk_metrics,
        risk_facts_hash=hashes.risk_facts,policy_hash=hashes.policy,archive_hash=hashes.archive,
        incremental_requirements=metrics.required_assets)
    return SimpleNamespace(archive=archive,intent=intent,basis=basis)
