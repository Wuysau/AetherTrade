import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, ChartNoAxesCombined, ChevronRight, CircleHelp, CircleOff, Info, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { chartObservations, chartTimeLabel, money, percent, time, tone } from './format';
import type { Dashboard, Range } from './types';

export function Brand() {
  return <div className="brand"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 30 30"><path fill="currentColor" d="M3 24 13 5h5L8 24zM15 22 24 5h3L18 22zM12 24h11v2H12z" /></svg></span><div><span className="brand-name">AETHERTRADE</span><span className="brand-label">AUTONOMOUS TRADING</span></div></div>;
}
export function Badge({ children, kind = '' }: { children: ReactNode; kind?: 'pass' | 'fail' | 'wait' | '' }) { return <span className={`status ${kind}`}>{children}</span>; }
export function Alert({ children, error = false, onRetry }: { children: ReactNode; error?: boolean; onRetry?: () => void }) {
  return <div className={`inline-alert ${error ? 'negative-alert' : ''}`} role={error ? 'alert' : 'status'}><Info className="icon" /><span>{children}</span>{onRetry && <button className="button small" onClick={onRetry}><RefreshCw className="icon" />重试</button>}</div>;
}
export function Empty({ title, text, children, compact = false }: { title: string; text: string; children?: ReactNode; compact?: boolean }) {
  return <div className={`empty-state ${compact ? 'compact' : ''}`}><CircleOff className="empty-icon" aria-hidden="true" /><h3>{title}</h3><p>{text}</p>{children}</div>;
}
export function Loading({ text = '正在同步账户与服务状态…' }: { text?: string }) { return <div className="loading-state" role="status"><LoaderCircle className="icon spin" /><span>{text}</span></div>; }
export function Panel({ title, caption, action, children, className = '' }: { title: string; caption?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-header"><div><h2 className="section-label">{title}</h2>{caption && <p className="section-caption">{caption}</p>}</div>{action}</div>{children}</section>;
}
export function Intro({ title, subtitle, children }: { title: string; subtitle: string; children?: ReactNode }) {
  return <div className="page-intro"><div><h1 className="page-title" tabIndex={-1} id="page-heading">{title}</h1><p className="page-subtitle">{subtitle}</p></div><div className="intro-actions">{children}</div></div>;
}
export function Metric({ label, value, foot, color = '' }: { label: string; value: ReactNode; foot: ReactNode; color?: string }) {
  return <div className="stat"><div className="stat-name">{label}</div><div className={`stat-value ${color}`}>{value}</div><div className="stat-foot">{foot}</div></div>;
}
export function Ranges({ range, onChange }: { range: Range; onChange: (range: Range) => void }) {
  return <div className="segment" aria-label="权益曲线时间范围">{(['24H', '7D', '30D', '90D'] as Range[]).map(value => <button key={value} type="button" className={range === value ? 'selected' : ''} aria-pressed={range === value} onClick={() => onChange(value)}>{value}</button>)}</div>;
}
export function EquityChart({ data, range, onRange, research = false, loading = false }: { data: Dashboard; range: Range; onRange: (value: Range) => void; research?: boolean; loading?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(650);
  const id = useId().replace(/:/g, '');
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(entries => setWidth(Math.max(240, entries[0].contentRect.width)));
    observer.observe(ref.current); return () => observer.disconnect();
  }, []);
  const unique = chartObservations(data.history);
  const values = unique.map(p => Number(p.equity));
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const padding = (max - min) * .16 || Math.max(Math.abs(min) * .001, 1);
  const lo = min - padding, hi = max + padding;
  const left = 9, right = width < 400 ? 66 : 76, top = 16, bottom = 27, h = 242, plotW = width - left - right;
  const start = unique.length ? Date.parse(unique[0].at) : 0;
  const end = unique.length ? Date.parse(unique[unique.length - 1].at) : 1;
  const x = (i: number) => left + (Date.parse(unique[i].at) - start) / (end - start || 1) * plotW;
  const y = (n: number) => top + (hi - n) / (hi - lo) * (h - top - bottom);
  const path = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
  const hasHistory = values.length >= 2;
  const dateLabel = (at: string) => chartTimeLabel(at, end - start);
  return <Panel title={research ? '模拟账户净值路径' : '账户权益曲线'} caption="净值表现 · 计入手续费与浮动盈亏 · USDT" action={<Ranges range={range} onChange={onRange} />} className="chart-panel">
    <div className="panel-body"><div className="chart-wrap" ref={ref} aria-busy={loading}>
      {hasHistory ? <svg className="chart-svg" viewBox={`0 0 ${width} ${h}`} role="img" aria-label={`${range} 账户权益曲线，共 ${values.length} 个真实记录点；最新 ${money(values[values.length - 1])}`}>
        <defs><linearGradient id={`area-${id}`} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#5486a7" stopOpacity=".25" /><stop offset="100%" stopColor="#5486a7" stopOpacity="0" /></linearGradient></defs>
        {[0, 1, 2, 3].map(i => <g key={i}><line className="chart-grid" x1={left} x2={width - right + 5} y1={top + i * (h - top - bottom) / 3} y2={top + i * (h - top - bottom) / 3} /><text className="axis" x={width - 3} y={top + i * (h - top - bottom) / 3 + 3} textAnchor="end">{(hi - i * (hi - lo) / 3).toLocaleString('en-US', { maximumFractionDigits: 0 })}</text></g>)}
        <path d={`${path} L${x(values.length - 1)},${h - bottom} L${left},${h - bottom} Z`} fill={`url(#area-${id})`} /><path className="chart-line" d={path} /><circle className="chart-point" cx={x(values.length - 1)} cy={y(values[values.length - 1])} r="4" />
        {[0, .25, .5, .75, 1].map((fraction, i) => <text key={i} className="axis" x={left + fraction * plotW} y={h - 3} textAnchor={i === 0 ? 'start' : i === 4 ? 'end' : 'middle'}>{dateLabel(new Date(start + fraction * (end - start)).toISOString())}</text>)}
      </svg> : <div className="chart-empty"><ChartNoAxesCombined className="empty-icon" /><strong>等待权益记录积累</strong><span>已记录 {values.length} 个观察点。后续同步后将显示实际净值变化。</span></div>}
      {loading && <span className="chart-updating"><LoaderCircle className="icon sm spin" />同步中</span>}
    </div><div className="chart-metrics"><div className="chart-metric"><span>账户总收益率</span><strong className={tone(data.research.total_return_pct)}>{percent(data.research.total_return_pct, true)}</strong></div><div className="chart-metric"><span>账户最大回撤</span><strong>{percent(data.research.max_drawdown_pct)}</strong></div><div className="chart-metric"><span>已平仓胜率</span><strong>{percent(data.research.win_rate)}</strong></div></div><p className="chart-data-note">曲线范围 {range} · 统计按账户完整记录计算{data.research.win_rate === null ? ' · 暂无已平仓样本' : ''}</p></div>
  </Panel>;
}
export function Timeline({ data, limit = 4 }: { data: Dashboard; limit?: number }) {
  return <Panel title="最近执行记录" caption="设置变更、风控操作与模拟成交均保留回执" action={<Badge>持久化记录</Badge>}>
    {data.events.length ? <div className="timeline">{data.events.slice(0, limit).map(event => <div className="timeline-item" key={event.id}><span className="timeline-bullet" /><div className="timeline-content"><div className="timeline-top"><span className="timeline-title">{event.title}</span><time className="timeline-time" dateTime={event.created_at}>{time(event.created_at)}</time></div><div className="timeline-detail">{event.detail}</div></div></div>)}</div> : <Empty title="尚无执行记录" text="保存配置或完成模拟操作后，回执会出现在这里。" compact />}
  </Panel>;
}
export function Dialog({ title, children, onClose, busy = false, wide = false }: { title: string; children: ReactNode; onClose: () => void; busy?: boolean; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.showModal();
    return () => { ref.current?.close(); previous?.focus(); };
  }, []);
  return <dialog ref={ref} className={wide ? 'wide-dialog' : ''} aria-labelledby={titleId} onCancel={event => { event.preventDefault(); if (!busy) closeRef.current(); }} onClick={event => { if (event.target === event.currentTarget && !busy) { const rect = event.currentTarget.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeRef.current(); } }}>
    <div className="dialog-inner"><div className="dialog-heading"><h2 id={titleId}>{title}</h2><button className="top-icon" aria-label="关闭对话框" onClick={onClose} disabled={busy}><X className="icon" /></button></div>{children}</div>
  </dialog>;
}
export function DetailsLink({ children, onClick }: { children: ReactNode; onClick: () => void }) { return <button className="button link" onClick={onClick}>{children}<ArrowRight className="icon" /></button>; }
export { ArrowRight, ChevronRight, CircleHelp };
