from dataclasses import replace

import pytest

from unit.test_program_commands import creation
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.execution.program_commands import build_creation_write


def case(states=('DURABLE_READY','MAY_HAVE_SENT','UNKNOWN')):
    request,bundle=creation()
    initial,binding=build_creation_write(request,bundle,execution_epoch=4)
    writes=tuple(replace(initial,state=s) for s in states)
    mark=domain_payload(replace(initial,state='MAY_HAVE_SENT')) if len(writes)>1 else None
    return writes,binding,bundle,mark


def restore(data):
    from trading_system.recovery.original_creation import _restore_original_creation
    return _restore_original_creation(*data)


def test_historical_identity_does_not_run_a_new_risk_assessment(monkeypatch):
    data=case()
    def no_new_assessment(*args,**kwargs):raise AssertionError('historical identity must not reassess risk')
    monkeypatch.setattr('trading_system.risk.evaluator.RiskEvaluator.evaluate_program',no_new_assessment)
    result=restore(data)
    assert result.initial==data[0][0] and result.request==data[1].request
    assert result.current_state=='UNKNOWN' and not result.can_resubmit and not result.execution_authorized


@pytest.mark.parametrize('fault',['request','binding','archive','history','missing_mark','false_mark'])
def test_immutable_hash_or_protected_history_corruption_is_not_adopted(fault):
    writes,binding,bundle,mark=case()
    if fault=='request':binding=replace(binding,request=replace(binding.request,command_id='other-command'))
    if fault=='binding':binding=replace(binding,intent_id='other-intent')
    if fault=='archive':bundle=replace(bundle,archive=replace(bundle.archive,intent_id='other-intent'))
    if fault=='history':writes=(writes[0],replace(writes[1],state='UNKNOWN'))
    if fault=='missing_mark':mark=None
    if fault=='false_mark':mark={**mark,'request_hash':'f'*64}
    with pytest.raises(ValueError):restore((writes,binding,bundle,mark))


def test_query_targets_and_scheduler_keep_existing_ids_after_submission_expires(monkeypatch):
    data=case();result=restore(data)
    from trading_system.recovery.original_creation import original_query_targets,original_creation_case
    def no_new_id(*args,**kwargs):raise AssertionError('query must not assign a new order identity')
    monkeypatch.setattr('trading_system.execution.creation_requests.assign_client_ids',no_new_id)
    targets=original_query_targets(result)
    assert targets[0].client_id==data[1].request.assigned_ids.members[0].client_id
    clock=t.ClockReading(utc_now='2027-01-01T00:00:00Z',monotonic_ns=100,clock_health='HEALTHY')
    budget=t.QueryBudget(maximum_requests=2,window_seconds=30,minimum_interval_ms=1000,retry_after=None)
    case=original_creation_case(result,now=clock,budget=budget)
    from trading_system.recovery.cases import CaseScheduler
    assert CaseScheduler((case,)).snapshot()==(case,)
    assert case.command_ids==(result.initial.command_id,) and not result.can_resubmit


def test_ready_original_does_not_become_an_unknown_case():
    result=restore(case(('DURABLE_READY',)))
    from trading_system.recovery.original_creation import original_creation_case
    clock=t.ClockReading(utc_now='2027-01-01T00:00:00Z',monotonic_ns=100,clock_health='HEALTHY')
    budget=t.QueryBudget(maximum_requests=2,window_seconds=30,minimum_interval_ms=1000,retry_after=None)
    with pytest.raises(ValueError,match='POTENTIALLY_SENT_ORIGINAL_REQUIRED'):
        original_creation_case(result,now=clock,budget=budget)
