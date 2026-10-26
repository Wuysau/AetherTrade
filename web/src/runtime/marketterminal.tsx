import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { ChevronDown, ChevronUp, RefreshCw, Settings2, TrendingDown, TrendingUp } from 'lucide-react';
import { errorMessage, type RuntimeApi } from './api';
import { chartTimeLabel, finite, marketPrice, marketVolume, percent, quantity, quoteIsFresh, time, tone } from './format';
import { INSTRUMENTS } from './instruments';
import { Alert, Badge, Empty } from './ui';
import type { BrowserMarketStatus, Candle, CandleInterval, Instrument, Market, MarketConfig, MarketFields, MarketInput, SymbolName } from './types';

export const INTERVALS: CandleInterval[] = ['1m', '5m', '15m', '1h', '4h', '1d'];
const OFFICIAL: MarketFields = { provider: 'binance', name: 'Binance 官方行情', rest_url: 'https://data-api.binance.vision', ws_url: 'wss://data-stream.binance.vision/stream', auth_scheme: 'none', local_mode: false, enabled: true, poll_seconds: 5, timeout_seconds: 8 };
export function marketTransportLabel(market: Market) {
  return ({ CONNECTING: '行情连接中', LIVE: '实时行情', RECONNECTING: '行情重连中', POLLING: 'REST 轮询', DISABLED: '行情已停用' } as Record<string, string>)[market.transport?.status ?? ''] ?? (market.status === 'CONNECTED' ? '行情已连接' : market.status === 'PARTIAL' ? '部分行情可用' : market.status === 'STALE' ? '缓存已过期' : '行情不可用');
}
export function SourceSettings({ api, market, onMarket, expanded, hideToggle = false }: { api: RuntimeApi; market: Market; onMarket?: (market: Market) => void; expanded?: boolean; hideToggle?: boolean }) {
  const [localOpen, setOpen] = useState(false), [config, setConfig] = useState<MarketConfig>(), [draft, setDraft] = useState<MarketFields>();
  const [key, setKey] = useState(''), [clearKey, setClearKey] = useState(false), [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('');
  const open = expanded ?? localOpen;
  const alive = useRef(true);
  useEffect(() => { if (open && !config) void load(); }, [open]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  async function load(replace = true) {
    if (!api.marketConfig) return;
    setBusy(true); setError('');
    try { const next = await api.marketConfig(); if (alive.current) { setConfig(next); if (replace) { setDraft(fields(next)); setDirty(false); setKey(''); setClearKey(false); } } }
    catch (err) { if (alive.current) setError(errorMessage(err)); } finally { if (alive.current) setBusy(false); }
  }
  function fields(value: MarketFields): MarketFields { const { provider, name, rest_url, ws_url, auth_scheme, local_mode, enabled, poll_seconds, timeout_seconds } = value; return { provider, name, rest_url, ws_url, auth_scheme, local_mode, enabled, poll_seconds, timeout_seconds }; }
  function edit(values: Partial<MarketFields>) { setDraft(previous => previous ? { ...previous, ...values } : previous); setDirty(true); setMessage(''); }
  async function save(official = false) {
    if (!config || !draft || !api.saveMarketConfig) return;
    setBusy(true); setError(''); setMessage('');
    const values: MarketInput = { ...(official ? { ...OFFICIAL, enabled: draft.enabled, poll_seconds: draft.poll_seconds, timeout_seconds: draft.timeout_seconds } : draft), expected_revision: config.revision, ...(official || draft.provider === 'binance' || clearKey ? { clear_api_key: true } : key ? { api_key: key } : {}) };
    try {
      const next = await api.saveMarketConfig(values);
      if (!alive.current) return;
      setConfig(next); setDraft(fields(next)); setKey(''); setClearKey(false); setDirty(false); setMessage('行情源已保存，正在读取新来源。');
      onMarket?.({ source: next.name, source_revision: next.revision, instance_id: market.instance_id, sequence: -1, status: 'UNAVAILABLE', quotes: [], books: {}, trades: {}, candles: {}, groups: [], last_updated: null, error: null, transport: { mode: next.enabled ? 'polling' : 'disabled', status: next.enabled ? 'CONNECTING' : 'DISABLED', last_event_at: null, last_error: null, reconnect_attempt: 0 } });
    } catch (err) { if (alive.current) setError(errorMessage(err)); } finally { if (alive.current) setBusy(false); }
  }
  async function test() {
    if (!config || !api.testMarketConfig) return;
    setBusy(true); setError(''); setMessage('');
    try { const result = await api.testMarketConfig(config.revision); if (alive.current) { setMessage(`${result.message} · ${result.quote_count} 个标的`); if (!result.ok) setError('连接测试未通过，请检查已保存的地址、协议与密钥。'); } }
    catch (err) { if (alive.current) setError(errorMessage(err)); } finally { if (alive.current) setBusy(false); }
  }
  function submit(event: FormEvent) { event.preventDefault(); void save(); }
  return <div className={`market-settings ${hideToggle ? 'market-settings-inline' : ''}`} hidden={hideToggle && !open}>{!hideToggle && <button className="button small" aria-expanded={open} aria-controls="market-source-settings" onClick={() => setOpen(!open)}><Settings2 className="icon" />行情源设置{open ? <ChevronUp className="icon sm" /> : <ChevronDown className="icon sm" />}</button>}{open && <div id="market-source-settings" className="market-source-body"><div className="source-heading"><div><h3>行情数据源</h3><p>公开行情无需密钥。自定义地址由本地服务读取，浏览器持续接收行情。</p></div>{config && <span className="quiet">已保存版本 {config.revision}{dirty ? ' · 有未保存更改' : ''}</span>}</div>{draft && config ? <form onSubmit={submit} className="editor-form"><div className="market-source-grid"><label className="field"><span>接口协议</span><select value={draft.provider} disabled={busy} onChange={e => edit(e.target.value === 'binance' ? OFFICIAL : { provider: e.target.value as MarketFields['provider'] })}><option value="binance">Binance 官方公开行情</option><option value="binance_compatible">Binance 兼容接口</option><option value="standard_json">标准 JSON 接口</option></select></label><label className="field"><span>来源名称</span><input value={draft.name} required maxLength={80} disabled={busy || draft.provider === 'binance'} onChange={e => edit({ name: e.target.value })} /></label><label className="field source-wide"><span>REST 基础地址</span><input value={draft.rest_url} type="url" required disabled={busy || draft.provider === 'binance'} onChange={e => edit({ rest_url: e.target.value })} placeholder="https://market.example.com" /></label><label className="field source-wide"><span>WebSocket 地址 <small>可选，留空使用 REST 轮询</small></span><input value={draft.ws_url} type="url" disabled={busy || draft.provider === 'binance'} onChange={e => edit({ ws_url: e.target.value })} placeholder="wss://market.example.com/stream" /></label><label className="field"><span>认证方式</span><select value={draft.auth_scheme} disabled={busy || draft.provider === 'binance'} onChange={e => edit({ auth_scheme: e.target.value as MarketFields['auth_scheme'] })}><option value="none">无需认证</option><option value="bearer">Bearer token</option><option value="x_api_key">X-API-Key</option></select></label><label className="field"><span>API Key <small>可选 · 只写入，不回显</small></span><input type="password" autoComplete="new-password" value={key} disabled={busy || clearKey || draft.provider === 'binance'} onChange={e => { setKey(e.target.value); setDirty(true); }} placeholder={config.has_api_key ? '已保存，留空保留现有密钥' : '公开接口可留空'} /><span className="field-help">{config.has_api_key ? config.key_available ? '密钥已加密保存。修改地址或认证方式时须重新输入或清除。' : '已保存的密钥当前无法读取，请重新输入。' : '密钥只用于此行情来源。'}</span></label><label className="field"><span>轮询间隔（秒）</span><input type="number" min={2} max={60} required value={draft.poll_seconds} disabled={busy} onChange={e => edit({ poll_seconds: Number(e.target.value) })} /></label><label className="field"><span>请求超时（秒）</span><input type="number" min={2} max={20} required value={draft.timeout_seconds} disabled={busy} onChange={e => edit({ timeout_seconds: Number(e.target.value) })} /></label></div><div className="market-source-checks"><label><input type="checkbox" checked={draft.enabled} disabled={busy} onChange={e => edit({ enabled: e.target.checked })} />启用行情</label><label><input type="checkbox" checked={draft.local_mode} disabled={busy || draft.provider === 'binance'} onChange={e => edit({ local_mode: e.target.checked })} />允许本机 HTTP / WS</label>{config.has_api_key && <label><input type="checkbox" checked={clearKey} disabled={busy} onChange={e => { setClearKey(e.target.checked); setKey(''); setDirty(true); }} />清除已保存密钥</label>}</div>{draft.provider === 'standard_json' && <p className="field-help">标准 JSON：REST 提供 /snapshot 与 /candles，WebSocket 可发送相同的行情对象。未提供的盘口或成交将显示为不可用。</p>}{error && <Alert error>{error}</Alert>}{message && <p role="status" className="field-help">{message}</p>}<div className="action-strip"><button className="button primary" disabled={busy || !dirty}>{busy ? '正在处理…' : '保存行情源'}</button><button type="button" className="button" disabled={busy || dirty} onClick={() => void test()}>测试已保存连接</button><button type="button" className="button" disabled={busy} onClick={() => void save(true)}>恢复 Binance 官方</button><button type="button" className="button link" disabled={busy} onClick={() => void load()}>重新读取配置</button>{dirty && <span className="quiet">保存后可测试连接</span>}</div></form> : error ? <Alert error onRetry={() => void load()}>{error}</Alert> : <p className="quiet">正在读取行情源配置…</p>}</div>}</div>;
}

export function validCandles(values: Candle[]): Candle[] {
  const unique = new Map<number, Candle>();
  for (const candle of values) {
    const nums = [candle.open, candle.high, candle.low, candle.close, candle.volume].map(finite);
    if (!Number.isFinite(candle.time) || candle.time <= 0 || nums.some(value => value === null) || nums.slice(0, 4).some(value => value! <= 0) || nums[4]! < 0 || nums[1]! < Math.max(nums[0]!, nums[3]!) || nums[2]! > Math.min(nums[0]!, nums[3]!) || nums[2]! > nums[1]!) continue;
    unique.set(candle.time, candle);
  }
  return [...unique.values()].sort((a, b) => a.time - b.time).slice(-200);
}
export function CandleChart({ candles, symbol, interval, loading, error }: { candles: Candle[]; symbol: string; interval: CandleInterval; loading: boolean; error: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  useEffect(() => {
    if (!chartRef.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(entries => {
      const measured = entries[0]?.contentRect.width;
      if (measured) setWidth(Math.max(240, Math.floor(measured)));
    });
    observer.observe(chartRef.current); return () => observer.disconnect();
  }, [Boolean(candles.length)]);
  useEffect(() => setHover(null), [symbol, interval, candles.length]);
  if (!candles.length) return <div className="candle-empty"><Empty compact title={loading ? '正在读取 K 线' : '暂无 K 线数据'} text={error || '此来源尚未提供该标的与周期的真实 OHLC 数据。'} /></div>;
  const compact = width < 500, height = compact ? 320 : 374;
  const left = 12, right = compact ? 76 : 90, top = 18, bottom = compact ? 238 : 270, volumeY = compact ? 258 : 292, volumeH = compact ? 38 : 46;
  const low = Math.min(...candles.map(c => Number(c.low))), high = Math.max(...candles.map(c => Number(c.high))), pad = Math.max((high - low) * .08, high * .0001), min = low - pad, max = high + pad;
  const y = (value: string | number) => top + (max - Number(value)) / (max - min) * (bottom - top);
  const step = (width - left - right) / candles.length, x = (i: number) => left + step * (i + .5), bodyW = Math.max(1, Math.min(10, step * .65));
  const maxVolume = Math.max(...candles.map(c => Number(c.volume)), 1), active = candles[hover ?? candles.length - 1];
  const span = candles.at(-1)!.time - candles[0].time;
  return <div className="candle-display" ref={chartRef}><div className="candle-readout" aria-live="off"><span>{time(new Date(active.time).toISOString(), true)} UTC+8</span>{(['open', 'high', 'low', 'close'] as const).map((field, index) => <span key={field}>{['开', '高', '低', '收'][index]} <b>{quantity(active[field])}</b></span>)}<span>量 <b>{quantity(active.volume)}</b></span></div><svg className="candle-chart" viewBox={`0 0 ${width} ${height}`} style={{ height }} role="img" tabIndex={0} onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); setHover(index => Math.max(0, Math.min(candles.length - 1, (index ?? candles.length - 1) + (event.key === 'ArrowLeft' ? -1 : 1)))); } }} aria-label={`${symbol} ${interval} 真实 OHLC K 线，${candles.length} 根`} onPointerLeave={() => setHover(null)}>{Array.from({ length: 5 }, (_, i) => { const value = max - (max - min) * i / 4; return <g key={i} className="candle-grid"><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} /><text x={width - right + 12} y={y(value) + 4}>{marketPrice(value)}</text></g>; })}<line className="volume-divider" x1={left} x2={width - right} y1={volumeY - 9} y2={volumeY - 9} />{candles.map((c, i) => <g key={c.time} className={Number(c.close) >= Number(c.open) ? 'candle-up' : 'candle-down'}><line x1={x(i)} x2={x(i)} y1={y(c.high)} y2={y(c.low)} /><rect x={x(i) - bodyW / 2} y={Math.min(y(c.open), y(c.close))} width={bodyW} height={Math.max(1, Math.abs(y(c.open) - y(c.close)))} /><rect className="candle-volume" x={x(i) - bodyW / 2} y={volumeY + volumeH * (1 - Number(c.volume) / maxVolume)} width={bodyW} height={Math.max(0, volumeH * Number(c.volume) / maxVolume)} /></g>)}{[0, Math.floor((candles.length - 1) / 2), candles.length - 1].filter((v, i, a) => a.indexOf(v) === i).map(i => <text className="candle-time" key={i} x={x(i)} y={height - 12} textAnchor={i === 0 ? 'start' : i === candles.length - 1 ? 'end' : 'middle'}>{chartTimeLabel(new Date(candles[i].time).toISOString(), span)}</text>)}{hover !== null && <line className="candle-crosshair" x1={x(hover)} x2={x(hover)} y1={top} y2={volumeY + volumeH} />}{candles.map((c, i) => <rect key={`hover-${c.time}`} className="candle-target" x={left + i * step} y={top} width={step} height={volumeY + volumeH - top} onPointerEnter={() => setHover(i)} />)}</svg>{error && <p className="market-chart-note">{error}</p>}{loading && <span className="market-chart-note">正在更新历史数据…</span>}</div>;
}

