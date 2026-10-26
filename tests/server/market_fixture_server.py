"""Controlled HTTP/WebSocket market fixture, never part of production startup.

Run with .venv/Scripts/python.exe tests/server/market_fixture_server.py --port18084.
Synthetic quotes/candles are only for isolated UI acceptance. No upstream calls.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import time

from fastapi import FastAPI, Header, HTTPException, WebSocket
import uvicorn

SYMBOLS = {'BTCUSDT': 68000, 'ETHUSDT': 3400, 'SOLUSDT': 160,
           'TSLABUSDT': 240, 'NVDABUSDT': 130, 'AAPLBUSDT': 220}
INTERVALS = {'1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400}
KEY = 'Bearer qa-market-fixture-key'
app = FastAPI(docs_url=None, redoc_url=None)


def authorized(value):
    if value != KEY:
        raise HTTPException(401, 'Controlled fixture authentication required')


def candle(symbol, interval, timestamp):
    base = SYMBOLS[symbol]
    phase = timestamp / INTERVALS[interval]
    opened = base * (1 + .004 * math.sin(phase / 6))
    closed = opened * (1 + .001 * math.sin(phase))
    return dict(time=timestamp * 1000, open=f'{opened:.8f}', close=f'{closed:.8f}',
                high=f'{max(opened, closed) * 1.0005:.8f}', low=f'{min(opened, closed) * .9995:.8f}', volume='125.50')


def snapshot():
    seconds = int(time.time())
    stamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    quotes, books, trades, candles = [], {}, {}, {}
    for index, (symbol, base) in enumerate(SYMBOLS.items()):
        price = base * (1 + .0015 * math.sin(seconds / 6 + index))
        quotes.append(dict(symbol=symbol, price=f'{price:.8f}', change_pct=f'{(price/base-1)*100:.4f}',
                           as_of=stamp, open=str(base), high=f'{base*1.01:.8f}', low=f'{base*.99:.8f}',
                           volume='4000', quote_volume=f'{base*4000:.8f}'))
        books[symbol] = dict(as_of=stamp, stale=False,
                            bids=[[f'{price*(1-.0001*(level+1)):.8f}', f'{.15*(level+1):.8f}'] for level in range(5)],
                            asks=[[f'{price*(1+.0001*(level+1)):.8f}', f'{.12*(level+1):.8f}'] for level in range(5)])
        trades[symbol] = [dict(id=f'{symbol}-{seconds}', price=f'{price:.8f}', quantity='0.02500000',
                               side='BUY' if seconds % 2 else 'SELL', as_of=stamp)]
        candles[symbol] = {interval: candle(symbol, interval, seconds // span * span)
                           for interval, span in INTERVALS.items()}
    return dict(quotes=quotes, books=books, trades=trades, candles=candles)


@app.get('/v1/snapshot')
def get_snapshot(authorization: str | None = Header(default=None)):
    authorized(authorization)
    return snapshot()


@app.get('/v1/candles')
def get_candles(symbol: str, interval: str, limit: int = 120, authorization: str | None = Header(default=None)):
    authorized(authorization)
    if symbol not in SYMBOLS or interval not in INTERVALS or not 1 <= limit <= 200:
        raise HTTPException(400, 'Invalid fixture query')
    span = INTERVALS[interval]
    latest = int(time.time()) // span * span
    return dict(symbol=symbol, interval=interval,
                candles=[candle(symbol, interval, latest - index * span) for index in reversed(range(limit))])


@app.websocket('/v1/stream')
async def stream(socket: WebSocket):
    if socket.headers.get('authorization') != KEY:
        await socket.close(code=1008)
        return
    await socket.accept()
    try:
        while True:
            await socket.send_text(json.dumps(snapshot()))
            await asyncio.sleep(.5)
    except Exception:
        return


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18084)
    args = parser.parse_args()
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False, log_level='warning')
