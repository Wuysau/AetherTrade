"""Actual financial command -> current venue target -> management, isolated PG."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from trading_system.contracts import types as t
from integration.test_paper_market_matcher_v2 import isolated_postgres,database,venue,venue_v2,advanced_case as _advanced_case
from integration.test_paper_market_matcher import submit,event

_VERIFIER_DSNS={}


@pytest.fixture(scope='module')
def management_database(database):
    import psycopg
    from integration.support_ip_03 import ROOT
    with psycopg.connect(database) as db:
        for name in ('0016_program_fill_source_approval.sql','0017_program_fill_source_snapshots.sql',
            '0018_program_fill_source_leaves.sql','0019_program_account_opening.sql','0020_program_legacy_opening.sql'):
            db.execute((ROOT/'migrations'/name).read_text(encoding='utf-8'))
        db.execute((ROOT/'migrations/0022_program_management_boundary.sql').read_text(encoding='utf-8'))
        db.execute((ROOT/'migrations/0027_effective_order_management.sql').read_text(encoding='utf-8'))
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
            'EXECUTE ON FUNCTION ledger.record_management_outcome(text,text,jsonb,text)',
            'EXECUTE ON FUNCTION ledger.abort_management_unsent(text,text,text)',
            'EXECUTE ON FUNCTION ledger.resolve_effective_management_v1(text,text,jsonb)'):
            db.execute(sql.SQL('GRANT '+grant+' TO {}').format(sql.Identifier(name)))
        db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(conninfo_to_dict(database)['dbname']),sql.Identifier(name)))
        _VERIFIER_DSNS[database]=make_conninfo(**{**conninfo_to_dict(database),'user':name,'password':password})
    return database

def advanced_case(*args,**kwargs):
    """Declare initial assets before actual initial financial intent/command."""
    from unittest.mock import patch
    import integration.test_paper_market_matcher as fixture_module
    from trading_system.execution.program_account_opening import ProgramAccountOpeningService,account_key
    from trading_system.execution.program_book_cash import bind_program_book_cash
    from trading_system.execution.program_legacy_opening import read_program_local_account_opening
    from trading_system.contracts.canonical import canonical_hash,domain_payload
    from trading_system.storage.uow import UnitOfWork
    import psycopg
    original=fixture_module.real_case
    def funded(*a,**k):
        # Current ROOT requires explicit capital before initial admission. This
        # wrapper supplies that proof below, so do not provision it twice.
        c=original(*a,**{**k,'provision_opening':False})
        # Explicit scenario declaration, never inferred from a current book.
        assets=(t.Money(currency='BTC',amount=D('10')),t.Money(currency='USDT',amount=D('1000000')))
        account=account_key(c.scope);at=c.c.facts.inputs.clock.utc_now
        evidence=t.EvidenceRef(id='management-declared-initial-assets:'+account.key,
            hash=canonical_hash(dict(account=domain_payload(account),assets=[domain_payload(m) for m in assets])),
            environment=c.scope.environment,source='EXPLICIT_ISOLATED_INITIAL_ASSET_PROVISION',visible_at=at)
        def connect():
            db=psycopg.connect(c.dsn);db.execute('SET ROLE ledger_program_account_provisioner');db.commit();return db
        c.management_opening=ProgramAccountOpeningService(UnitOfWork(connect),c.scope).initialize(assets,evidence=evidence)
        with c.service.uow.transaction(c.scope) as tx:
            frame=bind_program_book_cash(tx,tx.projections.book(c.scope),read_opening=read_program_local_account_opening)
            assert frame is not None and frame.opening_id==c.management_opening.opening_id
        return c
    with patch.object(fixture_module,'real_case',funded):return _advanced_case(*args,**kwargs)

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
        assert stored.opening_id==c.management_opening.opening_id and stored.cash_revision
        assert json.loads(stored.funding_snapshot_json)['opening_id']==stored.opening_id
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

@pytest.mark.parametrize('table',['management_bindings','management_writes','management_dispatch_marks','management_permit_consumptions','management_outcomes','management_unsent_aborts'])
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
    with c.engine._connect() as db:
        role=db.execute("SELECT r.rolname,r.rolsuper,r.rolcanlogin,r.rolcreatedb,r.rolcreaterole FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner WHERE p.oid='public.paper_manage_order_v4(text,text,jsonb)'::regprocedure").fetchone()
        assert tuple(role.values())==('paper_matcher_definer',False,False,False,False)

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
    import psycopg
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    with pytest.raises((ValueError,psycopg.Error)):s.abort_unsent(w.command_id,c.lease)
    s.boundary.consume(permit,c.lease)

def test_stale_book_cannot_be_refreshed_by_current_venue_target_alone(management_database,venue_v2,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    base=c.engine._clock();c.engine._clock=lambda:replace(base,
        utc_now=(utc_timestamp(base.utc_now)+timedelta(seconds=61)).isoformat(),monotonic_ns=base.monotonic_ns+61*10**9)
    c.lease=replace(c.lease,expires_at='2026-10-28T11:00:00Z');c.owner.lease=c.lease
    s=management(c);original=s.load_inputs
    def inputs(*args):
        i=original(*args)
        return replace(i,authorization=replace(i.authorization,authorization=replace(i.authorization.authorization,expires_at='2026-10-28T11:00:00Z')))
    s.load_inputs=inputs
    with pytest.raises(ValueError,match='BOOK_EVIDENCE_REQUIRED'):
        s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)

@pytest.mark.parametrize('phase',['admit','mark','consume'])
def test_reader_wait_advancing_current_clock_cannot_authorize_expired_write(management_database,venue_v2,tmp_path,phase):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c);s=management(c)
    w=None if phase=='admit' else s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease) if phase=='consume' else None
    read=s.load_inputs;base=c.engine._clock()
    def waited(*args):
        result=read(*args)
        c.engine._clock=lambda:replace(base,utc_now=(utc_timestamp(base.utc_now)+timedelta(seconds=31)).isoformat(),
            monotonic_ns=base.monotonic_ns+31*10**9)
        return result
    s.load_inputs=waited
    with pytest.raises(ValueError,match='WRITE_EXPIRED'):
        if phase=='admit':s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.CANCEL),c.lease)
        elif phase=='mark':s.boundary.mark_may_have_sent(w.command_id,c.lease)
        else:s.boundary.consume(permit,c.lease)


def _management_venue_admin(database,venue):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    return make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(venue.runtime_dsn)['dbname']})


def _hold_management_lock(blocker,c,write,lock_kind):
    market=blocker.execute('SELECT market_key FROM public.paper_order_terms WHERE scope_key=%s AND client_id=%s',
        (c.engine._scope_key,write.original_client_id)).fetchone()['market_key']
    if lock_kind=='advisory':blocker.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(market,))
    else:blocker.execute('SELECT client_id FROM public.paper_engine_orders WHERE scope_key=%s AND client_id=%s FOR UPDATE',
        (c.engine._scope_key,write.original_client_id))


def _await_actual_management_lock(monitor,future,lock_kind):
    import time
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        waiting=monitor.execute("SELECT EXISTS(SELECT FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND (query LIKE 'SELECT public.paper_manage_order_v4%' OR application_name LIKE 'aethertrade-management-v4%'))").fetchone()[0]
        if waiting:
            print('CONFIRMED actual management '+lock_kind+' lock wait',flush=True)
            return
        if future.done():
            future.result()
            raise AssertionError('dispatch finished before actual '+lock_kind+' wait')
        time.sleep(.02)
    raise AssertionError('actual management '+lock_kind+' lock wait was not observed')


def _await_original_authorization_expiry(monitor,c,write):
    import time
    remaining=monitor.execute('SELECT greatest(0,extract(epoch from expires_at-clock_timestamp())) FROM public.paper_management_authorizations_v4 WHERE scope_key=%s AND command_id=%s',
        (c.engine._scope_key,write.command_id)).fetchone()[0]
    time.sleep(float(remaining)+.15)
    assert monitor.execute('SELECT expires_at<clock_timestamp() FROM public.paper_management_authorizations_v4 WHERE scope_key=%s AND command_id=%s',
        (c.engine._scope_key,write.command_id)).fetchone()==(True,)
    print('CONFIRMED exact original private authorization expired in DB before unlock',flush=True)


def _assert_wait_failure_retains_original(c,service,write):
    from trading_system.execution.program_management_commands import ManagementRepository
    from trading_system.execution.program_management_outcomes import observe_paper_management
    from trading_system.risk.reservation_repository import ReservationRepository
    assert c.engine.query_creation(c.write.command_id).status=='SUBMITTED'
    with c.engine._connect() as db:
        assert db.execute('SELECT count(*) AS n FROM public.paper_management_actions_v4 WHERE scope_key=%s AND command_id=%s',
            (c.engine._scope_key,write.command_id)).fetchone()['n']==0
    with service.verifier_uow.transaction(c.scope) as tx:
        assert ManagementRepository(tx).read(write.command_id).write.state=='UNKNOWN'
        assert all(ReservationRepository(tx).get(p.reservation.reservation_id).held==p.held for p in c.bundle.reservations)
    observation=observe_paper_management(service,write.command_id)
    assert observation.cancel_result=='UNKNOWN' and observation.command_id==write.command_id and not observation.funds_released
    with pytest.raises(ValueError):service.boundary.mark_may_have_sent(write.command_id,c.lease)


@pytest.mark.parametrize('lock_kind',['advisory','row'])
def test_expired_permit_cannot_cancel_after_actual_venue_mutex_wait(
        management_database,venue_v2,management_venue,tmp_path,lock_kind):
    """Reviewed P1, extended to both actual lock waits; no artificial PG clock."""
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from trading_system.execution.program_management_paper import submit_management
    c,service,write=admitted(management_database,venue_v2,tmp_path)
    permit=service.boundary.mark_may_have_sent(write.command_id,c.lease)
    with c.engine._connect() as blocker:
        _hold_management_lock(blocker,c,write,lock_kind)
        def dispatch():
            with c.owner.session(c.lease.shard):
                return submit_management(c.engine,write,permit,boundary=service.boundary,
                    lease=c.lease,owner=c.owner,bridge=management_venue)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(dispatch)
            try:
                with psycopg.connect(_management_venue_admin(management_database,venue_v2),autocommit=True) as monitor:
                    _await_actual_management_lock(monitor,future,lock_kind)
                    _await_original_authorization_expiry(monitor,c,write)
            finally:blocker.commit()
            with pytest.raises((ValueError,psycopg.Error)):future.result(timeout=10)
    _assert_wait_failure_retains_original(c,service,write)


def _stage_actual_private_bridge(c,service,write,permit,bridge):
    """Exercise SQL's own checks with a genuinely consumed original authority."""
    import psycopg
    from trading_system.execution.program_management_commands import token_hash
    from trading_system.contracts.canonical import canonical_json,domain_payload
    from trading_system.storage.models import scope_id
    with c.owner.session(c.lease.shard):
        with c.owner.guard(c.lease):
            binding=service.boundary.consume(permit,c.lease)
            service.boundary.recheck_consumed(permit,c.lease)
            with service.verifier_uow.transaction(c.scope) as tx:
                expiry=tx.execute('SELECT expires_at FROM ledger.management_dispatch_marks WHERE scope_id=%s AND command_id=%s',
                    (scope_id(c.scope),write.command_id)).fetchone()[0]
            with psycopg.connect(bridge.bridge_dsn) as private:
                private.execute('INSERT INTO public.paper_management_authorizations_v4(scope_key,command_id,binding,token_hash,expires_at) VALUES(%s,%s,%s::jsonb,%s,%s)',
                    (c.engine._scope_key,write.command_id,canonical_json(domain_payload(binding)),token_hash(permit.token),expiry))
    return binding


