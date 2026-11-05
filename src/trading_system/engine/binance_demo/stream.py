"""Bounded private-stream connection and durable inbox. No event sends orders."""
import asyncio
import json
import inspect
import logging
import ssl
import time
from threading import Lock
from uuid import uuid4

from websockets.asyncio.client import connect
from .contracts import DemoEndpointRegistry, DemoError
from .async_safety import settled_thread


class _NoRedirectConnection(connect):
    def process_redirect(self, exc):
        return exc  # Never follow redirects with API-key authentication material.


def private_connection():
    logger = logging.Logger('aethertrade.demo.private_socket')
    logger.disabled = True  # WebSocket DEBUG logs otherwise reveal signed frames.
    return _NoRedirectConnection(DemoEndpointRegistry.SPOT_WS_API, proxy=None,
        ssl=ssl.create_default_context(), open_timeout=10, close_timeout=3,
        max_size=256 * 1024, max_queue=32, logger=logger)


_STREAM_PROOF_ISSUER = object()


class _ClosedStreamProof:
    """Internal one-shot proof minted only after receiver and socket exit."""
    def __init__(self, owner, scope, generation, credential_revision, subscription_id, *, stream=None, run_nonce=None, _issuer=None):
        if _issuer is not _STREAM_PROOF_ISSUER or stream is None or not isinstance(run_nonce, str) or not run_nonce:
            raise DemoError('DEMO_STREAM_CLOSE_PROOF_REQUIRED')
        self.stream, self.run_nonce = stream, run_nonce
        self.owner, self.scope, self.generation = owner, scope, generation
        self.credential_revision, self.subscription_id = credential_revision, subscription_id
        self._used = False

    def consume(self, owner, scope, generation, credential_revision, subscription_id):
        if (self._used or self.owner is not owner or self.scope.account_key != scope.account_key or
                self.generation != generation or self.credential_revision != credential_revision or
                self.subscription_id != subscription_id):
            raise DemoError('DEMO_STREAM_CLOSE_PROOF_REQUIRED')
        self.stream._seal_closed_proof(self, self.run_nonce)
        self._used = True


