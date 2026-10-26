import { Alert, Badge } from './ui';
import type { AnalysisMode, Dashboard, Decision, ModelProfile, ModelResult, Strategy } from './types';

export function profilesFromDashboard(data: Pick<Dashboard, 'model_profiles' | 'model_config'>): ModelProfile[] {
  if (data.model_profiles !== undefined) return data.model_profiles;
  return data.model_config ? [{ ...data.model_config, id: 'default', name: '默认模型' }] : [];
}
export const strategyProfileIds = (strategy: Strategy) => strategy.mode === 'RULE' ? [] : strategy.model_profile_ids ?? (strategy.mode === 'AI' ? ['default'] : []);
export function profileAvailability(profile?: ModelProfile) {
  if (!profile) return '配置不可用';
  if (!profile.enabled) return '未启用';
  if (!profile.model) return '未指定模型';
  if (!profile.local_mode && !profile.key_available) return '密钥不可用';
  return { CONNECTED: '目录已验证', CONFIGURED: '待验证', UNAVAILABLE: '连接不可用', NOT_CONFIGURED: '未配置' }[profile.connection_status];
}
export const profileReady = (p?: ModelProfile) => Boolean(p?.enabled && p.model && (p.local_mode || p.key_available) && p.connection_status !== 'NOT_CONFIGURED' && p.connection_status !== 'UNAVAILABLE');
export function routingError(mode: AnalysisMode, ids: string[], profiles: ModelProfile[], allowEmpty = false) {
  if (!ids.length && allowEmpty) return '';
  if (new Set(ids).size !== ids.length) return '请选择不同的模型配置。';
  if (mode === 'single' && ids.length !== 1) return '单模型分析需要选择 1 个模型配置。';
  if (mode === 'consensus' && (ids.length < 2 || ids.length > 3)) return '交叉分析需要选择 2 至 3 个不同模型配置。';
  if (ids.some(id => !profiles.some(p => p.id === id))) return '所选模型配置不可用，请重新选择；不会自动替换模型。';
  return '';
}

interface ModelSelectionProps {
  profiles: ModelProfile[]; mode: AnalysisMode; ids: string[];
  onChange: (mode: AnalysisMode, ids: string[]) => void;
  disabled?: boolean; recurring?: boolean;
}
export function ModelSelection({ profiles, mode, ids, onChange, disabled = false, recurring = false }: ModelSelectionProps) {
  const missing = ids.filter(id => !profiles.some(p => p.id === id));
  return <fieldset className="model-routing" disabled={disabled}>
    <legend>分析模型</legend>
    <label className="field">
      <span>分析方式</span>
      <select value={mode} onChange={e => {
        const next = e.target.value as AnalysisMode;
        onChange(next, next === 'single' ? ids.slice(0, 1) : ids);
      }}>
        <option value="single">单模型分析</option>
        <option value="consensus">交叉分析（可选，2–3 个模型）</option>
      </select>
    </label>
    {mode === 'single' ? <label className="field">
      <span>模型配置</span>
      <select value={ids[0] ?? ''} onChange={e => onChange(mode, e.target.value ? [e.target.value] : [])}>
        <option value="">请选择模型配置</option>
        {missing.map(id => <option key={id} value={id} disabled>配置不可用 · {id}</option>)}
        {profiles.map(p => <option key={p.id} value={p.id}>{p.name} · {p.model || '未指定模型'} · {profileAvailability(p)}</option>)}
      </select>
    </label> : <div className="profile-choices">
      {profiles.map(p => <label className="model-check profile-choice" key={p.id}>
        <input type="checkbox" checked={ids.includes(p.id)} disabled={!ids.includes(p.id) && ids.length >= 3}
          onChange={e => onChange(mode, e.target.checked ? [...ids, p.id] : ids.filter(id => id !== p.id))} />
        <span>{p.name}<small>{p.model || '未指定模型'} · {profileAvailability(p)}</small></span>
      </label>)}
      {missing.map(id => <label className="model-check profile-choice" key={id}>
        <input type="checkbox" checked onChange={() => onChange(mode, ids.filter(value => value !== id))} />
        <span>配置不可用 · {id}<small>取消选择后重新绑定模型</small></span>
      </label>)}
    </div>}
    {!profiles.length && <p className="field-help">尚无可选择的模型配置，请先在 AI 决策中心添加。</p>}
    <p className="field-help">每次分析调用 {ids.length} 个模型，各调用一次{recurring ? '，按策略间隔持续产生 API 费用' : '，可能分别产生 API 费用'}；不增加裁决模型调用。</p>
    {mode === 'consensus' && <p className="field-help">各模型使用同一份行情与账户快照。方向分歧或任一结果不完整时保持 HOLD；仅一致 BUY / SELL 才可能进入后续 PAPER 权限与风控检查。一致意见不代表风控通过。</p>}
    {!!missing.length && <Alert error>所选配置已不可用；系统不会自动改用默认模型。</Alert>}
  </fieldset>;
}

