"""Real financial consumption/matching before a separate, protected source attestation.

An approval proves historical PAPER source only. It cannot authorize another
send, collateral release, position ownership or a spendable asset balance.
"""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from integration.test_paper_market_matcher import isolated_postgres,venue,case,submit,event
from integration.test_program_commands_postgres import database as command_database
from integration.test_paper_program_reports_postgres import actual_verifier,change_observed_fill,reseal_report
from trading_system.contracts.canonical import domain_payload,canonical_hash,utc_timestamp
from trading_system.engine.paper_market import PaperMatcher
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork


@pytest.fixture(scope='module')
def database(command_database):
    import psycopg
    for name in ('0016_program_fill_source_approval.sql','0017_program_fill_source_snapshots.sql',
                 '0018_program_fill_source_leaves.sql'):
        path=Path(__file__).resolve().parents[2]/'migrations'/name
        if path.exists():
            with psycopg.connect(command_database) as db:db.execute(path.read_text(encoding='utf-8'))
    return command_database


def source_uow(database):
    import psycopg
    def connect():
        db=psycopg.connect(database)
        db.execute('SET ROLE ledger_program_source_verifier');db.commit()
        return db
    return UnitOfWork(connect)


def setup_source(database,venue,tmp_path,**options):
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    c=case(database,venue,tmp_path,**options);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    verifier,codec=actual_verifier(c,tmp_path/'archive')
    service=ProgramFillSourceService(source_uow(database),c.scope,verifier=verifier)
    return c,service,codec


def test_source_authority_is_separate_and_cannot_be_enrolled_implicitly(database):
    import psycopg
    with psycopg.connect(database) as db:
        assert db.execute("SELECT to_regclass('ledger.program_fill_source_approvals')").fetchone()[0] is not None
        assert db.execute("SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls "
            "FROM pg_roles WHERE rolname='ledger_program_source_verifier'").fetchone()==(False,)*7
        assert db.execute("SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles "
            "WHERE rolname='ledger_program_source_verifier') OR roleid=(SELECT oid FROM pg_roles "
            "WHERE rolname='ledger_program_source_verifier')").fetchone()==(0,)
        for role in ('ledger_owner','ledger_control','ledger_decision','ledger_program_dispatch_verifier',
                     'ledger_order_closure_resolver'):
            assert not db.execute("SELECT has_function_privilege(%s,'ledger.approve_program_fill_source(text,text,jsonb,text)','EXECUTE')",(role,)).fetchone()[0]
            assert not db.execute("SELECT has_table_privilege(%s,'ledger.program_fill_source_approvals','INSERT')",(role,)).fetchone()[0]
            assert not db.execute("SELECT has_function_privilege(%s,'ledger.approve_program_fill_source_snapshot(text,text,jsonb,text)','EXECUTE')",(role,)).fetchone()[0]
            assert not db.execute("SELECT has_function_privilege(%s,'ledger.approve_program_fill_source_v2(text,text,text,text,text)','EXECUTE')",(role,)).fetchone()[0]
            assert not db.execute("SELECT has_table_privilege(%s,'ledger.program_fill_source_snapshots','INSERT')",(role,)).fetchone()[0]
        for role in ('ledger_owner','ledger_control','ledger_decision','ledger_program_dispatch_verifier',
                     'ledger_order_closure_resolver','ledger_program_source_verifier'):
            assert not db.execute("SELECT has_function_privilege(%s,'ledger.approve_program_fill_source_snapshot_v1(text,text,jsonb,text)','EXECUTE')",(role,)).fetchone()[0]
            assert not db.execute("SELECT has_function_privilege(%s,'ledger.materialize_program_fill_source(text,text)','EXECUTE')",(role,)).fetchone()[0]
            for table in ('program_fill_source_headers','program_fill_source_leaves'):
                assert not db.execute('SELECT has_table_privilege(%s,%s,\'INSERT\')',(role,'ledger.'+table)).fetchone()[0]


