// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RuntimeApi } from '../src/runtime/api';
import { Memory } from '../src/runtime/memory';
import type {
  Dashboard, MemoryCheckpoint, MemoryDecisionRef, MemoryEvent, MemoryHealth, MemoryKnowledge,
  MemoryOverview, MemoryPlan, MemoryScope, MemoryState, MemoryThesis, Strategy,
} from '../src/runtime/types';

const now = '2026-11-04T06:30:00Z';
const scope: MemoryScope = {
  tenant_id: 'local', user_id: 'owner', account_scope: 'paper-local', strategy_id: 's1',
  product: 'SPOT', instrument: 'console-paper:BTCUSDT', environment: 'PAPER', scope_id: 'sms:scope-1',
};
const thesis: MemoryThesis = {
  thesis_id: 'thesis:1', scope, statement: '趋势仍然有效，回撤未破坏结构。', status: 'ACTIVE',
  evidence_refs: ['quote:BTCUSDT', 'candles:1h'], invalidation_conditions: ['收盘跌破 90000'],
  created_at: now, updated_at: now, expires_at: null, revision: 2, source: 'MODEL', model_confidence: '0.72',
};
const plan: MemoryPlan = {
  plan_id: 'plan:2', scope, thesis_id: 'thesis:1', status: 'ACTIVE', action: 'SET_TARGET',
  target_side: 'LONG', target_sizing: 'BASE_QUANTITY', target_value: '0.5', entry: {},
  exit_plan: { stop_price: '90000' }, evidence_refs: ['quote:BTCUSDT'], invalidation_conditions: [],
  valid_until: '2026-11-05T06:30:00Z', created_at: now, updated_at: now, revision: 2, source: 'MODEL',
  supersedes_plan_id: 'plan:1', authorization_ref: null, execution_refs: ['order:1'], source_proposal: {},
};
const previousPlan: MemoryPlan = {
  ...plan, plan_id: 'plan:1', status: 'REVISED', target_value: '0.3',
  exit_plan: { stop_price: '85000' }, supersedes_plan_id: null, revision: 1, execution_refs: [],
};
const state: MemoryState = {
  scope, state_revision: 4, schema_revision: 'strategy-memory-v1', strategy_version: 2, status: 'ACTIVE',
  active_thesis_id: thesis.thesis_id, active_plan_id: plan.plan_id, last_event_id: 'event:3',
  last_decision_id: 'run-1', event_cursor: 3, updated_at: now, manual_intervention_at: null,
  manual_action: null, theses: [thesis], plans: [previousPlan, plan], knowledge: [], state_hash: 'hash',
};
const decisionRef: MemoryDecisionRef = {
  run_id: 'run-1', scope, created_at: now, decision_status: 'DECIDED', action: 'SET_TARGET',
  model_provider: 'openai', model_name: 'fixture-model', prompt_revision: 'prompt-v1',
  schema_revision: 'assessment_v1', policy_revision: 'policy-v1', snapshot_id: 'snapshot:1',
  snapshot_hash: 'a'.repeat(64), memory_state_revision: 3, checkpoint_id: 'mctx:1',
  thesis_id: thesis.thesis_id, plan_id: plan.plan_id, execution_status: 'FILLED',
};
const overview: MemoryOverview = {
  strategy_id: 's1', scope, state, active_thesis: thesis, active_plan: plan, state_revision: 4,
  last_checkpoint: {
    checkpoint_id: 'mctx:1', run_id: 'run-1', created_at: now, completeness: 'PARTIAL',
    conflicts: ['EXECUTION_FACT_STALE'], missing: ['execution_facts'], token_estimate: 1200,
    block_new_risk: true, prompt_versions: { framework: 'memory-prompt-v1' },
    schema_versions: { memory: 'strategy-memory-v1' },
  },
};
const checkpoint: MemoryCheckpoint = {
  checkpoint_id: 'mctx:1', schema_revision: 'decision-context-checkpoint-v1', created_at: now,
  context: {
    run_id: 'run-1', scope, as_of: now, state_revision: 4, strategy_version: 2,
    snapshot_id: 'snapshot:1', snapshot_hash: 'a'.repeat(64), account_revision: 7,
    sections: {
      previous_decision: decisionRef,
      previous_proposal: { action: 'SET_TARGET', valid_until: '2026-11-05T06:30:00Z' },
      validation: { blocking: false, block_new_risk: true, reasons: ['EXECUTION_FACT_STALE'] },
    },
    token_estimate: 1200, completeness: 'PARTIAL', missing: ['execution_facts'],
    conflicts: ['EXECUTION_FACT_STALE'], evidence_refs: ['active_thesis:thesis:1', 'active_plan:plan:2'],
    prompt_versions: { framework: 'memory-prompt-v1' },
    schema_versions: { memory: 'strategy-memory-v1' }, model_versions: { model: 'fixture-model' },
    previous_plan_id: 'plan:1', previous_thesis_id: 'thesis:1', manual_intervention_at: null,
    input_hash: 'b'.repeat(64), schema_revision: 'decision-context-v1',
  },
};
const events: MemoryEvent[] = [
  { event_id: 'event:3', sequence: 3, event_type: 'PLAN_REVISED', source: 'CONTROL', occurred_at: now, recorded_at: now, state_revision: 4 },
  { event_id: 'event:2', sequence: 2, event_type: 'DECISION_RECORDED', source: 'CONTROL', occurred_at: now, recorded_at: now, state_revision: 3 },
];
const decisions: MemoryDecisionRef[] = [
  decisionRef,
  { ...decisionRef, run_id: 'run-2', checkpoint_id: 'mctx:2', decision_status: 'DATA_BLOCKED', action: null, execution_status: null, memory_state_revision: 2 },
  { ...decisionRef, run_id: 'run-3', checkpoint_id: 'mctx:3', decision_status: 'MODEL_ERROR', action: null, execution_status: 'ERROR', memory_state_revision: 1 },
];
const knowledge: MemoryKnowledge = {
  knowledge_id: 'knowledge:1', scope, statement: '高波动时段收紧目标仓位。', status: 'VALIDATED',
  market: 'crypto', product: 'SPOT', instrument: 'console-paper:BTCUSDT', timeframe: '1h',
  strategy_version: 2, sample_size: 24, evaluation_window: {}, evidence_refs: ['trade:1'],
  proposed_at: now, validated_at: now, expires_at: null, version: 1, provenance: 'DETERMINISTIC_STATISTICS',
};
const health: MemoryHealth = {
  strategy_id: 's1', state_revision: 4, event_cursor: 3, status: 'ACTIVE', manual_action: null,
  execution_unknown: [], execution_stale: ['order:2'], completeness: 'PARTIAL',
  conflicts: ['EXECUTION_FACT_STALE'], missing: ['execution_facts'], block_new_risk: true,
  provider_calls_replayed: false,
};

