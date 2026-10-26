import type { ControlCommandRequest, ControlCommandReceipt, RuleGraph, RiskPolicyDraft, TransferProjection, ExitReceipt, ExperimentReport } from '../../src/contracts/generated';
import { TransferPanel } from '../../src/features/ownership/TransferPanel';
import { textContent } from '../../src/app/App';

export const scope: ControlCommandRequest['scope'] = { environment: 'PAPER', venue_account_id: 'offline-account', product: 'SPOT', instrument_uid: 'BTCUSDT', venue_leg: 'BOTH' };
export function graph(): RuleGraph {
  return { rule_id: 'rule-30', revision: 1, instrument_ref: 'BTCUSDT',
    condition: { operator: 'ALL', input_ref: null, threshold: null, children: [
      { operator: 'GT', input_ref: 'quote.last', threshold: '9007199254740993.00000001', children: [] },
      { operator: 'ANY', input_ref: null, threshold: null, children: [
        { operator: 'LE', input_ref: 'quote.mid', threshold: '0.00000001', children: [] },
        { operator: 'NOT', input_ref: null, threshold: null, children: [
          { operator: 'EQ', input_ref: 'quote.spread', threshold: '1', children: [] },
        ] },
      ] },
    ] }, trigger: 'EDGE', window_seconds: 60, closed_bar_only: true, cooldown_seconds: 120,
    sizing: 'QUOTE_NOTIONAL', target_value: '100', exit_priority: ['stop', 'timeout'], missing_data: 'HOLD' };
}
export function transfer(mode: TransferProjection['request']['mode'] = 'EXIT_THEN_SWITCH'): TransferProjection {
  return { request: { transfer_id: 'transfer-30', scope: { ...scope }, mode, old_owner: 'old-strategy', new_owner: 'new-strategy', ownership_epoch: 4 },
    state: 'RECONCILE_PENDING', ownership_epoch: 4, durable_steps: [], blocked_reasons: ['MARKET_CLOSED'] };
}
export function renderTransferFixture({ state, actualQuantity }: { state: ExitReceipt['state']; actualQuantity: string }) {
  const view = TransferPanel({ transfer: transfer(), actualQuantity: { value: actualQuantity, unit: 'BASE' },
    exit: { position_id: 'position-30', state, command_ids: [], incomplete_reasons: ['MARKET_CLOSED'] } });
  return { view, text: () => textContent(view) };
}
export function policy(environment: RiskPolicyDraft['policy']['environment'] = 'PAPER'): RiskPolicyDraft {
  return { author_id: 'offline-user', expected_revision: 1, policy: {
    contract_version: '2026-10-20', policy_id: 'policy-30', revision: 2, environment, collateral_group_id: 'cash',
    valuation_currency: 'USDT', maximum_capital: '100', cash_buffer: '10', maximum_order_notional: '10',
    maximum_planned_loss_per_intent: '1', maximum_total_planned_loss: '5', maximum_gross_exposure: '90',
    maximum_net_exposure: '90', maximum_open_positions: 1, maximum_active_orders: 2, maximum_leverage: 1,
    daily_loss_fraction: '0.01', maximum_drawdown_fraction: '0.05', risk_day_timezone: 'UTC',
    maximum_quote_age_ms: 1000, maximum_entry_slippage_bps: '10', maximum_model_cost_per_day: { amount: '1', currency: 'USD' },
    protection_policy: 'NATIVE_REQUIRED', daily_lock_release: 'REVIEW_REQUIRED', drawdown_lock_release: 'REVIEW_REQUIRED',
    native_risk_bypass: false, live_enabled: false,
  } };
}
export function request(): ControlCommandRequest {
  return { action: 'pause_new', scope: { ...scope }, expected_revision: 1, ownership_epoch: 4, transfer: null };
}
export const receipt: ControlCommandReceipt = { command_id: 'cmd-30', status: 'QUEUED', reason: null, existing_result_ref: null };
export function report(): ExperimentReport {
  return { run_id: 'offline-research', config: { mode: 'REPLAY_OUTPUTS', build_digest: 'build', config_hash: 'config', model_fingerprint: 'model', proposal_refs: [] },
    dataset: { id: 'dataset', hash: 'dataset-hash', environment: 'RESEARCH', start: '2026-01-01T00:00:00Z', end: '2026-01-02T00:00:00Z', evidence: [
      { id: 'dataset-source', hash: 'source-hash', environment: 'RESEARCH', source: 'offline-fixture', visible_at: '2026-01-01T00:00:00Z' },
    ] },
    metrics: { metrics: [{ name: 'return', value: null, unit: '%' }], assumptions: { valuation_currency: 'USD', fees: [], funding: [], model_costs: [], evidence: [] }, missing: ['fees'] }, missing: ['publication-time'] };
}
