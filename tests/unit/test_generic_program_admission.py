"""Admission identities are deterministic; they never provide send authority."""
from dataclasses import replace

import pytest

from unit.test_assessment_archive import actual_archive


def test_creation_ids_bind_exact_source_scope_and_compilation():
    from trading_system.intents.generic_program_admission import creation_command_ids
    a=actual_archive();plan=a.compilation
    original=creation_command_ids(a.intent_id,plan)
    assert original==creation_command_ids(a.intent_id,plan)
    assert len(original)==len(plan.requests)==1 and original[0]
    assert original!=creation_command_ids(a.intent_id+'-other',plan)
    assert original!=creation_command_ids(a.intent_id,replace(plan,
        scope=replace(plan.scope,venue_account_id='other-account')))
    assert original!=creation_command_ids(a.intent_id,replace(plan,adapter_build_digest='e'*64))


def test_creation_identity_does_not_turn_a_local_schedule_into_a_venue_mother():
    from trading_system.intents.generic_program_admission import creation_command_ids
    a=actual_archive()
    from test_program_exposure import make_plan
    local=make_plan('TWAP','LOCAL_SYNTHETIC')
    assert creation_command_ids(a.intent_id,local)==()


def test_initial_admission_does_not_implicitly_authorize_staged_external_writes():
    from trading_system.intents.generic_program_admission import creation_command_ids
    from trading_system.intents.program_compilation import Timing
    a=actual_archive();p=a.compilation
    staged=replace(p,requests=(replace(p.requests[0],timing=Timing.AFTER_FULL_FILL),))
    with pytest.raises(ValueError,match='STAGED_PROGRAM_ACTIVATION_REQUIRED'):
        creation_command_ids(a.intent_id,staged)


def test_different_canonical_numeric_source_cannot_reuse_a_creation_identity():
    from trading_system.intents.generic_program_admission import creation_command_ids
    a=actual_archive();p=a.compilation
    node=p.original_program.nodes[0]
    changed=replace(p,original_program=replace(p.original_program,nodes=(replace(node,
        order=replace(node.order,price=replace(node.order.price,value='81490.0'))),)))
    assert creation_command_ids(a.intent_id,p)!=creation_command_ids(a.intent_id,changed)


@pytest.mark.parametrize('fault',['none','commit_failure','attachment_failure','expiry','qualification_expiry','fence','book','clock'])
def test_public_admission_returns_only_after_the_shared_financial_commit(monkeypatch,fault):
    """Composition-only probe. Actual restricted SQL admission is tested in PG."""
    from types import SimpleNamespace
    from trading_system.intents import generic_program_admission as m
    from trading_system.intents.generic_program_intake import GenericProgramIntake
    from trading_system.risk.program_financial_admission import ProgramFinancialBundle
    from trading_system.intents.program_assessment_archive import (
        financial_intent_from_assessment_archive,financial_risk_basis_from_assessment_archive)
    a=actual_archive();intent=financial_intent_from_assessment_archive(a)
    basis=financial_risk_basis_from_assessment_archive(intent,a)
    bundle=ProgramFinancialBundle(intent=intent,basis=basis,archive=a,decision=a.assessment,reservations=())
    record=GenericProgramIntake(contract_version='2026-10-29',record_kind='GENERIC_PROGRAM_INTAKE',
        intent_id=a.intent_id,proposal=a.compilation.original_proposal,context=a.source.context,
        compilation_hash=m.digest(a.compilation),book_sequence=a.book.sequence,basis_fingerprint=a.book.basis_fingerprint)
    events=[];rows={};state={'book':a.book,'clock':a.risk_facts.inputs.clock,'fence':a.target_facts.owner,
        'wall':a.risk_facts.inputs.clock.utc_now}
    tx=SimpleNamespace(scope=a.book.scope,transaction_id=a.target_facts.transaction_id,
        projections=SimpleNamespace(book=lambda scope:state['book']),
        _latest=lambda table,key,cls:rows.get(key),_append=lambda table,key,value,seq:rows.setdefault(key,value))
    monkeypatch.setattr(m,'read_program_source',lambda *args:(a.source,record))
    monkeypatch.setattr(m,'current_fence',lambda *args:state['fence'])
    # The real kernel and real attachment APIs have independent full tests;
    # this verifies that the public composition exposes no result before commit.
    def reserve(*args,**kwargs):events.append('financial');return bundle
    def transaction(operation):
        receipt=operation(tx)
        assert events==['financial','attachment']
        if fault=='commit_failure':raise OSError('injected commit failure')
        events.append('commit');return receipt
    reservations=SimpleNamespace(scope=a.book.scope,uow=object(),_reserve_program=reserve,
        _transaction=transaction,clock=lambda:state['wall'],
        monotonic_clock=lambda:a.risk_facts.inputs.clock.monotonic_ns)
    ingress=SimpleNamespace(scope=a.book.scope,uow=object(),accept=lambda *args:(a.source,record))
    service=m.GenericProgramAdmissionService(ingress,reservations,load_inputs=None,
        load_clock=lambda _:state['clock'])
    def attach(tx,value,ids):
        assert value is bundle and ids==m.creation_command_ids(a.intent_id,a.compilation)
        events.append('attachment')
        if fault=='attachment_failure':raise OSError('injected attachment failure')
        if fault=='expiry':state['wall']=bundle.intent.valid_until
        if fault=='qualification_expiry':state['wall']='2026-10-28T10:00:02Z'
        if fault=='fence':state['fence']=replace(state['fence'],ownership_epoch=2)
        if fault=='book':state['book']=replace(state['book'],sequence=state['book'].sequence+1)
        if fault=='clock':state['clock']=replace(state['clock'],clock_health='UNHEALTHY')
        return m.AdmissionAttachment(command_ids=ids,request_hashes=('f'*64,),activation_id=None)
    monkeypatch.setattr(service,'_attach_bundle',attach)
    monkeypatch.setattr(service,'_verify_attachments',lambda tx,value,attachment:
        '2026-10-28T10:00:01Z' if fault=='qualification_expiry' else bundle.intent.valid_until)
    if fault!='none':
        with pytest.raises((OSError,ValueError)):
            service.admit(record.proposal,record.context,a.compilation)
        assert events==['financial','attachment']
    else:
        result=service.admit(record.proposal,record.context,a.compilation)
        assert events==['financial','attachment','commit']
        assert result.intent_id==a.intent_id and not result.execution_authorized
        assert result.attachment.command_ids==m.creation_command_ids(a.intent_id,a.compilation)


def test_attachment_description_cannot_stand_in_for_a_physical_financial_attachment():
    from types import SimpleNamespace
    from trading_system.intents import generic_program_admission as m
    a=actual_archive()
    service=object.__new__(m.GenericProgramAdmissionService)
    bundle=SimpleNamespace(intent=SimpleNamespace(intent_id=a.intent_id))
    ids=m.creation_command_ids(a.intent_id,a.compilation)
    tx=SimpleNamespace(_latest=lambda *args:None)
    claimed=m.AdmissionAttachment(command_ids=ids,request_hashes=('f'*64,),activation_id=None)
    with pytest.raises(ValueError,match='PROGRAM_ADMISSION_ATTACHMENT_REQUIRED'):
        service._verify_attachments(tx,bundle,claimed)
