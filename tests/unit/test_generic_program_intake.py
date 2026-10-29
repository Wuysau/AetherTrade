"""Full typed source provenance; these unit rows are not PostgreSQL authority."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from unit.support_program_financial import financial_case
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.storage.models import scope_id


def source_case():
    c=financial_case()
    from trading_system.intents.generic_program_intake import GenericProgramIntake,read_program_source,intake_key
    i=c.intent
    record=GenericProgramIntake(contract_version='2026-10-29',record_kind='GENERIC_PROGRAM_INTAKE',
        intent_id=i.intent_id,proposal=i.proposal,context=i.source.context,
        compilation_hash=i.hashes.compilation,book_sequence=i.book.sequence,basis_fingerprint=i.book.basis_fingerprint)
    key=intake_key(i.proposal,i.source.context)
    row=(scope_id(i.scope),key,1,domain_payload(record),'actual-database-intake-transaction')
    tx=SimpleNamespace(scope=i.scope,execute=lambda *args:SimpleNamespace(fetchone=lambda:row))
    return c,record,key,row,tx,read_program_source


def test_source_hash_and_transaction_are_read_from_the_bounded_intake_row():
    c,record,key,row,tx,read=source_case()
    source,restored=read(tx,key)
    assert restored==record and restored.proposal==c.intent.proposal
    assert source.intake_hash==canonical_hash(row[3])
    assert source.intake_transaction_id==row[4] and source.source_hash==c.intent.hashes.proposal


@pytest.mark.parametrize('column,value',[(0,'foreign-scope'),(1,'other-key'),(2,2),(4,'')])
def test_source_rejects_physical_identity_or_transaction_substitution(column,value):
    c,record,key,row,tx,read=source_case()
    changed=list(row);changed[column]=value
    tx.execute=lambda *args:SimpleNamespace(fetchone=lambda:tuple(changed))
    with pytest.raises(ValueError,match='PROGRAM_INTAKE'):
        read(tx,key)


def test_source_rejects_unversioned_owner_audit_or_old_intake_payload():
    c,record,key,row,tx,read=source_case()
    payload=dict(row[3]);payload.pop('record_kind')
    changed=(*row[:3],payload,row[4])
    tx.execute=lambda *args:SimpleNamespace(fetchone=lambda:changed)
    with pytest.raises(ValueError):read(tx,key)


def test_source_retains_numeric_spelling_and_original_full_program():
    c,record,key,row,tx,read=source_case()
    _,result=read(tx,key)
    node=result.proposal.program.nodes[0].order
    assert node.price.kind=='FIXED_LIMIT' and node.price.value=='81490.00'
    assert result.execution_authorized is False


def test_global_financial_intent_ids_include_the_full_physical_scope():
    from trading_system.intents.generic_program_intake import financial_intent_id
    c,record,key,row,tx,read=source_case()
    other=replace(tx.scope,instrument_uid='other-instrument')
    assert financial_intent_id(tx.scope,key)!=financial_intent_id(other,key)
