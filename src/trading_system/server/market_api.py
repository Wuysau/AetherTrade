"""Authenticated, same-origin market snapshots, source controls and bounded SSE."""
import asyncio
import json
import time
from typing import Literal

from fastapi import Depends, Query, Request
from pydantic import Field, StrictInt
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from .errors import ApiError
from .validation import Payload, Symbol

Interval = Literal['1m', '5m', '15m', '1h', '4h', '1d']


class SourceRevision(Payload):
    expected_revision: StrictInt = Field(ge=0)


async def market_events(request, snapshot, validate_session, *, clock=time.monotonic, sleep=asyncio.sleep):
    """Coalesce updates, periodically re-authorize, and never retain a send queue."""
    previous = None
    checked = sent = clock()
    while not await request.is_disconnected():
        current_time = clock()
        if current_time - checked >= 10:
            try:
                await validate_session()
            except ApiError:
                yield 'event: auth-expired\ndata: {}\n\n'
                return
            checked = current_time
        # Snapshot is an in-memory detached value; source secrets never enter it.
        encoded = json.dumps(snapshot(), separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        if encoded != previous:
            yield 'event: market\ndata: ' + encoded + '\n\n'
            previous, sent = encoded, current_time
        elif current_time - sent >= 10:
            yield ': heartbeat\n\n'
            sent = current_time
        await sleep(.5)


def install_market_routes(app, *, store, config_service, feed, snapshot, reconfigure,
                          authenticated, mutation, settings, externally_provided=False):
    subscribers = set()

    @app.get('/api/market/config')
    def get_market_config(_=Depends(authenticated)):
        return config_service.get_config()

    @app.put('/api/market/config')
    async def save_market_config(payload: dict, _=Depends(mutation)):
        config = await run_in_threadpool(config_service.save_config, payload)
        await reconfigure()
        return config

    @app.post('/api/market/config/test')
    async def test_market_config(payload: SourceRevision, _=Depends(mutation)):
        if not hasattr(feed, 'test_connection'):
            raise ApiError('MARKET_TEST_UNAVAILABLE', '当前行情适配器不支持连接测试。', 409)
        return await feed.test_connection(payload.expected_revision)

    @app.get('/api/market/snapshot')
    def get_market_snapshot(_=Depends(authenticated)):
        return snapshot()

    @app.get('/api/market/candles')
    async def get_market_candles(symbol: Symbol, interval: Interval = '1m',
                                 limit: int = Query(default=120, ge=1, le=200), _=Depends(authenticated)):
        if (not settings.market_enabled and not externally_provided) or not hasattr(feed, 'candles'):
            raise ApiError('MARKET_HISTORY_UNAVAILABLE', '行情服务未启用或不支持 K 线历史。', 409)
        return await feed.candles(symbol, interval, limit)

    @app.get('/api/market/stream')
    async def stream_market(request: Request, _=Depends(authenticated)):
        origin = request.headers.get('origin')
        if origin and origin not in settings.allowed_origins:
            raise ApiError('ORIGIN_REJECTED', '行情推送来源不受信任。', 403)
        if len(subscribers) >= 12:
            raise ApiError('MARKET_STREAM_LIMIT', '行情连接过多，请关闭重复页面后重试。', 429)
        token = object()
        subscribers.add(token)
        session_token = request.cookies.get(settings.cookie_name)

        async def validate_session():
            await run_in_threadpool(store.session, session_token)

        async def events():
            try:
                async for event in market_events(request, snapshot, validate_session):
                    yield event
            finally:
                subscribers.discard(token)

        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
                                 background=BackgroundTask(subscribers.discard, token))
