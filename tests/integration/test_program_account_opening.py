"""Explicit PAPER initial assets, not inferred account balances or new deposits."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from integration.test_program_fill_source_approval import isolated_postgres,command_database,database as source_database
from integration.test_program_financial_admission import real_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.storage.uow import UnitOfWork
from trading_system.storage.models import scope_id


@pytest.fixture(scope='module')
def database(source_database):
    import psycopg
    path=Path(__file__).resolve().parents[2]/'migrations/0019_program_account_opening.sql'
    if path.exists():
        with psycopg.connect(source_database) as db:db.execute(path.read_text(encoding='utf-8'))
    return source_database


def provision_uow(database):
    import psycopg
    def connect():
        db=psycopg.connect(database);db.execute('SET ROLE ledger_program_account_provisioner');db.commit();return db
    return UnitOfWork(connect)


def inputs(c):
    balances=(t.Money(amount=D('1000000'),currency='USDT'),t.Money(amount=D('10'),currency='BTC'))
    evidence=t.EvidenceRef(id='explicit-opening:'+c.scope.venue_account_id,
        hash=canonical_hash(dict(USDT='1000000',BTC='10')),environment=t.Environment.PAPER,
        source='EXPLICIT_ISOLATED_PAPER_INITIAL_ASSETS',visible_at=c.c.facts.inputs.clock.utc_now)
    return balances,evidence


def service(database,c):
    from trading_system.execution.program_account_opening import ProgramAccountOpeningService
    return ProgramAccountOpeningService(provision_uow(database),c.scope)


def test_opening_role_is_not_enrolled_and_runtime_cannot_write(database):
    import psycopg
    with psycopg.connect(database) as db:
        assert db.execute("SELECT to_regclass('ledger.program_account_openings')").fetchone()[0] is not None
        assert db.execute("SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname='ledger_program_account_provisioner'").fetchone()==(False,)*7
        assert db.execute("SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles "
            "WHERE rolname='ledger_program_account_provisioner') OR roleid=(SELECT oid FROM pg_roles "
            "WHERE rolname='ledger_program_account_provisioner')").fetchone()==(0,)
        for role in ('ledger_owner','ledger_control','ledger_decision','ledger_program_dispatch_verifier','ledger_program_source_verifier'):
            assert not db.execute("SELECT has_function_privilege(%s,'ledger.provision_program_account_opening(text,jsonb,text)','EXECUTE')",(role,)).fetchone()[0]
            assert not db.execute("SELECT has_table_privilege(%s,'ledger.program_account_openings','INSERT')",(role,)).fetchone()[0]


def test_explicit_full_vector_is_committed_without_position_or_send_authority(database):
    from trading_system.execution.program_account_opening import read_program_account_opening
    c=real_case(database);balances,evidence=inputs(c)
    proof=service(database,c).initialize(balances,evidence=evidence)
    with c.service.uow.transaction(c.scope) as tx:
        actual=read_program_account_opening(tx)
        assert actual==proof and {m.currency:m.amount for m in actual.balances}==dict(USDT=D('1000000'),BTC=D(10))
        assert actual.collateral_group_id==tx.group_id and not actual.execution_authorized
        assert tx.execute('SELECT count(*) FROM ledger.positions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)


def test_owner_cannot_turn_an_opening_dto_into_capital(database):
    import psycopg
    from trading_system.execution.program_account_opening import ProgramAccountOpeningService,read_program_account_opening
    c=real_case(database);balances,evidence=inputs(c)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ProgramAccountOpeningService(c.service.uow,c.scope).initialize(balances,evidence=evidence)
    with c.service.uow.transaction(c.scope) as tx:assert read_program_account_opening(tx) is None


def test_initialization_cannot_be_used_after_admission_to_increase_capital(database):
    import psycopg
    from integration.test_program_commands_postgres import attach
    c=real_case(database);balances,evidence=inputs(c)
    c.service._transaction(lambda tx:attach(c,tx))
    with pytest.raises(psycopg.errors.CheckViolation,match='OPENING_REQUIRES_FRESH_ACCOUNT'):
        service(database,c).initialize(balances,evidence=evidence)


def test_replay_preserves_original_opening_but_a_different_vector_is_rejected(database):
    import psycopg
    from integration.test_program_commands_postgres import attach
    c=real_case(database);balances,evidence=inputs(c);s=service(database,c)
    first=s.initialize(balances,evidence=evidence)
    c.service._transaction(lambda tx:attach(c,tx))
    assert s.initialize(tuple(reversed(balances)),evidence=evidence)==first
    with pytest.raises(psycopg.errors.CheckViolation,match='OPENING_IMMUTABLE'):
        s.initialize((replace(balances[0],amount=D('1000001')),balances[1]),evidence=evidence)


def test_opening_is_shared_across_instruments_but_never_across_accounts(database):
    from trading_system.execution.program_account_opening import read_program_account_opening
    c=real_case(database);balances,evidence=inputs(c)
    original=service(database,c).initialize(balances,evidence=evidence)
    with c.service.uow.transaction(replace(c.scope,instrument_uid='another-instrument')) as tx:
        assert read_program_account_opening(tx)==original
    foreign=real_case(database)
    with foreign.service.uow.transaction(foreign.scope) as tx:assert read_program_account_opening(tx) is None


def test_failed_commit_does_not_create_spendable_opening(database):
    from trading_system.execution.program_account_opening import ProgramAccountOpeningService,read_program_account_opening
    c=real_case(database);balances,evidence=inputs(c);s=service(database,c)
    class FailCommit:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def commit(self):raise RuntimeError('injected opening commit failure')
    broken=ProgramAccountOpeningService(UnitOfWork(lambda:FailCommit(s.uow.connection_factory())),c.scope)
    with pytest.raises(RuntimeError,match='injected opening commit failure'):broken.initialize(balances,evidence=evidence)
    with c.service.uow.transaction(c.scope) as tx:assert read_program_account_opening(tx) is None
    assert s.initialize(balances,evidence=evidence)
