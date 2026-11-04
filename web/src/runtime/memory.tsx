import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, BrainCircuit, RefreshCw } from 'lucide-react';
import { errorMessage, type RuntimeApi } from './api';
import { executionLabel } from './automation';
import { MODE_NAMES } from './forms';
import { time } from './format';
import { Alert, Badge, Empty, Intro, Loading, Panel } from './ui';
import { live, t } from './i18n';
import type {
  Dashboard, MemoryCheckpoint, MemoryCompleteness, MemoryDecisionRef, MemoryEvent, MemoryHealth,
  MemoryKnowledge, MemoryOverview, MemoryPlan, MemoryThesis, Strategy,
} from './types';

type Tone = 'pass' | 'fail' | 'wait' | '';
const pick = <T,>(map: Record<string, T>, key: string | null | undefined, fallback: T): T =>
  (key && map[key] !== undefined ? map[key] : fallback);

const COMPLETENESS: Record<MemoryCompleteness, { label: string; kind: Tone }> = live(() => ({
  COMPLETE: { label: t("完整"), kind: 'pass' },
  PARTIAL: { label: t("部分缺失"), kind: 'wait' },
  CONTEXT_INCOMPLETE: { label: t("上下文不完整"), kind: 'fail' },
}));
const THESIS: Record<string, { label: string; kind: Tone }> = live(() => ({
  ACTIVE: { label: t("有效"), kind: 'pass' }, WEAKENED: { label: t("减弱"), kind: 'wait' },
  INVALIDATED: { label: t("已失效"), kind: 'fail' }, EXPIRED: { label: t("已过期"), kind: 'wait' },
  SUPERSEDED: { label: t("已替代"), kind: 'wait' },
}));
const PLAN: Record<string, { label: string; kind: Tone }> = live(() => ({
  PROPOSED: { label: t("已提议"), kind: 'wait' }, ACCEPTED: { label: t("已接受"), kind: '' },
  ACTIVE: { label: t("执行中"), kind: 'pass' }, REVISED: { label: t("已修订"), kind: 'wait' },
  CANCEL_PENDING: { label: t("待撤销"), kind: 'wait' }, COMPLETED: { label: t("已完成"), kind: '' },
  EXPIRED: { label: t("已过期"), kind: 'wait' }, REJECTED: { label: t("已拒绝"), kind: 'fail' },
}));
const KNOWLEDGE: Record<string, { label: string; kind: Tone }> = live(() => ({
  PROPOSED: { label: t("待验证"), kind: 'wait' }, VALIDATED: { label: t("已验证"), kind: 'pass' },
  REJECTED: { label: t("已拒绝"), kind: 'fail' }, EXPIRED: { label: t("已过期"), kind: 'wait' },
}));
const DECISION: Record<string, { label: string; kind: Tone }> = live(() => ({
  DECIDED: { label: t("有效分析"), kind: 'pass' }, DATA_BLOCKED: { label: t("数据不足"), kind: 'wait' },
  MODEL_ERROR: { label: t("模型失败"), kind: 'fail' }, REVIEW_REQUIRED: { label: t("待证据复核"), kind: 'wait' },
  EXPIRED: { label: t("计划已过期"), kind: 'wait' }, INVALID_PLAN: { label: t("计划无效"), kind: 'fail' },
  CAPABILITY_BLOCKED: { label: t("能力未就绪"), kind: 'wait' }, REPLAN_REQUIRED: { label: t("需重新规划"), kind: 'wait' },
  LEGACY_ADVICE: { label: t("历史建议"), kind: '' }, REJECTED: { label: t("已拒绝"), kind: 'fail' },
}));
const ACTION: Record<string, string> = live(() => ({
  BUY: t("买入"), SELL: t("卖出"), HOLD: t("观望"), SET_TARGET: t("设定目标"),
  UPDATE_EXIT: t("更新退出"), REDUCE: t("减仓"), EXIT: t("退出"),
}));
const EVENT: Record<string, string> = live(() => ({
  STRATEGY_CREATED: t("策略创建"), STRATEGY_CONFIG_CHANGED: t("策略配置变更"), STRATEGY_PAUSED: t("策略暂停"),
  STRATEGY_RESUMED: t("策略恢复"), THESIS_CREATED: t("论点创建"), THESIS_UPDATED: t("论点更新"),
  THESIS_INVALIDATED: t("论点失效"), THESIS_EXPIRED: t("论点过期"), PLAN_PROPOSED: t("计划提议"),
  PLAN_ACCEPTED: t("计划接受"), PLAN_ACTIVATED: t("计划激活"), PLAN_REVISED: t("计划修订"),
  PLAN_CANCEL_PENDING: t("计划待撤销"), PLAN_REJECTED: t("计划拒绝"), PLAN_EXPIRED: t("计划过期"),
  PLAN_COMPLETED: t("计划完成"), WATCH_CONDITION_TRIGGERED: t("复查条件触发"), MANUAL_INTERVENTION: t("人工干预"),
  DECISION_RECORDED: t("决策记录"), EXECUTION_FACT_LINKED: t("执行事实关联"), MODEL_SWITCHED: t("模型切换"),
  KNOWLEDGE_PROPOSED: t("知识提议"), KNOWLEDGE_VALIDATED: t("知识验证"), KNOWLEDGE_REJECTED: t("知识拒绝"),
  KNOWLEDGE_EXPIRED: t("知识过期"), CHECKPOINT_RECORDED: t("检查点记录"), PROJECTION_REBUILT: t("投影重建"),
}));
const SOURCE: Record<string, string> = live(() => ({
  MODEL: t("模型"), CONTROL: t("控制面"), EXECUTION_LEDGER: t("执行账本"), MANUAL: t("人工"), SYSTEM: t("系统"),
}));
const CONFLICT: Record<string, string> = live(() => ({
  EXECUTION_FACT_UNKNOWN: t("执行事实未知"), EXECUTION_FACT_STALE: t("执行事实过期"),
  MARKET_DATA_BLOCKED: t("行情数据阻断"), MARKET_SNAPSHOT_MISSING: t("缺少行情快照"),
  MARKET_SNAPSHOT_STALE: t("行情快照过期"), MARKET_FRESHNESS_UNKNOWN: t("行情时效未知"),
  EXPOSURE_WITHOUT_ACTIVE_PLAN: t("有敞口但无活跃计划"), STRATEGY_PAUSED: t("策略已暂停"),
  ACTIVE_PLAN_TERMINAL: t("活跃计划已终止"), ACTIVE_PLAN_EXPIRED: t("活跃计划已过期"),
  ACTIVE_THESIS_TERMINAL: t("活跃论点已终止"), ACTIVE_THESIS_UNAVAILABLE: t("活跃论点不可用"),
  ACTIVE_PLAN_UNAVAILABLE: t("活跃计划不可用"), CONTEXT_SCOPE_MISMATCH: t("作用域不匹配"),
  CONTEXT_STATE_SCOPE_MISMATCH: t("状态作用域不匹配"), CONTEXT_STATE_REVISION_CHANGED: t("状态修订已变化"),
  CONTEXT_STRATEGY_VERSION_CHANGED: t("策略版本已变化"), CONTEXT_AS_OF_BEFORE_SNAPSHOT: t("上下文早于快照"),
  MARKET_SNAPSHOT_TIME_INVALID: t("快照时间无效"),
}));
const MISSING: Record<string, string> = live(() => ({
  market_snapshot: t("行情快照"), execution_facts: t("执行事实"), quote: t("有效报价"),
  'identity.scope': t("作用域"), 'identity.state_revision': t("状态修订"),
  'identity.strategy_version': t("策略版本"), 'identity.status': t("策略状态"),
  ACTIVE_PLAN_REQUIRED_WITH_EXPOSURE: t("有敞口需活跃计划"), THESIS_NOT_ACTIVE: t("论点非活跃"),
  THESIS_EXPIRED: t("论点已过期"), PLAN_TERMINAL: t("计划已终止"), PLAN_EXPIRED: t("计划已过期"),
  MARKET_SNAPSHOT_STALE: t("行情快照过期"),
}));
const PROVENANCE: Record<string, string> = live(() => ({
  MODEL_PROPOSAL: t("模型提议"), DETERMINISTIC_STATISTICS: t("确定性统计"), USER_REVIEW: t("人工复核"),
}));
const EXECUTION_TONE: Record<string, Tone> = {
  FILLED: 'pass', PARTIALLY_FILLED: 'pass', SUBMITTED: 'wait', PENDING: 'wait',
  UNKNOWN: 'fail', BLOCKED: 'fail', ERROR: 'fail', SKIPPED: 'wait', NOT_ENABLED: 'wait',
};

