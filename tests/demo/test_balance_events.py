"""Offline account-event handling; fixtures are not venue qualification."""
import pytest
from sqlalchemy import create_engine, select
from trading_system.engine.binance_demo.contracts import DemoError, DemoScope
from trading_system.engine.binance_demo.stream import DemoUserStreamConsumer
from trading_system.storage.demo import DemoRepository, metadata, inbox, observations, postings
from trading_system.execution.demo_reconcile import DemoReconciler


@pytest.fixture
def setup(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "balance.sqlite"}'); metadata.create_all(engine)
    repo = DemoRepository(engine)
    scope = DemoScope(account_id='123', account_epoch='e1', strategy_id='s1')
    repo.register_account(scope, credential_ref='demo-session:fixture', fingerprint='fp', budget='1000')
    repo.balance_baseline(scope, {'BTC':'0.5', 'USDT':'100'})
    stream = DemoUserStreamConsumer(repo, scope, 'demo-session:fixture', request_id='s')
    stream.accept_subscription({'id':'s','status':200,'result':{'subscriptionId':4}})
    yield repo, scope, stream
    engine.dispose()


def position(*, free='100', locked='0', update=1000, event=1001):
    return {'e':'outboundAccountPosition','E':event,'u':update,'B':[{'a':'USDT','f':free,'l':locked}]}


def test_account_position_identical_frames_are_both_preserved_without_event_id(setup):
    repo, scope, stream = setup
    message = {'subscriptionId':4,'event':position()}
    first = stream.ingest(message, received_at=2)
    assert stream.ingest(message, received_at=3) != first
    with repo.engine.connect() as conn:
        rows = conn.execute(select(observations, inbox.c.state).join(inbox, inbox.c.observation_id == observations.c.observation_id)).mappings().all()
        assert len(rows) == 2 and all(row['source'] == 'WS_ACCOUNT_POSITION' and row['state'] == 'PENDING' for row in rows)
    assert repo.health(scope)['pending_inbox'] == 2


def test_balance_delta_remains_conflict_not_invented_posting(setup):
    repo, scope, stream = setup
    stream.ingest({'subscriptionId':4,'event':{'e':'balanceUpdate','E':1001,'a':'USDT','d':'10','T':1000}}, received_at=2)
    with repo.engine.connect() as conn:
        assert conn.execute(select(inbox.c.state)).scalar_one() == 'CONFLICT'
        assert conn.execute(select(postings)).first() is None


def test_order_inbox_drain_leaves_account_position_pending_for_reconciliation(setup):
    from trading_system.runtime.demo_strategy import DemoStrategyHost
    repo, scope, stream = setup
    stream.ingest({'subscriptionId':4,'event':position()}, received_at=2)
    host = DemoStrategyHost(repo, scope, frame_loader=lambda: {}, model_service=None, execution_owner=None, current_code_sha='a'*40)
    assert host.drain_inbox() == {'processed':0, 'remaining':1}
    with repo.engine.connect() as conn:
        assert conn.execute(select(inbox.c.state)).scalar_one() == 'PENDING'


def test_matching_snapshot_is_recorded_as_unqualified_and_leaves_pending(setup):
    from trading_system.storage.demo import outbox
    repo, scope, stream = setup
    stream._begin_run()
    stream.ingest({'subscriptionId':4,'event':position()}, received_at=2)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def account(self):
            return {'uid':123,'accountType':'SPOT','updateTime':1000,'balances':[
                {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'100','locked':'0'}]}
    result = DemoReconciler(repo, Transport(), clock=lambda:2).review_account_positions(scope, stream=stream)
    assert result['state'] == 'MATCHED_UNQUALIFIED' and result['execution_authorized'] is False
    assert repo.health(scope)['pending_inbox'] == 1
    with repo.engine.connect() as conn:
        assert conn.execute(select(postings)).first() is None
        assert conn.execute(select(outbox).where(outbox.c.kind=='ACCOUNT_POSITION_REVIEW')).first()


