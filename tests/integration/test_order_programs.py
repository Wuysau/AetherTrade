"""Real isolated PostgreSQL OP-B migration/observation probes, not venue qualification."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from integration.support_ip_03 import isolated_postgres,LedgerScenario,Counts,ROOT
from contracts.test_order_lifecycle import authority,event,NOW
from trading_system.contracts.order_program import OrderProgramCodec
from trading_system.contracts.order_lifecycle import ApplicationWrite,ChildFill,AssetBudget,ProgramState
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.storage.order_programs import OrderProgramRepository
from trading_system.storage.models import scope_id,encode,MissingEvidence

@pytest.fixture(scope='module')
def pre_upgrade(isolated_postgres):
 s=LedgerScenario(isolated_postgres)
 with s.uow.transaction(s.scope) as tx:
  s.write_bundle(tx)
  tx.commands.mark_may_have_sent(tx,s.command.command_id,'DURABLE_READY')
 with s.connect_admin() as c:
  s.original_rows=c.execute('SELECT scope_id,record_id,sequence,payload,transaction_id FROM ledger.commands ORDER BY scope_id,record_id,sequence').fetchall()
 return s

@pytest.fixture(scope='module')
def order_db(pre_upgrade):
 with pre_upgrade.connect_admin() as c:c.execute((ROOT/'migrations/0008_order_program_records.sql').read_text(encoding='utf-8'))
 return pre_upgrade.dsn

def test_ddl_upgrade_retains_existing_uncertain_command_bytes(order_db,pre_upgrade):
 s=pre_upgrade
 with s.connect_admin() as c:
  after=c.execute('SELECT scope_id,record_id,sequence,payload,transaction_id FROM ledger.commands WHERE scope_id=%s ORDER BY scope_id,record_id,sequence',(scope_id(s.scope),)).fetchall()
 assert after==[r for r in s.original_rows if r[0]==scope_id(s.scope)]
 with s.uow.transaction(s.scope) as tx:
  current=tx.commands.get(s.command.command_id)
  assert current.state=='MAY_HAVE_SENT' and current.venue_client_order_id==s.command.venue_client_order_id
  assert current.payload_hash==s.command.payload_hash


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
  cmd=ApplicationWrite(venue_client_id=s.authority.parent_client_id,service_namespace=s.authority.namespace,owner_strategy_id=s.intent.owner_strategy_id,contract_version='2026-10-28',command_id=s.authority.parent_command_id,program_id=s.program.program_id,scope=s.scope,origin='APPLICATION_ACTION',operation='SUBMIT_PROGRAM',parent_command_id=None,target_venue_id=None,canonical_program_hash=s.authority.program_hash,ownership_epoch=s.intent.ownership_epoch,execution_epoch=1,policy_revision=s.intent.risk_policy_revision,durable_record_ref='pending',state='DURABLE_READY',created_at=NOW)
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
 with pytest.raises(RuntimeError,match='rollback-only'), s.uow.transaction(s.scope) as tx:
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
 import psycopg
 with pytest.raises((psycopg.errors.InsufficientPrivilege,psycopg.errors.CheckViolation)):
  with s.connect_owner() as c:
   sql={'UPDATE':"UPDATE ledger.order_programs SET payload='{}'::jsonb",'DELETE':'DELETE FROM ledger.order_programs','TRUNCATE':'TRUNCATE ledger.order_programs'}[operation]
   c.execute(sql)


def test_missing_origin_is_rejected_by_database(scenario):
 s=scenario
 import psycopg
 with pytest.raises((psycopg.errors.InsufficientPrivilege,psycopg.errors.CheckViolation)):
  with s.connect_owner() as c:
   c.execute("INSERT INTO ledger.order_writes_v2(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,'bad-origin',1,%s::jsonb,'test')",(scope_id(s.scope),json.dumps(dict(contract_version='2026-10-28',state='DURABLE_READY'))))


def test_linked_parent_reservation_cannot_be_released_by_ordinary_order_path(scenario):
 from trading_system.risk.reservation_repository import ReservationRepository
 s=scenario
 with pytest.raises(MissingEvidence,match='PROGRAM_RESERVATION_REQUIRES_RECONCILIATION'):
  with s.uow.transaction(s.scope) as tx:
   r=ReservationRepository(tx);p=r.get(s.reservation.reservation_id)
   r.save(replace(p,held=Decimal(0),sequence=p.sequence+1))


def test_parent_unknown_is_archived_and_children_are_quarantined(scenario):
 from trading_system.contracts.order_lifecycle import ParentObservation
 s=scenario;a=s.authority
 e=ParentObservation(contract_version='2026-10-28',observation_id='parent-unknown',scope=s.scope,venue=a.venue,namespace=a.namespace,parent_client_id=a.parent_client_id,parent_venue_id=a.parent_venue_id,status='CANCEL_UNKNOWN',unallocated_quantity='10',cumulative_quantity='0',occurred_at=NOW)
 with s.uow.transaction(s.scope) as tx:assert repo(tx).observe_parent(s.program.program_id,e,proof(e)).restriction=='PARENT_STATE_UNKNOWN'
 child=observation(s)
 with s.uow.transaction(s.scope) as tx:
  result=repo(tx).ingest(s.program.program_id,child,proof(child))
  assert result.restriction=='PARENT_STATE_UNKNOWN' and not result.state.children


def test_new_incident_has_new_risk_lock_identity(scenario):
 s=scenario
 for n in range(2):
  e=observation(s,observation_id='incident'+str(n),parent_venue_id='wrong')
  with s.uow.transaction(s.scope) as tx:repo(tx).ingest(s.program.program_id,e,proof(e))
 with s.uow.transaction(s.scope) as tx:
  assert len(tx.risk_locks.applicable(s.scope))==2


def test_default_parent_verifier_never_accepts_metadata_alone(scenario):
 s=scenario
 with pytest.raises(MissingEvidence,match='Trusted parent'):
  with s.uow.transaction(s.scope) as tx:OrderProgramRepository(tx,clock=lambda:NOW).bind_parent(s.authority)


def test_unknown_parent_retains_original_ids_without_assuming_ownership(scenario):
 s=scenario;e=observation(s,parent_venue_id='unrecognized-native-parent')
 with s.uow.transaction(s.scope) as tx:
  r=repo(tx);result=r.ingest('unrecognized-program',e,proof(e))
  assert result.state is None and result.restriction=='PARENT_BINDING_MISSING'
 with s.uow.transaction(s.scope) as tx:
  assert repo(tx).ingest('unrecognized-program',e,proof(e)).duplicate
  row=tx.execute('SELECT payload FROM ledger.order_reconciliation_cases WHERE scope_id=%s',(scope_id(s.scope),)).fetchone()
  assert row[0]['original_parent_id']=='unrecognized-native-parent'
  assert row[0]['observation']['child_order_id']==e.child_order_id
  assert tx.risk_locks.applicable(s.scope)


def test_parent_duplicate_keeps_reconciliation_outcome(scenario):
 from trading_system.contracts.order_lifecycle import ParentObservation
 s=scenario;a=s.authority
 e=ParentObservation(contract_version='2026-10-28',observation_id='same-parent',scope=s.scope,venue=a.venue,namespace=a.namespace,parent_client_id=a.parent_client_id,parent_venue_id=a.parent_venue_id,status='CANCEL_UNKNOWN',unallocated_quantity='10',cumulative_quantity='0',occurred_at=NOW)
 with s.uow.transaction(s.scope) as tx:repo(tx).observe_parent(s.program.program_id,e,proof(e))
 with s.uow.transaction(s.scope) as tx:
  result=repo(tx).observe_parent(s.program.program_id,e,proof(e))
  assert result.duplicate and result.restriction=='PARENT_STATE_UNKNOWN'


def test_source_archived_under_wrong_parent_can_be_applied_to_correct_parent(scenario):
 s=scenario
 other_program=replace(s.program,program_id='second-program')
 other_intent=replace(s.intent,intent_id='second-intent')
 other_reservation=replace(s.reservation,reservation_id='second-reservation',intent_id=other_intent.intent_id)
 other=replace(s.authority,program_id=other_program.program_id,program_hash=OrderProgramCodec().hash(other_program),parent_command_id='second-command',parent_client_id='second-client',parent_venue_id='second-native',funding_intent_id=other_intent.intent_id,reservation_ids=(other_reservation.reservation_id,))
 with s.uow.transaction(s.scope) as tx:
  tx.intents.append(other_intent);tx.reservations.append(other_reservation,0)
  r=repo(tx);r.save_program(other_program)
  original=r.write(s.authority.parent_command_id)
  command=replace(original,command_id=other.parent_command_id,program_id=other.program_id,canonical_program_hash=other.program_hash,venue_client_id=other.parent_client_id,state='DURABLE_READY',durable_record_ref='pending')
  r.create_write(command)
  tx._append('order_writes_v2',command.command_id,replace(command,state='MAY_HAVE_SENT',durable_record_ref='fixture-parent2'),1)
  r.bind_parent(other)
 e=observation(s)
 with s.uow.transaction(s.scope) as tx:assert repo(tx).ingest(other.program_id,e,proof(e)).restriction=='PARENT_LINK_UNVERIFIED'
 with s.uow.transaction(s.scope) as tx:
  result=repo(tx).ingest(s.program.program_id,e,proof(e))
  assert result.restriction is None and not result.duplicate and len(result.state.children)==1


def test_owner_role_cannot_run_schema_migrations(scenario):
 import psycopg
 with pytest.raises(psycopg.errors.InsufficientPrivilege):
  with scenario.connect_owner() as c:c.execute('CREATE TABLE ledger.runtime_ddl_forbidden(value int)')


def versioned_case(order_db,*,kind='VP',duration=None):
    from unit.test_versioned_order_program_repository import program_record,command_for
    s=LedgerScenario(order_db)
    program=program_record('2026-10-29',kind,duration)
    command=replace(command_for(program),scope=s.scope,owner_strategy_id=s.intent.owner_strategy_id,
        ownership_epoch=s.intent.ownership_epoch,policy_revision=s.intent.risk_policy_revision)
    with s.uow.transaction(s.scope) as tx:
        s.write_bundle(tx)
        r=repo(tx);r.save_program(program);r.create_write(command)
    return s,program,command


@pytest.mark.parametrize('kind,duration',[('VP',None),('VP',120),('TWAP',300)])
def test_v3_algorithm_roundtrip_across_transactions_preserves_complete_payload(order_db,kind,duration):
    from trading_system.contracts.order_codec import VersionedOrderCodec
    s,program,command=versioned_case(order_db,kind=kind,duration=duration)
    codec=VersionedOrderCodec()
    with s.connect_admin() as conn:
        old=conn.execute('SELECT record_id,sequence,payload,transaction_id FROM ledger.commands WHERE scope_id=%s',
                         (scope_id(s.scope),)).fetchall()
        stored=conn.execute('SELECT payload FROM ledger.order_programs WHERE scope_id=%s',
                            (scope_id(s.scope),)).fetchone()[0]
    assert stored==codec.encode('order-program',program)
    with s.uow.transaction(s.scope) as tx:
        r=repo(tx);restored=r.program(program.program_id)
        assert type(restored) is type(program) and restored==program
        assert restored.algorithm.duration_seconds==duration
        assert restored.algorithm.urgency==('HIGH' if kind=='VP' else None)
        assert r.write(command.command_id)==command
        assert r.write(command.command_id).canonical_program_hash==codec.hash('order-program',program)
        assert r.save_program(restored)==restored
    with s.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.order_programs WHERE scope_id=%s',
                             (scope_id(s.scope),)).fetchone()[0]==1
        assert conn.execute('SELECT record_id,sequence,payload,transaction_id FROM ledger.commands WHERE scope_id=%s',
                            (scope_id(s.scope),)).fetchall()==old


def test_program_identity_cannot_change_version_after_commit(order_db):
    from unit.test_versioned_order_program_repository import program_record
    s=LedgerScenario(order_db);old=program_record()
    with s.uow.transaction(s.scope) as tx:repo(tx).save_program(old)
    with pytest.raises(ValueError,match='identity reused'),s.uow.transaction(s.scope) as tx:
        repo(tx).save_program(program_record('2026-10-29'))
    with s.uow.transaction(s.scope) as tx:
        assert repo(tx).program(old.program_id)==old
    with s.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.order_programs WHERE scope_id=%s',
                             (scope_id(s.scope),)).fetchone()[0]==1


def test_v3_command_cannot_hide_changed_algorithm_urgency_in_hash(order_db):
    from trading_system.contracts.order_codec import VersionedOrderCodec
    s,program,command=versioned_case(order_db)
    changed=replace(program,algorithm=replace(program.algorithm,urgency='LOW'))
    wrong=replace(command,command_id='wrong-versioned-hash',
        canonical_program_hash=VersionedOrderCodec().hash('order-program',changed))
    with pytest.raises(MissingEvidence,match='Program hash binding required'),s.uow.transaction(s.scope) as tx:
        repo(tx).create_write(wrong)
    with s.uow.transaction(s.scope) as tx:
        assert repo(tx).write(wrong.command_id) is None
        assert repo(tx).write(command.command_id)==command


def test_v3_native_parent_lifecycle_denied_without_rewriting_original_command(order_db):
    from trading_system.contracts.order_codec import VersionedOrderCodec
    s,program,command=versioned_case(order_db)
    a=replace(authority(),scope=s.scope,program_id=program.program_id,
        program_hash=VersionedOrderCodec().hash('order-program',program),parent_command_id=command.command_id,
        owner_strategy_id=s.intent.owner_strategy_id,ownership_epoch=s.intent.ownership_epoch,
        authorization_id=s.intent.authorization_id,funding_intent_id=s.intent.intent_id,
        reservation_ids=(s.reservation.reservation_id,))
    # Explicit historical boundary fixture only; no transport, permit or fill.
    uncertain=replace(command,state='MAY_HAVE_SENT',durable_record_ref='imported-original-v3-parent')
    with s.uow.transaction(s.scope) as tx:
        tx._append('order_writes_v2',command.command_id,uncertain,1)
    with pytest.raises(MissingEvidence,match='PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED'),s.uow.transaction(s.scope) as tx:
        repo(tx).bind_parent(a)
    with s.uow.transaction(s.scope) as tx:
        r=repo(tx)
        assert r.binding(program.program_id) is None and r.state(program.program_id) is None
        assert r.write(command.command_id)==uncertain
        assert r.program(program.program_id)==program
    assert s.counts()==Counts(1,1,1)


def test_v3_durable_storage_does_not_grant_send_or_resend_permit(order_db):
    from types import SimpleNamespace
    s,program,command=versioned_case(order_db)
    lease=SimpleNamespace(shard=SimpleNamespace(environment=s.scope.environment,
        venue_account_id=s.scope.venue_account_id,product=s.scope.product,execution_epoch=1),
        expires_at='2026-10-29T11:00:00Z',deadline_monotonic_ns=10**30)
    with pytest.raises(MissingEvidence,match='ORDER_COMPILER_RISK_ADMISSION_NOT_IMPLEMENTED'),s.uow.transaction(s.scope) as tx:
        repo(tx).mark_may_have_sent(command.command_id,lease)
    with s.uow.transaction(s.scope) as tx:
        assert repo(tx).write(command.command_id)==command
        tx._append('order_writes_v2',command.command_id,replace(command,state='MAY_HAVE_SENT'),1)
    with pytest.raises(MissingEvidence,match='UNKNOWN commands require original-ID reconciliation'),s.uow.transaction(s.scope) as tx:
        repo(tx).mark_may_have_sent(command.command_id,lease)
    with s.uow.transaction(s.scope) as tx:
        restored=repo(tx).write(command.command_id)
        assert restored.venue_client_id==command.venue_client_id and restored.state=='MAY_HAVE_SENT'
        assert restored.canonical_program_hash==command.canonical_program_hash


def test_unknown_program_version_is_preserved_but_not_decoded_as_v2(order_db):
    from unit.test_versioned_order_program_repository import program_record
    s=LedgerScenario(order_db)
    raw=replace(program_record(),contract_version='2099-01-01')
    # Unknown historical import sentinel: a row is never upgraded by reading it.
    with s.uow.transaction(s.scope) as tx:
        tx._append('order_programs',raw.program_id,raw,0)
    with s.connect_admin() as conn:
        before=conn.execute('SELECT payload,transaction_id FROM ledger.order_programs WHERE scope_id=%s',
                             (scope_id(s.scope),)).fetchall()
    with pytest.raises(ValueError,match='Unknown contract version'),s.uow.transaction(s.scope) as tx:
        repo(tx).program(raw.program_id)
    with s.connect_admin() as conn:
        assert conn.execute('SELECT payload,transaction_id FROM ledger.order_programs WHERE scope_id=%s',
                             (scope_id(s.scope),)).fetchall()==before
        assert conn.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',
                             (scope_id(s.scope),)).fetchone()[0]==0


@pytest.mark.parametrize('kind',['child','parent'])
@pytest.mark.parametrize('authenticated',[True,False])
def test_imported_v3_binding_preserves_reports_under_reconciliation_without_child_admission(order_db,kind,authenticated):
    from trading_system.contracts.order_codec import VersionedOrderCodec
    from trading_system.contracts.order_lifecycle import ParentObservation
    s,program,command=versioned_case(order_db)
    a=replace(authority(),scope=s.scope,program_id=program.program_id,
        program_hash=VersionedOrderCodec().hash('order-program',program),parent_command_id=command.command_id,
        owner_strategy_id=s.intent.owner_strategy_id,ownership_epoch=s.intent.ownership_epoch,
        authorization_id=s.intent.authorization_id,funding_intent_id=s.intent.intent_id,
        reservation_ids=(s.reservation.reservation_id,))
    original_state=ProgramState(program_id=program.program_id,sequence=1,status='ACTIVE',
        unallocated_quantity='10',children=(),observation_ids=(),restriction_codes=(),protection_quantity='0')
    uncertain=replace(command,state='MAY_HAVE_SENT',durable_record_ref='original-imported-v3-parent')
    # Pre-upgrade imported metadata fixture: no API call, simulated fill or new
    # permission. New bind_parent explicitly rejects this authority/program pair.
    with s.uow.transaction(s.scope) as tx:
        tx._append('order_writes_v2',command.command_id,uncertain,1)
        tx._append('order_program_bindings',program.program_id,a,0)
        tx._append('order_program_states',program.program_id,original_state,0)
    e=observation(s) if kind=='child' else ParentObservation(contract_version='2026-10-28',
        observation_id='original-parent-report',scope=s.scope,venue=a.venue,namespace=a.namespace,
        parent_client_id=a.parent_client_id,parent_venue_id=a.parent_venue_id,status='ACTIVE',
        unallocated_quantity='10',cumulative_quantity='0',occurred_at=NOW)
    evidence=proof(e)
    reason='PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED' if authenticated else 'SOURCE_UNVERIFIED'
    with s.uow.transaction(s.scope) as tx:
        r=OrderProgramRepository(tx,verify_ingress=lambda *args:authenticated,clock=lambda:NOW)
        operation=r.ingest if kind=='child' else r.observe_parent
        result=operation(program.program_id,e,evidence)
        assert result.restriction==reason and result.state.status=='RECONCILIATION_REQUIRED'
        assert result.state.children==() and result.state.unallocated_quantity=='10'
    with s.uow.transaction(s.scope) as tx:
        r=repo(tx)
        assert r.binding(program.program_id)==a and r.write(command.command_id)==uncertain
        assert tx.risk_locks.applicable(s.scope)
        case=tx.execute('SELECT payload FROM ledger.order_reconciliation_cases WHERE scope_id=%s',
                        (scope_id(s.scope),)).fetchone()[0]
        assert case['reason']==reason and case['original_parent_id']==a.parent_venue_id
        assert case['observation']==domain_payload(e) and case['evidence']==domain_payload(evidence)
        for table in ('order_child_links','order_trade_links','fills'):
            assert tx.execute('SELECT count(*) FROM ledger.'+table+' WHERE scope_id=%s',
                                (scope_id(s.scope),)).fetchone()[0]==0
        if kind=='child':
            assert tx.execute('SELECT count(*) FROM ledger.order_source_observations WHERE scope_id=%s',
                                (scope_id(s.scope),)).fetchone()[0]==int(authenticated)
        else:
            key='parent-observation:'+canonical_hash(dict(program=program.program_id,event=e.observation_id))
            assert (tx._latest('audit_events',key,ParentObservation) is not None)==authenticated
    assert s.counts()==Counts(1,1,1)
