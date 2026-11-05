"""Explicit, isolated Demo composition. Construction does not start any work.

The deployment supplies an already migrated repository, trusted epoch resolver
and build SHA. There is deliberately no HTTP enable/qualification-write route.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import time
from threading import RLock
from uuid import uuid4

from trading_system.engine.binance_demo.contracts import DemoError, DemoScope, digest
from trading_system.engine.binance_demo.async_safety import settled_thread, settle_before_cancel
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
    drain_failed: bool = False
    stop_requested: bool = False
    window_task: object | None = None
    strategy_revision: int = 0
    new_risk_paused: bool = False
    send_guard: object | None = None


class DemoRuntimeService:
    def __init__(self, *, connections, model_service, repository, scope_resolver, current_code_sha, rule_assessor=None, hybrid_gate=None, close_repository=None):
        self.connections, self.models, self.repository = connections, model_service, repository
        self.scope_resolver, self.current_code_sha = scope_resolver, current_code_sha
        self.rule_assessor, self.hybrid_gate = rule_assessor, hybrid_gate
        self._close_repository = close_repository
        self._sessions = {}
        self._jobs = {}
        self._closing = False
        self._lifecycle_lock = RLock()

    def bind_session(self, session_key):
        with self._lifecycle_lock:
            return self._bind_session(session_key)

    def _bind_session(self, session_key):
        if session_key in self._sessions:
            previous = self._sessions[session_key]
            if previous.drain_failed:
                raise DemoError('DEMO_DRAIN_PAUSE_UNCONFIRMED')
            try:
                if previous.revoked:
                    raise DemoError('DEMO_SESSION_LEASE_REVOKED')
                previous.lease.validate()
            except DemoError:
                # A revoked session must finish its stream window before a new
                # credential may take ownership. Never overlap old and new.
                self._close_session(session_key)
                if self._sessions.get(session_key) is previous:
                    # A running window owns the transport until all workers
                    # settle and its durable final pause has succeeded.
                    raise DemoError('DEMO_RUNTIME_DRAIN_REQUIRED') from None
            else:
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
            if any(other.drain_failed and other.host.scope.account_key == scope.account_key for other in self._sessions.values()):
                raise DemoError('DEMO_DRAIN_PAUSE_UNCONFIRMED')
            if any(other.running and other.host.scope.account_key == scope.account_key for other in self._sessions.values()):
                raise DemoError('DEMO_ACCOUNT_RUNTIME_DRAIN_REQUIRED')
            with lease.registration_guard():
                fingerprint = self.connections.provider.describe(lease.credential_ref)['fingerprint']
                try:
                    existing = self.repository.account(scope)
                except DemoError as exc:
                    if exc.code != 'DEMO_ACCOUNT_UNREGISTERED':
                        raise
                    self.repository.register_account(scope, credential_ref=lease.credential_ref, fingerprint=fingerprint, budget='10000')
                else:
                    if existing['credential_ref'] != lease.credential_ref or existing['fingerprint'] != fingerprint:
                        # Process already durable observations under their original
                        # revision before atomically changing that revision.
                        from trading_system.execution.demo_reports import DemoReportVerifier
                        verifier = DemoReportVerifier(self.repository)
                        for observation in self.repository.pending_observations(scope, limit=1000):
                            if not observation.get('scope'):
                                raise DemoError('DEMO_OBSERVATION_SCOPE_MISSING')
                            verifier.process(DemoScope(**observation['scope']), observation['observation_id'])
                        lease.validate()
                        existing = self.repository.account(scope)
                        self.repository.rotate_credential(scope, credential_ref=lease.credential_ref, fingerprint=fingerprint, expected_revision=existing['revision'])
                stream = DemoUserStreamConsumer(self.repository, scope, lease.credential_ref, request_id='demo-' + uuid4().hex)
                host = DemoStrategyHost(self.repository, scope, frame_loader=DemoFrameLoader(self.repository, scope, transport, current_code_sha=self.current_code_sha),
                    model_service=self.models, execution_owner=DemoExecutionOwner(self.repository, transport, preflight=lease.validate), stream=stream,
                    rule_assessor=self.rule_assessor, hybrid_gate=self.hybrid_gate, current_code_sha=self.current_code_sha, memory_service=DemoMemoryProjector(self.repository))
                runtime = _Runtime(lease, transport, host, stream, DemoReconciler(self.repository, transport))
                host.owner.preflight = lambda: self._sender_preflight(runtime)
                try:
                    runtime.strategy_revision = self.repository.strategy(scope)['revision']
                except DemoError as exc:
                    if exc.code != 'DEMO_STRATEGY_UNREGISTERED': raise
                lease.validate(); self._sessions[session_key] = runtime
                return self.status(session_key)
        except BaseException:
            transport.close()
            raise

    @staticmethod
    def _sender_preflight(runtime):
        # The owner calls this immediately before the durable MAY and send.
        # Never permit an owner to send outside an active execution window.
        guard = runtime.send_guard
        if not runtime.running or guard is None:
            raise DemoError('DEMO_EXECUTION_PERMISSION_REQUIRED')
        guard()

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
        rebind = 'BLOCKED_UNQUALIFIED_FENCING' if self.repository.engine.dialect.name == 'postgresql' else 'OFFLINE_ONLY'
        if session_key not in self._sessions:
            return {'configured': False, 'execution_authorized': False, 'blocker': 'DEMO_RUNTIME_NOT_BOUND', 'credential_rebind': rebind}
        try:
            if self._sessions[session_key].drain_failed:
                return {'configured': False, 'execution_authorized': False, 'blocker': 'DEMO_DRAIN_PAUSE_UNCONFIRMED', 'credential_rebind': rebind}
            runtime = self._runtime(session_key)
            job = self._jobs.get(session_key)
            window = {'state': job['state'], 'error': job['error']} if job else {'state': 'NOT_STARTED', 'error': None}
            return {**runtime.host.status(), 'credential_rebind': rebind, 'running': runtime.running, 'stop_requested': runtime.stop_requested, 'strategy_revision': runtime.strategy_revision, 'window': window, 'new_risk_paused': runtime.new_risk_paused, 'last_error': runtime.last_error, 'private_stream_healthy': runtime.stream.healthy,
                    'postgresql_owner_available': self.repository.engine.dialect.name == 'postgresql'}
        except DemoError as exc:
            return {'configured': False, 'execution_authorized': False, 'blocker': exc.code, 'credential_rebind': rebind}

    @staticmethod
    def _same_physical_account(left, right):
        return (left.environment, left.venue, left.product, left.account_id) == (right.environment, right.venue, right.product, right.account_id)

    def _account_has_active_window(self, scope):
        for key, other in self._sessions.items():
            if not self._same_physical_account(scope, other.host.scope):
                continue
            job = self._jobs.get(key)
            if other.running or (job and not job['task'].done()):
                return True
        return False

    def configure_strategy(self, session_key, payload):
        """Store a bounded, explicit AI grant; this neither qualifies nor starts it."""
        from .demo_control import DemoStrategyInput
        if not isinstance(payload, DemoStrategyInput):
            raise DemoError('DEMO_STRATEGY_INPUT_REQUIRED')
        with self._lifecycle_lock:
            runtime = self._runtime(session_key)
            if self._account_has_active_window(runtime.host.scope):
                raise DemoError('DEMO_ACCOUNT_RUNTIME_ACTIVE')
            with runtime.lease.registration_guard():
                if self.models is None:
                    raise DemoError('DEMO_MODEL_PROFILE_REQUIRED')
                model = self.models.get_config(payload.model_profile_id)
                if (model.get('revision') != payload.model_revision or model.get('enabled') is not True or
                        not (model.get('key_available') is True or model.get('local_mode') is True)):
                    raise DemoError('DEMO_MODEL_PROFILE_UNAVAILABLE')
                now = int(time.time())
                if now + payload.authorization_seconds >= runtime.lease.expires_at:
                    raise DemoError('DEMO_AUTHORIZATION_EXCEEDS_CREDENTIAL_TTL')
                revision = payload.expected_strategy_revision + 1
                config = {'revision': revision, 'policy_revision': revision, 'authorized': True,
                    'authorization_ref': 'demo-grant:' + uuid4().hex, 'authorized_until': now + payload.authorization_seconds,
                    'allow_execution': True, 'mode': 'AI', 'allow_model_calls': True,
                    'model_profile_ids': [payload.model_profile_id], 'model_revisions': {payload.model_profile_id: payload.model_revision},
                    'analysis_mode': 'single', 'decision_policy': 'unanimous', 'budget': payload.budget,
                    'max_planned_loss': payload.max_planned_loss, 'max_daily_loss': payload.max_daily_loss,
                    'fee_rate_cap': payload.fee_rate_cap, 'protection_mode': 'HOSTED', 'authorization_seconds': payload.authorization_seconds,
                    'profile': {'strategy_revision': revision, 'decision_interval': '1m',
                        'holding_period_seconds': payload.holding_period_seconds,
                        'allowed_operations': ['SET_TARGET', 'UPDATE_EXIT', 'HOLD'],
                        'required': ['quote', 'candles:1m'], 'optional': ['book'], 'windows': {'1m': 50}}}
                self.repository.save_strategy(runtime.host.scope, config, expected_revision=payload.expected_strategy_revision)
                runtime.strategy_revision = revision
                return {'revision': revision, 'authorized_until': config['authorized_until'],
                        'execution_authorized': False, 'state': 'CONFIGURED_NOT_QUALIFIED'}

    def operator_status(self, session_key):
        """Read the stored grant and evidence state without issuing authority."""
        result = self.status(session_key)
        if not result.get('configured'):
            return {**result, 'saved_strategy': None, 'qualification_blocker': result.get('blocker')}
        runtime = self._runtime(session_key)
        try:
            strategy = self.repository.strategy(runtime.host.scope)
        except DemoError as exc:
            if exc.code != 'DEMO_STRATEGY_UNREGISTERED': raise
            strategy = None
        summary = None
        if strategy is not None:
            ids = strategy.get('model_profile_ids') or []
            profile_id = ids[0] if len(ids) == 1 else None
            summary = {'revision': strategy['revision'], 'grant_hash': digest(strategy), 'mode': strategy.get('mode'),
                'model_profile_id': profile_id, 'model_revision': (strategy.get('model_revisions') or {}).get(profile_id),
                'budget': strategy.get('budget'), 'max_planned_loss': strategy.get('max_planned_loss'),
                'max_daily_loss': strategy.get('max_daily_loss'), 'fee_rate_cap': strategy.get('fee_rate_cap'),
                'holding_period_seconds': (strategy.get('profile') or {}).get('holding_period_seconds'),
                'authorization_seconds': strategy.get('authorization_seconds'), 'authorized_until': strategy.get('authorized_until'),
                'authorized': strategy.get('authorized') is True, 'allow_execution': strategy.get('allow_execution') is True,
                'allow_model_calls': strategy.get('allow_model_calls') is True}
        try:
            self._require_execution_qualification(runtime, self.repository.account(runtime.host.scope))
        except DemoError as exc:
            qualification_blocker = exc.code
        else:
            qualification_blocker = None  # Matching stored evidence is not a live venue guarantee.
        return {**result, 'saved_strategy': summary, 'qualification_blocker': qualification_blocker}

    def operator_observations(self, session_key, *, limit=50):
        runtime = self._runtime(session_key)
        return self.repository.operator_view(runtime.host.scope, limit=limit)

    def _require_execution_qualification(self, runtime, account):
        import re
        evidence = account.get('qualification') or {}
        required = {'state':'DEMO_VERIFIED', 'code_sha':self.current_code_sha,
                    'account_key':runtime.host.scope.account_key, 'credential_revision':account.get('credential_revision'),
                    'product':'SPOT', 'history_coverage':'COMPLETE', 'protection':'HOSTED'}
        if (any(evidence.get(key) != value for key, value in required.items()) or
                not re.fullmatch(r'[0-9a-f]{64}', evidence.get('baseline_hash') or '') or
                not re.fullmatch(r'[0-9a-f]{64}', evidence.get('evidence_sha256') or '') or
                not isinstance(evidence.get('official_contract_revision'), str) or not evidence['official_contract_revision']):
            raise DemoError('DEMO_QUALIFICATION_REQUIRED')
        capabilities = evidence.get('capabilities') or {}
        for capability in ('LIMIT', 'CANCEL'):
            proof = capabilities.get(capability) or (evidence if evidence.get('order_type') == capability else {})
            if (proof.get('state') != 'DEMO_VERIFIED' or
                    not re.fullmatch(r'[0-9a-f]{64}', proof.get('evidence_sha256') or '') or
                    not isinstance(proof.get('official_contract_revision'), str) or not proof['official_contract_revision']):
                raise DemoError('DEMO_QUALIFICATION_REQUIRED')

    async def assess_once(self, session_key, run_id):
        runtime = self._runtime(session_key)
        result = await runtime.host.assess_once(run_id)
        runtime.lease.validate()
        return result

    async def run_window(self, session_key, *, duration_seconds, execute=False, decision_interval_seconds=60, socket_factory=private_connection, expected_strategy=None, expected_credential_revision=None):
        with self._lifecycle_lock:
            runtime = self._runtime(session_key)
            if runtime.running or type(duration_seconds) is not int or not 1 <= duration_seconds <= 900 or type(decision_interval_seconds) is not int or decision_interval_seconds < 30:
                raise DemoError('DEMO_RUNTIME_WINDOW_INVALID')
            if any(other is not runtime and other.running and self._same_physical_account(other.host.scope, runtime.host.scope) for other in self._sessions.values()):
                raise DemoError('DEMO_ACCOUNT_RUNTIME_DRAIN_REQUIRED')
            account = self.repository.account(runtime.host.scope)
            if account['credential_ref'] != runtime.lease.credential_ref:
                raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            if expected_credential_revision is not None and account.get('credential_revision') != expected_credential_revision:
                raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            if self.repository.engine.dialect.name != 'postgresql':
                raise DemoError('DEMO_POSTGRESQL_OWNER_REQUIRED')
            if time.time() + duration_seconds >= runtime.lease.expires_at:
                raise DemoError('DEMO_WINDOW_EXCEEDS_CREDENTIAL_TTL')
            if execute:
                strategy = self.repository.strategy(runtime.host.scope)
                if strategy.get('allow_execution') is not True or strategy['authorized_until'] < time.time() + duration_seconds:
                    raise DemoError('DEMO_EXECUTION_PERMISSION_REQUIRED')
                if expected_strategy is not None and strategy != expected_strategy:
                    raise DemoError('DEMO_DECISION_BINDING_CHANGED')
                expected_strategy = deepcopy(strategy)
            runtime.running = True
            runtime.stop_requested = False
            runtime.new_risk_paused = False
            runtime.last_error = None
            runtime.window_task = asyncio.current_task()
        deadline = time.monotonic() + duration_seconds
        window_id = uuid4().hex
        def verify_binding():
            if runtime.stop_requested or runtime.revoked:
                raise DemoError('DEMO_STOP_REQUESTED')
            if time.monotonic() >= deadline:
                raise DemoError('DEMO_WINDOW_EXPIRED')
            if execute and time.time() >= expected_strategy.get('authorized_until', 0):
                raise DemoError('DEMO_EXECUTION_PERMISSION_REQUIRED')
            runtime.lease.validate()
            account = self.repository.account(runtime.host.scope)
            if (account['credential_ref'] != runtime.lease.credential_ref or
                    (expected_credential_revision is not None and account.get('credential_revision') != expected_credential_revision)):
                raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            if execute and self.repository.strategy(runtime.host.scope) != expected_strategy:
                raise DemoError('DEMO_DECISION_BINDING_CHANGED')
            # Repository and credential checks can block across the deadline.
            if runtime.stop_requested or runtime.revoked:
                raise DemoError('DEMO_STOP_REQUESTED')
            if time.monotonic() >= deadline:
                raise DemoError('DEMO_WINDOW_EXPIRED')
            if execute and time.time() >= expected_strategy.get('authorized_until', 0):
                raise DemoError('DEMO_EXECUTION_PERMISSION_REQUIRED')
        if execute:
            runtime.send_guard = verify_binding

        async def supervise(stream):
            await settled_thread(runtime.reconciler.reconcile, runtime.host.scope, symbol='BTCUSDT', stream=stream)
            new_risk_paused = False
            async def pause_new_risk(code):
                nonlocal new_risk_paused
                runtime.last_error = code; runtime.new_risk_paused = True; new_risk_paused = True
                try: await settled_thread(self.repository.pause, runtime.host.scope, code)
                except DemoError as exc:
                    if exc.code != 'DEMO_OWNER_BUSY': raise
            async def protect():
                last_recovery = 0
                while time.monotonic() < deadline:
                    self._runtime(session_key)
                    try:
                        await settled_thread(runtime.host.drain_inbox)
                        if runtime.host.memory is not None:
                            await settled_thread(runtime.host.memory.project, runtime.host.scope)
                        if execute:
                            await settled_thread(self.repository.abandon_stale_unsent, runtime.host.scope, now=int(time.time()))
                            health = await settled_thread(self.repository.health, runtime.host.scope)
                            if health['unknown'] and time.monotonic() - last_recovery >= 5:
                                await settled_thread(runtime.reconciler.reconcile, runtime.host.scope, symbol='BTCUSDT', stream=stream)
                                last_recovery = time.monotonic()
                            await settled_thread(verify_binding)
                            protection = await settled_thread(runtime.host.protection_tick)
                            if protection['state'] == 'RECONCILIATION_REQUIRED' and time.monotonic() - last_recovery >= 5:
                                await settled_thread(runtime.reconciler.reconcile, runtime.host.scope, symbol='BTCUSDT', stream=stream)
                                last_recovery = time.monotonic()
                            for command in protection['commands']:
                                self._runtime(session_key)
                                def dispatch_protection():
                                    verify_binding()
                                    return runtime.host.dispatch(command['command_id'])
                                await settled_thread(dispatch_protection)
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
                        if await settled_thread(self.repository.active_orders, runtime.host.scope):
                            await asyncio.sleep(1)
                            continue
                        await settled_thread(runtime.reconciler.reconcile_and_resume, runtime.host.scope, stream=stream, current_code_sha=self.current_code_sha)
                        sequence += 1
                        run_id = f'{window_id}:{sequence}'
                        await settled_thread(verify_binding)
                        await settle_before_cancel(runtime.host.assess_once(run_id, expected_strategy=expected_strategy, before_model=verify_binding))
                        if new_risk_paused: return
                        await settled_thread(verify_binding)
                        def admit_checked():
                            verify_binding()
                            return runtime.host.admit_assessment(run_id)
                        command = await settled_thread(admit_checked)
                        if command.get('command_id'):
                            self._runtime(session_key)
                            await settled_thread(verify_binding)
                            def dispatch_checked():
                                verify_binding()
                                return runtime.host.dispatch(command['command_id'])
                            await settled_thread(dispatch_checked)
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
            with self._lifecycle_lock:
                try:
                    self.repository.pause(runtime.host.scope, 'DEMO_RUNTIME_WINDOW_ENDED')
                except BaseException:
                    runtime.drain_failed = True
                    runtime.revoked = True
                    runtime.last_error = 'DEMO_DRAIN_PAUSE_UNCONFIRMED'
                    runtime.stream.healthy = False
                    runtime.transport.close()
                    raise
                finally:
                    runtime.running = False
                    runtime.send_guard = None
                    runtime.window_task = None
                    if runtime.revoked and not runtime.drain_failed and self._sessions.get(session_key) is runtime:
                        try:
                            runtime.transport.close()
                        except Exception:
                            runtime.drain_failed = True
                            runtime.last_error = 'DEMO_DRAIN_CLOSE_UNCONFIRMED'
                            raise
                        self._sessions.pop(session_key, None)
        return self.status(session_key)

    def start_window(self, session_key, *, duration_seconds, decision_interval_seconds, expected_strategy_revision, expected_grant_hash):
        """Schedule one bounded real-Demo AI window, without bypassing admission."""
        return self._schedule_window(session_key, duration_seconds=duration_seconds, decision_interval_seconds=decision_interval_seconds, execute=True, expected_strategy_revision=expected_strategy_revision, expected_grant_hash=expected_grant_hash)

    def start_recovery(self, session_key, *, duration_seconds):
        """Schedule subscribed backfill and persistence, with no model or sends."""
        return self._schedule_window(session_key, duration_seconds=duration_seconds, decision_interval_seconds=60, execute=False)

    def _schedule_window(self, session_key, *, duration_seconds, decision_interval_seconds, execute, expected_strategy_revision=None, expected_grant_hash=None):
        if (type(duration_seconds) is not int or not 1 <= duration_seconds <= 900 or
                type(decision_interval_seconds) is not int or not 30 <= decision_interval_seconds <= 900):
            raise DemoError('DEMO_RUNTIME_WINDOW_INVALID')
        with self._lifecycle_lock:
            if self._closing: raise DemoError('DEMO_SERVICE_CLOSED')
            runtime = self._runtime(session_key)
            if self.repository.engine.dialect.name != 'postgresql':
                raise DemoError('DEMO_POSTGRESQL_OWNER_REQUIRED')
            existing = self._jobs.get(session_key)
            if runtime.running or (existing and not existing['task'].done()):
                raise DemoError('DEMO_RUNTIME_WINDOW_ACTIVE')
            if self._account_has_active_window(runtime.host.scope):
                raise DemoError('DEMO_ACCOUNT_RUNTIME_ACTIVE')
            now = time.time()
            strategy = None
            account = self.repository.account(runtime.host.scope)
            if account['credential_ref'] != runtime.lease.credential_ref:
                raise DemoError('DEMO_CREDENTIAL_BINDING_REJECTED')
            if execute:
                strategy = deepcopy(self.repository.strategy(runtime.host.scope))
                if (type(expected_strategy_revision) is not int or strategy.get('revision') != expected_strategy_revision or
                        not isinstance(expected_grant_hash, str) or digest(strategy) != expected_grant_hash):
                    raise DemoError('DEMO_DECISION_BINDING_CHANGED')
                self._require_execution_qualification(runtime, account)
                if (strategy.get('mode') != 'AI' or strategy.get('authorized') is not True or
                        strategy.get('allow_model_calls') is not True or strategy.get('allow_execution') is not True or
                        strategy.get('authorized_until', 0) < now + duration_seconds):
                    raise DemoError('DEMO_EXECUTION_PERMISSION_REQUIRED')
            if now + duration_seconds >= runtime.lease.expires_at:
                raise DemoError('DEMO_WINDOW_EXCEEDS_CREDENTIAL_TTL')
            job = {'state': 'START_REQUESTED', 'error': None, 'task': None}
            async def run():
                job['state'] = 'RUNNING'
                try:
                    await self.run_window(session_key, duration_seconds=duration_seconds, execute=execute,
                                          decision_interval_seconds=decision_interval_seconds, expected_strategy=strategy,
                                          expected_credential_revision=account.get('credential_revision'))
                except asyncio.CancelledError:
                    job['state'] = 'BLOCKED' if runtime.drain_failed else 'STOPPED_REVIEW_REQUIRED'
                    if runtime.drain_failed: job['error'] = 'DEMO_DRAIN_PAUSE_UNCONFIRMED'
                except DemoError as exc:
                    job['state'], job['error'] = 'BLOCKED', exc.code
                except Exception:
                    job['state'], job['error'] = 'FAILED', 'DEMO_RUNTIME_WINDOW_FAILED'
                else:
                    if runtime.new_risk_paused:
                        job['state'], job['error'] = 'ENDED_BLOCKED_REVIEW_REQUIRED', runtime.last_error
                    else:
                        job['state'] = 'ENDED_REVIEW_REQUIRED'
            task = asyncio.get_running_loop().create_task(run(), name='aethertrade-demo-window')
            job['task'] = task
            self._jobs[session_key] = job
            def finished(value):
                if value.cancelled() and job['state'] == 'START_REQUESTED':
                    job['state'] = 'STOPPED_BEFORE_START'
            task.add_done_callback(finished)
            return {'state': 'START_REQUESTED'}

    def request_stop(self, session_key):
        # This requests cancellation only. Completion requires the window's
        # final durable pause; failures remain latched and block rebind.
        with self._lifecycle_lock:
            runtime = self._sessions.get(session_key)
            if runtime is None:
                raise DemoError('DEMO_RUNTIME_NOT_BOUND')
            if runtime.drain_failed:
                raise DemoError('DEMO_DRAIN_PAUSE_UNCONFIRMED')
            if not runtime.running or runtime.window_task is None:
                job = self._jobs.get(session_key)
                if job and not job['task'].done():
                    job['task'].get_loop().call_soon_threadsafe(job['task'].cancel)
                    return {'state': 'STOP_REQUESTED'}
                return {'state': 'NOT_RUNNING'}
            if not runtime.stop_requested:
                runtime.stop_requested = True
                job = self._jobs.get(session_key)
                if job and not job['task'].done(): job['state'] = 'STOP_PENDING'
                runtime.window_task.get_loop().call_soon_threadsafe(runtime.window_task.cancel)
            return {'state': 'STOP_REQUESTED'}

    def close_session(self, session_key):
        with self._lifecycle_lock:
            return self._close_session(session_key)

    def _close_session(self, session_key):
        runtime = self._sessions.get(session_key)
        if runtime:
            job = self._jobs.get(session_key)
            task = runtime.window_task or (job['task'] if job and not job['task'].done() else None)
            runtime.revoked = True; runtime.stream.healthy = False
            if task is not None and not task.done():
                if not runtime.stop_requested:
                    runtime.stop_requested = True
                    if job: job['state'] = 'STOP_PENDING'
                    task.get_loop().call_soon_threadsafe(task.cancel)
                if runtime.running:
                    return  # The window owns pause and transport close after all workers settle.
            try:
                self.repository.pause(runtime.host.scope, 'DEMO_SESSION_REVOKED')
            except BaseException:
                runtime.drain_failed = True
                runtime.last_error = 'DEMO_DRAIN_PAUSE_UNCONFIRMED'
                raise
            finally:
                runtime.transport.close()
            if not runtime.running and not runtime.drain_failed:
                self._sessions.pop(session_key, None)

    async def aclose(self):
        # Revoke first, then await actual tasks/threads before disposing storage.
        with self._lifecycle_lock:
            self._closing = True
            sessions = list(self._sessions)
        for session in sessions:
            try: self.close_session(session)
            except Exception: pass  # close() below reports unresolved durable pause.
        with self._lifecycle_lock:
            tasks = list({runtime.window_task for runtime in self._sessions.values() if runtime.window_task is not None})
            tasks += [job['task'] for job in self._jobs.values() if not job['task'].done() and job['task'] not in tasks]
        if tasks:
            await settle_before_cancel(asyncio.gather(*tasks, return_exceptions=True))
        self.close()

    def close(self):
        with self._lifecycle_lock:
            self._closing = True
            if any(runtime.running for runtime in self._sessions.values()) or any(not job['task'].done() for job in self._jobs.values()):
                raise DemoError('DEMO_RUNTIME_DRAIN_REQUIRED')
        failed = any(runtime.drain_failed for runtime in self._sessions.values())
        for session in list(self._sessions):
            try: self.close_session(session)
            except Exception: failed = True
        if self._close_repository is not None:
            self._close_repository()
        if failed:
            raise DemoError('DEMO_RUNTIME_SHUTDOWN_RECONCILIATION_REQUIRED')
