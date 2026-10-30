"""Genuine query observations separate submission knownness from fill/release."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from integration.test_program_fill_source_approval import database as source_database,source_uow,command_database
from integration.test_paper_market_matcher import isolated_postgres,venue,case,submit,event
from integration.test_paper_program_reports_postgres import actual_verifier
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.engine.paper_market import PaperMatcher
from trading_system.storage.models import scope_id


@pytest.fixture(scope='module')
def database(source_database):
    import psycopg
    migration=Path(__file__).resolve().parents[2]/'migrations/0021_program_submission_observations.sql'
    if migration.exists():
        with psycopg.connect(source_database) as db:db.execute(migration.read_text(encoding='utf-8'))
    return source_database


def observer(c,path,version=1):
    from trading_system.execution.program_submission_observation import ProgramSubmissionObservationService
    if version==1:verifier,codec=actual_verifier(c,path)
    else:
        from integration.test_paper_program_v2_reports_postgres import verifier as actual_v2
        verifier,codec=actual_v2(c,path)
    return ProgramSubmissionObservationService(source_uow(c.dsn),c.scope,verifier=verifier),codec


@pytest.mark.parametrize('version',[1,2])
def test_actual_accepted_unfilled_gtc_has_protected_knownness_after_creation_ttl_without_fill_or_release(
        database,venue,tmp_path,version):
    c=case(database,venue,tmp_path)
    if version==2:
        from psycopg.conninfo import conninfo_to_dict,make_conninfo
        from trading_system.engine.paper_market_v2 import provision_paper_market_v2,PaperMatchingModelV2
        provision_paper_market_v2(make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(venue.runtime_dsn)['dbname']}),venue)
        c.engine._matching_model=PaperMatchingModelV2(model_id='SUBMISSION_OBSERVATION_FROZEN_V2',quote_fee_rate='0')
    submit(c)
    raw=c.engine.query_creation(c.write.command_id)
    assert not raw.fills and raw.orders[0].venue_order_id and raw.remaining_quantity==1
    before=c.c.facts.inputs.clock
    c.engine._clock=lambda:replace(before,utc_now=(utc_timestamp(before.utc_now)+timedelta(seconds=10)).isoformat(),
        monotonic_ns=before.monotonic_ns+10**10)
    service,codec=observer(c,tmp_path/str(version),version)
    try:result=service.observe(c.write.command_id)
    finally:codec.close()
    from trading_system.storage.application_writes import application_write_obligations
    assert result.state=='ACCEPTED' and result.ever_accepted and not result.execution_authorized
    with c.service.uow.transaction(c.scope) as tx:
        obligation,=application_write_obligations(tx,scope_only=True)
        assert not obligation.unresolved and obligation.current.state=='MAY_HAVE_SENT'
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_approvals WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        snapshot=tx.execute('SELECT payload FROM ledger.program_fill_source_snapshots WHERE scope_id=%s AND snapshot_id=%s',
            (scope_id(c.scope),result.source_snapshot_hash)).fetchone()[0]
        assert snapshot['normalized_fills']=={} and snapshot['verification']['fills']==[]
        assert canonical_hash(snapshot)==result.source_snapshot_hash
        from trading_system.risk.reservation_repository import ReservationRepository
        assert tuple(ReservationRepository(tx).get(p.reservation.reservation_id) for p in c.bundle.reservations)==c.bundle.reservations


def test_actual_rejected_post_only_submission_never_posts_fill_or_releases_hold(database,venue,tmp_path):
    c=case(database,venue,tmp_path,post_only=True)
    PaperMatcher(c.engine).apply_market_event(event(c,ask='81490',quantity='1'));submit(c)
    assert c.engine.query_creation(c.write.command_id).status=='REJECTED'
    service,codec=observer(c,tmp_path/'rejected')
    try:result=service.observe(c.write.command_id)
    finally:codec.close()
    assert result.state=='REJECTED' and not result.ever_accepted and not result.funds_release_authorized
    with c.service.uow.transaction(c.scope) as tx:
        from trading_system.risk.reservation_repository import ReservationRepository
        assert tuple(ReservationRepository(tx).get(p.reservation.reservation_id) for p in c.bundle.reservations)==c.bundle.reservations


def test_latest_missing_query_preserves_acceptance_history_but_blocks_send_uncertainty(database,venue,tmp_path):
    c=case(database,venue,tmp_path);submit(c);service,codec=observer(c,tmp_path/'unknown')
    try:
        assert service.observe(c.write.command_id).state=='ACCEPTED'
        c.engine.query_creation=lambda _:None
        result=service.observe(c.write.command_id)
    finally:codec.close()
    assert result.state=='UNKNOWN' and result.ever_accepted
    with c.service.uow.transaction(c.scope) as tx:
        from trading_system.storage.application_writes import application_write_obligations
        obligation,=application_write_obligations(tx,scope_only=True)
        assert obligation.unresolved and obligation.current.state=='MAY_HAVE_SENT'
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s AND sequence=1',
            (scope_id(c.scope),)).fetchone()==(1,)


def test_owner_audit_flags_and_direct_private_calls_cannot_mint_submission_knownness(database,venue,tmp_path):
    import psycopg
    from psycopg.types.json import Jsonb
    c=case(database,venue,tmp_path);submit(c)
    from trading_system.execution.program_submission_observation import read_program_submission_observation
    with c.service.uow.transaction(c.scope) as tx:
        tx.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,%s,1,%s,%s)',
            (scope_id(c.scope),'fake-submission:'+c.write.command_id,Jsonb(dict(source_verified=True,state='ACCEPTED')),tx.transaction_id))
        assert read_program_submission_observation(tx,c.write) is None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.service.uow.transaction(c.scope) as tx:
            tx.execute('SELECT ledger.begin_program_submission_probe(%s,%s,%s::jsonb,%s)',
                (scope_id(c.scope),c.write.command_id,'{}',tx.transaction_id))


def test_resealed_unfilled_venue_identity_never_becomes_known_submission(database,venue,tmp_path):
    from integration.test_paper_program_reports_postgres import reseal_report
    c=case(database,venue,tmp_path);submit(c)
    genuine=c.engine.query_creation(c.write.command_id)
    c.engine.query_creation=lambda _:reseal_report(replace(genuine,orders=(replace(genuine.orders[0],venue_order_id='invented-venue-id'),)))
    service,codec=observer(c,tmp_path/'resealed')
    try:result=service.observe(c.write.command_id)
    finally:codec.close()
    assert result.state=='UNKNOWN' and not result.submission_known and result.record.raw_refs
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_snapshots WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)


def test_private_source_completion_cannot_reuse_another_commands_snapshot(database,venue,tmp_path):
    import psycopg
    from uuid import uuid4
    from trading_system.contracts.canonical import canonical_json
    a=case(database,venue,tmp_path);b=case(database,venue,tmp_path);submit(a);submit(b)
    service,codec=observer(a,tmp_path/'a')
    other,other_codec=observer(b,tmp_path/'b')
    try:
        first=service.observe(a.write.command_id);second=other.observe(b.write.command_id)
    finally:codec.close();other_codec.close()
    start=replace(first.record,probe_id=uuid4().hex,phase='STARTED',state='UNKNOWN',source_snapshot_hash=None,
        manifest_ref=None,raw_refs=(),members=(),reasons=('QUERY_IN_PROGRESS',))
    with service.uow.transaction(a.scope) as tx:
        tx.execute('SELECT ledger.begin_program_submission_probe(%s,%s,%s::jsonb,%s)',
            (scope_id(a.scope),a.write.command_id,canonical_json(domain_payload(start)),tx.transaction_id))
    forged=replace(first.record,probe_id=start.probe_id,source_snapshot_hash=second.source_snapshot_hash)
    with pytest.raises(psycopg.errors.CheckViolation,match='protected source snapshot'):
        with service.uow.transaction(a.scope) as tx:
            tx.execute('SELECT ledger.record_program_submission_observation(%s,%s,%s::jsonb,%s)',
                (scope_id(a.scope),a.write.command_id,canonical_json(domain_payload(forged)),tx.transaction_id))
    from trading_system.execution.program_submission_observation import read_program_submission_observation
    with a.service.uow.transaction(a.scope) as tx:
        latest=read_program_submission_observation(tx,a.write)
        assert latest.state=='UNKNOWN' and latest.ever_accepted


def test_delayed_actual_acceptance_completion_cannot_override_a_newer_query_failure(database,venue,tmp_path):
    import threading
    c=case(database,venue,tmp_path);submit(c)
    genuine=c.engine.query_creation;entered=threading.Event();release=threading.Event();lock=threading.Lock();calls=0
    def controlled_query(command_id):
        nonlocal calls
        with lock:calls+=1;index=calls
        if index==1:
            entered.set()
            assert release.wait(30),'actual old query was never released'
            return genuine(command_id)
        raise ConnectionError('controlled independent query failure')
    c.engine.query_creation=controlled_query
    results=[];errors=[]
    def older_probe():
        service,codec=observer(c,tmp_path/'older')
        try:results.append(service.observe(c.write.command_id))
        except BaseException as error:errors.append(error)
        finally:codec.close()
    thread=threading.Thread(target=older_probe);thread.start()
    try:
        assert entered.wait(20),'older actual query never started'
        service,codec=observer(c,tmp_path/'newer')
        try:assert service.observe(c.write.command_id).state=='UNKNOWN'
        finally:codec.close()
    finally:
        release.set();thread.join(35)
    assert not thread.is_alive() and not errors and results
    assert results[0].state=='UNKNOWN' and results[0].ever_accepted
    from trading_system.execution.program_submission_observation import read_program_submission_observation
    with c.service.uow.transaction(c.scope) as tx:
        latest=read_program_submission_observation(tx,c.write)
        assert latest.state=='UNKNOWN' and latest.ever_accepted and not latest.submission_known
        assert tx.execute("SELECT count(*) FROM ledger.program_submission_observations WHERE scope_id=%s AND phase='COMPLETED' AND state='ACCEPTED'",
            (scope_id(c.scope),)).fetchone()==(1,)
