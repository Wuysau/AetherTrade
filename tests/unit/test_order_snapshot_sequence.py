"""Pure projector interface tests; actual mixed-stream proof is in PG."""
from dataclasses import replace
from decimal import Decimal as D
import pytest
from integration.support_ip_12 import initial_projection,domain_event,STAMP
from trading_system.contracts import types as t
from trading_system.execution.projections import OrderProjector
from trading_system.storage.models import SequenceConflict,ScopeMismatch


def actual():
    previous=replace(initial_projection(),sequence=1,lifecycle_state=t.OrderLifecycle.PARTIALLY_FILLED,
        filled_quantity=t.Quantity(unit='BTC',value=D('.4')),leaves_quantity=t.Quantity(unit='BTC',value=D('.6')),
        command_certainty='CONFIRMED',pending_operation='NONE',outcome_provenance='VENUE_QUERY',lifecycle_revision=1)
    current=replace(previous,filled_quantity=t.Quantity(unit='BTC',value=D('.6')),
        leaves_quantity=t.Quantity(unit='BTC',value=D('.4')),lifecycle_revision=2)
    ref=t.EvidenceRef(id='pure-fixture',hash='a'*64,environment=t.Environment.PAPER,source='UNIT_ONLY',visible_at=STAMP)
    return previous,domain_event(previous,current,revision=4,evidence=(ref,))


def test_snapshot_interface_keeps_actual_event_object_and_old_apply_gap_denial():
    previous,event=actual();seen=[];projector=OrderProjector(require_evidence=lambda value:seen.append(value))
    with pytest.raises(SequenceConflict):projector.apply(event,previous)
    projected=projector.apply_observation(event,previous,expected_event_revision=3).projection
    assert seen==[event] and seen[0] is event
    assert projected.sequence==2 and projected.filled_quantity.value==D('.6') and projected.leaves_quantity.value==D('.4')
    with pytest.raises(SequenceConflict):projector.apply_observation(event,previous,expected_event_revision=2)
    with pytest.raises(ScopeMismatch):projector.apply_observation(replace(event,aggregate_id='order:foreign'),previous,expected_event_revision=3)
    with pytest.raises(ValueError):projector.apply_observation(event,previous,expected_event_revision=True)
