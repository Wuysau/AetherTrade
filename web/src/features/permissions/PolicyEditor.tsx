import { h, type ViewElement } from '../../app/App';
import type { Environment, RiskPolicyDraft, ControlCommandReceipt, PolicyEditor as PolicyEditorPort } from '../../contracts/generated';

export interface PolicySubmission {
  readonly path: string;
  readonly headers: Readonly<{ 'Idempotency-Key': string; 'If-Match': string }>;
  readonly draft: RiskPolicyDraft;
}
/** Host binds its authenticated control transport; no production fallback. */
export interface PolicySubmitPort {
  send(submission: PolicySubmission): Promise<ControlCommandReceipt>;
}
export class PolicyEditor implements PolicyEditorPort {
  private capital = '';
  private leverage = '';
  private hosted = false;
  private confirmed: string | null = null;
  private pending: Promise<ControlCommandReceipt> | null = null;
  private attempt: { fingerprint: string; key: string; receipt: ControlCommandReceipt | null } | null = null;
  constructor(readonly environment: Environment, private readonly port: PolicySubmitPort | null,
    private readonly available: () => boolean, private readonly newKey: () => string) {}

  setAmounts(capital: string, leverage: string): void {
    this.capital = capital; this.leverage = leverage; this.confirmed = null; this.hosted = false;
  }
  confirmHostedRisk(value: boolean): void { this.hosted = value; this.confirmed = null; }
  toDraft(base: RiskPolicyDraft): RiskPolicyDraft {
    return { ...structuredClone(base), policy: { ...structuredClone(base.policy),
      maximum_capital: this.capital, maximum_leverage: Number(this.leverage) } };
  }
  private validate(draft: RiskPolicyDraft, expectedRevision: number): void {
    const p = draft.policy;
    if (p.environment !== this.environment) throw new Error('CROSS_ENVIRONMENT_POLICY');
    if (!/^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/.test(this.capital) || /^0(?:\.0+)?$/.test(this.capital) ||
      !/^[1-9][0-9]*$/.test(this.leverage) || !Number.isSafeInteger(Number(this.leverage)) ||
      p.maximum_capital !== this.capital || p.maximum_leverage !== Number(this.leverage)) throw new Error('EXPLICIT_AMOUNTS_REQUIRED');
    if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 1 || draft.expected_revision !== expectedRevision ||
      p.revision !== expectedRevision + 1) throw new Error('POLICY_REVISION_CONFLICT');
    if (p.native_risk_bypass !== false || p.live_enabled) throw new Error('RISK_BYPASS_OR_LIVE_DISABLED');
    if (p.protection_policy === 'HOSTED_ALLOWED_CASH_ONLY' && (!this.hosted || p.maximum_leverage !== 1)) throw new Error('HOSTED_CASH_RISK_CONFIRMATION_REQUIRED');
  }
  confirmDraft(draft: RiskPolicyDraft): void {
    this.validate(draft, draft.expected_revision);
    this.confirmed = JSON.stringify(draft);
  }
  async submit(draft: RiskPolicyDraft, expectedRevision: number): Promise<ControlCommandReceipt> {
    this.validate(draft, expectedRevision);
    if (this.environment === 'LIVE') throw new Error('LIVE_DISABLED');
    if (!this.port || !this.available()) throw new Error('POLICY_CONTROL_UNAVAILABLE');
    const fingerprint = JSON.stringify(draft);
    if (this.confirmed !== fingerprint) throw new Error('EXACT_POLICY_CONFIRMATION_REQUIRED');
    // An unknown result cannot be replaced by a new policy/key. Reconcile and
    // construct a new editor from the next server revision for another intent.
    if (this.attempt && this.attempt.fingerprint !== fingerprint) throw new Error('RECONCILE_PREVIOUS_POLICY_FIRST');
    if (this.attempt?.receipt) return structuredClone(this.attempt.receipt);
    if (this.pending) return this.pending;
    if (!this.attempt) {
      const key = this.newKey();
      if (!key.trim()) throw new Error('IDEMPOTENCY_KEY_REQUIRED');
      this.attempt = { fingerprint, key, receipt: null };
    }
    const submission: PolicySubmission = {
      path: `/risk-policies/${encodeURIComponent(draft.policy.policy_id)}/revisions`,
      headers: { 'Idempotency-Key': this.attempt.key, 'If-Match': String(expectedRevision) },
      draft: structuredClone(draft),
    };
    this.pending = Promise.resolve().then(() => {
      if (!this.available()) throw new Error('POLICY_CONTROL_UNAVAILABLE');
      return this.port!.send(submission);
    }).then(receipt => {
      if (!['QUEUED', 'IN_PROGRESS', 'APPLIED', 'REJECTED'].includes(receipt.status) || !receipt.command_id) throw new Error('INVALID_RECEIPT');
      this.attempt!.receipt = structuredClone(receipt); return receipt;
    }).finally(() => { this.pending = null; });
    return this.pending;
  }
  render(draft: RiskPolicyDraft, onChange: () => void = () => undefined,
    onReceipt?: (receipt: ControlCommandReceipt) => void, onError?: (error: unknown) => void): ViewElement {
    draft = this.toDraft(draft);
    return h('section', { 'aria-label': '权限设置' }, [
      h('h2', { text: `${this.environment} 独立授权草稿` }),
      h('input', { 'aria-label': '最大资金', inputMode: 'decimal', value: this.capital,
        onChange: (event: { target: { value: string } }) => { this.setAmounts(event.target.value, this.leverage); onChange(); } }),
      h('input', { 'aria-label': '最大杠杆', inputMode: 'numeric', value: this.leverage,
        onChange: (event: { target: { value: string } }) => { this.setAmounts(this.capital, event.target.value); onChange(); } }),
      h('label', {}, [h('input', { type: 'checkbox', checked: this.hosted, 'aria-label': '独立确认托管保护风险',
        onChange: (event: { target: { checked: boolean } }) => { this.confirmHostedRisk(event.target.checked); onChange(); } }),
        '托管保护依赖主机、网络和交易时段；盘外可能不可执行，仅全额现金，不是原生保护']),
      h('pre', { text: JSON.stringify(draft.policy, null, 2), 'aria-label': '待确认政策全文' }),
      h('button', { type: 'button', text: '确认当前完整政策', onClick: () => {
        try { this.confirmDraft(draft); onChange(); } catch (error) { onError?.(error); }
      } }),
      h('button', { type: 'button', text: '提交政策修订', disabled: this.environment === 'LIVE' || !this.port ||
        !this.available() || !!this.pending || this.confirmed !== JSON.stringify(draft) || !onReceipt || !onError,
        onClick: async () => {
          if (!onReceipt || !onError) return;
          try { onReceipt(await this.submit(draft, draft.expected_revision)); } catch (error) { onError(error); }
          onChange();
        } }),
      h('p', { role: 'status', text: '资金与杠杆不从 PAPER 或历史授权预填。前端确认不替代后端授权、能力核验及 CAS；提交回执不是政策已生效。LIVE 未启用。' }),
    ]);
  }
}
