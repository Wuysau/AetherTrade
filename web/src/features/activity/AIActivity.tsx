// Read-only AI activity page: proposals/intents, their raw state, rationale and
// missing information. Intent state is only shown when a projection exists;
// the page never invents a state or claims that a proposal was filled.

import type {
  FreshnessView,
  IntentProjection,
  TradeIntent,
} from '../../contracts/generated';
import type { ViewChild, ViewElement } from '../../app/App';

export interface AIActivityProps {
  readonly intents: ReadonlyArray<TradeIntent>;
  readonly offline: boolean;
  readonly freshness: FreshnessView | null;
  readonly requiresSnapshot: boolean;
  readonly projections?: ReadonlyArray<IntentProjection>;
}

function el(
  type: string,
  props: Record<string, unknown> = {},
  children: ReadonlyArray<ViewChild> = [],
): ViewElement {
  const key = typeof props.key === 'string' ? props.key : null;
  const { key: _ignored, ...rest } = props;
  return { type, key, props: children.length === 0 ? rest : { ...rest, children } };
}

export interface IntentStateSummary {
  readonly intent_id: string;
  readonly state: string;
  readonly risk_verdict: string | null;
  readonly protection_states: ReadonlyArray<string>;
}

export function summariseIntentStates(props: AIActivityProps): ReadonlyArray<IntentStateSummary> {
  const projections = props.projections ?? [];
  return props.intents.map(intent => {
    const projection = projections.find(candidate => candidate.intent.intent_id === intent.intent_id);
    if (projection === undefined) {
      return {
        intent_id: intent.intent_id,
        state: 'STATE_NOT_REPORTED',
        risk_verdict: null,
        protection_states: [],
      };
    }
    return {
      intent_id: intent.intent_id,
      state: projection.state,
      risk_verdict: projection.risk?.verdict ?? null,
      protection_states: projection.protection.map(plan => `${plan.mode}/${plan.state}`),
    };
  });
}

export function AIActivity(props: AIActivityProps): ViewElement {
  const summaries = summariseIntentStates(props);
  const freshness = props.freshness;

  const alert = props.offline || props.requiresSnapshot || freshness === null || freshness.stale
    ? el('div', {
        'data-testid': 'activity-staleness',
        'data-code': props.offline
          ? 'OFFLINE_STALE_BY_DEFINITION'
          : props.requiresSnapshot
            ? 'SNAPSHOT_REQUIRED'
            : freshness === null
              ? 'FRESHNESS_UNAVAILABLE'
              : 'DATA_STALE',
        role: 'alert',
        text: 'AI 动态可能已过期：先重新取快照，不能把旧结论当作当前授权。',
      })
    : el('div', { 'data-testid': 'activity-freshness', text: '数据由服务端提供新鲜度。' });

  const items = props.intents.map(intent => {
    const summary = summaries.find(candidate => candidate.intent_id === intent.intent_id);
    const state = summary?.state ?? 'STATE_NOT_REPORTED';
    return el(
      'li',
      {
        'data-testid': `intent-${intent.intent_id}`,
        'data-intent-state': state,
      },
      [
        el('h3', { text: `意图 ${intent.intent_id}` }),
        el('span', { 'data-field': 'state', text: `状态 ${state}` }),
        el('span', {
          'data-field': 'action',
          text: `动作 ${intent.action} · 目标 ${intent.target_side} ${intent.target_quantity} ${intent.quantity_unit}`,
        }),
        el('span', {
          'data-field': 'risk',
          text: `风控 ${summary?.risk_verdict ?? '未提供'} · 授权 ${intent.authorization_id} · 政策修订 ${String(intent.risk_policy_revision)}`,
        }),
        el('span', {
          'data-field': 'protection',
          text: `保护 ${summary?.protection_states.join(',') || '未提供'}`,
        }),
        el('p', { 'data-field': 'rationale', text: intent.rationale_summary }),
        el('span', {
          'data-field': 'missing_information',
          text: '缺失信息与反证：本投影未提供明细，不能视为已完整论证。',
        }),
        el('span', {
          'data-field': 'evidence',
          text: `证据引用 ${String(intent.evidence_refs.length)} 项 · 提案哈希 ${intent.proposal_hash} · 有效期至 ${intent.valid_until}`,
        }),
        el('span', {
          'data-field': 'receipt_boundary',
          text: '意图/提案被接收不代表委托已提交、已成交或已受保护。',
        }),
      ],
    );
  });

  return el('section', { 'data-testid': 'ai-activity-section' }, [
    el('h2', { text: 'AI 动态' }),
    alert,
    props.intents.length === 0
      ? el('p', { 'data-code': 'NO_INTENT_REPORTED', text: '暂无意向投影；不显示推测的 AI 结论。' })
      : el('ul', {}, items),
    el('p', {
      'data-testid': 'activity-boundary',
      text: '本页只读；模型不能通过页面获得下单、撤保护或账户签名能力。',
    }),
  ]);
}
