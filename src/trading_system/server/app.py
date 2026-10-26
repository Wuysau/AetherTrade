"""Authenticated browser API and static runtime for the local PAPER console."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import inspect
import logging
from pathlib import Path
import secrets

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.concurrency import run_in_threadpool

from .automation import AutomationService
from .config import Settings
from .errors import ApiError
from .market_config import MarketConfigService
from .market_live import LiveMarketFeed
from .market_api import install_market_routes
from .instruments import instruments
from .model_service import ModelService
from .model_api import ProfileAnalysis, ProfileVersion
from .store import Store
from .validation import Credentials, ModelAnalysis, ModelTest, PaperAction, PaperOrder, RiskUpdate, Setup, StrategyCreate, StrategyPatch

log = logging.getLogger(__name__)
VERSION = "0.1.0"


def create_app(settings: Settings | None = None, *, quote_provider=None, model_client=None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.database_url)
    market_config = MarketConfigService(store, settings.model_key_path)
    feed = quote_provider if quote_provider is not None else LiveMarketFeed(market_config, max_age_seconds=settings.quote_max_age_seconds)
    models = ModelService(store, settings.model_key_path, client=model_client)
    refresh_lock = asyncio.Lock()
    refresh_failed = False

    def market_snapshot():
        current = feed.snapshot()
        if not settings.market_enabled and quote_provider is None:
            return {**current, 'status': 'UNAVAILABLE', 'error': '服务端行情读取已关闭。',
                    'transport': {**current.get('transport', {}), 'mode': 'disabled', 'status': 'DISABLED'},
                    'quotes': [{**quote, 'stale': True} for quote in current.get('quotes', [])]}
        if refresh_failed:
            return {**current, "status": "STALE" if current.get("quotes") else "UNAVAILABLE",
                    "error": "公开行情更新失败，请稍后重试。",
                    "quotes": [{**quote, "stale": True} for quote in current.get("quotes", [])]}
        return current

    automation = AutomationService(store, models, market_snapshot, settings)

    async def refresh():
        nonlocal refresh_failed
        if refresh_lock.locked():
            return market_snapshot()
        async with refresh_lock:
            try:
                # The provider has its own bound; this also bounds injected adapters.
                result = await asyncio.wait_for(feed.refresh(), timeout=15)
            except Exception:
                refresh_failed = True
                return market_snapshot()
            refresh_failed = False
            await run_in_threadpool(store.record_observation, result, settings.quote_max_age_seconds)
            return result

    async def poll():
        while True:
            try:
                if hasattr(feed, 'start'):
                    # Live adapters own their network loops. Persist only periodic
                    # observations, rather than writing every upstream market tick.
                    await run_in_threadpool(store.record_observation, market_snapshot(), settings.quote_max_age_seconds)
                else:
                    await refresh()
            except Exception:
                # Do not log provider URLs, database details or credential text.
                log.warning("Market observation unavailable; next scheduled refresh will retry")
            await asyncio.sleep(settings.market_poll_seconds)

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(store.initialize)
        await run_in_threadpool(models.migrate)
        await run_in_threadpool(market_config.migrate)
        if settings.market_enabled and hasattr(feed, 'start'):
            await feed.start()
        task = asyncio.create_task(poll(), name="aethertrade-public-quotes") if settings.market_enabled else None
        automation_task = asyncio.create_task(automation.run(), name="aethertrade-ai-automation") if settings.automation_enabled and not settings.testing else None
        try:
            yield
        finally:
            if automation_task:
                automation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await automation_task
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            close = getattr(feed, "close", None)
            try:
                if close:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            finally:
                try:
                    await models.close()
                finally:
                    await run_in_threadpool(store.close)

    app = FastAPI(title="AetherTrade local PAPER API", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.market = settings, store, feed
    app.state.models = models
    app.state.automation = automation
    app.state.market_config = market_config

    @app.exception_handler(ApiError)
    async def expected_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors can include passwords or raw request bodies.
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "请求字段无效，请检查格式、范围和必填项。"}}, status_code=422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse({"error": {"code": "DATABASE_UNAVAILABLE", "message": "数据库暂时不可用，请稍后重试。"}}, status_code=503)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": {"code": "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", "message": "未找到资源。" if exc.status_code == 404 else "请求无法处理。"}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "服务暂时无法完成请求，请稍后重试。"}}, status_code=500)

    @app.middleware("http")
    async def request_guard(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("origin") not in settings.allowed_origins:
                return JSONResponse({"error": {"code": "ORIGIN_REJECTED", "message": "请求来源不受信任，请从本地应用页面操作。"}}, status_code=403)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 16_384:
                    return JSONResponse({"error": {"code": "REQUEST_TOO_LARGE", "message": "请求内容过大。"}}, status_code=413)
            request._body = bytes(body)  # Starlette's cached request replays this to the endpoint.
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"])

    def authenticated(request: Request):
        return store.session(request.cookies.get(settings.cookie_name))

    def mutation(request: Request, session=Depends(authenticated)):
        provided = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(provided.encode(), session["csrf_token"].encode()):
            raise ApiError("CSRF_REJECTED", "安全令牌无效，请刷新页面后重试。", 403)
        return session

    async def reconfigure_market():
        nonlocal refresh_failed
        refresh_failed = False
        if hasattr(feed, 'reconfigure'):
            await feed.reconfigure()

    install_market_routes(app, store=store, config_service=market_config, feed=feed,
                          snapshot=market_snapshot, reconfigure=reconfigure_market,
                          authenticated=authenticated, mutation=mutation, settings=settings,
                          externally_provided=quote_provider is not None)

    def session_response(data):
        token = data.pop("token")
        response = JSONResponse(data)
        response.set_cookie(settings.cookie_name, token, max_age=settings.session_hours * 3600,
                            httponly=True, secure=settings.cookie_secure, samesite="strict", path="/")
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": VERSION, "environment": "PAPER", "database": store.healthy()}

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"needs_setup": store.needs_setup(), "environment": "PAPER", "version": VERSION}

    @app.post("/api/setup")
    def setup(payload: Setup):
        return session_response(store.setup(payload.username, payload.password, payload.initial_cash, settings.session_hours))

    @app.post("/api/session/login")
    def login(payload: Credentials, request: Request):
        return session_response(store.login(payload.username, payload.password, request.client.host if request.client else "local", settings.session_hours))

    @app.get("/api/session")
    def session(data=Depends(authenticated)):
        return data

    @app.post("/api/session/logout")
    def logout(request: Request, _=Depends(mutation)):
        store.logout(request.cookies.get(settings.cookie_name))
        response = JSONResponse({"ok": True})
        response.delete_cookie(settings.cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite="strict")
        return response

    @app.get("/api/dashboard")
    def dashboard(range: str = Query("24H"), _=Depends(authenticated)):
        result = store.dashboard(market_snapshot(), range)
        result['instruments'] = instruments()
        profile_collection = models.list_configs()
        result['model_profiles'] = profile_collection['profiles']
        result['model_config'] = next(profile for profile in profile_collection['profiles'] if profile['id'] == profile_collection['default_profile_id'])
        result['decisions'] = models.decisions()
        result['services'] = [item for item in result['services'] if item['id'] not in ('ai', 'equities')]
        result['services'].extend({'id': 'model:' + profile['id'], 'label': profile['name'],
            'status': profile['connection_status'] if profile['enabled'] else 'CONFIGURED' if profile['model'] else 'NOT_CONFIGURED',
            'detail': (profile['model'] or '尚未配置模型') + (' · 已启用，按策略选择使用' if profile['enabled'] else ' · 已停用'),
            'updated_at': profile['last_test_at']} for profile in profile_collection['profiles'])
        stock_group = next((group for group in result['market'].get('groups', []) if group['id'] == 'bstocks'), None)
        result['services'].append({'id': 'bstocks', 'label': 'bStocks 现货',
            'status': 'CONNECTED' if stock_group and stock_group['status'] == 'CONNECTED' else 'UNAVAILABLE',
            'detail': '首批：TSLABUSDT、NVDABUSDT、AAPLBUSDT；币安代币化股票现货。',
            'updated_at': stock_group['last_updated'] if stock_group else None})
        return automation.enrich_dashboard(result)

    @app.get("/api/notifications")
    def get_notifications(_=Depends(authenticated)):
        return automation.notifications()

    @app.post("/api/notifications/read")
    def read_notifications(payload: dict, _=Depends(mutation)):
        return automation.mark_read(payload)

    @app.get('/api/instruments')
    def instrument_catalog(_=Depends(authenticated)):
        return {'instruments': instruments()}

    @app.get('/api/model-config')
    def model_config(_=Depends(authenticated)):
        return models.get_config()

    @app.get('/api/model-configs')
    def model_profiles(_=Depends(authenticated)):
        return models.list_configs()

    @app.post('/api/model-configs')
    def create_model_profile(payload: dict, _=Depends(mutation)):
        return models.create_config(payload)

    @app.get('/api/model-configs/{profile_id}')
    def model_profile(profile_id: str, _=Depends(authenticated)):
        return models.get_config(profile_id)

    @app.put('/api/model-configs/{profile_id}')
    def save_model_profile(profile_id: str, payload: dict, _=Depends(mutation)):
        return models.save_config(payload, profile_id=profile_id)

    @app.delete('/api/model-configs/{profile_id}')
    def delete_model_profile(profile_id: str, payload: ProfileVersion, _=Depends(mutation)):
        return models.delete_config(profile_id, payload.expected_revision)

    @app.post('/api/model-configs/{profile_id}/test')
    async def test_model_profile(profile_id: str, payload: ProfileVersion, _=Depends(mutation)):
        return await models.test_connection(payload.expected_revision, profile_id=profile_id)

    @app.put('/api/model-config')
    def save_model_config(payload: dict, _=Depends(mutation)):
        return models.save_config(payload)

    @app.post('/api/model-config/test')
    async def test_model(payload: ModelTest, _=Depends(mutation)):
        return await models.test_connection(payload.expected_revision)

    @app.post('/api/decisions/generate')
    async def generate_decision(payload: ModelAnalysis, idempotency_key: str | None = Header(default=None), _=Depends(mutation)):
        context = await run_in_threadpool(store.analysis_context, payload.symbol, market_snapshot(), settings.quote_max_age_seconds)
        return await models.generate(payload.symbol, payload.expected_revision, idempotency_key, context)

    @app.post('/api/decisions/analyze')
    async def analyze_with_profiles(payload: ProfileAnalysis, idempotency_key: str | None = Header(default=None), _=Depends(mutation)):
        context = await run_in_threadpool(store.analysis_context, payload.symbol, market_snapshot(), settings.quote_max_age_seconds)
        return await models.analyze(payload.symbol, payload.analysis_mode, payload.model_profile_ids,
                                    payload.expected_revisions, idempotency_key, context)

    @app.post("/api/market/refresh")
    async def market_refresh(_=Depends(mutation)):
        if not settings.market_enabled and quote_provider is None:
            return market_snapshot()
        return await refresh()

    @app.post("/api/strategies")
    def create_strategy(payload: StrategyCreate, _=Depends(mutation)):
        return store.create_strategy(payload.model_dump(exclude_unset=True))

    @app.patch("/api/strategies/{identifier}")
    def update_strategy(identifier: str, payload: StrategyPatch, _=Depends(mutation)):
        return store.update_strategy(identifier, payload.model_dump(exclude_unset=True))

    @app.put("/api/risk-policy")
    def update_risk(payload: RiskUpdate, _=Depends(mutation)):
        return store.update_risk(payload.model_dump())

    @app.post("/api/paper/orders")
    def paper_order(payload: PaperOrder, idempotency_key: str | None = Header(default=None), _=Depends(mutation)):
        return store.order(payload.model_dump(), idempotency_key, market_snapshot(), settings.quote_max_age_seconds)

    @app.post("/api/paper/actions")
    def paper_action(payload: PaperAction, idempotency_key: str | None = Header(default=None), _=Depends(mutation)):
        return store.action(payload.model_dump(exclude_unset=True), idempotency_key, market_snapshot(), settings.quote_max_age_seconds)

    @app.get("/api/research/export")
    def research_export(_=Depends(authenticated)):
        return PlainTextResponse(store.export(market_snapshot()), headers={"Content-Disposition": 'attachment; filename="aethertrade-paper-report.txt"'})

    @app.get("/{path:path}")
    def frontend(path: str):
        if path == "api" or path.startswith("api/") or "\\" in path or "\x00" in path:
            raise HTTPException(404)
        root = Path(settings.web_dist).resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root) or any(part in {"..", "."} for part in Path(path).parts):
            raise HTTPException(404)
        if candidate.is_file():
            return FileResponse(candidate)
        if Path(path).suffix or path.startswith("assets/"):
            raise HTTPException(404)
        index = (root / "index.html").resolve()
        if index.is_relative_to(root) and index.is_file():
            return FileResponse(index)
        return PlainTextResponse("AetherTrade API 已启动。请运行 npm --prefix web run build 构建浏览器页面。", status_code=503)

    return app
