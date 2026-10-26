import { h, type ViewElement } from '../../app/App';
import type { RuleGraph, RuleCondition, RuleDraft, RuleArtifact, RuleEditor as RuleEditorPort } from '../../contracts/generated';

const decimal = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const inputs = ['quote.ask', 'quote.bid', 'quote.last', 'quote.mid', 'quote.spread'];
function validateCondition(node: RuleCondition, depth = 1, count = { value: 0 }): void {
  if (depth > 16) throw new Error('RULE_TOO_DEEP');
  if (++count.value > 128) throw new Error('RULE_TOO_LARGE');
  if (['ALL', 'ANY', 'NOT'].includes(node.operator)) {
    if (node.input_ref !== null || node.threshold !== null || !node.children.length ||
      (node.operator === 'NOT' && node.children.length !== 1)) throw new Error('INVALID_GROUP');
    node.children.forEach(child => validateCondition(child, depth + 1, count));
  } else if (!['GT', 'GE', 'LT', 'LE', 'EQ'].includes(node.operator) || !inputs.includes(node.input_ref ?? '') ||
    node.threshold === null || !decimal.test(node.threshold) || node.children.length) throw new Error('INVALID_COMPARISON');
}
function validate(graph: RuleGraph): void {
  validateCondition(graph.condition);
  if (!graph.rule_id.trim() || !/^[A-Za-z0-9_.:-]{1,128}$/.test(graph.instrument_ref) || !Number.isSafeInteger(graph.revision) || graph.revision < 1 ||
    ![graph.window_seconds, graph.cooldown_seconds].every(n => Number.isSafeInteger(n) && n >= 0) ||
    !['EDGE', 'CONTINUOUS'].includes(graph.trigger) || !['HOLD', 'REJECT'].includes(graph.missing_data) ||
    !['QUOTE_NOTIONAL', 'BASE_QUANTITY'].includes(graph.sizing) || typeof graph.closed_bar_only !== 'boolean' ||
    !decimal.test(graph.target_value) || (graph.closed_bar_only && graph.window_seconds === 0) ||
    !graph.exit_priority.length || graph.exit_priority.some(x => !x.trim()) ||
    new Set(graph.exit_priority).size !== graph.exit_priority.length) throw new Error('INVALID_GRAPH');
}
export function conditionMeaning(node: RuleCondition): string {
  if (node.operator === 'NOT') return `NOT (${conditionMeaning(node.children[0])})`;
  if (node.operator === 'ALL' || node.operator === 'ANY') return `(${node.children.map(conditionMeaning).join(node.operator === 'ALL' ? ' AND ' : ' OR ')})`;
  return `${node.input_ref} ${node.operator} ${node.threshold}`;
}