@pytest.mark.parametrize('lock_kind',['advisory','row'])
def test_sql_reducer_independently_rechecks_exact_expiry_after_each_lock(
        management_database,venue_v2,management_venue,tmp_path,lock_kind):
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from trading_system.contracts.canonical import canonical_json,domain_payload
    c,service,write=admitted(management_database,venue_v2,tmp_path)
    permit=service.boundary.mark_may_have_sent(write.command_id,c.lease)
    binding=_stage_actual_private_bridge(c,service,write,permit,management_venue)
    with c.engine._connect() as blocker:
        _hold_management_lock(blocker,c,write,lock_kind)
        def dispatch():
            try:
                with c.owner.session(c.lease.shard):
                    with c.owner.guard(c.lease):
                        with c.engine._connect() as db:
                            db.execute("SELECT set_config('application_name','aethertrade-management-v4-direct',true)")
                            return db.execute('SELECT public.paper_manage_order_v4(%s,%s,%s::jsonb)',
                                (c.engine._scope_key,write.command_id,canonical_json(domain_payload(binding)))).fetchone()
            except Exception:
                service.boundary.record_unknown(write.command_id)
                raise
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(dispatch)
            try:
                with psycopg.connect(_management_venue_admin(management_database,venue_v2),autocommit=True) as monitor:
                    _await_actual_management_lock(monitor,future,lock_kind)
                    _await_original_authorization_expiry(monitor,c,write)
            finally:blocker.commit()
            with pytest.raises(psycopg.errors.CheckViolation,match='expired after'):future.result(timeout=10)
    _assert_wait_failure_retains_original(c,service,write)


