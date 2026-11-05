import asyncio
from test_lifecycle import repo, scope
import json
import pytest
from sqlalchemy import create_engine, select
from trading_system.engine.binance_demo.contracts import DemoError, DemoScope
from trading_system.storage.demo import DemoRepository, metadata, inbox, reconciliations
from trading_system.engine.binance_demo.stream import DemoUserStreamConsumer
from trading_system.execution.demo_reconcile import DemoReconciler


@pytest.fixture
def setup(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'demo.sqlite3'))
    metadata.create_all(engine)
    repo = DemoRepository(engine)
    scope = DemoScope(account_id='account', account_epoch='e1', strategy_id='s1')
    repo.register_account(scope, credential_ref='demo-session:fixture', fingerprint='fp', budget='10000')
    yield repo, scope
    engine.dispose()


def test_stream_subscription_must_match_before_durable_ingest(setup):
    repo, scope = setup
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='subscribe-1')
    with pytest.raises(DemoError, match='SUBSCRIPTION_REQUIRED'):
        stream.ingest({'subscriptionId': 4, 'event': {'e': 'executionReport'}}, received_at=100)
    with pytest.raises(DemoError, match='SUBSCRIPTION_REJECTED'):
        stream.accept_subscription({'id': 'wrong', 'status': 200, 'result': {'subscriptionId': 4}})
    stream.accept_subscription({'id': 'subscribe-1', 'status': 200, 'result': {'subscriptionId': 4}})
    receipt = stream.ingest({'subscriptionId': 4, 'event': {'e': 'executionReport', 's': 'BTCUSDT'}}, received_at=100)
    with repo.engine.connect() as conn:
        assert conn.execute(select(inbox.c.state).where(inbox.c.observation_id == receipt)).scalar() == 'PENDING'
    with pytest.raises(DemoError, match='SUBSCRIPTION_MISMATCH'):
        stream.ingest({'subscriptionId': 9, 'event': {'e': 'executionReport'}}, received_at=101)
    stream.disconnected()
    assert stream.healthy is False
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'


def test_reconciliation_does_not_open_gap_or_implicitly_enable_execution(setup):
    repo, scope = setup
    reconciler = DemoReconciler(repo, transport=None)
    with pytest.raises(DemoError, match='STREAM_REQUIRED'):
        reconciler.reconcile(scope, symbol='BTCUSDT', stream=None, max_pages=2)
    assert repo.account(scope)['state'] == 'READ_ONLY'


def test_cursor_is_compare_and_swap_and_incomplete_stays_blocked(setup):
    repo, scope = setup
    repo.save_cursor(scope, 'BTCUSDT', {'from_id': 10}, complete=False, expected_revision=0)
    with pytest.raises(DemoError, match='CURSOR_CONFLICT'):
        repo.save_cursor(scope, 'BTCUSDT', {'from_id': 20}, complete=True, expected_revision=0)
    with repo.engine.connect() as conn:
        row = conn.execute(select(reconciliations)).mappings().one()
        assert row['complete'] is False
        assert row['cursor'] == {'from_id': 10}


def test_snapshot_differences_do_not_deposit_funds(setup):
    repo, scope = setup
    reconciler = DemoReconciler(repo, transport=None)
    reconciler.check_balances(scope, [{'asset': 'USDT', 'free': '100', 'locked': '0'}])
    assert repo.asset_deltas(scope) == {}
    with pytest.raises(DemoError, match='BALANCE_MISMATCH'):
        reconciler.check_balances(scope, [{'asset': 'USDT', 'free': '10000', 'locked': '0'}])
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'
    assert repo.asset_deltas(scope) == {}


def test_reconciler_rejects_other_accounts_credentials_before_query(setup):
    repo, scope = setup
    class WrongTransport:
        credential_ref = 'demo-session:wrong-account'
        def sync_time(self):
            pytest.fail('wrong account must be rejected before network')
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id': 's', 'status': 200, 'result': {'subscriptionId': 4}})
    with pytest.raises(DemoError, match='CREDENTIAL_BINDING'):
        DemoReconciler(repo, WrongTransport()).reconcile(scope, symbol='BTCUSDT', stream=stream)


