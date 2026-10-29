"""Real isolated PostgreSQL OP-B migration/observation probes, not venue qualification."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from integration.support_ip_03 import isolated_postgres,LedgerScenario,ROOT
from contracts.test_order_lifecycle import authority,event,NOW
from trading_system.contracts.order_program import OrderProgramCodec
from trading_system.contracts.order_lifecycle import ApplicationWrite,ChildFill,AssetBudget,ProgramState
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.storage.order_programs import OrderProgramRepository
from trading_system.storage.models import scope_id,encode,MissingEvidence

@pytest.fixture(scope='module')
def order_db(isolated_postgres):
 import psycopg
 with psycopg.connect(isolated_postgres) as c:c.execute((ROOT/'migrations/0008_order_program_records.sql').read_text(encoding='utf-8'))
 return isolated_postgres

def repo(tx,**kwargs):return OrderProgramRepository(tx,verify_parent=lambda a:True,verify_ingress=lambda o,e:True,clock=lambda:NOW,**kwargs)

@pytest.fixture
def scenario(order_db):
 s=LedgerScenario(order_db)
 with s.uow.transaction(s.scope) as tx:s.write_bundle(tx)
 program=json.loads((ROOT/'tests/fixtures/orders-v2/program.paper.json').read_text())
 program.update(template='TWAP',execution_mode='VENUE_NATIVE',algorithm=dict(kind='TWAP',duration_seconds=60,participation_bps=None,urgency=None))
 program['nodes'][0]['role']='ALGORITHM_PARENT'
 s.program=OrderProgramCodec().decode(program)
 s.authority=replace(authority(),scope=s.scope,program_id=s.program.program_id,program_hash=OrderProgramCodec().hash(s.program),parent_command_id='program-parent',owner_strategy_id=s.intent.owner_strategy_id,ownership_epoch=s.intent.ownership_epoch,authorization_id=s.intent.authorization_id,funding_intent_id=s.intent.intent_id,reservation_ids=(s.reservation.reservation_id,))
 with s.uow.transaction(s.scope) as tx:
  r=repo(tx);r.save_program(s.program)
  cmd=ApplicationWrite(owner_strategy_id=s.intent.owner_strategy_id,contract_version='2026-10-28',command_id=s.authority.parent_command_id,program_id=s.program.program_id,scope=s.scope,origin='APPLICATION_ACTION',operation='SUBMIT_PROGRAM',parent_command_id=None,target_venue_id=None,canonical_program_hash=s.authority.program_hash,ownership_epoch=s.intent.ownership_epoch,execution_epoch=1,policy_revision=s.intent.risk_policy_revision,durable_record_ref='pending',state='DURABLE_READY',created_at=NOW)
  r.create_write(cmd)
  # Explicit migration fixture for a pre-existing durable parent; no platform
  # send or fake fill. OP-B issuing a permit remains disabled pending OP-C.
  tx._append('order_writes_v2',cmd.command_id,replace(cmd,state='MAY_HAVE_SENT',durable_record_ref='fixture-committed-parent'),1)
  r.bind_parent(s.authority)
 return s

def observation(s,**kw):return event(scope=s.scope,**kw)
def proof(e):return t.EvidenceRef(id='proof:'+e.observation_id,hash=canonical_hash(domain_payload(e)),environment=e.scope.environment,source='PAPER_MODEL_EVENT',visible_at=NOW)

def test_migration_preserves_old_command_hash_and_unknown_recovery(scenario):
 s=scenario
 with s.uow.transaction(s.scope) as tx:
  old=tx.commands.get(s.command.command_id)
  assert old==s.command and old.payload_hash==s.command.payload_hash
  p=tx.commands.mark_may_have_sent(tx,old.command_id,'DURABLE_READY')
 with s.uow.transaction(s.scope) as tx:
  restored=tx.commands.get(s.command.command_id)
  assert restored.state=='MAY_HAVE_SENT' and restored.venue_client_order_id==old.venue_client_order_id
  assert restored.payload_hash==old.payload_hash
  with pytest.raises(Exception):tx.commands.mark_may_have_sent(tx,old.command_id,'DURABLE_READY')


def test_observation_restart_duplicate_preserves_budget_and_does_not_send(scenario):
 s=scenario;e=observation(s)
 before=s.counts()
 with s.uow.transaction(s.scope) as tx:assert repo(tx).ingest(s.program.program_id,e,proof(e)).restriction is None
 with s.uow.transaction(s.scope) as tx:
  r=repo(tx);result=r.ingest(s.program.program_id,e,proof(e))
  assert result.duplicate and len(result.state.children)==1
  assert result.state.unallocated_quantity=='8'
  assert tx.reservations.for_intent(s.intent.intent_id)==(s.reservation,)
 assert s.counts()==before


def test_conflicting_duplicate_is_archived_and_risk_restricted(scenario):
 s=scenario;e=observation(s)
 with s.uow.transaction(s.scope) as tx:repo(tx).ingest(s.program.program_id,e,proof(e))
 changed=replace(e,quantity='3')
 with s.uow.transaction(s.scope) as tx:
  result=repo(tx).ingest(s.program.program_id,changed,proof(changed));assert result.restriction=='EVENT_CONTENT_CONFLICT'
 with s.uow.transaction(s.scope) as tx:
  assert repo(tx).state(s.program.program_id).status=='RECONCILIATION_REQUIRED'
  assert tx.risk_locks.applicable(s.scope)
  row=tx.execute('SELECT payload FROM ledger.order_reconciliation_cases WHERE scope_id=%s',(scope_id(s.scope),)).fetchone()
  assert row[0]['observation']['child_order_id']==e.child_order_id


def test_unverified_source_keeps_case_without_claiming_child(scenario):
 s=scenario;e=observation(s)
 with s.uow.transaction(s.scope) as tx:
  r=OrderProgramRepository(tx,clock=lambda:NOW)
  result=r.ingest(s.program.program_id,e,proof(e));assert result.restriction=='SOURCE_UNVERIFIED'
  assert result.state.children==()


def test_concurrent_repeated_observation_applies_once(scenario):
 s=scenario;e=observation(s)
 def ingest(_):
  with s.uow.transaction(s.scope) as tx:return repo(tx).ingest(s.program.program_id,e,proof(e)).duplicate
 with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(ingest,[0,1]))==[False,True]

@pytest.mark.parametrize('field',['reflected','release_pending'])
def test_funding_subsets_are_not_extra_money(scenario,field):
 s=scenario
 with s.uow.transaction(s.scope) as tx:
  p=t.ReservationProjection(reservation=s.reservation,held=Decimal('100'),reflected=Decimal('100') if field=='reflected' else Decimal(0),release_pending=Decimal('100') if field=='release_pending' else Decimal(0),sequence=1)
  tx._append('audit_events','ip07:reservation:'+s.reservation.reservation_id,p,0)
  assert not repo(tx)._funding(replace(s.authority,funding_requirements=(AssetBudget(asset='USDT',amount='150'),)))

@pytest.mark.parametrize('operation',['UPDATE','DELETE','TRUNCATE'])
def test_runtime_cannot_rewrite_or_erase_order_history(scenario,operation):
 s=scenario
 with pytest.raises(Exception):
  with s.connect_owner() as c:
   sql={'UPDATE':"UPDATE ledger.order_programs SET payload='{}'::jsonb",'DELETE':'DELETE FROM ledger.order_programs','TRUNCATE':'TRUNCATE ledger.order_programs'}[operation]
   c.execute(sql)


def test_missing_origin_is_rejected_by_database(scenario):
 s=scenario
 with pytest.raises(Exception):
  with s.connect_owner() as c:
   c.execute("INSERT INTO ledger.order_writes_v2(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,'bad-origin',1,%s::jsonb,'test')",(scope_id(s.scope),json.dumps(dict(contract_version='2026-10-28',state='DURABLE_READY'))))
