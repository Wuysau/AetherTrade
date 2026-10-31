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
    from trading_system.execution.program_effective_orders import ManagedCreationCodecV1
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    codec=ManagedCreationCodecV1();assert codec.decode(codec.encode(report))==report
    bad=codec.encode(report);bad['unknown']=True
    with pytest.raises(ValueError,match='CLOSED_FIELDS'):codec.decode(bad)
    before=c.engine._clock
    clock=before();c.engine._clock=lambda:replace(clock,utc_now=(utc_timestamp(clock.utc_now)+timedelta(milliseconds=1)).isoformat())
    try:assert c.engine.query_creation(c.write.command_id).evidence.hash==report.evidence.hash
    finally:c.engine._clock=before


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
        with c.service.uow.transaction(c.scope) as tx:tx.execute('INSERT INTO ledger.management_effective_resolutions_v1 DEFAULT VALUES')


def admitted(database,venue,tmp_path,*,partial=False):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.engine.paper_market import PaperMatcher
    c=advanced_case(database,venue,tmp_path);submit(c)
    if partial:PaperMatcher(c.engine).apply_market_event(event(c,quantity='.2'))
    service=management(c)
    w=service.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity='0.5'),c.lease)
    return c,service,w

from dataclasses import replace
from integration.test_program_management_postgres import (
    _hold_management_lock,_management_venue_admin,_await_actual_management_lock,
    _await_original_authorization_expiry,_assert_wait_failure_retains_original,_stage_actual_private_bridge,
    _revoke_actual_deployment)


@pytest.mark.parametrize('phase',['mark','consume','after_wait'])
def test_amend_actual_target_fill_race_stops_stale_cancel(management_database,venue_v2,tmp_path,phase):
    from trading_system.engine.paper_market import PaperMatcher
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease) if phase!='mark' else None
    if phase=='after_wait':s.boundary.consume(permit,c.lease)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    with pytest.raises(ValueError,match='TARGET_CHANGED'):
        if phase=='mark':s.boundary.mark_may_have_sent(w.command_id,c.lease)
        elif phase=='consume':s.boundary.consume(permit,c.lease)
        else:s.boundary.recheck_consumed(permit,c.lease)
    assert c.engine.query_creation(c.write.command_id).filled_quantity==D('.4')


