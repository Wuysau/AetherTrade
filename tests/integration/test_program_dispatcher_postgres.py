"""Actual admission/MAY/consume/SIM writes and restart-safe original-ID recovery."""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from integration.test_paper_market_matcher import database,isolated_postgres,venue,case,event
from trading_system.storage.models import scope_id


def dispatcher(c,*,guard=True):
    from trading_system.execution.program_dispatcher import ProgramCommandDispatcher
    @contextmanager
    def side_effect(write,permit,lease):
        # The fixture uses the real owner's same RLock. Deployment/controller
        # serialization remains a separately qualified host responsibility.
        with c.owner.guard(lease):yield
    return ProgramCommandDispatcher(c.service.uow,c.scope,boundary=c.gate,engine=c.engine,
        owner=c.owner,side_effect_guard=side_effect if guard else None)


def test_dispatcher_commits_before_actual_single_use_and_never_resends_on_recovery(database,venue,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.2'))
    d=dispatcher(c)
    with c.owner.session(c.lease.shard):
        result=d.dispatch(c.write.command_id,c.lease)
        assert result.status=='OBSERVED' and result.observation.status=='PARTIALLY_FILLED'
        assert not result.execution_authorized and result.request_hash==c.write.request_hash
        again=d.dispatch(c.write.command_id,c.lease)
        assert again.status=='RECOVERY_REQUIRED' and again.observation is None
        assert again.original.request==c.request and not again.original.can_resubmit
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_dispatch_marks WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(1,)
        assert tx.reservations.for_intent(c.bundle.intent.intent_id)[0].state=='HELD'
    assert len(c.engine.query_creation(c.write.command_id).fills)==1


def test_actual_write_with_lost_ack_records_unknown_and_preserves_original_ids(database,venue,tmp_path,monkeypatch):
    from trading_system.engine.paper import PaperEngine
    from trading_system.engine.paper_market import PaperMatcher
    c=case(database,venue,tmp_path);PaperMatcher(c.engine).apply_market_event(event(c,quantity='.2'))
    original=PaperEngine.submit_creation
    def lose_ack(*args,**kwargs):
        original(*args,**kwargs)
        raise OSError('fixture acknowledgement lost after actual SIM commit')
    monkeypatch.setattr(PaperEngine,'submit_creation',lose_ack)
    with c.owner.session(c.lease.shard):
        result=dispatcher(c).dispatch(c.write.command_id,c.lease)
        assert result.status=='UNKNOWN' and result.original.current_state=='UNKNOWN'
        retry=dispatcher(c).dispatch(c.write.command_id,c.lease)
        assert retry.status=='RECOVERY_REQUIRED' and retry.original.request.assigned_ids==c.request.assigned_ids
    assert len(c.engine.query_creation(c.write.command_id).fills)==1


def test_durable_may_survives_process_boundary_exception_before_engine_and_never_mints_again(database,venue,tmp_path,monkeypatch):
    c=case(database,venue,tmp_path);original=c.gate.mark_may_have_sent
    def stop_after_commit(*args,**kwargs):
        original(*args,**kwargs)
        raise SystemExit('simulate process termination after committed MAY')
    monkeypatch.setattr(c.gate,'mark_may_have_sent',stop_after_commit)
    with c.owner.session(c.lease.shard):
        with pytest.raises(SystemExit):dispatcher(c).dispatch(c.write.command_id,c.lease)
        recovered=dispatcher(c).dispatch(c.write.command_id,c.lease)
        assert recovered.status=='RECOVERY_REQUIRED' and recovered.original.current_state=='MAY_HAVE_SENT'
    assert c.engine.query_creation(c.write.command_id) is None
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_dispatch_marks WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)


@pytest.mark.parametrize('fault',['missing_revocation_guard','foreign_lease'])
def test_dispatcher_missing_host_composition_never_crosses_may(database,venue,tmp_path,fault):
    c=case(database,venue,tmp_path)
    lease=replace(c.lease,instance_id='other-process') if fault=='foreign_lease' else c.lease
    with c.owner.session(c.lease.shard):
        with pytest.raises(ValueError):dispatcher(c,guard=fault!='missing_revocation_guard').dispatch(c.write.command_id,lease)
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_dispatch_marks WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)
