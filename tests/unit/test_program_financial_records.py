from dataclasses import replace
from decimal import Decimal
import hashlib,json
from pathlib import Path

import pytest

from unit.support_program_financial import financial_case
from trading_system.contracts.canonical import domain_payload
from trading_system.contracts.ledger_intents import LedgerIntentCodec,intent_identity
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.intents.program_financial_records import (
    ProgramFinancialArchiveCodec,financial_intent_from_archive,validate_financial_archive)


@pytest.mark.parametrize('template',['ATOMIC','VP'])
def test_v2_v3_content_archive_and_public_records_preserve_exact_types(template):
    c=financial_case(template);codec=ProgramFinancialArchiveCodec()
    restored=codec.decode(codec.encode(c.archive))
    assert restored==c.archive and codec.hash(restored)==c.intent.hashes.archive
    assert type(restored.compilation.original_proposal) is type(c.intent.proposal)
    assert type(restored.compilation.original_program) is type(c.intent.proposal.program)
    assert restored.compilation.nodes[0].parameters==c.archive.compilation.nodes[0].parameters
    assert LedgerIntentCodec().decode(LedgerIntentCodec().encode(c.intent))==c.intent
    assert VersionedOrderCodec().decode('program-financial-risk-basis',
        VersionedOrderCodec().encode('program-financial-risk-basis',c.basis))==c.basis
    assert not c.intent.execution_authorized and not c.basis.execution_authorized and not restored.execution_authorized
    assert not hasattr(c.intent,'execution') and 'order_preference' not in LedgerIntentCodec().encode(c.intent)


@pytest.mark.parametrize('fault',['rules','caps','profile','book','facts','allocation','context','source','terms','metrics','bounds'])
def test_hash_or_owner_conflict_in_full_archive_is_rejected(fault):
    c=financial_case();a=c.archive
    if fault=='rules':a=replace(a,rules=replace(a.rules,revision=2))
    if fault=='caps':a=replace(a,capabilities=replace(a.capabilities,adapter_build_digest='f'*64))
    if fault=='profile':a=replace(a,profile=replace(a.profile,profile_id='different'))
    if fault=='book':a=replace(a,book=replace(a.book,sequence=2))
    if fault=='facts':a=replace(a,target_facts=replace(a.target_facts,transaction_id='changed'))
    if fault=='allocation':a=replace(a,allocation=replace(a.allocation,program_hash='f'*64))
    if fault=='context':a=replace(a,target_facts=replace(a.target_facts,context=replace(a.source.context,identity_id='other')))
    if fault=='source':a=replace(a,source=replace(a.source,source_hash='f'*64))
    if fault=='terms':a=replace(a,risk_terms=replace(a.risk_terms,owner_strategy_id='other'))
    if fault=='metrics':a=replace(a,risk_metrics=replace(a.risk_metrics,required_assets=None))
    if fault=='bounds':a=replace(a,bounds=replace(a.bounds,maximum_planned_loss=replace(a.bounds.maximum_planned_loss,amount=Decimal('1'))))
    with pytest.raises(ValueError):ProgramFinancialArchiveCodec().encode(a)


def test_public_record_cannot_substitute_changed_archive_or_kind():
    c=financial_case()
    with pytest.raises(ValueError):validate_financial_archive(c.intent,replace(c.archive,intent_id='other'))
    with pytest.raises(ValueError):LedgerIntentCodec().decode(VersionedOrderCodec().encode('program-financial-risk-basis',c.basis))
    identity=intent_identity(c.intent)
    assert identity.scope==c.intent.scope and identity.owner_strategy_id==c.intent.owner_strategy_id
    assert not hasattr(identity,'execution')


@pytest.mark.parametrize('fault',['unit','residual','version','scope','hash'])
def test_public_schema_and_constructor_fail_closed(fault):
    c=financial_case();payload=LedgerIntentCodec().encode(c.intent)
    if fault=='unit':payload['unit']['ledger_units_per_native_unit']='2'
    if fault=='residual':payload['target']['residual']['value']='1'
    if fault=='version':payload['contract_version']='future'
    if fault=='scope':payload['scope']['venue_account_id']='foreign'
    if fault=='hash':payload['hashes']['proposal']='f'*64
    with pytest.raises(Exception):LedgerIntentCodec().decode(payload)


def test_existing_public_and_packaged_schema_files_remain_byte_identical():
    root=Path(__file__).parents[2]
    baseline=json.loads((root/'docs/evidence/binance-orders/op-c-financial-records/existing-schema-sha256.json').read_text())
    for name,expected in baseline.items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name


@pytest.mark.parametrize('field,value',[('requested_leverage',4),('action','HOLD'),
    ('submission_deadline','2026-10-28T11:00:00Z')])
def test_risk_terms_cannot_change_original_proposal_responsibility(field,value):
    c=financial_case();a=replace(c.archive,risk_terms=replace(c.archive.risk_terms,**{field:value}))
    with pytest.raises(ValueError,match='FINANCIAL_ARCHIVE_SOURCE_TERMS_MISMATCH'):
        ProgramFinancialArchiveCodec().encode(a)


def test_complete_known_content_and_provider_booleans_cannot_replace_typed_risk_facts():
    c=financial_case()
    ProgramFinancialArchiveCodec().encode(c.archive)
    assert c.archive.risk_metrics.protection_complete is True
    with pytest.raises(ValueError,match='PROGRAM_RISK_FACTS_ARCHIVE_REQUIRED'):
        financial_intent_from_archive(c.archive)


@pytest.mark.parametrize('fault',['side','bridge','signed_delta'])
def test_self_consistent_hashes_cannot_hide_source_or_unit_contradictions(fault):
    from trading_system.contracts.canonical import canonical_hash
    c=financial_case();a=c.archive;allocation=a.allocation;facts=a.target_facts;terms=a.risk_terms
    if fault=='side':
        allocation=replace(allocation,terms=replace(allocation.terms,target_side='SHORT'))
        terms=replace(terms,target_side='SHORT')
    if fault=='bridge':
        facts=replace(facts,unit_binding=replace(facts.unit_binding,native_multiplier=Decimal(2)))
        allocation=replace(allocation,facts_hash=canonical_hash(domain_payload(facts)))
    if fault=='signed_delta':
        allocation=replace(allocation,delta=replace(allocation.delta,
            target=replace(allocation.delta.target,value=-allocation.delta.target.value)))
    metrics=replace(a.risk_metrics,allocation_hash=canonical_hash(domain_payload(allocation)))
    altered=replace(a,allocation=allocation,target_facts=facts,risk_terms=terms,risk_metrics=metrics)
    with pytest.raises(ValueError):ProgramFinancialArchiveCodec().encode(altered)