def test_genuine_fill_source_is_committed_without_posting_or_authorizing(database,venue,tmp_path):
    c,service,codec=setup_source(database,venue,tmp_path)
    try:result=service.approve(c.write.command_id)
    finally:codec.close()
    assert result.status=='APPROVED' and result.verification.source_verified
    assert len(result.approvals)==1
    assert not result.execution_authorized and not result.funds_release_authorized and not result.balance_credit_authorized
    approval=result.approvals[0]
    assert approval.normalized.fill.quantity.value==__import__('decimal').Decimal('.4')
    assert approval.normalized.economics.unit==c.bundle.intent.unit
    with c.service.uow.transaction(c.scope) as tx:
        row=tx.execute('SELECT payload,payload_hash,database_transaction::text,xmin::text FROM ledger.program_fill_source_approvals '
            'WHERE scope_id=%s AND approval_id=%s',(scope_id(c.scope),approval.approval_id)).fetchone()
        assert canonical_hash(row[0])==row[1]==approval.approval_id
        assert int(row[2])%(2**32)==int(row[3])
        proof=row[0]
        if proof['record_kind']=='PAPER_PROGRAM_FILL_SOURCE_V2':
            proof=tx.execute('SELECT payload FROM ledger.program_fill_source_snapshots WHERE scope_id=%s AND snapshot_id=%s',
                (scope_id(c.scope),proof['source_snapshot_hash'])).fetchone()[0]
            assert canonical_hash(proof)==row[0]['source_snapshot_hash']
        assert proof['consumption_hash']==canonical_hash(tx.execute('SELECT context FROM ledger.program_permit_consumptions '
            'WHERE scope_id=%s AND command_id=%s',(scope_id(c.scope),c.write.command_id)).fetchone()[0])
        assert proof['verification']['market_evidence']['consumptions']
        assert proof['verification']['raw_refs']
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


def test_actual_accepted_unfilled_gtc_snapshot_can_be_committed_without_fill_or_credit(database,venue,tmp_path):
    c=case(database,venue,tmp_path);submit(c)
    observer,codec=actual_verifier(c,tmp_path/'empty-accepted-archive')
    try:verified=observer.verify(c.write.command_id)
    finally:codec.close()
    assert verified.source_verified and not verified.fills and len(verified.orders)==1
    assert verified.orders[0].leaves_quantity.value==__import__('decimal').Decimal('1')
    from trading_system.execution.program_fill_source import save_verification_snapshot
    with source_uow(database).transaction(c.scope) as tx:
        snapshot,hashes=save_verification_snapshot(tx,verified)
    assert hashes==() and len(snapshot)==64
    with c.service.uow.transaction(c.scope) as tx:
        row=tx.execute('SELECT payload FROM ledger.program_fill_source_snapshots WHERE scope_id=%s AND snapshot_id=%s',
            (scope_id(c.scope),snapshot)).fetchone()
        assert row[0]['verification']['raw_report']['request']['command_id']==c.write.command_id
        assert row[0]['normalized_fills']=={}
        assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_approvals WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


def test_owner_cannot_mint_or_mutate_source_approval(database,venue,tmp_path):
    import psycopg
    c,service,codec=setup_source(database,venue,tmp_path)
    try:result=service.approve(c.write.command_id)
    finally:codec.close()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.service.uow.transaction(c.scope) as tx:
            tx.execute('SELECT ledger.approve_program_fill_source(%s,%s,%s::jsonb,%s)',
                (scope_id(c.scope),c.write.command_id,'{}',tx.transaction_id))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.service.uow.transaction(c.scope) as tx:
            tx.execute("INSERT INTO ledger.program_fill_source_approvals(scope_id,command_id,approval_id,identity_hash,payload,payload_hash,transaction_id) "
                "VALUES(%s,%s,'forged','forged','{}','forged',%s)",(scope_id(c.scope),c.write.command_id,tx.transaction_id))
    # Even the source role cannot alter its committed historical statement.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with service.uow.transaction(c.scope) as tx:
            tx.execute('DELETE FROM ledger.program_fill_source_approvals WHERE scope_id=%s',(scope_id(c.scope),))
    assert result.approvals


