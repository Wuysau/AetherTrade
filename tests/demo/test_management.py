from decimal import Decimal
import pytest
from sqlalchemy import select
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.execution.demo_reports import DemoReportVerifier
from trading_system.storage.demo import protection
from test_lifecycle import repo, scope, register, order, report, observed


def buy(repo, scope):
    register(repo, scope)
    repo.stage(scope, command_id='entry', request=order(), reserve_asset='USDT', reserve_amount='500', available='1000',
        authorization_ref='grant', expires_at=200, now=100, protection_plan={'stop_price': '49000', 'maximum_holding_seconds': 3600, 'mode': 'HOSTED', 'authorization_ref': 'grant'})
    repo.begin_send(scope, 'entry', expected_revision=0, now=101)
    DemoReportVerifier(repo).process(scope, observed(repo, scope, report()))


def test_fill_persists_net_quantity_stop_and_deadline_atomically(repo, scope):
    buy(repo, scope)
    duty = repo.protection_duties(scope)[0]
    assert duty['plan']['stop_price'] == '49000'
    assert Decimal(duty['remaining_quantity']) == Decimal('0.00999')
    assert duty['entry_command_id'] == 'entry'
    assert duty['state'] == 'REQUIRED'
    assert repo.risk_summary(scope, mark='49000', now=106)['loss'] >= Decimal('10')


def test_sell_fill_discharges_only_owned_protection_quantity(repo, scope):
    buy(repo, scope)
    request = {**order('close'), 'side': 'SELL', 'quantity': '0.00999', 'price': '49000'}
    repo.stage(scope, command_id='exit', request=request, reserve_asset='BTC', reserve_amount='0.00999', available='0.00999',
        authorization_ref='grant', expires_at=200, now=110, purpose='REDUCTION')
    repo.begin_send(scope, 'exit', expected_revision=1, now=111)
    exit_report = report(c='close', S='SELL', q='0.00999', p='49000', i=23, t=34, l='0.00999', L='49000', z='0.00999', Y='489.51', n='0.48951', N='USDT')
    DemoReportVerifier(repo).process(scope, observed(repo, scope, exit_report))
    assert repo.protection_duties(scope) == []
    assert repo.asset_deltas(scope)['BTC'] == 0
    assert repo.risk_summary(scope, mark='49000', now=106)['loss'] >= Decimal('10')


def test_cancel_owner_can_reduce_risk_while_paused_and_binds_original_identity(repo, scope, monkeypatch):
    from contextlib import contextmanager
    from dataclasses import replace
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    from trading_system.execution.demo_commands import DemoExecutionOwner
    from test_lifecycle import ready_context
    register(repo, scope)
    repo.stage(scope, command_id='entry', request=order(), reserve_asset='USDT', reserve_amount='500', available='1000', authorization_ref='grant', expires_at=200, now=100)
    repo.begin_send(scope, 'entry', expected_revision=0, now=101)
    repo.pause(scope)
    repo.stage(scope, command_id='cancel', request={'symbol': 'BTCUSDT', 'origClientOrderId': 'original'}, method='DELETE', purpose='CANCEL',
        reserve_asset='BTC', reserve_amount='0', available='0', authorization_ref='grant', expires_at=200, now=110)
    context = replace(ready_context(), account_state='RECOVERY_REQUIRED', decision_expires_at=200, unresolved_unknown=1, private_stream_healthy=False)
    with repo.engine.begin() as conn:
        conn.execute(update(accounts).values(qualification={'code_sha': context.code_sha, 'account_key': scope.account_key, 'credential_revision': 0,
            'product': 'SPOT', 'order_type': 'CANCEL', 'state': 'DEMO_VERIFIED', 'evidence_sha256': 'f'*64, 'official_contract_revision': 'offline-only'}))
    @contextmanager
    def offline_guard(_): yield
    monkeypatch.setattr(repo, 'owner_guard', offline_guard)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def cancel_order(self, params, *, permit):
            assert repo.command(scope, 'cancel')['state'] == 'MAY_HAVE_SENT'
            permit.consume('DELETE', params, self.credential_ref)
            return {'symbol': 'BTCUSDT', 'origClientOrderId': 'original', 'clientOrderId': 'venue-cancel-id', 'orderId': 22}
    result = DemoExecutionOwner(repo, Transport(), clock=lambda: 110).dispatch(scope, 'cancel', context=context)
    assert result['state'] == 'ACKNOWLEDGED'
    assert repo.command(scope, 'entry')['reserve_amount'] == '500'


