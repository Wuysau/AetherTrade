import {researchProfile} from './assessment';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { SlidersHorizontal } from 'lucide-react';
import { ApiError, errorMessage, type RuntimeApi } from './api';
import { quoteIsFresh, time } from './format';
import { InstrumentOptions } from './instruments';
import { Alert, Badge, Panel } from './ui';
import { ModelSelection, profileAvailability, profileReady, profilesFromDashboard, routingError, strategyProfileIds } from './modelprofiles';
import type { AnalysisInput, AnalysisMode, Dashboard, ModelConfig, ModelFields, ModelProfile, SymbolName } from './types';

export function modelStatus(data: Dashboard) {
  if (data.model_profiles !== undefined) {
    const profiles = data.model_profiles;
    if (!profiles.length) return '模型未配置';
    return `${profiles.length} 份模型配置 · ${profiles.filter(p => p.enabled).length} 份已启用`;
  }
  const status = data.model_config?.connection_status ?? data.services.find(s => /ai|model/i.test(s.id))?.status;
  return status === 'CONNECTED' ? '模型目录已验证' : status === 'CONFIGURED' ? '模型已配置 · 待验证' : status === 'UNAVAILABLE' ? '模型连接不可用' : '模型未配置';
}
const fields = (c: ModelConfig): ModelFields => ({ protocol: c.protocol, base_url: c.base_url, model: c.model, enabled: c.enabled, local_mode: c.local_mode, max_output_tokens: c.max_output_tokens, timeout_seconds: c.timeout_seconds, anthropic_version: c.anthropic_version });
const emptyProfile: ModelConfig = { protocol: 'openai_responses', base_url: 'https://api.openai.com/v1', model: '', enabled: false, local_mode: false, max_output_tokens: 1024, timeout_seconds: 60, anthropic_version: '2023-06-01', revision: 0, has_api_key: false, key_available: false, connection_status: 'NOT_CONFIGURED', last_test_at: null, last_error: null };