def test_account_stream_routes_second_strategy_to_owner(setup):
    from dataclasses import replace
    repo, scope = setup
    other = replace(scope, strategy_id='s2')
    repo.stage(other, command_id='second', request={'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'LIMIT', 'quantity': '0.001', 'price': '50000', 'newClientOrderId': 'second', 'timeInForce': 'GTC'}, reserve_asset='USDT', reserve_amount='50', available='1000', authorization_ref='assessment-2', expires_at=200, now=100)
    repo.begin_send(other, 'second', expected_revision=0, now=101)
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id': 's', 'status': 200, 'result': {'subscriptionId': 4}})
    event = {'e': 'executionReport', 's': 'BTCUSDT', 'c': 'second', 'S': 'BUY', 'o': 'LIMIT', 'q': '0.001', 'p': '50000', 'x': 'NEW', 'X': 'NEW', 'i': 30, 'z': '0'}
    receipt = stream.ingest({'subscriptionId': 4, 'event': event}, received_at=101)
    from trading_system.execution.demo_reports import DemoReportVerifier
    DemoReportVerifier(repo).process(other, receipt)
    assert repo.command(other, 'second')['order_status'] == 'NEW'


def test_order_backfill_has_explicit_query_budget(setup):
    repo, scope = setup
    for index in range(3):
        repo.stage(scope, command_id=f'cmd-{index}', request={'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'LIMIT', 'quantity': '0.001', 'price': '50000', 'newClientOrderId': f'client-{index}', 'timeInForce': 'GTC'}, reserve_asset='USDT', reserve_amount='50', available='1000', authorization_ref='assessment', expires_at=200, now=100)
        repo.begin_send(scope, f'cmd-{index}', expected_revision=0, now=101)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self):
            pytest.fail('budget must be checked before any network request')
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id': 's', 'status': 200, 'result': {'subscriptionId': 4}})
    with pytest.raises(DemoError, match='ORDER_BUDGET'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream, max_order_queries=2)


def test_stream_drains_durable_frames_while_backfill_callback_runs(setup):
    from contextlib import asynccontextmanager
    from threading import Event
    repo, scope = setup
    receipt_ready = Event()
    original_observe = repo.observe
    def observe(*args, **kwargs):
        result = original_observe(*args, **kwargs)
        receipt_ready.set()
        return result
    repo.observe = observe
    class Transport:
        def sync_time(self):
            return {}
        def subscription(self, request_id):
            return {'id': request_id}
    class Socket:
        def __init__(self): self.closed = asyncio.Event()
        async def close(self): self.closed.set()
        async def send(self, payload):
            assert json.loads(payload) == {'id': 's'}
        async def recv(self):
            return json.dumps({'id': 's', 'status': 200, 'result': {'subscriptionId': 4}})
        def __aiter__(self):
            self.done = False
            return self
        async def __anext__(self):
            if self.done:
                await self.closed.wait()
                raise StopAsyncIteration
            self.done = True
            return json.dumps({'subscriptionId': 4, 'event': {'e': 'executionReport', 's': 'BTCUSDT', 'c': 'unbound'}})
    @asynccontextmanager
    async def socket_factory():
        yield Socket()
    def backfill(_):
        assert receipt_ready.wait(timeout=1), 'private ingestion was blocked by REST callback'
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    asyncio.run(stream.run_window(Transport(), duration_seconds=2, on_subscribed=backfill, socket_factory=socket_factory))
    assert receipt_ready.is_set()


def test_ready_requires_full_bound_reconciliation_not_page_completion(repo, scope):
    from trading_system.execution.demo_reconcile import DemoReconciler
    from trading_system.engine.binance_demo.contracts import digest
    from trading_system.storage.demo import accounts
    from sqlalchemy import update
    from test_lifecycle import register
    register(repo, scope)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *a, **kw): return []
        def account(self): return {'accountType': 'SPOT', 'balances': [{'asset': 'USDT', 'free': '1000', 'locked': '0'}]}
        def open_orders(self, symbol=None): return []
    class Stream:
        healthy = True
        credential_ref = 'demo-session:fixture'
        credential_revision = 0
        subscription_id = 1
    stream = Stream(); stream.scope = scope
    reconciler = DemoReconciler(repo, Transport(), clock=lambda: 100)
    with pytest.raises(DemoError, match='QUALIFICATION'):
        reconciler.reconcile_and_resume(scope, stream=stream, current_code_sha='a'*40)
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).values(qualification={'state': 'DEMO_VERIFIED', 'code_sha': 'a'*40, 'account_key': scope.account_key,
            'credential_revision': 0, 'product': 'SPOT', 'evidence_sha256': 'f'*64, 'official_contract_revision': 'offline-only',
            'baseline_hash': digest({'USDT': '1000'}), 'history_coverage': 'COMPLETE'}))
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_STREAM_BARRIER_UNQUALIFIED'):
        reconciler.reconcile_and_resume(scope, stream=stream, current_code_sha='a'*40)
    assert repo.account(scope)['state'] != 'READY'
    stream.healthy = False
    with pytest.raises(DemoError): reconciler.reconcile_and_resume(scope, stream=stream, current_code_sha='a'*40)


def test_rotation_cannot_strand_unprocessed_observation(setup):
    repo, scope = setup
    repo.observe(scope, source='WS_EXECUTION_REPORT', payload={'e': 'executionReport'}, received_at=100, credential_ref='demo-session:fixture')
    with pytest.raises(DemoError, match='INBOX_RECONCILIATION_REQUIRED'):
        repo.rotate_credential(scope, credential_ref='demo-session:rotated', fingerprint='new', expected_revision=0)
    assert repo.account(scope)['credential_ref'] == 'demo-session:fixture'
    assert repo.account(scope)['credential_revision'] == 0


