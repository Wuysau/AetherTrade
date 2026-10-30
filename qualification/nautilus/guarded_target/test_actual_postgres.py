"""Run only in the measured full-SDK runner, against fresh private PostgreSQL."""
from dataclasses import replace
from datetime import timedelta
from threading import Thread
from time import monotonic,sleep
from urllib.parse import urlsplit,parse_qsl
from hashlib import sha256
import hmac
import pytest
from integration.test_native_creation_owner_postgres import prepared
from integration.test_program_commands_postgres import isolated_postgres,database
from trading_system.contracts.canonical import utc_timestamp
from trading_system.storage.models import scope_id
from trading_system.engine.nautilus import guarded_creation as guarded
from trading_system.engine.nautilus.guarded_sdk_build import inspect_guarded_sdk

@pytest.mark.parametrize('fault',['positive','expiry','fence'])
def test_actual_sdk_queue_rechecks_original_private_pg_consumption(database,tmp_path,monkeypatch,fault):
    inspect_guarded_sdk()  # Missing full measured artifact is an ERROR, never skip.
    c=prepared(database,tmp_path)
    paths=[];changes=[];errors=[]
    actual_server=guarded.ThreadingHTTPServer
    class ObservedServer(actual_server):
        def __init__(self,address,handler):
            class ObservedHandler(handler):
                def do_POST(self):
                    if self.path != '/__warm':paths.append(self.path)
                    super().do_POST()
            super().__init__(address,ObservedHandler)
    # This observes the actual local HTTP recorder; Rust HTTP and quotas are intact.
    monkeypatch.setattr(guarded,'ThreadingHTTPServer',ObservedServer)
    def change_after_actual_consume():
        try:
            deadline=monotonic()+10
            while monotonic()<deadline:
                with c.owner.transaction(c.scope) as tx:
                    consumed=tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
                                        (scope_id(c.scope),)).fetchone()[0]
                if consumed:break
                sleep(.01)
            assert consumed==1
            if fault=='expiry':
                before=c.c.facts.inputs.clock
                advanced=replace(before,utc_now=(utc_timestamp(before.utc_now)+timedelta(seconds=6)).isoformat(),
                                 monotonic_ns=before.monotonic_ns+6*10**9)
                c.gate.readers['load_clock']=lambda _:advanced
                c.exec_owner.clock=lambda:advanced
            elif fault=='fence':
                from trading_system.intents.ingress import revision_key,IngressRevision
                with c.owner.transaction(c.scope) as tx:
                    key=revision_key(c.source.context);old=tx._latest('audit_events',key,IngressRevision)
                    tx._append('audit_events',key,replace(old,sequence=old.sequence+1,enabled=False),old.sequence)
            changes.append(fault)
        except BaseException as exc:errors.append(exc)
    worker=Thread(target=change_after_actual_consume)
    try:
        with c.exec_owner.session(c.lease.shard):
            permit=c.gate.mark_may_have_sent(c.write.command_id,c.lease)
            worker.start()
            if fault=='positive':
                result=guarded.submit_guarded_creation_probe(c.engine,c.write,c.request,permit,
                    boundary=c.gate,lease=c.lease,owner=c.exec_owner)
                assert result.status=='UNKNOWN' and not result.execution_authorized
                assert result.call.original_parameters==c.request.form_parameters()
            else:
                with pytest.raises(ValueError):
                    guarded.submit_guarded_creation_probe(c.engine,c.write,c.request,permit,
                        boundary=c.gate,lease=c.lease,owner=c.exec_owner)
        worker.join(timeout=12)
        assert not worker.is_alive() and not errors and changes==[fault]
        assert len(paths)==int(fault=='positive')
        if fault=='positive':
            wire=urlsplit(paths[0])
            assert wire.path==result.call.endpoint.value
            assert parse_qsl(wire.query)[:-1]==list(result.call.parameters)
            signed,signature=wire.query.rsplit('&signature=',1)
            assert signature==hmac.new(b'OFFLINE-PUBLIC-SHAPE-FIXTURE-NOT-AN-ACCOUNT-SECRET',
                                       signed.encode(),sha256).hexdigest()
        with c.owner.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
                              (scope_id(c.scope),)).fetchone()==(1,)
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',
                              (scope_id(c.scope),)).fetchone()==(0,)
    finally:
        if worker.ident is not None:worker.join(timeout=12)
        c.codec.close()