def test_replay_and_additional_real_fills_retain_identity_without_ledger_side_effects(database,venue,tmp_path):
    c,service,codec=setup_source(database,venue,tmp_path)
    try:
        first=service.approve(c.write.command_id);again=service.approve(c.write.command_id)
        assert first.approvals==again.approvals
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        additional=service.approve(c.write.command_id)
    finally:codec.close()
    assert len(additional.approvals)==2
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(DISTINCT identity_hash) FROM ledger.program_fill_source_approvals '
            'WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(2,)
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


def test_service_requires_actual_verifier_and_original_id_only(database,venue,tmp_path):
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    c,service,codec=setup_source(database,venue,tmp_path)
    try:
        with pytest.raises(ValueError,match='ACTUAL_SCOPED_PAPER_VERIFIER_REQUIRED'):
            ProgramFillSourceService(source_uow(database),c.scope,verifier=object())
        with pytest.raises(ValueError,match='ACTUAL_SCOPED_PAPER_VERIFIER_REQUIRED'):
            ProgramFillSourceService(source_uow(database),replace(c.scope,venue_account_id='foreign'),verifier=service.verifier)
        with pytest.raises(ValueError,match='ORIGINAL_COMMAND_ID_REQUIRED'):
            service.approve(service.verifier.verify(c.write.command_id))
    finally:codec.close()
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_approvals WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)


def test_historical_fill_source_does_not_expire_into_a_new_execution_permit(database,venue,tmp_path):
    c,service,codec=setup_source(database,venue,tmp_path)
    old=c.c.facts.inputs.clock
    c.c.facts=replace(c.c.facts,inputs=replace(c.c.facts.inputs,clock=replace(old,
        utc_now=(utc_timestamp(c.bundle.intent.valid_until)+timedelta(minutes=2)).isoformat(),
        monotonic_ns=old.monotonic_ns+1_000_000_000_000)))
    try:result=service.approve(c.write.command_id)
    finally:codec.close()
    assert result.status=='APPROVED' and result.approvals and not result.execution_authorized
    assert not result.funds_release_authorized and not result.balance_credit_authorized


def test_economic_discrepancy_preserves_source_without_certain_credit(database,venue,tmp_path):
    from decimal import Decimal
    c,service,codec=setup_source(database,venue,tmp_path)
    original=c.engine.query_creation
    def observe_changed(command_id):
        report=original(command_id)
        return reseal_report(replace(report,fills=(change_observed_fill(report.fills[0],gross_notional=Decimal('1')),)))
    c.engine.query_creation=observe_changed
    try:result=service.approve(c.write.command_id)
    finally:codec.close()
    assert result.status=='SOURCE_ONLY' and result.verification.source_verified
    assert result.approvals and result.approvals[0].normalized.economics.discrepancies
    assert not result.balance_credit_authorized


def test_missing_or_not_observed_command_cannot_mint_source_approval(database,venue,tmp_path):
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    c=case(database,venue,tmp_path)
    verifier,codec=actual_verifier(c,tmp_path/'archive')
    service=ProgramFillSourceService(source_uow(database),c.scope,verifier=verifier)
    try:
        before=service.approve(c.write.command_id)
        missing=service.approve('missing-original-command')
    finally:codec.close()
    assert before.status==missing.status=='BLOCKED' and not before.approvals and not missing.approvals


