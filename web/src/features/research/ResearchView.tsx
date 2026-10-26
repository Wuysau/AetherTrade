import { h, type ViewElement } from '../../app/App';
import type { ExperimentReport } from '../../contracts/generated';

export function ResearchView({ report }: { readonly report: ExperimentReport }): ViewElement {
  const { config, dataset, metrics } = report;
  const money = (items: typeof metrics.assumptions.fees): string => items.length
    ? items.map(item => `${item.amount} ${item.currency}`).join(' / ') : '缺失（不是零）';
  return h('section', { 'aria-label': '研究复盘' }, [
    h('h2', { text: `实验 ${report.run_id} · ${config.mode === 'REPLAY_OUTPUTS' ? '确定性回放原输出' : '重新推断'}` }),
    h('p', { text: `${dataset.environment} 数据 ${dataset.id} · ${dataset.hash} · ${dataset.start} — ${dataset.end}` }),
    h('p', { text: `构建 ${config.build_digest}；配置 ${config.config_hash}；模型 ${config.model_fingerprint}` }),
    h('ul', {}, [...dataset.evidence, ...config.proposal_refs, ...metrics.assumptions.evidence].map(ref =>
      h('li', { text: `${ref.id} · ${ref.hash} · ${ref.environment} · 来源 ${ref.source} · 可见于 ${ref.visible_at}` }))),
    h('p', { text: `费用 ${money(metrics.assumptions.fees)}；资金费 ${money(metrics.assumptions.funding)}；模型费 ${money(metrics.assumptions.model_costs)}` }),
    h('p', { text: `缺失：${[...report.missing, ...metrics.missing].join('；') || '未报告缺失（不等于数据完整）'}` }),
    h('ul', {}, metrics.metrics.map(metric => h('li', { text: `${metric.name}：${metric.value ?? '缺失'} ${metric.unit}` }))),
    h('p', { role: 'note', text: '可能的信息泄漏：模型可能知道历史结果。历史实验须按当时可见数据切片，前向模拟另行核验；实验结果不构成 LIVE 授权或安全资格。' }),
  ]);
}