def test_amend_lost_cancel_ack_recovers_original_id_without_retry_or_release(management_database,venue_v2,management_venue,tmp_path):
    from trading_system.execution.program_management_paper import submit_management
    from trading_system.execution.program_effective_orders import observe_effective_management as observe_paper_management
    from trading_system.execution.program_management_commands import ManagementRepository
    c,s,w=admitted(management_database,venue_v2,tmp_path);permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    original=c.engine._connect
    class LostAck:
        def __init__(self):self.db=original();self.wrote=False
        def __getattr__(self,name):return getattr(self.db,name)
        def __enter__(self):self.db.__enter__();return self
        def execute(self,sql,*args,**kwargs):
            self.wrote=self.wrote or 'SELECT public.paper_manage_order_v4' in sql
            return self.db.execute(sql,*args,**kwargs)
        def __exit__(self,*args):
            result=self.db.__exit__(*args)
            if self.wrote and args[0] is None:raise OSError('lost ACK after actual SIM commit')
            return result
    c.engine._connect=LostAck
    with c.owner.session(c.lease.shard):
        with pytest.raises(OSError,match='lost ACK'):
            submit_management(c.engine,w,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    c.engine._connect=original
    with s.verifier_uow.transaction(c.scope) as tx:assert ManagementRepository(tx).read(w.command_id).write.state=='UNKNOWN'
    result=observe_paper_management(s,w.command_id)
    assert result['effective_quantity']=='0.5' and result['command_id']==w.command_id and not result['funds_released']
    with pytest.raises(ValueError):s.boundary.mark_may_have_sent(w.command_id,c.lease)
    assert c.engine.query_creation(c.write.command_id).orders[0].quantity==D('.5')


@pytest.mark.parametrize('lock_kind',['advisory','row'])
def test_amend_expired_permit_cannot_cancel_after_actual_venue_mutex_wait(
        management_database,venue_v2,management_venue,tmp_path,lock_kind):
    """Reviewed P1, extended to both actual lock waits; no artificial PG clock."""
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from trading_system.execution.program_management_paper import submit_management
    c,service,write=admitted(management_database,venue_v2,tmp_path)
    permit=service.boundary.mark_may_have_sent(write.command_id,c.lease)
    with c.engine._connect() as blocker:
        _hold_management_lock(blocker,c,write,lock_kind)
        def dispatch():
            with c.owner.session(c.lease.shard):
                return submit_management(c.engine,write,permit,boundary=service.boundary,
                    lease=c.lease,owner=c.owner,bridge=management_venue)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(dispatch)
            try:
                with psycopg.connect(_management_venue_admin(management_database,venue_v2),autocommit=True) as monitor:
                    _await_actual_management_lock(monitor,future,lock_kind)
                    _await_original_authorization_expiry(monitor,c,write)
            finally:blocker.commit()
            with pytest.raises((ValueError,psycopg.Error)):future.result(timeout=10)
    _assert_wait_failure_retains_original(c,service,write)


@pytest.mark.parametrize('lock_kind',['advisory','row'])
@pytest.mark.parametrize('change',['deployment','owner_epoch','lease_epoch'])
def test_amend_actual_authority_change_during_venue_wait_is_reloaded_after_lock(
        management_database,venue_v2,management_venue,tmp_path,lock_kind,change):
    from concurrent.futures import ThreadPoolExecutor
    import psycopg
    from trading_system.execution.program_management_paper import submit_management
    c,service,write=admitted(management_database,venue_v2,tmp_path)
    permit=service.boundary.mark_may_have_sent(write.command_id,c.lease)
    with c.engine._connect() as blocker:
        _hold_management_lock(blocker,c,write,lock_kind)
        def dispatch():
            with c.owner.session(c.lease.shard):
                return submit_management(c.engine,write,permit,boundary=service.boundary,
                    lease=c.lease,owner=c.owner,bridge=management_venue)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(dispatch)
            try:
                with psycopg.connect(_management_venue_admin(management_database,venue_v2),autocommit=True) as monitor:
                    _await_actual_management_lock(monitor,future,lock_kind)
                    if change=='deployment':_revoke_actual_deployment(c)
                    elif change=='owner_epoch':
                        with psycopg.connect(management_database) as admin:
                            admin.execute('ALTER TABLE ledger.active_owners DISABLE TRIGGER owner_immutable')
                            admin.execute('UPDATE ledger.active_owners SET ownership_epoch=ownership_epoch+1 WHERE account_id=%s',(c.scope.venue_account_id,))
                            admin.execute('ALTER TABLE ledger.active_owners ENABLE TRIGGER owner_immutable')
                    else:service.load_lease=lambda _:replace(c.lease,shard=replace(c.lease.shard,execution_epoch=2))
                    assert monitor.execute('SELECT expires_at>clock_timestamp() FROM public.paper_management_authorizations_v4 WHERE scope_key=%s AND command_id=%s',
                        (c.engine._scope_key,write.command_id)).fetchone()==(True,),'authority test must unlock before expiry'
            finally:blocker.commit()
            expected={'deployment':'DEPLOYMENT_FENCE_REQUIRED','owner_epoch':'STALE_OWNER','lease_epoch':'CURRENT_LEASE_REQUIRED'}[change]
            with pytest.raises(ValueError,match=expected):future.result(timeout=10)
    _assert_wait_failure_retains_original(c,service,write)


@pytest.mark.parametrize('change',['deployment','expiry'])
def test_amend_final_guard_rolls_back_actual_venue_mutation_before_commit(
        management_database,venue_v2,management_venue,tmp_path,change):
    import psycopg
    from trading_system.execution.program_management_paper import submit_management
    c,service,write=admitted(management_database,venue_v2,tmp_path)
    permit=service.boundary.mark_may_have_sent(write.command_id,c.lease)
    original=c.engine._connect;executed=[]
    class DelayedResult:
        def __init__(self):self.db=original()
        def __getattr__(self,name):return getattr(self.db,name)
        def __enter__(self):self.db.__enter__();return self
        def __exit__(self,*args):return self.db.__exit__(*args)
        def execute(self,sql,*args,**kwargs):
            cursor=self.db.execute(sql,*args,**kwargs)
            if sql.startswith('SELECT public.paper_manage_order_v4('):
                executed.append(True)
                if change=='deployment':_revoke_actual_deployment(c)
                else:
                    with psycopg.connect(_management_venue_admin(management_database,venue_v2),autocommit=True) as monitor:
                        _await_original_authorization_expiry(monitor,c,write)
            return cursor
    c.engine._connect=DelayedResult
    try:
        with c.owner.session(c.lease.shard):
            with pytest.raises(ValueError):
                submit_management(c.engine,write,permit,boundary=service.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    finally:c.engine._connect=original
    assert executed==[True],'the real venue mutation must precede the late guard failure'
    _assert_wait_failure_retains_original(c,service,write)


def test_amend_outlives_creation_ttl_with_fresh_grant(management_database,venue_v2,management_venue,tmp_path):
    from unittest.mock import patch
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.execution.program_management_paper import submit_management
    import integration.test_paper_market_matcher as fixture_module
    original_case=fixture_module.real_case
    short='2026-10-28T10:00:30Z'
    def short_case(*args,proposal_transform=None,**kwargs):
        def transform(p):
            p=proposal_transform(p) if proposal_transform else p
            return replace(p,valid_until=short,program=replace(p.program,submit_valid_until=short))
        return original_case(*args,proposal_transform=transform,**kwargs)
    with patch.object(fixture_module,'real_case',short_case):c=advanced_case(management_database,venue_v2,tmp_path)
    submit(c)
    base=c.engine._clock();later=replace(base,utc_now='2026-10-28T10:00:31Z',monotonic_ns=base.monotonic_ns+31*10**9)
    assert utc_timestamp(c.write.valid_until)<utc_timestamp(later.utc_now)
    c.engine._clock=lambda:later
    c.lease=replace(c.lease,expires_at='2026-10-28T10:01:00Z');c.owner.lease=c.lease
    s=management(c);read=s.load_inputs
    def current_grant(*args):
        inputs=read(*args)
        return replace(inputs,authorization=replace(inputs.authorization,authorization=replace(
            inputs.authorization.authorization,expires_at='2026-10-28T10:01:00Z')))
    s.load_inputs=current_grant
    w=s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity="0.5"),c.lease)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    with c.owner.session(c.lease.shard):
        result=submit_management(c.engine,w,permit,boundary=s.boundary,lease=c.lease,owner=c.owner,bridge=management_venue)
    assert result['effective_quantity']=='0.5' and c.engine.query_creation(c.write.command_id).filled_quantity==0


@pytest.mark.parametrize('quantity',['0.2','0.1','1'])
def test_amend_cannot_erase_fill_or_increase_current_quantity(management_database,venue_v2,management_venue,tmp_path,quantity):
    from trading_system.intents.order_management_compilation import ManagementInstruction,ManagementKind
    from trading_system.engine.paper_market import PaperMatcher
    c=advanced_case(management_database,venue_v2,tmp_path);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.2'))
    s=management(c)
    with pytest.raises(Exception,match='KEEP_PRIORITY_REDUCTION_REQUIRED'):
        s.admit(c.write.command_id,ManagementInstruction(kind=ManagementKind.KEEP_PRIORITY,new_quantity=quantity),c.lease)
    assert c.engine.query_creation(c.write.command_id).orders[0].quantity==D('1')
