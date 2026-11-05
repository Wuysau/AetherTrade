"""Explicit authenticated controls for the isolated Demo runtime.

These routes do not create qualification evidence or bypass runtime admission.
"""
from fastapi import Depends, Query, Request
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.runtime.demo_control import DemoBindInput, DemoRecoveryInput, DemoStrategyInput, DemoWindowInput
from .demo_api import session_key
from .errors import ApiError


def install_demo_control_routes(app, service, authenticated, mutation, settings):
    def runtime():
        if service.runtime is None:
            raise ApiError('DEMO_RUNTIME_NOT_CONFIGURED', 'Demo 运行时未配置。', 503)
        return service.runtime

    def fail(exc):
        raise ApiError(exc.code, 'Demo 操作被安全门禁阻止，请检查运行状态。', 422) from None

    @app.get('/api/demo/runtime/status')
    def status(request: Request, _=Depends(authenticated)):
        return runtime().operator_status(session_key(request, settings))

    @app.get('/api/demo/runtime/observations')
    def observations(request: Request, limit: int = Query(50, ge=1, le=100), _=Depends(authenticated)):
        try: return runtime().operator_observations(session_key(request, settings), limit=limit)
        except DemoError as exc: fail(exc)

    @app.post('/api/demo/runtime/bind')
    def bind(payload: DemoBindInput, request: Request, _=Depends(mutation)):
        try: return runtime().bind_session(session_key(request, settings))
        except DemoError as exc: fail(exc)

    @app.post('/api/demo/runtime/strategy')
    def configure(payload: DemoStrategyInput, request: Request, _=Depends(mutation)):
        try: return runtime().configure_strategy(session_key(request, settings), payload)
        except DemoError as exc: fail(exc)

    @app.post('/api/demo/runtime/start')
    async def start(payload: DemoWindowInput, request: Request, _=Depends(mutation)):
        try: return runtime().start_window(session_key(request, settings), duration_seconds=payload.duration_seconds,
                                           decision_interval_seconds=payload.decision_interval_seconds,
                                           expected_strategy_revision=payload.expected_strategy_revision, expected_grant_hash=payload.expected_grant_hash)
        except DemoError as exc: fail(exc)

    @app.post('/api/demo/runtime/recover')
    async def recover(payload: DemoRecoveryInput, request: Request, _=Depends(mutation)):
        try: return runtime().start_recovery(session_key(request, settings), duration_seconds=payload.duration_seconds)
        except DemoError as exc: fail(exc)

    @app.post('/api/demo/runtime/stop')
    def stop(request: Request, _=Depends(mutation)):
        try: return runtime().request_stop(session_key(request, settings))
        except DemoError as exc: fail(exc)