export function ModelSettings({ data, api, onSaved }: { data: Dashboard; api: RuntimeApi; onSaved: (message: string) => void }) {
  const [profiles, setProfiles] = useState(() => profilesFromDashboard(data));
  const [selected, setSelected] = useState<string | null>(() => profiles[0]?.id ?? null), [adding, setAdding] = useState(false);
  const [dirtyIds, setDirtyIds] = useState<string[]>([]), [deleteId, setDeleteId] = useState<string | null>(null), [deleting, setDeleting] = useState(false);
  const [editorProfiles, setEditorProfiles] = useState(profiles);
  const [open, setOpen] = useState(false), [error, setError] = useState('');
  const pendingCreates = useRef(new Map<string, ModelProfile>()), pendingDeletes = useRef(new Set<string>());
  useEffect(() => {
    if (data.model_profiles === undefined && !data.model_config) return;
    const snapshot = profilesFromDashboard(data);
    const incomingIds = new Set(snapshot.map(profile => profile.id));
    // A poll already in flight must not undo a completed create/delete.
    for (const id of pendingCreates.current.keys()) if (incomingIds.has(id)) pendingCreates.current.delete(id);
    for (const id of pendingDeletes.current) if (!incomingIds.has(id)) pendingDeletes.current.delete(id);
    const incoming = snapshot.filter(profile => !pendingDeletes.current.has(profile.id));
    setProfiles(previous => [
      ...incoming.map(profile => { const local = previous.find(p => p.id === profile.id); return local && local.revision > profile.revision ? local : profile; }),
      ...pendingCreates.current.values(),
    ]);
  }, [data.model_profiles, data.model_config]);
  useEffect(() => { if (data.model_profiles !== undefined || data.model_config) return; let active = true; void api.modelConfigs().then(result => { if (active) setProfiles(result.profiles); }).catch(e => { if (active) setError(errorMessage(e)); }); return () => { active = false; }; }, [api, data.model_profiles, data.model_config]);
  useEffect(() => {
    setEditorProfiles(previous => [...profiles, ...previous.filter(profile => dirtyIds.includes(profile.id) && !profiles.some(current => current.id === profile.id))]);
  }, [profiles, dirtyIds]);
  function savedProfile(config: ModelConfig) {
    const next = config as ModelProfile;
    if (!profiles.some(profile => profile.id === next.id) || pendingCreates.current.has(next.id)) pendingCreates.current.set(next.id, next);
    setProfiles(previous => previous.some(p => p.id === next.id) ? previous.map(p => p.id === next.id ? next : p) : [...previous, next]);
  }
  const dirtyChanged = (id: string, dirty: boolean) => setDirtyIds(previous => dirty ? previous.includes(id) ? previous : [...previous, id] : previous.filter(value => value !== id));
  const referencedBy = (id: string) => data.strategies.filter(s => strategyProfileIds(s).includes(id));
  async function remove(profile: ModelProfile) {
    setDeleting(true); setError('');
    try { await api.deleteModelProfile(profile.id, profile.revision); pendingDeletes.current.add(profile.id); pendingCreates.current.delete(profile.id); setProfiles(previous => previous.filter(p => p.id !== profile.id)); setDeleteId(null); if (selected === profile.id) setSelected(null); onSaved(`已删除模型配置「${profile.name}」。`); }
    catch (e) { setError(errorMessage(e)); } finally { setDeleting(false); }
  }
  return <Panel title="模型服务" caption="每份配置独立保存密钥；策略各自选择模型" className="model-panel" action={<button className="button primary" aria-expanded={open} onClick={() => setOpen(!open)}><SlidersHorizontal className="icon" />模型配置{dirtyIds.length ? ` · ${dirtyIds.length} 份未保存` : ''}</button>}>
    <div className="panel-body"><div className="model-summary"><Badge>{profiles.length} / 20 份配置</Badge><span>{profiles.filter(profileReady).length} 份可用于分析</span><span className="quiet">保存、测试与分析分别执行</span></div>
      {error && <Alert error>{error}</Alert>}
      <div hidden={!open} className="profile-manager"><div className="section-row"><h3>已保存的模型</h3><button type="button" className="button small" disabled={profiles.length >= 20 || deleting} onClick={() => { setAdding(true); setSelected('new'); }}>添加模型配置</button></div>
        <div className="profile-list">{profiles.map(profile => <ProfileRow
          key={profile.id} profile={profile} references={referencedBy(profile.id).map(strategy => strategy.name)}
          dirty={dirtyIds.includes(profile.id)} selected={selected === profile.id} deleting={deleting} confirming={deleteId === profile.id}
          onEdit={() => { setSelected(profile.id); setDeleteId(null); }} onDelete={() => setDeleteId(profile.id)}
          onCancel={() => setDeleteId(null)} onConfirm={() => void remove(profile)}
        />)}</div>
        {editorProfiles.filter(profile => !profiles.some(current => current.id === profile.id)).map(profile => <div className="profile-row" key={profile.id}><p>{profile.name} · 此配置已不可用，未保存内容仍保留。</p><div className="action-strip"><button className="button small" onClick={() => setSelected(profile.id)}>查看保留的编辑</button><button className="button small" onClick={() => { dirtyChanged(profile.id, false); if (selected === profile.id) setSelected(null); }}>丢弃该配置编辑</button></div></div>)}
        {editorProfiles.map(profile => <div hidden={selected !== profile.id} key={profile.id}><ConfigForm config={profile} profileId={profile.id} profileName={profile.name} unavailable={!profiles.some(current => current.id === profile.id)} api={api} onConfig={savedProfile} onSaved={onSaved} onDirty={dirty => dirtyChanged(profile.id, dirty)} /></div>)}
        {adding && <div hidden={selected !== 'new'}><ConfigForm config={emptyProfile} creating profileName="" api={api} onConfig={next => { savedProfile(next); setAdding(false); setSelected((next as ModelProfile).id); dirtyChanged('new', false); }} onSaved={onSaved} onDirty={dirty => dirtyChanged('new', dirty)} /></div>}
      </div>
      <details className="manual-disclosure"><summary>手动分析一次（不自动下单）</summary><ManualAnalysis profiles={profiles} data={data} api={api} onSaved={onSaved} /></details>
    </div>
  </Panel>;
}
interface ProfileRowProps {
  profile: ModelProfile; references: string[]; dirty: boolean; selected: boolean; deleting: boolean; confirming: boolean;
  onEdit: () => void; onDelete: () => void; onCancel: () => void; onConfirm: () => void;
}
function ProfileRow({ profile, references, dirty, selected, deleting, confirming, onEdit, onDelete, onCancel, onConfirm }: ProfileRowProps) {
  const protectedProfile = profile.id === 'default' || references.length > 0;
  return <div className="profile-row">
    <div>
      <strong>{profile.name}</strong>{dirty && <Badge kind="wait">未保存</Badge>}
      <p className="field-help">{profile.model || '未指定模型'} · {profileAvailability(profile)} · 版本 {profile.revision}</p>
      <p className="field-help">{references.length ? `已关联：${references.join('、')}` : '未关联策略'}{profile.id === 'default' ? ' · 默认配置不可删除' : ''}</p>
    </div>
    <div className="action-strip">
      <button type="button" className="button small" aria-pressed={selected} onClick={onEdit}>编辑 {profile.name}</button>
      <button type="button" className="button small" disabled={deleting || protectedProfile || dirty} onClick={onDelete}>删除 {profile.name}</button>
    </div>
    {confirming && <div className="profile-delete">
      <p>删除「{profile.name}」及其已保存密钥？此配置当前未关联策略。</p>
      <div className="action-strip">
        <button className="button danger small" disabled={deleting} onClick={onConfirm}>{deleting ? '删除中…' : '确认删除配置'}</button>
        <button className="button small" disabled={deleting} onClick={onCancel}>取消删除</button>
      </div>
    </div>}
  </div>;
}

