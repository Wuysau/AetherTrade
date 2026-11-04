"""Hosted exit updates tighten committed responsibilities without venue IO."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import pytest
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.execution.demo_reports import DemoReportVerifier
from trading_system.server.demo_snapshot import snapshot_hash
from test_host import host, frame, assessment
from test_lifecycle import report


def open_position(service, repo, scope):
    asyncio.run(service.assess_once('entry'))
    command = service.admit_assessment('entry')
    repo.begin_send(scope, command['command_id'], expected_revision=0, now=int(service.clock()))
    for trade_id, cumulative, status, total_quote in [(33, '0.0005', 'PARTIALLY_FILLED', '25'), (34, '0.001', 'FILLED', '50')]:
        event = report(c=command['client_id'], t=trade_id, q='0.001', l='0.0005', z=cumulative, X=status, Y='25', Z=total_quote, n='0', N='USDT', E=int(service.clock()*1000))
        oid = repo.observe(scope, source='WS_EXECUTION_REPORT', payload=event, received_at=int(service.clock()), credential_ref='demo-session:fixture')
        DemoReportVerifier(repo).process(scope, oid)
    return command


@pytest.mark.parametrize('hybrid_veto', [False, True])
def test_update_exit_uses_committed_head_and_tightens_all_duties_atomically(host, hybrid_veto):
    service, repo, scope, config = host
    config['profile']['allowed_operations'].append('UPDATE_EXIT')
    # Save config before any decision; the fixture's persisted JSON is independent.
    with repo.transaction(scope.account_key) as conn:
        from trading_system.storage.demo import strategies
        from sqlalchemy import update
        conn.execute(update(strategies).values(config=config))
    def no_leverage(snapshot):
        value = assessment(snapshot); value['proposal'].pop('requested_leverage', None); return value
    service.rule_assessor = no_leverage
    open_position(service, repo, scope)
    previous = repo.plan_head(scope)
    opened_at = repo.protection_duties(scope)[0]['opened_at']
    def current():
        value = frame(scope, config)
        value['portfolio']['previous_proposal'] = repo.plan_head(scope)['proposal']
        value['portfolio']['position'] = {'symbol': 'BTCUSDT', 'quantity': '0.001'}
        value['bindings']['account_revision'] = repo.account(scope)['revision']
        value['bindings']['plan_revision'] = repo.plan_head(scope)['revision']
        value['bindings']['protection_hash'] = repo.duties_hash(repo.protection_duties(scope))
        value['snapshot_hash'] = snapshot_hash(value); value['snapshot_id'] = 'demo-snapshot:' + value['snapshot_hash'][:32]
        return value
    service.frame_loader = current
    def tighten(snapshot):
        value = assessment(snapshot)
        value['proposal'] = deepcopy(snapshot['portfolio']['previous_proposal'])
        value['proposal'].update(action='UPDATE_EXIT', proposal_id='tighten-exit')
        deadline = (datetime.now(timezone.utc)+timedelta(seconds=90)).isoformat()
        value['proposal']['valid_until'] = deadline
        value['proposal']['entry']['entry_deadline'] = deadline
        value['proposal']['exit_plan'].update(stop_price='49500', maximum_holding_seconds=1800, review_due_at=deadline)
        return value
    service.rule_assessor = tighten
    asyncio.run(service.assess_once('tighten'))
    row = repo.decision(scope, 'tighten')
    candidate = row['result']['decision']['proposal']
    with pytest.raises(DemoError, match='EXPIRED'):
        repo.apply_exit_update(scope, 'tighten', candidate=candidate, expected_account_revision=repo.account(scope)['revision'],
            expected_plan_revision=previous['revision'], expected_duties_hash=repo.duties_hash(repo.protection_duties(scope)), strategy=config,
            now=int(service.clock()), clock=lambda: int(datetime.fromisoformat(candidate['valid_until']).timestamp())+1, bid='49999')
    assert repo.plan_head(scope)['revision'] == previous['revision']
    if hybrid_veto:
        from trading_system.runtime.demo_strategy import ENVELOPE
        service._strategy = lambda: {**config, 'mode': 'HYBRID'}
        service._validate_receipt = lambda row, strategy: {key: row['result']['decision'].get(key) for key in ENVELOPE}
        service.hybrid_gate = lambda value, snapshot: False
        with pytest.raises(DemoError, match='HYBRID_GATE'):
            service.admit_assessment('tighten')
        assert repo.plan_head(scope)['revision'] == previous['revision']
        return
    result = service.admit_assessment('tighten')
    assert result['state'] == 'APPLIED' and result['purpose'] == 'UPDATE_EXIT'
    assert service.admit_assessment('tighten')['management_id'] == result['management_id']
    duty = repo.protection_duties(scope)[0]
    assert duty['plan']['stop_price'] == '49500' and duty['opened_at'] == opened_at
    assert duty['remaining_quantity'] == '0.0005'
    assert len(repo.protection_duties(scope)) == 2
    assert all(item['plan']['stop_price'] == '49500' for item in repo.protection_duties(scope))
    assert repo.plan_head(scope)['revision'] == previous['revision'] + 1
    service.rule_assessor = lambda snapshot: {**tighten(snapshot), 'proposal': {**tighten(snapshot)['proposal'], 'exit_plan': {**tighten(snapshot)['proposal']['exit_plan'], 'stop_price': '48000'}}}
    asyncio.run(service.assess_once('loosen'))
    with pytest.raises(DemoError, match='LOOSEN'):
        service.admit_assessment('loosen')
    assert repo.protection_duties(scope)[0]['plan']['stop_price'] == '49500'


def test_demo_update_exit_may_tighten_above_entry_using_current_bid(host):
    from trading_system.server.decision_assessment import validate_assessment
    service, repo, scope, config = host
    open_position(service, repo, scope)
    snapshot = frame(scope, config)
    snapshot['task_profile']['allowed_operations'].append('UPDATE_EXIT')
    snapshot['portfolio']['previous_proposal'] = repo.plan_head(scope)['proposal']
    snapshot['market']['quote']['price'] = '55000'
    snapshot['market']['book'].update(bid='54999', ask='55001')
    value = assessment(snapshot)
    value['proposal'].update(action='UPDATE_EXIT')
    value['proposal']['exit_plan']['stop_price'] = '53000'
    assert validate_assessment(value, snapshot)['proposal']['entry']['limit_price'] == '50000'
    snapshot['schema_revision'] = 'analysis-snapshot-v1'
    with pytest.raises(ValueError, match='protective stop'):
        validate_assessment(value, snapshot)
