import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Check, CircleDot, Eye, History, RotateCcw, Save, Sparkles } from 'lucide-react';
import { ApiError, errorMessage, type RuntimeApi } from './api';
import { live, t, useLocale } from './i18n';
import { Alert, Badge, Dialog, Empty, Intro, Loading, Panel } from './ui';

export interface PromptLayer {
  layer_id: string;
  title: string;
  role: string;
  version: string;
  template: string;
  default_template: string;
  customized: boolean;
  variables: string[];
  template_hash: string;
  default_hash: string;
  updated_at: string | null;
}

export interface PromptFrameworkInfo {
  version: string;
  hash: string;
  layers: Record<string, string>;
  customized: string[];
}

export interface PromptLayerResponse {
  layers: PromptLayer[];
  framework: PromptFrameworkInfo;
  available_variables?: string[];
  max_length?: number;
  layer?: PromptLayer;
}

const LAYER_COPY = live(() => ({
  P0: { name: t("系统准则"), note: t("模型的角色边界与事实口径") },
  P1: { name: t("交易理念"), note: t("机会与风险的取舍原则") },
  P2: { name: t("策略档案"), note: t("当前策略、标的与只读限额") },
  P3: { name: t("市场研究协议"), note: t("行情快照的使用与证据引用") },
  P4: { name: t("记忆上下文协议"), note: t("与历史论点和计划的对比") },
  P5: { name: t("决策协议"), note: t("可选动作与受限条件") },
  P6: { name: t("输出契约"), note: t("输出结构与禁止项") },
} as Record<string, { name: string; note: string }>));

const VARIABLE_COPY = live(() => ({
  strategy_id: t("策略编号"),
  instrument: t("交易标的"),
  product: t("产品类型"),
  strategy_version: t("策略版本"),
  decision_mode: t("决策模式"),
  authorization_summary: t("授权摘要"),
  risk_limits: t("风险限额"),
  memory_completeness: t("记忆完整度"),
  memory_conflicts: t("记忆冲突"),
  memory_missing: t("记忆缺失"),
  output_schema: t("输出结构"),
} as Record<string, string>));



function checkTemplate(template: string, allowed: Set<string>, maxLength: number): string {
  const text = template.trim();
  if (text.length < 24) return t("内容过短，至少需要 24 个字符。");
  if (text.length > maxLength) return t("内容超过长度上限。");
  let rest = template;
  const placeholder = /\{([^{}]*)\}/g;
  for (const match of template.matchAll(placeholder)) {
    if (!allowed.has(match[1])) return t("未知占位符：") + match[1];
    rest = rest.replace(match[0], '');
  }
  if (/[{}]/.test(rest)) return t("花括号不匹配。占位符请使用 {变量名}。");
  return '';
}

function shortHash(value: string | null | undefined) {
  return value ? value.slice(0, 10) : '—';
}

