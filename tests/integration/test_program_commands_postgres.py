"""Fresh isolated PostgreSQL program financial attachments; synthetic PAPER only."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
import pytest
from integration.support_ip_03 import isolated_postgres,ROOT
from integration.test_program_financial_admission import real_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.execution.creation_requests import assign_client_ids,materialize_creation
from trading_system.execution.program_commands import ProgramCommandRepository,build_creation_write
from trading_system.execution.program_commands import ProgramCommandBoundary
from trading_system.storage.uow import UnitOfWork
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
                (scope_id(c.scope),w.command_id,'{}', 'c'*64,tx.transaction_id))

def test_v2_function_remains_old_version_and_no_verifier_memberships(database):
    import psycopg
    with psycopg.connect(database) as db:
        body=db.execute("SELECT prosrc FROM pg_proc WHERE oid='ledger.check_order_write_v2()'::regprocedure").fetchone()[0]
        assert '2026-10-28' in body and '2026-10-29' not in body
        assert db.execute("SELECT count(*) FROM pg_auth_members WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_dispatch_verifier') OR member=(SELECT oid FROM pg_roles WHERE rolname='ledger_program_dispatch_verifier')").fetchone()==(0,)

def boundary(c):
    import psycopg
    def connect():
        db=psycopg.connect(c.dsn);db.execute('SET ROLE ledger_program_dispatch_verifier');db.commit();return db
    uow=UnitOfWork(connect,permit_ttl_ms=5000)
    return ProgramCommandBoundary(uow,c.scope,load_clock=lambda _:c.c.facts.inputs.clock,
        load_lease=lambda _:lease(c),load_current_facts=c.load,load_qualification=c.qualify)

def test_actual_may_commits_before_short_permit_consumes_once(database):
    import psycopg
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx))
    gate=boundary(c);permit=gate.mark_may_have_sent(w.command_id,lease(c))
    with c.owner.transaction(c.scope) as tx:
        assert repo(tx,c).write(w.command_id).state=='MAY_HAVE_SENT'
        assert application_write_obligations(tx)[0].unresolved
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)
    assert gate.consume(permit,lease(c))==r
    with pytest.raises((ValueError,psycopg.Error)):gate.consume(permit,lease(c))
    with pytest.raises(ValueError,match='NO_RETRY'):gate.mark_may_have_sent(w.command_id,lease(c))

@pytest.mark.parametrize('phase',['mark','consume'])
@pytest.mark.parametrize('fault',['missing_clock','bad_clock','missing_lease','lease_epoch','missing_facts',
    'missing_qualification','qual_artifact','capacity','revocation','risk_duty','request_hash','owner'])
def test_current_fact_failure_never_creates_or_consumes_a_permit(database,phase,fault):
    import psycopg
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    permit=gate.mark_may_have_sent(w.command_id,lease(c)) if phase=='consume' else None
    if fault=='missing_clock':gate.readers['load_clock']=None
    if fault=='bad_clock':gate.readers['load_clock']=lambda _:replace(c.c.facts.inputs.clock,clock_health='UNHEALTHY')
    if fault=='missing_lease':gate.readers['load_lease']=None
    if fault=='lease_epoch':gate.readers['load_lease']=lambda _:replace(lease(c),shard=replace(lease(c).shard,execution_epoch=2))
    if fault=='missing_facts':gate.readers['load_current_facts']=None
    if fault=='missing_qualification':gate.readers['load_qualification']=None
    if fault=='qual_artifact':gate.readers['load_qualification']=lambda *args:replace(c.qualify(*args),adapter_build_digest='f'*64)
    if fault=='capacity':
        def load(*args):return replace(c.load(*args),group_capacity=(t.Money(currency='USDT',amount=Decimal(0)),))
        gate.readers['load_current_facts']=load
    if fault=='revocation':
        from trading_system.intents.ingress import revision_key,IngressRevision
        with c.owner.transaction(c.scope) as tx:
            current=tx._latest('audit_events',revision_key(c.source.context),IngressRevision)
            tx._append('audit_events',revision_key(c.source.context),replace(current,sequence=current.sequence+1,enabled=False),current.sequence)
    if fault=='risk_duty':
        with c.owner.transaction(c.scope) as tx:tx.risk_locks.append(t.RiskLock(lock_id='isolated-current-lock',scope=c.scope,
            cause='ORDER_OUTCOME_UNKNOWN',state='ACTIVE',permission=t.RiskPermission.RECOVERY_REQUIRED,
            required_evidence=('ORIGINAL_IDS',),raised_at=c.c.facts.inputs.clock.utc_now),0)
    if fault=='request_hash':
        def load(*args):
            value=c.load(*args)
            return replace(value,archive=replace(value.archive,source=replace(value.archive.source,source_hash='d'*64)))
        gate.readers['load_current_facts']=load
    if fault=='owner':
        # Isolated migration administrator mutates test owner only; operational
        # roles remain denied by the original immutable owner trigger.
        with psycopg.connect(database) as db:
            db.execute('ALTER TABLE ledger.active_owners DISABLE TRIGGER owner_immutable')
            db.execute('UPDATE ledger.active_owners SET policy_revision=policy_revision+1 WHERE account_id=%s',(c.scope.venue_account_id,))
            db.execute('ALTER TABLE ledger.active_owners ENABLE TRIGGER owner_immutable')
    with pytest.raises((ValueError,LookupError,psycopg.Error)):
        gate.consume(permit,lease(c)) if phase=='consume' else gate.mark_may_have_sent(w.command_id,lease(c))
    with c.owner.transaction(c.scope) as tx:
        assert repo(tx,c).write(w.command_id).state==('MAY_HAVE_SENT' if phase=='consume' else 'DURABLE_READY')
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)

def test_unknown_keeps_original_request_ids_and_cannot_issue_or_consume(database):
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    permit=gate.mark_may_have_sent(w.command_id,lease(c))
    with c.owner.transaction(c.scope) as tx:assert repo(tx,c).record_unknown(w.command_id)==r
    with pytest.raises(ValueError):gate.mark_may_have_sent(w.command_id,lease(c))
    with pytest.raises(ValueError):gate.consume(permit,lease(c))
    with c.owner.transaction(c.scope) as tx:
        assert repo(tx,c).request(w.command_id)==r
        assert application_write_obligations(tx)[0].current.state=='UNKNOWN'

@pytest.mark.parametrize('after_commit',[False,True])
def test_uncertain_may_commit_returns_no_permit_and_never_retries(database,after_commit):
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    original=gate.uow.connection_factory
    class BrokenCommit:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def commit(self):
            if after_commit:self.db.commit()
            raise OSError('injected uncertain MAY commit')
    gate.uow=UnitOfWork(lambda:BrokenCommit(original()),permit_ttl_ms=5000)
    with pytest.raises(OSError):gate.mark_may_have_sent(w.command_id,lease(c))
    with c.owner.transaction(c.scope) as tx:
        assert repo(tx,c).write(w.command_id).state==('MAY_HAVE_SENT' if after_commit else 'DURABLE_READY')
        assert repo(tx,c).request(w.command_id)==r

@pytest.mark.parametrize('phase',['before','after'])
def test_process_exit_at_actual_may_commit_boundary_preserves_original_identity(database,phase):
    import os,sys
    if sys.platform!='linux':pytest.skip('real fork fault requires isolated Linux')
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    original=gate.uow.connection_factory
    class ExitAtCommit:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def commit(self):
            if phase=='after':self.db.commit()
            os._exit(74 if phase=='after' else 73)
    child=os.fork()
    if child==0:
        try:
            gate.uow=UnitOfWork(lambda:ExitAtCommit(original()),permit_ttl_ms=5000)
            gate.mark_may_have_sent(w.command_id,lease(c))
        except BaseException:os._exit(75)
        os._exit(76)
    _,status=os.waitpid(child,0)
    assert os.waitstatus_to_exitcode(status)==(74 if phase=='after' else 73)
    with c.owner.transaction(c.scope) as tx:
        assert repo(tx,c).write(w.command_id).state==('MAY_HAVE_SENT' if phase=='after' else 'DURABLE_READY')
        assert repo(tx,c).request(w.command_id)==r
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)

def test_host_lease_replacement_after_committed_may_cannot_consume(database):
    import psycopg
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    permit=gate.mark_may_have_sent(w.command_id,lease(c))
    replacement=replace(lease(c),instance_id='replacement-instance')
    gate.readers['load_lease']=lambda _:replacement
    with pytest.raises(ValueError):gate.consume(permit,lease(c))
    # Even passing the replacement expected lease cannot use another host's
    # single-use mark: SQL binds the original independently loaded mark lease.
    with pytest.raises(psycopg.errors.CheckViolation):gate.consume(permit,replacement)

def test_scoped_client_identity_collision_and_distinct_namespace_are_conservative(database):
    import psycopg
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx))
    cid=r.assigned_ids.members[0].client_id
    insert='INSERT INTO ledger.program_client_identities VALUES(%s,%s,%s,%s,%s,%s,%s)'
    values=(str(c.scope.environment),c.scope.venue_account_id,str(c.scope.product),w.service_namespace,cid,scope_id(c.scope),'other-command')
    with pytest.raises(psycopg.errors.UniqueViolation):
        with c.owner.transaction(c.scope) as tx:tx.execute(insert,values)
    with c.owner.transaction(c.scope) as tx:
        tx.execute(insert,(*values[:3],'BINANCE_SPOT_ORDER_LIST',*values[4:]))
        assert len(application_write_obligations(tx))==1

def test_same_transaction_consume_and_forged_token_are_denied(database):
    import psycopg
    from trading_system.execution.program_commands import _token_hash
    from trading_system.contracts.canonical import canonical_json
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    with pytest.raises(psycopg.errors.CheckViolation):
        with gate.uow.transaction(c.scope) as tx:
            repository=ProgramCommandRepository(tx,**gate.readers)
            repository.mark_may_have_sent(w.command_id,lease(c),token_hash=_token_hash('test-token'))
            marked=repository.write(w.command_id)
            context,*_=repository._current(marked,lease(c))
            tx.execute('SELECT ledger.consume_program_permit(%s,%s,%s,%s::jsonb,%s)',
                (scope_id(c.scope),w.command_id,_token_hash('test-token'),canonical_json(context),tx.transaction_id))
    permit=gate.mark_may_have_sent(w.command_id,lease(c))
    with pytest.raises(psycopg.errors.CheckViolation):gate.consume(replace(permit,token='forged-token'),lease(c))

def test_sql_deadline_expires_even_when_fixture_host_clocks_do_not_advance(database):
    import psycopg
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    gate.uow.permit_ttl_ms=1
    permit=gate.mark_may_have_sent(w.command_id,lease(c))
    with pytest.raises(psycopg.errors.CheckViolation):gate.consume(permit,lease(c))

@pytest.mark.parametrize('after_commit',[False,True])
def test_uncertain_consume_commit_returns_no_request_or_second_use(database,after_commit):
    import psycopg
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    permit=gate.mark_may_have_sent(w.command_id,lease(c));original=gate.uow.connection_factory
    class BrokenCommit:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def commit(self):
            if after_commit:self.db.commit()
            raise OSError('injected uncertain permit consumption commit')
    gate.uow=UnitOfWork(lambda:BrokenCommit(original()),permit_ttl_ms=5000)
    with pytest.raises(OSError):gate.consume(permit,lease(c))
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(1 if after_commit else 0,)
    if after_commit:
        gate.uow=UnitOfWork(original,permit_ttl_ms=5000)
        with pytest.raises((ValueError,psycopg.Error)):gate.consume(permit,lease(c))

def test_no_incomplete_request_set_can_cross_may_after_private_partial_attachment(database):
    c=real_case(database);cid='partial-'+uuid4().hex
    def operation(tx):
        b=c.service._reserve_program(tx,c.key,(cid,'missing-request-id'),load_inputs=c.load,
            load_qualification=c.qualify,load_clock=lambda _:c.c.facts.inputs.clock)
        r=materialize_creation(b.archive.compilation,0,b.archive.profile,assign_client_ids(b.archive.compilation,0,cid))
        w,_=build_creation_write(r,b,execution_epoch=1)
        return repo(tx,c).append_creation(w,r,bundle=b)
    w=c.service._transaction(operation)
    with pytest.raises(ValueError,match='COMPLETE_ATTACHMENT'):boundary(c).mark_may_have_sent(cid,lease(c))

def test_v3_unknown_cannot_regress_to_ready_or_local_terminal(database):
    import psycopg
    c=real_case(database);w,r,b=c.service._transaction(lambda tx:attach(c,tx));gate=boundary(c)
    gate.mark_may_have_sent(w.command_id,lease(c))
    with c.owner.transaction(c.scope) as tx:repo(tx,c).record_unknown(w.command_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        with c.owner.transaction(c.scope) as tx:tx._append('order_writes_v2',w.command_id,w,3)
    with c.owner.transaction(c.scope) as tx:
        assert application_write_obligations(tx)[0].current.state=='UNKNOWN'
