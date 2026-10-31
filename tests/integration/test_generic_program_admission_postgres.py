"""Actual public intake/financial/request/outbox composition in isolated PG."""
from dataclasses import replace

import pytest

from integration.test_program_commands_postgres import database,lease,isolated_postgres
from integration.test_program_financial_admission import real_case
from trading_system.intents.generic_program_admission import GenericProgramAdmissionService,ADMISSION_PREFIX
from trading_system.execution.program_commands import ProgramCommandRepository
from trading_system.risk.program_financial_admission import ProgramFinancialBundle,BUNDLE_PREFIX
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork


def public_service(c):
    return GenericProgramAdmissionService(c.ingress,c.service,load_inputs=c.load,
        load_qualification=c.qualify,load_clock=lambda _:c.c.facts.inputs.clock,
        load_lease=lambda _:lease(c))


def test_actual_public_admission_commits_request_and_outbox_then_replays_original_ids(database):
    c=real_case(database);service=public_service(c)
    receipt=service.admit(c.record.proposal,c.record.context,c.c.plan)
    assert receipt.intent_id==c.record.intent_id and not receipt.execution_authorized
    assert len(receipt.attachment.command_ids)==1 and receipt.attachment.activation_id is None
    command_id=receipt.attachment.command_ids[0]
    with c.owner.transaction(c.scope) as tx:
        repo=ProgramCommandRepository(tx)
        write,request=repo.write(command_id),repo.request(command_id)
        assert write.state=='DURABLE_READY' and write.intent_id==receipt.intent_id
        assert dict(request.form_parameters())['price']=='81490.00'
        assert dict(request.form_parameters())['type']=='LIMIT'
        assert write.request_hash==receipt.attachment.request_hashes[0]
        assert repo.binding(command_id).bundle_hash==receipt.bundle_hash
        assert tx.execute('SELECT kind FROM ledger.outbox WHERE aggregate_id=%s',(command_id,)).fetchone()==('PROGRAM_COMMAND_READY',)
        assert tx._latest('audit_events',ADMISSION_PREFIX+receipt.intent_id,type(receipt))==receipt
        assert tx.reservations.for_intent(receipt.intent_id)[0].command_refs==(command_id,)
        assert tx.execute('SELECT count(*) FROM ledger.commands WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
    def never_reassess(*args):raise AssertionError('durable replay must not renew financial admission')
    service.load_inputs=never_reassess;service.load_lease=never_reassess
    assert service.admit(c.record.proposal,c.record.context,c.c.plan)==receipt
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.outbox WHERE aggregate_id=%s',(command_id,)).fetchone()==(1,)


@pytest.mark.parametrize('fault',['after_real_append','commit_failure','final_book_changed','final_proof_expired'])
def test_public_failure_rolls_back_real_request_outbox_and_financial_bundle(database,monkeypatch,fault):
    c=real_case(database);service=public_service(c)
    if fault=='commit_failure':
        class FailBeforeCommit:
            def __init__(self,connection):self.connection=connection
            def __getattr__(self,name):return getattr(self.connection,name)
            def commit(self):raise OSError('injected actual public PostgreSQL commit failure')
        c.service.uow=UnitOfWork(lambda:FailBeforeCommit(c.owner.connection_factory()))
    original=ProgramCommandRepository.append_creation
    def append(repo,*args,**kwargs):
        result=original(repo,*args,**kwargs)
        if fault=='after_real_append':raise OSError('injected after actual request/outbox append')
        if fault=='final_book_changed':
            previous=repo.tx.projections.book(c.scope)
            repo.tx.projections.save_book(replace(previous,sequence=previous.sequence+1),previous.sequence)
        if fault=='final_proof_expired':c.service.clock=lambda:c.c.terms.valid_until
        return result
    monkeypatch.setattr(ProgramCommandRepository,'append_creation',append)
    with pytest.raises((ValueError,OSError)):
        service.admit(c.record.proposal,c.record.context,c.c.plan)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
        assert tx.reservations.for_intent(c.record.intent_id)==()
        assert tx._latest('audit_events',BUNDLE_PREFIX+c.record.intent_id,ProgramFinancialBundle) is None
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.program_request_bindings WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.outbox WHERE scope_id=%s AND kind=%s',
            (scope_id(c.scope),'PROGRAM_COMMAND_READY')).fetchone()==(0,)
        assert tx._latest('order_program_intakes',c.key,type(c.record))==c.record


