import { h, type ViewElement } from '../../app/App';
import type { ControlApiClient, ControlCommandRequest, ControlCommandReceipt, Environment } from '../../contracts/generated';

export type StrategyMode = 'AI' | 'RULE' | 'HYBRID';
export const modes: ReadonlyArray<StrategyMode> = ['AI', 'RULE', 'HYBRID'];
export const controlMeaning = {
  pause_new: '暂停本管理域新增风险，已有仓位与保护继续管理',
  cancel_entries: '撤销本管理域自有入场订单，保留保护订单',
  exit_owned: '仅退出本管理域自有持仓，不包含人工或外部持仓；回执不代表退出完成',
} as const;

/** One explicit user intent. Retain this instance/key across manual retries.
 * A host must persist the key/request if it supports reload recovery. */
export class ControlAttempt {
  private pending: Promise<ControlCommandReceipt> | null = null;
  private receipt: ControlCommandReceipt | null = null;
  private readonly request: ControlCommandRequest;
  constructor(private readonly client: Pick<ControlApiClient, 'command'>, request: ControlCommandRequest,
    readonly key: string, private readonly available: () => boolean) {
    if (!key.trim()) throw new Error('IDEMPOTENCY_KEY_REQUIRED');
    this.request = structuredClone(request);
  }
  matches(action: string, environment: Environment): boolean {
    return this.request.action === action && this.request.scope.environment === environment;
  }
  isAvailable(): boolean { return this.request.scope.environment !== 'LIVE' && this.available(); }
  submit(): Promise<ControlCommandReceipt> {
    if (!this.available()) return Promise.reject(new Error('CONTROL_UNAVAILABLE_REFRESH_REQUIRED'));
    if (this.request.scope.environment === 'LIVE') return Promise.reject(new Error('LIVE_DISABLED'));
    if (this.receipt) return Promise.resolve(structuredClone(this.receipt));
    if (this.pending) return this.pending;
    this.pending = Promise.resolve().then(() => {
      if (!this.available()) throw new Error('CONTROL_UNAVAILABLE_REFRESH_REQUIRED');
      return this.client.command(structuredClone(this.request), this.key);
    }).then(receipt => {
      if (!receipt.command_id || !['QUEUED', 'IN_PROGRESS', 'APPLIED', 'REJECTED'].includes(receipt.status)) throw new Error('INVALID_RECEIPT');
      this.receipt = structuredClone(receipt);
      return receipt;
    }).finally(() => { this.pending = null; });
    return this.pending;
  }
}

export interface StrategyCenterProps {
  readonly environment: Environment;
  readonly mode: StrategyMode;
  readonly onConfigure: (mode: StrategyMode) => void;
  readonly entryOwner: string;
  readonly exitOwner: string;
  readonly scopeLabel: string;
  readonly controls?: Partial<Record<keyof typeof controlMeaning, ControlAttempt>>;
  readonly onReceipt?: (receipt: ControlCommandReceipt) => void;
  readonly onError?: (error: unknown) => void;
}
export function StrategyCenter(props: StrategyCenterProps): ViewElement {
  return h('section', { 'aria-label': '策略中心' }, [
    h('h2', { text: '策略中心' }),
    h('p', { text: `${props.environment} · ${props.scopeLabel} · 配置草稿不代表授权启用` }),
    h('div', { role: 'group', 'aria-label': '决策模式' }, modes.map(mode => h('button', {
      type: 'button', 'data-mode': mode, 'aria-pressed': props.mode === mode,
      text: { AI: 'AI 自主', RULE: '确定性规则', HYBRID: 'AI / 规则混合' }[mode],
      onClick: () => props.onConfigure(mode),
    }))),
    h('p', { text: `入场责任：${props.entryOwner || '未配置'}；普通退出唯一管理者：${props.exitOwner || '未配置'}；保护管理独立减险` }),
    ...Object.entries(controlMeaning).map(([action, meaning]) => {
      const attempt = props.controls?.[action as keyof typeof controlMeaning];
      const enabled = attempt?.matches(action, props.environment) && attempt.isAvailable() && !!props.onReceipt && !!props.onError;
      return h('button', { type: 'button', 'data-action': action, text: `${action}：${meaning}`,
        disabled: !enabled,
        onClick: async () => {
          if (!attempt || !props.onReceipt || !props.onError || !attempt.matches(action, props.environment) || !attempt.isAvailable()) return;
          try { props.onReceipt(await attempt.submit()); } catch (error) { props.onError(error); }
        },
      });
    }),
    h('p', { role: 'status', text: '后端仍须校验授权、修订与 ownership_epoch。断线不自动重放。LIVE 未启用。' }),
  ]);
}
