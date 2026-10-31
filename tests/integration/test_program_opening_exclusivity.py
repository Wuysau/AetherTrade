"""Additive guard: one account has one initial proof, including concurrent init."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from integration.support_ip_03 import isolated_postgres,ROOT
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json
from trading_system.execution.program_account_opening import ProgramAccountOpeningService
from trading_system.execution.program_legacy_opening import ProgramLegacyOpeningService
from trading_system.server.paper_runtime import scope_for,ACCOUNT,GROUP
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork


@pytest.fixture
def database(isolated_postgres,request):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    name='ip03_test_opening_exclusivity_'+uuid4().hex[:16]
    with psycopg.connect(isolated_postgres,autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn=make_conninfo(**{**conninfo_to_dict(isolated_postgres),'dbname':name})
    names=('0004_command_abort_guard.sql','0006_proposal_intake_role.sql','0008_order_program_records.sql',
        '0009_order_program_closure.sql','0010_order_program_intake.sql','0011_order_program_financial_credit_guard.sql',
        '0012_program_command_boundary.sql','0016_program_fill_source_approval.sql','0017_program_fill_source_snapshots.sql',
        '0018_program_fill_source_leaves.sql','0019_program_account_opening.sql','0020_program_legacy_opening.sql',
        '0024_program_opening_exclusivity.sql')
    with psycopg.connect(dsn) as db:
        db.execute((ROOT/'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        db.execute((ROOT/'deploy/db/roles.sql').read_text(encoding='utf-8'))
        for name in names:
            if name=='0024_program_opening_exclusivity.sql' and not getattr(request,'param',True):continue
            path=ROOT/'migrations'/name
            if path.exists():db.execute(path.read_text(encoding='utf-8'))
        db.execute('INSERT INTO ledger.collateral_mutex VALUES(%s,%s)',('PAPER',GROUP))
        db.execute('INSERT INTO ledger.account_bindings VALUES(%s,%s,%s,%s,NULL)',('PAPER',ACCOUNT,'SPOT',GROUP))
        db.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) '
            "VALUES(%s,'console:seed',1,%s::jsonb,%s)",(scope_id(scope_for('BTCUSDT')),canonical_json(seed()),uuid4().hex))
    return dsn


@pytest.mark.parametrize('database',(False,),indirect=True)
@pytest.mark.parametrize('reader_name',('read_program_account_opening','read_program_legacy_opening',
    'read_program_local_account_opening'))
@pytest.mark.parametrize('strict_cash',(False,True))
def test_historical_dual_proofs_survive_upgrade_but_every_reader_rejects(database,reader_name,strict_cash):
    import psycopg
    from trading_system.execution import program_account_opening,program_legacy_opening
    from trading_system.execution.program_account_cash import read_program_account_cash
    from trading_system.storage.models import MissingEvidence
    new,legacy,ref=services(database)
    legacy.attest(canonical_hash(seed()),evidence=ref)
    new.initialize((t.Money(currency='USDT',amount=D('100000')),),evidence=ref)
    def stored(db):
        return tuple(db.execute('SELECT payload,payload_hash,xmin::text,database_transaction::text '
            'FROM ledger.'+table).fetchall() for table in
            ('program_account_openings','program_legacy_account_openings'))
    with psycopg.connect(database) as db:before=stored(db)
    assert tuple(map(len,before))==(1,1) # genuine protected pre-0024 history
    with psycopg.connect(database) as db:
        db.execute((ROOT/'migrations/0024_program_opening_exclusivity.sql').read_text(encoding='utf-8'))
    with psycopg.connect(database) as db:assert stored(db)==before
    reader=getattr(program_account_opening,reader_name,None) or getattr(program_legacy_opening,reader_name)
    def connect():
        db=psycopg.connect(database);db.execute('SET ROLE ledger_owner');db.commit();return db
    with UnitOfWork(connect).transaction(scope_for('BTCUSDT')) as tx:
        with pytest.raises(MissingEvidence,match='AMBIGUOUS_ACCOUNT_OPENING'):
            if strict_cash:read_program_account_cash(tx,read_opening=reader)
            else:reader(tx)
    with psycopg.connect(database) as db:assert stored(db)==before


def seed():return dict(initial_cash='100000',source='EXPLICIT_NEW_VIRTUAL_CAPITAL')


def services(database):
    import psycopg
    def connect():
        db=psycopg.connect(database);db.execute('SET ROLE ledger_program_account_provisioner');db.commit();return db
    uow=UnitOfWork(connect);scope=scope_for('BTCUSDT')
    ref=t.EvidenceRef(id='explicit-initial-proof-review',hash=canonical_hash(seed()),environment=t.Environment.PAPER,
        source='ISOLATED_INDEPENDENT_INITIALIZATION_REVIEW',visible_at='2026-10-28T10:00:00Z')
    return ProgramAccountOpeningService(uow,scope),ProgramLegacyOpeningService(uow,scope),ref


@pytest.mark.parametrize('first',('legacy','new'))
def test_reverse_initialization_never_leaves_two_capital_proofs(database,first):
    import psycopg
    new,legacy,ref=services(database)
    actions=dict(new=lambda:new.initialize((t.Money(currency='USDT',amount=D('100000')),),evidence=ref),
        legacy=lambda:legacy.attest(canonical_hash(seed()),evidence=ref))
    original=actions[first]()
    with pytest.raises(psycopg.errors.CheckViolation):actions['new' if first=='legacy' else 'legacy']()
    assert actions[first]()==original # exact retry retains the original proof
    with psycopg.connect(database) as db:
        assert db.execute('SELECT (SELECT count(*) FROM ledger.program_account_openings)+'
            '(SELECT count(*) FROM ledger.program_legacy_account_openings)').fetchone()==(1,)


def test_concurrent_initializers_cannot_both_win(database):
    import psycopg
    new,legacy,ref=services(database);ready=Barrier(2)
    def initialize(kind):
        ready.wait(timeout=20)
        try:
            if kind=='new':new.initialize((t.Money(currency='USDT',amount=D('100000')),),evidence=ref)
            else:legacy.attest(canonical_hash(seed()),evidence=ref)
            return 'created'
        except psycopg.errors.CheckViolation:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(initialize,('new','legacy')))
    assert sorted(results)==['created','rejected']
    with psycopg.connect(database) as db:
        assert db.execute('SELECT (SELECT count(*) FROM ledger.program_account_openings)+'
            '(SELECT count(*) FROM ledger.program_legacy_account_openings)').fetchone()==(1,)