def test_owner_reads_only_committed_protected_source_and_not_own_audit_json(database,venue,tmp_path):
    from trading_system.execution.program_fill_source import read_committed_fill_source
    c,service,codec=setup_source(database,venue,tmp_path)
    try:approved=service.approve(c.write.command_id)
    finally:codec.close()
    expected=approved.approvals[0]
    with c.service.uow.transaction(c.scope) as tx:
        found=read_committed_fill_source(tx,expected.approval_id)
        assert found==expected and not found.balance_credit_authorized
        tx.execute("INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) "
            "VALUES(%s,'forged-source-approval',1,%s::jsonb,%s)",
            (scope_id(c.scope),'{"source_verified":true}',tx.transaction_id))
        with pytest.raises(ValueError,match='PROGRAM_COMMITTED_SOURCE_APPROVAL_REQUIRED'):
            read_committed_fill_source(tx,'f'*64)
    with c.service.uow.transaction(replace(c.scope,instrument_uid='foreign-instrument')) as tx:
        with pytest.raises(ValueError,match='PROGRAM_COMMITTED_SOURCE_APPROVAL_REQUIRED'):
            read_committed_fill_source(tx,expected.approval_id)


def test_uncommitted_approval_cannot_be_consumed_even_in_the_source_transaction(database,venue,tmp_path):
    from trading_system.execution.program_fill_source import read_committed_fill_source,_source_context
    from trading_system.contracts.canonical import canonical_json
    c,service,codec=setup_source(database,venue,tmp_path)
    try:verified=service.verifier.verify(c.write.command_id)
    finally:codec.close()
    with pytest.raises(ValueError,match='PROGRAM_COMMITTED_SOURCE_APPROVAL_REQUIRED'):
        with service.uow.transaction(c.scope) as tx:
            payload=dict(contract_version='2026-10-30',record_kind='PAPER_PROGRAM_FILL_SOURCE_V1',
                scope=domain_payload(c.scope),command_id=c.write.command_id,**_source_context(tx,c.write.command_id),
                normalized=domain_payload(verified.fills[0]),verification=domain_payload(verified))
            approval_id=tx.execute('SELECT ledger.approve_program_fill_source(%s,%s,%s::jsonb,%s)',
                (scope_id(c.scope),c.write.command_id,canonical_json(payload),tx.transaction_id)).fetchone()[0]
            read_committed_fill_source(tx,approval_id)
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_approvals WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)


def test_source_commit_failure_cannot_return_a_usable_approval(database,venue,tmp_path):
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    c,service,codec=setup_source(database,venue,tmp_path)
    class FailCommit:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def commit(self):raise RuntimeError('injected source commit failure')
    failing=ProgramFillSourceService(UnitOfWork(lambda:FailCommit(service.uow.connection_factory())),
        c.scope,verifier=service.verifier)
    try:
        with pytest.raises(RuntimeError,match='injected source commit failure'):
            failing.approve(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_approvals WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(0,)
        assert service.approve(c.write.command_id).approvals
    finally:codec.close()


def test_many_genuine_fills_send_one_source_proof_instead_of_one_copy_per_fill(database,venue,tmp_path):
    from trading_system.contracts.canonical import canonical_json
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    c,service,codec=setup_source(database,venue,tmp_path)
    for sequence in range(2,10):
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=sequence,quantity='.01',kind='DEPTH_DELTA'))
    sent=[]
    class Measured:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def execute(self,sql,params=()):
            if sql.startswith('SELECT ledger.approve_program_fill_source'):
                sent.append(sum(len(p.encode('utf-8')) for p in params if type(p) is str))
            return self.db.execute(sql,params)
    measured=ProgramFillSourceService(UnitOfWork(lambda:Measured(service.uow.connection_factory())),
        c.scope,verifier=service.verifier)
    try:
        verified=service.verifier.verify(c.write.command_id)
        result=measured.approve(c.write.command_id)
    finally:codec.close()
    assert len(result.approvals)==len(verified.fills)==9
    source_bytes=len(canonical_json(domain_payload(verified)).encode('utf-8'))
    # Modest real-source probe: no enormous fixture/allocation is needed to
    # expose repeated full report/all-rival serialization inside the mutex.
    assert sum(sent)<=3*source_bytes+4096*len(verified.fills),(sum(sent),source_bytes)
    from trading_system.execution.program_fill_source import CommittedProgramFillSources
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_snapshots WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(1,)
        source_reads=[];original=tx.execute
        def measured_read(sql,params=()):
            if 'FROM ledger.program_fill_source_snapshots WHERE' in sql:source_reads.append(sql)
            return original(sql,params)
        tx.execute=measured_read
        reader=CommittedProgramFillSources(tx)
        assert tuple(reader.read(a.approval_id) for a in result.approvals)==result.approvals
        assert len(source_reads)==1
    with pytest.raises(ValueError,match='PROGRAM_SOURCE_TRANSACTION_CLOSED'):
        reader.read(result.approvals[0].approval_id)


