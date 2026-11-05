"""Offline ownership-state tests; these do not qualify PostgreSQL locking."""
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import pytest
from sqlalchemy import create_engine, select
from trading_system.engine.binance_demo.contracts import DemoError, DemoScope
from trading_system.storage.demo import DemoRepository, metadata, accounts, physical_owners
from trading_system.storage.demo_physical import PhysicalOwner, _reserve_new_dirty


def scope(epoch='epoch'):
    return DemoScope(account_id='123', account_epoch=epoch, strategy_id='s')


def test_dirty_is_durable_and_never_automatically_reused(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "physical.sqlite"}'); metadata.create_all(engine)
    assert _reserve_new_dirty(engine, physical_owners, accounts, scope()) == 1
    with engine.connect() as conn:
        row = conn.execute(select(physical_owners)).mappings().one()
        assert row['state'] == 'DIRTY' and row['generation'] == 1
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_RECOVERY_REQUIRED'):
        _reserve_new_dirty(engine, physical_owners, accounts, scope('different-epoch'))
    engine.dispose()


def test_legacy_same_uid_is_never_silently_mapped(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "legacy.sqlite"}'); metadata.create_all(engine)
    DemoRepository(engine).register_account(scope('old'), credential_ref='fixture', fingerprint='fp', budget='100')
    with pytest.raises(DemoError, match='DEMO_LEGACY_PHYSICAL_MAPPING_REQUIRED'):
        _reserve_new_dirty(engine, physical_owners, accounts, scope('new'))
    with engine.connect() as conn: assert conn.execute(select(physical_owners)).first() is None
    engine.dispose()


def test_owner_holds_one_transaction_and_lock_loss_does_not_clear_dirty(monkeypatch):
    import trading_system.storage.demo_physical as module
    events = []; dirty = []
    class Result:
        def __init__(self, value): self.value = value
        def scalar_one(self): return self.value
    class Conn:
        invalidated = False; held = True; transaction = False
        def in_transaction(self): return self.transaction
        def exec_driver_sql(self, statement, args=None):
            events.append(statement); self.transaction = True
            if 'pg_try_advisory_xact_lock' in statement: return Result(True)
            if 'pg_backend_pid' in statement and 'pg_locks' not in statement: return Result(123)
            if 'pg_locks' in statement: return Result(self.held)
            if 'pg_advisory_unlock' in statement: return Result(True)
            raise AssertionError(statement)
        def rollback(self): events.append('ROLLBACK'); self.transaction = False
        def close(self): events.append('CLOSE')
    connection = Conn(); engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'), connect=lambda:connection)
    monkeypatch.setattr(module, '_reserve_new_dirty', lambda *args, **kwargs: dirty.append('DIRTY') or 1)
    owner = PhysicalOwner.acquire(engine, None, None, scope())
    assert dirty == ['DIRTY'] and owner.generation == 1
    assert connection.in_transaction() and 'COMMIT' not in events
    connection.held = False
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_OWNER_LOST'): owner.validate(scope())
    owner.close()
    assert dirty == ['DIRTY'] and events[-2:] == ['ROLLBACK','CLOSE']


