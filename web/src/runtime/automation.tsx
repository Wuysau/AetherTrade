import { useState } from 'react';
import { errorMessage, type RuntimeApi } from './api';
import { time } from './format';
import { Alert, Badge, Empty, Panel } from './ui';
import type { Dashboard, Strategy } from './types';

export const runtimeLabel = (s: Strategy) => ({ DRAFT: '草稿 · 未启用', PAUSED: '已暂停', WAITING_MODEL: '等待模型服务', WAITING_QUOTES: '等待有效行情', READY: '等待分析', RUNNING: '正在分析', ERROR: '分析失败', ACTIVE: '已启用' }[s.runtime_status ?? s.status] ?? s.runtime_status);
export const executionLabel = (status?: string) => ({ FILLED: 'PAPER 已成交', BLOCKED: 'PAPER 风控拦截', NOT_ENABLED: '自动下单未启用', NO_ACTION: '观望 · 无需下单', HOLD: '观望 · 无需下单', SKIPPED: '已跳过执行', PAUSED: '已暂停', ERROR: '执行失败' }[status ?? ''] ?? '未执行订单');
export function StrategyAutomation({ strategy: s, api, onSaved }: { strategy: Strategy; api: RuntimeApi; onSaved: (message: string) => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function update(status: Strategy['status'], auto = s.auto_execute ?? false) {
    setBusy(true); setError('');
    try { await api.saveStrategy({ name: s.name, symbol: s.symbol, mode: s.mode, budget: s.budget, target_pct: s.target_pct, analysis_interval_seconds: s.analysis_interval_seconds ?? 300, auto_execute: auto, ...(status === 'ACTIVE' && s.status !== 'ACTIVE' ? { acknowledge_model_cost: true } : {}) }, s, status); onSaved(status !== 'ACTIVE' ? '策略未运行，自动下单权限已更新。' : auto ? '自动下单已启用，仅限后续周期的本地 PAPER 模拟成交。' : '自动分析已启用；自动下单已关闭。'); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  if (s.mode !== 'AI') return <span className="quiet">仅保存配置</span>;
  return <div className="automation-controls"><div className="action-strip"><Badge kind={s.status === 'ACTIVE' ? 'pass' : 'wait'}>{runtimeLabel(s)}</Badge><button className="button small" disabled={busy} onClick={() => void update(s.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE')}>{s.status === 'ACTIVE' ? '暂停分析' : '启用自动分析'}</button></div><label className="model-check"><input type="checkbox" disabled={busy} checked={s.auto_execute ?? false} onChange={e => void update(e.target.checked ? 'ACTIVE' : s.status, e.target.checked)} />自动下单（PAPER）<span className="sr-only"> · {s.name}</span></label><p className="field-help">每 {s.analysis_interval_seconds ?? 300} 秒分析 · 下次 {s.status === 'ACTIVE' ? s.next_analysis_at ? time(s.next_analysis_at, true) : '等待调度' : '未安排'}</p>{s.last_analysis_at && <p className="field-help">最近分析 {time(s.last_analysis_at, true)}</p>}{s.last_analysis_error && <Alert error>{s.last_analysis_error}</Alert>}{error && <Alert error>{error}</Alert>}</div>;
}
export function AutomationPanel({ data, api, onSaved, onCreate, onEdit }: { data: Dashboard; api: RuntimeApi; onSaved: (m: string) => void; onCreate: () => void; onEdit: (s: Strategy) => void }) {
  const strategies = data.strategies.filter(s => s.mode === 'AI');
  return <Panel title="AI 策略自动分析" caption="启用策略后定期分析，结果通过右上角铃铛通知" className="automation-panel" action={<button className="button" onClick={onCreate}>创建 AI 策略</button>}><div className="panel-body"><p className="field-help">启用或勾选自动下单，即同意按策略间隔持续调用模型，可能产生 API 费用。取消自动下单后分析继续；暂停策略停止后续分析。自动订单只使用本地 PAPER 虚拟资金，并逐笔校验风控。</p>{data.automation?.enabled === false && <Alert>服务端自动分析当前已关闭，已保存的策略权限保留。</Alert>}{strategies.length ? strategies.map(s => <div className="automation-row" key={s.id}><div><h3>{s.name}</h3><p className="field-help">{s.symbol} · 预算 {s.budget} USDT · 目标 {s.target_pct}%</p><button className="button link" onClick={() => onEdit(s)}>编辑间隔与预算</button>{data.decisions.find(d => d.strategy_id === s.id) && <p className="field-help">最近结果：{executionLabel(data.decisions.find(d => d.strategy_id === s.id)?.execution_status)}</p>}</div><StrategyAutomation strategy={s} api={api} onSaved={onSaved} /></div>) : <Empty compact title="尚未启用 AI 策略" text="创建策略并显式启用后开始自动分析；已有草稿不会自行启动。" />}</div></Panel>;
}
