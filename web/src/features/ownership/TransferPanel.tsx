import { h, type ViewElement } from '../../app/App';
import type { ExitReceipt, Quantity, TransferProjection } from '../../contracts/generated';

export interface TransferPanelProps {
  readonly transfer: TransferProjection;
  readonly exit?: ExitReceipt;
  readonly actualQuantity?: Quantity;
}
const meaning = {
  RETAIN_OWNER: '旧策略停止开仓并继续管理旧仓；新模式只接收无冲突的新管理域',
  TRANSFER_OWNER: '冻结新增、对账、确认保护后原子递增 epoch 并切换管理者',
  EXIT_THEN_SWITCH: '旧管理者退出；真实归零且订单终态核实后才交接，休市保持退出中',
};
export function TransferPanel({ transfer: t, exit, actualQuantity }: TransferPanelProps): ViewElement {
  // Projection ACTIVE alone cannot prove a flat position or an executed exit.
  const flat = t.request.mode === 'EXIT_THEN_SWITCH' && t.state === 'ACTIVE' &&
    t.blocked_reasons.length === 0 && t.durable_steps.length > 0 &&
    exit?.state === 'FLAT_VERIFIED' && exit.incomplete_reasons.length === 0 &&
    actualQuantity !== undefined && /^-?0(?:\.0+)?$/.test(actualQuantity.value);
  return h('section', { 'aria-label': '管理权交接' }, [
    h('h2', { text: `交接 ${t.request.transfer_id} · ${t.request.mode}` }),
    h('p', { text: meaning[t.request.mode] }),
    h('p', { text: `${t.request.scope.environment} / ${t.request.scope.venue_account_id} / ${t.request.scope.product} / ${t.request.scope.instrument_uid} / ${t.request.scope.venue_leg}` }),
    h('p', { text: `旧责任人 ${t.request.old_owner}；目标责任人 ${t.request.new_owner}；阶段 ${t.state}；ownership_epoch ${t.ownership_epoch}` }),
    h('p', { text: `实际数量 ${actualQuantity ? `${actualQuantity.value} ${actualQuantity.unit}` : '缺失'}；退出状态 ${exit?.state ?? '缺少退出回执'}` }),
    h('p', { role: 'status', text: flat ? '已全部退出' : '未确认全部退出；以服务端对账与交接证据为准' }),
    h('ul', {}, t.durable_steps.map(step => h('li', { text: `${step.id} · ${step.hash} · ${step.source} · ${step.visible_at}` }))),
    h('p', { text: `阻断/未完成原因：${[...t.blocked_reasons, ...(exit?.incomplete_reasons ?? [])].join('；') || '未报告'}；交接期间系统继续维护保护` }),
  ]);
}
