"""Explicit, isolated Demo composition. Construction does not start any work.

The deployment supplies an already migrated repository, trusted epoch resolver
and build SHA. There is deliberately no HTTP enable/qualification-write route.
"""
import asyncio
from dataclasses import dataclass
import time
from uuid import uuid4

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope
from trading_system.engine.binance_demo.stream import DemoUserStreamConsumer, private_connection
from trading_system.execution.demo_commands import DemoExecutionOwner
from trading_system.execution.demo_reconcile import DemoReconciler
from .demo_frame import DemoFrameLoader
from .demo_memory import DemoMemoryProjector
from .demo_strategy import DemoStrategyHost


@dataclass(repr=False)
class _Runtime:
    lease: object
    transport: object
    host: DemoStrategyHost
    stream: DemoUserStreamConsumer
    reconciler: DemoReconciler
    running: bool = False
    revoked: bool = False
    last_error: str | None = None


class DemoRuntimeService:
    def __init__(self, *, connections, model_service, repository, scope_resolver, current_code_sha, rule_assessor=None, hybrid_gate=None):
        self.connections, self.models, self.repository = connections, model_service, repository
        self.scope_resolver, self.current_code_sha = scope_resolver, current_code_sha
        self.rule_assessor, self.hybrid_gate = rule_assessor, hybrid_gate
        self._sessions = {}

    def bind_session(self, session_key):
        if session_key in self._sessions:
            self._sessions[session_key].lease.validate()
            return self.status(session_key)
        lease = self.connections.connection_lease(session_key)
        transport = lease.transport()
        try:
            transport.sync_time(); account = transport.account(); lease.validate()
            # Scope comes from a deployment-owned account/epoch mapping, not a
            # browser field or key fingerprint. The signed UID must match it.
            scope = self.scope_resolver(account)
            if type(scope) is not DemoScope or scope.product != 'SPOT' or type(account.get('uid')) is not int or str(account['uid']) != scope.account_id:
                raise DemoError('DEMO_VERIFIED_ACCOUNT_IDENTITY_REQUIRED')
            fingerprint = self.connections.provider.describe(lease.credential_ref)['fingerprint']
            self.repository.register_account(scope, credential_ref=lease.credential_ref, fingerprint=fingerprint, budget='10000')
            stream = DemoUserStreamConsumer(self.repository, scope, lease.credential_ref, request_id='demo-' + uuid4().hex)
            host = DemoStrategyHost(self.repository, scope, frame_loader=DemoFrameLoader(self.repository, scope, transport, current_code_sha=self.current_code_sha),
                model_service=self.models, execution_owner=DemoExecutionOwner(self.repository, transport, preflight=lease.validate), stream=stream,
                rule_assessor=self.rule_assessor, hybrid_gate=self.hybrid_gate, current_code_sha=self.current_code_sha, memory_service=DemoMemoryProjector(self.repository))
            runtime = _Runtime(lease, transport, host, stream, DemoReconciler(self.repository, transport))
            lease.validate(); self._sessions[session_key] = runtime
            return self.status(session_key)
        except BaseException:
            transport.close()
            raise

    def _runtime(self, session_key):
        runtime = self._sessions.get(session_key)
        if runtime is None or runtime.revoked:
            raise DemoError('DEMO_RUNTIME_NOT_BOUND')
        try: runtime.lease.validate()
        except DemoError:
            self.close_session(session_key)
            raise
        return runtime

    def status(self, session_key):
        if session_key not in self._sessions:
            return {'configured': False, 'execution_authorized': False, 'blocker': 'DEMO_RUNTIME_NOT_BOUND'}
        try:
            runtime = self._runtime(session_key)
            return {**runtime.host.status(), 'running': runtime.running, 'last_error': runtime.last_error, 'private_stream_healthy': runtime.stream.healthy,
                    'postgresql_owner_available': self.repository.engine.dialect.name == 'postgresql'}
        except DemoError as exc:
            return {'configured': False, 'execution_authorized': False, 'blocker': exc.code}

    async def assess_once(self, session_key, run_id):
        runtime = self._runtime(session_key)
        result = await runtime.host.assess_once(run_id)
        runtime.lease.validate()
        return result

    async def run_window(self, session_key, *, duration_seconds, execute=False, decision_interval_seconds=60, socket_factory=private_connection):
        runtime = self._runtime(session_key)
        if runtime.running or type(duration_seconds) is not int or not 1 <= duration_seconds <= 900 or type(decision_interval_seconds) is not int or decision_interval_seconds < 30:
            raise DemoError('DEMO_RUNTIME_WINDOW_INVALID')
        if time.time() + duration_seconds >= runtime.lease.expires_at:
            raise DemoError('DEMO_WINDOW_EXCEEDS_CREDENTIAL_TTL')
        if execute:
            strategy = self.repository.strategy(runtime.host.scope)
            if strategy.get('allow_execution') is not True or strategy['authorized_until'] < time.time() + duration_seconds:
                raise DemoError('DEMO_EXECUTION_PERMISSION_REQUIRED')
            if self.repository.engine.dialect.name != 'postgresql':
                raise DemoError('DEMO_POSTGRESQL_OWNER_REQUIRED')
        runtime.running = True
        deadline = time.monotonic() + duration_seconds
        window_id = uuid4().hex
        async def supervise(stream):
            await asyncio.to_thread(runtime.reconciler.reconcile, runtime.host.scope, symbol='BTCUSDT', stream=stream)
            new_risk_paused = False
            async def pause_new_risk(code):
                nonlocal new_risk_paused
                runtime.last_error = code; new_risk_paused = True
                try: await asyncio.to_thread(self.repository.pause, runtime.host.scope, code)
                except DemoError as exc:
                    if exc.code != 'DEMO_OWNER_BUSY': raise
            async def protect():
                last_recovery = 0
                while time.monotonic() < deadline:
                    self._runtime(session_key)
                    try:
                        await asyncio.to_thread(runtime.host.drain_inbox)
                        if runtime.host.memory is not None:
                            await asyncio.to_thread(runtime.host.memory.project, runtime.host.scope)
                        if execute:
                            await asyncio.to_thread(self.repository.abandon_stale_unsent, runtime.host.scope, now=int(time.time()))
                            health = await asyncio.to_thread(self.repository.health, runtime.host.scope)
                            if health['unknown'] and time.monotonic() - last_recovery >= 5:
                                await asyncio.to_thread(runtime.reconciler.reconcile, runtime.host.scope, symbol='BTCUSDT', stream=stream)
                                last_recovery = time.monotonic()
                            protection = await asyncio.to_thread(runtime.host.protection_tick)
                            for command in protection['commands']:
                                self._runtime(session_key)
                                await asyncio.to_thread(runtime.host.dispatch, command['command_id'])
                    except DemoError as exc:
                        # Never retry a write: only reobserve and reconstruct
                        # still-unsent work on the next tick. Coverage expires
                        # while evidence is unavailable, blocking all new risk.
                        await pause_new_risk(exc.code)
                    except Exception:
                        await pause_new_risk('DEMO_PROTECTION_SUPERVISION_ERROR')
                    await asyncio.sleep(1)
            async def decide():
                sequence = 0
                while time.monotonic() < deadline:
                    self._runtime(session_key)
                    if new_risk_paused:
                        return  # Existing protection and ingestion keep running.
                    try:
                        if await asyncio.to_thread(self.repository.active_orders, runtime.host.scope):
                            await asyncio.sleep(1)
                            continue
                        await asyncio.to_thread(runtime.reconciler.reconcile_and_resume, runtime.host.scope, stream=stream, current_code_sha=self.current_code_sha)
                        sequence += 1
                        run_id = f'{window_id}:{sequence}'
                        await runtime.host.assess_once(run_id)
                        if new_risk_paused: return
                        command = await asyncio.to_thread(runtime.host.admit_assessment, run_id)
                        if command.get('command_id'):
                            self._runtime(session_key)
                            await asyncio.to_thread(runtime.host.dispatch, command['command_id'])
                    except DemoError as exc:
                        await pause_new_risk(exc.code)
                        return
                    except Exception:
                        await pause_new_risk('DEMO_DECISION_SUPERVISION_ERROR')
                        return
                    await asyncio.sleep(decision_interval_seconds)
            # Model latency cannot starve private-frame ingestion or stop
            # supervision. Decision failure pauses only new-risk work.
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(protect())
                if execute: tasks.create_task(decide())
        try:
            await runtime.stream.run_window(runtime.transport, duration_seconds=duration_seconds, on_subscribed=supervise, socket_factory=socket_factory)
        finally:
            runtime.running = False
            self.repository.pause(runtime.host.scope, 'DEMO_RUNTIME_WINDOW_ENDED')
        return self.status(session_key)

    def close_session(self, session_key):
        runtime = self._sessions.pop(session_key, None)
        if runtime:
            runtime.revoked = True; runtime.stream.healthy = False
            try:
                self.repository.pause(runtime.host.scope, 'DEMO_SESSION_REVOKED')
            finally:
                runtime.transport.close()

    def close(self):
        failed = False
        for session in list(self._sessions):
            try: self.close_session(session)
            except Exception: failed = True
        if failed:
            raise DemoError('DEMO_RUNTIME_SHUTDOWN_RECONCILIATION_REQUIRED')