class DemoUserStreamConsumer:
    supports_graceful_stop = True
    def __init__(self, repository, scope, credential_ref, *, request_id, physical_owner=None):
        self.repository, self.scope, self.credential_ref = repository, scope, credential_ref
        self._run_lock = Lock()
        self._active_run_nonce = None
        self._last_closed_nonce = None
        self._sealed = False
        if repository.engine.dialect.name == 'postgresql' and physical_owner is None:
            raise DemoError('DEMO_PHYSICAL_OWNER_REQUIRED')
        if physical_owner is not None: physical_owner.validate(scope)
        self._physical_owner = physical_owner
        self._physical_generation = physical_owner.generation if physical_owner is not None else None
        account = repository.account(scope)
        if account['credential_ref'] != credential_ref or type(account['credential_revision']) is not int:
            raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
        self._credential_revision = account['credential_revision']
        if physical_owner is not None and hasattr(physical_owner, 'register_stream'):
            physical_owner.register_stream(self, scope)
        self.request_id = request_id
        self.subscription_id = None
        self.healthy = False
        self.last_event_at = None
        self._closed_proof = None

    @property
    def credential_revision(self):
        return self._credential_revision

    @property
    def physical_generation(self):
        return self._physical_generation

    @property
    def closed_proof(self):
        with self._run_lock: return self._closed_proof

    @property
    def active_run_count(self):
        with self._run_lock: return int(self._active_run_nonce is not None)

    @property
    def active_run_nonce(self):
        with self._run_lock: return self._active_run_nonce

    def _begin_run(self):
        with self._run_lock:
            if self._sealed: raise DemoError('DEMO_STREAM_SEALED')
            if self._active_run_nonce is not None: raise DemoError('DEMO_STREAM_WINDOW_ACTIVE')
            nonce = uuid4().hex
            self._active_run_nonce, self._last_closed_nonce, self._closed_proof = nonce, None, None
            return nonce

    def _seal_closed_proof(self, proof, nonce):
        with self._run_lock:
            if (self._sealed or self._active_run_nonce is not None or self._last_closed_nonce != nonce or
                    self._closed_proof is not proof):
                raise DemoError('DEMO_STREAM_CLOSE_PROOF_REQUIRED')
            self._sealed = True

    def validate_owner(self):
        if self._physical_owner is not None:
            self._physical_owner.validate(self.scope)

    def accept_subscription(self, response):
        self.validate_owner()
        result = response.get('result') if isinstance(response, dict) else None
        subscription_id = result.get('subscriptionId') if isinstance(result, dict) else None
        if response.get('id') != self.request_id or response.get('status') != 200 or type(subscription_id) is not int or subscription_id < 0:
            raise DemoError('DEMO_SUBSCRIPTION_REJECTED')
        self.subscription_id, self.healthy = subscription_id, True

    def ingest(self, message, *, received_at):
        self.validate_owner()
        if not self.healthy:
            raise DemoError('DEMO_SUBSCRIPTION_REQUIRED')
        if message.get('subscriptionId') != self.subscription_id:
            raise DemoError('DEMO_SUBSCRIPTION_MISMATCH')
        event = message.get('event')
        if not isinstance(event, dict):
            raise DemoError('DEMO_STREAM_EVENT_INVALID')
        kind = event.get('e')
        if kind == 'outboundAccountPosition':
            from .account_events import parse_account_position
            try:
                parse_account_position(event)
            except DemoError:
                pass  # Persist malformed frames as unsupported conflicts below.
            else:
                receipt = self.repository.observe(self.scope, source='WS_ACCOUNT_POSITION', payload=event,
                    received_at=received_at, credential_ref=self.credential_ref, credential_revision=self.credential_revision,
                    stream_generation=self.physical_generation)
                self.validate_owner()
                self.last_event_at = received_at
                return receipt
        if kind != 'executionReport':
            # Preserve every unsupported frame as a durable conflict. A pause
            # alone is not an activity marker and could be overwritten later.
            receipt = self.repository.observe(self.scope, source='WS_UNSUPPORTED_EVENT', payload=event,
                received_at=received_at, credential_ref=self.credential_ref, credential_revision=self.credential_revision,
                stream_generation=self.physical_generation)
            if kind == 'eventStreamTerminated':
                self.healthy = False
            self.validate_owner()
            self.repository.pause(self.scope, 'DEMO_STREAM_RECONCILIATION_REQUIRED')
            return receipt
        try:
            owner_scope = self.repository.resolve_order_scope(self.scope, client_id=(event.get('C') if event.get('X') == 'CANCELED' and event.get('C') else event.get('c')), symbol=event.get('s'))
        except DemoError:
            owner_scope = self.scope  # Preserve unknown-account events for review.
            self.repository.pause(self.scope, 'DEMO_UNBOUND_ACCOUNT_ORDER')
        self.validate_owner()
        receipt = self.repository.observe(owner_scope, source='WS_EXECUTION_REPORT', payload=event,
            received_at=received_at, credential_ref=self.credential_ref, credential_revision=self.credential_revision, stream_generation=self.physical_generation)
        self.validate_owner()
        self.last_event_at = received_at
        return receipt

    def disconnected(self):
        self.healthy = False
        self.repository.pause(self.scope, 'DEMO_STREAM_DISCONNECTED')

    async def run_window(self, transport, *, duration_seconds, on_subscribed=None, socket_factory=private_connection):
        if type(duration_seconds) is not int or not 1 <= duration_seconds <= 3600:
            raise DemoError('DEMO_STREAM_WINDOW_INVALID')
        # Explicit bounded run only; no implicit background runtime or reconnect.
        run_nonce = self._begin_run()
        subscribed = False
        completed = False
        local_close_requested = False
        deadline = asyncio.get_running_loop().time() + duration_seconds
        try:
            await settled_thread(self.validate_owner)
            await settled_thread(transport.sync_time)
            await settled_thread(self.validate_owner)
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError
            async with asyncio.timeout_at(deadline):
                async with socket_factory() as socket:
                    await settled_thread(self.validate_owner)
                    subscription = await settled_thread(transport.subscription, self.request_id)
                    await settled_thread(self.validate_owner)
                    await socket.send(json.dumps(subscription))
                    await settled_thread(self.validate_owner)
                    response = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                    await settled_thread(self.accept_subscription, response)
                    subscribed = True
                    async with asyncio.TaskGroup() as tasks:
                        if on_subscribed:
                            async def backfill():
                                nonlocal local_close_requested
                                result = await settled_thread(on_subscribed, self)
                                if inspect.isawaitable(result):
                                    await result
                                # A remotely closed socket is not an orderly
                                # local shutdown, even if the iterator ended cleanly.
                                from websockets.protocol import State
                                state = getattr(socket, 'state', None)
                                if not self.healthy or (state is not None and state != State.OPEN):
                                    raise DemoError('DEMO_STREAM_REMOTE_CLOSED')
                                close = getattr(socket, 'close', None)
                                if close is not None:
                                    local_close_requested = True
                                    result = close()
                                    if inspect.isawaitable(result): await result
                            tasks.create_task(backfill())
                        # Drain frames while the REST callback runs. Every accepted
                        # execution report is durably archived before next recv.
                        async for message in socket:
                            await settled_thread(self.ingest, json.loads(message), received_at=int(time.time()))
                        self.healthy = False
                        if not local_close_requested and hasattr(socket, 'close'):
                            raise DemoError('DEMO_STREAM_REMOTE_CLOSED')
            completed = True
        except TimeoutError:
            if not subscribed:
                raise DemoError('DEMO_STREAM_SUBSCRIPTION_TIMEOUT') from None
        except Exception:
            raise DemoError('DEMO_STREAM_INTERRUPTED') from None
        finally:
            disconnected = False
            try:
                await settled_thread(self.disconnected)
                disconnected = True
            finally:
                with self._run_lock:
                    if self._active_run_nonce == run_nonce:
                        self._active_run_nonce = None
                        if completed and subscribed and local_close_requested and disconnected and self._physical_owner is not None:
                            self._last_closed_nonce = run_nonce
                            self._closed_proof = _ClosedStreamProof(self._physical_owner, self.scope, self.physical_generation,
                                self.credential_revision, self.subscription_id, stream=self, run_nonce=run_nonce,
                                _issuer=_STREAM_PROOF_ISSUER)
