"""Cross-version safety probes; these synthetic rows confer no execution permit."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload, utc_timestamp
from trading_system.contracts.order_lifecycle import ApplicationWrite
from trading_system.contracts.order_program import OrderProgramCodec
from trading_system.execution.commands import CommandRepository
from trading_system.intents.semantic_validation import ProposalRejected
from trading_system.risk.target_guard import target_obligations
from trading_system.storage.models import MissingEvidence, scope_id
from unit.support_ip_09 import make_case, NOW


def write_case(scope=None, command_id='program-command'):
    scope=scope or make_case().book.scope
    program=OrderProgramCodec().decode(json.loads((Path(__file__).parents[1]/
        'fixtures/orders-v2/program.paper.json').read_text(encoding='utf-8')))
    write=ApplicationWrite(contract_version='2026-10-28',command_id=command_id,
        scope=scope,program_id=program.program_id,origin='APPLICATION_ACTION',operation='SUBMIT_PROGRAM',
        service_namespace='EXECUTION_ALGO',venue_client_id='original-client',owner_strategy_id='owner',
        parent_command_id=None,target_venue_id=None,canonical_program_hash=OrderProgramCodec().hash(program),
        ownership_epoch=1,execution_epoch=1,policy_revision=1,durable_record_ref='pending',
        state='DURABLE_READY',created_at=NOW)
    return write,program


class Reads:
    def __init__(self,scope,group='group'):
        self.scope,self.group_id=scope,group
        self.present=True
        self.rows=[]
        self.programs={}
        self.bindings={}

    def add(self,write,program,sequence=1):
        self.rows.append((scope_id(write.scope),write.command_id,sequence,domain_payload(write)))
        self.programs[(scope_id(write.scope),program.program_id)]=domain_payload(program)
        self.bindings[(str(write.scope.environment),write.scope.venue_account_id,str(write.scope.product))]=self.group_id

    def execute(self,query,args=()):
        if 'to_regclass' in query:
            return SimpleNamespace(fetchone=lambda:('ledger.order_writes_v2' if self.present else None,))
        if 'FROM ledger.order_writes_v2' in query:
            return SimpleNamespace(fetchall=lambda:list(self.rows))
        if 'FROM ledger.account_bindings' in query:
            value=self.bindings.get(tuple(args))
            return SimpleNamespace(fetchone=lambda:None if value is None else (value,))
        if 'FROM ledger.order_programs' in query:
            value=self.programs.get(tuple(args))
            return SimpleNamespace(fetchone=lambda:None if value is None else (value,))
        if 'FROM ledger.commands' in query or 'FROM ledger.commands' in query.replace('\n',' '):
            return SimpleNamespace(fetchall=lambda:[])
        raise AssertionError((query,args))

    def _latest(self,*args):return None


def populated(state='DURABLE_READY',command_id='program-command'):
    write,program=write_case(command_id=command_id)
    tx=Reads(write.scope)
    tx.add(write,program)
    if state!='DURABLE_READY':
        tx.add(replace(write,state=state,durable_record_ref='original-send-boundary'),program,2)
    return tx,write,program


def test_ready_program_is_not_silently_absent_from_legacy_target():
    tx,_,_=populated()
    with pytest.raises(ProposalRejected,match='PROGRAM_TARGET_ALLOCATION_REQUIRED'):
        target_obligations(tx,None,None,(),utc_timestamp(NOW))


@pytest.mark.parametrize('state',['MAY_HAVE_SENT','UNKNOWN','VENUE_CONFIRMED','VENUE_REJECTED','ABORTED_UNSENT'])
def test_no_raw_state_label_discharges_group_obligation(state):
    tx,write,_=populated(state)
    assert CommandRepository(tx).unresolved_in_group('legacy-other')


def test_legacy_exclusion_never_hides_program_id_collision():
    tx,write,_=populated('MAY_HAVE_SENT',command_id='same-text-id')
    unresolved=CommandRepository(tx).unresolved_in_group('same-text-id')
    assert unresolved and write.command_id in unresolved[0] and 'EXECUTION_ALGO' in unresolved[0]


def test_program_ready_consumes_queued_reduction_before_proof():
    from unit.test_locked_actions import LockedCase, NOW as locked_now
    from trading_system.risk.locked_actions import LockedActionVerifier
    case=LockedCase()
    verifier=LockedActionVerifier(case.proof)
    assert verifier.verify(case.tx,case.command,case.intent,locked_now) is not None
    case.proof_calls=0
    write,program=write_case(case.scope,case.command.command_id)
    write=replace(write,created_at=locked_now)
    reads=Reads(case.scope,case.tx.group_id);reads.add(write,program)
    original=case.tx.execute
    case.tx.execute=lambda query,args=():reads.execute(query,args) if any(
        key in query for key in ('order_writes_v2','order_programs','account_bindings')) else original(query,args)
    assert verifier.verify(case.tx,case.command,case.intent,locked_now) is None
    assert case.proof_calls==0


def test_reader_preserves_exact_identity_and_namespaces():
    from trading_system.storage.application_writes import application_write_obligations
    tx,write,_=populated('MAY_HAVE_SENT')
    obligation,=application_write_obligations(tx)
    assert obligation.original==write
    assert obligation.current.scope==write.scope
    assert obligation.current.venue_client_id==write.venue_client_id
    assert obligation.current.service_namespace=='EXECUTION_ALGO'


@pytest.mark.parametrize('fault',['physical-scope','record-id','first-sequence','version','missing-first',
    'identity','program-hash','program-missing','binding-missing','origin','malformed-latest','gap'])
def test_malformed_present_history_fails_closed(fault):
    from trading_system.storage.application_writes import application_write_obligations
    tx,write,program=populated('MAY_HAVE_SENT')
    first=list(tx.rows[0]);latest=list(tx.rows[1])
    if fault=='physical-scope':first[0]='wrong'
    if fault=='record-id':first[1]='wrong'
    if fault=='first-sequence':first[2]=0
    if fault=='version':first[3]={**first[3],'contract_version':'unknown'}
    if fault=='missing-first':tx.rows=tx.rows[1:]
    if fault=='identity':latest[3]={**latest[3],'venue_client_id':'changed'}
    if fault=='program-hash':latest[3]={**latest[3],'canonical_program_hash':'f'*64}
    if fault=='program-missing':tx.programs={}
    if fault=='binding-missing':tx.bindings={}
    if fault=='origin':first[3]={**first[3],'origin':'VENUE_GENERATED_CHILD'}
    if fault=='malformed-latest':latest[3]={'state':'UNKNOWN'}
    if fault=='gap':latest[2]=3
    if fault!='missing-first':tx.rows=[tuple(first),tuple(latest)]
    with pytest.raises(MissingEvidence,match='PROGRAM_WRITE_'):
        application_write_obligations(tx)


def test_older_database_without_table_has_no_new_obligations():
    from trading_system.storage.application_writes import application_write_obligations
    tx,_,_=populated('UNKNOWN');tx.present=False
    assert application_write_obligations(tx)==()


def test_group_binding_keeps_other_account_product_and_skips_other_group():
    from trading_system.storage.application_writes import application_write_obligations
    tx,write,program=populated('MAY_HAVE_SENT')
    other_scope=replace(write.scope,venue_account_id='other-account',product=t.Product.USD_M_PERP)
    other=replace(write,scope=other_scope,command_id='other-product')
    tx.add(other,program);tx.add(replace(other,state='MAY_HAVE_SENT'),program,2)
    assert len(application_write_obligations(tx))==2
    tx.bindings[(str(other_scope.environment),other_scope.venue_account_id,str(other_scope.product))]='other-group'
    assert len(application_write_obligations(tx))==1
