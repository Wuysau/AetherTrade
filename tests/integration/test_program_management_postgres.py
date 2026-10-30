"""Actual financial command -> current venue target -> management, isolated PG."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
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
    # Current authorization is resolved through the actual existing LOCAL reader.
    def inputs(tx,original,target,book,clock):
        current=c.load(tx,c.source,c.record,book,clock)
        return ManagementInputs(rules=c.bundle.archive.rules,profile=c.bundle.archive.profile,
            capabilities=replace(c.bundle.archive.capabilities,capability_ids=(*c.bundle.archive.capabilities.capability_ids,
                'SPOT-CANCEL_QUERY','SPOT-AMEND','USDM-CANCEL_QUERY','USDM-AMEND')),
            authorization=current.authorization,group_capacity=current.group_capacity,
            strategy_capacity=current.strategy_capacity,buffer=current.buffer)
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
