import { useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, Check, Info, LoaderCircle, ShieldCheck } from 'lucide-react';
import { errorMessage, type RuntimeApi } from './api';
import { money, positiveDecimal, quantity, quoteIsFresh, time } from './format';
import { Alert, Badge, Dialog } from './ui';
import type { Dashboard, Mode, PaperAction, PaperOrderInput, Risk, Strategy, StrategyInput, SymbolName } from './types';

export const MODE_NAMES: Record<Mode, string> = { AI: 'AI 自主决策', RULE: '用户规则', HYBRID: 'AI / 规则混合' };
export const SYMBOLS: SymbolName[] = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];
export function StrategyDialog({ api, existing, mode, onClose, onSaved }: { api: RuntimeApi; existing?: Strategy; mode: Mode; onClose: () => void; onSaved: (message: string) => void }) {
  const [form, setForm] = useState<StrategyInput>(existing ? { name: existing.name, symbol: existing.symbol, mode: existing.mode, budget: existing.budget, target_pct: existing.target_pct } : { name: '', symbol: 'BTCUSDT', mode, budget: '1000', target_pct: '10' });
  const [status, setStatus] = useState<Strategy['status']>(existing?.status ?? 'DRAFT');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const update = <K extends keyof StrategyInput>(key: K, value: StrategyInput[K]) => setForm(prev => ({ ...prev, [key]: value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form.name.trim() || !positiveDecimal(form.budget) || !positiveDecimal(form.target_pct) || Number(form.target_pct) > 100) { setError('请填写策略名称、正数预算和 0 至 100% 之间的目标仓位。'); return; }
    setBusy(true); setError('');
    try { await api.saveStrategy({ ...form, name: form.name.trim() }, existing, status); onSaved(existing ? '策略配置已更新；自动交易尚未启用。' : '策略草稿已保存；自动交易尚未启用。'); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <Dialog title={existing ? '编辑策略' : '创建策略'} onClose={onClose} busy={busy} wide><form onSubmit={submit} className="editor-form">
    <p>配置保存在本地 PAPER 账户。保存草稿不会运行模型或自动下单。</p>
    <label className="field"><span>策略名称</span><input required maxLength={80} autoFocus value={form.name} placeholder="例如：BTC 趋势观察" onChange={e => update('name', e.target.value)} disabled={busy} /></label>
    <div className="form-grid"><label className="field"><span>交易标的</span><select value={form.symbol} onChange={e => update('symbol', e.target.value as SymbolName)} disabled={busy}>{SYMBOLS.map(symbol => <option key={symbol}>{symbol}</option>)}</select></label><label className="field"><span>决策模式</span><select value={form.mode} onChange={e => update('mode', e.target.value as Mode)} disabled={busy}>{Object.entries(MODE_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
    <div className="form-grid"><label className="field"><span>策略预算 <small>USDT</small></span><input required inputMode="decimal" value={form.budget} onChange={e => update('budget', e.target.value)} disabled={busy} /></label><label className="field"><span>目标仓位 <small>%</small></span><input required inputMode="decimal" value={form.target_pct} onChange={e => update('target_pct', e.target.value)} disabled={busy} /></label></div>
    {existing && <label className="field"><span>配置状态</span><select value={status} onChange={e => setStatus(e.target.value as Strategy['status'])} disabled={busy}><option value="DRAFT">草稿</option><option value="PAUSED">暂停</option></select></label>}
    <div className="form-note"><Info className="icon sm" />仅支持现货模拟记录。{form.mode === 'RULE' ? '规则执行引擎未接入。' : 'AI 模型未配置。'}所有模式均不会自动交易。</div>
    {error && <Alert error>{error}</Alert>}
    <div className="dialog-buttons"><button type="button" className="button" onClick={onClose} disabled={busy}>取消</button><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="icon spin" /> : <Check className="icon" />}{busy ? '正在保存…' : '保存策略'}</button></div>
  </form></Dialog>;
}
export function RiskForm({ risk, api, onSaved }: { risk: Risk; api: RuntimeApi; onSaved: (message: string) => void }) {
  const [editing, setEditing] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [draft, setDraft] = useState(risk);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!positiveDecimal(draft.max_daily_loss) || !positiveDecimal(draft.max_position_pct) || Number(draft.max_position_pct) > 100) { setError('每日亏损限额必须大于 0，单标的上限须在 0 至 100% 之间。'); return; }
    setError(''); setBusy(true);
    try { await api.saveRisk({ max_position_pct: draft.max_position_pct, max_daily_loss: draft.max_daily_loss }, draft.revision); setEditing(false); onSaved('风险阈值已保存，对后续模拟订单生效。'); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <form className="form-rows risk-form" onSubmit={submit}>
    <div className="form-row"><div><label className="form-name" htmlFor="max-loss">当日最大可承受亏损</label><div className="form-help">触发后禁止新增开仓 · USDT</div></div>{editing ? <input id="max-loss" className="compact-input" required inputMode="decimal" value={draft.max_daily_loss} onChange={e => setDraft({ ...draft, max_daily_loss: e.target.value })} disabled={busy} /> : <span className="form-val">{money(risk.max_daily_loss)}</span>}</div>
    <div className="form-row"><div><label className="form-name" htmlFor="max-position">单标的持仓上限</label><div className="form-help">按账户权益计算 · %</div></div>{editing ? <input id="max-position" className="compact-input" required inputMode="decimal" value={draft.max_position_pct} onChange={e => setDraft({ ...draft, max_position_pct: e.target.value })} disabled={busy} /> : <span className="form-val">{risk.max_position_pct}%</span>}</div>
    <div className="form-row"><div><div className="form-name">行情时效校验</div><div className="form-help">报价过期或缺失时拒绝成交</div></div><Badge kind="pass">服务端强制</Badge></div>
    <div className="form-row"><div><div className="form-name">模拟交易费率</div><div className="form-help">计入成交成本与账户净值</div></div><span className="form-val">0.10%</span></div>
    {editing && draft.revision !== risk.revision && <Alert error>风险配置已被更新。请取消编辑后重新读取最新值。</Alert>}
    {error && <Alert error>{error}</Alert>}
    <div className="form-actions">{editing ? <><button type="button" className="button small" disabled={busy} onClick={() => { setEditing(false); setError(''); }}>取消编辑</button><button className="button small primary" disabled={busy || draft.revision !== risk.revision}>{busy ? '保存中…' : '保存风险阈值'}</button></> : <button type="button" className="button small" onClick={() => { setDraft(risk); setEditing(true); setError(''); }}>编辑风险阈值</button>}<span className="quiet revision-label">版本 {risk.revision}</span></div>
  </form>;
}
export function OrderDialog({ api, data, initial, onClose, onSaved }: { api: RuntimeApi; data: Dashboard; initial?: Partial<PaperOrderInput>; onClose: () => void; onSaved: (message: string) => void }) {
  const [form, setForm] = useState<PaperOrderInput>({ symbol: 'BTCUSDT', side: 'BUY', quantity: '', strategy_id: null, ...initial });
  const [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const key = useRef(crypto.randomUUID());
  const quote = data.market.quotes.find(q => q.symbol === form.symbol);
  const fresh = quoteIsFresh(quote) && data.market.status === 'CONNECTED';
  const holding = data.positions.find(p => p.symbol === form.symbol);
  const paused = data.account.paused && form.side === 'BUY';
  function update<K extends keyof PaperOrderInput>(field: K, value: PaperOrderInput[K]) { setForm(previous => ({ ...previous, [field]: value })); key.current = crypto.randomUUID(); }
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (!positiveDecimal(form.quantity)) { setError('请输入大于 0 的交易数量，不支持科学计数法。'); return; }
    if (!fresh) { setError('当前没有可用的新鲜行情，请关闭窗口后刷新行情。'); return; }
    if (!confirming) { setConfirming(true); return; }
    setBusy(true);
    try { const response = await api.order(form, key.current); onSaved(`模拟订单已成交 · ${response.order.id}`); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <Dialog title={confirming ? '确认模拟订单' : '创建模拟订单'} busy={busy} onClose={onClose} wide><form onSubmit={submit} className="editor-form">
    <Alert><strong>PAPER</strong> · 仅影响本地虚拟资金，使用服务端最新有效报价成交，模拟手续费 0.10%。</Alert>
    {confirming ? <><div className="order-confirm-summary"><span className={`order-direction ${form.side === 'BUY' ? 'positive' : 'negative'}`}>{form.side === 'BUY' ? '买入' : '卖出'}</span><h3>{form.symbol}</h3><div>{quantity(form.quantity)} <span className="quiet">{form.symbol.replace('USDT', '')}</span></div></div><div className="form-note">按确认时服务端报价成交。以下金额为参考估算，最终以成交回执为准。</div></> : <>
      <div className="form-grid"><label className="field"><span>交易标的</span><select value={form.symbol} onChange={e => update('symbol', e.target.value as SymbolName)}>{SYMBOLS.map(symbol => <option key={symbol}>{symbol}</option>)}</select></label><label className="field"><span>交易方向</span><select value={form.side} onChange={e => update('side', e.target.value as 'BUY' | 'SELL')}><option value="BUY">买入 · 增加持仓</option><option value="SELL">卖出 · 减少持仓</option></select></label></div>
      <label className="field"><span>交易数量 <small>{form.symbol.replace('USDT', '')}</small></span><input autoFocus required inputMode="decimal" value={form.quantity} placeholder="输入数量" onChange={e => update('quantity', e.target.value)} /></label>
      <label className="field"><span>归属策略 <small>可选</small></span><select value={form.strategy_id ?? ''} onChange={e => update('strategy_id', e.target.value || null)}><option value="">手动交易</option>{data.strategies.filter(s => s.symbol === form.symbol).map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select></label>
    </>}
    <div className="order-estimate"><div><span>参考报价</span><strong>{quote ? money(quote.price) : '—'}</strong></div><div><span>参考成交额</span><strong>{quote && positiveDecimal(form.quantity) ? money(Number(quote.price) * Number(form.quantity)) : '—'}</strong></div><div><span>{form.side === 'BUY' ? '可用资金' : '当前持有'}</span><strong>{form.side === 'BUY' ? `${money(data.account.cash)} USDT` : `${quantity(holding?.quantity ?? '0')} ${form.symbol.replace('USDT', '')}`}</strong></div></div>
    <div className="form-note">来源：{quote?.source ?? '暂无'} · {time(quote?.as_of)} UTC+8 <Badge kind={fresh ? 'pass' : 'wait'}>{fresh ? '有效行情' : '行情不可用'}</Badge></div>
    {paused && <Alert error>新增开仓已暂停。请先在风控与权限中恢复开仓，卖出不受此限制。</Alert>}
    {!fresh && <Alert error>报价缺失或已过期，当前不能执行模拟订单。请刷新行情后重试。</Alert>}
    {error && <Alert error>{error}</Alert>}
    <div className="dialog-buttons">{confirming ? <button className="button" type="button" disabled={busy} onClick={() => { setConfirming(false); setError(''); }}><ArrowLeft className="icon" />返回修改</button> : <button className="button" type="button" disabled={busy} onClick={onClose}>取消</button>}<button className="button primary" disabled={busy || !fresh || paused}>{busy ? <LoaderCircle className="icon spin" /> : <ShieldCheck className="icon" />}{busy ? '正在提交…' : confirming ? '确认模拟成交' : '核对订单'}</button></div>
  </form></Dialog>;
}
const ACTION_COPY: Record<PaperAction, { title: string; description: string; button: string }> = {
  pause_new: { title: '暂停新增开仓', description: '暂停本地 PAPER 账户的新买入。现有持仓保持不变，卖出仍可执行。', button: '确认暂停开仓' },
  resume_new: { title: '恢复新增开仓', description: '允许本地 PAPER 账户再次买入。行情时效、资金与风险阈值仍由服务端逐笔校验。', button: '确认恢复开仓' },
  cancel_entries: { title: '撤销未成交入场委托', description: '仅撤销未成交的模拟入场委托。当前手动订单即时成交，已成交订单不会被撤回；没有待撤订单时会返回无变更回执。', button: '确认撤销入场委托' },
  exit_owned: { title: '退出全部模拟持仓', description: '按每个标的最新有效报价卖出全部本地 PAPER 持仓，并计入模拟手续费。任一报价不可用时整笔操作不执行。', button: '确认退出全部持仓' },
};
export function ActionDialog({ action, data, api, onClose, onSaved }: { action: PaperAction; data: Dashboard; api: RuntimeApi; onClose: () => void; onSaved: (message: string) => void }) {
  const [revision] = useState(data.account.revision), [key] = useState(() => crypto.randomUUID());
  const [confirmation, setConfirmation] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const copy = ACTION_COPY[action];
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { const receipt = await api.action(action, revision, key, action === 'exit_owned' ? confirmation : undefined); onSaved(`${receipt.detail} · 影响 ${receipt.affected_count} 项`); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <Dialog title={copy.title} onClose={onClose} busy={busy}><form className="editor-form" onSubmit={submit}><p>{copy.description}</p><div className="confirmation-context"><Badge>PAPER</Badge><span>当前持仓 {data.positions.length} 笔</span><span>账户版本 {revision}</span></div>{action === 'exit_owned' && <label className="field"><span>输入 <strong>EXIT PAPER</strong> 确认操作</span><input autoFocus autoComplete="off" value={confirmation} onChange={e => setConfirmation(e.target.value)} placeholder="EXIT PAPER" disabled={busy} /></label>}{error && <Alert error>{error}</Alert>}<div className="dialog-buttons"><button type="button" className="button" onClick={onClose} disabled={busy} autoFocus={action !== 'exit_owned'}>取消</button><button className={`button ${action === 'exit_owned' ? 'danger' : 'primary'}`} disabled={busy || (action === 'exit_owned' && confirmation !== 'EXIT PAPER')}>{busy ? '执行中…' : copy.button}</button></div></form></Dialog>;
}
