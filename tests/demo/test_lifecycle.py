"""Portable persistence/reducer tests. These are not PostgreSQL qualification."""
from decimal import Decimal
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope
from trading_system.storage.demo import DemoRepository, metadata, accounts, commands, postings, fills, outbox
from trading_system.execution.demo_reports import DemoReportVerifier, spot_postings
from trading_system.runtime.demo_host import AdmissionContext, DemoRuntimeHost


@pytest.fixture
def repo(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'demo.sqlite3'))
    metadata.create_all(engine)
    repository = DemoRepository(engine)
    yield repository
    engine.dispose()


@pytest.fixture
def scope():
    return DemoScope(account_id='verified-demo-account', account_epoch='epoch-1', strategy_id='strategy-1')


def register(repo, scope):
    repo.register_account(scope, credential_ref='demo-session:fixture', fingerprint='abcdef', budget='10000')


def order(client_id='original'):
    return {'symbol': 'BTCUSDT', 'side': 'BUY', 'type': 'LIMIT', 'quantity': '0.01', 'price': '50000', 'timeInForce': 'GTC', 'newClientOrderId': client_id}


def stage(repo, scope, command_id='cmd-1', client_id='original', reserve='500'):
    return repo.stage(scope, command_id=command_id, request=order(client_id), reserve_asset='USDT', reserve_amount=reserve, available='1000', authorization_ref='assessment-1', expires_at=200, now=100)


def test_account_never_starts_ready_or_copies_paper_balance(repo, scope):
    register(repo, scope)
    account = repo.account(scope)
    assert account['state'] == 'READ_ONLY'
    assert account['balances'] is None
    assert account['budget'] == '10000'


def test_atomic_stage_preserves_identity_and_rejects_changed_intent(repo, scope):
    register(repo, scope)
    first = stage(repo, scope)
    assert stage(repo, scope)['request_hash'] == first['request_hash']
    with pytest.raises(DemoError, match='IDEMPOTENCY_CONFLICT'):
        stage(repo, scope, reserve='501')
    with pytest.raises(DemoError, match='CLIENT_ID_CONFLICT'):
        stage(repo, scope, command_id='cmd-2')
    assert repo.command(scope, 'cmd-1')['state'] == 'DURABLE_READY'


def test_account_budget_shared_across_strategies(repo, scope):
    register(repo, scope)
    stage(repo, scope, reserve='600')
    other = replace(scope, strategy_id='another')
    with pytest.raises(DemoError, match='BUDGET_EXCEEDED'):
        stage(repo, other, command_id='cmd-2', client_id='another', reserve='500')


