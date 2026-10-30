"""Scoped quantity facts are declarations; ROOT authenticates physical IDs."""
from dataclasses import fields,replace
from decimal import Decimal as D

import pytest

from unit.support_program_financial import financial_case
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.storage.models import decode


def scoped(facts,ids=(),**changes):
    from trading_system.intents.program_target_allocation import ProgramScopedTargetFactsV2
    return ProgramScopedTargetFactsV2(**{f.name:getattr(facts,f.name) for f in fields(facts)},
        contract_version='2026-10-30',record_kind='PROGRAM_SCOPED_TARGET_FACTS_V2',
        quantity_scope=changes.pop('quantity_scope',facts.rules.scope),quantity_reservation_ids=ids,**changes)


def setup(*,local_pending=False):
    financial_case()
    from test_program_target_allocation import plan_for,scenario
    from unit.support_ip_09 import reservation,make_case
    from trading_system.intents.target_compiler import PendingQuantity
    plan,rules=plan_for(quantity='.8' if local_pending else '1');book,facts=scenario(plan,rules)
    money=reservation(make_case())
    money=replace(money,reservation=replace(money.reservation,environment=book.scope.environment,
        venue_account_id=book.scope.venue_account_id,collateral_group_id=facts.collateral_group_id,
        owner_strategy_id=facts.owner.owner_strategy_id))
    if not local_pending:
        money=replace(money,reservation=replace(money.reservation,venue_account_id='foreign-collateral-account'))
    book=replace(book,reservations=(money,))
    facts=replace(facts,book_hash=canonical_hash(domain_payload(book)))
    if local_pending:
        facts=replace(facts,inputs=replace(facts.inputs,pending=(PendingQuantity(intent_id=money.reservation.intent_id,
            signed_quantity=replace(facts.inputs.instrument.quantity_step,value=D('.2')),
            reservation_ids=(money.reservation.reservation_id,),command_refs=money.reservation.command_refs),)))
    return plan,book,facts,money


def test_explicit_subset_keeps_full_financial_book_hash_and_foreign_cash_hold():
    from trading_system.intents.program_target_allocation import compile_program_target
    from trading_system.intents.program_target_allocation import program_target_quantity_book
    plan,book,facts,money=setup();current=scoped(facts)
    result=compile_program_target(plan,book,current)
    assert result.delta.remaining.value==1 and not result.blockers
    assert result.book_hash==current.book_hash==canonical_hash(domain_payload(book))
    assert book.reservations==(money,) and program_target_quantity_book(book,current).balances==book.balances
    assert program_target_quantity_book(book,current).reservations==()


def test_selected_local_quantity_reuses_existing_target_mapping_and_equation():
    from trading_system.intents.program_target_allocation import compile_program_target
    plan,book,facts,money=setup(local_pending=True)
    result=compile_program_target(plan,book,scoped(facts,(money.reservation.reservation_id,)))
    assert result.delta.working.value==D('.2') and result.delta.remaining.value==D('.8')
    assert result.nodes[0].ledger_quantity.value==D('.8')
    with pytest.raises(ValueError,match='RESERVATION_MAPPING_INVALID'):
        compile_program_target(plan,book,scoped(facts))


@pytest.mark.parametrize('fault',('duplicate','unknown','selected_foreign','duplicate_book','wrong_scope'))
def test_forged_subset_never_creates_a_quantity_plan(fault):
    from trading_system.intents.program_target_allocation import compile_program_target
    plan,book,facts,money=setup();identifier=money.reservation.reservation_id
    if fault=='duplicate':ids=(identifier,identifier)
    elif fault=='unknown':ids=('missing-physical-id',)
    elif fault=='selected_foreign':ids=(identifier,)
    elif fault=='duplicate_book':
        ids=();book=replace(book,reservations=book.reservations*2)
        facts=replace(facts,book_hash=canonical_hash(domain_payload(book)))
    else:
        ids=();facts=scoped(facts,quantity_scope=replace(book.scope,instrument_uid='foreign-instrument'))
    with pytest.raises(ValueError):
        compile_program_target(plan,book,facts if fault=='wrong_scope' else scoped(facts,ids))


def test_v1_exact_bytes_and_target_behavior_do_not_gain_implicit_subset():
    from trading_system.intents.program_target_allocation import compile_program_target,ProgramTargetFacts
    from trading_system.intents.program_target_allocation import program_target_quantity_book
    plan,book,facts,_=setup();before=domain_payload(facts);digest=canonical_hash(before)
    assert program_target_quantity_book(book,facts) is book
    assert decode(ProgramTargetFacts,before)==facts and canonical_hash(domain_payload(facts))==digest
    with pytest.raises(ValueError,match='RESERVATION_QUANTITY_REQUIRED'):compile_program_target(plan,book,facts)
    with pytest.raises(ValueError):decode(ProgramTargetFacts,domain_payload(scoped(facts)))


@pytest.mark.parametrize('fault',('version','kind','partial','extra','bool_id'))
def test_unknown_or_partial_tags_cannot_decode_as_old_facts(fault):
    from trading_system.intents.program_target_allocation import ProgramTargetFacts
    from trading_system.intents.program_target_allocation import ProgramScopedTargetFactsV2
    _,_,facts,_=setup();payload=domain_payload(scoped(facts))
    if fault=='version':payload['contract_version']='2099-01-01'
    elif fault=='kind':payload['record_kind']='UNTRUSTED_TARGET_FACTS'
    elif fault=='partial':del payload['quantity_reservation_ids']
    elif fault=='extra':payload['trusted']=True
    else:payload['quantity_reservation_ids']=[True]
    with pytest.raises(ValueError):decode(ProgramTargetFacts|ProgramScopedTargetFactsV2,payload)


def test_full_actual_assessment_replay_and_static_union_restore_new_facts():
    from unit.test_assessment_archive import archive_from_case
    from test_program_risk_assessment import risk_case
    from trading_system.intents.program_target_allocation import compile_program_target
    from trading_system.intents.program_target_allocation import ProgramScopedTargetFactsV2
    from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchiveCodec
    from trading_system.intents.program_financial_records import ProgramFinancialArchive,ProgramFinancialArchiveCodec
    c=risk_case();c.facts=replace(c.facts,target_facts=scoped(c.facts.target_facts))
    c.allocation=compile_program_target(c.plan,c.book,c.facts.target_facts)
    c.facts=replace(c.facts,protection=tuple(replace(p,allocation_hash=canonical_hash(domain_payload(c.allocation))) for p in c.facts.protection))
    archive=archive_from_case(c);codec=ProgramFinancialAssessmentArchiveCodec()
    restored=codec.decode(codec.encode(archive))
    assert type(restored.target_facts) is type(restored.risk_facts.target_facts) is ProgramScopedTargetFactsV2
    assert restored==archive and restored.assessment.verdict=='ALLOW'
    old=financial_case().archive;payload=domain_payload(old);digest=canonical_hash(payload)
    assert ProgramFinancialArchiveCodec().decode(payload)==old
    assert canonical_hash(ProgramFinancialArchiveCodec().encode(old))==digest
    assert decode(ProgramFinancialArchive,payload)==old
