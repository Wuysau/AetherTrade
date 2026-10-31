"""Offline activation-bound volume facts and VP schedule policy regressions."""
from types import SimpleNamespace
from decimal import Decimal as D
import pytest

from test_program_exposure import make_plan
from trading_system.execution.local_child_schedule import preview_local_schedule
from trading_system.execution.local_volume import (
    LocalVolumePage, bind_local_volume_window, require_verified_volume,
)


START = '2026-10-28T10:00:00Z'
NOW = '2026-10-28T10:02:00Z'


def activation(plan, *, unit='BTC'):
    return SimpleNamespace(activation_id='activation', activated_at=START,
                           authority=SimpleNamespace(scope=plan.scope,
                                                     units=SimpleNamespace(quantity_unit=unit)))


def page(start, end, quantity='10', *, verified=True, complete=True, unit='BTC',
         request='r', manifest='m', payload='p'):
    return LocalVolumePage(request_hash=request, manifest_hash=manifest,
                           payload_hash=payload, start=start, end=end,
                           quantity_unit=unit, observed_quantity=quantity,
                           source_verified=verified, coverage_complete=complete,
                           status='PAGE_BOUNDARY_OBSERVED')


def test_complete_contiguous_pages_bind_exact_volume():
    plan = make_plan('VP', 'LOCAL_SYNTHETIC')
    facts = bind_local_volume_window(activation=activation(plan), pages=(
        page(START, '2026-10-28T10:01:00Z', '10'),
        page('2026-10-28T10:01:00Z', NOW, '15', request='r2', manifest='m2', payload='p2')),
        now=NOW)
    assert facts.observed_quantity == '25' and facts.quantity_unit == 'BTC'
    assert facts.source_verified and facts.coverage_complete
    assert facts.scan_status == 'PAGE_BOUNDARY_OBSERVED'
    require_verified_volume(facts)
    assert facts.execution_authorized is False


@pytest.mark.parametrize('pages,fault', [
    ((page(START, '2026-10-28T10:01:00Z'), page('2026-10-28T10:01:01Z', NOW)), 'GAP'),
    ((page(START, '2026-10-28T10:01:00Z', unit='ETH'), page('2026-10-28T10:01:00Z', NOW)), 'UNIT'),
    ((page('2026-10-28T09:59:00Z', NOW),), 'WINDOW'),
])
def test_gap_unit_or_out_of_window_volume_is_rejected(pages, fault):
    plan = make_plan('VP', 'LOCAL_SYNTHETIC')
    with pytest.raises(ValueError, match='LOCAL_VOLUME_'):
        bind_local_volume_window(activation=activation(plan), pages=pages, now=NOW)


def test_unverified_or_short_page_is_never_headroom():
    plan = make_plan('VP', 'LOCAL_SYNTHETIC')
    facts = bind_local_volume_window(
        activation=activation(plan),
        pages=(page(START, NOW, verified=False, complete=False),), now=NOW)
    assert facts.source_verified is False and facts.coverage_complete is False
    with pytest.raises(ValueError, match='SOURCE_UNVERIFIED'):
        require_verified_volume(facts)
    facts = bind_local_volume_window(
        activation=activation(plan), pages=(page(START, '2026-10-28T10:01:00Z'),), now=NOW)
    assert facts.coverage_complete is False
    with pytest.raises(ValueError, match='COVERAGE_INCOMPLETE'):
        require_verified_volume(facts)


def test_vp_participation_can_come_from_trusted_host_policy():
    plan = make_plan('VP', 'LOCAL_SYNTHETIC')
    result = preview_local_schedule(plan, activated_at=START, now=START, interval_seconds=60,
                                    committed_quantity='0.1', quantity_step='0.001',
                                    observed_volume='25', volume_unit=plan.nodes[0].quantity_unit,
                                    participation_bps='100')
    assert D(result.scheduled_quantity) == D('0.25')
    assert D(result.available_quantity) == D('0.15')
    assert result.execution_authorized is False