def test_unknown_cancel_is_resolved_only_by_verified_terminal_target(repo, scope):
    register(repo, scope)
    repo.stage(scope, command_id='entry', request=order(), reserve_asset='USDT', reserve_amount='500', available='1000', authorization_ref='grant', expires_at=200, now=100)
    repo.begin_send(scope, 'entry', expected_revision=0, now=101)
    repo.stage(scope, command_id='cancel', request={'symbol': 'BTCUSDT', 'origClientOrderId': 'original'}, method='DELETE', purpose='CANCEL', reserve_asset='BTC', reserve_amount='0', available='0', authorization_ref='grant', expires_at=200, now=102)
    repo.begin_send(scope, 'cancel', expected_revision=0, now=103)
    repo.mark_unknown(scope, 'cancel', 'LOST_RESPONSE')
    event = report(c='venue-cancel-id', C='original', x='CANCELED', X='CANCELED', z='0', l='0', t=-1)
    DemoReportVerifier(repo).process(scope, observed(repo, scope, event))
    assert repo.command(scope, 'cancel')['state'] == 'OBSERVED'
    assert repo.health(scope)['unknown'] == 0
    assert repo.command(scope, 'entry')['reserve_amount'] == '0'


def test_unknown_cancel_cannot_be_replaced_with_new_identity(repo, scope):
    register(repo, scope)
    request = {'symbol': 'BTCUSDT', 'origClientOrderId': 'original'}
    original = repo.stage(scope, command_id='cancel-1', request=request, method='DELETE', purpose='CANCEL', reserve_asset='BTC', reserve_amount='0', available='0', authorization_ref='grant', expires_at=200, now=100)
    repo.begin_send(scope, 'cancel-1', expected_revision=0, now=101)
    repo.mark_unknown(scope, 'cancel-1', 'LOST_RESPONSE')
    attempted = repo.stage(scope, command_id='cancel-2', request=request, method='DELETE', purpose='CANCEL', reserve_asset='BTC', reserve_amount='0', available='0', authorization_ref='grant', expires_at=210, now=110)
    assert attempted['command_id'] == 'cancel-1' and attempted['state'] == 'UNKNOWN'


def test_terminal_protection_releases_residual_only_after_fill_reconciliation(repo, scope):
    buy(repo, scope)
    duty = repo.protection_duties(scope)[0]
    request = {**order('protect'), 'side': 'SELL', 'quantity': '0.009', 'price': '49000'}
    repo.stage(scope, command_id='protect', request=request, reserve_asset='BTC', reserve_amount='0.009', available='0.00999', authorization_ref='grant', expires_at=200, now=110, purpose='PROTECTION', risk_receipt={'protection_fill_keys': [duty['fill_key']]})
    repo.bind_mitigation(scope, [duty['fill_key']], 'protect')
    repo.begin_send(scope, 'protect', expected_revision=1, now=111)
    verifier = DemoReportVerifier(repo)
    terminal = report(c='protect', S='SELL', q='0.009', p='49000', i=23, t=-1, l='0', z='0.004', x='CANCELED', X='CANCELED')
    verifier.process(scope, observed(repo, scope, terminal))
    assert repo.protection_duties(scope)[0]['mitigation_command_id'] == 'protect'
    assert repo.command(scope, 'protect')['reserve_amount'] == '0.009'
    trade = {'symbol': 'BTCUSDT', 'orderId': 23, 'id': 34, 'isBuyer': False, 'qty': '0.004', 'price': '49000', 'quoteQty': '196', 'commission': '0', 'commissionAsset': 'USDT', 'time': 105000}
    oid = repo.observe(scope, source='REST_TRADES', payload=trade, received_at=106, credential_ref='demo-session:fixture')
    verifier.process(scope, oid)
    remaining = repo.protection_duties(scope)[0]
    assert remaining['mitigation_command_id'] is None and remaining['state'] == 'REQUIRED'
    assert Decimal(remaining['remaining_quantity']) == Decimal('0.00599')
    assert repo.command(scope, 'protect')['reserve_amount'] == '0'


def test_partial_protection_fee_exhausts_owned_duty_but_keeps_live_order_visible(repo, scope):
    buy(repo, scope)
    duty = repo.protection_duties(scope)[0]
    request = {**order('protect-excess'), 'side':'SELL', 'quantity':'0.0095', 'price':'49000'}
    repo.stage(scope, command_id='protect-excess', request=request, reserve_asset='BTC', reserve_amount='0.0095', available='0.00999', authorization_ref='grant', expires_at=200, now=110, purpose='PROTECTION', risk_receipt={'protection_fill_keys':[duty['fill_key']], 'fee_rate_cap':'0.001'})
    repo.bind_mitigation(scope, [duty['fill_key']], 'protect-excess')
    repo.begin_send(scope, 'protect-excess', expected_revision=1, now=111)
    event = report(c='protect-excess', S='SELL', q='0.0095', p='49000', i=23, t=34, l='0.009', L='49000', z='0.009', Y='441', n='0.001', N='BTC', X='PARTIALLY_FILLED')
    DemoReportVerifier(repo).process(scope, observed(repo, scope, event))
    assert repo.protection_duties(scope) == []
    assert repo.asset_deltas(scope)['BTC'] == Decimal('-0.00001')
    assert repo.health(scope)['risk_breaches'] == 2
    assert repo.command(scope, 'protect-excess')['order_status'] == 'PARTIALLY_FILLED'
    assert repo.command(scope, 'protect-excess')['reserve_amount'] != '0'


