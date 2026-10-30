"""Owner-controlled creation extension with concrete offline probes only.

An installed probe is never an external activation switch. Actual account
clients, arbitrary callbacks, URLs and secrets are not accepted. The pinned SDK
probe signs a public fixture using a fixed in-process fake HTTP implementation.
"""
import asyncio
from dataclasses import dataclass,replace
from hashlib import sha256
from threading import Event
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit,parse_qsl

from trading_system.contracts import types as t
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.execution.program_commands import ProgramCommandBoundary,ProgramWritePermit
from trading_system.execution.program_consumed_boundary import recheck_consumed
from .native_creation import NativeCreationCall,creation_call
from .native_events import NativeEventBridge

SOURCE='OFFLINE_NATIVE_CREATION_PROBE_NOT_VENUE_EVIDENCE'


def require(condition,code):
    if not condition:raise ValueError(code)


def digest(record):return canonical_hash(domain_payload(record))


class NativeCreationQueue:
    """Bounded host-owned probe queue, not a venue quota qualification."""
    def __init__(self,*,initially_ready=True):
        require(type(initially_ready) is bool,'NATIVE_EXPLICIT_QUEUE_STATE_REQUIRED')
        self.entered=Event();self._ready=Event()
        if initially_ready:self._ready.set()

    def release(self):self._ready.set()

    def wait(self,maximum_seconds):
        self.entered.set()
        require(self._ready.wait(timeout=max(0,min(5,maximum_seconds))),'NATIVE_QUEUE_WINDOW_EXPIRED')


@dataclass(frozen=True,slots=True,kw_only=True)
class NativeCreationObservation:
    command_id:str
    call:NativeCreationCall
    raw_ref:t.EvidenceRef
    actual_sdk_shape:bool
    status:Literal['UNKNOWN']='UNKNOWN'
    retained_duties:tuple[str,...]=('QUERY_ORIGINAL_IDS','RESERVATION_RECONCILIATION_REQUIRED',
        'ACCOUNT_HISTORY_NOT_VERIFIED','NATIVE_LAST_SEND_NOT_QUALIFIED','OFFLINE_SOURCE_NOT_VENUE_EVIDENCE')

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def native_order_qualified(self)->Literal[False]:return False


class OfflineNativeCreationProbe:
    def __init__(self,scope,*,archive_bridge,queue=None):
        require(type(scope) is t.ScopeKey and scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),
            'NATIVE_EXTERNAL_ENVIRONMENT_DISABLED')
        require(type(archive_bridge) is NativeEventBridge and archive_bridge.codec.scope==scope,'NATIVE_SCOPED_ARCHIVE_REQUIRED')
        require(queue is None or type(queue) is NativeCreationQueue,'NATIVE_CONTROLLED_QUEUE_REQUIRED')
        self.scope,self._archive,self.queue=scope,archive_bridge,queue or NativeCreationQueue()
        self._observations=[]

    @property
    def observations(self):return tuple(self._observations)

    def _preflight(self):pass

    def _raw_shape(self,call):
        return dict(source=SOURCE,record_kind='OFFLINE_NATIVE_CALL_SHAPE',call=domain_payload(call),
            actual_sdk_shape=False,external_endpoint_called=False)

    def _observe(self,call,clock):
        body=self._raw_shape(call)
        raw=canonical_json(body).encode('utf-8')
        ref=self._archive.ingest(raw,source=SOURCE,received_at=clock.utc_now,
            processed_at=clock.utc_now).raw.raw_payload_ref
        result=NativeCreationObservation(command_id=call.command_id,call=call,raw_ref=ref,
            actual_sdk_shape=body['actual_sdk_shape'])
        self._observations.append(result)
        return result