export function Prompts({ api }: { api: RuntimeApi }) {
  useLocale();
  const [layers, setLayers] = useState<PromptLayer[]>([]);
  const [framework, setFramework] = useState<PromptFrameworkInfo | null>(null);
  const [available, setAvailable] = useState<string[]>([]);
  const [maxLength, setMaxLength] = useState(12000);
  const [selected, setSelected] = useState('P0');
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [preview, setPreview] = useState<{ text: string; hash: string } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const area = useRef<HTMLTextAreaElement>(null);

  const adopt = useCallback((payload: PromptLayerResponse) => {
    if (payload.layers) setLayers(payload.layers);
    if (payload.framework) setFramework(payload.framework);
    if (payload.available_variables) setAvailable(payload.available_variables);
    if (payload.max_length) setMaxLength(payload.max_length);
  }, []);

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    setLoading(true);
    api.promptLayers(controller.signal)
      .then(payload => { if (!disposed) { adopt(payload); setError(''); } })
      .catch(err => { if (!disposed) setError(errorMessage(err)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; controller.abort(); };
  }, [api, adopt]);

  const layer = layers.find(item => item.layer_id === selected) ?? layers[0];
  const allowed = useMemo(() => new Set(available), [available]);
  const draft = layer ? drafts[layer.layer_id] ?? layer.template : '';
  const dirty = Boolean(layer && draft !== layer.template);
  const validation = layer ? checkTemplate(draft, allowed, maxLength) : '';

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(''), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  function edit(value: string) {
    if (!layer) return;
    setDrafts(previous => ({ ...previous, [layer.layer_id]: value }));
    setPreview(null);
  }

  function choose(layerId: string) {
    setSelected(layerId);
    setPreview(null);
    setPreviewError('');
  }

  function insert(variable: string) {
    const node = area.current;
    const token = `{${variable}}`;
    if (!node) { edit(draft + token); return; }
    const start = node.selectionStart ?? draft.length;
    const end = node.selectionEnd ?? draft.length;
    edit(draft.slice(0, start) + token + draft.slice(end));
    window.requestAnimationFrame(() => {
      node.focus();
      node.setSelectionRange(start + token.length, start + token.length);
    });
  }

  async function save() {
    if (!layer || validation || !dirty) return;
    setSaving(true); setError('');
    try {
      const payload = await api.savePromptLayer(layer.layer_id, draft.trim(), layer.version);
      adopt(payload);
      setDrafts(previous => { const next = { ...previous }; delete next[layer.layer_id]; return next; });
      setPreview(null);
      setNotice(t("已保存。下一次分析将使用新版本。"));
    } catch (err) {
      if (err instanceof ApiError && err.code === 'PROMPT_TEMPLATE_CONFLICT') {
        setError(err.message);
        const payload = await api.promptLayers().catch(() => null);
        if (payload) adopt(payload);
      } else setError(errorMessage(err));
    } finally { setSaving(false); }
  }

  async function reset() {
    if (!layer) return;
    setResetting(true); setError('');
    try {
      const payload = await api.resetPromptLayer(layer.layer_id, layer.version);
      adopt(payload);
      setDrafts(previous => { const next = { ...previous }; delete next[layer.layer_id]; return next; });
      setPreview(null); setConfirmReset(false);
      setNotice(t("已恢复为默认规则。"));
    } catch (err) { setError(errorMessage(err)); setConfirmReset(false); }
    finally { setResetting(false); }
  }

  async function showPreview() {
    if (!layer) return;
    setPreviewing(true); setPreviewError('');
    try {
      const result = await api.previewPromptLayer(layer.layer_id, draft);
      setPreview({ text: result.text, hash: result.template_hash });
    } catch (err) { setPreviewError(errorMessage(err)); }
    finally { setPreviewing(false); }
  }

  useEffect(() => {
    function key(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        void save();
      }
    }
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  });

  const customizedCount = layers.filter(item => item.customized).length;

  return <>
    <Intro title={t("决策提示词")} subtitle={t("维护 P0–P6 规则。每次分析由服务端填入当前策略、风险限制与行情后发送给模型。")}>
      <span className="chip ghost"><History className="icon sm" />{t("框架")} {framework?.version ?? '—'}</span>
      <span className={`chip ${customizedCount ? 'blue' : 'ghost'}`}>{customizedCount ? `${customizedCount} / 7 · ${t("已自定义")}` : t("全部为默认规则")}</span>
    </Intro>
    {error && <div className="global-message"><Alert error onRetry={() => { setError(''); void api.promptLayers().then(adopt).catch(err => setError(errorMessage(err))); }}>{error}</Alert></div>}
    {notice && <div className="prompt-notice" role="status"><Check className="icon sm" />{notice}</div>}
    {loading && !layers.length ? <section className="panel prompt-loading"><Loading text={t("正在读取提示词…")} /></section>
      : !layers.length ? <section className="panel"><Empty title={t("提示词暂不可用")} text={t("请刷新重试。")} compact /></section>
        : <div className="prompt-workbench">
          <aside className="panel prompt-rail">
            <div className="prompt-rail-head"><span className="prompt-eyebrow">{t("规则层")}</span><span className="prompt-rail-count">P0–P6</span></div>
            <div className="prompt-layer-list" role="listbox" aria-label={t("规则层")}>
              {layers.map(item => {
                const copy = LAYER_COPY[item.layer_id] ?? { name: item.title, note: '' };
                const edited = Boolean(drafts[item.layer_id] && drafts[item.layer_id] !== item.template);
                return <button key={item.layer_id} role="option" aria-selected={item.layer_id === selected}
                  className={`prompt-layer ${item.layer_id === selected ? 'active' : ''}`} onClick={() => choose(item.layer_id)}>
                  <span className="prompt-layer-id">{item.layer_id}</span>
                  <span className="prompt-layer-text">
                    <strong>{copy.name}</strong>
                    <small>{copy.note}</small>
                  </span>
                  <span className="prompt-layer-flags">
                    {edited && <CircleDot className="icon sm prompt-dirty" aria-label={t("未保存")} />}
                    {item.customized && <span className="prompt-custom-dot" aria-label={t("已自定义")} />}
                    <span className="prompt-version">{item.version}</span>
                  </span>
                </button>;
              })}
            </div>
            <div className="prompt-rail-foot">
              <div><span>{t("框架哈希")}</span><code title={framework?.hash}>{shortHash(framework?.hash)}</code></div>
              <div><span>{t("自定义层")}</span><strong>{customizedCount} / 7</strong></div>
            </div>
          </aside>

          {layer && <section className="panel prompt-editor">
            <header className="prompt-editor-head">
              <div>
                <div className="prompt-eyebrow">{layer.layer_id} · {t("规则层")}</div>
                <h2>{LAYER_COPY[layer.layer_id]?.name ?? layer.title}</h2>
                <p>{LAYER_COPY[layer.layer_id]?.note}</p>
              </div>
              <div className="prompt-editor-meta">
                {layer.customized ? <Badge kind="pass">{t("自定义")}</Badge> : <Badge>{t("默认")}</Badge>}
                <span className="prompt-version">{layer.version}</span>
                <code title={layer.template_hash}>{shortHash(layer.template_hash)}</code>
              </div>
            </header>

            <div className={`prompt-split ${preview ? 'with-preview' : ''}`}>
              <div className="prompt-compose">
                <div className="prompt-compose-head">
                  <span>{t("规则内容")}</span>
                  <span className={`prompt-count ${draft.trim().length > maxLength ? 'over' : ''}`}>{draft.trim().length} / {maxLength}</span>
                </div>
                <textarea ref={area} className="prompt-textarea" spellCheck={false} value={draft}
                  onChange={event => edit(event.target.value)}
                  aria-label={`${LAYER_COPY[layer.layer_id]?.name ?? layer.title} ${t("规则内容")}`} />
                {validation && draft !== layer.template && <p className="prompt-error"><AlertTriangle className="icon sm" />{validation}</p>}
                <div className="prompt-variables">
                  <span className="prompt-variables-label"><Sparkles className="icon sm" />{t("插入变量")}</span>
                  <div className="prompt-variable-chips">
                    {(available.length ? available : Object.keys(VARIABLE_COPY)).map(variable =>
                      <button key={variable} type="button" className="prompt-variable" title={`{${variable}}`} onClick={() => insert(variable)}>
                        <span>{VARIABLE_COPY[variable] ?? variable}</span><code>{`{${variable}}`}</code>
                      </button>)}
                  </div>
                </div>
              </div>

              {preview && <div className="prompt-preview">
                <div className="prompt-compose-head"><span>{t("渲染预览")}</span><code>{shortHash(preview.hash)}</code></div>
                <pre className="prompt-preview-text">{preview.text}</pre>
                <p className="prompt-preview-note">{t("示例值仅用于检查结构与占位符；实际分析使用本轮真实数据。")}</p>
              </div>}
            </div>

            {previewError && <div className="prompt-editor-message"><Alert error>{previewError}</Alert></div>}

            <footer className="prompt-editor-foot">
              <div className="prompt-foot-left">
                <button className="button small" onClick={() => void showPreview()} disabled={previewing || Boolean(validation)}>
                  <Eye className="icon" />{previewing ? t("生成中…") : preview ? t("刷新预览") : t("预览")}
                </button>
                {dirty && <span className="prompt-unsaved"><CircleDot className="icon sm" />{t("有未保存的更改")}</span>}
              </div>
              <div className="prompt-foot-right">
                <button className="button small" onClick={() => setConfirmReset(true)} disabled={saving || resetting || !layer.customized && !dirty}>
                  <RotateCcw className="icon" />{t("恢复默认")}
                </button>
                <button className="button primary small" onClick={() => void save()} disabled={saving || !dirty || Boolean(validation)}>
                  <Save className="icon" />{saving ? t("保存中…") : t("保存并生效")}
                </button>
              </div>
            </footer>
          </section>}
        </div>}

    {confirmReset && layer && <Dialog title={t("恢复默认规则")} onClose={() => setConfirmReset(false)} busy={resetting}>
      <p>{t("将 P")}{layer.layer_id.slice(1)}{t(" 层恢复为随版本发布的默认文本。当前自定义内容会保留在历史版本中。")}</p>
      <div className="dialog-buttons">
        <button className="button" onClick={() => setConfirmReset(false)} disabled={resetting}>{t("取消")}</button>
        <button className="button danger" onClick={() => void reset()} disabled={resetting}>{resetting ? t("恢复中…") : t("恢复默认")}</button>
      </div>
    </Dialog>}
  </>;
}
