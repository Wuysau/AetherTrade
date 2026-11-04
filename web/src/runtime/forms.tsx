import {researchProfile, changeResearchInterval} from './assessment';
import { INSTRUMENTS, InstrumentOptions } from './instruments';
import { useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, Check, Info, LoaderCircle, ShieldCheck } from 'lucide-react';
import { errorMessage, type RuntimeApi } from './api';
import { money, positiveDecimal, quantity, quoteIsFresh, time } from './format';
import { Alert, Badge, Dialog } from './ui';
import { ModelSelection, routingError, strategyProfileIds } from './modelprofiles';
import { paperOrderLabel } from './paperexecution';
import type { Dashboard, Mode, ModelProfile, PaperAction, PaperOrderInput, PriceRule, Risk, Strategy, StrategyInput, SymbolName } from './types';
import { live, t, tt } from './i18n';

export const MODE_NAMES: Record<Mode, string> = live(() => ({ AI: t("AI 自主决策"), RULE: t("用户规则"), HYBRID: t("AI / 规则混合") }));
export const SYMBOLS: SymbolName[] = INSTRUMENTS.map(i => i.symbol);
export function StrategyDialog({ api, existing, mode, profiles, onClose, onSaved }: { api: RuntimeApi; existing?: Strategy; mode: Mode; profiles?: ModelProfile[]; onClose: () => void; onSaved: (message: string) => void }) {
  const [form, setForm] = useState<StrategyInput>(existing ? { name: existing.name, symbol: existing.symbol, mode: existing.mode, budget: existing.budget, target_pct: existing.target_pct, analysis_interval_seconds: existing.analysis_interval_seconds ?? 300, auto_execute: existing.auto_execute ?? false, decision_schema: existing.decision_schema, decision_policy:existing.decision_policy, task_profile:existing.task_profile, analysis_mode: existing.analysis_mode ?? 'single', model_profile_ids: strategyProfileIds(existing) } : { name: '', symbol: 'BTCUSDT', mode, budget: '1000', target_pct: '10', analysis_interval_seconds: 300, auto_execute: false, analysis_mode: 'single', model_profile_ids: mode !== 'RULE' && profiles?.some(p => p.id === 'default') ? ['default'] : [] });
  const [status, setStatus] = useState<Strategy['status']>(existing?.status ?? 'DRAFT');
  const [rule, setRule] = useState<PriceRule>(existing?.rule ?? {kind:'PRICE_THRESHOLD', operator:'LTE', price:'', stop_loss_pct:'5'});
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const update = <K extends keyof StrategyInput>(key: K, value: StrategyInput[K]) => setForm(prev => ({ ...prev, [key]: value }));
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form.name.trim() || !positiveDecimal(form.budget) || !positiveDecimal(form.target_pct) || Number(form.target_pct) > 100) { setError(t("请填写策略名称、正数预算和 0 至 100% 之间的目标仓位。")); return; }
    if (form.mode !== 'AI' && status === 'ACTIVE' && (!positiveDecimal(rule.price) || !positiveDecimal(rule.stop_loss_pct) || Number(rule.stop_loss_pct) >= 100)) { setError(t("启用规则或混合模式前，请填写触发价格和大于 0、小于 100 的止损比例。")); return; }
    if (profiles && form.mode !== 'RULE') { const problem = routingError(form.analysis_mode ?? 'single', form.model_profile_ids ?? [], profiles, status !== 'ACTIVE'); if (problem) { setError(problem); return; } }
    setBusy(true); setError('');
    const { analysis_mode, model_profile_ids, ...legacyForm } = form;
    const values = profiles !== undefined || existing?.model_profile_ids !== undefined ? { ...legacyForm, analysis_mode, model_profile_ids } : legacyForm;
    try { await api.saveStrategy({ ...values, name: form.name.trim(), ...(form.mode !== 'AI' && positiveDecimal(rule.price) ? {rule} : {}), ...(form.mode !== 'AI' && status === 'ACTIVE' ? {acknowledge_paper_automation:true} : {}), acknowledge_model_cost: status === 'ACTIVE' && form.mode !== 'RULE' }, existing, status); onSaved(status === 'ACTIVE' ? t("策略已启用，将按周期评估；自动下单以勾选状态为准。") : t("策略配置已保存；当前不运行自动分析。")); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <Dialog title={existing ? t("编辑策略") : t("创建策略")} onClose={onClose} busy={busy} wide><form onSubmit={submit} className="editor-form">
    <p>{t("配置保存在本地 PAPER 账户。保存草稿不会运行模型或自动下单。")}</p>
    <label className="field"><span>{t("策略名称")}</span><input required maxLength={80} autoFocus value={form.name} placeholder={t("例如：BTC 趋势观察")} onChange={e => update('name', e.target.value)} disabled={busy} /></label>
    <div className="form-grid"><label className="field"><span>{t("交易标的")}</span><select value={form.symbol} onChange={e => update('symbol', e.target.value as SymbolName)} disabled={busy}><InstrumentOptions /></select></label><label className="field"><span>{t("决策模式")}</span><select value={form.mode} onChange={e => { update('mode', e.target.value as Mode); if (e.target.value !== 'AI') { update('auto_execute', false); setStatus('DRAFT'); } if (e.target.value === 'RULE') { update('model_profile_ids', []); update('analysis_mode', 'single'); } }} disabled={busy}>{Object.entries(MODE_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
    <div className="form-grid"><label className="field"><span>{t("策略预算")} <small>USDT</small></span><input required inputMode="decimal" value={form.budget} onChange={e => update('budget', e.target.value)} disabled={busy} /></label><label className="field"><span>{t("目标仓位")} <small>%</small></span><input required inputMode="decimal" value={form.target_pct} onChange={e => update('target_pct', e.target.value)} disabled={busy} /></label></div>
    {form.mode !== 'RULE' && <fieldset className="model-routing"><legend>{t("分析与计划契约")}</legend><label className="field"><span>{t("计划格式")}</span><select value={form.decision_schema ?? 'legacy'} disabled={busy} onChange={e => setForm(previous=>({...previous,decision_schema:e.target.value as 'legacy'|'assessment_v1',decision_policy:'unanimous',task_profile:e.target.value === 'assessment_v1' ? researchProfile() : undefined}))}><option value="legacy">{t("历史建议（需升级后自动执行）")}</option><option value="assessment_v1">{t("结构化目标与退出计划")}</option></select></label>{form.decision_schema === 'assessment_v1' && <><label className="field"><span>{t("研究周期（32 根已收盘 K 线）")}</span><select value={form.task_profile?.decision_interval ?? '15m'} disabled={busy} onChange={e=>update('task_profile',changeResearchInterval(form.task_profile,e.target.value))}>{['5m','15m','1h','4h'].map(v=><option key={v}>{v}</option>)}</select></label><p className="field-help">{t("观察持有期 1 小时；报价与 K 线为必需，盘口和新闻为辅助。预算与仓位比例是上限。")}</p>{form.analysis_mode === 'consensus' && <label className="field"><span>{t("交叉决策政策")}</span><select value={form.decision_policy ?? 'unanimous'} disabled={busy} onChange={e=>update('decision_policy',e.target.value as 'unanimous'|'evidence_review')}><option value="unanimous">{t("所有模型一致")}</option><option value="evidence_review">{t("证据复核与有限裁决")}</option></select></label>}</>}</fieldset>}
    {profiles && form.mode !== 'RULE' && <ModelSelection profiles={profiles} mode={form.analysis_mode ?? 'single'} ids={form.model_profile_ids ?? []} disabled={busy} recurring onChange={(analysis_mode, model_profile_ids) => setForm(previous => ({ ...previous, analysis_mode, model_profile_ids, decision_policy:analysis_mode === 'single' ? 'unanimous' : previous.decision_policy }))} />}
    {form.mode !== 'AI' && <fieldset className="model-routing"><legend>{t("价格规则")}</legend><p className="field-help">{t("仅使用新鲜行情。规则满足时提出目标仓位；混合模式还需模型建议一致，仍须通过同一风控。")}</p><div className="form-grid"><label className="field"><span>{t("价格触发条件")}</span><select value={rule.operator} onChange={e => setRule({...rule, operator:e.target.value as PriceRule['operator']})} disabled={busy}><option value="LTE">{t("最新价格 ≤ 触发价格")}</option><option value="GTE">{t("最新价格 ≥ 触发价格")}</option></select></label><label className="field"><span>{t("规则触发价格")}</span><input inputMode="decimal" value={rule.price} disabled={busy} onChange={e => setRule({...rule,price:e.target.value})} /></label></div><label className="field"><span>{t("计划止损比例（%）")}</span><input inputMode="decimal" value={rule.stop_loss_pct} disabled={busy} onChange={e => setRule({...rule,stop_loss_pct:e.target.value})} /></label><p className="field-help">{t("止损计划需要单独验证，不保证实际退出价格。")}</p></fieldset>}
    <label className="field"><span>{t("配置状态")}</span><select value={status} onChange={e => { setStatus(e.target.value as Strategy['status']); if (e.target.value === 'DRAFT') update('auto_execute', false); }} disabled={busy}><option value="DRAFT">{t("草稿")}</option><option value="ACTIVE">{form.mode === 'RULE' ? t("启用规则评估（无模型调用）") : t("启用自动分析（可能持续产生 API 费用）")}</option><option value="PAUSED">{t("暂停")}</option></select></label>
    {<><label className="field"><span>{t("分析间隔（秒）")}</span><input type="number" required min={60} max={86400} step={1} value={form.analysis_interval_seconds} disabled={busy} onChange={e => update('analysis_interval_seconds', Number(e.target.value))} /></label><p className="field-help">{form.mode === 'RULE' ? t("按间隔评估价格规则，不调用模型。暂停停止新目标；已有订单和保护维护继续。") : t("启用后按间隔调用模型，可能持续产生 API 费用。暂停停止新分析；旧建议不能直接下单。")}</p><label className="model-check"><input type="checkbox" checked={form.auto_execute ?? false} disabled={busy} onChange={e => { update('auto_execute', e.target.checked); if (e.target.checked) setStatus('ACTIVE'); }} />{t("自动下单（PAPER）")}</label><p className="field-help">{t("勾选后允许新目标通过风控进入 PAPER 命令队列；取消停止新自动订单，已有订单和保护责任继续。")}</p></>}
    {status === 'ACTIVE' && form.mode === 'RULE' && <Alert>{t("保存即确认持续评估规则。自动下单须单独勾选，零模型调用；PAPER 成交事件由模拟控制器确认。")}</Alert>}
    {status === 'ACTIVE' && form.mode !== 'RULE' && <Alert>{t("保存即确认按当前模型选择持续分析：每轮最多")} {(form.model_profile_ids?.length ?? 1) + (form.decision_policy === 'evidence_review' ? 1 : 0)}  {t("次调用，每")} {form.analysis_interval_seconds ?? 300}  {t("秒一轮，可能分别产生 API 费用。自动下单仍需权限与风控检查。")}</Alert>}
    {error && <Alert error>{error}</Alert>}
    <div className="dialog-buttons"><button type="button" className="button" onClick={onClose} disabled={busy}>{t("取消")}</button><button className="button primary" disabled={busy}>{busy ? <LoaderCircle className="icon spin" /> : <Check className="icon" />}{busy ? t("正在保存…") : t("保存策略")}</button></div>
  </form></Dialog>;
}
export function RiskForm({ risk, api, onSaved }: { risk: Risk; api: RuntimeApi; onSaved: (message: string) => void }) {
  const [editing, setEditing] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [draft, setDraft] = useState(risk);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!positiveDecimal(draft.max_daily_loss) || !positiveDecimal(draft.max_position_pct) || Number(draft.max_position_pct) > 100) { setError(t("每日亏损限额必须大于 0，单标的上限须在 0 至 100% 之间。")); return; }
    setError(''); setBusy(true);
    try { await api.saveRisk({ max_position_pct: draft.max_position_pct, max_daily_loss: draft.max_daily_loss }, draft.revision); setEditing(false); onSaved(t("风险阈值已保存，对后续模拟订单生效。")); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <form className="form-rows risk-form" onSubmit={submit}>
    <div className="form-row"><div><label className="form-name" htmlFor="max-loss">{t("当日最大可承受亏损")}</label><div className="form-help">{t("触发后禁止新增开仓 · USDT")}</div></div>{editing ? <input id="max-loss" className="compact-input" required inputMode="decimal" value={draft.max_daily_loss} onChange={e => setDraft({ ...draft, max_daily_loss: e.target.value })} disabled={busy} /> : <span className="form-val">{money(risk.max_daily_loss)}</span>}</div>
    <div className="form-row"><div><label className="form-name" htmlFor="max-position">{t("单标的持仓上限")}</label><div className="form-help">{t("按账户权益计算 · %")}</div></div>{editing ? <input id="max-position" className="compact-input" required inputMode="decimal" value={draft.max_position_pct} onChange={e => setDraft({ ...draft, max_position_pct: e.target.value })} disabled={busy} /> : <span className="form-val">{risk.max_position_pct}%</span>}</div>
    <div className="form-row"><div><div className="form-name">{t("行情时效校验")}</div><div className="form-help">{t("报价过期或缺失时拒绝成交")}</div></div><Badge kind="pass">{t("服务端强制")}</Badge></div>
    <div className="form-row"><div><div className="form-name">{t("模拟交易费率")}</div><div className="form-help">{t("计入成交成本与账户净值")}</div></div><span className="form-val">0.10%</span></div>
    {editing && draft.revision !== risk.revision && <Alert error>{t("风险配置已被更新。请取消编辑后重新读取最新值。")}</Alert>}
    {error && <Alert error>{error}</Alert>}
    <div className="form-actions">{editing ? <><button type="button" className="button small" disabled={busy} onClick={() => { setEditing(false); setError(''); }}>{t("取消编辑")}</button><button className="button small primary" disabled={busy || draft.revision !== risk.revision}>{busy ? t("保存中…") : t("保存风险阈值")}</button></> : <button type="button" className="button small" onClick={() => { setDraft(risk); setEditing(true); setError(''); }}>{t("编辑风险阈值")}</button>}<span className="quiet revision-label">{t("版本")} {risk.revision}</span></div>
  </form>;
}
export function OrderDialog({ api, data, initial, onClose, onSaved }: { api: RuntimeApi; data: Dashboard; initial?: Partial<PaperOrderInput>; onClose: () => void; onSaved: (message: string) => void }) {
  const [form, setForm] = useState<PaperOrderInput>({ symbol: 'BTCUSDT', side: 'BUY', quantity: '', strategy_id: null, ...initial });
  const [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const key = useRef(crypto.randomUUID());
  const quote = data.market.quotes.find(q => q.symbol === form.symbol);
  const fresh = quoteIsFresh(quote) && ['CONNECTED', 'PARTIAL'].includes(data.market.status);
  const holding = data.positions.find(p => p.symbol === form.symbol);
  const paused = data.account.paused && form.side === 'BUY';
  const eligibleStrategies = data.strategies.filter(strategy => strategy.symbol === form.symbol);
  const unavailableStrategy = Boolean(form.strategy_id && !eligibleStrategies.some(strategy => strategy.id === form.strategy_id));
  function update<K extends keyof PaperOrderInput>(field: K, value: PaperOrderInput[K]) {
    setForm(previous => {
      const next = { ...previous, [field]: value };
      if (field === 'symbol' && !data.strategies.some(strategy => strategy.id === next.strategy_id && strategy.symbol === next.symbol)) next.strategy_id = null;
      return next;
    });
    key.current = crypto.randomUUID();
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (!positiveDecimal(form.quantity)) { setError(t("请输入大于 0 的交易数量，不支持科学计数法。")); return; }
    if (!fresh) { setError(t("当前没有可用的新鲜行情，请关闭窗口后刷新行情。")); return; }
    if (unavailableStrategy) { setError(t("原归属策略已不可用或与交易标的不符，请重新选择归属策略。")); return; }
    if (!confirming) { setConfirming(true); return; }
    setBusy(true);
    try { const response = await api.order(form, key.current); onSaved(`${t("PAPER 请求已记录 · ")}${paperOrderLabel(response.order.status)} · ${response.order.id}`); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <Dialog title={confirming ? t("确认模拟订单") : t("创建模拟订单")} busy={busy} onClose={onClose} wide><form onSubmit={submit} className="editor-form">
    <Alert><strong>PAPER</strong>  {t("· 仅使用虚拟资金。提交后等待模拟订单事件；无法验证真实滑点或流动性。")}</Alert>
    {confirming ? <><div className="order-confirm-summary"><span className={`order-direction ${form.side === 'BUY' ? 'positive' : 'negative'}`}>{form.side === 'BUY' ? t("买入") : t("卖出")}</span><h3>{form.symbol}</h3><div>{quantity(form.quantity)} <span className="quiet">{form.symbol.replace('USDT', '')}</span></div></div><div className="form-note">{t("以下金额仅为估算。回执表示请求已记录；实际数量、价格和保护状态以之后的事件为准。")}</div></> : <>
      <div className="form-grid"><label className="field"><span>{t("交易标的")}</span><select value={form.symbol} onChange={e => update('symbol', e.target.value as SymbolName)}><InstrumentOptions /></select></label><label className="field"><span>{t("交易方向")}</span><select value={form.side} onChange={e => update('side', e.target.value as 'BUY' | 'SELL')}><option value="BUY">{t("买入 · 增加持仓")}</option><option value="SELL">{t("卖出 · 减少持仓")}</option></select></label></div>
      <label className="field"><span>{t("交易数量")} <small>{form.symbol.replace('USDT', '')}</small></span><input autoFocus required inputMode="decimal" value={form.quantity} placeholder={t("输入数量")} onChange={e => update('quantity', e.target.value)} /></label>
      <label className="field"><span>{t("归属策略")} <small>{t("可选")}</small></span><select value={form.strategy_id ?? ''} onChange={e => update('strategy_id', e.target.value || null)}><option value="">{t("手动交易")}</option>{unavailableStrategy && <option value={form.strategy_id!} disabled>{t("原策略不可用，请重新选择")}</option>}{eligibleStrategies.map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select></label>
    </>}
    <div className="order-estimate"><div><span>{t("参考报价")}</span><strong>{quote ? money(quote.price) : '—'}</strong></div><div><span>{t("参考成交额")}</span><strong>{quote && positiveDecimal(form.quantity) ? money(Number(quote.price) * Number(form.quantity)) : '—'}</strong></div><div><span>{form.side === 'BUY' ? t("可用资金") : t("当前持有")}</span><strong>{form.side === 'BUY' ? `${money(data.account.cash)} USDT` : `${quantity(holding?.quantity ?? '0')} ${form.symbol.replace('USDT', '')}`}</strong></div></div>
    <div className="form-note">{t("来源：")}{quote?.source ?? t("暂无")} · {time(quote?.as_of)} UTC+8 <Badge kind={fresh ? 'pass' : 'wait'}>{fresh ? t("有效行情") : t("行情不可用")}</Badge></div>
    {paused && <Alert error>{t("新增开仓已暂停。请先在风控与权限中恢复开仓，卖出不受此限制。")}</Alert>}
    {!fresh && <Alert error>{t("报价缺失或已过期，当前不能执行模拟订单。请刷新行情后重试。")}</Alert>}
    {error && <Alert error>{error}</Alert>}
    <div className="dialog-buttons">{confirming ? <button className="button" type="button" disabled={busy} onClick={() => { setConfirming(false); setError(''); }}><ArrowLeft className="icon" />{t("返回修改")}</button> : <button className="button" type="button" disabled={busy} onClick={onClose}>{t("取消")}</button>}<button className="button primary" disabled={busy || !fresh || paused}>{busy ? <LoaderCircle className="icon spin" /> : <ShieldCheck className="icon" />}{busy ? t("正在提交…") : confirming ? t("确认模拟委托") : t("核对订单")}</button></div>
  </form></Dialog>;
}
const ACTION_COPY: Record<PaperAction, { title: string; description: string; button: string }> = live(() => ({
  pause_new: { title: t("暂停新增开仓"), description: t("暂停本地 PAPER 账户的新买入。现有持仓保持不变，卖出仍可执行。"), button: t("确认暂停开仓") },
  resume_new: { title: t("恢复新增开仓"), description: t("允许本地 PAPER 账户再次买入。行情时效、资金与风险阈值仍由服务端逐笔校验。"), button: t("确认恢复开仓") },
  cancel_entries: { title: t("撤销未成交入场委托"), description: t("仅撤销未成交的模拟入场委托。撤单请求需等待确认；结果未知时保留余单预留，已成交部分仍需保护。"), button: t("确认撤销入场委托") },
  exit_owned: { title: t("退出全部模拟持仓"), description: t("为已有持仓请求减仓，逐笔校验归属、数量和授权。只有成交与对账确认后才算完成退出。"), button: t("确认退出全部持仓") },
}));
export function ActionDialog({ action, data, api, onClose, onSaved }: { action: PaperAction; data: Dashboard; api: RuntimeApi; onClose: () => void; onSaved: (message: string) => void }) {
  const [revision] = useState(data.account.revision), [key] = useState(() => crypto.randomUUID());
  const [confirmation, setConfirmation] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const copy = ACTION_COPY[action];
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { const receipt = await api.action(action, revision, key, action === 'exit_owned' ? confirmation : undefined); onSaved(`${tt(receipt.detail)}${t(" · 影响 ")}${receipt.affected_count}${t(" 项")}`); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <Dialog title={copy.title} onClose={onClose} busy={busy}><form className="editor-form" onSubmit={submit}><p>{copy.description}</p><div className="confirmation-context"><Badge>PAPER</Badge><span>{t("当前持仓")} {data.positions.length}  {t("笔")}</span><span>{t("账户版本")} {revision}</span></div>{action === 'exit_owned' && <label className="field"><span>{t("输入")} <strong>EXIT PAPER</strong>  {t("确认操作")}</span><input autoFocus autoComplete="off" value={confirmation} onChange={e => setConfirmation(e.target.value)} placeholder="EXIT PAPER" disabled={busy} /></label>}{error && <Alert error>{error}</Alert>}<div className="dialog-buttons"><button type="button" className="button" onClick={onClose} disabled={busy} autoFocus={action !== 'exit_owned'}>{t("取消")}</button><button className={`button ${action === 'exit_owned' ? 'danger' : 'primary'}`} disabled={busy || (action === 'exit_owned' && confirmation !== 'EXIT PAPER')}>{busy ? t("执行中…") : copy.button}</button></div></form></Dialog>;
}
