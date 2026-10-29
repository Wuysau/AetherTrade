"""New interval intent kind never aliases the historical scalar financial shape."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.ledger_intents import LedgerIntentCodec,intent_identity
from test_quote_risk_assessment import quote_risk_case,assess


def archive_case():
    from trading_system.contracts.quote_financial import QuoteFinancialSourceV1
    from trading_system.intents.quote_assessment_archive import QuoteAssessmentArchiveV1
    c=quote_risk_case();s=c.facts.target_facts.scope_facts
    source=QuoteFinancialSourceV1(source_kind='POSITION_VALUE_BAND_PROPOSAL',source_version='2026-10-30',
        source_hash=c.plan.proposal_hash,intake_record_id='bounded-intake',intake_hash='a'*64,
        intake_transaction_id='synthetic-intake-transaction',context=s.context,
        context_hash=canonical_hash(domain_payload(s.context)))
    return QuoteAssessmentArchiveV1(contract_version='2026-10-30',record_kind='QUOTE_ASSESSMENT_ARCHIVE_V1',
        intent_id=c.terms.decision_id,source=source,compilation=c.plan,book=c.book,allocation=c.allocation,
        risk_terms=c.terms,risk_facts=c.facts,risk_policy=c.policy,assessment=assess(c))


def test_interval_financial_intent_roundtrips_in_same_explicit_registry_without_fake_target():
    from trading_system.intents.quote_assessment_archive import quote_financial_intent,quote_financial_basis
    a=archive_case();intent=quote_financial_intent(a);basis=quote_financial_basis(intent,a)
    codec=LedgerIntentCodec();payload=codec.encode(intent)
    assert payload['record_kind']=='QUOTE_FINANCIAL_INTENT_V1' and payload['contract_version']=='2026-10-30'
    assert 'target' not in payload and intent.position.goal==a.compilation.original_proposal.goal
    assert canonical_hash(payload)==codec.hash(codec.decode(payload))
    assert basis.intent_hash==codec.hash(intent) and basis.incremental_requirements==a.assessment.metrics.required_assets
    assert intent_identity(intent).scope==a.book.scope
    assert not intent.execution_authorized and not basis.execution_authorized


@pytest.mark.parametrize('fault',['wrong_source','understated_spend','wrong_band','wrong_kind'])
def test_new_financial_registry_rejects_changed_source_or_version(fault):
    from trading_system.intents.quote_assessment_archive import quote_financial_intent
    codec=LedgerIntentCodec();payload=codec.encode(quote_financial_intent(archive_case()))
    if fault=='wrong_source':payload['source']['source_hash']='f'*64
    if fault=='understated_spend':payload['native_quote_amount']['amount']='1'
    if fault=='wrong_band':payload['position']['goal']['maximum_quote']['amount']='1'
    if fault=='wrong_kind':payload['record_kind']='PROGRAM_FINANCIAL_INTENT'
    with pytest.raises(ValueError):codec.decode(payload)
