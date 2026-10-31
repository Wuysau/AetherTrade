"""Actual quote financial bundle/request/outbox closure, not venue qualification."""
import pytest

from integration.test_program_commands_postgres import database,isolated_postgres,lease
from integration.test_quote_financial_kernel_postgres import quote_case
from trading_system.intents.generic_program_admission import GenericProgramAdmissionService
from trading_system.execution.program_commands import ProgramCommandRepository
from trading_system.recovery.original_creation import read_original_creation
from trading_system.storage.models import scope_id


def public(c,*,bounds=True):
    return GenericProgramAdmissionService(c.qingress,c.service,load_inputs=c.qload,
        load_qualification=c.qqualify,load_clock=lambda _:c.c.facts.inputs.clock,
        load_lease=lambda _:lease(c),load_execution_bounds=c.bounds if bounds else None)


def test_quote_public_admission_attaches_actual_full_request_funding_and_outbox_then_replays(database):
    c=quote_case(database);service=public(c)
    receipt=service.admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)
    assert not receipt.execution_authorized and len(receipt.attachment.command_ids)==1
    command_id=receipt.attachment.command_ids[0]
    with c.owner.transaction(c.scope) as tx:
        repo=ProgramCommandRepository(tx);write=repo.write(command_id);request=repo.request(command_id)
        assert write.state=='DURABLE_READY' and write.request_hash==receipt.attachment.request_hashes[0]
        assert dict(request.form_parameters())['quoteOrderQty']=='4000'
        assert 'quantity' not in dict(request.form_parameters()) and 'price' not in dict(request.form_parameters())
        assert tx.execute('SELECT kind FROM ledger.outbox WHERE aggregate_id=%s',(command_id,)).fetchone()==('PROGRAM_COMMAND_READY',)
        assert tx.execute('SELECT count(*) FROM ledger.order_funding_links WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(1,)
        assert read_original_creation(tx,command_id).request==request
        assert tx.intents.get(receipt.intent_id).record_kind=='QUOTE_FINANCIAL_INTENT_V1'
    def do_not_renew(*args):raise AssertionError('committed receipt replay cannot renew financial facts')
    service.load_inputs=do_not_renew;service.load_execution_bounds=do_not_renew
    assert service.admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)==receipt


def test_quote_failure_after_real_request_append_rolls_back_all_financial_attachments(database,monkeypatch):
    c=quote_case(database);original=ProgramCommandRepository.append_creation;observed=[]
    def append(repo,*args,**kwargs):
        result=original(repo,*args,**kwargs);observed.append(result.command_id)
        assert repo.tx.execute('SELECT count(*) FROM ledger.outbox WHERE aggregate_id=%s',(result.command_id,)).fetchone()==(1,)
        raise OSError('fixture fails after actual quote request/outbox append')
    monkeypatch.setattr(ProgramCommandRepository,'append_creation',append)
    with pytest.raises(OSError):public(c).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)
    assert len(observed)==1
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.qrecord.intent_id) is None and tx.reservations.for_intent(c.qrecord.intent_id)==()
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.order_funding_links WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


def test_quote_public_missing_independent_execution_reader_keeps_intake_only(database):
    c=quote_case(database)
    with pytest.raises(ValueError):public(c,bounds=False).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.qrecord.intent_id) is None and tx.reservations.for_intent(c.qrecord.intent_id)==()