class PinnedSdkOfflineCreationProbe(OfflineNativeCreationProbe):
    def _preflight(self):
        from .sdk_pins import inspect_installed_sdk
        inspect_installed_sdk()

    def _raw_shape(self,call):
        # The signer is lexical to this owner-held call and cannot be supplied
        # by a server request/provider. No actual key or external client is read.
        from .sdk_pins import inspect_installed_sdk
        inspect_installed_sdk()
        from nautilus_trader.adapters.binance.http.client import BinanceHttpClient
        from nautilus_trader.common.component import LiveClock
        from nautilus_trader.core.nautilus_pyo3 import HttpMethod
        class InProcessHttp:
            async def request(self,method,*,url,headers,body,keys):
                require(method==HttpMethod.POST and url.startswith('https://offline.invalid/') and body is None,
                    'NATIVE_OFFLINE_TRANSPORT_ONLY')
                actual=urlsplit(url)
                require(actual.path==call.endpoint.value,'NATIVE_STATIC_SERVICE_ROUTE_CHANGED')
                parameters=parse_qsl(actual.query)
                require(parameters[:-1]==list(call.parameters) and parameters[-1][0]=='signature','NATIVE_SDK_FIELDS_CHANGED')
                self.signed_query_hash=sha256(actual.query.encode()).hexdigest()
                return SimpleNamespace(status=200,body=b'{"offline_native_shape":true}',headers={})
        async def invoke():
            client=BinanceHttpClient(clock=LiveClock(),api_key='OFFLINE-PUBLIC-SHAPE-FIXTURE',
                api_secret='OFFLINE-PUBLIC-SHAPE-FIXTURE-NOT-AN-ACCOUNT-SECRET',base_url='https://offline.invalid')
            wire=InProcessHttp();client._client=wire
            raw=await BinanceHttpClient.sign_request(client,HttpMethod.POST,call.endpoint.value,
                payload=dict(call.parameters),ratelimiter_keys=['binance:'+call.endpoint.value,'binance:global'])
            require(raw==b'{"offline_native_shape":true}','NATIVE_OFFLINE_SDK_RESPONSE_CHANGED')
            return wire.signed_query_hash
        query_hash=asyncio.run(invoke())
        return dict(source=SOURCE,record_kind='OFFLINE_NATIVE_CALL_SHAPE',call=domain_payload(call),
            actual_sdk_shape=True,external_endpoint_called=False,actual_account_credentials_used=False,
            signed_query_hash=query_hash)


def submit_creation_probe(engine,write,request,permit,*,boundary,lease,owner,probe):
    from .bridge import NautilusEngine
    from trading_system.runtime.execution_owner import ExecutionOwner
    require(type(engine) is NautilusEngine and type(owner) is ExecutionOwner and owner.engine is engine,
        'NATIVE_ACTUAL_OWNER_ENGINE_REQUIRED')
    require(type(probe) in (OfflineNativeCreationProbe,PinnedSdkOfflineCreationProbe)
        and type(boundary) is ProgramCommandBoundary and type(permit) is ProgramWritePermit,
        'NATIVE_ACTUAL_BOUNDARY_PERMIT_REQUIRED')
    require(type(write) is ApplicationWriteV3 and type(request) is VenueCreationRequest
        and write.state in ('DURABLE_READY','MAY_HAVE_SENT') and write.scope==request.scope==permit.scope==boundary.scope==probe.scope
        and write.command_id==request.command_id==permit.command_id and write.request_hash==permit.request_hash==digest(request),
        'NATIVE_ORIGINAL_REQUEST_REQUIRED')
    with owner.guard(lease):
        probe._preflight()
        # Validate complete static shape before consuming. It remains unsigned
        # and grants no authority; consume independently reloads the real request.
        creation_call(request,clock=owner._reading())
        consumed=boundary.consume(permit,lease)
        require(digest(consumed)==digest(request),'NATIVE_CONSUMED_REQUEST_CHANGED')
        clock=owner._reading()
    # No business transaction survives consume. The controlled probe queue can
    # wait; every source/current check runs again afterwards under the owner.
    probe.queue.wait((permit.deadline_monotonic_ns-clock.monotonic_ns)/1_000_000_000)
    with owner.guard(lease):
        probe._preflight()
        current=recheck_consumed(boundary,permit,lease)
        require(digest(current.write)==digest(replace(write,state='MAY_HAVE_SENT'))
            and digest(current.request)==digest(request),'NATIVE_CURRENT_ORIGINAL_CHANGED')
        remaining_ms=(current.deadline_monotonic_ns-current.clock.monotonic_ns)//1_000_000
        require(remaining_ms>=1,'NATIVE_CONSUMED_WINDOW_EXPIRED')
        call=creation_call(current.request,clock=current.clock,recv_window_ms=min(1000,remaining_ms))
        result=probe._observe(call,current.clock)
        owner._reading()
    return result
