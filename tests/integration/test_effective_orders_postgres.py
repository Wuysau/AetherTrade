"""Actual funded effective-order execution; no environment-skipped qualification."""
from decimal import Decimal as D
import pytest
from trading_system.contracts import types as t
from integration.test_program_management_postgres import (
    isolated_postgres,database,venue,venue_v2,management_database,management_venue,advanced_case,management,submit,event)

def test_keep_priority_actual_funded_quantity_reduction(management_database,venue_v2,management_venue,tmp_path):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.execution.program_management_paper import submit_management
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    s=management(c)
    w=s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity='0.5'),c.lease)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        result=submit_management(c.engine,w,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    assert result['effective_quantity']=='0.5'
    report=c.engine.query_creation(c.write.command_id)
    assert report.orders[0].quantity==D('0.5')


def test_keep_priority_partial_source_fifo_and_repeated_reduction(management_database,venue_v2,management_venue,tmp_path):
    from uuid import uuid4
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.execution.program_management_paper import submit_management
    from trading_system.execution.program_effective_orders import PaperManagedCreationReportV1,observe_effective_management,SOURCE
    from trading_system.engine.paper_market import PaperMatcher
    from trading_system.risk.reservation_repository import ReservationRepository
    market='effective-'+uuid4().hex
    first=advanced_case(management_database,venue_v2,tmp_path,market=market);submit(first)
    second=advanced_case(management_database,venue_v2,tmp_path,market=market);submit(second)
    matcher=PaperMatcher(first.engine);matcher.apply_market_event(event(first,quantity='.2'))
    s=management(first)
    initial=first.engine.query_creation(first.write.command_id)
    def amend(q):
        w=s.admit(first.write.command_id,ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity=q),first.lease)
        permit=s.boundary.mark_may_have_sent(w.command_id,first.lease)
        with first.owner.session(first.lease.shard):
            submit_management(first.engine,w,permit,boundary=s.boundary,lease=first.lease,owner=first.owner,bridge=management_venue)
        return w
    w=amend('0.5')
    report=first.engine.query_creation(first.write.command_id)
    assert type(report) is PaperManagedCreationReportV1 and report.evidence.source==SOURCE
    assert report.accepted_contexts==initial.accepted_contexts
    assert report.effective_history[0].original_quantity==D('1')
    assert report.orders[0].quantity==D('.5') and report.filled_quantity==D('.2') and report.remaining_quantity==D('.3')
    assert report.orders[0].runtime.queue_priority==initial.orders[0].runtime.queue_priority
    with pytest.raises(Exception,match='existing management duty'):amend('0.4')
    observe_effective_management(s,w.command_id)
    next_write=amend('0.4');observe_effective_management(s,next_write.command_id)
    assert len(first.engine.query_creation(first.write.command_id).effective_history)==2
    matcher.apply_market_event(event(first,sequence=2,quantity='.4',kind='DEPTH_DELTA'))
    final=first.engine.query_creation(first.write.command_id)
    assert final.status=='FILLED' and final.filled_quantity==D('.4')
    assert second.engine.query_creation(second.write.command_id).filled_quantity==D('.2')
    with first.service.uow.transaction(first.scope) as tx:
        assert ReservationRepository(tx).get(first.bundle.reservations[0].reservation.reservation_id).held==first.bundle.reservations[0].held


def test_effective_source_tables_are_not_runtime_writable(management_database,venue_v2,management_venue,tmp_path):
    import psycopg
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.engine._connect() as db:db.execute('INSERT INTO public.paper_effective_orders_v1 DEFAULT VALUES')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.owner.transaction(c.scope) as tx:tx.execute('INSERT INTO ledger.management_effective_resolutions_v1 DEFAULT VALUES')
