import { useEffect, useRef, useState, type FormEvent } from 'react';
import { RuntimeApi, errorMessage } from './api';
import { useLocale } from './i18n';
import type { ModelProfile } from './types';
import { Alert } from './ui';
import { DemoTakeoverControls } from './demo_takeover';

interface SavedStrategy {
  revision:number; grant_hash:string; mode:string; model_profile_id:string; model_revision:number;
  budget:string; max_planned_loss:string; max_daily_loss:string; fee_rate_cap:string;
  holding_period_seconds:number; authorization_seconds:number; authorized_until:number;
  authorized:boolean; allow_execution:boolean; allow_model_calls:boolean;
}
interface RuntimeStatus {
  configured: boolean; execution_authorized: boolean; blocker?: string; binding_blocker?: string;
  account_state?: string; running?: boolean; new_risk_paused?: boolean; last_error?: string | null; strategy_revision?: number; saved_strategy?: SavedStrategy | null; qualification_blocker?: string | null;
  window?: { state: string; error: string | null };
}

interface Observations {
  decisions: Array<{run_id:string; state:string; action:string | null; summary:string | null}>;
  orders: Array<{command_id:string; state:string; confirmation:string; order_id:string | null; order_status:string | null}>;
  reports: Array<{observation_id:string; source:string; received_at:number; inbox_state:string | null}>;
  fills: Array<{symbol:string; order_id:string; trade_id:string; quantity:string | null; price:string | null; confirmation:string}>;
  unresolved_trade_history?: Array<{symbol:string; order_id:string | null; trade_id:string | null; trade_hash:string}>;
  account_position_review?: {state:string; count:number; execution_authorized:false; blocker:string | null};
  pnl: {state:string; reason:string}; complete_history:boolean;
}