/** Local draft editor only; compilation, hashing and release remain server responsibilities. */
export class RuleEditor implements RuleEditorPort {
  private graph: RuleGraph;
  private confirmed = false;
  private ambiguities: ReadonlyArray<string> = [];
  private source = 'VISUAL';
  constructor(graph: RuleGraph) { validate(graph); this.graph = structuredClone(graph); }
  toGraph(): RuleGraph { return structuredClone(this.graph); }
  private updateGraph(graph: RuleGraph): void {
    validate(graph); this.graph = structuredClone(graph); this.confirmed = false;
  }
  fromGraph(graph: RuleGraph): void {
    this.updateGraph(graph);
    this.ambiguities = []; this.source = 'VISUAL';
  }
  fromDraft(draft: RuleDraft): void {
    if (!draft.graph) throw new Error('RULE_DRAFT_HAS_NO_GRAPH');
    this.fromGraph(draft.graph); this.ambiguities = [...draft.ambiguities]; this.source = draft.source_ref.id;
  }
  confirmMeaning(): void {
    if (this.ambiguities.length) throw new Error('UNRESOLVED_AMBIGUITIES');
    this.confirmed = true;
  }
  save(): RuleGraph {
    if (!this.confirmed) throw new Error('EXACT_MEANING_CONFIRMATION_REQUIRED');
    return this.toGraph();
  }
  /** Replace a tree node without flattening AND/OR, NOT, or decimal strings. */
  replaceCondition(path: ReadonlyArray<number>, replacement: RuleCondition): void {
    const replace = (node: RuleCondition, remaining: ReadonlyArray<number>): RuleCondition => {
      if (!remaining.length) return structuredClone(replacement);
      const [index, ...rest] = remaining;
      if (!Number.isInteger(index) || index < 0 || index >= node.children.length) throw new Error('INVALID_NODE_PATH');
      return { ...node, children: node.children.map((child, i) => i === index ? replace(child, rest) : child) };
    };
    this.updateGraph({ ...this.graph, condition: replace(this.graph.condition, path) });
  }
  render(onChange: () => void = () => undefined, artifact?: RuleArtifact): ViewElement {
    const g = this.graph;
    const update = (patch: Partial<RuleGraph>): void => { this.updateGraph({ ...this.graph, ...patch }); onChange(); };
    const select = (label: string, value: string, values: ReadonlyArray<string>, change: (value: string) => void): ViewElement =>
      h('select', { 'aria-label': label, value, onChange: (event: { target: { value: string } }) => change(event.target.value) },
        values.map(item => h('option', { value: item, text: item })));
    const tree = (node: RuleCondition, path: ReadonlyArray<number>): ViewElement => h('li', {}, [
      h('span', { text: conditionMeaning(node) }),
      h('select', { 'aria-label': `条件 ${path.join('.') || 'root'} 运算符`, value: node.operator,
        onChange: (event: { target: { value: string } }) => {
          const operator = event.target.value as RuleCondition['operator'];
          this.replaceCondition(path, { ...node, operator }); onChange();
        },
      }, (node.children.length ? (node.operator === 'NOT' ? ['NOT'] : ['ALL', 'ANY']) : ['GT', 'GE', 'LT', 'LE', 'EQ'])
        .map(value => h('option', { value, text: value }))),
      ...(node.children.length ? [h('ul', {}, node.children.map((child, i) => tree(child, [...path, i])))] : [
        select(`输入 ${path.join('.')}`, node.input_ref ?? '', inputs, value => {
          this.replaceCondition(path, { ...node, input_ref: value }); onChange();
        }),
        h('input', { 'aria-label': `阈值 ${path.join('.')}`, value: node.threshold, onChange: (event: { target: { value: string } }) => {
          this.replaceCondition(path, { ...node, threshold: event.target.value }); onChange();
        } }),
      ]),
    ]);
    return h('section', { 'aria-label': '规则可视化' }, [
      h('h2', { text: `规则 ${g.rule_id} · 修订 ${g.revision} · 来源 ${this.source}` }),
      h('ul', {}, [tree(g.condition, [])]),
      select('触发', g.trigger, ['EDGE', 'CONTINUOUS'], value => update({ trigger: value as RuleGraph['trigger'] })),
      select('缺失数据', g.missing_data, ['HOLD', 'REJECT'], value => update({ missing_data: value as RuleGraph['missing_data'] })),
      select('仓位算法', g.sizing, ['QUOTE_NOTIONAL', 'BASE_QUANTITY'], value => update({ sizing: value as RuleGraph['sizing'] })),
      ...(['window_seconds', 'cooldown_seconds', 'revision'] as const).map(field => h('input', {
        'aria-label': field, type: 'number', value: String(g[field]), min: field === 'revision' ? 1 : 0,
        onChange: (event: { target: { value: string } }) => update({ [field]: Number(event.target.value) }),
      })),
      ...(['instrument_ref', 'target_value'] as const).map(field => h('input', {
        'aria-label': field, value: g[field], onChange: (event: { target: { value: string } }) => update({ [field]: event.target.value }),
      })),
      h('label', {}, [h('input', { type: 'checkbox', checked: g.closed_bar_only,
        onChange: (event: { target: { checked: boolean } }) => update({ closed_bar_only: event.target.checked }) }), '仅使用已收盘 bar']),
      h('input', { 'aria-label': '退出优先级（逗号分隔）', value: g.exit_priority.join(','),
        onChange: (event: { target: { value: string } }) => update({ exit_priority: event.target.value.split(',').map(item => item.trim()) }) }),
      h('p', { text: `${g.instrument_ref}；触发 ${g.trigger}；窗口 ${g.window_seconds}s；仅收盘 ${g.closed_bar_only}；冷却 ${g.cooldown_seconds}s；仓位 ${g.sizing} ${g.target_value}；退出顺序 ${g.exit_priority.join(' → ')}；缺失数据 ${g.missing_data}` }),
      h('p', { text: `歧义：${this.ambiguities.join('；') || '无'}。自然语言只导入固定规则，不逐轮重释。` }),
      h('p', { text: artifact ? `Python 制品 ${artifact.artifact_ref} · hash ${artifact.hash} · 依赖 ${artifact.dependency_lock_hash}；须隔离评估与授权发布` : 'Python 规则须导入锁定制品；本页不执行代码。' }),
      h('button', { type: 'button', disabled: !!this.ambiguities.length, text: '确认上述确切含义',
        onClick: () => { this.confirmMeaning(); onChange(); } }),
      h('p', { role: 'status', text: this.confirmed ? '已确认本地草稿；仍须编译及授权发布' : '尚未确认，不能保存为已确认草稿' }),
    ]);
  }
}