def test_migration_preserves_legacy_records_and_has_no_clean_path():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE demo_observations (observation_id TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE demo_decisions (run_id TEXT PRIMARY KEY)')
    db.execute("INSERT INTO demo_observations VALUES ('old')"); db.commit()
    migration = Path('migrations/0037_binance_demo_physical_owner.sql').read_text()
    db.executescript(migration)
    assert db.execute('SELECT observation_id, stream_generation FROM demo_observations').fetchone() == ('old', None)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO demo_physical_owners VALUES ('k','{}','a',1,'INVALID',NULL)")
    db.close()


def test_sender_lock_is_held_in_one_transaction_and_detects_loss():
    events = []
    class Result:
        def __init__(self, value): self.value = value
        def scalar_one(self): return self.value
    class Conn:
        invalidated = False; held = True; transaction = False
        def in_transaction(self): return self.transaction
        def exec_driver_sql(self, statement, args=None):
            events.append(statement); self.transaction = True
            if 'pg_try_advisory_xact_lock' in statement: return Result(True)
            if 'pg_backend_pid' in statement and 'pg_locks' not in statement: return Result(123)
            if 'pg_locks' in statement: return Result(self.held)
            if 'pg_advisory_unlock' in statement: return Result(True)
            raise AssertionError(statement)
        def rollback(self): events.append('ROLLBACK'); self.transaction = False
        def close(self): events.append('CLOSE')
    connection = Conn(); engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'), connect=lambda:connection)
    repo = DemoRepository(engine)
    with repo.owner_guard(scope()) as guard:
        guard.validate()
        assert connection.in_transaction() and 'COMMIT' not in events
        connection.held = False
        with pytest.raises(DemoError, match='DEMO_OWNER_LOST'): guard.validate()
    assert events[-2:] == ['ROLLBACK','CLOSE']


def test_physical_connection_is_invalidated_if_rollback_fails():
    events = []
    class Conn:
        def rollback(self): events.append('ROLLBACK'); raise OSError('db lost')
        def invalidate(self): events.append('INVALIDATE')
        def close(self): events.append('CLOSE')
    owner = PhysicalOwner(Conn(), scope(), -42, 123, 1)
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_OWNER_LOST'): owner.close()
    assert events == ['ROLLBACK','INVALIDATE','CLOSE']


def test_sender_connection_is_invalidated_if_rollback_fails():
    from trading_system.storage.demo_physical import PinnedSenderLock
    events = []
    class Conn:
        def rollback(self): events.append('ROLLBACK'); raise OSError('db lost')
        def invalidate(self): events.append('INVALIDATE')
        def close(self): events.append('CLOSE')
    owner = PinnedSenderLock(Conn(), 42, 123)
    with pytest.raises(DemoError, match='DEMO_OWNER_LOST'): owner.close()
    assert events == ['ROLLBACK','INVALIDATE','CLOSE']


def test_postgres_sender_rejects_missing_physical_owner_before_sender_lock():
    from trading_system.execution.demo_commands import DemoExecutionOwner
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        def owner_guard(self, scope): pytest.fail('physical ownership must be checked first')
    owner = DemoExecutionOwner(Repo(), object())
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_OWNER_REQUIRED'):
        owner.dispatch(scope(), 'cmd', context=None)


def test_sender_rechecks_epoch_lock_after_blocking_physical_preflight():
    from contextlib import contextmanager
    from trading_system.execution.demo_commands import DemoExecutionOwner
    class Guard:
        lost = False
        def validate(self):
            if self.lost: raise DemoError('DEMO_OWNER_LOST')
    guard = Guard()
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        @contextmanager
        def owner_guard(self, scope): yield guard
        def account(self, scope): pytest.fail('must not proceed after lock loss')
    owner = DemoExecutionOwner(Repo(), object(), physical_owner=SimpleNamespace(validate=lambda scope:None), preflight=lambda: setattr(guard,'lost',True))
    with pytest.raises(DemoError, match='DEMO_OWNER_LOST'):
        owner.dispatch(scope(), 'cmd', context=None)


def test_sender_rechecks_physical_lock_after_arbitrary_preflight():
    from contextlib import contextmanager
    from trading_system.execution.demo_commands import DemoExecutionOwner
    class Physical:
        lost = False
        def validate(self, scope):
            if self.lost: raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
    physical = Physical()
    class Guard:
        def validate(self): pass
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        @contextmanager
        def owner_guard(self, scope): yield Guard()
        def account(self, scope): pytest.fail('must not proceed after physical lock loss')
    owner = DemoExecutionOwner(Repo(), object(), physical_owner=physical, preflight=lambda: setattr(physical,'lost',True))
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_OWNER_LOST'):
        owner.dispatch(scope(), 'cmd', context=None)


def test_sender_guard_rejects_reuse_from_other_thread():
    from concurrent.futures import ThreadPoolExecutor
    from trading_system.storage.demo_physical import PinnedSenderLock
    class Result:
        def __init__(self, value): self.value = value
        def scalar_one(self): return self.value
    class Conn:
        invalidated = False
        def in_transaction(self): return True
        def exec_driver_sql(self, sql, params=None):
            return Result(True if 'pg_locks' in sql else 123)
    guard = PinnedSenderLock(Conn(), 42, 123)
    guard.validate()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(guard.validate)
        with pytest.raises(DemoError, match='DEMO_OWNER_LOST'): future.result()


def test_dirty_postgres_transaction_requires_synchronous_commit_before_write():
    from contextlib import contextmanager
    events = []
    class Result:
        def first(self): return None
        def scalars(self): return []
        def mappings(self): return self
        def __iter__(self): return iter([])
    class Conn:
        def exec_driver_sql(self, sql): events.append(sql)
        def execute(self, statement): events.append(str(statement)); return Result()
    class Engine:
        dialect = SimpleNamespace(name='postgresql')
        @contextmanager
        def begin(self):
            yield Conn()
            events.append('COMMIT')
    assert _reserve_new_dirty(Engine(), physical_owners, accounts, scope()) == 1
    assert events[0] == 'SET LOCAL synchronous_commit = on'
    assert events[-1] == 'COMMIT'
    assert any('INSERT INTO demo_physical_owners' in event for event in events)


def test_physical_generation_must_match_scope_epoch_and_dirty_row(tmp_path):
    from trading_system.storage.demo import require_physical_generation
    engine = create_engine(f'sqlite:///{tmp_path / "generation.sqlite"}'); metadata.create_all(engine)
    _reserve_new_dirty(engine, physical_owners, accounts, scope())
    with engine.begin() as conn:
        require_physical_generation(conn, scope(), 1)
        for candidate, generation in [(scope(),None),(scope(),True),(scope(),2),(scope('other'),1)]:
            with pytest.raises(DemoError, match='DEMO_PHYSICAL_GENERATION_REJECTED'):
                require_physical_generation(conn,candidate,generation)
    engine.dispose()


def test_owner_loss_during_http_never_marks_acknowledged(monkeypatch):
    from contextlib import contextmanager
    from trading_system.execution.demo_commands import DemoExecutionOwner
    from trading_system.runtime.demo_host import DemoRuntimeHost, AdmissionContext
    physical_scope = scope(); events = []
    class Physical:
        lost = False
        def validate(self, scope):
            if self.lost: raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
    physical = Physical()
    class Guard:
        def validate(self): pass
    class Repo:
        engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        @contextmanager
        def owner_guard(self, scope): yield Guard()
        def account(self, scope):
            return {'credential_ref':'fixture','credential_revision':0,'revision':0,'state':'READY',
                'qualification':{'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,'credential_revision':0,'product':'SPOT',
                'capabilities':{'LIMIT':{'state':'DEMO_VERIFIED','evidence_sha256':'e','official_contract_revision':'v'}}}}
        def command(self, scope, command): return {'method':'POST','purpose':'ENTRY','request':{'type':'LIMIT'}}
        def health(self, scope): return {'unknown':0,'unprotected':0,'pending_inbox':0,'risk_breaches':0}
        def begin_send(self, *args, **kwargs): events.append('MAY'); return {'method':'POST','request':{'type':'LIMIT'}}
        def acknowledge(self, *args): events.append('ACK')
        def mark_unknown(self, *args): events.append('UNKNOWN')
    class Transport:
        credential_ref = 'fixture'
        def sync_time(self): pass
        def send_order(self, request, *, permit): physical.lost = True; return {'orderId':1}
    monkeypatch.setattr(DemoRuntimeHost,'admit',staticmethod(lambda context:None))
    context = AdmissionContext(now=100,quote_at=100,account_at=100,quote_source='BINANCE_DEMO',model_source='assessment_v1',decision_expires_at=200,capability_state='DEMO_VERIFIED',code_sha='a'*40,qualified_sha='a'*40,private_stream_healthy=True,account_state='READY',authorized=True,budget='100',available='100',risk_limit='100',unresolved_unknown=0,unprotected=0)
    owner = DemoExecutionOwner(Repo(),Transport(),clock=lambda:100,physical_owner=physical)
    with pytest.raises(DemoError,match='DEMO_SEND_OUTCOME_UNKNOWN'): owner.dispatch(physical_scope,'cmd',context=context)
    assert events == ['MAY','UNKNOWN']


def _prepared_clean_fixture(tmp_path):
    from sqlalchemy import insert, update
    from dataclasses import asdict
    from trading_system.storage.demo import observations, inbox
    from trading_system.engine.binance_demo.contracts import digest
    from trading_system.engine.binance_demo.stream import _ClosedStreamProof, _STREAM_PROOF_ISSUER, DemoUserStreamConsumer
    from trading_system.storage.demo import final_activity_digest
    engine = create_engine(f'sqlite:///{tmp_path / "clean.sqlite"}'); metadata.create_all(engine)
    current = scope(); repo = DemoRepository(engine)
    repo.register_account(current,credential_ref='fixture',fingerprint='fp',budget='100')
    qualification = {'fixture':'not-venue-qualification'}
    with repo.transaction(current.account_key) as conn:
        conn.execute(update(accounts).values(state='RECOVERY_REQUIRED',qualification=qualification))
        conn.execute(insert(physical_owners).values(physical_key=current.physical_account_key,identity={'environment':current.environment,'venue':current.venue,'product':current.product,'account_id':current.account_id},account_key=current.account_key,generation=1,state='DIRTY',clean_evidence=None))
        conn.execute(insert(observations).values(observation_id='old-observation',account_key=current.account_key,scope_key=current.key,scope=asdict(current),source='REST_ORDER',credential_revision=0,stream_generation=1,payload_hash='f'*64,payload={'fixture':True},received_at=99))
        conn.execute(insert(inbox).values(observation_id='old-observation',state='PROCESSED',attempts=1))
        receipt = {'physical_key':current.physical_account_key,'account_key':current.account_key,'physical_generation':1,
            'credential_revision':0,'subscription_id':4,'stream_run_nonce':'fixture-run','qualification_hash':digest(qualification),
            'activity_hash':final_activity_digest(conn,current),'checked_at':100,'nonce':'a'*32}
        repo._event(conn,current.account_key,'FINAL_RECONCILIATION',receipt['nonce'],receipt)
    connection = engine.connect()
    authority = object()
    owner = PhysicalOwner(connection,current,-1,123,1,authority=authority)
    owner.validate = lambda scope: None
    stream = DemoUserStreamConsumer(repo,current,'fixture',request_id='s',physical_owner=owner)
    stream._last_closed_nonce = 'fixture-run'
    proof = _ClosedStreamProof(owner,current,1,0,4,stream=stream,run_nonce='fixture-run',_issuer=_STREAM_PROOF_ISSUER)
    stream._closed_proof = proof
    return engine,repo,current,connection,owner,proof,receipt,authority


def test_clean_transition_requires_matching_proof_and_atomic_snapshot(tmp_path):
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    handoff = owner.commit_clean(current,receipt,close_proof=proof,validate_sender=lambda:None,now=101)
    from trading_system.storage.demo_physical import _Handoff
    assert type(handoff) is _Handoff
    with engine.connect() as conn:
        row = conn.execute(select(physical_owners)).mappings().one()
        assert row['state'] == 'CLEAN' and row['clean_evidence']['generation'] == 1
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_RECOVERY_REQUIRED'):
        _reserve_new_dirty(engine,physical_owners,accounts,current)
    engine.dispose()


def test_clean_transition_rejects_activity_after_reconciliation(tmp_path):
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    repo.stage(current,command_id='late',request={'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000','timeInForce':'GTC','newClientOrderId':'late'},reserve_asset='USDT',reserve_amount='50',available='100',authorization_ref='fixture',expires_at=200,now=100)
    with pytest.raises(DemoError, match='DEMO_FINAL_ACTIVITY_CHANGED'):
        owner.commit_clean(current,receipt,close_proof=proof,validate_sender=lambda:None,now=101)
    with engine.connect() as conn: assert conn.execute(select(physical_owners.c.state)).scalar_one() == 'DIRTY'
    engine.dispose()


def test_uncertain_clean_commit_never_produces_handoff(tmp_path, monkeypatch):
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    original_commit = connection.commit
    def commit_then_fail(): original_commit(); raise OSError('commit acknowledgement lost')
    monkeypatch.setattr(connection,'commit',commit_then_fail)
    with pytest.raises(OSError,match='acknowledgement lost'):
        owner.commit_clean(current,receipt,close_proof=proof,validate_sender=lambda:None,now=101)
    # Even if CLEAN reached the database, no in-memory successful handoff
    # exists and the ordinary acquire path remains blocked.
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_RECOVERY_REQUIRED'):
        _reserve_new_dirty(engine,physical_owners,accounts,current)
    engine.dispose()


def test_acknowledged_same_process_handoff_advances_generation_once(tmp_path):
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    handoff = owner.commit_clean(current,receipt,close_proof=proof,validate_sender=lambda:None,now=101)
    assert _reserve_new_dirty(engine,physical_owners,accounts,current,authority=authority,handoff=handoff) == 2
    with engine.connect() as conn:
        row = conn.execute(select(physical_owners)).mappings().one()
        assert row['state'] == 'DIRTY' and row['generation'] == 2 and row['clean_evidence'] is None
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_RECOVERY_REQUIRED'):
        _reserve_new_dirty(engine,physical_owners,accounts,current,authority=authority,handoff=handoff)
    engine.dispose()


def test_clean_handoff_never_crosses_epoch_or_survives_missing_token(tmp_path):
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    handoff = owner.commit_clean(current,receipt,close_proof=proof,validate_sender=lambda:None,now=101)
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_RECOVERY_REQUIRED'):
        _reserve_new_dirty(engine,physical_owners,accounts,current)
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_RECOVERY_REQUIRED'):
        _reserve_new_dirty(engine,physical_owners,accounts,scope('other'),authority=authority,handoff=handoff)
    engine.dispose()


def test_fenced_same_process_rotation_invalidates_qualification_and_preserves_history(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from trading_system.storage.demo import observations, inbox
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    handoff = owner.commit_clean(current,receipt,close_proof=proof,validate_sender=lambda:None,now=101)
    assert _reserve_new_dirty(engine,physical_owners,accounts,current,authority=authority,handoff=handoff) == 2
    # SQLite stands in only for transaction-shape testing; no PG claim.
    class Owner:
        generation = 2
        def validate(self, scope): pass
    class Sender:
        def validate(self): pass
    @contextmanager
    def sender_guard(scope): yield Sender()
    original_transaction = repo.transaction
    @contextmanager
    def offline_transaction(key):
        engine.dialect.name = 'sqlite'
        try:
            with original_transaction(key) as conn:
                engine.dialect.name = 'postgresql'
                yield conn
        finally:
            engine.dialect.name = 'postgresql'
    monkeypatch.setattr(repo,'owner_guard',sender_guard)
    monkeypatch.setattr(repo,'transaction',offline_transaction)
    revision = repo.account(current)['revision']
    engine.dialect.name = 'postgresql'
    repo.rotate_credential(current,credential_ref='next-fixture',fingerprint='new',expected_revision=revision,physical_owner=Owner())
    engine.dialect.name = 'sqlite'
    account = repo.account(current)
    assert account['credential_revision'] == 1 and account['qualification'] is None
    assert account['state'] == 'RECOVERY_REQUIRED' and account['credential_ref'] == 'next-fixture'
    with engine.connect() as conn:
        assert conn.execute(select(physical_owners.c.generation)).scalar_one() == 2
        old = conn.execute(select(observations).where(observations.c.observation_id=='old-observation')).mappings().one()
        assert old['credential_revision'] == 0 and old['stream_generation'] == 1
    engine.dispose()


def test_database_evidence_and_new_service_cannot_recreate_handoff(tmp_path):
    import pickle
    from trading_system.storage.demo_physical import _Handoff
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    handoff = owner.commit_clean(current,receipt,close_proof=proof,validate_sender=lambda:None,now=101)
    with engine.connect() as conn: evidence = conn.execute(select(physical_owners.c.clean_evidence)).scalar_one()
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_RECOVERY_REQUIRED'):
        _reserve_new_dirty(engine,physical_owners,accounts,current,authority=object(),handoff=evidence)
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_RECOVERY_REQUIRED'):
        _reserve_new_dirty(engine,physical_owners,accounts,current,authority=object(),handoff=handoff)
    with pytest.raises(DemoError, match='DEMO_HANDOFF_CAPABILITY_REQUIRED'):
        _Handoff(object(),evidence)
    with pytest.raises(TypeError): pickle.dumps(handoff)
    assert _reserve_new_dirty(engine,physical_owners,accounts,current,authority=authority,handoff=handoff) == 2
    engine.dispose()


def test_stream_close_proof_cannot_be_directly_constructed():
    from trading_system.engine.binance_demo.stream import _ClosedStreamProof
    with pytest.raises(DemoError, match='DEMO_STREAM_CLOSE_PROOF_REQUIRED'):
        _ClosedStreamProof(object(),scope(),1,0,4)


def test_clean_rejects_proof_when_another_run_is_active(tmp_path):
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    proof.stream._active_run_nonce = 'another-run'
    with pytest.raises(DemoError, match='DEMO_STREAM_CLOSE_PROOF_REQUIRED'):
        owner.commit_clean(current,receipt,close_proof=proof,validate_sender=lambda:None,now=101)
    with engine.connect() as conn: assert conn.execute(select(physical_owners.c.state)).scalar_one() == 'DIRTY'
    owner.close(); engine.dispose()


def test_old_receipt_cannot_use_new_run_proof_with_same_subscription_id(tmp_path):
    from trading_system.engine.binance_demo.stream import _ClosedStreamProof, _STREAM_PROOF_ISSUER
    engine,repo,current,connection,owner,proof,receipt,authority = _prepared_clean_fixture(tmp_path)
    stream = proof.stream
    stream._last_closed_nonce = 'new-run'
    newer = _ClosedStreamProof(owner,current,1,0,4,stream=stream,run_nonce='new-run',_issuer=_STREAM_PROOF_ISSUER)
    stream._closed_proof = newer
    with pytest.raises(DemoError, match='DEMO_STREAM_CLOSE_PROOF_REQUIRED'):
        owner.commit_clean(current,receipt,close_proof=newer,validate_sender=lambda:None,now=101)
    with engine.connect() as conn: assert conn.execute(select(physical_owners.c.state)).scalar_one() == 'DIRTY'
    owner.close(); engine.dispose()