export function MarketTerminal({ market, api, instruments = INSTRUMENTS, refreshing, onRefresh, onMarket, browserStatus = 'connecting' }: { market: Market; api: RuntimeApi; instruments?: Instrument[]; refreshing: boolean; onRefresh: () => void; onMarket?: (market: Market) => void; browserStatus?: BrowserMarketStatus }) {
  const [symbol, setSymbol] = useState<SymbolName>('BTCUSDT'), [interval, setInterval] = useState<CandleInterval>('15m');
  const [history, setHistory] = useState<{ key: string; values: Candle[] }>(), [observed, setObserved] = useState<{ key: string; values: Candle[] }>(), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [direction, setDirection] = useState(''), [settingsOpen, setSettingsOpen] = useState(false);
  const quote = market.quotes.find(q => q.symbol === symbol), fresh = quoteIsFresh(quote), book = market.books?.[symbol], trades = market.trades?.[symbol] ?? [];
  const sourceKey = `${market.instance_id ?? 'legacy'}:${market.source_revision ?? 0}`;
  const historyKey = `${sourceKey}:${symbol}:${interval}`;
  const previousPrice = useRef<{ key: string; value: number | null } | null>(null);
  useEffect(() => {
    const value = finite(quote?.price), previous = previousPrice.current;
    setDirection(previous?.key === `${sourceKey}:${symbol}` && value !== null && previous.value !== null && value !== previous.value ? value > previous.value ? 'tick-up' : 'tick-down' : '');
    previousPrice.current = { key: `${sourceKey}:${symbol}`, value };
    const timer = window.setTimeout(() => setDirection(''), 700); return () => window.clearTimeout(timer);
  }, [quote?.price, sourceKey, symbol]);
  useEffect(() => {
    const controller = new AbortController(); let disposed = false;
    setHistory(undefined); setError('');
    if (!api.marketCandles || !market.instance_id) return;
    setLoading(true);
    void api.marketCandles(symbol, interval, controller.signal).then(result => {
      if (!disposed && result.symbol === symbol && result.interval === interval && result.source_revision === market.source_revision) setHistory({ key: historyKey, values: validCandles(result.candles) });
    }).catch(err => { if (!disposed) setError(errorMessage(err)); }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; controller.abort(); };
  }, [api, historyKey, symbol, interval, market.instance_id, market.source_revision]);
  const latest = market.candles?.[symbol]?.[interval];
  useEffect(() => {
    if (latest) setObserved(previous => ({ key: historyKey, values: validCandles([...(previous?.key === historyKey ? previous.values : []), latest]) }));
  }, [latest, historyKey]);
  const candles = useMemo(() => {
    const values = history?.key === historyKey ? history.values : [];
    const actualUpdates = observed?.key === historyKey ? observed.values : [];
    // Retain genuinely observed closed candles as each new streaming candle opens.
    // Streaming values win when history completed after the corresponding event.
    return validCandles([...values, ...actualUpdates, ...(latest ? [latest] : [])]);
  }, [history, observed, historyKey, latest]);
  const browserLabel = ({ connecting: '推送连接中', streaming: '页面推送已连接', polling: '页面使用快照轮询', paused: '页面推送已暂停', unavailable: '页面连接异常' })[browserStatus];
  return <section className="panel market-terminal" aria-label="实时行情终端"><header className="terminal-header"><div><h2 className="section-label">行情终端</h2><div className="terminal-connection"><Badge kind={market.transport?.status === 'LIVE' ? 'pass' : 'wait'}>{marketTransportLabel(market)}</Badge><span>{market.source || '未连接来源'}</span><span>{browserLabel}</span>{market.status === 'PARTIAL' && <span>部分标的可用</span>}</div></div><div className="terminal-header-actions"><button className="button small" aria-expanded={settingsOpen} aria-controls="market-source-settings" onClick={() => setSettingsOpen(!settingsOpen)}><Settings2 className="icon" />行情源设置{settingsOpen ? <ChevronUp className="icon sm" /> : <ChevronDown className="icon sm" />}</button><button className="button small" onClick={onRefresh} disabled={refreshing}><RefreshCw className={`icon ${refreshing ? 'spin' : ''}`} />{refreshing ? '刷新中…' : '刷新行情'}</button></div></header><SourceSettings api={api} market={market} onMarket={onMarket} expanded={settingsOpen} hideToggle /><div className="terminal-watchlist" aria-label="选择行情标的">{instruments.map(instrument => { const q = market.quotes.find(item => item.symbol === instrument.symbol); return <button key={instrument.symbol} className={`watch-symbol ${instrument.symbol === symbol ? 'selected' : ''}`} aria-pressed={instrument.symbol === symbol} onClick={() => setSymbol(instrument.symbol)}><span>{instrument.base_asset}<small>{instrument.asset_class === 'BSTOCK' ? 'bStock' : '/ USDT'}</small></span><strong title={q?.price}>{marketPrice(q?.price)}</strong><span className={quoteIsFresh(q) ? tone(q?.change_pct) : 'quiet'}>{q ? quoteIsFresh(q) ? percent(q.change_pct, true) : '报价已过期' : '暂不可用'}</span></button>; })}</div><div className="terminal-selected"><div className="terminal-price-block"><div><strong>{symbol.replace('USDT', '')}<span> / USDT</span></strong><span className="quiet">{instruments.find(i => i.symbol === symbol)?.name}</span></div><div className={`terminal-price ${direction}`} title={quote?.price}>{marketPrice(quote?.price)}{direction === 'tick-up' ? <TrendingUp className="icon" aria-label="价格上涨" /> : direction === 'tick-down' ? <TrendingDown className="icon" aria-label="价格下跌" /> : null}</div><div className="terminal-price-caption"><span className={fresh ? tone(quote?.change_pct) : 'quiet'}>{percent(quote?.change_pct, true)}</span><span>24h</span><span>{quote ? fresh ? `${time(quote.as_of)} UTC+8` : `已过期 · ${time(quote.as_of)}` : '等待有效报价'}</span></div></div><dl className="terminal-stats"><div><dt>24h 最高</dt><dd title={quote?.high}>{marketPrice(quote?.high)}</dd></div><div><dt>24h 最低</dt><dd title={quote?.low}>{marketPrice(quote?.low)}</dd></div><div><dt>24h 成交量</dt><dd title={quote?.volume}>{marketVolume(quote?.volume)} <small>{symbol.replace('USDT', '')}</small></dd></div><div><dt>24h 成交额</dt><dd title={quote?.quote_volume}>{marketVolume(quote?.quote_volume)} <small>USDT</small></dd></div></dl></div><div className="terminal-main"><div className="terminal-chart"><div className="terminal-chart-toolbar"><span>K 线 <small>OHLC · UTC+8</small></span><div className="segment" aria-label="K 线周期">{INTERVALS.map(value => <button type="button" key={value} className={interval === value ? 'selected' : ''} aria-pressed={interval === value} onClick={() => setInterval(value)}>{value}</button>)}</div></div><CandleChart candles={candles} symbol={symbol} interval={interval} loading={loading} error={error} /></div><aside className="terminal-tape"><section className="order-book"><div className="tape-heading"><h3>五档盘口</h3><span>{book ? book.stale ? '盘口已过期' : time(book.as_of) : '暂无盘口'}</span></div><div className="tape-columns"><span>价格 USDT</span><span>数量</span></div>{book && (book.asks.length || book.bids.length) ? <><div className="book-asks">{book.asks.slice(0, 5).reverse().map(([price, size], i) => <div className="book-row" key={`ask-${i}`}><span className="negative" title={price}>{marketPrice(price)}</span><span>{quantity(size)}</span></div>)}</div><div className="book-mid"><strong className={fresh ? tone(quote?.change_pct) : ''} title={quote?.price}>{marketPrice(quote?.price)}</strong><span>{book.asks[0] && book.bids[0] ? `价差 ${quantity(String(Number(book.asks[0][0]) - Number(book.bids[0][0])))}` : '—'}</span></div><div className="book-bids">{book.bids.slice(0, 5).map(([price, size], i) => <div className="book-row" key={`bid-${i}`}><span className="positive" title={price}>{marketPrice(price)}</span><span>{quantity(size)}</span></div>)}</div></> : <p className="tape-empty">来源未提供有效买卖盘</p>}</section><section className="recent-trades"><div className="tape-heading"><h3>最近成交</h3><span>实际市场成交</span></div><div className="tape-columns three"><span>价格</span><span>数量</span><span>时间</span></div>{trades.length ? <div className="trades-scroll">{trades.slice(0, 40).map(trade => <div className="trade-row" key={trade.id}><span className={trade.side === 'BUY' ? 'positive' : 'negative'} title={trade.price}>{marketPrice(trade.price)}</span><span>{quantity(trade.quantity)}</span><time>{time(trade.as_of)}</time></div>)}</div> : <p className="tape-empty">来源未提供近期成交</p>}</section></aside></div>{(market.error || market.transport?.last_error) && <p className="terminal-error">{market.error || market.transport?.last_error}</p>}<footer className="terminal-footer"><span>只读行情 · 模拟订单独立校验报价时效</span><span>行情更新 {time(market.last_updated)} UTC+8</span></footer></section>;
}