def test_rotation_preserves_unknown_original_identity_and_epoch(setup):
    repo, scope = setup
    request = {'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'LIMIT', 'quantity': '0.001', 'price': '50000', 'newClientOrderId': 'original', 'timeInForce': 'GTC'}
    repo.stage(scope, command_id='original-command', request=request, reserve_asset='USDT', reserve_amount='50', available='1000', authorization_ref='grant', expires_at=200, now=100)
    repo.begin_send(scope, 'original-command', expected_revision=0, now=101)
    repo.mark_unknown(scope, 'original-command', 'LOST_RESPONSE')
    revision = repo.account(scope)['revision']
    repo.rotate_credential(scope, credential_ref='demo-session:rotated', fingerprint='new', expected_revision=revision)
    command = repo.command(scope, 'original-command')
    assert command['state'] == 'UNKNOWN'
    assert command['client_id'] == 'original' and command['request'] == request
    assert command['reserve_amount'] == '50'
    assert repo.account(scope)['state'] == 'READ_ONLY'
    assert repo.account(scope)['identity']['account_epoch'] == 'e1'


def test_postgres_rotation_is_blocked_without_cross_process_stream_fence(setup, monkeypatch):
    repo, scope = setup
    with monkeypatch.context() as patch:
        patch.setattr(repo.engine.dialect, 'name', 'postgresql')
        with pytest.raises(DemoError, match='REBIND_FENCING_UNQUALIFIED'):
            repo.rotate_credential(scope, credential_ref='demo-session:new', fingerprint='new', expected_revision=0)
    assert repo.account(scope)['credential_revision'] == 0
    assert repo.account(scope)['credential_ref'] == 'demo-session:fixture'


def test_stale_stream_revision_is_not_relabelled_as_new_credential(setup):
    from trading_system.storage.demo import observations
    repo, scope = setup
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    # Deliberately reuse the reference to expose why a revision, not just a
    # reference comparison, must bind every observation.
    repo.rotate_credential(scope, credential_ref='demo-session:fixture', fingerprint='next', expected_revision=0)
    event = {'e':'executionReport','s':'BTCUSDT','c':'unknown'}
    with pytest.raises(DemoError, match='DEMO_OBSERVATION_BINDING_REJECTED'):
        stream.ingest({'subscriptionId':4,'event':event}, received_at=101)
    with repo.engine.connect() as conn:
        assert conn.execute(select(observations)).first() is None


def test_stale_stream_revision_blocks_rest_backfill_before_network(setup):
    repo, scope = setup
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    repo.rotate_credential(scope, credential_ref='demo-session:fixture', fingerprint='next', expected_revision=0)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pytest.fail('stale stream must be rejected before network')
    with pytest.raises(DemoError, match='DEMO_CREDENTIAL_BINDING_REJECTED'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream)


def test_physical_account_identity_excludes_epoch_and_strategy():
    from dataclasses import replace
    first = DemoScope(account_id='123', account_epoch='first', strategy_id='one')
    other = replace(first, account_epoch='second', strategy_id='two')
    assert first.account_key != other.account_key
    assert first.physical_account_key == other.physical_account_key
    assert first.physical_account_key != replace(first, account_id='456').physical_account_key
    assert first.physical_account_key != replace(first, product='USDM').physical_account_key


def test_rotation_during_rest_page_cannot_advance_old_cursor(setup):
    repo, scope = setup
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs):
            current = repo.account(scope)
            repo.rotate_credential(scope, credential_ref='demo-session:fixture', fingerprint='next', expected_revision=current['revision'])
            return []
    with pytest.raises(DemoError, match='DEMO_CREDENTIAL_BINDING_REJECTED'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream)
    assert repo.cursor(scope, 'BTCUSDT')['revision'] == 0


def test_stream_credential_revision_cannot_be_reassigned(setup):
    repo, scope = setup
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    with pytest.raises(AttributeError):
        stream.credential_revision = 10


def test_lost_physical_owner_during_time_sync_stops_backfill(setup):
    repo, scope = setup
    class Owner:
        generation = 1; lost = False
        def validate(self, scope):
            if self.lost: raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
    owner = Owner(); stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s', physical_owner=owner)
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): owner.lost = True
        def trades(self, *args, **kwargs): pytest.fail('must not fetch after lock loss')
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_OWNER_LOST'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream)
    assert repo.cursor(scope,'BTCUSDT')['revision'] == 0


def test_lost_physical_owner_during_trade_read_does_not_advance_cursor(setup):
    repo, scope = setup
    class Owner:
        generation = 1; lost = False
        def validate(self, scope):
            if self.lost: raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
    owner = Owner(); stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s', physical_owner=owner)
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): owner.lost = True; return []
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_OWNER_LOST'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream)
    assert repo.cursor(scope,'BTCUSDT')['revision'] == 0


def test_lost_physical_owner_during_handshake_sends_no_subscription(setup):
    from contextlib import asynccontextmanager
    repo, scope = setup
    class Owner:
        generation = 1; lost = False
        def validate(self, scope):
            if self.lost: raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
    owner = Owner(); stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s', physical_owner=owner)
    class Transport:
        def sync_time(self): pass
        def subscription(self, request_id): pytest.fail('must not sign after owner loss')
    class Socket:
        async def send(self, payload): pytest.fail('must not send after owner loss')
    @asynccontextmanager
    async def socket_factory():
        owner.lost = True
        yield Socket()
    with pytest.raises(DemoError, match='DEMO_STREAM_INTERRUPTED'):
        asyncio.run(stream.run_window(Transport(), duration_seconds=1, socket_factory=socket_factory))


