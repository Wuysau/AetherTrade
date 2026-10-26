// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { marketPrice, marketVolume } from '../src/runtime/format';
import { ApiError, RuntimeApi } from '../src/runtime/api';
import { MarketOrder, useMarketStream } from '../src/runtime/marketlive';
import { CandleChart, MarketTerminal, SourceSettings, validCandles } from '../src/runtime/marketterminal';
import type { Candle, CandleHistory, Market, MarketConfig } from '../src/runtime/types';
function snapshot(revision = 1, sequence = 1, instance = 'server-a'): Market {
  const now = new Date().toISOString();
  return { instance_id: instance, source_revision: revision, sequence, source: 'TEST SOURCE', status: 'CONNECTED', last_updated: now, error: null, quotes: [{ symbol: 'BTCUSDT', price: String(100 + sequence), change_pct: '1.2', as_of: now, source: 'TEST SOURCE', stale: false }], transport: { mode: 'websocket', status: 'LIVE', last_event_at: now, last_error: null, reconnect_attempt: 0 } };
}
const config: MarketConfig = { provider: 'standard_json', name: 'My source', rest_url: 'https://example.com', ws_url: '', auth_scheme: 'bearer', local_mode: false, enabled: true, poll_seconds: 5, timeout_seconds: 8, revision: 3, has_api_key: true, key_available: true };
const candle: Candle = { time: 1792994400000, open: '100', high: '110', low: '95', close: '105', volume: '4' };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => resolve = r); return { promise, resolve }; }
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('market ordering boundary', () => {
  it('blocks slower dashboard and previous-source data after a source switch', () => {
    const order = new MarketOrder(), newest = snapshot(2, 30); order.accept(newest, true);
    expect(order.accept(snapshot(2, 29))).toBe(newest); expect(order.accept(snapshot(1, 99), true)).toBe(newest);
    expect(order.accept({ ...snapshot(), instance_id: undefined, source_revision: undefined, sequence: undefined })).toBe(newest);
    const cleared = { ...snapshot(3, -1), quotes: [], books: {}, candles: {} }; order.accept(cleared, true);
    expect(order.accept(newest)).toBe(cleared); expect(order.current?.quotes).toHaveLength(0);
  });
  it('establishes process changes from live channel and rejects retired instances', () => {
    const order = new MarketOrder(), initial = snapshot(); order.accept(initial);
    expect(order.accept(snapshot(0, 1, 'server-b'))).toBe(initial);
    const restarted = snapshot(0, 1, 'server-b'); expect(order.accept(restarted, true)).toBe(restarted);
    expect(order.accept(snapshot(99, 999, 'server-a'), true)).toBe(restarted);
  });
});
describe('authenticated browser market stream', () => {
  it('uses same-origin EventSource and closes auth-expired stream', () => {
    class FakeSource {
      static latest: FakeSource; listeners = new Map<string, (event: unknown) => void>(); close = vi.fn(); onerror: (() => void) | null = null;
      constructor(public url: string) { FakeSource.latest = this; }
      addEventListener(name: string, callback: (event: unknown) => void) { this.listeners.set(name, callback); }
    }
    vi.stubGlobal('EventSource', FakeSource);
    const api = new RuntimeApi(), market = vi.fn(), unauthorized = vi.fn(); api.onUnauthorized = unauthorized;
    const close = api.openMarketStream({ market, unauthorized: vi.fn(), error: vi.fn() }), stream = FakeSource.latest;
    expect(stream.url).toBe('/api/market/stream'); stream.listeners.get('market')!({ data: JSON.stringify(snapshot()) }); expect(market).toHaveBeenCalledOnce();
    stream.listeners.get('auth-expired')!({}); expect(stream.close).toHaveBeenCalledOnce(); expect(unauthorized).toHaveBeenCalledOnce(); close();
  });
  it('closes hidden/unmounted streams, reconnects visible and ignores late events', () => {
    vi.stubGlobal('EventSource', class {}); let visibility = 'visible'; vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility as DocumentVisibilityState);
    const api = new RuntimeApi(), closes: ReturnType<typeof vi.fn>[] = [], callbacks: Parameters<RuntimeApi['openMarketStream']>[0][] = [];
    vi.spyOn(api, 'openMarketStream').mockImplementation(value => { callbacks.push(value); const close = vi.fn(); closes.push(close); return close; });
    const receive = vi.fn(), hook = renderHook(() => useMarketStream(api, true, receive));
    act(() => callbacks[0].market(snapshot())); expect(hook.result.current).toBe('streaming');
    act(() => { visibility = 'hidden'; document.dispatchEvent(new Event('visibilitychange')); }); expect(closes[0]).toHaveBeenCalledOnce(); expect(hook.result.current).toBe('paused');
    act(() => callbacks[0].market(snapshot(1, 3))); expect(receive).toHaveBeenCalledOnce();
    act(() => { visibility = 'visible'; document.dispatchEvent(new Event('visibilitychange')); }); expect(callbacks).toHaveLength(2);
    act(() => callbacks[0].market(snapshot(1, 99))); expect(receive).toHaveBeenCalledOnce();
    hook.unmount(); expect(closes[1]).toHaveBeenCalledOnce(); act(() => callbacks[1].market(snapshot())); expect(receive).toHaveBeenCalledOnce();
  });
  it('closes the live channel when the authenticated console is disabled', () => {
    vi.stubGlobal('EventSource', class {});
    const api = new RuntimeApi(), close = vi.fn(), receive = vi.fn();
    const open = vi.spyOn(api, 'openMarketStream').mockReturnValue(close);
    const hook = renderHook(({ enabled }) => useMarketStream(api, enabled, receive), { initialProps: { enabled: true } });
    expect(open).toHaveBeenCalledOnce(); hook.rerender({ enabled: false }); expect(close).toHaveBeenCalledOnce();
  });
  it('falls back to nonoverlapping five-second snapshots and stops after unauthorized', async () => {
    vi.useFakeTimers(); vi.stubGlobal('EventSource', undefined);
    const api = new RuntimeApi(), receive = vi.fn(), pending = deferred<Market>();
    const poll = vi.spyOn(api, 'marketSnapshot').mockReturnValueOnce(pending.promise).mockRejectedValueOnce(new ApiError(401, 'AUTH_REQUIRED', 'expired'));
    const hook = renderHook(() => useMarketStream(api, true, receive)); expect(poll).toHaveBeenCalledOnce();
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); }); expect(poll).toHaveBeenCalledOnce();
    await act(async () => { pending.resolve(snapshot()); await pending.promise; }); expect(receive).toHaveBeenCalledOnce(); expect(hook.result.current).toBe('polling');
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); expect(poll).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); }); expect(poll).toHaveBeenCalledTimes(2); hook.unmount();
  });
});
describe('source settings', () => {
  it('preserves dirty edits/key on updates and sends CAS with editable fields only', async () => {
    const api = new RuntimeApi(); vi.spyOn(api, 'marketConfig').mockResolvedValue(config);
    const saved = vi.spyOn(api, 'saveMarketConfig').mockResolvedValue({ ...config, name: 'Own source', revision: 4 }), onMarket = vi.fn();
    const view = render(<SourceSettings api={api} market={snapshot()} onMarket={onMarket} />);
    fireEvent.click(screen.getByRole('button', { name: '行情源设置' })); await screen.findByDisplayValue('My source');
    const input = screen.getByLabelText('来源名称'), secret = screen.getByLabelText(/API Key/);
    fireEvent.change(input, { target: { value: 'Own source' } }); fireEvent.change(secret, { target: { value: 'secret-for-test' } });
    view.rerender(<SourceSettings api={api} market={snapshot(1, 9)} onMarket={onMarket} />);
    expect((input as HTMLInputElement).value).toBe('Own source'); expect((secret as HTMLInputElement).value).toBe('secret-for-test');
    expect((screen.getByRole('button', { name: '测试已保存连接' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '保存行情源' })); await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(saved.mock.calls[0][0]).toEqual({ provider: config.provider, name: 'Own source', rest_url: config.rest_url, ws_url: '', auth_scheme: 'bearer', enabled: true, local_mode: false, poll_seconds: 5, timeout_seconds: 8, expected_revision: 3, api_key: 'secret-for-test' });
    await waitFor(() => expect((secret as HTMLInputElement).value).toBe('')); expect(onMarket.mock.calls[0][0]).toMatchObject({ source_revision: 4, quotes: [], books: {}, trades: {}, candles: {} });
  });
  it('omits blank key, retains CAS-conflict draft, and separately resets official', async () => {
    const api = new RuntimeApi(); vi.spyOn(api, 'marketConfig').mockResolvedValue(config);
    const saved = vi.spyOn(api, 'saveMarketConfig').mockRejectedValueOnce(new ApiError(409, 'REVISION_CONFLICT', '配置已变化，请重新读取。')).mockResolvedValueOnce({ ...config, provider: 'binance', revision: 4, has_api_key: false });
    render(<SourceSettings api={api} market={snapshot()} />); fireEvent.click(screen.getByRole('button', { name: '行情源设置' })); await screen.findByDisplayValue('My source');
    fireEvent.change(screen.getByLabelText('来源名称'), { target: { value: 'Draft survives' } }); fireEvent.click(screen.getByRole('button', { name: '保存行情源' })); await screen.findByRole('alert');
    expect(saved.mock.calls[0][0]).not.toHaveProperty('api_key'); expect((screen.getByLabelText('来源名称') as HTMLInputElement).value).toBe('Draft survives');
    fireEvent.click(screen.getByRole('button', { name: '恢复 Binance 官方' })); await waitFor(() => expect(saved).toHaveBeenCalledTimes(2));
    expect(saved.mock.calls[1][0]).toMatchObject({ provider: 'binance', expected_revision: 3, clear_api_key: true, rest_url: 'https://data-api.binance.vision', auth_scheme: 'none', local_mode: false });
  });
});
describe('real candle terminal', () => {
  it('keeps displayed quote/stat precision compact without rounding tiny prices to zero', () => {
    expect(marketPrice('67933.14402548')).toBe('67,933.14');
    expect(marketPrice('123.123456789')).toBe('123.1235');
    expect(marketPrice('0.00000000012345678')).toBe('0.000000000123457');
    expect(marketVolume('2500123456.123456')).toBe('2.5B');
  });
  it('uses measured mobile SVG coordinates so price and time labels retain pixel size', () => {
    vi.stubGlobal('ResizeObserver', class {
      constructor(private callback: (entries: { contentRect: { width: number } }[]) => void) {}
      observe() { this.callback([{ contentRect: { width: 320 } }]); }
      disconnect() {}
    });
    render(<CandleChart candles={[candle]} symbol="BTCUSDT" interval="1m" loading={false} error="" />);
    expect(screen.getByRole('img').getAttribute('viewBox')).toBe('0 0 320 320');
    expect((screen.getByRole('img') as unknown as SVGElement).style.height).toBe('320px');
  });
  it('places one source control beside refresh and retains the inline dirty form during ticks', async () => {
    const api = new RuntimeApi(); vi.spyOn(api, 'marketConfig').mockResolvedValue(config);
    vi.spyOn(api, 'marketCandles').mockResolvedValue({ symbol: 'BTCUSDT', interval: '15m', source_revision: 1, source: 'TEST', candles: [] });
    const market = snapshot(); market.quotes[0].price = '67933.14402548';
    const view = render(<MarketTerminal api={api} market={market} refreshing={false} onRefresh={vi.fn()} />);
    const controls = screen.getAllByRole('button', { name: '行情源设置' }); expect(controls).toHaveLength(1);
    expect(controls[0].closest('header')).toBe(screen.getByRole('button', { name: '刷新行情' }).closest('header'));
    expect(view.container.querySelector('.watch-symbol strong')?.textContent).toBe('67,933.14');
    expect(view.container.querySelector('.watch-symbol strong')?.getAttribute('title')).toBe('67933.14402548');
    fireEvent.click(controls[0]); const input = await screen.findByDisplayValue('My source');
    input.focus(); fireEvent.change(input, { target: { value: 'My dirty source' } });
    view.rerender(<MarketTerminal api={api} market={snapshot(1, 2)} refreshing={false} onRefresh={vi.fn()} />);
    expect(document.activeElement).toBe(input); expect((input as HTMLInputElement).value).toBe('My dirty source');
    expect(view.container.querySelector('.market-source-body')!.compareDocumentPosition(view.container.querySelector('.terminal-watchlist')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
  it('reads the actual hovered candle and supports keyboard candle navigation', () => {
    const previous = { ...candle, time: candle.time - 60_000, open: '97', close: '98' };
    const view = render(<CandleChart candles={[previous, candle]} symbol="BTCUSDT" interval="1m" loading={false} error="" />);
    expect(screen.getByText('105', { selector: '.candle-readout b' })).toBeDefined();
    fireEvent.pointerEnter(view.container.querySelector('.candle-target')!);
    expect(screen.getByText('98', { selector: '.candle-readout b' })).toBeDefined();
    fireEvent.keyDown(screen.getByRole('img'), { key: 'ArrowRight' });
    expect(screen.getByText('105', { selector: '.candle-readout b' })).toBeDefined();
  });
  it('rejects invalid OHLC and never fabricates missing candles', () => {
    expect(validCandles([candle, { ...candle, time: candle.time + 1, high: '90' }, { ...candle, time: candle.time + 2, volume: '-1' }])).toEqual([candle]);
    render(<CandleChart candles={[]} symbol="BTCUSDT" interval="1m" loading={false} error="" />); expect(screen.queryByRole('img')).toBeNull(); expect(screen.getByText('暂无 K 线数据')).toBeDefined();
  });
  it('switches symbol/interval and rejects late history from an old source', async () => {
    const api = new RuntimeApi(), first = deferred<CandleHistory>();
    const history = vi.spyOn(api, 'marketCandles').mockReturnValueOnce(first.promise).mockImplementation(async (symbol, interval) => ({ symbol, interval, source_revision: 2, source: 'NEW', candles: [{ ...candle, close: '108' }] }));
    const view = render(<MarketTerminal api={api} market={snapshot()} refreshing={false} onRefresh={vi.fn()} />); expect(history.mock.calls[0].slice(0, 2)).toEqual(['BTCUSDT', '15m']);
    view.rerender(<MarketTerminal api={api} market={snapshot(2)} refreshing={false} onRefresh={vi.fn()} />); await screen.findByRole('img', { name: /BTCUSDT 15m/ });
    await act(async () => { first.resolve({ symbol: 'BTCUSDT', interval: '15m', source_revision: 1, source: 'OLD', candles: [{ ...candle, close: '101' }] }); }); expect(screen.queryByText('101', { selector: '.candle-readout b' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '1h', exact: true })); await screen.findByRole('img', { name: /BTCUSDT 1h/ });
    fireEvent.click(screen.getByRole('button', { name: /ETH.*USDT.*暂不可用/ })); await screen.findByRole('img', { name: /ETHUSDT 1h/ }); expect(history.mock.calls.at(-1)!.slice(0, 2)).toEqual(['ETHUSDT', '1h']);
  });
  it('retains completed candles actually observed between historical refreshes', async () => {
    const api = new RuntimeApi(); vi.spyOn(api, 'marketCandles').mockResolvedValue({ symbol: 'BTCUSDT', interval: '15m', source_revision: 1, source: 'TEST', candles: [candle] });
    const market = { ...snapshot(), candles: { BTCUSDT: { '15m': candle } } };
    const view = render(<MarketTerminal api={api} market={market} refreshing={false} onRefresh={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('img').getAttribute('aria-label')).toContain('1 根'));
    for (let step = 1; step <= 2; step++) {
      view.rerender(<MarketTerminal api={api} market={{ ...market, sequence: step + 1, candles: { BTCUSDT: { '15m': { ...candle, time: candle.time + step * 900_000 } } } }} refreshing={false} onRefresh={vi.fn()} />);
    }
    await waitFor(() => expect(screen.getByRole('img').getAttribute('aria-label')).toContain('3 根'));
  });
  it('prefers genuine stream candle over slower history for that same candle', async () => {
    const api = new RuntimeApi(), pending = deferred<CandleHistory>(); vi.spyOn(api, 'marketCandles').mockReturnValue(pending.promise);
    const market = { ...snapshot(), candles: { BTCUSDT: { '15m': { ...candle, close: '109' } } } };
    render(<MarketTerminal api={api} market={market} refreshing={false} onRefresh={vi.fn()} />);
    await act(async () => pending.resolve({ symbol: 'BTCUSDT', interval: '15m', source_revision: 1, source: 'TEST', candles: [{ ...candle, close: '102' }] }));
    expect(screen.getByText('109', { selector: '.candle-readout b' })).toBeDefined(); expect(screen.queryByText('102', { selector: '.candle-readout b' })).toBeNull(); expect(screen.getByRole('img').getAttribute('aria-label')).toContain('1 根');
  });
});