def test_historical_v1_approval_still_reads_without_rewriting_its_payload(database,venue,tmp_path):
    from trading_system.execution.program_fill_source import _source_context,read_committed_fill_source
    from trading_system.contracts.canonical import canonical_json
    c,service,codec=setup_source(database,venue,tmp_path)
    try:verified=service.verifier.verify(c.write.command_id)
    finally:codec.close()
    with service.uow.transaction(c.scope) as tx:
        payload=dict(contract_version='2026-10-30',record_kind='PAPER_PROGRAM_FILL_SOURCE_V1',
            scope=domain_payload(c.scope),command_id=c.write.command_id,**_source_context(tx,c.write.command_id),
            normalized=domain_payload(verified.fills[0]),verification=domain_payload(verified))
        approval_id=tx.execute('SELECT ledger.approve_program_fill_source(%s,%s,%s::jsonb,%s)',
            (scope_id(c.scope),c.write.command_id,canonical_json(payload),tx.transaction_id)).fetchone()[0]
    with c.service.uow.transaction(c.scope) as tx:
        found=read_committed_fill_source(tx,approval_id)
        assert found.normalized==verified.fills[0] and not found.balance_credit_authorized
        assert tx.execute('SELECT payload FROM ledger.program_fill_source_approvals WHERE scope_id=%s AND approval_id=%s',
            (scope_id(c.scope),approval_id)).fetchone()[0]==payload