def test_lost_physical_owner_during_scope_resolution_cannot_archive(setup, monkeypatch):
    from trading_system.storage.demo import observations
    repo, scope = setup
    class Owner:
        generation = 1; lost = False
        def validate(self, scope):
            if self.lost: raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
    owner = Owner(); stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s', physical_owner=owner)
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    def resolve(*args, **kwargs): owner.lost = True; return scope
    monkeypatch.setattr(repo, 'resolve_order_scope', resolve)
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_OWNER_LOST'):
        stream.ingest({'subscriptionId':4,'event':{'e':'executionReport','s':'BTCUSDT','c':'id'}}, received_at=100)
    with repo.engine.connect() as conn: assert conn.execute(select(observations)).first() is None


def test_lock_loss_during_observation_commit_preserves_fact_but_interrupts_stream(setup, monkeypatch):
    from trading_system.storage.demo import observations
    repo, scope = setup
    class Owner:
        generation = 1; lost = False
        def validate(self, scope):
            if self.lost: raise DemoError('DEMO_PHYSICAL_OWNER_LOST')
    owner = Owner(); stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s', physical_owner=owner)
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    monkeypatch.setattr(repo, 'resolve_order_scope', lambda *args, **kwargs:scope)
    observe = repo.observe
    def commit_then_lose(*args, **kwargs):
        receipt = observe(*args, **kwargs); owner.lost = True; return receipt
    monkeypatch.setattr(repo, 'observe', commit_then_lose)
    with pytest.raises(DemoError, match='DEMO_PHYSICAL_OWNER_LOST'):
        stream.ingest({'subscriptionId':4,'event':{'e':'executionReport','s':'BTCUSDT','c':'id'}},received_at=100)
    with repo.engine.connect() as conn:
        row = conn.execute(select(observations)).mappings().one()
        assert row['credential_revision'] == 0 and row['stream_generation'] == 1


def test_time_sync_crossing_window_does_not_open_socket(setup):
    from contextlib import asynccontextmanager
    from threading import Event
    repo, scope = setup
    entered, release = Event(), Event()
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    class Transport:
        def sync_time(self): entered.set(); assert release.wait(3)
    @asynccontextmanager
    async def socket_factory():
        pytest.fail('expired window must not open a socket')
        yield None
    async def scenario():
        task = asyncio.create_task(stream.run_window(Transport(),duration_seconds=1,socket_factory=socket_factory))
        assert await asyncio.to_thread(entered.wait,2)
        await asyncio.sleep(1.05); release.set()
        with pytest.raises(DemoError, match='DEMO_STREAM_SUBSCRIPTION_TIMEOUT'): await task
    asyncio.run(scenario())


def test_final_reconcile_records_generation_bound_nonready_snapshot(tmp_path, monkeypatch):
    from trading_system.engine.binance_demo.contracts import digest
    from trading_system.storage.demo import accounts, outbox, final_activity_digest
    from sqlalchemy import update
    engine = create_engine('sqlite:///' + str(tmp_path/'final.sqlite')); metadata.create_all(engine)
    repo = DemoRepository(engine); scope = DemoScope(account_id='123',account_epoch='e',strategy_id='s')
    repo.register_account(scope,credential_ref='demo-session:fixture',fingerprint='fp',budget='10000')
    repo.balance_baseline(scope, {'USDT':'1000'})
    qualification = {'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,'credential_revision':0,'product':'SPOT','history_coverage':'COMPLETE','baseline_hash':digest({'USDT':'1000'}),'evidence_sha256':'f'*64,'official_contract_revision':'fixture-only'}
    with repo.transaction(scope.account_key) as conn: conn.execute(update(accounts).values(qualification=qualification))
    class Owner:
        generation = 1
        def validate(self, scope): pass
    stream = DemoUserStreamConsumer(repo,scope,'demo-session:fixture',request_id='s',physical_owner=Owner())
    stream._begin_run()
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self,*args,**kwargs): return []
        def open_orders(self,*args): return []
        def account(self): return {'uid':int(scope.account_id) if scope.account_id.isdigit() else None,'accountType':'SPOT','balances':[{'asset':'USDT','free':'1000','locked':'0'}]}
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_STREAM_BARRIER_UNQUALIFIED'):
        DemoReconciler(repo,Transport(),clock=lambda:100).reconcile_for_clean(scope,stream=stream,current_code_sha='a'*40)
    # Offline-only seam exercises receipt activity binding below; never venue evidence.
    import trading_system.execution.demo_reconcile as barrier
    monkeypatch.setattr(barrier, 'require_qualified_stream_barrier', lambda: None)
    receipt = DemoReconciler(repo,Transport(),clock=lambda:100).reconcile_for_clean(scope,stream=stream,current_code_sha='a'*40)
    assert receipt['physical_generation'] == 1 and receipt['credential_revision'] == 0
    assert receipt['stream_run_nonce'] == stream.active_run_nonce
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'
    with repo.transaction(scope.account_key) as conn:
        assert receipt['activity_hash'] == final_activity_digest(conn,scope)
        assert conn.execute(select(outbox).where(outbox.c.kind=='FINAL_RECONCILIATION')).first() is not None
    stream.ingest({'subscriptionId':4,'event':{'e':'outboundAccountPosition','E':101000}},received_at=101)
    with repo.transaction(scope.account_key) as conn:
        assert receipt['activity_hash'] != final_activity_digest(conn,scope)
        assert conn.execute(select(inbox.c.state).where(inbox.c.state=='CONFLICT')).first() is not None
    engine.dispose()


