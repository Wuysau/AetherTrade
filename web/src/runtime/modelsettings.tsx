import { useEffect, useRef, useState, type FormEvent } from 'react';
import { SlidersHorizontal } from 'lucide-react';
import { ApiError, errorMessage, type RuntimeApi } from './api';
import { quoteIsFresh, time } from './format';
import { InstrumentOptions } from './instruments';
import { Alert, Badge, Panel } from './ui';
import type { Dashboard, ModelConfig, ModelFields, SymbolName } from './types';

export function modelStatus(data: Dashboard) {
  const status = data.model_config?.connection_status ?? data.services.find(s => /ai|model/i.test(s.id))?.status;
  return status === 'CONNECTED' ? '模型目录已验证' : status === 'CONFIGURED' ? '模型已配置 · 待验证' : status === 'UNAVAILABLE' ? '模型连接不可用' : '模型未配置';
}
const fields = (c: ModelConfig): ModelFields => ({ protocol: c.protocol, base_url: c.base_url, model: c.model, enabled: c.enabled, local_mode: c.local_mode, max_output_tokens: c.max_output_tokens, timeout_seconds: c.timeout_seconds, anthropic_version: c.anthropic_version });

export function ModelSettings({ data, api, onSaved }: { data: Dashboard; api: RuntimeApi; onSaved: (message: string) => void }) {
  const [config, setConfig] = useState(data.model_config);
  const [open, setOpen] = useState(false), [error, setError] = useState('');
  useEffect(() => { if (data.model_config) setConfig(previous => !previous || data.model_config!.revision >= previous.revision ? data.model_config : previous); }, [data.model_config]);
  useEffect(() => { if (data.model_config) return; let active = true; void api.modelConfig().then(c => { if (active) setConfig(c); }).catch(e => { if (active) setError(errorMessage(e)); }); return () => { active = false; }; }, [api, data.model_config]);
  return <Panel title="模型服务" caption="保存配置、验证目录与生成分析分别执行" className="model-panel" action={<button className="button primary" aria-expanded={open} onClick={() => setOpen(!open)}><SlidersHorizontal className="icon" />模型配置</button>}>
    <div className="panel-body"><div className="model-summary"><Badge kind={config?.connection_status === 'CONNECTED' ? 'pass' : 'wait'}>{modelStatus({ ...data, model_config: config })}</Badge><span>{config?.model || '尚未指定模型'}</span><span className="quiet">{config?.enabled ? '模型服务已启用' : '模型服务未启用'}</span></div>
      {error && <Alert error>{error}</Alert>}
      {open && (config ? <ConfigForm config={config} api={api} onConfig={setConfig} onSaved={onSaved} /> : <p>正在读取配置；读取失败时请刷新页面重试。</p>)}
      {config && <details className="manual-disclosure"><summary>手动分析一次（不自动下单）</summary><ManualAnalysis config={config} data={data} api={api} onSaved={onSaved} /></details>}
    </div>
  </Panel>;
}
export function ConfigForm({ config, api, onConfig, onSaved }: { config: ModelConfig; api: RuntimeApi; onConfig: (c: ModelConfig) => void; onSaved: (message: string) => void }) {
  const [draft, setDraft] = useState(() => fields(config)), [revision, setRevision] = useState(config.revision);
  const [key, setKey] = useState(''), [clearKey, setClearKey] = useState(false), [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState(''), [conflict, setConflict] = useState(false);
  useEffect(() => { if (!dirty && !busy && config.revision >= revision) { setDraft(fields(config)); setRevision(config.revision); } }, [config, dirty, busy, revision]);
  const update = <K extends keyof ModelFields>(name: K, value: ModelFields[K]) => { setDraft(d => ({ ...d, [name]: value, ...(name === 'protocol' && ['https://api.openai.com/v1', 'https://api.anthropic.com/v1'].includes(d.base_url.replace(/\/$/, '')) ? { base_url: value === 'anthropic' ? 'https://api.anthropic.com/v1' : 'https://api.openai.com/v1' } : {}) })); setDirty(true); setMessage(''); };
  const stale = conflict || config.revision > revision;
  const destinationChanged = config.base_url !== draft.base_url || config.protocol !== draft.protocol;
  async function reload() { setBusy('reload'); setError(''); try { const next = await api.modelConfig(); onConfig(next); setDraft(fields(next)); setRevision(next.revision); setKey(''); setClearKey(false); setDirty(false); setConflict(false); } catch (e) { setError(errorMessage(e)); } finally { setBusy(''); } }
  async function save(e: FormEvent) {
    e.preventDefault(); setError(''); setMessage('');
    if (destinationChanged && config.has_api_key && !key && !clearKey) { setError('更换地址或协议后，请重新输入密钥，或明确清除已保存的密钥。'); return; }
    setBusy('save');
    try { const next = await api.saveModelConfig({ ...draft, expected_revision: revision, ...(key ? { api_key: key } : {}), clear_api_key: clearKey }); onConfig(next); setRevision(next.revision); setDraft(fields(next)); setKey(''); setClearKey(false); setDirty(false); setConflict(false); setMessage('配置已保存。已启用的策略会在后续分析周期使用新配置，可能产生 API 费用。'); onSaved('模型配置已保存。'); }
    catch (e) { if (e instanceof ApiError && e.status === 409) setConflict(true); setError(errorMessage(e)); } finally { setBusy(''); }
  }
  async function test() { setBusy('test'); setError(''); setMessage(''); try { const result = await api.testModelConfig(revision); onConfig(result.config); setMessage(result.message); onSaved('模型目录测试已完成。'); } catch (e) { if (e instanceof ApiError && e.status === 409) setConflict(true); setError(errorMessage(e)); } finally { setBusy(''); } }
  return <form className="editor-form model-settings" onSubmit={save}><p className="field-help">保存后，已启用策略将在后续周期使用新配置。启用模型服务本身不会激活策略草稿。</p>
    <div className="form-grid"><label className="field"><span>API 协议</span><select value={draft.protocol} disabled={!!busy} onChange={e => update('protocol', e.target.value as ModelFields['protocol'])}><option value="openai_responses">OpenAI Responses</option><option value="openai_chat">OpenAI Chat 兼容</option><option value="anthropic">Claude / Anthropic</option></select></label><label className="field"><span>模型名称</span><input required maxLength={200} value={draft.model} disabled={!!busy} onChange={e => update('model', e.target.value)} placeholder="填写服务商提供的模型 ID" /></label></div>
    <label className="field"><span>API 基础地址</span><input aria-label="API 基础地址" type="url" required value={draft.base_url} disabled={!!busy} onChange={e => update('base_url', e.target.value)} /><span className="field-help">填写包含版本路径的基础地址，不含 /responses、/messages 或 /chat/completions。{draft.protocol === 'anthropic' ? 'Claude 官方地址：https://api.anthropic.com/v1' : 'OpenAI 官方地址：https://api.openai.com/v1'}；自定义地址切换协议时保留。</span></label>
    <label className="field"><span>API 密钥</span><input aria-label="API 密钥" type="password" autoComplete="new-password" value={key} disabled={!!busy || clearKey} onChange={e => { setKey(e.target.value); setDirty(true); }} placeholder={config.has_api_key ? '留空保留已保存的密钥' : '输入服务商密钥'} /><span className="field-help">{config.has_api_key ? config.key_available ? '密钥已保存，不会回显。' : '已保存密钥当前不可用，请重新输入。' : '尚未保存密钥。'} 密钥在本地加密保存，不在浏览器持久存储或回显原文。测试、手动分析或已启用策略的周期分析时，服务端会使用密钥向所配置的服务商认证。</span></label>
    <label className="model-check"><input type="checkbox" checked={clearKey} disabled={!!busy} onChange={e => { setClearKey(e.target.checked); setKey(''); setDirty(true); }} />清除已保存的密钥</label>
    <label className="model-check"><input type="checkbox" checked={draft.enabled} disabled={!!busy} onChange={e => update('enabled', e.target.checked)} />启用模型服务</label>
    <details><summary>高级设置</summary><div className="model-advanced"><label className="model-check"><input type="checkbox" checked={draft.local_mode} disabled={!!busy} onChange={e => update('local_mode', e.target.checked)} />本地兼容服务模式（可选）</label><p className="field-help">仅在你已有本机 API 服务时开启，允许 localhost / 127.0.0.1 / ::1 的 HTTP 地址。使用远程 OpenAI 或 Claude API 无需安装本地模型。</p><div className="form-grid"><label className="field"><span>请求超时（秒）</span><input type="number" min={5} max={120} required value={draft.timeout_seconds} disabled={!!busy} onChange={e => update('timeout_seconds', Number(e.target.value))} /></label><label className="field"><span>最大输出 token</span><input type="number" min={128} max={8192} required value={draft.max_output_tokens} disabled={!!busy} onChange={e => update('max_output_tokens', Number(e.target.value))} /></label></div>{draft.protocol === 'anthropic' && <label className="field"><span>Anthropic API 版本</span><input required value={draft.anthropic_version} disabled={!!busy} onChange={e => update('anthropic_version', e.target.value)} /></label>}</div></details>
    {stale && <Alert error>配置已在别处更新；你的输入已保留。重新载入会丢弃当前编辑。<button className="button small" type="button" disabled={!!busy} onClick={() => void reload()}>丢弃编辑并重新载入</button></Alert>}
    {error && <Alert error>{error}</Alert>}{message && <div role="status"><Alert>{message}</Alert></div>}
    <div className="action-strip"><button className="button primary" disabled={!!busy || stale}>{busy === 'save' ? '保存中…' : '保存配置'}</button><button type="button" className="button" disabled={!!busy || dirty || stale || !config.model} onClick={() => void test()}>{busy === 'test' ? '测试中…' : '测试连接'}</button><span className="quiet">版本 {revision} · 最近测试 {time(config.last_test_at)}</span></div><p className="field-help">测试仅查询模型目录 /models，不生成付费内容；目录验证不代表推理调用一定可用。修改后请先保存。</p>
  </form>;
}
export function ManualAnalysis({ config, data, api, onSaved }: { config: ModelConfig; data: Dashboard; api: RuntimeApi; onSaved: (message: string) => void }) {
  const [symbol, setSymbol] = useState<SymbolName | ''>(''), [ack, setAck] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const attempt = useRef<{ symbol: SymbolName; revision: number; key: string } | null>(null);
  const fresh = quoteIsFresh(data.market.quotes.find(q => q.symbol === symbol)) && ['CONNECTED', 'PARTIAL'].includes(data.market.status);
  async function generate(e: FormEvent) { e.preventDefault(); if (!symbol || !ack || !fresh || busy) return; setBusy(true); setError(''); if (!attempt.current) attempt.current = { symbol, revision: config.revision, key: crypto.randomUUID() }; try { const result = await api.generateDecision(attempt.current.symbol, attempt.current.revision, attempt.current.key); onSaved(`AI 分析已保存 · ${result.decision.id}。未执行订单，未经风控审批。`); attempt.current = null; setAck(false); } catch (e) { setError(`${errorMessage(e)} 不会自动重发；再次提交同一请求使用原请求编号。`); } finally { setBusy(false); } }
  return <form className="manual-analysis" onSubmit={generate}><h3>手动生成分析</h3><p className="field-help">仅发送所选标的的公开报价与相关 PAPER 账户摘要。分析是建议记录，不是订单或风控批准。</p><label className="field"><span>分析标的</span><select required value={symbol} disabled={busy} onChange={e => { setSymbol(e.target.value as SymbolName); attempt.current = null; setAck(false); setError(''); }}><option value="">请选择标的</option><InstrumentOptions instruments={data.instruments} /></select></label><label className="model-check"><input type="checkbox" checked={ack} disabled={busy} onChange={e => setAck(e.target.checked)} />我确认调用已配置的模型服务，可能产生 API 费用</label>{!config.enabled && <p className="field-help">请先在模型配置中启用模型服务并保存。</p>}{symbol && !fresh && <Alert error>所选标的缺少有效报价，请刷新行情后再生成。</Alert>}{error && <><Alert error>{error} 新建请求可能再次产生费用；结果未知时请先核对原请求记录。</Alert><button type="button" className="button" disabled={busy} onClick={() => { attempt.current = null; setAck(false); setError(''); }}>新建分析请求</button></>}<button className="button primary" disabled={busy || !ack || !symbol || !fresh || !config.enabled || (!config.local_mode && !config.key_available)}>{busy ? '分析生成中…' : attempt.current ? '查询 / 重试同一请求' : '生成一次分析'}</button></form>;
}
