"""Fresh isolated PostgreSQL program financial attachments; synthetic PAPER only."""
from dataclasses import replace
from uuid import uuid4
import pytest
from integration.support_ip_03 import isolated_postgres,ROOT
from integration.test_program_financial_admission import real_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.execution.creation_requests import assign_client_ids,materialize_creation
from trading_system.execution.program_commands import ProgramCommandRepository,build_creation_write
from trading_system.storage.models import scope_id,encode
from trading_system.storage.application_writes import application_write_obligations

@pytest.fixture(scope='module')
def database(isolated_postgres):
    import psycopg
    with psycopg.connect(isolated_postgres) as db:
        for name in ('0006_proposal_intake_role.sql','0008_order_program_records.sql','0010_order_program_intake.sql',
            '0012_program_command_boundary.sql'):
            db.execute((ROOT/'migrations'/name).read_text(encoding='utf-8'))
    return isolated_postgres

def lease(c):
    return t.OwnerLease(shard=t.ExecutionShard(environment=c.scope.environment,venue_account_id=c.scope.venue_account_id,
        product=c.scope.product,node_id='isolated-financial-fixture-owner',execution_epoch=1),instance_id='fixture-instance',
        expires_at=c.c.terms.valid_until,deadline_monotonic_ns=10**18)

def repo(tx,c,**kwargs):
    return ProgramCommandRepository(tx,load_clock=lambda _:c.c.facts.inputs.clock,load_lease=lambda _:lease(c),**kwargs)

def attach(c,tx,*,fault=None):
    cid='isolated-command-'+uuid4().hex
    b=c.service._reserve_program(tx,c.key,(cid,),load_inputs=c.load,load_qualification=c.qualify,
        load_clock=lambda _:c.c.facts.inputs.clock)
    r=materialize_creation(b.archive.compilation,0,b.archive.profile,assign_client_ids(b.archive.compilation,0,cid))
    w,_=build_creation_write(r,b,execution_epoch=1)
    if fault=='request':r=replace(r,fields=tuple(replace(f,value='MARKET') if f.name=='type' else f for f in r.fields))
    if fault=='hash':w=replace(w,intent_hash='c'*64)
    if fault=='owner':w=replace(w,owner_strategy_id='other-owner')
    repository=repo(tx,c)
    if fault=='lease':repository.load_lease=lambda _:replace(lease(c),shard=replace(lease(c).shard,execution_epoch=2))
    stored=repository.append_creation(w,r,bundle=b)
    if fault=='rollback':raise RuntimeError('injected after actual append')
    return stored,r,b

def test_actual_financial_bundle_request_and_outbox_commit_then_restart(database):
    c=real_case(database)
    w,r,b=c.service._transaction(lambda tx:attach(c,tx))
    with c.owner.transaction(c.scope) as tx:
        repository=repo(tx,c)
        assert repository.write(w.command_id)==w and repository.request(w.command_id)==r
        assert repository.binding(w.command_id).bundle_hash
        obligations=application_write_obligations(tx)
        assert len(obligations)==1 and obligations[0].original==w
        assert tx.execute("SELECT kind FROM ledger.outbox WHERE aggregate_id=%s",(w.command_id,)).fetchone()==('PROGRAM_COMMAND_READY',)
        assert tx.execute('SELECT count(*) FROM ledger.commands WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.intents.get(w.intent_id)==b.intent

@pytest.mark.parametrize('fault',['request','hash','owner','lease','rollback'])
def test_attachment_failure_rolls_back_same_financial_bundle(database,fault):
    c=real_case(database)
    with pytest.raises((ValueError,RuntimeError)):c.service._transaction(lambda tx:attach(c,tx,fault=fault))
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
        assert tx.reservations.for_intent(c.record.intent_id)==()
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)

@pytest.mark.parametrize('target',['program_dispatch_marks','program_permit_consumptions'])
def test_ordinary_owner_cannot_forge_protected_send_or_consumption_rows(database,target):
    import psycopg
    c=real_case(database)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.owner.transaction(c.scope) as tx:tx.execute('INSERT INTO ledger.'+target+' DEFAULT VALUES')

def test_owner_cannot_raw_cross_may_or_execute_verifier_function(database):
    import psycopg
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx))
    with pytest.raises(psycopg.errors.CheckViolation):
        with c.owner.transaction(c.scope) as tx:tx._append('order_writes_v2',w.command_id,replace(w,state='MAY_HAVE_SENT'),1)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.owner.transaction(c.scope) as tx:
            tx.execute('SELECT ledger.mark_program_may(%s,%s,%s::jsonb,%s,1000,%s)',
                (scope_id(c.scope),w.command_id,encode({}), 'c'*64,tx.transaction_id))

def test_v2_function_remains_old_version_and_no_verifier_memberships(database):
    import psycopg
    with psycopg.connect(database) as db:
        body=db.execute("SELECT prosrc FROM pg_proc WHERE oid='ledger.check_order_write_v2()'::regprocedure").fetchone()[0]
        assert '2026-10-28' in body and '2026-10-29' not in body
        assert db.execute("SELECT count(*) FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_dispatch_verifier') OR member=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_dispatch_verifier')").fetchone()==(0,)