@pytest.mark.parametrize('template',['TWAP','VP'])
def test_public_local_program_admission_attaches_real_sum_state_without_a_venue_mother(database,template):
    from trading_system.execution.local_program_activation import read_local_activation
    c=real_case(database,template=template,mode='LOCAL_SYNTHETIC');service=public_service(c)
    service.load_lease=None
    receipt=service.admit(c.record.proposal,c.record.context,c.c.plan)
    assert receipt.attachment.command_ids==() and receipt.attachment.request_hashes==()
    assert receipt.attachment.activation_id and not receipt.execution_authorized
    with c.owner.transaction(c.scope) as tx:
        local=read_local_activation(tx,receipt.attachment.activation_id)
        assert local.financial_intent_id==receipt.intent_id and local.bundle_hash==receipt.bundle_hash
        assert local.state.status=='ACTIVE' and local.authority.groups[0].aggregation=='SUM'
        assert local.authority.parent_command_id is local.binding.parent_venue_id is None
        assert tx.reservations.for_intent(receipt.intent_id)[0].command_refs==()
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.outbox WHERE scope_id=%s AND kind=%s',
            (scope_id(c.scope),'PROGRAM_COMMAND_READY')).fetchone()==(0,)
    assert service.admit(c.record.proposal,c.record.context,c.c.plan)==receipt


def test_public_local_attachment_failure_rolls_back_actual_activation_and_financial_bundle(database,monkeypatch):
    from trading_system.execution import local_program_activation as local
    c=real_case(database,template='TWAP',mode='LOCAL_SYNTHETIC');service=public_service(c)
    original=local.activate_local_program
    def fail_after(*args,**kwargs):
        original(*args,**kwargs)
        raise OSError('injected after actual local activation')
    monkeypatch.setattr(local,'activate_local_program',fail_after)
    with pytest.raises(OSError,match='after actual local activation'):
        service.admit(c.record.proposal,c.record.context,c.c.plan)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
        assert tx.reservations.for_intent(c.record.intent_id)==()
        assert local.read_local_activation(tx,'local-program:'+c.record.intent_id) is None


@pytest.mark.parametrize('phase',['mark','consume'])
def test_real_zero_asset_local_unknown_cannot_be_bypassed_by_external_current_boundary(database,phase):
    from trading_system.contracts import types as t
    from trading_system.execution.local_program_activation import read_local_activation
    from trading_system.storage.local_programs import STATE_PREFIX
    from trading_system.risk.reservation_repository import ReservationRepository
    from integration.test_program_commands_postgres import boundary
    c=real_case(database,product=t.Product.USD_M_PERP,actual='1',target='0',quantity='1',side='SELL',reduce_only=True)
    external=public_service(c).admit(c.record.proposal,c.record.context,c.c.plan)
    command_id=external.attachment.command_ids[0]
    local=real_case(database,collateral_group_id=c.c.terms.collateral_group_id,
        template='TWAP',mode='LOCAL_SYNTHETIC',product=t.Product.USD_M_PERP,
        actual='1',target='0',quantity='1',side='SELL',reduce_only=True)
    # Both actual risk-assessed exits have zero new asset requirements. Their
    # quantity duties remain despite the absence of cash reservations.
    with local.owner.transaction(local.scope) as tx:holds=ReservationRepository(tx).group()
    assert holds==()
    receipt=public_service(local).admit(local.record.proposal,local.record.context,local.c.plan)
    with local.owner.transaction(local.scope) as tx:
        record=read_local_activation(tx,receipt.attachment.activation_id)
        assert tx.reservations.for_intent(local.record.intent_id)==()
    gate=boundary(c)
    permit=gate.mark_may_have_sent(command_id,lease(c)) if phase=='consume' else None
    with local.owner.transaction(local.scope) as tx:
        tx._append('audit_events',STATE_PREFIX+record.activation_id,
            replace(record.state,sequence=4,status='SUBMIT_UNKNOWN'),3)
    with pytest.raises(ValueError,match='LOCAL_PROGRAM'):
        if phase=='mark':gate.mark_may_have_sent(command_id,lease(c))
        else:gate.consume(permit,lease(c))
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)
