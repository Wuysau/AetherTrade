"""Interval adapter into ReservationService's one financial transaction.

No second cash total, standalone public success or command/permit path exists.
The caller must still attach the actual outbox request before commit. Current
execution bounds are independently resolved by a trusted LOCAL reader; hashes
inside a supplied archive cannot establish their enforcement provenance.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.accounts.authorization import AuthorizationBinding,AuthorizationService
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.program_financial import require
from trading_system.contracts.quote_financial import QuoteFinancialIntentV1,QuoteFinancialRiskBasisV1,QuoteFinancialSourceV1
from trading_system.intents.quote_assessment_archive import (QuoteAssessmentArchiveV1,QuoteAssessmentArchiveCodec,
    quote_financial_intent,quote_financial_basis)
from trading_system.intents.quote_target_allocation import QuoteExecutionBoundsV1
from .policy_checks import capital_limit_exceeded
from .quote_assessment import QuoteRiskDecisionV1

def digest(value):return canonical_hash(domain_payload(value))


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteFinancialInputsV1:
    archive:QuoteAssessmentArchiveV1
    authorization:AuthorizationBinding
    group_capacity:tuple[t.Money,...]
    strategy_capacity:tuple[t.Money,...]
    buffer:tuple[t.Money,...]
    reconciled_holds:tuple[t.ReservationProjection,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteProgramFinancialBundleV1:
    intent:QuoteFinancialIntentV1
    basis:QuoteFinancialRiskBasisV1
    archive:QuoteAssessmentArchiveV1
    decision:QuoteRiskDecisionV1
    reservations:tuple[t.ReservationProjection,...]
    contract_version:Literal['2026-10-30']='2026-10-30'
    record_kind:Literal['QUOTE_PROGRAM_FINANCIAL_BUNDLE_V1']='QUOTE_PROGRAM_FINANCIAL_BUNDLE_V1'

    @property
    def execution_authorized(self)->Literal[False]:return False


def validate_current_quote_inputs(tx,source,intake,book,inputs,clock,load_execution_bounds):
    require(type(inputs) is QuoteFinancialInputsV1 and type(source) is QuoteFinancialSourceV1,
        'QUOTE_FINANCIAL_INPUTS_REQUIRED')
    a=inputs.archive
    require(type(a) is QuoteAssessmentArchiveV1,'QUOTE_ASSESSMENT_ARCHIVE_REQUIRED')
    s=a.risk_facts.target_facts.scope_facts
    require(digest(source)==digest(a.source) and intake.intent_id==a.intent_id
        and digest(intake.proposal)==digest(a.compilation.original_proposal)
        and digest(intake.context)==digest(source.context) and intake.compilation_hash==digest(a.compilation),
        'PROGRAM_CURRENT_SOURCE_MISMATCH')
    require(intake.book_sequence==book.sequence==a.book.sequence and intake.basis_fingerprint==book.basis_fingerprint
        and digest(book)==digest(a.book),'PROGRAM_CURRENT_BOOK_CHANGED')
    require(a.compilation.scope==book.scope==tx.scope and tx.group_id==s.collateral_group_id,
        'PROGRAM_FINANCIAL_SCOPE_MISMATCH')
    require(s.transaction_id==a.allocation.transaction_id==tx.transaction_id,'PROGRAM_CURRENT_TRANSACTION_REQUIRED')
    require(clock.clock_health=='HEALTHY' and type(clock.monotonic_ns) is int and clock.monotonic_ns>=0
        and digest(a.risk_facts.inputs.clock)==digest(clock) and digest(s.inputs.clock)==digest(clock),
        'PROGRAM_CURRENT_CLOCK_REQUIRED')
    require(type(inputs.authorization) is AuthorizationBinding,'AUTHORIZATION_REQUIRED')
    now=utc_timestamp(clock.utc_now);grant=inputs.authorization.authorization
    auth=AuthorizationService(lambda _:inputs.authorization,clock=lambda:now).validate(source.context)
    require(auth.allowed is True and not auth.rejection_codes and digest(auth)==digest(a.risk_facts.authorization)
        and grant.owner_strategy_id==a.risk_terms.owner_strategy_id,'AUTHORIZATION_REQUIRED')
    require(a.risk_terms.protection_policy in grant.protection_options,'PROTECTION_NOT_AUTHORIZED')
    require(now<utc_timestamp(a.risk_terms.valid_until)<=utc_timestamp(grant.expires_at),
        'PROGRAM_AUTHORIZATION_WINDOW_UNBOUND')
    validate_current_quote_bounds(tx,source,intake,book,clock,a,load_execution_bounds)
    QuoteAssessmentArchiveCodec().hash(a)
    intent=quote_financial_intent(a);basis=quote_financial_basis(intent,a)
    m=a.assessment.metrics
    require(grant.maximum_capital.currency==m.capital.currency==a.risk_facts.inputs.capital_in_use.currency,
        'PROGRAM_AUTHORIZATION_CAPITAL_CURRENCY_UNBOUND')
    from dataclasses import replace
    require(not capital_limit_exceeded(capital=m.capital.amount,fees=m.fees.amount,funding=m.funding.amount,
        capital_in_use=a.risk_facts.inputs.capital_in_use.amount,
        policy=replace(a.risk_policy,maximum_capital=format(grant.maximum_capital.amount,'f'))),
        'PROGRAM_AUTHORIZATION_CAPITAL_EXCEEDED')
    # Asset-scoped balances still cannot prove selected CARD/MAIN funding.
    require(tx.scope.product!=t.Product.US_EQUITY,'PROGRAM_EQUITY_WALLET_FUNDING_LOCATION_UNBOUND')
    return intent,basis


def validate_current_quote_bounds(tx,source,intake,book,clock,archive,load_execution_bounds):
    actual=load_execution_bounds(tx,source,intake,book,clock) if load_execution_bounds else None
    expected=archive.risk_facts.target_facts.bounds
    require(type(actual) is QuoteExecutionBoundsV1 and digest(actual)==digest(expected)
        and actual.scope==tx.scope and utc_timestamp(clock.utc_now)<utc_timestamp(actual.valid_until),
        'QUOTE_CURRENT_ENFORCEMENT_REQUIRED')
