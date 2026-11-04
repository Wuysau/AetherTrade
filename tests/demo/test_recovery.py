import asyncio
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
        async def send(self, payload):
            assert json.loads(payload) == {'id': 's'}
        async def recv(self):
            return json.dumps({'id': 's', 'status': 200, 'result': {'subscriptionId': 4}})
        def __aiter__(self):
            self.done = False
            return self
        async def __anext__(self):
            if self.done:
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
