"""Current bundle predicates use real program assessment, never supplied ALLOW."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from unit.test_assessment_archive import actual_archive
from trading_system.accounts.authorization import AuthorizationBinding
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.intents.generic_program_intake import GenericProgramIntake


def current_case():
    from trading_system.risk.program_financial_admission import ProgramFinancialInputs,validate_current_inputs
    a=actual_archive();context=a.source.context;terms=a.risk_terms
    grant=t.Authorization(authorization_id=context.authorization_id,owner_strategy_id=terms.owner_strategy_id,
        environment=a.book.scope.environment,deployment_id=context.deployment_id,scopes=(a.book.scope,),
        maximum_capital=t.Money(currency='USDT',amount=Decimal('1000000')),
        policy_revision=context.policy_revision,protection_options=(terms.protection_policy,),
        model_cost_budget=t.Money(currency='USDT',amount=Decimal(0)),
        valid_from=terms.created_at,expires_at=terms.valid_until,revoked_at=None)
    auth=AuthorizationBinding(authorization=grant,identity_id=context.identity_id,
        strategy_revision=context.strategy_revision,ownership_epoch=context.ownership_epoch,
        queue_environment=context.environment,capabilities=(),evidence=a.book.evidence)
    intake=GenericProgramIntake(contract_version='2026-10-29',record_kind='GENERIC_PROGRAM_INTAKE',
        intent_id=a.intent_id,proposal=a.compilation.original_proposal,context=context,
        compilation_hash=canonical_hash(domain_payload(a.compilation)),book_sequence=a.book.sequence,
        basis_fingerprint=a.book.basis_fingerprint)
    tx=SimpleNamespace(transaction_id=a.target_facts.transaction_id,scope=a.book.scope,group_id=terms.collateral_group_id)
    funds=(t.Money(currency='USDT',amount=Decimal('1000000')),)
    inputs=ProgramFinancialInputs(archive=a,authorization=auth,group_capacity=funds,strategy_capacity=funds,
        buffer=(t.Money(currency='USDT',amount=Decimal(0)),))
    return a,intake,tx,inputs,validate_current_inputs


def test_complete_actual_assessment_still_requires_current_bound_source_and_clock():
    a,intake,tx,inputs,validate=current_case()
    intent,basis=validate(tx,a.source,intake,a.book,inputs,a.risk_facts.inputs.clock)
    assert intent.proposal.program.nodes[0].order.price.value=='81490.00'
    assert basis.incremental_requirements==a.assessment.metrics.required_assets
    assert not intent.execution_authorized and not basis.execution_authorized


@pytest.mark.parametrize('fault',['foreign_tx','changed_book','changed_source','stale_clock','revoked','protection','capital','pending'])
def test_historical_allow_cannot_replace_current_financial_inputs(fault):
    a,intake,tx,inputs,validate=current_case();source=a.source;book=a.book;clock=a.risk_facts.inputs.clock
    if fault=='foreign_tx':tx.transaction_id='other-owner-transaction'
    if fault=='changed_book':book=replace(book,sequence=book.sequence+1)
    if fault=='changed_source':source=replace(source,intake_transaction_id='different-source-transaction')
    if fault=='stale_clock':clock=replace(clock,monotonic_ns=clock.monotonic_ns+1)
    if fault=='revoked':inputs=replace(inputs,authorization=replace(inputs.authorization,
        authorization=replace(inputs.authorization.authorization,revoked_at=clock.utc_now)))
    if fault=='protection':inputs=replace(inputs,authorization=replace(inputs.authorization,
        authorization=replace(inputs.authorization.authorization,protection_options=('HOSTED_ALLOWED_CASH_ONLY',))))
    if fault=='capital':inputs=replace(inputs,authorization=replace(inputs.authorization,
        authorization=replace(inputs.authorization.authorization,maximum_capital=replace(
            inputs.authorization.authorization.maximum_capital,amount=Decimal(1)))))
    if fault=='pending':intake=replace(intake,book_sequence=intake.book_sequence+1)
    with pytest.raises(ValueError):validate(tx,source,intake,book,inputs,clock)