def test_completed_supervision_closes_and_joins_private_receiver(setup):
    from contextlib import asynccontextmanager
    repo, scope = setup; events = []
    stream = DemoUserStreamConsumer(repo,scope,'demo-session:fixture',request_id='s')
    class Transport:
        def sync_time(self): pass
        def subscription(self, request_id): return {'id':request_id}
    class Socket:
        def __init__(self): self.closed = asyncio.Event()
        async def send(self, payload): events.append('subscribed')
        async def recv(self): return json.dumps({'id':'s','status':200,'result':{'subscriptionId':4}})
        async def close(self): events.append('socket-close'); self.closed.set()
        def __aiter__(self): return self
        async def __anext__(self): await self.closed.wait(); events.append('receiver-ended'); raise StopAsyncIteration
    @asynccontextmanager
    async def factory():
        yield Socket()
    async def final(_stream):
        assert _stream.healthy; events.append('final-reconcile')
    asyncio.run(stream.run_window(Transport(),duration_seconds=2,on_subscribed=final,socket_factory=factory))
    assert events == ['subscribed','final-reconcile','socket-close','receiver-ended']
    assert not stream.healthy


def test_final_reconcile_rejects_frozen_account_before_any_network(setup):
    from sqlalchemy import update
    from trading_system.engine.binance_demo.contracts import digest
    from trading_system.storage.demo import accounts, outbox
    repo, scope = setup
    repo.balance_baseline(scope, {'USDT':'1000'})
    evidence = {'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,'credential_revision':0,'product':'SPOT','history_coverage':'COMPLETE','baseline_hash':digest({'USDT':'1000'}),'evidence_sha256':'f'*64,'official_contract_revision':'fixture'}
    with repo.transaction(scope.account_key) as conn: conn.execute(update(accounts).values(qualification=evidence,frozen=True))
    class Owner:
        generation = 1
        def validate(self, scope): pass
    stream = DemoUserStreamConsumer(repo,scope,'demo-session:fixture',request_id='s',physical_owner=Owner())
    stream._begin_run()
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pytest.fail('frozen account must not be reconciled as clean')
    with pytest.raises(DemoError, match='DEMO_QUALIFICATION_BASELINE_REQUIRED'):
        DemoReconciler(repo,Transport(),clock=lambda:100).reconcile_for_clean(scope,stream=stream,current_code_sha='a'*40)
    with repo.engine.connect() as conn: assert conn.execute(select(outbox).where(outbox.c.kind=='FINAL_RECONCILIATION')).first() is None


def test_remote_normal_close_cannot_issue_local_close_proof(setup):
    from contextlib import asynccontextmanager
    repo, scope = setup
    class Owner:
        generation = 1
        def validate(self, scope): pass
    stream = DemoUserStreamConsumer(repo,scope,'demo-session:fixture',request_id='s',physical_owner=Owner())
    class Transport:
        def sync_time(self): pass
        def subscription(self, request_id): return {'id':request_id}
    class Socket:
        async def send(self, payload): pass
        async def recv(self): return json.dumps({'id':'s','status':200,'result':{'subscriptionId':4}})
        async def close(self): pass
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
    @asynccontextmanager
    async def factory(): yield Socket()
    async def final(_): await asyncio.Event().wait()
    with pytest.raises(DemoError, match='DEMO_STREAM_INTERRUPTED'):
        asyncio.run(stream.run_window(Transport(),duration_seconds=2,on_subscribed=final,socket_factory=factory))
    assert not stream.healthy and stream.closed_proof is None


def test_one_consumer_cannot_run_two_sockets_or_reuse_sealed_proof(setup):
    from contextlib import asynccontextmanager
    repo, scope = setup; socket_count = []; closed = asyncio.Event(); release = asyncio.Event()
    class Owner:
        generation = 1
        def validate(self, scope): pass
    owner = Owner(); stream = DemoUserStreamConsumer(repo,scope,'demo-session:fixture',request_id='s',physical_owner=owner)
    class Transport:
        def sync_time(self): pass
        def subscription(self, request_id): return {'id':request_id}
    class Socket:
        async def send(self, payload): pass
        async def recv(self): return json.dumps({'id':'s','status':200,'result':{'subscriptionId':4}})
        async def close(self): closed.set()
        def __aiter__(self): return self
        async def __anext__(self): await closed.wait(); raise StopAsyncIteration
    @asynccontextmanager
    async def factory(): socket_count.append(True); yield Socket()
    async def final(_stream): await release.wait()
    async def scenario():
        first = asyncio.create_task(stream.run_window(Transport(),duration_seconds=2,on_subscribed=final,socket_factory=factory))
        for _ in range(100):
            if stream.healthy: break
            await asyncio.sleep(0.01)
        assert stream.active_run_count == 1
        with pytest.raises(DemoError,match='DEMO_STREAM_WINDOW_ACTIVE'):
            await stream.run_window(Transport(),duration_seconds=2,on_subscribed=final,socket_factory=factory)
        assert len(socket_count) == 1
        release.set(); await first
        assert stream.active_run_count == 0 and stream.closed_proof is not None
        stream.closed_proof.consume(owner,scope,1,0,4)
        with pytest.raises(DemoError,match='DEMO_STREAM_SEALED'):
            await stream.run_window(Transport(),duration_seconds=2,on_subscribed=final,socket_factory=factory)
    asyncio.run(scenario())


def test_unresolved_history_is_durable_blocker_and_never_strategy_fill(setup):
    from trading_system.storage.demo import outbox, fills, postings
    repo, scope = setup
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs):
            return [{'id':12, 'orderId':345, 'symbol':'BTCUSDT', 'qty':'0.01'}]
    with pytest.raises(DemoError, match='DEMO_UNRESOLVED_TRADE_HISTORY_REVIEW_REQUIRED'):
        DemoReconciler(repo, Transport(), clock=lambda:100).reconcile(scope, symbol='BTCUSDT', stream=stream)
    assert repo.cursor(scope, 'BTCUSDT')['revision'] == 0
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'
    with repo.engine.connect() as conn:
        rows = conn.execute(select(outbox).where(outbox.c.kind == 'UNRESOLVED_TRADE_HISTORY')).mappings().all()
        assert len(rows) == 1 and rows[0]['payload']['symbol'] == 'BTCUSDT'
        assert rows[0]['payload']['trade_hash'] and 'orderId' not in rows[0]['payload']
        assert conn.execute(select(fills)).first() is None
        assert conn.execute(select(postings)).first() is None


