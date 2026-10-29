"""Version dispatch orchestration only; PostgreSQL tests prove persistence."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contracts.test_order_lifecycle import authority, event, NOW
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_program import OrderProgramCodec
from trading_system.contracts.order_lifecycle import ApplicationWrite
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.storage.models import MissingEvidence
from trading_system.storage.order_programs import OrderProgramRepository


def program_payload(version='2026-10-28',kind='TWAP',duration=300):
    value=json.loads(Path('tests/fixtures/orders-v2/program.paper.json').read_text(encoding='utf-8'))
    value.update(contract_version=version,template=kind,execution_mode='VENUE_NATIVE')
    value['nodes'][0]['role']='ALGORITHM_PARENT'
    value['algorithm']=dict(kind=kind,duration_seconds=duration,urgency='HIGH' if kind=='VP' else None)
    if version=='2026-10-28':value['algorithm']['participation_bps']='100' if kind=='VP' else None
    return value


def program_record(version='2026-10-28',kind='TWAP',duration=300):
    return VersionedOrderCodec().decode('order-program',program_payload(version,kind,duration))


def command_for(program):
    a=authority()
    return ApplicationWrite(contract_version='2026-10-28',command_id='versioned-command',
        program_id=program.program_id,scope=a.scope,origin='APPLICATION_ACTION',operation='SUBMIT_PROGRAM',
        parent_command_id=None,target_venue_id=None,canonical_program_hash=VersionedOrderCodec().hash('order-program',program),
        ownership_epoch=a.ownership_epoch,execution_epoch=1,policy_revision=1,
        durable_record_ref='pending',state='DURABLE_READY',created_at=NOW,
        venue_client_id=a.parent_client_id,service_namespace=a.namespace,owner_strategy_id=a.owner_strategy_id)


class MemoryProgramTx:
    def __init__(self):
        self.scope=authority().scope
        self.failed=False
        self.rows={}
        self.records={}
        self.appended=[]

    def require_scope(self,scope):assert scope==self.scope

    def execute(self,sql,params=()):
        if 'FROM ledger.order_programs' in sql:
            payload=self.rows.get(params[1])
            return SimpleNamespace(fetchone=lambda:None if payload is None else (payload,))
        if 'FROM ledger.active_owners' in sql:
            a=authority()
            return SimpleNamespace(fetchone=lambda:(a.owner_strategy_id,a.ownership_epoch,1))
        raise AssertionError(sql)

    def _latest(self,table,key,cls):return self.records.get((table,key))

    def _append(self,table,key,record,sequence):
        assert sequence==0
        self.appended.append((table,key,record))
        self.records[(table,key)]=record
        if table=='order_programs':self.rows[key]=domain_payload(record)


@pytest.mark.parametrize('kind,duration',[('VP',None),('VP',120),('TWAP',300)])
def test_v3_exact_algorithm_storage_roundtrip(kind,duration):
    tx=MemoryProgramTx();repo=OrderProgramRepository(tx)
    program=program_record('2026-10-29',kind,duration)
    repo.save_program(program)
    restored=repo.program(program.program_id)
    assert type(restored) is type(program) and restored==program
    assert restored.algorithm.duration_seconds==duration
    assert restored.algorithm.urgency==('HIGH' if kind=='VP' else None)
    assert tx.rows[program.program_id]==program_payload('2026-10-29',kind,duration)


def test_v2_storage_and_hash_remain_identical_to_original_codec():
    tx=MemoryProgramTx();repo=OrderProgramRepository(tx);program=program_record()
    expected=OrderProgramCodec().encode(program)
    digest=OrderProgramCodec().hash(program)
    assert digest==canonical_hash(expected)
    repo.save_program(program)
    assert tx.rows[program.program_id]==expected
    assert VersionedOrderCodec().hash('order-program',repo.program(program.program_id))==digest
    command=command_for(program);repo.create_write(command)
    assert repo.write(command.command_id)==command


def test_v3_durable_write_hash_binds_full_v3_program_without_minting_permit():
    tx=MemoryProgramTx();repo=OrderProgramRepository(tx);program=program_record('2026-10-29','VP',None)
    repo.save_program(program);command=command_for(program);repo.create_write(command)
    assert repo.write(command.command_id)==command
    before=list(tx.appended)
    lease=SimpleNamespace(shard=SimpleNamespace(environment=tx.scope.environment,
        venue_account_id=tx.scope.venue_account_id,product=tx.scope.product,execution_epoch=1),
        expires_at='2026-10-29T11:00:00Z',deadline_monotonic_ns=10**30)
    with pytest.raises(MissingEvidence,match='ORDER_COMPILER_RISK_ADMISSION_NOT_IMPLEMENTED'):
        OrderProgramRepository(tx,clock=lambda:NOW).mark_may_have_sent(command.command_id,lease)
    assert tx.appended==before and repo.write(command.command_id).state=='DURABLE_READY'


def test_same_id_different_version_cannot_replace_existing_program():
    tx=MemoryProgramTx();repo=OrderProgramRepository(tx);old=program_record()
    repo.save_program(old);before=list(tx.appended)
    with pytest.raises(ValueError,match='identity reused'):
        repo.save_program(program_record('2026-10-29'))
    assert tx.appended==before and repo.program(old.program_id)==old


@pytest.mark.parametrize('version',['latest','2099-01-01',None])
def test_unknown_stored_version_is_not_inferred_or_upgraded(version):
    tx=MemoryProgramTx();raw=program_payload();raw['contract_version']=version
    tx.rows[raw['program_id']]=raw
    with pytest.raises(ValueError,match='Unknown contract version'):
        OrderProgramRepository(tx).program(raw['program_id'])
    assert tx.rows[raw['program_id']]==raw and not tx.appended


def test_v3_native_parent_cannot_inherit_incomplete_v2_lifecycle_authority():
    tx=MemoryProgramTx();repo=OrderProgramRepository(tx);program=program_record('2026-10-29','VP',None)
    repo.save_program(program)
    command=command_for(program);repo.create_write(command)
    # Historical import fixture only: no send, permit or simulated fill.
    tx.records[('order_writes_v2',command.command_id)]=replace(command,state='MAY_HAVE_SENT')
    repo.verify_parent=lambda *args:True
    repo.clock=lambda:NOW
    repo._funding=lambda *args:True
    parent=replace(authority(),program_id=program.program_id,parent_command_id=command.command_id,
        program_hash=VersionedOrderCodec().hash('order-program',program))
    before=list(tx.appended)
    with pytest.raises(MissingEvidence,match='PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED'):
        repo.bind_parent(parent)
    assert tx.appended==before


@pytest.mark.parametrize('kind',['child','parent'])
def test_imported_v3_binding_authenticates_and_archives_before_restricting(kind,monkeypatch):
    from trading_system.contracts.order_lifecycle import ProgramState,ParentObservation
    tx=MemoryProgramTx();repo=OrderProgramRepository(tx,verify_ingress=lambda *args:True,clock=lambda:NOW)
    program=program_record('2026-10-29','VP',None);repo.save_program(program)
    a=replace(authority(),program_id=program.program_id,
        program_hash=VersionedOrderCodec().hash('order-program',program))
    tx.records[('order_program_bindings',program.program_id)]=a
    tx.records[('order_program_states',program.program_id)]=ProgramState(program_id=program.program_id,
        sequence=1,status='ACTIVE',unallocated_quantity='10',children=(),observation_ids=(),restriction_codes=(),protection_quantity='0')
    e=event() if kind=='child' else ParentObservation(contract_version='2026-10-28',observation_id='parent-v3',
        scope=a.scope,venue=a.venue,namespace=a.namespace,parent_client_id=a.parent_client_id,
        parent_venue_id=a.parent_venue_id,status='ACTIVE',unallocated_quantity='10',cumulative_quantity='0',occurred_at=NOW)
    evidence=replace(a.evidence,hash=canonical_hash(domain_payload(e)))
    reasons=[]
    repo._restrict=lambda authority,state,reason,event,proof:reasons.append((reason,event,proof)) or reason
    def incompatible(*args):raise AssertionError('incompatible V2 reducer reached')
    monkeypatch.setattr('trading_system.storage.order_programs.apply_child_observation',incompatible)
    monkeypatch.setattr('trading_system.storage.order_programs.apply_parent_observation',incompatible)
    repo._funding=lambda *args:True
    ingest=repo.ingest if kind=='child' else repo.observe_parent
    assert ingest(program.program_id,e,evidence)=='PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED'
    assert reasons==[('PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED',e,evidence)]
    assert any((table=='order_source_observations' if kind=='child' else table=='audit_events') for table,_,_ in tx.appended)
    assert not any(table in ('order_child_links','order_trade_links','fills') for table,_,_ in tx.appended)


@pytest.mark.parametrize('kind',['child','parent'])
def test_imported_v3_binding_does_not_bypass_source_authentication(kind):
    from trading_system.contracts.order_lifecycle import ProgramState,ParentObservation
    tx=MemoryProgramTx();repo=OrderProgramRepository(tx,clock=lambda:NOW)
    program=program_record('2026-10-29','VP',None);repo.save_program(program)
    a=replace(authority(),program_id=program.program_id,
        program_hash=VersionedOrderCodec().hash('order-program',program))
    tx.records[('order_program_bindings',program.program_id)]=a
    tx.records[('order_program_states',program.program_id)]=ProgramState(program_id=program.program_id,
        sequence=1,status='ACTIVE',unallocated_quantity='10',children=(),observation_ids=(),restriction_codes=(),protection_quantity='0')
    e=event() if kind=='child' else ParentObservation(contract_version='2026-10-28',observation_id='unverified-v3',
        scope=a.scope,venue=a.venue,namespace=a.namespace,parent_client_id=a.parent_client_id,
        parent_venue_id=a.parent_venue_id,status='ACTIVE',unallocated_quantity='10',cumulative_quantity='0',occurred_at=NOW)
    evidence=replace(a.evidence,hash=canonical_hash(domain_payload(e)))
    repo._restrict=lambda authority,state,reason,*args:reason
    ingest=repo.ingest if kind=='child' else repo.observe_parent
    before=list(tx.appended)
    assert ingest(program.program_id,e,evidence)=='SOURCE_UNVERIFIED'
    assert tx.appended==before