const shortId = (value: string | null | undefined, head = 10, tail = 6) => {
  if (!value) return t("未记录");
  return value.length <= head + tail + 1 ? value : `${value.slice(0, head)}…${value.slice(-tail)}`;
};
const codeLabel = (map: Record<string, string>, value: string) => map[value] ?? value;
const versions = (value: Record<string, string> | null | undefined) => {
  const entries = Object.entries(value ?? {}).filter(([, item]) => Boolean(item));
  return entries.length ? entries.map(([key, item]) => `${key} ${item}`).join(' · ') : t("未记录");
};
const diffTime = (value: string | null | undefined) => {
  if (!value || !Number.isFinite(Date.parse(value))) return t("未指定");
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value));
};
const planTarget = (plan: MemoryPlan) => {
  const value = plan.target_value ?? '—';
  return `${plan.target_side} · ${value} ${plan.target_sizing === 'QUOTE_NOTIONAL' ? t("计价币名义") : t("基础数量")}`;
};
const stopPrice = (plan: MemoryPlan) => {
  const value = plan.exit_plan?.stop_price;
  return typeof value === 'string' && value ? value : t("未指定");
};

function StatusBadge({ map, value, fallback }: { map: Record<string, { label: string; kind: Tone }>; value: string | null | undefined; fallback: string }) {
  const item = value ? map[value] : undefined;
  return <Badge kind={item?.kind ?? ''}>{item?.label ?? (value || fallback)}</Badge>;
}

