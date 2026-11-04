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