def test_account_position_pending_revokes_and_prevents_hosted_coverage(repo, scope):
    from sqlalchemy import select
    from trading_system.storage.demo import protection
    buy(repo, scope)
    duty = repo.protection_duties(scope)[0]
    repo.renew_hosted_protection(scope, fill_key=duty['fill_key'], now=110, until=115, authorization_ref='grant')
    repo.observe(scope, source='WS_ACCOUNT_POSITION', payload={'e':'outboundAccountPosition','E':110000,'u':110000,'B':[{'a':'USDT','f':'500','l':'0'}]},
        received_at=110, credential_ref='demo-session:fixture', credential_revision=0)
    with repo.engine.connect() as conn:
        stored = conn.execute(select(protection).where(protection.c.fill_key==duty['fill_key'])).mappings().one()
        assert stored['state'] == 'REQUIRED' and stored['coverage_until'] == 0
    with pytest.raises(DemoError, match='DEMO_PROTECTION_RECONCILIATION_REQUIRED'):
        repo.renew_hosted_protection(scope, fill_key=duty['fill_key'], now=111, until=116, authorization_ref='grant')


def test_balance_invalidation_preserves_only_bound_cancel_capability(repo, scope, monkeypatch):
    from contextlib import contextmanager
    from dataclasses import replace
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    from trading_system.execution.demo_commands import DemoExecutionOwner
    from test_lifecycle import ready_context
    register(repo, scope)
    repo.stage(scope, command_id='entry', request=order(), reserve_asset='USDT', reserve_amount='500', available='1000', authorization_ref='grant', expires_at=200, now=100)
    repo.begin_send(scope, 'entry', expected_revision=0, now=101)
    repo.acknowledge(scope, 'entry', {'symbol':'BTCUSDT','clientOrderId':'original','orderId':22})
    repo.observe(scope, source='WS_EXECUTION_REPORT', payload=report(x='NEW',X='NEW',t=-1,l='0',z='0'), received_at=105,
                 credential_ref='demo-session:fixture', credential_revision=0)
    DemoReportVerifier(repo).process(scope, observed(repo, scope, report(x='NEW',X='NEW',t=-1,l='0',z='0')))
    proof = {'state':'DEMO_VERIFIED','evidence_sha256':'f'*64,'official_contract_revision':'fixture'}
    qualification = {'state':'DEMO_VERIFIED','code_sha':'a'*40,'account_key':scope.account_key,'credential_revision':0,'product':'SPOT',
                     'capabilities':{'LIMIT':proof, 'CANCEL':proof}}
    with repo.transaction(scope.account_key) as conn: conn.execute(update(accounts).values(qualification=qualification))
    repo.observe(scope, source='WS_UNSUPPORTED_EVENT', payload={'e':'balanceUpdate','E':109000,'a':'USDT','d':'10','T':109000},
                 received_at=109, credential_ref='demo-session:fixture', credential_revision=0)
    evidence = repo.account(scope)['qualification']
    assert evidence['state'] == 'INVALIDATED_BALANCE' and set(evidence['capabilities']) == {'CANCEL'}
    repo.stage(scope, command_id='cancel-after-reset', request={'symbol':'BTCUSDT','origClientOrderId':'original'}, method='DELETE', purpose='CANCEL',
               reserve_asset='BTC', reserve_amount='0', available='0', authorization_ref='grant', expires_at=200, now=110)
    @contextmanager
    def guard(_): yield
    monkeypatch.setattr(repo, 'owner_guard', guard)
    class Transport:
        credential_ref = 'demo-session:fixture'
        def sync_time(self): pass
        def cancel_order(self, params, *, permit):
            permit.consume('DELETE', params, self.credential_ref)
            return {'symbol':'BTCUSDT','origClientOrderId':'original','orderId':22}
    context = replace(ready_context(), account_state='RECOVERY_REQUIRED', capability_state='INVALIDATED_BALANCE', decision_expires_at=200)
    result = DemoExecutionOwner(repo, Transport(), clock=lambda:110).dispatch(scope, 'cancel-after-reset', context=context)
    assert result['state'] == 'ACKNOWLEDGED'


def test_invalidated_balance_evidence_does_not_authorize_new_or_reduction():
    from dataclasses import replace
    from trading_system.runtime.demo_host import DemoRuntimeHost
    from test_lifecycle import ready_context
    context = replace(ready_context(), capability_state='INVALIDATED_BALANCE')
    with pytest.raises(DemoError, match='DEMO_CAPABILITY_NOT_QUALIFIED'):
        DemoRuntimeHost.admit(context)
    with pytest.raises(DemoError, match='DEMO_CAPABILITY_NOT_QUALIFIED'):
        DemoRuntimeHost.admit_management(context, cancel=False)
