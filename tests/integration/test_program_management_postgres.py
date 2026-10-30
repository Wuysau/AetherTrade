"""Actual financial command -> current venue target -> management, isolated PG."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from trading_system.contracts import types as t
from integration.test_paper_market_matcher_v2 import isolated_postgres,database,venue,venue_v2,advanced_case
from integration.test_paper_market_matcher import submit,event

_VERIFIER_DSNS={}


@pytest.fixture(scope='module')
def management_database(database):
    import psycopg
    from integration.support_ip_03 import ROOT
    with psycopg.connect(database) as db:
        db.execute((ROOT/'migrations/0022_program_management_boundary.sql').read_text(encoding='utf-8'))
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict,make_conninfo
        import secrets
        from uuid import uuid4
        name='management_verifier_'+uuid4().hex[:12];password=secrets.token_urlsafe(32)
        db.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS').format(sql.Identifier(name),sql.Literal(password)))
        for grant in ('USAGE ON SCHEMA ledger','SELECT ON ALL TABLES IN SCHEMA ledger',
            'UPDATE(group_id) ON ledger.collateral_mutex','UPDATE(collateral_group_id) ON ledger.account_bindings',
            'UPDATE(ownership_epoch) ON ledger.active_owners','INSERT ON ledger.transaction_observations',
            'EXECUTE ON FUNCTION ledger.admit_management(text,text,jsonb,jsonb,text)',
            'EXECUTE ON FUNCTION ledger.mark_management_may(text,text,jsonb,text,integer,text)',
            'EXECUTE ON FUNCTION ledger.consume_management_permit(text,text,text,jsonb,text)',
            'EXECUTE ON FUNCTION ledger.mark_management_unknown(text,text,text)',
            'EXECUTE ON FUNCTION ledger.record_management_outcome(text,text,jsonb,text)'):
            db.execute(sql.SQL('GRANT '+grant+' TO {}').format(sql.Identifier(name)))
        db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(conninfo_to_dict(database)['dbname']),sql.Identifier(name)))
        _VERIFIER_DSNS[database]=make_conninfo(**{**conninfo_to_dict(database),'user':name,'password':password})
    return database

@pytest.fixture(scope='module')
def management_venue(management_database,venue_v2):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from trading_system.execution.program_management_paper import provision_paper_management
    admin=make_conninfo(**{**conninfo_to_dict(management_database),'dbname':conninfo_to_dict(venue_v2.runtime_dsn)['dbname']})
    return provision_paper_management(admin,venue_v2)


def management(c):
    from trading_system.execution.program_management_admission import ProgramManagementService,ManagementInputs
    from trading_system.execution.program_management_paper import PaperManagementTargetReader
    from trading_system.accounts.authorization import AuthorizationBinding
    from trading_system.storage.uow import UnitOfWork
    import psycopg
    # Independent LOCAL management grant. Replaying a creation assessment here
    # would incorrectly reintroduce its old proposal/intent submission TTL.
    ctx=c.source.context
    grant=t.Authorization(authorization_id=ctx.authorization_id,owner_strategy_id=c.bundle.intent.owner_strategy_id,
        environment=c.scope.environment,deployment_id=ctx.deployment_id,scopes=(c.scope,),
        maximum_capital=t.Money(currency='USDT',amount=D('1000000')),policy_revision=ctx.policy_revision,
        protection_options=(c.c.terms.protection_policy,),model_cost_budget=t.Money(currency='USDT',amount=D(0)),
        valid_from=c.bundle.intent.created_at,expires_at=c.c.terms.valid_until,revoked_at=None)
    authorization=AuthorizationBinding(authorization=grant,identity_id=ctx.identity_id,strategy_revision=ctx.strategy_revision,
        ownership_epoch=ctx.ownership_epoch,queue_environment=ctx.environment,capabilities=(),evidence=c.bundle.archive.book.evidence)
    def inputs(tx,original,target,book,clock):
        return ManagementInputs(rules=c.bundle.archive.rules,profile=c.bundle.archive.profile,
            capabilities=replace(c.bundle.archive.capabilities,capability_ids=(*c.bundle.archive.capabilities.capability_ids,
                'SPOT-CANCEL_QUERY','SPOT-AMEND','USDM-CANCEL_QUERY','USDM-AMEND')),
            authorization=authorization,group_capacity=(t.Money(currency='USDT',amount=D('1000000')),),
            strategy_capacity=(t.Money(currency='USDT',amount=D('1000000')),),buffer=(t.Money(currency='USDT',amount=D(0)),))
    def verifier():
        return psycopg.connect(_VERIFIER_DSNS[c.dsn])
    return ProgramManagementService(c.service,UnitOfWork(verifier,permit_ttl_ms=5000),
        target_reader=PaperManagementTargetReader(c.engine),load_clock=lambda _:c.engine._clock(),
        load_lease=lambda _:c.lease,load_inputs=inputs)


def test_actual_management_commits_full_plan_and_real_outbox(management_database,venue_v2,tmp_path):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    service=management(c)
    write=service.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    with c.service.uow.transaction(c.scope) as tx:
        from trading_system.execution.program_management_commands import ManagementRepository
        stored=ManagementRepository(tx).read(write.command_id)
        assert stored.write==write and stored.plan.target.client_id==c.request.assigned_ids.members[0].client_id
        assert stored.write.plan_hash and stored.write.target_hash and stored.write.funding_hash
        from hashlib import sha256
        import json
        assert sha256(stored.target_source_json.encode('utf-8')).hexdigest()==stored.target_source_hash
        assert json.loads(stored.target_source_json)['evidence']['hash']==stored.plan.target.evidence.hash
        assert tx.execute('SELECT kind FROM ledger.outbox WHERE aggregate_id=%s',(write.command_id,)).fetchone()==('PROGRAM_MANAGEMENT_READY',)


def test_queries_never_create_management_commands(management_database,venue_v2,tmp_path):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    with pytest.raises(ValueError,match='QUERY_READ_ONLY'):
        management(c).admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.QUERY),c.lease)

def admitted(database,venue,tmp_path,*,partial=False):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.engine.paper_market import PaperMatcher
    c=advanced_case(database,venue,tmp_path);submit(c)
    if partial:PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    service=management(c)
    w=service.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    return c,service,w

def test_actual_cancel_partial_keeps_market_fills_and_entire_business_hold(management_database,venue_v2,management_venue,tmp_path):
    from trading_system.execution.program_management_paper import submit_management
    from trading_system.execution.program_management_outcomes import observe_paper_management
    from trading_system.risk.reservation_repository import ReservationRepository
    c,s,w=admitted(management_database,venue_v2,tmp_path,partial=True)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        result=submit_management(c.engine,w,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    assert result['cancel_result']=='CANCELED' and not result['funds_released']
    report=c.engine.query_creation(c.write.command_id)
    assert report.status=='CANCELED' and report.filled_quantity==D('.4') and len(report.fills)==1
    observed=observe_paper_management(s,w.command_id)
    assert observed.cancel_result=='CANCELED' and observed.replacement_result=='NOT_ATTEMPTED'
    assert not observed.children_terminal and not observed.funds_released
    with c.service.uow.transaction(c.scope) as tx:
        assert ReservationRepository(tx).get(c.bundle.reservations[0].reservation.reservation_id).held==c.bundle.reservations[0].held

def test_creation_permit_cannot_authorize_any_management(management_database,venue_v2,tmp_path):
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    with pytest.raises(ValueError,match='MANAGEMENT_PRIVATE_PERMIT_REQUIRED'):s.boundary.consume(c.permit,c.lease)

def test_concurrent_consumption_has_one_winner(management_database,venue_v2,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    c,s,w=admitted(management_database,venue_v2,tmp_path);permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    def consume(_):
        try:s.boundary.consume(permit,c.lease);return True
        except Exception:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(consume,range(2)))==[False,True]

@pytest.mark.parametrize('phase',['admit','mark','consume','after_wait'])
def test_current_authorization_revocation_denies_every_write_phase(management_database,venue_v2,tmp_path,phase):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c);s=management(c)
    w=None if phase=='admit' else s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease) if phase in ('consume','after_wait') else None
    if phase=='after_wait':s.boundary.consume(permit,c.lease)
    current=s.load_inputs
    def revoked(*args):
        i=current(*args)
        return replace(i,authorization=replace(i.authorization,authorization=replace(i.authorization.authorization,revoked_at=c.engine._clock().utc_now)))
    s.load_inputs=revoked
    with pytest.raises(ValueError,match='AUTHORIZATION'):
        if phase=='admit':s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
        elif phase=='mark':s.boundary.mark_may_have_sent(w.command_id,c.lease)
        elif phase=='consume':s.boundary.consume(permit,c.lease)
        else:s.boundary.recheck_consumed(permit,c.lease)
    assert c.engine.query_creation(c.write.command_id).status=='SUBMITTED'

@pytest.mark.parametrize('phase',['mark','consume','after_wait'])
def test_actual_target_fill_race_stops_stale_cancel(management_database,venue_v2,tmp_path,phase):
    from trading_system.engine.paper_market import PaperMatcher
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease) if phase!='mark' else None
    if phase=='after_wait':s.boundary.consume(permit,c.lease)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    with pytest.raises(ValueError,match='TARGET_CHANGED'):
        if phase=='mark':s.boundary.mark_may_have_sent(w.command_id,c.lease)
        elif phase=='consume':s.boundary.consume(permit,c.lease)
        else:s.boundary.recheck_consumed(permit,c.lease)
    assert c.engine.query_creation(c.write.command_id).filled_quantity==D('.4')

def test_missing_local_inputs_and_target_reader_deny(management_database,venue_v2,tmp_path):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c);s=management(c)
    s.load_inputs=None
    with pytest.raises(ValueError,match='INPUTS_REQUIRED'):s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    s.target_reader=None
    with pytest.raises(ValueError,match='TARGET_READER_REQUIRED'):s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)

@pytest.mark.parametrize('table',['management_bindings','management_writes','management_dispatch_marks','management_permit_consumptions','management_outcomes'])
def test_ordinary_owner_cannot_forge_private_management_records(management_database,table):
    import psycopg
    from integration.test_program_financial_admission import real_case
    c=real_case(management_database)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.owner.transaction(c.scope) as tx:tx.execute('INSERT INTO ledger.'+table+' DEFAULT VALUES')

def test_raw_paper_runtime_cannot_forge_consumed_bridge_authorization(management_database,venue_v2,management_venue,tmp_path):
    import psycopg
    from trading_system.contracts.canonical import canonical_json,domain_payload
    from trading_system.execution.program_management_commands import ManagementRepository
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    with s.verifier_uow.transaction(c.scope) as tx:b=ManagementRepository(tx).read(w.command_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        with c.engine._connect() as db:db.execute('SELECT public.paper_manage_order_v4(%s,%s,%s::jsonb)',
            (c.engine._scope_key,w.command_id,canonical_json(domain_payload(b))))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.engine._connect() as db:db.execute('INSERT INTO public.paper_management_authorizations_v4 DEFAULT VALUES')

def test_lost_cancel_ack_recovers_original_id_without_retry_or_release(management_database,venue_v2,management_venue,tmp_path):
    from trading_system.execution.program_management_paper import submit_management
    from trading_system.execution.program_management_outcomes import observe_paper_management
    from trading_system.execution.program_management_commands import ManagementRepository
    c,s,w=admitted(management_database,venue_v2,tmp_path);permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    original=c.engine._connect
    class LostAck:
        def __init__(self):self.db=original();self.wrote=False
        def __getattr__(self,name):return getattr(self.db,name)
        def __enter__(self):self.db.__enter__();return self
        def execute(self,sql,*args,**kwargs):
            self.wrote=self.wrote or 'SELECT public.paper_manage_order_v4' in sql
            return self.db.execute(sql,*args,**kwargs)
        def __exit__(self,*args):
            result=self.db.__exit__(*args)
            if self.wrote and args[0] is None:raise OSError('lost ACK after actual SIM commit')
            return result
    c.engine._connect=LostAck
    with c.owner.session(c.lease.shard):
        with pytest.raises(OSError,match='lost ACK'):
            submit_management(c.engine,w,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    c.engine._connect=original
    with s.verifier_uow.transaction(c.scope) as tx:assert ManagementRepository(tx).read(w.command_id).write.state=='UNKNOWN'
    result=observe_paper_management(s,w.command_id)
    assert result.cancel_result=='CANCELED' and result.command_id==w.command_id and not result.funds_released
    with pytest.raises(ValueError):s.boundary.mark_may_have_sent(w.command_id,c.lease)
    assert c.engine.query_creation(c.write.command_id).status=='CANCELED'

def test_expiry_after_consumption_cannot_be_renewed_after_wait(management_database,venue_v2,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    c,s,w=admitted(management_database,venue_v2,tmp_path);permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    s.boundary.consume(permit,c.lease)
    previous=c.engine._clock()
    c.engine._clock=lambda:replace(previous,utc_now=(utc_timestamp(previous.utc_now)+timedelta(seconds=6)).isoformat(),
        monotonic_ns=previous.monotonic_ns+6*10**9)
    with pytest.raises(ValueError,match='PERMIT_EXPIRED'):s.boundary.recheck_consumed(permit,c.lease)
    assert c.engine.query_creation(c.write.command_id).status=='SUBMITTED'

def test_actual_login_is_restricted_and_has_no_role_memberships(management_database):
    import psycopg
    with psycopg.connect(_VERIFIER_DSNS[management_database]) as db:
        name=db.execute('SELECT current_user').fetchone()[0]
        assert db.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s',(name,)).fetchone()==(False,)*6
        assert db.execute('SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s)',(name,)).fetchone()==(0,)
        assert db.execute("SELECT has_schema_privilege(current_user,'ledger','CREATE')").fetchone()==(False,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):db.execute('INSERT INTO ledger.management_dispatch_marks DEFAULT VALUES')

def test_existing_gtc_management_outlives_creation_ttl_with_fresh_current_grant(management_database,venue_v2,management_venue,tmp_path):
    from unittest.mock import patch
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.execution.program_management_paper import submit_management
    import integration.test_paper_market_matcher as fixture_module
    original_case=fixture_module.real_case
    short='2026-10-28T10:00:30Z'
    def short_case(*args,proposal_transform=None,**kwargs):
        def transform(p):
            p=proposal_transform(p) if proposal_transform else p
            return replace(p,valid_until=short,program=replace(p.program,submit_valid_until=short))
        return original_case(*args,proposal_transform=transform,**kwargs)
    with patch.object(fixture_module,'real_case',short_case):c=advanced_case(management_database,venue_v2,tmp_path)
    submit(c)
    base=c.engine._clock();later=replace(base,utc_now='2026-10-28T10:00:31Z',monotonic_ns=base.monotonic_ns+31*10**9)
    assert utc_timestamp(c.write.valid_until)<utc_timestamp(later.utc_now)
    c.engine._clock=lambda:later
    c.lease=replace(c.lease,expires_at='2026-10-28T10:01:00Z');c.owner.lease=c.lease
    s=management(c);read=s.load_inputs
    def current_grant(*args):
        inputs=read(*args)
        return replace(inputs,authorization=replace(inputs.authorization,authorization=replace(
            inputs.authorization.authorization,expires_at='2026-10-28T10:01:00Z')))
    s.load_inputs=current_grant
    w=s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        result=submit_management(c.engine,w,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    assert result['cancel_result']=='CANCELED' and c.engine.query_creation(c.write.command_id).filled_quantity==0

@pytest.mark.parametrize('phase',['mark','consume','after_wait'])
def test_current_owner_policy_change_denies_each_boundary(management_database,venue_v2,tmp_path,phase):
    import psycopg
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease) if phase!='mark' else None
    if phase=='after_wait':s.boundary.consume(permit,c.lease)
    with psycopg.connect(management_database) as db:
        db.execute('ALTER TABLE ledger.active_owners DISABLE TRIGGER owner_immutable')
        db.execute('UPDATE ledger.active_owners SET policy_revision=policy_revision+1 WHERE account_id=%s',(c.scope.venue_account_id,))
        db.execute('ALTER TABLE ledger.active_owners ENABLE TRIGGER owner_immutable')
    with pytest.raises(ValueError,match='STALE_OWNER'):
        if phase=='mark':s.boundary.mark_may_have_sent(w.command_id,c.lease)
        elif phase=='consume':s.boundary.consume(permit,c.lease)
        else:s.boundary.recheck_consumed(permit,c.lease)

def test_protected_binding_and_marks_are_immutable_even_to_verifier(management_database,venue_v2,tmp_path):
    import psycopg
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    s.boundary.mark_may_have_sent(w.command_id,c.lease)
    for table in ('management_bindings','management_writes','management_dispatch_marks'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with s.verifier_uow.transaction(c.scope) as tx:tx.execute('DELETE FROM ledger.'+table)

def test_foreign_actual_venue_target_is_never_claimed(management_database,venue_v2,tmp_path):
    from trading_system.execution.program_management_paper import PaperManagementTargetReader
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    other=advanced_case(management_database,venue_v2,tmp_path);submit(other)
    s=management(c);s.target_reader=PaperManagementTargetReader(other.engine)
    with pytest.raises(ValueError,match='ORIGINAL_SCOPE_REQUIRED'):
        s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)

def test_fresh_execution_owner_epoch_manages_old_original_without_creation_rewrite(management_database,venue_v2,management_venue,tmp_path):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.execution.program_management_paper import submit_management
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c);old=c.write
    c.lease=replace(c.lease,shard=replace(c.lease.shard,execution_epoch=2));c.owner.lease=c.lease
    s=management(c)
    w=s.admit(old.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    assert old.execution_epoch==1 and w.execution_epoch==2
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        result=submit_management(c.engine,w,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    assert result['cancel_result']=='CANCELED' and c.write==old

def test_neutral_management_duty_is_visible_across_instrument_scope(management_database,venue_v2,tmp_path):
    from trading_system.execution.program_management_obligations import management_obligations
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    with s.verifier_uow.transaction(replace(c.scope,instrument_uid='other-isolated-instrument')) as tx:
        obligations=management_obligations(tx)
        assert len(obligations)==1 and obligations[0].original==w and obligations[0].unresolved
        assert management_obligations(tx,scope_only=True)==()

def test_protective_reduction_cancel_requires_independent_action_proof(management_database,venue_v2,tmp_path):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    c=advanced_case(management_database,venue_v2,tmp_path,actual='1',target='0',quantity='1',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);s=management(c)
    assert c.bundle.archive.allocation.delta.classification=='REDUCE'
    with pytest.raises(ValueError,match='PROTECTION_ACTION_PROOF_REQUIRED'):
        s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    assert c.engine.query_creation(c.write.command_id).status=='SUBMITTED'

def test_direct_sql_consume_cannot_use_uncommitted_management_may(management_database,venue_v2,tmp_path):
    import psycopg
    from trading_system.execution.program_management_commands import ManagementRepository,token_hash
    from trading_system.contracts.canonical import canonical_json
    from trading_system.storage.models import scope_id
    c,s,w=admitted(management_database,venue_v2,tmp_path);target=s.capture_for(w.command_id)
    with pytest.raises(psycopg.errors.CheckViolation,match='committed unused'):
        with s.verifier_uow.transaction(c.scope) as tx:
            b=ManagementRepository(tx).read(w.command_id)
            ctx,clock,_=s.current(tx,b,target,c.lease)
            s.boundary.tx_mark(tx,b,ctx,'private-test-token',5000)
            marked=ManagementRepository(tx).read(w.command_id)
            ctx,_,_=s.current(tx,marked,target,c.lease)
            tx.execute('SELECT ledger.consume_management_permit(%s,%s,%s,%s::jsonb,%s)',
                (scope_id(c.scope),w.command_id,token_hash('private-test-token'),canonical_json(ctx),tx.transaction_id))
    with s.verifier_uow.transaction(c.scope) as tx:assert ManagementRepository(tx).read(w.command_id).write.state=='DURABLE_READY'

def test_stale_unsent_plan_can_abort_then_fresh_cancel_without_releasing_hold(management_database,venue_v2,management_venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.execution.program_management_paper import submit_management
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    with pytest.raises(ValueError,match='TARGET_CHANGED'):s.boundary.mark_may_have_sent(w.command_id,c.lease)
    receipt=s.abort_unsent(w.command_id,c.lease)
    assert receipt.command_id==w.command_id and receipt.state=='ABORTED_UNSENT' and not receipt.funds_released
    fresh=s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    assert fresh.command_id!=w.command_id and fresh.target_hash!=w.target_hash
    permit=s.boundary.mark_may_have_sent(fresh.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        result=submit_management(c.engine,fresh,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    assert result['cancel_result']=='CANCELED' and c.engine.query_creation(c.write.command_id).filled_quantity==D('.4')

def test_committed_may_can_never_be_unsent_aborted(management_database,venue_v2,tmp_path):
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    with pytest.raises(Exception):s.abort_unsent(w.command_id,c.lease)
    s.boundary.consume(permit,c.lease)