export function StrategyModelSummary({ strategy, profiles }: { strategy: Strategy; profiles: ModelProfile[] }) {
  if (strategy.mode === 'RULE') return <p className="field-help">用户规则 · 不调用模型</p>;
  const ids = strategyProfileIds(strategy);
  return <div className="strategy-models"><p className="field-help">{strategy.analysis_mode === 'consensus' ? '交叉分析' : '单模型分析'} · 每轮 {ids.length} 次调用{strategy.mode === 'HYBRID' ? ' · 仅保存绑定' : ''}</p>{ids.length ? ids.map(id => { const profile = profiles.find(p => p.id === id); return <p className="field-help" key={id}>{profile ? `${profile.name} · ${profile.model || '未指定模型'} · ${profileAvailability(profile)}` : `配置不可用 · ${id}（不会自动替换）`}</p>; }) : <p className="field-help">未选择模型</p>}</div>;
}

export const consensusLabel = (status?: Decision['consensus_status']) => status ? { AGREED: '方向一致', DISAGREED: '方向分歧 · HOLD', INCOMPLETE: '结果不完整 · HOLD' }[status] : '交叉分析结果待确认';
function ModelResultRow({ result }: { result: ModelResult }) {
  return <section className="model-outcome">
    <div className="flex between gap8">
      <h4>{result.profile_name}</h4>
      <Badge kind={result.status === 'FAILED' ? 'fail' : ''}>
        {result.status === 'SUCCEEDED' ? `已返回 · ${result.decision?.action ?? '未提供方向'}` : '调用失败'}
      </Badge>
    </div>
    <p className="field-help">{result.model} · 配置版本 {result.config_revision}</p>
    {result.status === 'SUCCEEDED' ? <>
      <p>{result.decision?.summary || '该模型未提供分析依据。'}</p>
      {!!result.decision?.counter_evidence?.length && <ul className="evidence-list">
        {result.decision.counter_evidence.map((evidence, i) => <li key={i}>{evidence}</li>)}
      </ul>}
      {result.decision?.invalidation && <p className="field-help">失效条件：{result.decision.invalidation}</p>}
    </> : <p>{result.error || '未取得完整结果，不能据此自动下单。'}</p>}
    <p className="field-help">Token：输入 {result.usage?.input_tokens ?? '未知'} · 输出 {result.usage?.output_tokens ?? '未知'}</p>
  </section>;
}

export function ModelOutcomes({ decision }: { decision: Decision }) {
  if (decision.analysis_mode !== 'consensus' && !decision.model_results?.length) {
    return decision.profile_name ? <div className="detail-block">
      <h3>分析模型</h3>
      <p>{decision.profile_name} · {decision.model}{decision.config_revision !== undefined ? ` · 配置版本 ${decision.config_revision}` : ''}</p>
    </div> : null;
  }
  const results = decision.model_results ?? [];
  const missing = (decision.model_profile_ids ?? []).filter(id => !results.some(result => result.profile_id === id));
  return <div className="detail-block">
    <h3>模型结果对照</h3>
    {decision.analysis_mode === 'consensus' && <Badge kind={decision.consensus_status === 'AGREED' ? '' : 'wait'}>{consensusLabel(decision.consensus_status)}</Badge>}
    <p className="field-help">一致仅表示方向相同，不代表建议正确或风控批准。各模型原始建议未经独立风控评估。</p>
    <div className="model-outcomes">
      {results.map(result => <ModelResultRow key={result.profile_id} result={result} />)}
      {missing.map(id => <section className="model-outcome" key={id}>
        <h4>{id}</h4><Badge kind="wait">结果缺失</Badge><p>未取得该模型结果，不能据此自动下单。</p>
      </section>)}
    </div>
  </div>;
}
