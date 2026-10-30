"""Actual financial command -> current venue target -> management, isolated PG."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from integration.test_paper_market_matcher_v2 import isolated_postgres,database,venue,venue_v2,advanced_case
from integration.test_paper_market_matcher import submit,event


@pytest.fixture(scope='module')
def management_database(database):
    import psycopg
    from integration.support_ip_03 import ROOT
    with psycopg.connect(database) as db:
        db.execute((ROOT/'migrations/0022_program_management_boundary.sql').read_text(encoding='utf-8'))
    return database


def management(c):
    from trading_system.execution.program_management_admission import ProgramManagementService
    from trading_system.execution.program_management_paper import PaperManagementTargetReader
    from trading_system.accounts.authorization import AuthorizationBinding
    from trading_system.storage.uow import UnitOfWork
    import psycopg
    # Current authorization is resolved through the actual existing LOCAL reader.
    def authorization(tx,source,clock):
        return c.load(tx,source,c.record,tx.projections.book(c.scope),clock).authorization
    def verifier():
        db=psycopg.connect(c.dsn);db.execute('SET ROLE ledger_management_dispatch_verifier');db.commit();return db
    return ProgramManagementService(c.service,UnitOfWork(verifier,permit_ttl_ms=5000),
        target_reader=PaperManagementTargetReader(c.engine),load_clock=lambda _:c.engine._clock(),
        load_lease=lambda _:c.lease,load_authorization=authorization)


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
