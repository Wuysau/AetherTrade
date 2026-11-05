"""Bounded private-stream connection and durable inbox. No event sends orders."""
import asyncio
import json
import inspect
import logging
import ssl
import time

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


class DemoUserStreamConsumer:
    def __init__(self, repository, scope, credential_ref, *, request_id):
        self.repository, self.scope, self.credential_ref = repository, scope, credential_ref
        self.request_id = request_id
        self.subscription_id = None
        self.healthy = False
        self.last_event_at = None

    def accept_subscription(self, response):
        result = response.get('result') if isinstance(response, dict) else None
        subscription_id = result.get('subscriptionId') if isinstance(result, dict) else None
        if response.get('id') != self.request_id or response.get('status') != 200 or type(subscription_id) is not int or subscription_id < 0:
            raise DemoError('DEMO_SUBSCRIPTION_REJECTED')
        self.subscription_id, self.healthy = subscription_id, True

    def ingest(self, message, *, received_at):
        if not self.healthy:
            raise DemoError('DEMO_SUBSCRIPTION_REQUIRED')
        if message.get('subscriptionId') != self.subscription_id:
            raise DemoError('DEMO_SUBSCRIPTION_MISMATCH')
        event = message.get('event')
        if not isinstance(event, dict):
            raise DemoError('DEMO_STREAM_EVENT_INVALID')
        kind = event.get('e')
        if kind != 'executionReport':
            # Balance/list events require reconciliation, never silently ignored
            # as complete coverage for unsupported products/capabilities.
            self.repository.pause(self.scope, 'DEMO_STREAM_RECONCILIATION_REQUIRED')
            return None
        try:
            owner_scope = self.repository.resolve_order_scope(self.scope, client_id=(event.get('C') if event.get('X') == 'CANCELED' and event.get('C') else event.get('c')), symbol=event.get('s'))
        except DemoError:
            owner_scope = self.scope  # Preserve unknown-account events for review.
            self.repository.pause(self.scope, 'DEMO_UNBOUND_ACCOUNT_ORDER')
        receipt = self.repository.observe(owner_scope, source='WS_EXECUTION_REPORT', payload=event,
            received_at=received_at, credential_ref=self.credential_ref)
        self.last_event_at = received_at
        return receipt

    def disconnected(self):
        self.healthy = False
        self.repository.pause(self.scope, 'DEMO_STREAM_DISCONNECTED')

    async def run_window(self, transport, *, duration_seconds, on_subscribed=None, socket_factory=private_connection):
        if type(duration_seconds) is not int or not 1 <= duration_seconds <= 3600:
            raise DemoError('DEMO_STREAM_WINDOW_INVALID')
        # Explicit bounded run only; no implicit background runtime or reconnect.
        subscribed = False
        try:
            await settled_thread(transport.sync_time)
            async with asyncio.timeout(duration_seconds):
                async with socket_factory() as socket:
                    await socket.send(json.dumps(transport.subscription(self.request_id)))
                    response = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                    self.accept_subscription(response)
                    subscribed = True
                    async with asyncio.TaskGroup() as tasks:
                        if on_subscribed:
                            async def backfill():
                                result = await settled_thread(on_subscribed, self)
                                if inspect.isawaitable(result):
                                    await result
                            tasks.create_task(backfill())
                        # Drain frames while the REST callback runs. Every accepted
                        # execution report is durably archived before next recv.
                        async for message in socket:
                            await settled_thread(self.ingest, json.loads(message), received_at=int(time.time()))
        except TimeoutError:
            if not subscribed:
                raise DemoError('DEMO_STREAM_SUBSCRIPTION_TIMEOUT') from None
        except Exception:
            raise DemoError('DEMO_STREAM_INTERRUPTED') from None
        finally:
            self.disconnected()