def test_recorded_unresolved_trade_blocks_later_ready_even_if_next_page_is_empty(setup):
    from sqlalchemy import update
    from trading_system.engine.binance_demo.contracts import digest
    from trading_system.storage.demo import accounts
    repo, scope = setup
    repo.record_unresolved_trade(scope, symbol='BTCUSDT', trade={'id':12, 'orderId':345},
        credential_ref='demo-session:fixture', credential_revision=0, stream_generation=None)
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).values(qualification={'state':'DEMO_VERIFIED','code_sha':'a'*40,
            'account_key':scope.account_key,'credential_revision':0,'product':'SPOT',
            'evidence_sha256':'f'*64,'official_contract_revision':'offline-fixture',
            'baseline_hash':digest({'USDT':'1000'}),'history_coverage':'COMPLETE'}))
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *a, **kw): return []
        def account(self): return {'accountType':'SPOT','balances':[{'asset':'USDT','free':'1000','locked':'0'}]}
        def open_orders(self, *a): return []
    class Stream:
        healthy = True
        credential_ref = 'demo-session:fixture'
        credential_revision = 0
        subscription_id = 1
    stream = Stream(); stream.scope = scope
    with pytest.raises(DemoError, match='DEMO_RECOVERY_REQUIRED'):
        DemoReconciler(repo, Transport(), clock=lambda:100).reconcile_and_resume(scope, stream=stream, current_code_sha='a'*40)
    assert repo.account(scope)['state'] != 'READY'


def test_unresolved_history_fences_entry_but_not_cancel(setup):
    repo, scope = setup
    order = {'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000',
             'newClientOrderId':'original','timeInForce':'GTC'}
    repo.stage(scope, command_id='before', request=order, reserve_asset='USDT', reserve_amount='50',
               available='1000', authorization_ref='fixture', expires_at=200, now=100)
    repo.record_unresolved_trade(scope, symbol='BTCUSDT', trade={'id':9,'orderId':88},
        credential_ref='demo-session:fixture', credential_revision=0, stream_generation=None)
    with pytest.raises(DemoError, match='DEMO_UNRESOLVED_TRADE_HISTORY_REVIEW_REQUIRED'):
        repo.stage(scope, command_id='after', request={**order,'newClientOrderId':'other'}, reserve_asset='USDT',
                   reserve_amount='50', available='1000', authorization_ref='fixture', expires_at=200, now=100)
    # Unresolved history must not disable independently authorized cancellation.
    result = repo.stage(scope, command_id='cancel', request={'symbol':'BTCUSDT','origClientOrderId':'original'},
                        reserve_asset='USDT', reserve_amount='0', available='1000', authorization_ref='fixture',
                        expires_at=200, now=100, method='DELETE', purpose='CANCEL')
    assert result['state'] == 'DURABLE_READY'
    reduction = repo.stage(scope, command_id='reduce', request={**order, 'side':'SELL', 'newClientOrderId':'reduce'},
        reserve_asset='BTC', reserve_amount='0.001', available='0.001', authorization_ref='fixture',
        expires_at=200, now=100, purpose='REDUCTION')
    assert reduction['state'] == 'DURABLE_READY'  # Dispatch still verifies owned exposure.


def test_unresolved_history_rejects_stale_credential_before_recording(setup):
    from trading_system.storage.demo import outbox
    repo, scope = setup
    with pytest.raises(DemoError, match='DEMO_CREDENTIAL_BINDING_REJECTED'):
        repo.record_unresolved_trade(scope, symbol='BTCUSDT', trade={'id':9,'orderId':88},
            credential_ref='demo-session:fixture', credential_revision=1, stream_generation=None)
    with repo.engine.connect() as conn:
        assert not conn.execute(select(outbox).where(outbox.c.kind=='UNRESOLVED_TRADE_HISTORY')).first()