def test_per_fill_approval_does_not_fetch_or_decompress_shared_toast_proof(database,venue,tmp_path,record_property):
    import psycopg
    c,service,codec=setup_source(database,venue,tmp_path)
    for sequence in range(2,10):
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=sequence,quantity='.01',kind='DEPTH_DELTA'))
    try:result=service.approve(c.write.command_id)
    finally:codec.close()
    with c.service.uow.transaction(c.scope) as tx:
        references=tx.execute('SELECT payload FROM ledger.program_fill_source_approvals WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchall()
    def read_blocks():
        with psycopg.connect(database) as db:
            db.execute('SELECT pg_stat_clear_snapshot()')
            return db.execute("SELECT heap_blks_read+heap_blks_hit,toast_blks_read+toast_blks_hit "
                "FROM pg_statio_user_tables WHERE schemaname='ledger' AND relname='program_fill_source_snapshots'").fetchone()
    initial=read_blocks()
    # Positive control: reading/decompressing the real shared proof must change
    # these counters, otherwise a zero delta would not prove the absence of IO.
    with psycopg.connect(database) as db:
        assert db.execute("SELECT pg_relation_size(reltoastrelid)>0 FROM pg_class "
            "WHERE oid='ledger.program_fill_source_snapshots'::regclass").fetchone()==(True,)
        db.execute('SELECT length(payload::text) FROM ledger.program_fill_source_snapshots WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchall()
        db.execute('SELECT pg_stat_force_next_flush()')
    before=read_blocks()
    assert sum(before)>sum(initial),(initial,before)
    with service.uow.transaction(c.scope) as tx:
        for (payload,) in references:
            tx.execute('SELECT ledger.approve_program_fill_source_v2(%s,%s,%s,%s,%s)',
                (scope_id(c.scope),c.write.command_id,payload['source_snapshot_hash'],payload['normalized_hash'],tx.transaction_id)).fetchone()
        tx.execute('SELECT pg_stat_force_next_flush()')
    after=read_blocks()
    record_property('shared_proof_heap_blocks_before',before[0])
    record_property('shared_proof_toast_blocks_before',before[1])
    record_property('shared_proof_heap_blocks_after',after[0])
    record_property('shared_proof_toast_blocks_after',after[1])
    assert len(references)==len(result.approvals)==9
    assert after==before,dict(before=before,after=after,per_fill_calls=len(references))


def test_0018_backfills_existing_v2_source_without_rewriting_history(database,venue,tmp_path):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from uuid import uuid4
    from trading_system.execution.program_fill_source import ProgramFillSourceService,read_committed_fill_source
    root=Path(__file__).resolve().parents[2]
    name='ip03_test_source_upgrade_'+uuid4().hex[:12]
    with psycopg.connect(database,autocommit=True) as db:
        db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    old_dsn=make_conninfo(**{**conninfo_to_dict(database),'dbname':name})
    with psycopg.connect(old_dsn) as db:
        for relative in ('migrations/0001_business_ledger.sql','deploy/db/roles.sql',
            'migrations/0004_command_abort_guard.sql','migrations/0006_proposal_intake_role.sql',
            'migrations/0008_order_program_records.sql','migrations/0009_order_program_closure.sql',
            'migrations/0010_order_program_intake.sql','migrations/0011_order_program_financial_credit_guard.sql',
            'migrations/0012_program_command_boundary.sql','migrations/0016_program_fill_source_approval.sql',
            'migrations/0017_program_fill_source_snapshots.sql'):
            db.execute((root/relative).read_text(encoding='utf-8'))
    from integration.support_historical_program_source import historical_source_case,VINTAGE
    c,original_approvals,archive=historical_source_case(old_dsn,venue,tmp_path)
    assert VINTAGE=='3d13721cbcdc2e35cdd347c610372f13fee03b4f'
    verifier,codec=actual_verifier(c,archive)
    service=ProgramFillSourceService(source_uow(old_dsn),c.scope,verifier=verifier)
    try:
        with c.service.uow.transaction(c.scope) as tx:
            prior=tuple(read_committed_fill_source(tx,a['approval_id']) for a in original_approvals)
            for actual,original in zip(prior,original_approvals,strict=True):
                assert set(original)=={'approval_id','identity_hash','normalized'}
                assert {key:domain_payload(actual)[key] for key in original}==original
                assert actual.command_id==c.write.command_id
        with psycopg.connect(old_dsn) as db:
            assert db.execute("SELECT to_regclass('ledger.program_fill_source_headers')").fetchone()==(None,)
            snapshots=db.execute('SELECT scope_id,snapshot_id,payload,payload_hash,xmin::text FROM ledger.program_fill_source_snapshots').fetchall()
            approvals=db.execute('SELECT scope_id,approval_id,payload,payload_hash,xmin::text FROM ledger.program_fill_source_approvals').fetchall()
            verification=snapshots[0][2]['verification']
            assert prior[0].verification_status==verification['status']
            assert prior[0].verification_discrepancies==tuple(verification['discrepancies'])
            assert prior[0].retained_duties==tuple(verification['retained_duties'])
            db.execute((root/'migrations/0018_program_fill_source_leaves.sql').read_text(encoding='utf-8'))
        with psycopg.connect(old_dsn) as db:
            assert db.execute('SELECT scope_id,snapshot_id,payload,payload_hash,xmin::text FROM ledger.program_fill_source_snapshots').fetchall()==snapshots
            assert db.execute('SELECT scope_id,approval_id,payload,payload_hash,xmin::text FROM ledger.program_fill_source_approvals').fetchall()==approvals
            assert db.execute('SELECT count(*) FROM ledger.program_fill_source_headers').fetchone()==(1,)
            assert db.execute('SELECT count(*) FROM ledger.program_fill_source_leaves').fetchone()==(1,)
        with c.service.uow.transaction(c.scope) as tx:
            assert read_committed_fill_source(tx,prior[0].approval_id)==prior[0]
        assert service.approve(c.write.command_id).approvals==prior
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        assert len(service.approve(c.write.command_id).approvals)==2
    finally:codec.close()
