"""Real financial/owner/consume boundary; offline call shapes, no venue receipt."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

from integration.test_program_commands_postgres import isolated_postgres,database,attach,boundary,lease
from integration.test_program_financial_admission import real_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.storage.models import scope_id


def prepared(database,tmp_path,*,blocked=False,installed=True):
    c=real_case(database);c.write,c.request,c.bundle=c.service._transaction(lambda tx:attach(c,tx))
    c.gate=boundary(c);c.lease=lease(c)
    from trading_system.engine.nautilus.native_program_adapter import OfflineNativeCreationProbe,NativeCreationQueue
    from trading_system.engine.nautilus.bridge import NautilusEngine
    from trading_system.engine.nautilus.id_codec import VenueIdCodec
    from trading_system.engine.nautilus.native_events import NativeEventBridge
    from trading_system.runtime.execution_owner import ExecutionOwner
    c.codec=VenueIdCodec(c.scope);c.queue=NativeCreationQueue(initially_ready=not blocked)
    c.probe=OfflineNativeCreationProbe(c.scope,archive_bridge=NativeEventBridge(c.codec,tmp_path/'raw'),queue=c.queue)
    c.engine=NautilusEngine(t.CompatibilityMap(build_digest='a'*64,entries=(),send_path_inventory=(),
        uncovered_paths=('NATIVE_LAST_SEND_NOT_QUALIFIED',)),creation_probe=c.probe if installed else None)
    @contextmanager
    def claim(shard):
        import fcntl
        with (tmp_path/'owner.lock').open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)
    c.exec_owner=ExecutionOwner(c.lease,journal_path=tmp_path/'owner.ndjson',engine=c.engine,
        claim=claim,clock=lambda:c.c.facts.inputs.clock)
    return c


def submit(c,*,request=None,permit=None):
    with c.exec_owner.session(c.lease.shard):
        token=permit or c.gate.mark_may_have_sent(c.write.command_id,c.lease)
        c.permit=token
        return c.engine.submit_creation(c.write,c.request if request is None else request,token,
            boundary=c.gate,lease=c.lease,owner=c.exec_owner)


def test_real_owner_consumption_emits_only_archived_offline_unknown_observation(database,tmp_path):
    c=prepared(database,tmp_path)
    try:
        result=submit(c)
        assert result.status=='UNKNOWN' and not result.execution_authorized and not result.funds_release_authorized
        assert result.call.original_parameters==c.request.form_parameters() and result.raw_ref
        assert len(c.probe.observations)==1 and result.call.command_id==c.write.command_id
        assert 'QUERY_ORIGINAL_IDS' in result.retained_duties and not result.native_order_qualified
        with c.owner.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
    finally:c.codec.close()


@pytest.mark.parametrize('fault',['default_disabled','changed_request','no_active_owner','reused_permit'])
def test_no_caller_can_convert_unsigned_or_stale_creation_data_into_an_offline_write(database,tmp_path,fault):
    import psycopg
    c=prepared(database,tmp_path,installed=fault!='default_disabled')
    try:
        request=c.request
        if fault=='changed_request':request=replace(request,fields=tuple(replace(f,value='MARKET') if f.name=='type' else f for f in request.fields))
        if fault=='reused_permit':submit(c)
        with pytest.raises((ValueError,psycopg.Error)):
            if fault=='no_active_owner':
                token=c.gate.mark_may_have_sent(c.write.command_id,c.lease)
                c.engine.submit_creation(c.write,request,token,boundary=c.gate,lease=c.lease,owner=c.exec_owner)
            else:submit(c,request=request,permit=c.permit if fault=='reused_permit' else None)
        assert len(c.probe.observations)==(1 if fault=='reused_permit' else 0)
    finally:c.codec.close()


@pytest.mark.parametrize('fault',['expiry','fence','lock'])
def test_actual_queue_wait_requires_fresh_owner_and_protected_current_recheck(database,tmp_path,fault):
    c=prepared(database,tmp_path,blocked=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(submit,c)
            assert c.queue.entered.wait(20),'actual bounded native queue was not entered'
            # These real ledger writes completing while queued prove that no
            # financial business transaction is held across the wait.
            if fault=='fence':
                from trading_system.intents.ingress import revision_key,IngressRevision
                with c.owner.transaction(c.scope) as tx:
                    key=revision_key(c.source.context);old=tx._latest('audit_events',key,IngressRevision)
                    tx._append('audit_events',key,replace(old,sequence=old.sequence+1,enabled=False),old.sequence)
            if fault=='lock':
                with c.owner.transaction(c.scope) as tx:tx.risk_locks.append(t.RiskLock(lock_id='queued-native-lock',scope=c.scope,
                    cause='ORDER_OUTCOME_UNKNOWN',state='ACTIVE',permission=t.RiskPermission.RECOVERY_REQUIRED,
                    required_evidence=('ORIGINAL_IDS',),raised_at=c.c.facts.inputs.clock.utc_now),0)
            if fault=='expiry':
                before=c.c.facts.inputs.clock
                advanced=replace(before,utc_now=(utc_timestamp(before.utc_now)+timedelta(seconds=6)).isoformat(),monotonic_ns=before.monotonic_ns+6*10**9)
                c.gate.readers['load_clock']=lambda _:advanced;c.exec_owner.clock=lambda:advanced
            c.queue.release()
            with pytest.raises(ValueError):pending.result(timeout=20)
        assert not c.probe.observations
        with c.owner.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
    finally:c.codec.close()