def _revoke_actual_deployment(c):
    from trading_system.intents.ingress import IngressRevision,revision_key
    with c.service.uow.transaction(c.scope) as tx:
        key=revision_key(c.source.context)
        current=tx._latest('audit_events',key,IngressRevision)
        tx._append('audit_events',key,replace(current,sequence=current.sequence+1,enabled=False),current.sequence)


@pytest.mark.parametrize('lock_kind',['advisory','row'])
@pytest.mark.parametrize('change',['deployment','owner_epoch','lease_epoch'])
def test_actual_authority_change_during_venue_wait_is_reloaded_after_lock(
        management_database,venue_v2,management_venue,tmp_path,lock_kind,change):
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from trading_system.execution.program_management_paper import submit_management
    c,service,write=admitted(management_database,venue_v2,tmp_path)
    permit=service.boundary.mark_may_have_sent(write.command_id,c.lease)
    with c.engine._connect() as blocker:
        _hold_management_lock(blocker,c,write,lock_kind)
        def dispatch():
            with c.owner.session(c.lease.shard):
                return submit_management(c.engine,write,permit,boundary=service.boundary,
                    lease=c.lease,owner=c.owner,bridge=management_venue)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(dispatch)
            try:
                with psycopg.connect(_management_venue_admin(management_database,venue_v2),autocommit=True) as monitor:
                    _await_actual_management_lock(monitor,future,lock_kind)
                    if change=='deployment':_revoke_actual_deployment(c)
                    elif change=='owner_epoch':
                        with psycopg.connect(management_database) as admin:
                            admin.execute('ALTER TABLE ledger.active_owners DISABLE TRIGGER owner_immutable')
                            admin.execute('UPDATE ledger.active_owners SET ownership_epoch=ownership_epoch+1 WHERE account_id=%s',(c.scope.venue_account_id,))
                            admin.execute('ALTER TABLE ledger.active_owners ENABLE TRIGGER owner_immutable')
                    else:service.load_lease=lambda _:replace(c.lease,shard=replace(c.lease.shard,execution_epoch=2))
                    assert monitor.execute('SELECT expires_at>clock_timestamp() FROM public.paper_management_authorizations_v4 WHERE scope_key=%s AND command_id=%s',
                        (c.engine._scope_key,write.command_id)).fetchone()==(True,),'authority test must unlock before expiry'
            finally:blocker.commit()
            expected={'deployment':'DEPLOYMENT_FENCE_REQUIRED','owner_epoch':'STALE_OWNER','lease_epoch':'CURRENT_LEASE_REQUIRED'}[change]
            with pytest.raises(ValueError,match=expected):future.result(timeout=10)
    _assert_wait_failure_retains_original(c,service,write)


