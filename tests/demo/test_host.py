"""Demo-specific evidence and native assessment composition; no external IO."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, digest
from trading_system.server.demo_snapshot import build_demo_snapshot, validate_demo_snapshot
from trading_system.runtime.demo_strategy import DemoStrategyHost
from trading_system.storage.demo import DemoRepository, metadata
from test_transport import RULES


def strategy():
    return {'revision': 1, 'policy_revision': 1, 'authorized': True, 'authorization_ref': 'explicit-demo-strategy-grant',
        'authorized_until': int(datetime.now(timezone.utc).timestamp()) + 3600,
        'allow_execution': True, 'mode': 'RULE', 'rule_revision': 'fixture-rule-v1', 'allow_model_calls': False,
        'model_profile_ids': ['default'], 'model_revisions': {'default': 1}, 'analysis_mode': 'single', 'decision_policy': 'unanimous',
        'budget': '1000', 'max_planned_loss': '100', 'max_daily_loss': '100', 'fee_rate_cap': '0.001', 'protection_mode': 'HOSTED',
        'profile': {'strategy_revision': 1, 'decision_interval': '1m', 'holding_period_seconds': 3600, 'allowed_operations': ['SET_TARGET', 'HOLD'],
        'required': ['quote', 'candles:1m'], 'optional': ['book'], 'windows': {'1m': 2}}}


def frame(scope, config=None):
    now = datetime.now(timezone.utc)
    start = int(now.timestamp()) // 60 * 60000
    market = {'environment': 'BINANCE_DEMO', 'symbol': 'BTCUSDT', 'as_of': now.isoformat(), 'bid': '49999', 'ask': '50001',
        'candles': {'1m': [{'time': start - i * 60000, 'close_time': start - (i - 1) * 60000 - 1, 'closed': True,
            'open': '50000', 'high': '50000', 'low': '50000', 'close': '50000', 'volume': '10'} for i in (2, 1)]}}
    return build_demo_snapshot(scope=scope, strategy=config or strategy(), account={'revision': 0, 'credential_revision': 0,
        'observed_at': now.isoformat(), 'balances': {'USDT': {'free': '1000', 'locked': '0'}, 'BTC': {'free': '0', 'locked': '0'}},
        'position': '0', 'state': 'READY', 'fee_rate': '0.001'}, market=market, rules=RULES,
        code_sha='a' * 40, qualification={'code_sha': 'a' * 40, 'state': 'DEMO_VERIFIED', 'protection': 'HOSTED', 'evidence_sha256': 'f' * 64})


def assessment(snapshot):
    result = json.loads(Path('tests/fixtures/contracts/examples/decision-proposal.paper.json').read_text())
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
    result.update(instrument_ref=snapshot['instrument_ref'], proposal_id='demo-proposal', valid_until=deadline, evidence_refs=['quote'])
    result['entry'].update(entry_deadline=deadline, limit_price='50000')
    result['exit_plan'].update(review_due_at=deadline, maximum_holding_seconds=3600)
    return {'schema_revision': 'assessment_v1', 'summary': 'Offline fixture', 'proposal': result, 'evidence_claims': [], 'watch_conditions': [], 'next_review_at': None}


def test_demo_snapshot_has_distinct_identity_and_cannot_relabel_production():
    scope = DemoScope(account_id='account', account_epoch='epoch', strategy_id='s1')
    snapshot = frame(scope)
    assert validate_demo_snapshot(snapshot)['instrument_ref'] == 'binance-demo:SPOT:BTCUSDT'
    assert snapshot['costs']['fee_source'] == 'BINANCE_DEMO_ACCOUNT'
    for mutate in [lambda x: x.update(source_id='PRODUCTION'), lambda x: x['scope'].update(environment='PAPER'), lambda x: x['market']['quote'].update(price='1')]:
        invalid = deepcopy(snapshot); mutate(invalid)
        with pytest.raises(DemoError): validate_demo_snapshot(invalid)


def test_real_model_clean_context_accepts_demo_schema_without_weakening_paper():
    from trading_system.server.model_service import ModelService
    from trading_system.server.analysis_snapshot import validate_snapshot
    from trading_system.server.errors import ApiError
    snapshot = frame(DemoScope(account_id='account', account_epoch='epoch', strategy_id='s1'))
    clean = ModelService._clean_context('BTCUSDT', {'analysis_snapshot': snapshot, 'quote_max_age_seconds': 5})
    assert clean['analysis_snapshot']['scope']['environment'] == 'BINANCE_DEMO'
    with pytest.raises(ApiError): validate_snapshot(snapshot)


@pytest.fixture
def host(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'host.sqlite3')); metadata.create_all(engine)
    repo = DemoRepository(engine); scope = DemoScope(account_id='account', account_epoch='epoch', strategy_id='s1')
    repo.register_account(scope, credential_ref='demo-session:fixture', fingerprint='fp', budget='10000')
    config = strategy()
    repo.save_strategy(scope, config, expected_revision=0)
    service = DemoStrategyHost(repo, scope, frame_loader=lambda: frame(scope, config), model_service=None,
        execution_owner=None, rule_assessor=assessment, current_code_sha='a' * 40)
    yield service, repo, scope, config
    engine.dispose()


def test_rule_assessment_is_durable_and_stages_one_native_command(host):
    service, repo, scope, _ = host
    first = asyncio.run(service.assess_once('run-1'))
    assert first['state'] == 'ASSESSED'
    assert first['source'] == 'verified_rule_v1'
    command = service.admit_assessment('run-1')
    assert command['state'] == 'DURABLE_READY'
    assert command['request']['type'] == 'LIMIT'
    assert command['request']['quantity'] == '0.001'
    assert Decimal(command['reserve_amount']) >= Decimal('50.05')
    assert command['protection_plan']['stop_price'] == '49000'
    assert service.admit_assessment('run-1')['command_id'] == command['command_id']
    assert repo.account(scope)['state'] == 'READ_ONLY'  # staging never enables sends


def test_changed_strategy_invalidates_assessment_before_reservation(host):
    service, repo, scope, config = host
    asyncio.run(service.assess_once('run-1'))
    changed = {**config, 'revision': 2, 'profile': {**config['profile'], 'strategy_revision': 2}}
    repo.save_strategy(scope, changed, expected_revision=1)
    with pytest.raises(DemoError, match='BINDING_CHANGED'):
        service.admit_assessment('run-1')


def test_missing_model_permission_never_calls_model(host):
    service, repo, scope, config = host
    updated = {**config, 'mode': 'AI', 'revision': 2, 'profile': {**config['profile'], 'strategy_revision': 2}}
    repo.save_strategy(scope, updated, expected_revision=1)
    with pytest.raises(DemoError, match='MODEL_PERMISSION'):
        asyncio.run(service.assess_once('forbidden-model'))


def test_snapshot_cannot_self_certify_missing_required_evidence():
    from trading_system.server.demo_snapshot import snapshot_hash
    scope = DemoScope(account_id='account', account_epoch='epoch', strategy_id='s1')
    snapshot = frame(scope)
    snapshot['market']['candles']['1m'] = []
    snapshot['market']['features'] = {}
    snapshot['snapshot_hash'] = snapshot_hash(snapshot)
    snapshot['snapshot_id'] = 'demo-snapshot:' + snapshot['snapshot_hash'][:32]
    with pytest.raises(DemoError, match='QUALITY'):
        validate_demo_snapshot(snapshot)


def test_atomic_decision_claim_rejects_duplicate_worker(host):
    _, repo, scope, config = host
    frozen = frame(scope, config)
    repo.begin_decision(scope, 'concurrent', frozen, config, 'verified_rule_v1')
    with pytest.raises(DemoError, match='NOT_RETRYABLE'):
        repo.begin_decision(scope, 'concurrent', frozen, config, 'verified_rule_v1')


def test_cached_frame_expires_at_host_boundary(host):
    service, _, scope, config = host
    frozen = frame(scope, config)
    service.frame_loader = lambda: frozen
    service.clock = lambda: datetime.fromisoformat(frozen['evidence_cutoff']).timestamp() + 6
    with pytest.raises(DemoError, match='STALE'):
        asyncio.run(service.assess_once('stale'))


def test_second_target_cannot_ignore_pending_order(host):
    service, _, _, _ = host
    asyncio.run(service.assess_once('first'))
    service.admit_assessment('first')
    asyncio.run(service.assess_once('second'))
    with pytest.raises(DemoError, match='ACTIVE_ORDER'):
        service.admit_assessment('second')


def test_daily_loss_rejects_new_risk(host):
    service, repo, _, _ = host
    asyncio.run(service.assess_once('loss'))
    repo.risk_summary = lambda *a, **kw: {'loss': Decimal('100'), 'complete': True}
    with pytest.raises(DemoError, match='DAILY_LOSS'):
        service.admit_assessment('loss')


def test_host_dispatch_rechecks_real_owner_gate_and_never_enables_sqlite(host):
    from trading_system.execution.demo_commands import DemoExecutionOwner
    service, repo, scope, _ = host
    asyncio.run(service.assess_once('dispatch'))
    command = service.admit_assessment('dispatch')
    service.owner = DemoExecutionOwner(repo, None)
    with pytest.raises(DemoError, match='POSTGRESQL_OWNER_REQUIRED'):
        service.dispatch(command['command_id'])


def test_empty_protection_tick_does_not_issue_a_command(host):
    service, _, _, _ = host
    assert service.protection_tick() == {'state': 'NO_EXPOSURE', 'commands': []}


def test_far_marketable_limit_is_rejected_even_if_midpoint_did_not_move(host):
    service, _, _, _ = host
    def unsafe(snapshot):
        value = assessment(snapshot)
        value['proposal']['entry']['limit_price'] = '90000'
        return value
    service.rule_assessor = unsafe
    asyncio.run(service.assess_once('slippage'))
    with pytest.raises(DemoError, match='EXECUTABLE_PRICE'):
        service.admit_assessment('slippage')


def test_stop_trigger_stages_owned_exit_and_never_calls_send_from_event(host):
    from types import SimpleNamespace
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    from trading_system.server.demo_snapshot import snapshot_hash
    from test_lifecycle import report
    from trading_system.execution.demo_reports import DemoReportVerifier
    service, repo, scope, config = host
    asyncio.run(service.assess_once('entry'))
    entry = service.admit_assessment('entry')
    repo.begin_send(scope, entry['command_id'], expected_revision=0, now=int(service.clock()))
    event = report(c=entry['client_id'], q='0.001', l='0.001', z='0.001', Y='50', Z='50', n='0', N='USDT', E=int(service.clock()*1000))
    observation = repo.observe(scope, source='WS_EXECUTION_REPORT', payload=event, received_at=int(service.clock()), credential_ref='demo-session:fixture')
    DemoReportVerifier(repo).process(scope, observation)
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).values(qualification={'state': 'DEMO_VERIFIED', 'code_sha': 'a'*40, 'protection': 'HOSTED'}))
    def current():
        result = frame(scope, config)
        result['bindings']['account_revision'] = repo.account(scope)['revision']
        result['portfolio']['position'] = {'symbol': 'BTCUSDT', 'quantity': str(repo.asset_deltas(scope)['BTC'])}
        result['snapshot_hash'] = snapshot_hash(result); result['snapshot_id'] = 'demo-snapshot:' + result['snapshot_hash'][:32]
        return result
    service.frame_loader = current
    service.stream = SimpleNamespace(healthy=True, credential_ref='demo-session:fixture')
    service.owner = SimpleNamespace(transport=SimpleNamespace(last_price=lambda symbol: {'symbol': symbol, 'price': '48000'}))
    result = service.protection_tick()
    assert result['state'] == 'MITIGATION_STAGED'
    command = result['commands'][0]
    assert command['purpose'] == 'PROTECTION' and command['request']['side'] == 'SELL'
    assert Decimal(command['reserve_amount']) <= repo.asset_deltas(scope)['BTC']
    assert repo.protection_duties(scope)[0]['state'] == 'MITIGATION_PENDING'
    assert service.protection_tick()['commands'] == []  # no duplicate sell
    repo.begin_send(scope, command['command_id'], expected_revision=repo.account(scope)['revision'], now=int(service.clock()))
    repo.acknowledge(scope, command['command_id'], {'symbol': 'BTCUSDT', 'clientOrderId': command['client_id'], 'orderId': 45})
    service.clock = lambda: command['expires_at']
    service._frame = current
    assert service.protection_tick()['state'] == 'RECONCILIATION_REQUIRED'
    now = int(service.clock())
    event = report(c=command['client_id'], S='SELL', q=command['request']['quantity'], p=command['request']['price'], i=45, t=-1, l='0', z='0', x='NEW', X='NEW', E=now * 1000)
    observation = repo.observe(scope, source='WS_EXECUTION_REPORT', payload=event, received_at=now, credential_ref='demo-session:fixture')
    DemoReportVerifier(repo).process(scope, observation)
    service.clock = lambda: command['expires_at']
    service._frame = current  # fixture clock advances; production fetches fresh data
    service.owner.transport.last_price = lambda symbol: {'symbol': symbol, 'price': '51000'}  # rebound does not cancel the exit obligation
    result = service.protection_tick()
    assert result['state'] == 'CANCEL_STALE_PROTECTION'
    assert result['commands'][0]['request']['origClientOrderId'] == command['client_id']
    assert repo.protection_duties(scope)[0]['mitigation_command_id'] == command['command_id']
    cancel = result['commands'][0]
    repo.begin_send(scope, cancel['command_id'], expected_revision=repo.account(scope)['revision'], now=int(service.clock()))
    repo.mark_unknown(scope, cancel['command_id'], 'LOST_RESPONSE')
    assert service.protection_tick()['commands'] == []
    assert repo.protection_duties(scope)[0]['mitigation_command_id'] == command['command_id']
    event.update(x='CANCELED', X='CANCELED', E=int(service.clock()) * 1000)
    observation = repo.observe(scope, source='WS_EXECUTION_REPORT', payload=event, received_at=int(service.clock()), credential_ref='demo-session:fixture')
    DemoReportVerifier(repo).process(scope, observation)
    assert repo.command(scope, cancel['command_id'])['state'] == 'OBSERVED'
    assert repo.protection_duties(scope)[0]['mitigation_command_id'] is None
    service.owner.transport.last_price = lambda symbol: {'symbol': symbol, 'price': '48000'}
    retry = service.protection_tick()
    assert retry['state'] == 'MITIGATION_STAGED'
    assert retry['commands'][0]['command_id'] != command['command_id']


def test_expired_unsent_cancel_gets_new_lineage(host):
    service, repo, scope, config = host
    asyncio.run(service.assess_once('entry-cancel'))
    entry = service.admit_assessment('entry-cancel')
    now = int(service.clock()); repo.begin_send(scope, entry['command_id'], expected_revision=0, now=now)
    service._frame = lambda: frame(scope, config)
    first = service.stage_cancel(entry['command_id'])
    service.clock = lambda: first['expires_at']
    second = service.stage_cancel(entry['command_id'])
    assert second['command_id'] != first['command_id']
    assert repo.command(scope, first['command_id'])['state'] == 'ABANDONED'
    assert second['state'] == 'DURABLE_READY'


def test_no_remaining_duty_still_cancels_confirmed_live_protection(host):
    from types import SimpleNamespace
    from sqlalchemy import update
    from trading_system.storage.demo import accounts
    from test_lifecycle import report
    from trading_system.execution.demo_reports import DemoReportVerifier
    service, repo, scope, config = host
    with repo.transaction(scope.account_key) as conn:
        conn.execute(update(accounts).values(qualification={'state':'DEMO_VERIFIED', 'code_sha':'a'*40, 'protection':'HOSTED'}))
    now = int(service.clock())
    request = {'symbol':'BTCUSDT', 'side':'SELL', 'type':'LIMIT', 'quantity':'0.001', 'price':'49000', 'newClientOrderId':'leftover-exit', 'timeInForce':'GTC'}
    repo.stage(scope, command_id='leftover-exit', request=request, reserve_asset='BTC', reserve_amount='0.001', available='0.001', authorization_ref=config['authorization_ref'], expires_at=now+60, now=now, purpose='PROTECTION')
    repo.begin_send(scope, 'leftover-exit', expected_revision=0, now=now)
    event = report(c='leftover-exit', S='SELL', q='0.001', p='49000', i=45, t=-1, l='0', z='0', x='NEW', X='NEW', E=now*1000)
    DemoReportVerifier(repo).process(scope, repo.observe(scope, source='WS_EXECUTION_REPORT', payload=event, received_at=now, credential_ref='demo-session:fixture'))
    service.stream = SimpleNamespace(healthy=True, credential_ref='demo-session:fixture')
    service.owner = SimpleNamespace(transport=SimpleNamespace(last_price=lambda symbol: {'symbol':symbol, 'price':'51000'}))
    result = service.protection_tick()
    assert result['state'] == 'CANCEL_STALE_PROTECTION'
    assert result['commands'][0]['request']['origClientOrderId'] == 'leftover-exit'


def test_stop_during_frame_prevents_starting_model_call(host):
    from threading import Event
    from trading_system.engine.binance_demo.async_safety import settle_before_cancel
    service, repo, scope, config = host
    updated = {**config, 'revision':2, 'mode':'AI', 'allow_model_calls':True, 'profile':{**config['profile'], 'strategy_revision':2}}
    repo.save_strategy(scope, updated, expected_revision=1)
    entered, release = Event(), Event(); stopped = [False]; model_calls = []
    class Models:
        async def analyze(self, *args, **kwargs): model_calls.append(True); return {}
    service.models = Models()
    def frame_wait():
        entered.set(); assert release.wait(3)
        return {'task_profile':{'strategy_revision':2}, 'snapshot_hash':'fixture',
                'bindings':{'account_revision':repo.account(scope)['revision']}, 'quality':{'status':'READY'}}
    service._frame = frame_wait
    def before_model():
        if stopped[0]: raise DemoError('DEMO_STOP_REQUESTED')
    async def scenario():
        task = asyncio.create_task(settle_before_cancel(service.assess_once('stop-frame', expected_strategy=updated, before_model=before_model)))
        assert await asyncio.to_thread(entered.wait, 2)
        stopped[0] = True; task.cancel()
        await asyncio.sleep(0.05); assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(scenario())
    assert not model_calls
    assert repo.decision(scope, 'stop-frame')['error'] == 'DEMO_STOP_REQUESTED'
