"""An independent current program reduction proof is required under cause locks."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from unit.test_assessment_archive import actual_archive,bounded_archive
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.intents.program_assessment_archive import financial_intent_from_assessment_archive
from trading_system.risk.locked_actions import LockedActionVerifier


def digest(value):return canonical_hash(domain_payload(value))


def tx_for(a,locks=()):
    return SimpleNamespace(scope=a.book.scope,group_id=a.risk_terms.collateral_group_id,
        transaction_id=a.target_facts.transaction_id,risk_locks=SimpleNamespace(applicable=lambda s:locks),
        execute=lambda sql,*args:SimpleNamespace(fetchone=lambda:(None,),fetchall=lambda:[]))


def test_unlocked_program_still_requires_complete_current_book():
    a=actual_archive();i=financial_intent_from_assessment_archive(a);tx=tx_for(a)
    verify=LockedActionVerifier().verify_program
    assert verify(tx,i,a,a.risk_facts.inputs.clock.utc_now,book=a.book) is not None
    assert verify(tx,i,a,a.risk_facts.inputs.clock.utc_now,
        book=replace(a.book,coverage=replace(a.book.coverage,pagination_complete=False))) is None


def test_locked_program_defaults_deny_and_genuine_bounded_exit_needs_independent_reader():
    a=bounded_archive();i=financial_intent_from_assessment_archive(a);p=a.risk_facts.reduction_proof
    tx=tx_for(a,p.locks);now=a.risk_facts.inputs.clock.utc_now
    verify=LockedActionVerifier().verify_program
    assert verify(tx,i,a,now,book=a.book) is None
    from trading_system.risk.locked_actions import ProgramActionProof
    proof=ProgramActionProof(transaction_id=tx.transaction_id,intent_hash=digest(i),archive_hash=digest(a),
        book_hash=digest(a.book),reduction=p,valid_until=p.valid_until)
    assert verify(tx,i,a,now,book=a.book,read_proof=lambda *args:proof) is not None
    assert verify(tx,i,a,now,book=a.book,read_proof=lambda *args:replace(proof,transaction_id='foreign')) is None


def test_new_current_lock_cannot_be_erased_by_a_captured_allow_or_reduction_proof():
    a=bounded_archive();i=financial_intent_from_assessment_archive(a);p=a.risk_facts.reduction_proof
    from trading_system.risk.locked_actions import ProgramActionProof
    lock=replace(p.locks[0],lock_id='new-unknown',cause='ORDER_OUTCOME_UNKNOWN')
    tx=tx_for(a,p.locks+(lock,));proof=ProgramActionProof(transaction_id=tx.transaction_id,intent_hash=digest(i),
        archive_hash=digest(a),book_hash=digest(a.book),reduction=p,valid_until=p.valid_until)
    assert LockedActionVerifier().verify_program(tx,i,a,a.risk_facts.inputs.clock.utc_now,book=a.book,
        read_proof=lambda *args:proof) is None
