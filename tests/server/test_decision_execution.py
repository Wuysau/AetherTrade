from types import SimpleNamespace
from trading_system.server.automation import AutomationService
from trading_system.server.decision_execution import proposal_target, watch_triggered
from decimal import Decimal


def test_d11_d12_target_is_absolute_not_strategy_ceiling():
    assert proposal_target({'side':'LONG','sizing':'QUOTE_NOTIONAL','value':'10'}, Decimal('100')) == Decimal('.1')
    assert proposal_target({'side':'LONG','sizing':'BASE_QUANTITY','value':'.05'}, Decimal('100')) == Decimal('.05')


def test_d18_watch_only_on_crossing_or_new_closed_bar():
    conditions = [{'kind':'PRICE_ABOVE','value':'100','evidence_ref':'quote'}]
    assert not watch_triggered(conditions, {'price':'101'}, {'price':'102'})
    assert watch_triggered(conditions, {'price':'99'}, {'price':'101'})
    assert not watch_triggered(conditions, {'price':'99'}, {'price':None})


def test_d16_evidence_review_guard_does_not_revote_soft_disagreement():
    service = AutomationService(None,None,None,None)
    job = dict(analysis_mode='consensus', model_profile_ids=['a','b'], profile_revisions={'a':1,'b':1},
               decision_policy='evidence_review', strategy_revision=2, snapshot_hash='h', snapshot_id='s')
    decision = dict(schema_revision='assessment_v1', assessment_status='DECIDED', decision_policy='evidence_review',
        strategy_revision=2,snapshot_hash='h',snapshot_id='s', required_profile_ids=['a','b'],profile_revisions={'a':1,'b':1},
        component_results=[dict(profile_id=p,config_revision=1,snapshot_hash='h',status='SUCCEEDED') for p in ['a','b']],
        review_report={'status':'SOFT'},adjudication_history=[{'profile_id':'a','status':'SUCCEEDED','snapshot_hash':'h','config_revision':1}])
    assert service._consensus_guard(job, decision) is None
    decision['component_results'].pop()
    assert service._consensus_guard(job, decision)[0] == 'BLOCKED'

import json
from pathlib import Path
from contextlib import nullcontext
from datetime import timedelta
import pytest
from trading_system.server.paper_runtime import UnifiedPaperRuntime
from trading_system.server.store import now
from trading_system.server.errors import ApiError


def proposal(value='0.05'):
    p = json.loads(Path('tests/fixtures/contracts/examples/decision-proposal.paper.json').read_text(encoding='utf-8'))
    expiry = (now() + timedelta(seconds=60)).isoformat()
    p.update(instrument_ref='console-paper:BTCUSDT', action='SET_TARGET', valid_until=expiry)
    p['target'] = dict(side='LONG', sizing='BASE_QUANTITY', value=format(Decimal(value), 'f'))
    p['entry'].update(order_preference='MARKET', limit_price=None, entry_deadline=expiry,
                      maximum_entry_slippage_bps='20', allow_partial=True)
    p['exit_plan'].update(stop_price='95', stop_trigger='LAST', review_due_at=expiry)
    return p


def adapter(position=None, commands=None):
    runtime = object.__new__(UnifiedPaperRuntime)
    runtime.clock = lambda: SimpleNamespace(utc_now=now().isoformat())
    runtime.control_guard = nullcontext
    runtime.projection = lambda: {'commands': commands or []}
    runtime.account_view = lambda: {'positions': [position] if position else []}
    captured = []
    runtime._accept = lambda *args: captured.append(args) or {'status':'PENDING'}
    return runtime, captured


def accept(runtime, p):
    strategy = dict(id='s',symbol='BTCUSDT',budget='1000',target_pct='25')
    return runtime.accept_proposal(p, 'key', {'quotes':[{'symbol':'BTCUSDT','price':'100'}]}, 90,
        {'account': {'paused':False,'initial_cash':'10000'}}, strategy, {'id':'d','snapshot_id':'snapshot'})


def test_d10_d11_target_proposal_reaches_durable_adapter_without_max_fill():
    runtime, calls = adapter()
    accept(runtime, proposal())
    assert Decimal(calls[0][0]['quantity']) == Decimal('0.05')
    assert calls[0][0]['proposal']['exit_plan']['stop_price'] == '95'


def test_d12_partial_reduction_uses_delta_not_full_sell():
    runtime, calls = adapter({'symbol':'BTCUSDT','strategy_id':'s','quantity':'.2'})
    accept(runtime, proposal('.1'))
    assert Decimal(calls[0][0]['quantity']) == Decimal('.1')
    assert calls[0][0]['side'] == 'SELL'


def test_d11_repeated_target_has_no_second_command():
    runtime, calls = adapter({'symbol':'BTCUSDT','strategy_id':'s','quantity':'.05'})
    assert accept(runtime, proposal())['status'] == 'NO_ACTION'
    assert not calls


def test_d12_unknown_cancel_is_not_free_capacity():
    runtime, calls = adapter(commands=[dict(symbol='BTCUSDT',order_status='CANCEL_UNKNOWN',operation='CANCEL')])
    with pytest.raises(ApiError):
        accept(runtime, proposal())
    assert not calls


def test_d19_expired_proposal_never_reaches_adapter():
    runtime, calls = adapter()
    p = proposal()
    p['valid_until'] = (now()-timedelta(seconds=1)).isoformat()
    with pytest.raises(ApiError):
        accept(runtime,p)
    assert not calls

from server.test_automation import system, strategy
from trading_system.server.automation import read
from trading_system.server.store import strategies


@pytest.mark.paper_runtime
def test_d10_d11_real_unified_proposal_creates_only_target_and_preserves_exit(system):
    service = system
    saved = strategy(service)
    runtime = service.store.execution
    p = proposal('0.001')
    # The shared synthetic quote is 60,000; retain a valid exact stop.
    price = Decimal(service.quote_snapshot()['quotes'][0]['price'])
    p['exit_plan']['stop_price'] = format(price * Decimal('.95'), 'f')
    with service.store.transaction() as connection:
        current = read(connection, strategies, saved['id'])
        receipt = runtime.accept_proposal(p, 'proposal-integration', service.quote_snapshot(),
            service.settings.quote_max_age_seconds, service.store._state(connection), current,
            {'id':'assessment-fixture','snapshot_id':'evidence-fixture'})
    assert Decimal(receipt['quantity']) == Decimal('.001')
    assert runtime.account_view()['positions'] == []
    with runtime.uow.transaction(__import__('trading_system.server.paper_runtime', fromlist=['scope_for']).scope_for('BTCUSDT')) as tx:
        request = runtime._request(tx, receipt['command_id'])
        assert request.intent.target_quantity == '0.001'
        assert request.intent.exit_plan.stop_price == p['exit_plan']['stop_price']
    with service.store.transaction() as connection:
        with pytest.raises(ApiError):
            runtime.accept_proposal(p, 'proposal-second', service.quote_snapshot(),
                service.settings.quote_max_age_seconds, service.store._state(connection), current,
                {'id':'assessment-second','snapshot_id':'evidence-second'})
    assert len(runtime.projection()['commands']) == 1
