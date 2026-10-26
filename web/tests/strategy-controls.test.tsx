import { describe, it, expect, vi } from 'vitest';
import { findAll, findOne, textContent } from '../src/app/App';
import { StrategyCenter, ControlAttempt, modes } from '../src/features/strategies/StrategyCenter';
import { RuleEditor } from '../src/features/rules/RuleEditor';
import { PolicyEditor, type PolicySubmission } from '../src/features/permissions/PolicyEditor';
import { TransferPanel } from '../src/features/ownership/TransferPanel';
import { ResearchView } from '../src/features/research/ResearchView';
import { graph, policy, transfer, renderTransferFixture, request, receipt, report } from './support/ip-30';

describe('IP-30 local functional probes (not trading qualification)', () => {
  it('test_three_modes_configurable', () => {
    const configure = vi.fn();
    const view = StrategyCenter({ environment: 'PAPER', mode: 'AI', onConfigure: configure, entryOwner: 'ai', exitOwner: 'rules', scopeLabel: 'offline scope' });
    for (const mode of modes) {
      const button = findOne(view, node => node.props['data-mode'] === mode)!;
      expect(button.props.disabled).not.toBe(true);
      (button.props.onClick as () => void)();
      expect(configure).toHaveBeenLastCalledWith(mode);
    }
    expect(textContent(view)).toContain('唯一管理者');
    expect(textContent(view)).toContain('不包含人工或外部持仓');
    expect(findAll(view, node => !!node.props['data-action']).every(node => node.props.disabled)).toBe(true);
  });

  it.each(['EDGE', 'CONTINUOUS'] as const)('test_rule_ast_roundtrip %s', trigger => {
    const original = { ...graph(), trigger };
    const editor = new RuleEditor(original);
    editor.fromGraph(original);
    expect(editor.toGraph()).toEqual(original);
    const text = textContent(editor.render());
    for (const token of ['AND', 'OR', 'NOT', trigger, 'stop → timeout', '9007199254740993.00000001', 'HOLD']) expect(text).toContain(token);
    expect(() => editor.save()).toThrow('CONFIRMATION');
    editor.confirmMeaning(); expect(editor.save()).toEqual(original);
    const exported = editor.toGraph();
    (exported.exit_priority as string[]).reverse();
    expect(editor.toGraph().exit_priority).toEqual(['stop', 'timeout']);
    editor.replaceCondition([0], { operator: 'LT', input_ref: 'quote.last', threshold: '2', children: [] });
    expect(editor.toGraph().condition.children[0].operator).toBe('LT');
    expect(() => editor.save()).toThrow('CONFIRMATION');
  });

  it('rejects ambiguous language and invalid graphs without interpreting or executing code', () => {
    const editor = new RuleEditor(graph());
    editor.fromDraft({ graph: graph(), ambiguities: ['which bar?'], source_ref: { id: 'draft-19', hash: 'hash', environment: 'PAPER', source: 'IP-19', visible_at: '2026-01-01T00:00:00Z' } });
    expect(() => editor.confirmMeaning()).toThrow('AMBIGUITIES');
    editor.replaceCondition([0], { operator: 'LT', input_ref: 'quote.last', threshold: '2', children: [] });
    expect(() => editor.confirmMeaning()).toThrow('AMBIGUITIES');
    expect(textContent(editor.render())).toContain('draft-19');
    expect(() => editor.fromGraph({ ...graph(), cooldown_seconds: -1 })).toThrow();
    expect(() => editor.fromGraph({ ...graph(), target_value: '1e9' })).toThrow();
    expect(() => editor.replaceCondition([99], graph().condition)).toThrow('PATH');
    editor.fromGraph(graph()); editor.confirmMeaning(); expect(editor.save()).toEqual(graph());
  });

  it.each(['RETAIN_OWNER', 'TRANSFER_OWNER', 'EXIT_THEN_SWITCH'] as const)('test_transfer_progress %s', mode => {
    const text = textContent(TransferPanel({ transfer: transfer(mode) }));
    for (const value of [mode, 'old-strategy', 'new-strategy', 'RECONCILE_PENDING', 'MARKET_CLOSED']) expect(text).toContain(value);
    expect(text).not.toContain('已全部退出');
  });
  it.each(['EXITING', 'EXIT_PENDING_MARKET', 'MANUAL_REQUIRED', 'NONTRADABLE_RESIDUAL'] as const)('does not label unfinished %s as complete', state => {
    expect(renderTransferFixture({ state, actualQuantity: '1' }).text()).not.toContain('已全部退出');
  });
  it('requires flat evidence as well as ACTIVE, no residual, and no blockers', () => {
    const t = { ...transfer(), state: 'ACTIVE' as const, blocked_reasons: [], durable_steps: [{ id: 'step', hash: 'hash', source: 'owner', environment: 'PAPER' as const, visible_at: '2026-01-01T00:00:00Z' }] };
    expect(textContent(TransferPanel({ transfer: t }))).not.toContain('已全部退出');
    const exit = { position_id: 'pos', state: 'FLAT_VERIFIED' as const, command_ids: ['cmd'], incomplete_reasons: [] };
    for (const value of ['1', '0.00000000000000000001']) expect(textContent(TransferPanel({ transfer: t, exit, actualQuantity: { value, unit: 'BASE' } }))).not.toContain('已全部退出');
    expect(textContent(TransferPanel({ transfer: t, exit, actualQuantity: { value: '0.000', unit: 'BASE' } }))).toContain('已全部退出');
  });

  it('test_authorization_no_defaults', async () => {
    const send = vi.fn(async () => receipt);
    const editor = new PolicyEditor('LIVE', { send }, () => true, () => 'key');
    const view = editor.render(policy('LIVE'));
    expect(findAll(view, node => node.type === 'input' && node.props.inputMode).map(node => node.props.value)).toEqual(['', '']);
    expect(findOne(view, node => node.props.type === 'checkbox')?.props.checked).toBe(false);
    await expect(editor.submit(policy('LIVE'), 1)).rejects.toThrow('AMOUNTS');
    editor.setAmounts('100', '1'); editor.confirmDraft(policy('LIVE'));
    await expect(editor.submit(policy('LIVE'), 1)).rejects.toThrow('LIVE_DISABLED');
    expect(send).not.toHaveBeenCalled();
  });
  it('requires independent hosted confirmation, exact draft confirmation, and matching CAS', async () => {
    const editor = new PolicyEditor('PAPER', { send: async () => receipt }, () => true, () => 'key');
    const base = policy();
    const draft = { ...base, policy: { ...base.policy, protection_policy: 'HOSTED_ALLOWED_CASH_ONLY' as const } };
    editor.setAmounts('100', '1');
    expect(() => editor.confirmDraft(draft)).toThrow('HOSTED');
    editor.confirmHostedRisk(true); editor.confirmDraft(draft);
    await expect(editor.submit(draft, 2)).rejects.toThrow('REVISION');
    await expect(editor.submit({ ...draft, author_id: 'changed' }, 1)).rejects.toThrow('CONFIRMATION');
    expect(await editor.submit(draft, 1)).toEqual(receipt);
    editor.setAmounts('100', '1'); expect(() => editor.confirmDraft(draft)).toThrow('HOSTED');
  });
  it('preserves policy key and If-Match across explicit retry; never automatically replays', async () => {
    let online = true;
    const submissions: PolicySubmission[] = [];
    const send = vi.fn(async (submission: PolicySubmission) => { submissions.push(submission); if (submissions.length === 1) throw new Error('connection lost'); return receipt; });
    const key = vi.fn(() => 'policy-intent-30');
    const editor = new PolicyEditor('PAPER', { send }, () => online, key);
    editor.setAmounts('100', '1'); editor.confirmDraft(policy());
    await expect(editor.submit(policy(), 1)).rejects.toThrow('connection lost');
    online = false; await expect(editor.submit(policy(), 1)).rejects.toThrow('UNAVAILABLE');
    online = true; expect(send).toHaveBeenCalledTimes(1);
    await Promise.all([editor.submit(policy(), 1), editor.submit(policy(), 1)]);
    expect(send).toHaveBeenCalledTimes(2); expect(key).toHaveBeenCalledTimes(1);
    expect(submissions[1]).toEqual(submissions[0]);
    expect(submissions[0].headers).toEqual({ 'Idempotency-Key': 'policy-intent-30', 'If-Match': '1' });
  });
  it('deduplicates command clicks and keeps original request and key after uncertainty', async () => {
    let online = true;
    const command = vi.fn().mockRejectedValueOnce(new Error('lost')).mockResolvedValue(receipt);
    const input = request(); const attempt = new ControlAttempt({ command }, input, 'intent-30', () => online);
    await expect(attempt.submit()).rejects.toThrow('lost');
    online = false; await expect(attempt.submit()).rejects.toThrow('UNAVAILABLE');
    online = true; expect(command).toHaveBeenCalledTimes(1);
    const results = await Promise.all([attempt.submit(), attempt.submit()]);
    expect(results).toEqual([receipt, receipt]); expect(command).toHaveBeenCalledTimes(2);
    expect(command).toHaveBeenLastCalledWith(input, 'intent-30');
    await attempt.submit(); expect(command).toHaveBeenCalledTimes(2);
    expect(textContent(StrategyCenter({ environment: 'LIVE', mode: 'RULE', onConfigure: () => undefined, entryOwner: '', exitOwner: '', scopeLabel: '' }))).toContain('LIVE 未启用');
  });
  it('shows research provenance, missing costs and leakage for both experiment modes', () => {
    for (const mode of ['REPLAY_OUTPUTS', 'REINFER'] as const) {
      const r = report(); const text = textContent(ResearchView({ report: { ...r, config: { ...r.config, mode } } }));
      for (const token of ['dataset-hash', 'publication-time', '费用 缺失', '模型费 缺失', '信息泄漏', '不构成 LIVE']) expect(text).toContain(token);
    }
  });

  it('rejects unsupported inputs, excessive trees and undefined closed-bar windows atomically', () => {
    const original = graph(); const editor = new RuleEditor(original);
    expect(() => editor.fromGraph({ ...original, window_seconds: 0 })).toThrow('INVALID_GRAPH');
    expect(() => editor.replaceCondition([0], { operator: 'GT', input_ref: 'future.price', threshold: '1', children: [] })).toThrow('COMPARISON');
    const leaf = original.condition.children[0];
    expect(() => editor.fromGraph({ ...original, condition: { operator: 'ALL', input_ref: null, threshold: null, children: Array.from({ length: 128 }, () => leaf) } })).toThrow('TOO_LARGE');
    let deep = leaf;
    for (let i = 0; i < 16; i++) deep = { operator: 'NOT', input_ref: null, threshold: null, children: [deep] };
    expect(() => editor.fromGraph({ ...original, condition: deep })).toThrow('TOO_DEEP');
    expect(editor.toGraph()).toEqual(original);
    // Signed targets are part of the compiler contract; no float conversion.
    editor.fromGraph({ ...original, target_value: '-9007199254740993.00000001' });
    expect(editor.toGraph().target_value).toBe('-9007199254740993.00000001');
  });

  it('drives visual controls and displays immutable Python artifact references', () => {
    const editor = new RuleEditor(graph()); const changed = vi.fn();
    const view = editor.render(changed, { artifact_ref: 'python-artifact', hash: 'artifact-hash', dependency_lock_hash: 'lock-hash' });
    const input = findOne(view, node => node.props['aria-label'] === '输入 0')!;
    (input.props.onChange as (e: { target: { value: string } }) => void)({ target: { value: 'quote.bid' } });
    expect(editor.toGraph().condition.children[0].input_ref).toBe('quote.bid');
    expect(changed).toHaveBeenCalledTimes(1);
    for (const token of ['python-artifact', 'artifact-hash', 'lock-hash', '隔离评估']) expect(textContent(view)).toContain(token);
  });

  it('fails closed for LIVE, action/environment mismatch, and availability revoked before dispatch', async () => {
    const command = vi.fn(async () => receipt); const onReceipt = vi.fn(); const onError = vi.fn();
    const attempt = new ControlAttempt({ command }, request(), 'bound-key', () => true);
    const view = StrategyCenter({ environment: 'PAPER', mode: 'RULE', onConfigure: vi.fn(), entryOwner: 'old', exitOwner: 'old', scopeLabel: 'offline', controls: { exit_owned: attempt }, onReceipt, onError });
    const button = findOne(view, node => node.props['data-action'] === 'exit_owned')!;
    expect(button.props.disabled).toBe(true);
    await (button.props.onClick as () => Promise<void>)();
    const live = new ControlAttempt({ command }, { ...request(), scope: { ...request().scope, environment: 'LIVE' } }, 'live-key', () => true);
    await expect(live.submit()).rejects.toThrow('LIVE_DISABLED');
    expect(attempt.matches('pause_new', 'TESTNET')).toBe(false);
    let available = true;
    const revoked = new ControlAttempt({ command }, request(), 'revoked-key', () => available);
    const pending = revoked.submit(); available = false;
    await expect(pending).rejects.toThrow('UNAVAILABLE');
    expect(command).not.toHaveBeenCalled(); expect(onReceipt).not.toHaveBeenCalled();
  });

  it('keeps server rejections visible and rejects malformed command receipts', async () => {
    const rejected = { ...receipt, status: 'REJECTED' as const, reason: 'STALE_OWNER_EPOCH' };
    const command = vi.fn(async () => rejected);
    const attempt = new ControlAttempt({ command }, request(), 'stale-key', () => true);
    expect(await attempt.submit()).toEqual(rejected);
    expect(await attempt.submit()).toEqual(rejected);
    expect(command).toHaveBeenCalledTimes(1);
    const invalid = new ControlAttempt({ command: async () => ({ ...receipt, command_id: '' }) }, request(), 'invalid-key', () => true);
    await expect(invalid.submit()).rejects.toThrow('INVALID_RECEIPT');
  });

  it('snapshots requests so caller or transport mutation cannot alter a retry', async () => {
    const original = request(); const expected = structuredClone(original);
    const command = vi.fn(async (sent: typeof original): Promise<typeof receipt> => {
      (sent as { ownership_epoch: number }).ownership_epoch = 999;
      throw new Error('lost');
    });
    const attempt = new ControlAttempt({ command }, original, 'immutable-key', () => true);
    (original as { ownership_epoch: number }).ownership_epoch = 888;
    await expect(attempt.submit()).rejects.toThrow('lost');
    command.mockImplementationOnce(async sent => { expect(sent).toEqual(expected); return receipt; });
    expect(await attempt.submit()).toEqual(receipt);
  });

  it('prevents replacement of uncertain policy intent and propagation of transport mutation', async () => {
    const calls: PolicySubmission[] = [];
    const send = vi.fn(async (submission: PolicySubmission) => {
      calls.push(structuredClone(submission));
      (submission.draft as { author_id: string }).author_id = 'mutated';
      throw new Error('unknown');
    });
    const editor = new PolicyEditor('PAPER', { send }, () => true, () => 'stable-key');
    editor.setAmounts('100', '1'); editor.confirmDraft(policy());
    await expect(editor.submit(policy(), 1)).rejects.toThrow('unknown');
    const changed = { ...policy(), author_id: 'other' }; editor.confirmDraft(changed);
    await expect(editor.submit(changed, 1)).rejects.toThrow('RECONCILE');
    editor.confirmDraft(policy());
    await expect(editor.submit(policy(), 1)).rejects.toThrow('unknown');
    expect(calls[1]).toEqual(calls[0]);
  });

  it('rejects cross-environment and risk-bypass policies without sending', async () => {
    const send = vi.fn(async () => receipt);
    const editor = new PolicyEditor('PAPER', { send }, () => true, () => 'key');
    editor.setAmounts('100', '1');
    await expect(editor.submit(policy('TESTNET'), 1)).rejects.toThrow('CROSS_ENVIRONMENT');
    await expect(editor.submit({ ...policy(), policy: { ...policy().policy, live_enabled: true } }, 1)).rejects.toThrow('LIVE_DISABLED');
    await expect(editor.submit({ ...policy(), policy: { ...policy().policy, native_risk_bypass: true as never } }, 1)).rejects.toThrow('RISK_BYPASS');
    expect(send).not.toHaveBeenCalled();
  });
});
