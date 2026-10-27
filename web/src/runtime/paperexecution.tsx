import type { Dashboard, Order } from './types';
import { Alert, Badge, Empty, Panel } from './ui';

export const paperOrderLabel = (status?: string) => ({
  PENDING: '已排队 · 等待执行', DURABLE_READY: '已持久化 · 等待执行',
  SUBMITTED: '已提交 · 等待成交事件', PARTIALLY_FILLED: '部分成交',
  FILLED: '已成交', CANCEL_UNKNOWN: '撤单结果未知', UNKNOWN: '结果未知 · 待核对',
  EXPIRED: '已过期', REJECTED: '已拒绝', CANCELED: '已撤销',
}[status ?? ''] ?? '状态待核对');

export const orderFilledQuantity = (order: Order): string => order.filled_quantity ?? (order.status === 'FILLED' ? order.quantity : '0');
export const orderHasFill = (order: Order): boolean => /[1-9]/.test(orderFilledQuantity(order));
export const paperProtectionLabel = (state: string): string => ({
  GAP: '保护缺口', PENDING: '保护待确认', REPAIRING: '保护修复中',
  VERIFIED_SYNTHETIC: '模拟保护已验证', VERIFIED_NATIVE: '模拟保护已验证',
  VERIFIED_HOSTED: '托管保护已验证', EXITING: '退出处理中', FLAT_VERIFIED: '已核对为空仓',
  UNCONFIGURED: '未配置', UNVERIFIED: '未验证',
}[state] ?? state);

export function PaperExecutionView({ data }: { data: Dashboard }) {
  const execution = data.execution;
  const locks = (execution?.risk_locks ?? data.risk_locks ?? []).filter(lock => lock.state === 'ACTIVE');
  return <Panel title="PAPER 执行进度" caption="目标、在途和成交分别记录；收到请求回执不代表成交" className="paper-execution">
    <div className="panel-body">
      <p className="field-help">{execution?.simulation_notice ?? '当前快照未提供统一执行链证据。历史本地账本不代表已通过统一 PAPER 验收。'}</p>
      {execution && <p className="field-help">执行模式：{execution.mode} · 模拟无法验证真实滑点和流动性</p>}
      {locks.length > 0 && <Alert error><strong>新增风险受限</strong><ul className="paper-locks">{locks.map(lock => <li key={lock.lock_id}>{lock.cause} · {lock.permission}<span className="field-help">{lock.lock_id}</span></li>)}</ul>风险锁需有证据的审核解除；价格回升或恢复开仓不会清除。</Alert>}
      {execution?.targets.length ? <div className="paper-targets">{execution.targets.map((target, index) => <section className="paper-target" key={`${target.symbol}:${target.strategy_id ?? 'manual'}:${index}`} aria-label={`${target.symbol} 执行进度`}>
        <h3>{target.symbol}<span className="field-help">{target.strategy_id ?? '手动操作'}</span></h3>
        <dl className="paper-state-columns">
          <div><dt>目标数量</dt><dd>{target.target_quantity}</dd></div>
          <div><dt>在途数量</dt><dd>{target.in_flight_quantity}</dd></div>
          <div><dt>实际成交持仓</dt><dd>{target.actual_quantity}</dd></div>
          <div><dt>保护已验证数量</dt><dd>{['VERIFIED_SYNTHETIC', 'VERIFIED_NATIVE', 'VERIFIED_HOSTED'].includes(target.protection_state) ? target.protected_quantity : '未验证'}<span className="field-help">{target.protection_state}</span></dd></div>
          <div><dt>风险锁</dt><dd>{locks.length ? `${locks.length} 项生效` : '无已报告的活动锁'}</dd></div>
        </dl>
        {target.reason && <p className="field-help">{target.reason}</p>}
      </section>)}</div> : <Empty compact title="尚无统一目标记录" text="提交请求后在这里查看目标与实际状态；提交成功不会直接增加成交数量。" />}
      {execution && execution.commands.length > 0 && <details className="paper-command-details"><summary>命令与订单详情（{execution.commands.length}）</summary><div className="table-scroller" role="region" tabIndex={0} aria-label="PAPER 命令与订单状态，可横向滚动"><table className="data-table"><thead><tr><th>命令 / 标的</th><th>命令状态</th><th>订单状态</th><th>委托数量</th><th>已成交数量</th><th>余单数量</th></tr></thead><tbody>{execution.commands.map(command => <tr key={command.command_id}><td>{command.symbol}<div className="asset-sub">{command.command_id}</div>{command.reason && <div className="field-help">{command.reason}</div>}</td><td>{command.state}</td><td><Badge kind={command.order_status === 'FILLED' ? 'pass' : command.order_status.includes('UNKNOWN') ? 'fail' : 'wait'}>{paperOrderLabel(command.order_status)}</Badge></td><td className="t-num">{command.quantity}</td><td className="t-num">{command.filled_quantity}</td><td className="t-num">{command.leaves_quantity}</td></tr>)}</tbody></table></div></details>}
    </div>
  </Panel>;
}
