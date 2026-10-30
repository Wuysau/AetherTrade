"""Closed owner-thread PAPER/RESEARCH loopback composition; never venue truth."""
import asyncio
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlencode

from trading_system.contracts import types as t
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.canonical import canonical_json, domain_payload
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.execution.program_commands import ProgramCommandBoundary, ProgramWritePermit
from trading_system.execution.program_consumed_boundary import recheck_consumed
from .native_creation import creation_call
from .native_program_adapter import (OfflineNativeCreationProbe, NativeCreationObservation,
                                     digest, require, SOURCE)
from .guarded_sdk_build import inspect_guarded_sdk


def submit_guarded_creation_probe(engine, write, request, permit, *, boundary, lease, owner):
    from .bridge import NautilusEngine
    from trading_system.runtime.execution_owner import ExecutionOwner
    require(type(engine) is NautilusEngine and type(owner) is ExecutionOwner and owner.engine is engine,
            'NATIVE_ACTUAL_OWNER_ENGINE_REQUIRED')
    require(type(boundary) is ProgramCommandBoundary and type(permit) is ProgramWritePermit
            and type(write) is ApplicationWriteV3 and type(request) is VenueCreationRequest,
            'NATIVE_ACTUAL_BOUNDARY_PERMIT_REQUIRED')
    require(request.scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH)
            and request.scope.product != t.Product.US_EQUITY, 'NATIVE_EXTERNAL_ENVIRONMENT_DISABLED')
    probe = engine._creation_probe
    require(type(probe) is OfflineNativeCreationProbe and probe.scope == request.scope,
            'NATIVE_SCOPED_ARCHIVE_REQUIRED')
    require(write.state in ('DURABLE_READY','MAY_HAVE_SENT')
            and write.scope == request.scope == permit.scope == boundary.scope
            and write.command_id == request.command_id == permit.command_id
            and write.request_hash == permit.request_hash == digest(request),
            'NATIVE_ORIGINAL_REQUEST_REQUIRED')
    # A synchronous owner API must not silently block asyncio cancellation.
    try: asyncio.get_running_loop()
    except RuntimeError: pass
    else: raise ValueError('GUARDED_SYNCHRONOUS_OWNER_CONTEXT_REQUIRED')
    inspect_guarded_sdk()  # Before consume or opening the recorder.
    from nautilus_trader.core.nautilus_pyo3 import HttpClient, HttpMethod, Quota, hmac_signature
    with owner.guard(lease):
        call = creation_call(request, clock=owner._reading())
    recorded = []
    class Recorder(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            if self.path != '/__warm': recorded.append(self.path)
            self.send_response(200)
            self.send_header('Content-Length','2')
            self.end_headers(); self.wfile.write(b'{}')
    server = ThreadingHTTPServer(('127.0.0.1',0),Recorder)
    thread = Thread(target=server.serve_forever,daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    client = HttpClient(keyed_quotas=[('creation',Quota.rate_per_second(1))])
    try:
        client.request_guarded(HttpMethod.POST,base+'/__warm',nullcontext(),keys=['creation'])
        with owner.guard(lease):
            consumed = boundary.consume(permit,lease)
            require(digest(consumed) == digest(request),'NATIVE_CONSUMED_REQUEST_CHANGED')
            clock = owner._reading()
            remaining = (permit.deadline_monotonic_ns-clock.monotonic_ns)//1_000_000
            require(remaining >= 1,'NATIVE_CONSUMED_WINDOW_EXPIRED')
            call = creation_call(request,clock=clock,recv_window_ms=min(5000,remaining))
        query = urlencode(call.parameters)
        signature = hmac_signature('OFFLINE-PUBLIC-SHAPE-FIXTURE-NOT-AN-ACCOUNT-SECRET',query)
        url = base+call.endpoint.value+'?'+query+'&signature='+signature
        @contextmanager
        def final_guard():
            with owner.guard(lease):
                current = recheck_consumed(boundary,permit,lease)
                require(digest(current.write) == digest(replace(write,state='MAY_HAVE_SENT'))
                        and digest(current.request) == digest(request),'NATIVE_CURRENT_ORIGINAL_CHANGED')
                require(current.clock.monotonic_ns < current.deadline_monotonic_ns,
                        'NATIVE_CONSUMED_WINDOW_EXPIRED')
                # recheck's transaction has closed. Same OS thread owns this
                # context until transport completes, including connection errors.
                yield
        try:
            response = client.request_guarded(HttpMethod.POST,url,final_guard(),
                headers={'X-MBX-APIKEY':'OFFLINE-PUBLIC-SHAPE-FIXTURE'},keys=['creation'],budget_ms=min(remaining,5000))
            outcome = {'http_status':response.status,'body_sha256':sha256(response.body).hexdigest()}
        except Exception:
            # No retry, fresh permit or client ID. The durable MAY and consume
            # survive all uncertainty; the original query path remains required.
            raise
        raw = canonical_json(dict(source=SOURCE,record_kind='GUARDED_SDK_LOOPBACK',
            call=domain_payload(call),outcome=outcome,recorded_requests=len(recorded),
            venue_evidence=False,external_activation_allowed=False)).encode()
        ref = probe._archive.ingest(raw,source=SOURCE,received_at=clock.utc_now,
                                  processed_at=owner._reading().utc_now).raw.raw_payload_ref
        return NativeCreationObservation(command_id=call.command_id,call=call,raw_ref=ref,actual_sdk_shape=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