/** Operator actions call the real isolated Demo runtime; no route certifies it. */
export function DemoRuntimeControls({ api, connectionGeneration = "", connectionBusy = false }: { api: RuntimeApi; connectionGeneration?: string; connectionBusy?: boolean }) {
  const { locale } = useLocale(), en = locale === 'en';
  const text = (zh: string, english: string) => en ? english : zh;
  const [status, setStatus] = useState<RuntimeStatus | null>(null), [statusError, setStatusError] = useState('');
  const [models, setModels] = useState<ModelProfile[]>([]), [modelId, setModelId] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [bindAck, setBindAck] = useState(false), [costAck, setCostAck] = useState(false), [orderAck, setOrderAck] = useState(false);
  const [startCostAck, setStartCostAck] = useState(false), [startOrderAck, setStartOrderAck] = useState(false), [recoveryAck, setRecoveryAck] = useState(false);
  const [budget, setBudget] = useState('100'), [planned, setPlanned] = useState('2'), [daily, setDaily] = useState('5'), [fee, setFee] = useState('0.001');
  const [holding, setHolding] = useState('300'), [authorization, setAuthorization] = useState('300'), [duration, setDuration] = useState('60'), [interval, setIntervalSeconds] = useState('60');
  const [strategyRevision, setStrategyRevision] = useState(0);
  const [observations, setObservations] = useState<Observations | null>(null), [observationError, setObservationError] = useState('');
  const alive = useRef(true), busyRef = useRef(false), sequence = useRef(0);
  const observationSequence = useRef(0);
  useEffect(() => { ++observationSequence.current; setObservations(null); setObservationError(''); }, [connectionGeneration, connectionBusy]);
  const model = models.find(item => item.id === modelId);
  const eligible = models.filter(item => item.enabled && (item.key_available || item.local_mode));

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController(); let polling = false;
    async function refresh() {
      if (polling || busyRef.current) return;
      polling = true; const version = sequence.current;
      try {
        const value = await api.request<RuntimeStatus>('/demo/runtime/status', { signal: controller.signal });
        if (alive.current && version === sequence.current) {
          setStatus(value); setStatusError('');
          if (typeof value.strategy_revision === 'number') setStrategyRevision(value.strategy_revision);
        }
      } catch (err) {
        if (!controller.signal.aborted && alive.current && version === sequence.current) setStatusError(errorMessage(err));
      } finally { polling = false; }
    }
    void refresh();
    void api.modelConfigs().then(value => {
      if (!alive.current) return;
      const list = Array.isArray(value.profiles) ? value.profiles : [];
      setModels(list);
      setModelId(list.find(item => item.enabled && (item.key_available || item.local_mode))?.id ?? '');
    }).catch(err => { if (alive.current) setError(errorMessage(err)); });
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => { alive.current = false; ++sequence.current; controller.abort(); window.clearInterval(timer); };
  }, [api]);

  async function act(path: string, payload?: object) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(''); setNotice(''); ++sequence.current;
    try {
      const result = await api.request<{ state?: string; revision?: number }>(path,
        { method:'POST', ...(payload ? { body:JSON.stringify(payload) } : {}) }, 60_000);
      if (!alive.current) return;
      if (typeof result.revision === 'number') setStrategyRevision(result.revision);
      setNotice(result.state ?? text('操作已提交，请查看运行状态。', 'Request submitted; check runtime status.'));
      const value = await api.request<RuntimeStatus>('/demo/runtime/status');
      if (alive.current) { setStatus(value); setStatusError(''); }
    } catch (err) { if (alive.current) setError(errorMessage(err)); }
    finally { busyRef.current = false; if (alive.current) setBusy(false); }
  }
  async function readObservations() {
    setObservationError(''); const version = ++observationSequence.current;
    try {
      const value = await api.request<Observations>('/demo/runtime/observations');
      if (alive.current && version === observationSequence.current) setObservations(value);
    } catch (err) {
      if (alive.current && version === observationSequence.current) { setObservations(null); setObservationError(errorMessage(err)); }
    }
  }
  function configure(event: FormEvent) {
    event.preventDefault();
    if (!model || !costAck || !orderAck) return;
    void act('/demo/runtime/strategy', { model_profile_id:model.id, model_revision:model.revision, expected_strategy_revision:strategyRevision,
      budget, max_planned_loss:planned, max_daily_loss:daily, fee_rate_cap:fee, holding_period_seconds:Number(holding),
      authorization_seconds:Number(authorization), acknowledge_model_cost:true, acknowledge_demo_execution:true });
  }
  const saved = status?.saved_strategy;
  const dirty = Boolean(saved && (saved.model_profile_id !== modelId || saved.model_revision !== model?.revision ||
    saved.budget !== budget || saved.max_planned_loss !== planned || saved.max_daily_loss !== daily || saved.fee_rate_cap !== fee ||
    saved.holding_period_seconds !== Number(holding) || saved.authorization_seconds !== Number(authorization)));
  const validDuration = Number.isInteger(Number(duration)) && Number(duration) >= 1 && Number(duration) <= 900;
  const validInterval = Number.isInteger(Number(interval)) && Number(interval) >= 30 && Number(interval) <= 900;
  return <section className="panel" style={{padding:'24px',marginTop:'20px'}} aria-label={text('AI 模拟交易控制', 'AI Demo trading controls')}>
    <h2>{text('AI 模拟交易控制', 'AI Demo trading controls')}</h2>
    <p>{text('仅针对独立币安 SPOT Demo 的 BTCUSDT。配置或点击按钮不会绕过账户、凭据、构建、PostgreSQL 和场所资格门禁。', 'For BTCUSDT on the isolated Binance SPOT Demo only. Configuration and controls do not bypass account, credential, build, PostgreSQL, or venue qualification gates.')}</p>
    {statusError && <Alert error>{statusError}</Alert>}
    {status && <p>{statusError ? text('上次运行状态（当前未验证）', 'Last runtime status (currently unverified)') : text('运行状态', 'Runtime status')}: {status.account_state ?? status.blocker ?? 'NOT_BOUND'} · {status.window?.state ?? 'NOT_STARTED'}</p>}
    {status?.binding_blocker && <Alert>{text('账户绑定受阻：', 'Account binding blocked: ')}{status.binding_blocker}</Alert>}
    {status?.new_risk_paused && status.last_error && <Alert error>{text('新风险已暂停：', 'New risk paused: ')}{status.last_error}</Alert>}
    {status?.window?.error && (!status.new_risk_paused || status.window.error !== status.last_error) && <Alert error>{status.window.error}</Alert>}
    {status?.qualification_blocker && <Alert>{text('资格门禁：', 'Qualification gate: ')}{status.qualification_blocker}</Alert>}
    {saved && <p>{text('已保存策略', 'Saved strategy')} · {models.find(item => item.id === saved.model_profile_id)?.name ?? saved.model_profile_id} · v{saved.model_revision} · {text('修订', 'revision')} {saved.revision} · {text('预算', 'budget')} {saved.budget} USDT · {text('单次/每日亏损上限', 'planned/daily loss caps')} {saved.max_planned_loss} / {saved.max_daily_loss} USDT · {text('费率上限', 'fee cap')} {saved.fee_rate_cap} · {text('持有上限', 'holding cap')} {saved.holding_period_seconds}s · {text('授权到期', 'authorized until')} {new Date(saved.authorized_until * 1000).toISOString()}</p>}
    {dirty && <Alert>{text('配置已修改，必须先保存后才能按新配置启动。', 'Configuration changed. Save it before starting with the new values.')}</Alert>}
    <label><input type="checkbox" checked={bindAck} disabled={busy} onChange={e => setBindAck(e.target.checked)} /> {text('确认绑定当前登录会话中已验证的 Demo 账户。', 'Bind the verified Demo account in this login session.')}</label>
    <div className="form-actions"><button className="button" type="button" disabled={busy || !bindAck} onClick={() => void act('/demo/runtime/bind', {acknowledge_account:true})}>{text('绑定 Demo 运行时', 'Bind Demo runtime')}</button></div>
    <form className="editor-form" onSubmit={configure}>
      <label className="field"><span>{text('AI 模型配置', 'AI model profile')}</span><select value={modelId} onChange={e => setModelId(e.target.value)} disabled={busy}><option value="">{text('选择已启用模型', 'Select an enabled model')}</option>{eligible.map(item => <option key={item.id} value={item.id}>{item.name} · v{item.revision}</option>)}</select></label>
      <label className="field"><span>{text('策略预算上限（USDT）', 'Strategy budget cap (USDT)')}</span><input inputMode="decimal" value={budget} onChange={e => setBudget(e.target.value)} disabled={busy} /></label>
      <label className="field"><span>{text('单次计划亏损上限（USDT）', 'Planned loss cap (USDT)')}</span><input inputMode="decimal" value={planned} onChange={e => setPlanned(e.target.value)} disabled={busy} /></label>
      <label className="field"><span>{text('每日亏损上限（USDT）', 'Daily loss cap (USDT)')}</span><input inputMode="decimal" value={daily} onChange={e => setDaily(e.target.value)} disabled={busy} /></label>
      <label className="field"><span>{text('费率上限', 'Fee rate cap')}</span><input inputMode="decimal" value={fee} onChange={e => setFee(e.target.value)} disabled={busy} /></label>
      <label className="field"><span>{text('最大持有秒数', 'Maximum holding seconds')}</span><input type="number" min="60" max="86400" value={holding} onChange={e => setHolding(e.target.value)} disabled={busy} /></label>
      <label className="field"><span>{text('授权有效秒数', 'Authorization seconds')}</span><input type="number" min="60" max="900" value={authorization} onChange={e => setAuthorization(e.target.value)} disabled={busy} /></label>
      <label><input type="checkbox" checked={costAck} onChange={e => setCostAck(e.target.checked)} disabled={busy} /> {text('配置确认模型调用可能产生费用', 'Configuration: acknowledge possible model charges')}</label>
      <label><input type="checkbox" checked={orderAck} onChange={e => setOrderAck(e.target.checked)} disabled={busy} /> {text('配置确认允许真实 Demo 模拟订单', 'Configuration: allow orders on the real Demo venue')}</label>
      <button className="button" type="submit" disabled={busy || !model || !costAck || !orderAck}>{text('保存 AI 策略授权', 'Save AI strategy authorization')}</button>
    </form>
    <div className="editor-form">
      <label className="field"><span>{text('运行窗口秒数', 'Window seconds')}</span><input type="number" min="1" max="900" value={duration} onChange={e => setDuration(e.target.value)} disabled={busy} /></label>
      <label className="field"><span>{text('决策间隔秒数', 'Decision interval seconds')}</span><input type="number" min="30" max="900" value={interval} onChange={e => setIntervalSeconds(e.target.value)} disabled={busy} /></label>
      <label><input type="checkbox" checked={startCostAck} onChange={e => setStartCostAck(e.target.checked)} disabled={busy} /> {text('启动确认模型调用可能产生费用', 'Start: acknowledge possible model charges')}</label>
      <label><input type="checkbox" checked={startOrderAck} onChange={e => setStartOrderAck(e.target.checked)} disabled={busy} /> {text('启动确认允许真实 Demo 模拟订单', 'Start: allow orders on the real Demo venue')}</label>
      <button className="button primary" type="button" disabled={busy || !status?.configured || Boolean(statusError) || Boolean(status?.qualification_blocker) || !saved || dirty || !saved.authorized || !saved.allow_execution || !saved.allow_model_calls || !validDuration || !validInterval || !startCostAck || !startOrderAck} onClick={() => saved && void act('/demo/runtime/start', {duration_seconds:Number(duration),decision_interval_seconds:Number(interval),expected_strategy_revision:saved.revision,expected_grant_hash:saved.grant_hash,acknowledge_model_cost:true,acknowledge_demo_orders:true})}>{text('启动 AI 模拟交易', 'Start AI Demo trading')}</button>
      <label><input type="checkbox" checked={recoveryAck} onChange={e => setRecoveryAck(e.target.checked)} disabled={busy} /> {text('确认恢复会订阅私有流、对账并写入本地状态，不调用模型或发送订单。', 'Recovery subscribes to the private stream, reconciles, and writes local state; it does not call models or send orders.')}</label>
      <div className="form-actions"><button className="button" type="button" disabled={busy || !status?.configured || Boolean(statusError) || !validDuration || !recoveryAck} onClick={() => void act('/demo/runtime/recover', {duration_seconds:Number(duration),acknowledge_state_changes:true})}>{text('启动对账恢复', 'Start reconciliation')}</button><button className="button" type="button" disabled={busy} onClick={() => void act('/demo/runtime/stop')}>{text('请求停止', 'Request stop')}</button></div>
    </div>
    <DemoTakeoverControls api={api} generation={connectionGeneration} disabled={connectionBusy || busy || !status?.configured || Boolean(status.running)} />
    <section aria-label={text('已记录的模拟交易结果', 'Recorded Demo results')}>
      <h3>{text('已记录的模拟交易结果', 'Recorded Demo results')}</h3>
      <button className="button" type="button" disabled={connectionBusy} onClick={() => void readObservations()}>{text('读取记录', 'Read records')}</button>
      {observationError && <Alert error>{observationError}</Alert>}
      {observations && <div>
        <p>{text('以下为本次读取的持久记录快照，可能不完整或过时；未知结果必须继续恢复核对。', 'This is a potentially incomplete or stale snapshot of durable records. Unknown outcomes require recovery and reconciliation.')}</p>
        <h4>{text('决策', 'Decisions')}</h4><ul>{observations.decisions.map(item => <li key={item.run_id}>{item.run_id} · {item.state} · {item.action ?? '—'} · {item.summary ?? '—'}</li>)}</ul>
        <h4>{text('订单', 'Orders')}</h4><ul>{observations.orders.map(item => <li key={item.command_id}>{item.command_id} · {item.state} · {item.confirmation} · {item.order_id ?? '—'} · {item.order_status ?? '—'}</li>)}</ul>
        <h4>{text('回报', 'Reports')}</h4><ul>{observations.reports.map(item => <li key={item.observation_id}>{item.source} · {item.received_at} · {item.inbox_state ?? 'UNVERIFIED'}</li>)}</ul>
        <h4>{text('成交', 'Fills')}</h4><ul>{observations.fills.map(item => <li key={`${item.symbol}-${item.trade_id}`}>{item.symbol} · {item.order_id} · {item.trade_id} · {item.quantity ?? '—'} @ {item.price ?? '—'} · {item.confirmation}</li>)}</ul>
        {Boolean(observations.unresolved_trade_history?.length) && <div>
          <h4>{text('未归属成交记录需要核对', 'Unresolved account trades require review')}</h4>
          <p>{text('这些记录可能是外部成交或本系统尚未确认的订单，不计入策略成交。只有精确匹配并验证原订单的记录才可解除阻断；其他记录仍需人工核对。', 'These may be external trades or this system’s unconfirmed orders and are not counted as strategy fills. Only a verified exact match to the original order clears a record; other records require review.')}</p>
          <ul>{observations.unresolved_trade_history?.map(item => <li key={item.trade_hash}>{item.symbol} · {item.order_id ?? '—'} · {item.trade_id ?? '—'}</li>)}</ul>
        </div>}
        {Boolean(observations.account_position_review?.count) && <p>
          {text(`${observations.account_position_review?.count} 条余额事件仍待核对，不能用于恢复交易。`, `${observations.account_position_review?.count} account balance events remain pending; trading cannot resume.`)}
          {' '}{observations.account_position_review?.blocker}
        </p>}
        <p>{text('盈亏', 'P&L')}: {observations.pnl.state} · {observations.pnl.reason}</p>
      </div>}
    </section>
    <p className="field-help">{text('停止不会取消交易所挂单，也不保证平仓；窗口结束后保护监督停止，必须检查并接管未平仓和挂单。', 'Stopping does not cancel exchange orders or ensure a flat position. Protection supervision ends with the window; inspect and take over open exposure and orders.')}</p>
    {notice && <Alert>{notice}</Alert>}{error && <Alert error>{error}</Alert>}
  </section>;
}