function Fact({ label, children, mono = false }: { label: string; children: ReactNode; mono?: boolean }) {
  return <div><span>{label}</span><strong className={mono ? 'memory-code' : ''}>{children}</strong></div>;
}

function Chips({ values, map, empty }: { values: string[]; map?: Record<string, string>; empty: string }) {
  if (!values.length) return <p className="memory-none">{empty}</p>;
  return <div className="memory-chips">{values.map(value => <span className="memory-chip" key={value} title={value}>{map ? codeLabel(map, value) : value}</span>)}</div>;
}

function ThesisBlock({ thesis }: { thesis: MemoryThesis | null }) {
  return <div className="memory-block">
    <div className="memory-block-head"><span className="memory-eyebrow">{t("活跃论点")}</span><StatusBadge map={THESIS} value={thesis?.status} fallback={t("未建立")} /></div>
    {thesis ? <>
      <p className="memory-statement">{thesis.statement}</p>
      <div className="memory-facts">
        <Fact label={t("论点编号")} mono>{shortId(thesis.thesis_id)}</Fact>
        <Fact label={t("修订")}>{`r${thesis.revision}`}</Fact>
        <Fact label={t("来源")}>{codeLabel(SOURCE, thesis.source)}</Fact>
        <Fact label={t("更新")}>{time(thesis.updated_at, true)}</Fact>
      </div>
      <div className="memory-subhead">{t("证据引用")} <span>{thesis.evidence_refs.length}  {t("项")}</span></div>
      <Chips values={thesis.evidence_refs.slice(0, 8)} empty={t("该论点未记录证据引用。")} />
      {thesis.invalidation_conditions.length > 0 && <>
        <div className="memory-subhead">{t("失效条件")}</div>
        <ul className="memory-list">{thesis.invalidation_conditions.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
      </>}
    </> : <p className="memory-none">{t("尚无活跃论点。一次有效分析通过证据校验后，才会记录论点与引用。")}</p>}
  </div>;
}

function PlanBlock({ plan }: { plan: MemoryPlan | null }) {
  return <div className="memory-block">
    <div className="memory-block-head"><span className="memory-eyebrow">{t("活跃计划")}</span><StatusBadge map={PLAN} value={plan?.status} fallback={t("未建立")} /></div>
    {plan ? <>
      <div className="memory-facts">
        <Fact label={t("动作")}>{codeLabel(ACTION, plan.action)}</Fact>
        <Fact label={t("目标")}>{planTarget(plan)}</Fact>
        <Fact label={t("止损参考")}>{stopPrice(plan)}</Fact>
        <Fact label={t("有效期")}>{plan.valid_until ? time(plan.valid_until, true) : t("未指定")}</Fact>
      </div>
      <div className="memory-subhead">{t("证据引用")} <span>{plan.evidence_refs.length}  {t("项")}</span></div>
      <Chips values={plan.evidence_refs.slice(0, 8)} empty={t("该计划未记录证据引用。")} />
      {plan.invalidation_conditions.length > 0 && <>
        <div className="memory-subhead">{t("失效条件")}</div>
        <ul className="memory-list">{plan.invalidation_conditions.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
      </>}
      {plan.execution_refs.length > 0 && <>
        <div className="memory-subhead">{t("执行引用")} <span>{plan.execution_refs.length}  {t("项")}</span></div>
        <Chips values={plan.execution_refs.slice(0, 6)} empty={t("尚无执行引用。")} />
      </>}
    </> : <p className="memory-none">{t("尚无活跃计划。规则策略可能只记录决策与执行事实，不生成计划对象。")}</p>}
  </div>;
}

const DIFF_FIELDS: { label: string; read: (plan: MemoryPlan) => string }[] = live(() => ([
  { label: t("动作"), read: plan => codeLabel(ACTION, plan.action) },
  { label: t("目标方向"), read: plan => plan.target_side },
  { label: t("目标数量"), read: plan => `${plan.target_value} ${plan.target_sizing === 'QUOTE_NOTIONAL' ? t("计价币名义") : t("基础数量")}` },
  { label: t("止损参考"), read: stopPrice },
  { label: t("有效期"), read: plan => diffTime(plan.valid_until) },
  { label: t("状态"), read: plan => pick(PLAN, plan.status, { label: plan.status, kind: '' as Tone }).label },
]));

function PlanChange({ plan, plans, previousPlanId }: { plan: MemoryPlan | null; plans: MemoryPlan[]; previousPlanId: string | null }) {
  const previous = useMemo(() => {
    if (!plan) return null;
    const superseded = plan.supersedes_plan_id ? plans.find(item => item.plan_id === plan.supersedes_plan_id) : null;
    const fallback = previousPlanId && previousPlanId !== plan.plan_id ? plans.find(item => item.plan_id === previousPlanId) : null;
    return superseded ?? fallback ?? null;
  }, [plan, plans, previousPlanId]);
  const changed = previous && plan ? DIFF_FIELDS.filter(field => field.read(previous) !== field.read(plan)).map(field => field.label) : [];
  return <div className="memory-block">
    <div className="memory-block-head"><span className="memory-eyebrow">{t("计划变更")}</span>{plan && <span className="memory-change-note">{previous ? changed.length ? `${t("本次变化：")}${changed.join('、')}` : t("与上一版一致") : t("无上一版可比")}</span>}</div>
    {plan && previous ? <div className="memory-diff-wrap">
      <table className="memory-diff">
        <thead><tr><th scope="col">{t("字段")}</th><th scope="col">{t("上一版")} <span className="memory-code">{shortId(previous.plan_id)}</span></th><th scope="col">{t("当前版")} <span className="memory-code">{shortId(plan.plan_id)}</span></th></tr></thead>
        <tbody>{DIFF_FIELDS.map(field => { const before = field.read(previous), after = field.read(plan); return <tr key={field.label} className={before !== after ? 'changed' : ''}><th scope="row">{field.label}</th><td>{before}</td><td>{after}</td></tr>; })}</tbody>
      </table>
    </div> : <p className="memory-none">{plan ? t("当前计划未关联上一版计划，无法计算版本差异。") : t("记录计划后才可比较版本差异。")}</p>}
  </div>;
}

function ContextBlock({ overview, checkpoint, health, data, checkpointError }: { overview: MemoryOverview; checkpoint: MemoryCheckpoint | null; health: MemoryHealth | null; data: Dashboard; checkpointError: boolean }) {
  const context = checkpoint?.context ?? null;
  const previous = context?.sections.previous_decision ?? context?.sections.recent_decisions?.[0] ?? null;
  const linked = previous?.checkpoint_id ? data.decisions.find(decision => decision.memory_checkpoint_id === previous.checkpoint_id) : undefined;
  const proposal = context?.sections.previous_proposal ?? null;
  const conflicts = health?.conflicts?.length ? health.conflicts : (context?.conflicts ?? overview.last_checkpoint?.conflicts ?? []);
  const missing = health?.missing?.length ? health.missing : (context?.missing ?? overview.last_checkpoint?.missing ?? []);
  const validation = context?.sections.validation;
  const completeness = context?.completeness ?? overview.last_checkpoint?.completeness ?? health?.completeness ?? null;
  const tokenEstimate = context?.token_estimate ?? overview.last_checkpoint?.token_estimate;
  if (!context) return <div className="panel-body">
    {checkpointError
      ? <Empty title={t("上下文读取失败")} text={t("检查点请求未完成。请刷新重试；已保存的记忆不会被修改。")} compact />
      : <Empty title={t("尚无上下文检查点")} text={t("该策略尚未产生可观测的决策上下文。检查点会在首次有效分析或规则决策后写入。")} compact />}
  </div>;
  return <div className="panel-body memory-context">
    <div className="memory-context-top">
      <div className="action-strip">{completeness && <StatusBadge map={COMPLETENESS} value={completeness} fallback={t("未记录")} />}<span className="memory-code" title={context.run_id}>{t("检查点")} {shortId(checkpoint?.checkpoint_id)}</span></div>
      <time dateTime={context.as_of}>{time(context.as_of, true)}</time>
    </div>
    <div className="memory-meta">
      <Fact label={t("状态修订")}>{`r${context.state_revision}`}</Fact>
      <Fact label={t("账户修订")}>{`r${context.account_revision}`}</Fact>
      <Fact label={t("Token 估算")}>{tokenEstimate ?? t("未记录")}</Fact>
      <Fact label={t("策略版本")}>{`v${context.strategy_version}`}</Fact>
      <Fact label={t("行情快照")}><span className="memory-code" title={context.snapshot_id}>{shortId(context.snapshot_id)}</span></Fact>
      <Fact label={t("模型版本")}><span title={versions(context.model_versions)}>{versions(context.model_versions)}</span></Fact>
      <Fact label={t("Prompt 版本")}><span title={versions(context.prompt_versions)}>{versions(context.prompt_versions)}</span></Fact>
      <Fact label={t("人工干预")}>{context.manual_intervention_at ? time(context.manual_intervention_at, true) : t("无")}</Fact>
    </div>
    <div className="memory-block">
      <div className="memory-block-head"><span className="memory-eyebrow">{t("上一轮结论")}</span>{previous && <StatusBadge map={DECISION} value={previous.decision_status} fallback={t("历史记录")} />}</div>
      {previous ? <>
        <div className="memory-facts">
          <Fact label={t("动作")}>{previous.action ? codeLabel(ACTION, previous.action) : t("无交易计划")}</Fact>
          <Fact label={t("执行结果")}>{previous.execution_status ? executionLabel(previous.execution_status) : t("未记录")}</Fact>
          <Fact label={t("记忆修订")}>{`r${previous.memory_state_revision}`}</Fact>
          <Fact label={t("记录时间")}>{time(previous.created_at, true)}</Fact>
        </div>
        <div className="memory-subhead">{t("决策记录")} <span className="memory-code" title={previous.run_id}>{shortId(previous.run_id)}</span></div>
        <div className="memory-facts">
          <Fact label={t("模型")}>{previous.model_name ?? t("未记录")}</Fact>
          <Fact label="Provider">{previous.model_provider ?? t("未记录")}</Fact>
          <Fact label={t("Prompt 修订")}>{previous.prompt_revision ?? t("未记录")}</Fact>
          <Fact label={t("Schema 修订")}>{previous.schema_revision ?? t("未记录")}</Fact>
        </div>
      </> : <p className="memory-none">{t("该检查点未引用上一轮决策。")}</p>}
      {linked && <>
        <div className="memory-subhead">{t("结论摘要")}</div>
        <p className="memory-statement">{linked.summary}</p>
        <div className="memory-subhead">{t("反证")} <span>{linked.counter_evidence.length}  {t("项")}</span></div>
        {linked.counter_evidence.length ? <ul className="memory-list">{linked.counter_evidence.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : <p className="memory-none">{t("该记录未提供反对证据。")}</p>}
        {linked.replan_reasons?.length ? <><div className="memory-subhead">{t("需重新规划")}</div><Chips values={linked.replan_reasons} empty={t("无")} /></> : null}
      </>}
    </div>
    {proposal && <>
      <div className="memory-subhead">{t("上一轮计划提议")}</div>
      <div className="memory-facts">
        <Fact label={t("动作")}>{codeLabel(ACTION, typeof proposal.action === 'string' ? proposal.action : '') || t("未记录")}</Fact>
        <Fact label={t("有效期")}>{typeof proposal.valid_until === 'string' && proposal.valid_until ? time(proposal.valid_until, true) : t("未指定")}</Fact>
      </div>
    </>}
    <div className="memory-block">
      <div className="memory-block-head"><span className="memory-eyebrow">{t("冲突与缺失")}</span><span className="memory-change-note">{conflicts.length + missing.length}  {t("项")}</span></div>
      <div className="memory-subhead">{t("冲突")} <span>{conflicts.length}  {t("项")}</span></div>
      <Chips values={conflicts} map={CONFLICT} empty={t("无未解决冲突。")} />
      <div className="memory-subhead">{t("缺失")} <span>{missing.length}  {t("项")}</span></div>
      <Chips values={missing} map={MISSING} empty={t("无缺失字段。")} />
    </div>
    <div className="memory-block">
      <div className="memory-block-head"><span className="memory-eyebrow">{t("引用证据")}</span><span className="memory-change-note">{context.evidence_refs.length}  {t("项")}</span></div>
      <Chips values={context.evidence_refs.slice(0, 10)} empty={t("该检查点未记录引用证据。")} />
      {context.evidence_refs.length > 10 && <p className="memory-none">{t("另有")} {context.evidence_refs.length - 10}  {t("项引用未展开。")}</p>}
    </div>
    {(validation?.blocking || validation?.block_new_risk || health?.block_new_risk) && <Alert error={Boolean(validation?.blocking)}>
      {validation?.blocking ? t("上下文校验未通过，新增风险已被阻断。") : t("上下文可用于观察，但新增风险已被限制。")}
      {validation?.reasons?.length ? `${t(" 原因：")}${validation.reasons.map(reason => codeLabel(CONFLICT, reason)).join('、')}。` : ''}
    </Alert>}
    {!validation?.blocking && !validation?.block_new_risk && !health?.block_new_risk && <p className="memory-none">{t("上下文校验未报告阻断；任何交易仍须通过独立的执行风控。")}</p>}
  </div>;
}

function DecisionTimeline({ decisions, data, onDecision }: { decisions: MemoryDecisionRef[]; data: Dashboard; onDecision: (id: string) => void }) {
  return <Panel title={t("决策时间线")} caption={t("已保存决策 · 结论、动作与执行结果 · 最多 20 条 · 窄屏横向滚动")} action={<Badge>{decisions.length}  {t("条")}</Badge>} className="memory-timeline">
    {decisions.length ? <div className="table-scroller" tabIndex={0} role="region" aria-label={t("策略记忆决策时间线，可横向滚动")}>
      <table className="data-table memory-table">
        <thead><tr><th>{t("记录时间 UTC+8")}</th><th>{t("结论")}</th><th>{t("动作")}</th><th>{t("执行结果")}</th><th>{t("模型")}</th><th>{t("记忆修订")}</th><th>{t("检查点")}</th><th>{t("关联")}</th></tr></thead>
        <tbody>{decisions.map(item => {
          const decision = (item.checkpoint_id ? data.decisions.find(entry => entry.memory_checkpoint_id === item.checkpoint_id) : undefined)
            ?? data.decisions.find(entry => entry.id === item.run_id);
          return <tr key={item.run_id}>
            <td>{time(item.created_at, true)}</td>
            <td><StatusBadge map={DECISION} value={item.decision_status} fallback={t("历史记录")} /></td>
            <td>{item.action ? codeLabel(ACTION, item.action) : t("无交易计划")}</td>
            <td><Badge kind={pick(EXECUTION_TONE, item.execution_status, '')}>{item.execution_status ? executionLabel(item.execution_status) : t("未记录")}</Badge></td>
            <td><span>{item.model_name ?? t("未记录")}</span><div className="asset-sub">{item.model_provider ?? t("Provider 未记录")}</div></td>
            <td className="t-num">{`r${item.memory_state_revision}`}</td>
            <td><span className="memory-code" title={item.checkpoint_id ?? ''}>{shortId(item.checkpoint_id)}</span></td>
            <td>{decision ? <button className="button link" onClick={() => onDecision(decision.id)}>{t("查看决策")}<ArrowRight className="icon sm" /></button> : <span className="quiet">{t("未关联")}</span>}</td>
          </tr>;
        })}</tbody>
      </table>
    </div> : <Empty title={t("尚无决策记录")} text={t("策略产生第一条可观测决策后，时间线会记录结论、动作与执行结果。")} compact />}
    <div className="table-footer"><span>{t("结论与执行结果分别记录，未执行不等于已拒绝")}</span><span>{t("只读视图 · 不改变记忆状态")}</span></div>
  </Panel>;
}

function EventTimeline({ events }: { events: MemoryEvent[] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? events : events.slice(0, 8);
  return <Panel title={t("记忆事件")} caption={t("按时间倒序排列的状态记录")} action={<Badge>{events.length}  {t("条")}</Badge>} className="memory-events-panel">
    {events.length ? <>
      <div className="memory-events">{visible.map(event => <div className="memory-event" key={event.event_id}>
        <span className="memory-event-dot" aria-hidden="true" />
        <div className="memory-event-main">
          <div className="memory-event-title">{codeLabel(EVENT, event.event_type)}<span className="memory-chip">{codeLabel(SOURCE, event.source)}</span></div>
          <div className="memory-event-sub">{t("序列 #")}{event.sequence}  {t("· 状态修订 r")}{event.state_revision} · <span className="memory-code" title={event.event_id}>{shortId(event.event_id)}</span></div>
        </div>
        <time className="memory-event-time" dateTime={event.occurred_at}>{time(event.occurred_at, true)}</time>
      </div>)}</div>
      {events.length > 8 && <div className="memory-expand"><button className="button link" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? t("收起事件") : `${t("展开全部 ")}${events.length}${t(" 条事件")}`}</button></div>}
    </> : <Empty title={t("尚无记忆事件")} text={t("策略创建、论点更新、计划修订与人工干预都会追加到这里。")} compact />}
  </Panel>;
}

function KnowledgeList({ knowledge }: { knowledge: MemoryKnowledge[] }) {
  return <Panel title={t("已固化知识")} caption={t("经过统计或人工复核的长期结论 · 模型提议不会自动生效")} action={<Badge>{knowledge.length}  {t("条")}</Badge>} className="memory-knowledge-panel">
    {knowledge.length ? <div className="memory-knowledge">{knowledge.map(item => <div className="memory-knowledge-item" key={item.knowledge_id}>
      <div className="flex between gap12"><StatusBadge map={KNOWLEDGE} value={item.status} fallback={t("未记录")} /><span className="quiet">{`v${item.version}`}</span></div>
      <p className="memory-statement">{item.statement}</p>
      <div className="memory-knowledge-meta"><span>{t("样本")} {item.sample_size}</span><span>{codeLabel(PROVENANCE, item.provenance)}</span><span>{t("策略版本 v")}{item.strategy_version}</span>{item.validated_at && <span>{t("验证于")} {time(item.validated_at, true)}</span>}</div>
      {item.evidence_refs.length > 0 && <Chips values={item.evidence_refs.slice(0, 5)} empty={t("未记录证据引用。")} />}
    </div>)}</div> : <Empty title={t("尚无已固化知识")} text={t("知识先以提议形式记录；只有满足样本与证据要求并通过统计或人工复核后才会生效。")} compact />}
  </Panel>;
}

export function Memory({ data, api, onNavigate, onDecision }: { data: Dashboard; api: RuntimeApi; onNavigate: (page: 'strategies') => void; onDecision: (id: string) => void }) {
  const strategies = data.strategies;
  const preferred = useMemo(() => strategies.find(strategy => strategy.status === 'ACTIVE') ?? strategies[0], [strategies]);
  const [strategyId, setStrategyId] = useState(preferred?.id ?? '');
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [events, setEvents] = useState<MemoryEvent[]>([]);
  const [decisions, setDecisions] = useState<MemoryDecisionRef[]>([]);
  const [knowledge, setKnowledge] = useState<MemoryKnowledge[]>([]);
  const [health, setHealth] = useState<MemoryHealth | null>(null);
  const [checkpoint, setCheckpoint] = useState<MemoryCheckpoint | null>(null);
  const [checkpointError, setCheckpointError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refreshToken, setRefreshToken] = useState(0);
  const selected: Strategy | undefined = strategies.find(strategy => strategy.id === strategyId);
  const loadedStrategy = useRef('');

  useEffect(() => {
    if (!strategies.some(strategy => strategy.id === strategyId)) setStrategyId(preferred?.id ?? '');
  }, [strategies, strategyId, preferred?.id]);

  useEffect(() => {
    if (!strategyId) { setOverview(null); setEvents([]); setDecisions([]); setKnowledge([]); setHealth(null); setCheckpoint(null); setLoading(false); return; }
    let disposed = false;
    const controller = new AbortController();
    const strategyChanged = loadedStrategy.current !== strategyId;
    if (strategyChanged) {
      loadedStrategy.current = strategyId;
      setLoading(true); setOverview(null); setCheckpoint(null); setError('');
    }
    void (async () => {
      try {
        const [nextOverview, nextEvents, nextDecisions, nextKnowledge, nextHealth] = await Promise.all([
          api.strategyMemory(strategyId, controller.signal),
          api.strategyMemoryEvents(strategyId, controller.signal),
          api.strategyMemoryDecisions(strategyId, controller.signal),
          api.strategyMemoryKnowledge(strategyId, controller.signal),
          api.strategyMemoryHealth(strategyId, controller.signal),
        ]);
        if (disposed) return;
        setOverview(nextOverview); setEvents(nextEvents.events); setDecisions(nextDecisions.decisions);
        setKnowledge(nextKnowledge.knowledge); setHealth(nextHealth);
        if (!disposed) setError('');
      } catch (err) {
        if (!disposed) setError(errorMessage(err));
      } finally {
        if (!disposed) setLoading(false);
      }
    })();
    return () => { disposed = true; controller.abort(); };
  }, [api, strategyId, refreshToken, data.as_of]);

  const checkpointRun = overview?.last_checkpoint?.run_id ?? null;
  useEffect(() => {
    if (!checkpointRun) { setCheckpoint(null); setCheckpointError(false); return; }
    let disposed = false;
    const controller = new AbortController();
    setCheckpointError(false);
    void (async () => {
      try {
        const nextCheckpoint = await api.decisionContext(checkpointRun, controller.signal);
        if (!disposed) setCheckpoint(nextCheckpoint);
      } catch {
        if (!disposed) { setCheckpoint(null); setCheckpointError(true); }
      }
    })();
    return () => { disposed = true; controller.abort(); };
  }, [api, checkpointRun, refreshToken]);

  const refresh = useCallback(() => { setLoading(true); setRefreshToken(value => value + 1); }, []);
  const state = overview?.state;
  const completeness = checkpoint?.context.completeness ?? overview?.last_checkpoint?.completeness ?? health?.completeness ?? null;
  const conflicts = health?.conflicts?.length ? health.conflicts : (checkpoint?.context.conflicts ?? overview?.last_checkpoint?.conflicts ?? []);
  const missing = health?.missing?.length ? health.missing : (checkpoint?.context.missing ?? overview?.last_checkpoint?.missing ?? []);
  const tokenEstimate = checkpoint?.context.token_estimate ?? overview?.last_checkpoint?.token_estimate;
  const blockNewRisk = Boolean(health?.block_new_risk || overview?.last_checkpoint?.block_new_risk || checkpoint?.context.sections.validation?.block_new_risk);
  const blocking = Boolean(checkpoint?.context.sections.validation?.blocking);

  if (!strategies.length) return <>
    <Intro title={t("策略记忆")} subtitle={t("查看每次决策依据的论点、计划与上下文变化。")}><span className="chip blue">PAPER</span></Intro>
    <Panel title={t("策略记忆")} caption={t("按策略查看论点、计划与历史记录")}><Empty title={t("尚无策略记忆")} text={t("创建策略并产生第一条决策后，这里会显示活跃论点、计划、上下文检查点与变化时间线。")}><button className="button small" onClick={() => onNavigate('strategies')}><BrainCircuit className="icon" />{t("前往策略中心")}</button></Empty></Panel>
  </>;

  return <>
    <Intro title={t("策略记忆")} subtitle={t("查看每次决策依据的论点、计划与上下文变化。")}>
      <span className="chip blue">PAPER</span>
    </Intro>
    <section className="panel memory-scope">
      <div className="memory-scope-main">
        <label className="memory-scope-picker"><span>{t("查看策略")}</span>
          <select value={strategyId} onChange={event => setStrategyId(event.target.value)} aria-label={t("选择要查看记忆的策略")}>
            {strategies.map(strategy => <option key={strategy.id} value={strategy.id}>{strategy.name} · {strategy.symbol} · {MODE_NAMES[strategy.mode]}</option>)}
          </select>
        </label>
        <div className="memory-scope-meta">
          <div><span>{t("记忆状态")}</span><strong><Badge kind={state?.status === 'ACTIVE' ? 'pass' : state?.status === 'PAUSED' ? 'wait' : ''}>{state ? ({ DRAFT: t("草稿"), ACTIVE: t("运行中"), PAUSED: t("已暂停") }[state.status] ?? state.status) : t("读取中")}</Badge></strong></div>
          <div><span>{t("状态修订")}</span><strong className="t-num">{state ? `r${state.state_revision}` : '—'}</strong></div>
          <div><span>{t("策略版本")}</span><strong className="t-num">{state ? `v${state.strategy_version}` : '—'}</strong></div>
          <div><span>{t("最近更新")}</span><strong>{state?.updated_at ? time(state.updated_at, true) : '—'}</strong></div>
        </div>
        <button className="button small" onClick={refresh} disabled={loading}><RefreshCw className={`icon ${loading ? 'spin' : ''}`} />{loading ? t("同步中…") : t("刷新记忆")}</button>
      </div>
      <div className="memory-scope-foot">
        <span>{selected ? `${selected.symbol} · ${MODE_NAMES[selected.mode]}${t(" · 本地 PAPER")}` : t("本地 PAPER")}</span>
      </div>
    </section>
    {error && <div className="memory-message"><Alert error onRetry={refresh}>{error}</Alert></div>}
    {loading && !overview ? <section className="panel memory-loading"><Loading text={t("正在读取策略记忆…")} /></section> : overview ? <>
      <div className="memory-health" role="group" aria-label={t("记忆健康摘要")}>
        <div><span>{t("记忆完整度")}</span><strong>{completeness ? <StatusBadge map={COMPLETENESS} value={completeness} fallback={t("未记录")} /> : t("未记录")}</strong><small>{overview.last_checkpoint ? `${t("最近检查点 ")}${time(overview.last_checkpoint.created_at)}` : t("尚无上下文检查点")}</small></div>
        <div><span>{t("未解决冲突")}</span><strong className={conflicts.length ? 'negative' : ''}>{conflicts.length}<small className="memory-unit">  {t("项")}</small></strong><small>{conflicts.length ? t("执行、行情或计划存在冲突") : t("未报告冲突")}</small></div>
        <div><span>{t("缺失字段")}</span><strong className={missing.length ? 'warn' : ''}>{missing.length}<small className="memory-unit">  {t("项")}</small></strong><small>{missing.length ? t("上下文缺少必要数据") : t("必要数据齐全")}</small></div>
        <div><span>{t("上下文 Token")}</span><strong className="t-num">{tokenEstimate ?? '—'}</strong><small>{t("冻结快照的保守估算")}</small></div>
        <div className="memory-health-foot">
          <span className={`memory-signal ${blockNewRisk ? 'blocked' : 'clear'}`}><span className="small-dot" />{blockNewRisk ? t("新增风险已阻断") : t("未触发新增风险阻断")}</span>
          <span>{t("执行未知")} {health?.execution_unknown.length ?? 0}  {t("项")}</span>
          <span>{t("执行过期")} {health?.execution_stale.length ?? 0}  {t("项")}</span>
          {blocking && <span className="negative">{t("上下文校验未通过")}</span>}
        </div>
      </div>
      {overview.state.manual_intervention_at && <div className="memory-message"><Alert>{t("人工干预记录于")} {time(overview.state.manual_intervention_at, true)}{t("；干预后的过期模型结论不会获得执行授权。")}</Alert></div>}
      <div className="memory-main">
        <div className="memory-stack">
          <Panel title={t("当前判断")} caption={t("活跃论点与计划 · 模型建议不构成执行授权")} action={<StatusBadge map={PLAN} value={overview.active_plan?.status} fallback={t("尚无计划")} />}>
            <div className="panel-body">
              <ThesisBlock thesis={overview.active_thesis} />
              <PlanBlock plan={overview.active_plan} />
              <PlanChange plan={overview.active_plan} plans={overview.state.plans} previousPlanId={checkpoint?.context.previous_plan_id ?? null} />
            </div>
          </Panel>
        </div>
        <div className="memory-stack">
          <Panel title={t("最近决策上下文")} caption={t("本轮使用的账户、行情与版本记录")} action={completeness ? <StatusBadge map={COMPLETENESS} value={completeness} fallback={t("未记录")} /> : <Badge>{t("未记录")}</Badge>}>
            <ContextBlock overview={overview} checkpoint={checkpoint} health={health} data={data} checkpointError={checkpointError} />
          </Panel>
        </div>
      </div>
      <DecisionTimeline decisions={decisions} data={data} onDecision={onDecision} />
      <div className="memory-lower">
        <EventTimeline events={events} />
        <KnowledgeList knowledge={knowledge} />
      </div>
    </> : <section className="panel memory-loading"><Empty title={t("记忆读取未完成")} text={t("请刷新重试；若问题持续，请确认本地服务与策略记录仍然可用。")} compact /></section>}
  </>;
}