def test_mismatched_or_stale_snapshot_cannot_record_success(setup):
    from trading_system.storage.demo import outbox
    repo, scope, stream = setup
    stream._begin_run()
    stream.ingest({'subscriptionId':4,'event':position()}, received_at=2)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def account(self):
            return {'uid':123,'accountType':'SPOT','updateTime':999,'balances':[
                {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'100','locked':'0'}]}
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_POSITION_SNAPSHOT_STALE'):
        DemoReconciler(repo, Transport(), clock=lambda:2).review_account_positions(scope, stream=stream)
    assert repo.health(scope)['pending_inbox'] == 1
    with repo.engine.connect() as conn:
        assert conn.execute(select(outbox).where(outbox.c.kind=='ACCOUNT_POSITION_REVIEW')).first() is None


def test_recovery_backfill_runs_account_position_review_without_clearing_it(setup):
    repo, scope, stream = setup
    stream._begin_run()
    stream.ingest({'subscriptionId':4,'event':position()}, received_at=2)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): return []
        def account(self):
            return {'uid':123,'accountType':'SPOT','updateTime':1000,'balances':[
                {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'100','locked':'0'}]}
    result = DemoReconciler(repo, Transport(), clock=lambda:2).reconcile(scope, symbol='BTCUSDT', stream=stream)
    assert result['account_position_review']['state'] == 'MATCHED_UNQUALIFIED'
    assert result['execution_authorized'] is False and repo.health(scope)['pending_inbox'] == 1


def test_different_older_frame_is_not_silently_hidden_by_newer_snapshot(setup):
    repo, scope, stream = setup
    stream._begin_run()
    stream.ingest({'subscriptionId':4,'event':position(free='90',update=900,event=901)}, received_at=2)
    stream.ingest({'subscriptionId':4,'event':position(free='100',update=1000,event=1001)}, received_at=2)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def account(self):
            return {'uid':123,'accountType':'SPOT','updateTime':1000,'balances':[
                {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'100','locked':'0'}]}
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_POSITION_MISMATCH'):
        DemoReconciler(repo, Transport(), clock=lambda:2).review_account_positions(scope, stream=stream)
    assert repo.health(scope)['pending_inbox'] == 2


@pytest.mark.parametrize('bad', [
    {'e':'outboundAccountPosition','E':1001},
    {'e':'outboundAccountPosition','E':1001,'u':1000,'B':[{'a':'USDT','f':'-1','l':'0'}]},
    {'e':'outboundAccountPosition','E':1001,'u':1000,'B':[{'a':'USDT','f':'1','l':'0'},{'a':'USDT','f':'1','l':'0'}]},
])
def test_malformed_account_position_is_preserved_as_conflict(setup, bad):
    repo, scope, stream = setup
    stream.ingest({'subscriptionId':4,'event':bad}, received_at=2)
    with repo.engine.connect() as conn:
        row = conn.execute(select(observations, inbox.c.state).join(inbox, inbox.c.observation_id == observations.c.observation_id)).mappings().one()
        assert row['source'] == 'WS_UNSUPPORTED_EVENT' and row['state'] == 'CONFLICT'
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'


def test_snapshot_match_does_not_enable_ready_or_clear_pending(setup):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    from trading_system.engine.binance_demo.contracts import digest
    repo, scope, stream = setup
    stream._begin_run()
    stream.ingest({'subscriptionId':4,'event':position()}, received_at=2)
    proof = {'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,'credential_revision':0,
             'product':'SPOT','history_coverage':'COMPLETE','baseline_hash':digest({'BTC':'0.5','USDT':'100'}),
             'evidence_sha256':'f'*64,'official_contract_revision':'fixture-only'}
    with repo.transaction(scope.account_key) as conn: conn.execute(update(accounts).values(qualification=proof))
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def trades(self, *args, **kwargs): return []
        def open_orders(self, *args): return []
        def account(self):
            return {'uid':123,'accountType':'SPOT','updateTime':1000,'balances':[
                {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'100','locked':'0'}]}
    with pytest.raises(DemoError, match='DEMO_RECOVERY_REQUIRED'):
        DemoReconciler(repo, Transport(), clock=lambda:2).reconcile_and_resume(scope, stream=stream, current_code_sha='a'*40)
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'
    assert repo.health(scope)['pending_inbox'] == 1