def test_backfill_rechecks_order_binding_under_lock_before_latching_unresolved(setup, monkeypatch):
    from trading_system.storage.demo import outbox, fills
    repo, scope = setup
    request = {'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000',
               'newClientOrderId':'original','timeInForce':'GTC'}
    repo.stage(scope, command_id='cmd', request=request, reserve_asset='USDT', reserve_amount='50',
               available='1000', authorization_ref='fixture', expires_at=200, now=100)
    repo.begin_send(scope, 'cmd', expected_revision=0, now=101)
    def first_lookup(*args, **kwargs):
        repo.acknowledge(scope, 'cmd', {'symbol':'BTCUSDT','clientOrderId':'original','orderId':345})
        raise DemoError('DEMO_ORDER_BINDING_REJECTED')
    monkeypatch.setattr(repo, 'resolve_order_scope', first_lookup)
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def query_order(self, *args):
            return {'symbol':'BTCUSDT','clientOrderId':'original','side':'BUY','type':'LIMIT','origQty':'0.001','price':'50000','orderId':345,'status':'NEW','executedQty':'0','cummulativeQuoteQty':'0'}
        def trades(self, *args, **kwargs):
            return [{'id':12,'orderId':345,'symbol':'BTCUSDT','qty':'0.001','price':'50000','quoteQty':'50',
                     'commission':'0','commissionAsset':'USDT','isBuyer':True,'time':100000}]
    # Isolate the race at the trade lookup rather than the earlier pending-order query.
    monkeypatch.setattr(repo, 'pending_commands', lambda *args, **kwargs: [])
    DemoReconciler(repo, Transport(), clock=lambda:100).reconcile(scope, symbol='BTCUSDT', stream=stream)
    with repo.engine.connect() as conn:
        assert conn.execute(select(fills)).first() is not None
        assert conn.execute(select(outbox).where(outbox.c.kind=='UNRESOLVED_TRADE_HISTORY')).first() is None


def test_unresolved_history_clears_only_after_exact_trade_is_verified_for_original_order(setup, monkeypatch):
    repo, scope = setup
    request = {'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000',
               'newClientOrderId':'original','timeInForce':'GTC'}
    repo.stage(scope, command_id='cmd', request=request, reserve_asset='USDT', reserve_amount='50',
               available='1000', authorization_ref='fixture', expires_at=200, now=100)
    repo.begin_send(scope, 'cmd', expected_revision=0, now=101)
    trade = {'id':12,'orderId':345,'symbol':'BTCUSDT','qty':'0.001','price':'50000','quoteQty':'50',
             'commission':'0','commissionAsset':'USDT','isBuyer':True,'time':100000}
    repo.record_unresolved_trade(scope, symbol='BTCUSDT', trade=trade,
        credential_ref='demo-session:fixture', credential_revision=0, stream_generation=None)
    assert len(repo.operator_view(scope)['unresolved_trade_history']) == 1
    repo.acknowledge(scope, 'cmd', {'symbol':'BTCUSDT','clientOrderId':'original','orderId':345})
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): return [trade]
    monkeypatch.setattr(repo, 'pending_commands', lambda *args, **kwargs: [])
    DemoReconciler(repo, Transport(), clock=lambda:100).reconcile(scope, symbol='BTCUSDT', stream=stream)
    assert repo.operator_view(scope)['unresolved_trade_history'] == []
    assert repo.operator_view(scope)['fills'][0]['order_id'] == '345'
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'  # No automatic READY.


def test_different_trade_fact_does_not_clear_unresolved_marker(setup, monkeypatch):
    repo, scope = setup
    request = {'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000',
               'newClientOrderId':'original','timeInForce':'GTC'}
    repo.stage(scope, command_id='cmd', request=request, reserve_asset='USDT', reserve_amount='50',
               available='1000', authorization_ref='fixture', expires_at=200, now=100)
    repo.begin_send(scope, 'cmd', expected_revision=0, now=101)
    trade = {'id':12,'orderId':345,'symbol':'BTCUSDT','qty':'0.001','price':'50000','quoteQty':'50',
             'commission':'0','commissionAsset':'USDT','isBuyer':True,'time':100000}
    repo.record_unresolved_trade(scope, symbol='BTCUSDT', trade={**trade, 'id':13},
        credential_ref='demo-session:fixture', credential_revision=0, stream_generation=None)
    repo.acknowledge(scope, 'cmd', {'symbol':'BTCUSDT','clientOrderId':'original','orderId':345})
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): return [trade]
    monkeypatch.setattr(repo, 'pending_commands', lambda *args, **kwargs: [])
    DemoReconciler(repo, Transport(), clock=lambda:100).reconcile(scope, symbol='BTCUSDT', stream=stream)
    assert len(repo.operator_view(scope)['unresolved_trade_history']) == 1
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'


def test_first_history_backfill_starts_at_zero_without_claiming_full_baseline(setup):
    repo, scope = setup
    class Transport:
        credential_ref = 'demo-session:fixture'
        seen = []
        def sync_time(self): pass
        def trades(self, symbol, *, from_id, limit):
            self.seen.append(from_id)
            return []
    transport = Transport()
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    result = DemoReconciler(repo, transport).reconcile(scope, symbol='BTCUSDT', stream=stream)
    assert transport.seen == [0]
    assert result['execution_authorized'] is False and result['state'] == 'RECOVERY_REQUIRED'


