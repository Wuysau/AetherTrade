from decimal import Decimal as D,localcontext
import pytest
from test_program_exposure import make_plan
from trading_system.execution.local_child_schedule import preview_local_schedule

START='2026-10-28T10:00:00Z'

def test_twap_releases_declared_slots_and_retains_committed_unknown_quantity():
    plan=make_plan('TWAP','LOCAL_SYNTHETIC')
    with localcontext() as ctx:
        ctx.prec=1
        first=preview_local_schedule(plan,activated_at=START,now=START,interval_seconds=60,
            committed_quantity='0',quantity_step='0.001')
        later=preview_local_schedule(plan,activated_at=START,now='2026-10-28T10:01:00Z',interval_seconds=60,
            committed_quantity='0.3',quantity_step='0.001')
    assert D(first.available_quantity)==D('.2')
    assert D(later.scheduled_quantity)==D('.4') and D(later.available_quantity)==D('.1')
    assert not first.execution_authorized and not first.funds_release_authorized

def test_closed_schedule_does_not_mean_child_outcomes_are_complete():
    result=preview_local_schedule(make_plan('TWAP','LOCAL_SYNTHETIC'),activated_at=START,
        now='2026-10-28T10:05:00Z',interval_seconds=60,committed_quantity='0.4',quantity_step='0.001')
    assert result.window_closed and D(result.available_quantity)==0
    assert D(result.unallocated_quantity)==D('.6') and not result.funds_release_authorized

def test_vp_requires_native_volume_and_applies_participation_without_float():
    plan=make_plan('VP','LOCAL_SYNTHETIC')
    result=preview_local_schedule(plan,activated_at=START,now=START,interval_seconds=60,
        committed_quantity='0.1',quantity_step='0.001',observed_volume='25',volume_unit=plan.nodes[0].quantity_unit)
    assert D(result.scheduled_quantity)==D('.25') and D(result.available_quantity)==D('.15')

@pytest.mark.parametrize('change',[{'interval_seconds':True},{'interval_seconds':0},
    {'committed_quantity':'1.1'},{'quantity_step':'0'},{'quantity_step':'0.3'},
    {'now':'2026-10-28T09:59:59Z'}])
def test_invalid_clock_grid_or_budget_never_yields_a_slice(change):
    args=dict(activated_at=START,now=START,interval_seconds=60,committed_quantity='0',quantity_step='0.001')
    with pytest.raises(ValueError):preview_local_schedule(make_plan('TWAP','LOCAL_SYNTHETIC'),**{**args,**change})

@pytest.mark.parametrize('volume,unit',[(None,None),('1','wrong'),('-1','BTC')])
def test_vp_missing_or_unbound_volume_is_not_assumed_zero(volume,unit):
    with pytest.raises(ValueError):preview_local_schedule(make_plan('VP','LOCAL_SYNTHETIC'),activated_at=START,
        now=START,interval_seconds=60,committed_quantity='0',quantity_step='0.001',observed_volume=volume,volume_unit=unit)


@pytest.mark.parametrize('now,expected',[('2026-10-28T10:00:59.999999999Z','0.2'),
    ('2026-10-28T10:01:00Z','0.4'),('2026-10-28T10:04:40Z','1')])
def test_exact_slot_boundaries_and_short_final_window(now,expected):
    interval=70 if now.endswith('04:40Z') else 60
    result=preview_local_schedule(make_plan('TWAP','LOCAL_SYNTHETIC'),activated_at=START,now=now,
        interval_seconds=interval,committed_quantity='0',quantity_step='0.001')
    assert D(result.scheduled_quantity)==D(expected)