def test_balance_discontinuity_invalidates_qualification_without_replacing_baseline(setup):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts, outbox
    repo, scope, stream = setup
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(qualification={'state':'DEMO_VERIFIED','fixture':True}))
    with pytest.raises(DemoError, match='DEMO_BALANCE_MISMATCH'):
        DemoReconciler(repo, transport=None).check_balances(scope, [
            {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'10000','locked':'0'}])
    account = repo.account(scope)
    assert account['qualification'] is None
    assert account['balances'] == {'BTC':'0.5','USDT':'100'}
    assert account['state'] == 'RECOVERY_REQUIRED'
    with repo.engine.connect() as conn:
        assert conn.execute(select(outbox).where(outbox.c.kind=='BALANCE_BASELINE_INVALIDATED')).first()


def test_external_balance_delta_revokes_qualification_without_posting(setup):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts, outbox
    repo, scope, stream = setup
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(qualification={'state':'DEMO_VERIFIED','fixture':True}))
    stream.ingest({'subscriptionId':4,'event':{'e':'balanceUpdate','E':1001,'a':'USDT','d':'10','T':1000}}, received_at=2)
    assert repo.account(scope)['qualification'] is None
    with repo.engine.connect() as conn:
        assert conn.execute(select(outbox).where(outbox.c.kind=='BALANCE_BASELINE_INVALIDATED')).first()
        assert conn.execute(select(postings)).first() is None


def test_terminated_subscription_immediately_becomes_unhealthy(setup):
    repo, scope, stream = setup
    stream.ingest({'subscriptionId':4,'event':{'e':'eventStreamTerminated','E':1001}}, received_at=2)
    assert stream.healthy is False
    assert repo.health(scope)['pending_inbox'] == 1


def test_new_frame_during_rest_snapshot_cannot_be_covered_by_old_review(setup):
    from trading_system.storage.demo import outbox
    repo, scope, stream = setup
    stream._begin_run()
    stream.ingest({'subscriptionId':4,'event':position()}, received_at=2)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def account(self):
            stream.ingest({'subscriptionId':4,'event':position(update=1002,event=1003)}, received_at=2)
            return {'uid':123,'accountType':'SPOT','updateTime':1002,'balances':[
                {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'100','locked':'0'}]}
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_READ_RACED'):
        DemoReconciler(repo, Transport(), clock=lambda:2).review_account_positions(scope, stream=stream)
    assert repo.health(scope)['pending_inbox'] == 2
    with repo.engine.connect() as conn:
        assert conn.execute(select(outbox).where(outbox.c.kind=='ACCOUNT_POSITION_REVIEW')).first() is None


def test_snapshot_balance_reset_revokes_qualification_during_event_review(setup):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    repo, scope, stream = setup
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(qualification={'state':'DEMO_VERIFIED','fixture':True}))
    stream._begin_run()
    stream.ingest({'subscriptionId':4,'event':position(free='10000')}, received_at=2)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def account(self):
            return {'uid':123,'accountType':'SPOT','updateTime':1000,'balances':[
                {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'10000','locked':'0'}]}
    with pytest.raises(DemoError, match='DEMO_BALANCE_MISMATCH'):
        DemoReconciler(repo, Transport(), clock=lambda:2).review_account_positions(scope, stream=stream)
    assert repo.account(scope)['qualification'] is None
    assert repo.account(scope)['balances'] == {'BTC':'0.5','USDT':'100'}
    assert repo.health(scope)['pending_inbox'] == 1


def test_operator_view_exposes_pending_account_events_without_asserting_reconciliation(setup):
    repo, scope, stream = setup
    stream.ingest({'subscriptionId':4,'event':position()}, received_at=2)
    status = repo.operator_view(scope)['account_position_review']
    assert status == {'state':'PENDING', 'count':1, 'execution_authorized':False,
                      'blocker':'DEMO_ACCOUNT_STREAM_SNAPSHOT_ORDERING_UNQUALIFIED'}