@pytest.mark.parametrize('change',['deployment','expiry'])
def test_final_guard_rolls_back_actual_venue_mutation_before_commit(
        management_database,venue_v2,management_venue,tmp_path,change):
    import psycopg
    from trading_system.execution.program_management_paper import submit_management
    c,service,write=admitted(management_database,venue_v2,tmp_path)
    permit=service.boundary.mark_may_have_sent(write.command_id,c.lease)
    original=c.engine._connect;executed=[]
    class DelayedResult:
        def __init__(self):self.db=original()
        def __getattr__(self,name):return getattr(self.db,name)
        def __enter__(self):self.db.__enter__();return self
        def __exit__(self,*args):return self.db.__exit__(*args)
        def execute(self,sql,*args,**kwargs):
            cursor=self.db.execute(sql,*args,**kwargs)
            if sql.startswith('SELECT public.paper_manage_order_v4('):
                executed.append(True)
                if change=='deployment':_revoke_actual_deployment(c)
                else:
                    with psycopg.connect(_management_venue_admin(management_database,venue_v2),autocommit=True) as monitor:
                        _await_original_authorization_expiry(monitor,c,write)
            return cursor
    c.engine._connect=DelayedResult
    try:
        with c.owner.session(c.lease.shard):
            with pytest.raises(ValueError):
                submit_management(c.engine,write,permit,boundary=service.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    finally:c.engine._connect=original
    assert executed==[True],'the real venue mutation must precede the late guard failure'
    _assert_wait_failure_retains_original(c,service,write)


def test_keep_priority_actual_funded_quantity_reduction(management_database,venue_v2,management_venue,tmp_path):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.execution.program_management_paper import submit_management
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    s=management(c)
    w=s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity='0.5'),c.lease)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        result=submit_management(c.engine,w,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    assert result['effective_quantity']=='0.5'
    report=c.engine.query_creation(c.write.command_id)
    assert report.orders[0].quantity==D('0.5')


def test_keep_priority_partial_source_fifo_and_repeated_reduction(management_database,venue_v2,management_venue,tmp_path):
    from uuid import uuid4
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.execution.program_management_paper import submit_management
    from trading_system.execution.program_effective_orders import PaperManagedCreationReportV1,observe_effective_management,SOURCE
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.risk.reservation_repository import ReservationRepository
    market='effective-'+uuid4().hex
    first=advanced_case(management_database,venue_v2,tmp_path,market=market);submit(first)
    second=advanced_case(management_database,venue_v2,tmp_path,market=market);submit(second)
    matcher=PaperMatcher(first.engine);matcher.apply_market_event(event(first,quantity='.2'))
    s=management(first)
    initial=first.engine.query_creation(first.write.command_id)
    def amend(q):
        w=s.admit(first.write.command_id,ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity=q),first.lease)
        permit=s.boundary.mark_may_have_sent(w.command_id,first.lease)
        with first.owner.session(first.lease.shard):
            submit_management(first.engine,w,permit,boundary=s.boundary,lease=first.lease,owner=first.owner,bridge=management_venue)
        return w
    w=amend('0.5')
    report=first.engine.query_creation(first.write.command_id)
    assert type(report) is PaperManagedCreationReportV1 and report.evidence.source==SOURCE
    assert report.accepted_contexts==initial.accepted_contexts
    assert report.effective_history[0].original_quantity==D('1')
    assert report.orders[0].quantity==D('.5') and report.filled_quantity==D('.2') and report.remaining_quantity==D('.3')
    assert report.orders[0].runtime.queue_priority==initial.orders[0].runtime.queue_priority
    with pytest.raises(Exception,match='existing management duty'):amend('0.4')
    observe_effective_management(s,w.command_id)
    next_write=amend('0.4');observe_effective_management(s,next_write.command_id)
    assert len(first.engine.query_creation(first.write.command_id).effective_history)==2
    matcher.apply_market_event(event(first,sequence=2,quantity='.6'))
    final=first.engine.query_creation(first.write.command_id)
    assert final.status=='FILLED' and final.filled_quantity==D('.4')
    assert second.engine.query_creation(second.write.command_id).filled_quantity==D('.2')
    with first.service.uow.transaction(first.scope) as tx:
        assert ReservationRepository(tx).get(first.bundle.reservations[0].reservation.reservation_id).held==first.bundle.reservations[0].held


def test_effective_source_tables_are_not_runtime_writable(management_database,venue_v2,management_venue,tmp_path):
    import psycopg
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.engine._connect() as db:db.execute('INSERT INTO public.paper_effective_orders_v1 DEFAULT VALUES')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.owner.transaction(c.scope) as tx:tx.execute('INSERT INTO ledger.management_effective_resolutions_v1 DEFAULT VALUES')