export function ConfigForm({ config, api, onConfig, onSaved, profileId, profileName, creating = false, unavailable = false, onDirty }: { config: ModelConfig; api: RuntimeApi; onConfig: (c: ModelConfig) => void; onSaved: (message: string) => void; profileId?: string; profileName?: string; creating?: boolean; unavailable?: boolean; onDirty?: (dirty: boolean) => void }) {
  const [draft, setDraft] = useState(() => fields(config)), [revision, setRevision] = useState(config.revision);
  const [name, setName] = useState(profileName ?? '');
  const [key, setKey] = useState(''), [clearKey, setClearKey] = useState(false), [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState(''), [conflict, setConflict] = useState(false);
  useEffect(() => { if (!dirty && !busy && config.revision >= revision) { setDraft(fields(config)); setRevision(config.revision); setName(profileName ?? ''); } }, [config, dirty, busy, revision, profileName]);
  const dirtyCallback = useRef(onDirty); dirtyCallback.current = onDirty;
  useEffect(() => { dirtyCallback.current?.(dirty); }, [dirty]);
  const update = <K extends keyof ModelFields>(name: K, value: ModelFields[K]) => { setDraft(d => ({ ...d, [name]: value, ...(name === 'protocol' && ['https://api.openai.com/v1', 'https://api.anthropic.com/v1'].includes(d.base_url.replace(/\/$/, '')) ? { base_url: value === 'anthropic' ? 'https://api.anthropic.com/v1' : 'https://api.openai.com/v1' } : {}) })); setDirty(true); setMessage(''); };
  const stale = conflict || config.revision > revision;
  const destinationChanged = config.base_url !== draft.base_url || config.protocol !== draft.protocol;
  async function reload() { setBusy('reload'); setError(''); try { const next = profileId ? await api.modelProfile(profileId) : await api.modelConfig(); onConfig(next); setDraft(fields(next)); setRevision(next.revision); setName('name' in next ? String(next.name) : ''); setKey(''); setClearKey(false); setDirty(false); setConflict(false); } catch (e) { setError(errorMessage(e)); } finally { setBusy(''); } }
  async function save(e: FormEvent) {
    e.preventDefault(); setError(''); setMessage('');
    if (unavailable) return;
    if ((profileId || creating) && !name.trim()) { setError('请为模型配置填写名称。'); return; }
    if (destinationChanged && config.has_api_key && !key && !clearKey) { setError('更换地址或协议后，请重新输入密钥，或明确清除已保存的密钥。'); return; }
    setBusy('save');
    try {
      const values = { ...draft, ...(key ? { api_key: key } : {}), clear_api_key: clearKey };
      const next = creating ? await api.createModelProfile({ ...values, name: name.trim() }) : profileId ? await api.saveModelProfile(profileId, { ...values, name: name.trim(), expected_revision: revision }) : await api.saveModelConfig({ ...values, expected_revision: revision });
      onConfig(next); setRevision(next.revision); setDraft(fields(next)); setKey(''); setClearKey(false); setDirty(false); setConflict(false); setMessage('配置已保存。使用此配置的已启用策略会在后续分析周期使用新版本，可能产生 API 费用。'); onSaved('模型配置已保存。');
    }
    catch (e) { if (e instanceof ApiError && e.status === 409) setConflict(true); setError(errorMessage(e)); } finally { setBusy(''); }
  }
  async function test() { setBusy('test'); setError(''); setMessage(''); try { const result = profileId ? await api.testModelProfile(profileId, revision) : await api.testModelConfig(revision); onConfig(result.config); setMessage(result.message); onSaved('模型目录测试已完成。'); } catch (e) { if (e instanceof ApiError && e.status === 409) setConflict(true); setError(errorMessage(e)); } finally { setBusy(''); } }
  return <form className="editor-form model-settings" onSubmit={save}><p className="field-help">保存后，已启用策略将在后续周期使用新配置。启用模型服务本身不会激活策略草稿。</p>
    {(profileId || creating) && <label className="field"><span>配置名称</span><input required maxLength={80} value={name} disabled={!!busy} placeholder="例如：趋势分析 / 独立复核" onChange={e => { setName(e.target.value); setDirty(true); }} /></label>}
    <div className="form-grid"><label className="field"><span>API 协议</span><select value={draft.protocol} disabled={!!busy} onChange={e => update('protocol', e.target.value as ModelFields['protocol'])}><option value="openai_responses">OpenAI Responses</option><option value="openai_chat">OpenAI Chat 兼容</option><option value="anthropic">Claude / Anthropic</option></select></label><label className="field"><span>模型名称</span><input required maxLength={200} value={draft.model} disabled={!!busy} onChange={e => update('model', e.target.value)} placeholder="填写服务商提供的模型 ID" /></label></div>
    <label className="field"><span>API 基础地址</span><input aria-label="API 基础地址" type="url" required value={draft.base_url} disabled={!!busy} onChange={e => update('base_url', e.target.value)} /><span className="field-help">填写包含版本路径的基础地址，不含 /responses、/messages 或 /chat/completions。{draft.protocol === 'anthropic' ? 'Claude 官方地址：https://api.anthropic.com/v1' : 'OpenAI 官方地址：https://api.openai.com/v1'}；自定义地址切换协议时保留。</span></label>
    <label className="field"><span>API 密钥</span><input aria-label="API 密钥" type="password" autoComplete="new-password" value={key} disabled={!!busy || clearKey} onChange={e => { setKey(e.target.value); setDirty(true); }} placeholder={config.has_api_key ? '留空保留已保存的密钥' : '输入服务商密钥'} /><span className="field-help">{config.has_api_key ? config.key_available ? '密钥已保存，不会回显。' : '已保存密钥当前不可用，请重新输入。' : '尚未保存密钥。'} 密钥在本地加密保存，不在浏览器持久存储或回显原文。测试、手动分析或已启用策略的周期分析时，服务端会使用密钥向所配置的服务商认证。</span></label>
    <label className="model-check"><input type="checkbox" checked={clearKey} disabled={!!busy} onChange={e => { setClearKey(e.target.checked); setKey(''); setDirty(true); }} />清除已保存的密钥</label>
    <label className="model-check"><input type="checkbox" checked={draft.enabled} disabled={!!busy} onChange={e => update('enabled', e.target.checked)} />启用模型服务</label>
    <details><summary>高级设置</summary><div className="model-advanced"><label className="model-check"><input type="checkbox" checked={draft.local_mode} disabled={!!busy} onChange={e => update('local_mode', e.target.checked)} />本地兼容服务模式（可选）</label><p className="field-help">仅在你已有本机 API 服务时开启，允许 localhost / 127.0.0.1 / ::1 的 HTTP 地址。使用远程 OpenAI 或 Claude API 无需安装本地模型。</p><div className="form-grid"><label className="field"><span>请求超时（秒）</span><input type="number" min={5} max={120} required value={draft.timeout_seconds} disabled={!!busy} onChange={e => update('timeout_seconds', Number(e.target.value))} /></label><label className="field"><span>最大输出 token</span><input type="number" min={128} max={8192} required value={draft.max_output_tokens} disabled={!!busy} onChange={e => update('max_output_tokens', Number(e.target.value))} /></label></div>{draft.protocol === 'anthropic' && <label className="field"><span>Anthropic API 版本</span><input required value={draft.anthropic_version} disabled={!!busy} onChange={e => update('anthropic_version', e.target.value)} /></label>}</div></details>
    {stale && <Alert error>配置已在别处更新；你的输入已保留。重新载入会丢弃当前编辑。<button className="button small" type="button" disabled={!!busy} onClick={() => void reload()}>丢弃编辑并重新载入</button></Alert>}
    {error && <Alert error>{error}</Alert>}{message && <div role="status"><Alert>{message}</Alert></div>}
    <div className="action-strip"><button className="button primary" disabled={!!busy || stale || unavailable}>{busy === 'save' ? '保存中…' : '保存配置'}</button><button type="button" className="button" disabled={!!busy || dirty || stale || !config.model || creating || unavailable} onClick={() => void test()}>{busy === 'test' ? '测试中…' : '测试连接'}</button><span className="quiet">版本 {revision} · 最近测试 {time(config.last_test_at)}</span></div><p className="field-help">测试仅查询模型目录 /models，不生成付费内容；目录验证不代表推理调用一定可用。修改后请先保存。</p>
  </form>;
}
export function ManualAnalysis({ config, profiles, data, api, onSaved }: { config?: ModelConfig; profiles?: ModelProfile[]; data: Dashboard; api: RuntimeApi; onSaved: (message: string) => void }) {
  const [symbol, setSymbol] = useState<SymbolName | ''>(''), [ack, setAck] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [mode, setMode] = useState<AnalysisMode>('single'), [ids, setIds] = useState<string[]>(() => profiles?.some(p => p.id === 'default') ? ['default'] : []);
  const [structured, setStructured] = useState(false), [policy,setPolicy] = useState<'unanimous'|'evidence_review'>('unanimous'), [interval,setInterval] = useState('15m');
  const attempt = useRef<{ input: AnalysisInput; key: string } | null>(null);
  const available = profiles ?? (config ? [{ ...config, id: 'default', name: '默认模型' }] : []);
  const selectedIds = profiles ? ids : ['default'];
  const fresh = quoteIsFresh(data.market.quotes.find(q => q.symbol === symbol)) && ['CONNECTED', 'PARTIAL'].includes(data.market.status);
  const selectionError = routingError(mode, selectedIds, available);
  const ready = !selectionError && selectedIds.every(id => profileReady(available.find(p => p.id === id)));
  // An unknown result retains both the original routing/revisions and key, even after polling changes them.
  const locked = busy || Boolean(profiles && attempt.current);
  function reset() { attempt.current = null; setAck(false); setError(''); }
  async function generate(e: FormEvent) {
    e.preventDefault();
    if (busy || !ack || (!attempt.current && (!symbol || !fresh || !ready))) return;
    setBusy(true); setError('');
    if (!attempt.current) attempt.current = { input: { ...(structured ? {decision_schema:'assessment_v1' as const, decision_policy:mode === 'consensus' ? policy : 'unanimous', task_profile:researchProfile(interval)} : {}), symbol: symbol as SymbolName, analysis_mode: mode, model_profile_ids: [...selectedIds], expected_revisions: Object.fromEntries(selectedIds.map(id => [id, available.find(p => p.id === id)!.revision])), acknowledge_cost: true }, key: crypto.randomUUID() };
    try {
      const { input, key } = attempt.current;
      const result = profiles ? await api.analyzeDecision(input, key) : await api.generateDecision(input.symbol, input.expected_revisions.default, key);
      onSaved(`AI 分析已保存 · ${result.decision.id}。未执行订单，未经风控审批。`); reset();
    } catch (e) { setError(`${errorMessage(e)} 不会自动重发；再次提交同一请求使用原请求编号。`); }
    finally { setBusy(false); }
  }
  return <form className="manual-analysis" onSubmit={generate}><h3>手动生成分析</h3><p className="field-help">仅发送所选标的的公开报价与相关 PAPER 账户摘要。分析是建议记录，不是订单或风控批准。</p>
    <label className="field"><span>分析标的</span><select required value={symbol} disabled={locked} onChange={e => { setSymbol(e.target.value as SymbolName); reset(); }}><option value="">请选择标的</option><InstrumentOptions instruments={data.instruments} /></select></label>
    {profiles && <ModelSelection profiles={profiles} mode={mode} ids={ids} disabled={locked} onChange={(nextMode, nextIds) => { setMode(nextMode); setIds(nextIds); reset(); }} />}
    {profiles && <><label className="model-check"><input type="checkbox" checked={structured} disabled={locked} onChange={e=>{setStructured(e.target.checked);reset();}}/>使用结构化证据与交易计划</label>{structured && <><label className="field"><span>研究周期（32 根已收盘 K 线）</span><select value={interval} disabled={locked} onChange={e=>{setInterval(e.target.value);reset();}}>{['5m','15m','1h','4h'].map(v=><option key={v}>{v}</option>)}</select></label><p className="field-help">观察持有期 1 小时；报价、K 线为必需，新闻与盘口为辅助。缺少必需数据时不调用模型。</p>{mode === 'consensus' && <label className="field"><span>交叉决策政策</span><select value={policy} disabled={locked} onChange={e=>{setPolicy(e.target.value as 'unanimous'|'evidence_review');reset();}}><option value="unanimous">所有模型一致</option><option value="evidence_review">证据复核（最多增加一次裁决调用）</option></select></label>}</>}</>}
    <label className="model-check"><input type="checkbox" checked={ack} disabled={busy} onChange={e => setAck(e.target.checked)} />我确认调用已配置的模型服务，可能产生 API 费用</label>
    {selectionError && <p className="field-help">{selectionError}</p>}{!ready && !selectionError && <p className="field-help">所选模型尚不可用，请检查启用状态、密钥和连接配置；不会自动替换模型。</p>}
    {symbol && !fresh && !attempt.current && <Alert error>所选标的缺少有效报价，请刷新行情后再生成。</Alert>}
    {error && <><Alert error>{error} 新建请求可能再次产生费用；结果未知时请先核对原请求记录。</Alert>{attempt.current && <p className="field-help">原请求：{attempt.current.key} · {attempt.current.input.model_profile_ids.length} 个模型</p>}<button type="button" className="button" disabled={busy} onClick={reset}>新建分析请求</button></>}
    <button className="button primary" disabled={busy || !ack || (!attempt.current && (!symbol || !fresh || !ready))}>{busy ? '分析生成中…' : attempt.current ? '查询 / 重试同一请求' : '生成一次分析'}</button>
  </form>;
}