def test_ledger_change_during_rest_read_does_not_invalidate_qualification(setup):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    repo, scope, stream = setup
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(qualification={'state':'DEMO_VERIFIED','fixture':True}))
    stream._begin_run()
    stream.ingest({'subscriptionId':4,'event':position()}, received_at=2)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def account(self):
            with repo.transaction(scope.account_key) as conn:
                conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(revision=accounts.c.revision + 1))
            return {'uid':123,'accountType':'SPOT','updateTime':1000,'balances':[
                {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'90','locked':'0'}]}
    with pytest.raises(DemoError, match='DEMO_ACCOUNT_READ_RACED'):
        DemoReconciler(repo, Transport(), clock=lambda:2).review_account_positions(scope, stream=stream)
    assert repo.account(scope)['qualification'] == {'state':'DEMO_VERIFIED','fixture':True}


def test_pending_execution_report_makes_balance_mismatch_ambiguous(setup):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    repo, scope, stream = setup
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(qualification={'state':'DEMO_VERIFIED','fixture':True}))
    repo.observe(scope, source='WS_EXECUTION_REPORT', payload={'e':'executionReport'}, received_at=2,
        credential_ref='demo-session:fixture', credential_revision=0)
    with pytest.raises(DemoError, match='DEMO_BALANCE_RECONCILIATION_INCOMPLETE'):
        DemoReconciler(repo, transport=None).check_balances(scope, [
            {'asset':'BTC','free':'0.5','locked':'0'}, {'asset':'USDT','free':'90','locked':'0'}], stream=stream)
    assert repo.account(scope)['qualification'] == {'state':'DEMO_VERIFIED','fixture':True}
    assert repo.health(scope)['pending_inbox'] == 1


def test_terminal_order_with_unsettled_reservation_keeps_balance_mismatch_ambiguous(setup):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts, commands
    repo, scope, stream = setup
    request = {'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000','newClientOrderId':'before','timeInForce':'GTC'}
    repo.stage(scope, command_id='before', request=request, reserve_asset='USDT', reserve_amount='50', available='100', authorization_ref='fixture', expires_at=200, now=100)
    repo.begin_send(scope, 'before', expected_revision=0, now=101)
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(qualification={'state':'DEMO_VERIFIED','fixture':True}))
        conn.execute(update(commands).where(commands.c.command_id=='before').values(state='OBSERVED',order_status='FILLED',cumulative_qty='0.001'))
    with pytest.raises(DemoError, match='DEMO_BALANCE_RECONCILIATION_INCOMPLETE'):
        DemoReconciler(repo, transport=None).check_balances(scope, [{'asset':'BTC','free':'0.501','locked':'0'}, {'asset':'USDT','free':'50','locked':'0'}], stream=stream)
    assert repo.account(scope)['qualification'] == {'state':'DEMO_VERIFIED','fixture':True}


def test_terminal_order_unaccounted_fill_is_ambiguous_even_if_reserve_is_zero(setup):
    from sqlalchemy import update
    from trading_system.storage.demo import accounts, commands
    repo, scope, stream = setup
    request = {'symbol':'BTCUSDT','side':'BUY','type':'LIMIT','quantity':'0.001','price':'50000','newClientOrderId':'before','timeInForce':'GTC'}
    repo.stage(scope, command_id='before', request=request, reserve_asset='USDT', reserve_amount='50', available='100', authorization_ref='fixture', expires_at=200, now=100)
    repo.begin_send(scope, 'before', expected_revision=0, now=101)
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).where(accounts.c.account_key==scope.account_key).values(qualification={'state':'DEMO_VERIFIED','fixture':True}))
        conn.execute(update(commands).where(commands.c.command_id=='before').values(state='OBSERVED',order_id='22',order_status='FILLED',cumulative_qty='0.001',reserve_amount='0'))
    with pytest.raises(DemoError, match='DEMO_BALANCE_RECONCILIATION_INCOMPLETE'):
        DemoReconciler(repo, transport=None).check_balances(scope, [{'asset':'BTC','free':'0.501','locked':'0'}, {'asset':'USDT','free':'50','locked':'0'}], stream=stream)
    assert repo.account(scope)['qualification'] == {'state':'DEMO_VERIFIED','fixture':True}