def test_legacy_cursor_without_known_start_cannot_be_silently_promoted(setup):
    repo, scope = setup
    repo.save_cursor(scope, 'BTCUSDT', {'from_id':None}, complete=True, expected_revision=0)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): pytest.fail('legacy cursor needs review before backfill')
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    with pytest.raises(DemoError, match='DEMO_HISTORY_CURSOR_UNVERIFIED'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream)


def test_legacy_nonempty_cursor_cannot_assert_full_history(setup):
    repo, scope = setup
    repo.save_cursor(scope, 'BTCUSDT', {'from_id':900}, complete=True, expected_revision=0)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): pytest.fail('unproven legacy cursor')
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    with pytest.raises(DemoError, match='DEMO_HISTORY_CURSOR_UNVERIFIED'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream)


@pytest.mark.parametrize('page', [[], [{'id':11,'orderId':1}], [{'id':9,'orderId':1}, {'id':10,'orderId':2}]])
def test_backfill_rejects_lost_or_regressed_overlap_anchor(setup, page):
    repo, scope = setup
    repo.save_cursor(scope, 'BTCUSDT', {'from_id':10,'started_at_zero':True,'overlap_required':True}, complete=True, expected_revision=0)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): return page
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    with pytest.raises(DemoError, match='DEMO_BACKFILL_OVERLAP_LOST'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream)
    stored = repo.cursor(scope, 'BTCUSDT')
    assert stored['cursor']['from_id'] == 10 and stored['complete'] is False


def test_backfill_rejects_single_item_page_budget_before_network(setup):
    repo, scope = setup
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pytest.fail('invalid page budget must fail before network')
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    with pytest.raises(DemoError, match='DEMO_BACKFILL_BUDGET_INVALID'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream, page_size=1)


def test_backfill_rejects_response_larger_than_requested(setup):
    repo, scope = setup
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): return [{'id':1},{'id':2},{'id':3}]
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    with pytest.raises(DemoError, match='DEMO_BACKFILL_RESPONSE_INVALID'):
        DemoReconciler(repo, Transport()).reconcile(scope, symbol='BTCUSDT', stream=stream, page_size=2)
    assert repo.cursor(scope, 'BTCUSDT')['revision'] == 0


def test_unresolved_trade_keeps_bounded_economic_facts_without_strategy_fill(setup):
    from trading_system.storage.demo import outbox, fills
    repo, scope = setup
    trade = {'symbol':'BTCUSDT','id':12,'orderId':345,'orderListId':-1,'price':'50000','qty':'0.001',
             'quoteQty':'50','commission':'0.01','commissionAsset':'USDT','time':100000,
             'isBuyer':True,'isMaker':False,'isBestMatch':True}
    repo.record_unresolved_trade(scope, symbol='BTCUSDT', trade=trade,
        credential_ref='demo-session:fixture', credential_revision=0, stream_generation=None)
    with repo.engine.connect() as conn:
        event = conn.execute(select(outbox).where(outbox.c.kind=='UNRESOLVED_TRADE_HISTORY')).mappings().one()
        assert event['payload']['trade_fact'] == trade
        assert conn.execute(select(fills)).first() is None


def test_unresolved_trade_drops_untrusted_secret_text(setup):
    from trading_system.storage.demo import outbox
    repo, scope = setup
    trade = {'symbol':'BTCUSDT','id':12,'orderId':345,'price':'secret-value','apiKey':'secret-value'}
    repo.record_unresolved_trade(scope, symbol='BTCUSDT', trade=trade,
        credential_ref='demo-session:fixture', credential_revision=0, stream_generation=None)
    with repo.engine.connect() as conn:
        event = conn.execute(select(outbox).where(outbox.c.kind=='UNRESOLVED_TRADE_HISTORY')).mappings().one()
        assert 'secret-value' not in str(event['payload'])
        assert event['payload']['trade_fact_complete'] is False


def test_empty_inbox_and_old_qualification_cannot_set_ready_without_stream_barrier(repo, scope):
    from sqlalchemy import update
    from trading_system.engine.binance_demo.contracts import digest
    from trading_system.storage.demo import accounts
    from test_lifecycle import register
    register(repo, scope)
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).values(qualification={'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,
            'credential_revision':0,'product':'SPOT','history_coverage':'COMPLETE','baseline_hash':digest({'USDT':'1000'}),
            'evidence_sha256':'f'*64,'official_contract_revision':'fixture-only'}))
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self,*args,**kwargs): return []
        def open_orders(self,*args): return []
        def account(self): return {'accountType':'SPOT','balances':[{'asset':'USDT','free':'1000','locked':'0'}]}
    class Stream:
        healthy=True; credential_ref='demo-session:fixture'; credential_revision=0; subscription_id=1
    stream=Stream(); stream.scope=scope
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_STREAM_BARRIER_UNQUALIFIED'):
        DemoReconciler(repo, Transport(), clock=lambda:100).reconcile_and_resume(scope, stream=stream, current_code_sha='a'*40)
    assert repo.account(scope)['state'] != 'READY'