def test_may_commit_is_one_way_and_unknown_keeps_reservation(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    assert repo.command(scope, 'cmd-1')['state'] == 'MAY_HAVE_SENT'
    repo.mark_unknown(scope, 'cmd-1', 'DEMO_TRANSPORT_UNKNOWN')
    assert repo.command(scope, 'cmd-1')['reserve_amount'] == '500'
    with pytest.raises(DemoError, match='SEND_ALREADY_ATTEMPTED'):
        repo.begin_send(scope, 'cmd-1', expected_revision=0, now=102)
    # A single not-found observation does not clear ambiguity.
    repo.reconcile_not_found(scope, 'cmd-1')
    assert repo.command(scope, 'cmd-1')['state'] == 'UNKNOWN'
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'


def report(**extra):
    return {'e': 'executionReport', 'E': 105000, 's': 'BTCUSDT', 'c': 'original', 'S': 'BUY', 'o': 'LIMIT', 'q': '0.01', 'p': '50000',
            'x': 'TRADE', 'X': 'FILLED', 'i': 22, 't': 33, 'l': '0.01', 'L': '50000', 'z': '0.01', 'Z': '500', 'Y': '500', 'n': '0.00001', 'N': 'BTC', **extra}


def observed(repo, scope, payload):
    return repo.observe(scope, source='WS_EXECUTION_REPORT', payload=payload, received_at=106, credential_ref='demo-session:fixture')


def test_fill_replay_and_late_new_are_idempotent(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    verifier = DemoReportVerifier(repo)
    observation = observed(repo, scope, report())
    verifier.process(scope, observation)
    verifier.process(scope, observation)
    # Same economic fill, a later exchange event timestamp.
    verifier.process(scope, observed(repo, scope, report(E=106000)))
    verifier.process(scope, observed(repo, scope, report(E=104000, x='NEW', X='NEW', t=-1, l='0', z='0', Z='0', n='0', N=None)))
    assert repo.command(scope, 'cmd-1')['order_status'] == 'FILLED'
    assert repo.asset_deltas(scope) == {'BTC': Decimal('0.00999'), 'USDT': Decimal('-500')}
    assert repo.health(scope)['fill_count'] == 1
    assert repo.health(scope)['unprotected'] == 1
    assert repo.command(scope, 'cmd-1')['reserve_amount'] == '0'


def test_conflicting_trade_identity_freezes_without_second_posting(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    verifier = DemoReportVerifier(repo)
    verifier.process(scope, observed(repo, scope, report()))
    with pytest.raises(DemoError, match='FILL_CONFLICT'):
        verifier.process(scope, observed(repo, scope, report(n='0.00002')))
    assert repo.health(scope)['fill_count'] == 1
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'


def test_fill_cannot_be_credited_to_second_strategy(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    verifier = DemoReportVerifier(repo)
    other = replace(scope, strategy_id='other')
    with pytest.raises(DemoError, match='ORDER_BINDING'):
        verifier.process(other, observed(repo, other, report()))
    assert repo.health(scope)['fill_count'] == 0


def test_partial_cancel_preserves_fills_and_rest_duplicate(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    verifier = DemoReportVerifier(repo)
    verifier.process(scope, observed(repo, scope, report(X='PARTIALLY_FILLED', l='0.004', z='0.004', Z='200', Y='200', n='0.002', N='BNB')))
    verifier.process(scope, observed(repo, scope, report(X='CANCELED', x='CANCELED', t=-1, l='0', z='0.004', Z='200', n='0', N=None)))
    assert repo.command(scope, 'cmd-1')['order_status'] == 'CANCELED'
    assert repo.asset_deltas(scope) == {'BTC': Decimal('0.004'), 'USDT': Decimal('-200'), 'BNB': Decimal('-0.002')}


def test_epoch_freeze_preserves_old_facts(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.freeze_epoch(scope, evidence_ref='reset-evidence')
    with pytest.raises(DemoError, match='EPOCH_FROZEN'):
        stage(repo, scope, command_id='cmd-2', client_id='new')
    next_scope = replace(scope, account_epoch='epoch-2')
    register(repo, next_scope)
    assert repo.account(next_scope)['balances'] is None
    assert repo.command(scope, 'cmd-1')['request']['newClientOrderId'] == 'original'


@pytest.mark.parametrize('asset,expected', [('BTC', {'BTC': Decimal('0.009'), 'USDT': Decimal('-500')}), ('USDT', {'BTC': Decimal('0.01'), 'USDT': Decimal('-500.001')}), ('BNB', {'BTC': Decimal('0.01'), 'USDT': Decimal('-500'), 'BNB': Decimal('-0.001')})])
def test_commission_keeps_original_asset(asset, expected):
    assert spot_postings(side='BUY', base='BTC', quote='USDT', quantity='0.01', quote_quantity='500', commission='0.001', commission_asset=asset) == expected


def ready_context():
    return AdmissionContext(now=100, quote_at=99, account_at=99, quote_source='BINANCE_DEMO', model_source='assessment_v1', decision_expires_at=110,
        capability_state='DEMO_VERIFIED', code_sha='a' * 40, qualified_sha='a' * 40, private_stream_healthy=True,
        account_state='READY', authorized=True, budget='1000', available='1000', risk_limit='1000', unresolved_unknown=0, unprotected=0)


def test_admission_is_fail_closed_and_current_sha_bound():
    assert DemoRuntimeHost.admit(ready_context()) == Decimal('1000')
    for updates in [{'quote_source': 'PRODUCTION'}, {'model_source': ''}, {'capability_state': 'COMPILED'}, {'qualified_sha': 'b' * 40}, {'private_stream_healthy': False}, {'unresolved_unknown': 1}, {'unprotected': 1}, {'quote_at': 1}, {'account_at': 1}, {'decision_expires_at': 99}, {'authorized': False}]:
        with pytest.raises(DemoError):
            DemoRuntimeHost.admit(replace(ready_context(), **updates))


def test_rest_and_ws_same_trade_credit_once(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    verifier = DemoReportVerifier(repo)
    verifier.process(scope, observed(repo, scope, report()))
    rest = {'symbol': 'BTCUSDT', 'id': 33, 'orderId': 22, 'price': '50000', 'qty': '0.010000', 'quoteQty': '500.000', 'commission': '0.000010', 'commissionAsset': 'BTC', 'isBuyer': True, 'time': 105000}
    observation = repo.observe(scope, source='REST_TRADES', payload=rest, received_at=107, credential_ref='demo-session:fixture')
    verifier.process(scope, observation)
    assert repo.health(scope)['fill_count'] == 1
    assert repo.asset_deltas(scope)['BTC'] == Decimal('0.00999')


def test_rest_order_resolves_original_id_without_resending(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    repo.mark_unknown(scope, 'cmd-1', 'LOST_RESPONSE')
    payload = {'symbol': 'BTCUSDT', 'clientOrderId': 'original', 'orderId': 22, 'side': 'BUY', 'type': 'LIMIT', 'origQty': '0.01', 'executedQty': '0', 'status': 'NEW', 'price': '50000', 'updateTime': 105000}
    observation = repo.observe(scope, source='REST_ORDER', payload=payload, received_at=107, credential_ref='demo-session:fixture')
    DemoReportVerifier(repo).process(scope, observation)
    assert repo.command(scope, 'cmd-1')['order_status'] == 'NEW'
    assert repo.command(scope, 'cmd-1')['reserve_amount'] == '500'
    assert repo.account(scope)['state'] == 'RECOVERY_REQUIRED'


def test_transport_permit_is_one_shot_and_binds_request():
    from trading_system.execution.demo_commands import DemoSendPermit
    with pytest.raises(DemoError, match='OWNER_REQUIRED'):
        DemoSendPermit()


def test_owner_cannot_dispatch_on_unqualified_sqlite(repo, scope):
    from trading_system.execution.demo_commands import DemoExecutionOwner
    register(repo, scope)
    stage(repo, scope)
    with pytest.raises(DemoError, match='POSTGRESQL_OWNER_REQUIRED'):
        DemoExecutionOwner(repo, transport=None).dispatch(scope, 'cmd-1', context=ready_context())
    assert repo.command(scope, 'cmd-1')['state'] == 'DURABLE_READY'


def test_rotation_preserves_commands_but_invalidates_authorization(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.rotate_credential(scope, credential_ref='demo-session:rotated', fingerprint='new', expected_revision=0)
    assert repo.account(scope)['state'] == 'READ_ONLY'
    with pytest.raises(DemoError, match='AUTHORIZATION_STALE'):
        repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)


def test_underreserved_buy_and_wrong_asset_cannot_stage(repo, scope):
    register(repo, scope)
    with pytest.raises(DemoError, match='RESERVATION_TOO_SMALL'):
        stage(repo, scope, reserve='0')
    with pytest.raises(DemoError, match='RESERVATION_ASSET'):
        repo.stage(scope, command_id='wrong', request=order(), reserve_asset='BTC', reserve_amount='500', available='1000', authorization_ref='assessment-1', expires_at=200, now=100)


def test_limit_price_violation_cannot_be_credited(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    with pytest.raises(DemoError, match='LIMIT_PRICE'):
        DemoReportVerifier(repo).process(scope, observed(repo, scope, report(L='60000', Y='600', Z='600')))
    assert repo.health(scope)['fill_count'] == 0


def test_terminal_rest_report_before_trade_releases_after_missing_trade(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)
    verifier = DemoReportVerifier(repo)
    terminal = {'symbol': 'BTCUSDT', 'clientOrderId': 'original', 'orderId': 22, 'side': 'BUY', 'type': 'LIMIT', 'origQty': '0.01', 'executedQty': '0.01', 'status': 'FILLED', 'price': '50000'}
    verifier.process(scope, repo.observe(scope, source='REST_ORDER', payload=terminal, received_at=107, credential_ref='demo-session:fixture'))
    assert repo.command(scope, 'cmd-1')['reserve_amount'] == '500'
    trade = {'symbol': 'BTCUSDT', 'id': 33, 'orderId': 22, 'price': '50000', 'qty': '0.01', 'quoteQty': '500', 'commission': '0', 'commissionAsset': 'USDT', 'isBuyer': True}
    verifier.process(scope, repo.observe(scope, source='REST_TRADES', payload=trade, received_at=108, credential_ref='demo-session:fixture'))
    assert repo.command(scope, 'cmd-1')['reserve_amount'] == '0'


def test_pause_fences_prepared_command(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    repo.pause(scope, 'STALE_ACCOUNT')
    with pytest.raises(DemoError, match='AUTHORIZATION_STALE'):
        repo.begin_send(scope, 'cmd-1', expected_revision=0, now=101)


def test_unsent_command_cannot_claim_venue_facts(repo, scope):
    register(repo, scope)
    stage(repo, scope)
    with pytest.raises(DemoError, match='ORDER_NEVER_SENT'):
        DemoReportVerifier(repo).process(scope, observed(repo, scope, report()))
    assert repo.health(scope)['fill_count'] == 0
    assert repo.command(scope, 'cmd-1')['reserve_amount'] == '500'


def test_owner_commits_may_before_one_attempt_and_never_retries_unknown(repo, scope, monkeypatch):
    from contextlib import contextmanager
    from sqlalchemy import update
    from trading_system.execution.demo_commands import DemoExecutionOwner
    register(repo, scope)
    stage(repo, scope)
    context = ready_context()
    with repo.engine.begin() as conn:
        conn.execute(update(accounts).values(state='READY', qualification={'code_sha': context.code_sha, 'account_key': scope.account_key,
            'credential_revision': 0, 'product': 'SPOT', 'order_type': 'LIMIT', 'state': 'DEMO_VERIFIED',
            'evidence_sha256': 'f' * 64, 'official_contract_revision': 'offline-fixture-only'}))
    @contextmanager
    def offline_guard(_):
        # Unit seam only. Production owner_guard remains PostgreSQL-only.
        yield
    monkeypatch.setattr(repo, 'owner_guard', offline_guard)
    class LostResponse:
        credential_ref = 'demo-session:fixture'
        attempts = 0
        def sync_time(self):
            return {}
        def send_order(self, request, *, permit):
            assert repo.command(scope, 'cmd-1')['state'] == 'MAY_HAVE_SENT'
            permit.consume('POST', request, self.credential_ref)
            with pytest.raises(DemoError, match='PERMIT_INVALID'):
                permit.consume('POST', request, self.credential_ref)
            self.attempts += 1
            raise TimeoutError('lost acknowledgement')
    transport = LostResponse()
    owner = DemoExecutionOwner(repo, transport, clock=lambda: 100)
    with pytest.raises(DemoError, match='SEND_OUTCOME_UNKNOWN'):
        owner.dispatch(scope, 'cmd-1', context=context)
    with pytest.raises(DemoError):
        owner.dispatch(scope, 'cmd-1', context=context)
    assert transport.attempts == 1
    assert repo.command(scope, 'cmd-1')['reserve_amount'] == '500'
    assert repo.command(scope, 'cmd-1')['state'] == 'UNKNOWN'


def test_parallel_reservations_do_not_oversubscribe_account(repo, scope):
    from concurrent.futures import ThreadPoolExecutor
    register(repo, scope)
    def submit(index):
        try:
            other_repo = DemoRepository(repo.engine)
            stage(other_repo, scope, command_id=f'parallel-{index}', client_id=f'client-{index}', reserve='600')
            return 'OK'
        except DemoError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sorted(results) == ['DEMO_BUDGET_EXCEEDED', 'OK']