const strategy: Strategy = {
  id: 's1', name: 'BTC 趋势观察', symbol: 'BTCUSDT', mode: 'AI', budget: '10000', target_pct: '10',
  status: 'ACTIVE', revision: 2, created_at: now, updated_at: now,
};
function dashboard(strategies: Strategy[] = [strategy]): Dashboard {
  return {
    as_of: now, environment: 'PAPER',
    account: { id: 'paper', name: 'Paper', currency: 'USDT', initial_cash: '100000', cash: '100000', equity: '100000', day_pnl: '0', day_pnl_pct: '0', exposure_pct: '0', risk_used: '0', risk_limit: '600', revision: 1, paused: false },
    history: [{ at: now, equity: '100000' }], positions: [], orders: [], events: [],
    decisions: [{ id: 'run-1', symbol: 'BTCUSDT', action: 'SET_TARGET', source: 'MODEL', summary: '趋势延续', counter_evidence: [], invalidation: '跌破 90000', risk_status: 'PAPER_PASSED', created_at: now, next_review_at: null, memory_checkpoint_id: 'mctx:1' }],
    strategies,
    risk: { max_position_pct: '20', max_daily_loss: '600', revision: 1 },
    services: [{ id: 'model', label: 'AI 模型', status: 'CONNECTED', detail: '已连接', updated_at: now }],
    market: { source: 'TEST-ONLY', status: 'CONNECTED', last_updated: now, error: null, quotes: [] },
    research: { total_return_pct: '0', max_drawdown_pct: '0', win_rate: null, trade_count: 0, fees: '0' },
  };
}
function apiWithMemory() {
  const api = new RuntimeApi();
  vi.spyOn(api, 'strategyMemory').mockResolvedValue(overview);
  vi.spyOn(api, 'strategyMemoryEvents').mockResolvedValue({ events });
  vi.spyOn(api, 'strategyMemoryDecisions').mockResolvedValue({ decisions });
  vi.spyOn(api, 'strategyMemoryKnowledge').mockResolvedValue({ knowledge: [knowledge] });
  vi.spyOn(api, 'strategyMemoryHealth').mockResolvedValue(health);
  vi.spyOn(api, 'decisionContext').mockResolvedValue(checkpoint);
  return api;
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('strategy memory observability', () => {
  it('shows the active judgement, frozen context, change diff and distinct decision states', async () => {
    const api = apiWithMemory();
    render(<Memory data={dashboard()} api={api} onNavigate={vi.fn()} onDecision={vi.fn()} />);
    await screen.findByText('趋势仍然有效，回撤未破坏结构。');

    expect(screen.getByRole('heading', { name: '策略记忆', level: 1 })).toBeDefined();
    expect((screen.getByLabelText('选择要查看记忆的策略') as HTMLSelectElement).value).toBe('s1');
    expect(screen.getByText('本次变化：目标数量、止损参考、状态')).toBeDefined();
    expect(screen.getAllByText('部分缺失').length).toBeGreaterThan(0);
    expect((await screen.findAllByText('执行事实过期')).length).toBeGreaterThan(0);
    expect(screen.getByText('数据不足')).toBeDefined();
    expect(screen.getByText('模型失败')).toBeDefined();
    expect(screen.getByText('计划修订')).toBeDefined();
    expect(screen.getByText('趋势延续')).toBeDefined();
    expect(screen.getByText('该记录未提供反对证据。')).toBeDefined();
    expect(screen.getByText('高波动时段收紧目标仓位。')).toBeDefined();
  });

  it('opens a linked decision from the timeline and refreshes on demand', async () => {
    const api = apiWithMemory();
    const onDecision = vi.fn();
    const user = userEvent.setup();
    render(<Memory data={dashboard()} api={api} onNavigate={vi.fn()} onDecision={onDecision} />);
    await screen.findByText('趋势仍然有效，回撤未破坏结构。');
    await user.click(screen.getByRole('button', { name: /查看决策/ }));
    expect(onDecision).toHaveBeenCalledWith('run-1');
    const calls = vi.mocked(api.strategyMemory).mock.calls.length;
    await user.click(screen.getByRole('button', { name: '刷新记忆' }));
    expect(vi.mocked(api.strategyMemory).mock.calls.length).toBeGreaterThan(calls);
  });

  it('distinguishes a failed checkpoint request from an empty memory', async () => {
    const api = apiWithMemory();
    vi.mocked(api.decisionContext).mockRejectedValue(new Error('checkpoint unavailable'));
    render(<Memory data={dashboard()} api={api} onNavigate={vi.fn()} onDecision={vi.fn()} />);
    await screen.findByText('上下文读取失败');
    expect(screen.queryByText('尚无上下文检查点')).toBeNull();
  });

  it('renders a truthful empty state when no strategy exists', () => {
    render(<Memory data={dashboard([])} api={apiWithMemory()} onNavigate={vi.fn()} onDecision={vi.fn()} />);
    expect(screen.getByText('尚无策略记忆')).toBeDefined();
    expect(screen.queryByText('当前判断')).toBeNull();
  });
});
