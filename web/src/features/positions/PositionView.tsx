// Read-only position/order page. The four business states are kept as four
// separate rows: submitted, filled, protected, exited. They are never merged
// into one badge and an absent state stays visible as "not reported".

import type {
  ExitReceipt,
  FreshnessView,
  OrderProjection,
  PositionProjection,
} from '../../contracts/generated';
import type { ViewChild, ViewElement } from '../../app/App';

export interface PositionViewProps {
  readonly position: PositionProjection;
  readonly freshness: FreshnessView;
  readonly orders?: ReadonlyArray<OrderProjection>;
  readonly exits?: ReadonlyArray<ExitReceipt>;
  readonly offline?: boolean;
  readonly controlAvailable?: boolean;
  readonly onRequestExit?: () => void;
}

export interface PositionStateRow {
  readonly key: 'submitted' | 'filled' | 'protected' | 'exited';
  readonly label: string;
  readonly raw_code: string;
  readonly present: boolean;
  readonly details: ReadonlyArray<string>;
  readonly missing: ReadonlyArray<string>;
}

export interface ExitControlDecision {
  readonly allowed: boolean;
  readonly reason_code: string;
  readonly message: string;
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

export function buildPositionStateRows(props: PositionViewProps): ReadonlyArray<PositionStateRow> {
  const positionScope = props.position.position.scope;
  const orders = (props.orders ?? []).filter(order => {
    const scope = order.order.identity.scope;
    return (
      scope.environment === positionScope.environment &&
      scope.venue_account_id === positionScope.venue_account_id &&
      scope.product === positionScope.product &&
      scope.instrument_uid === positionScope.instrument_uid &&
      scope.venue_leg === positionScope.venue_leg
    );
  });
  const protection = props.position.protection;
  const exits = (props.exits ?? []).filter(exit => exit.position_id === props.position.position.position_id);

  const submittedPresent = orders.some(
    order => order.lifecycle_state !== 'INITIALIZED' || order.pending_operation !== 'NONE',
  );
  const submittedRaw = orders.length === 0
    ? 'NO_ORDER_REPORTED'
    : orders
        .map(order => `${order.lifecycle_state}/${order.pending_operation}/${order.command_certainty}`)
        .join(';');

  const filledRaw = orders.length === 0
    ? 'NO_FILL_REPORTED'
    : orders
        .map(order => `${order.filled_quantity.value}+${order.leaves_quantity.value}${order.filled_quantity.unit}`)
        .join(';');
  const filledPresent = orders.some(
    order => order.filled_quantity.value !== '0' || order.lifecycle_state === 'FILLED' || order.lifecycle_state === 'PARTIALLY_FILLED',
  );

  const protectionRaw = protection === null
    ? 'NO_PROTECTION_PLAN'
    : `${protection.mode}/${protection.state}`;
  const exitRaw = exits.length > 0
    ? exits.map(exit => exit.state).join(';')
    : props.position.position.actual_quantity.value === '0'
      ? 'QUANTITY_ZERO_AND_NO_EXIT_RECEIPT'
      : 'NO_EXIT_REPORTED';
  const exitPresent = exits.length > 0 && exits.every(exit => exit.state === 'FLAT_VERIFIED');

  return [
    {
      key: 'submitted',
      label: '已提交',
      raw_code: submittedRaw,
      present: submittedPresent,
      details: orders.map(
        order =>
          `逻辑订单 ${order.order.identity.logical_client_order_id} · 生命周期 ${order.lifecycle_state} · 挂起操作 ${order.pending_operation} · 确定性 ${order.command_certainty} · 来源 ${order.outcome_provenance} · 修订 ${String(order.lifecycle_revision)}`,
      ),
      missing: submittedPresent ? [] : ['order_projection'],
    },
    {
      key: 'filled',
      label: '已成交',
      raw_code: filledRaw,
      present: filledPresent,
      details: orders.map(
        order =>
          `已成交 ${order.filled_quantity.value} ${order.filled_quantity.unit} · 剩余 ${order.leaves_quantity.value} ${order.leaves_quantity.unit} · 序列 ${String(order.sequence)}`,
      ),
      missing: filledPresent ? [] : ['fill_evidence'],
    },
    {
      key: 'protected',
      label: '已保护',
      raw_code: protectionRaw,
      present: protection !== null,
      details:
        protection === null
          ? []
          : [
              `保护方式 ${protection.mode}`,
              `保护状态 ${protection.state}`,
              `覆盖 ${protection.coverage_quantity.value} ${protection.coverage_quantity.unit}`,
              `触发 ${protection.stop_trigger} ${protection.stop_price.value} ${protection.stop_price.currency}`,
              `最近核验 ${protection.last_verified_at ?? '未提供'}`,
              `覆盖缺口起点 ${protection.coverage_gap_since ?? '无'}`,
              `替换修订 ${String(protection.replacement_revision)}`,
              `缺口政策 ${protection.on_gap_policy}`,
            ],
      missing: protection === null ? ['protection_plan'] : [],
    },
    {
      key: 'exited',
      label: '已退出',
      raw_code: exitRaw,
      present: exitPresent,
      details: exits.map(
        exit =>
          `退出状态 ${exit.state} · 命令 ${exit.command_ids.join(',') || '无'} · 未完成原因 ${exit.incomplete_reasons.join(',') || '无'}`,
      ),
      missing: exitPresent ? [] : ['flat_exit_receipt'],
    },
  ];
}

/**
 * The UI only enables `exit_owned` when it can be an honest, in-scope reduce
 * action. Offline/stale/unknown states show the denial instead of pretending
 * the click was queued.
 */
export function evaluateExitControl(props: PositionViewProps): ExitControlDecision {
  if (props.offline === true) {
    return {
      allowed: false,
      reason_code: 'OFFLINE_NO_CONTROL_WRITE',
      message: '离线：退出命令不会排队，也不会在重连后自动重放。',
    };
  }
  if (props.freshness.stale) {
    return {
      allowed: false,
      reason_code: 'DATA_STALE',
      message: '数据新鲜度不足：先重新取快照。',
    };
  }
  if (props.position.position.actual_quantity.value === '0') {
    return {
      allowed: false,
      reason_code: 'ALREADY_FLAT',
      message: '投影仓位为 0；仍需退出回执才能确认已退出。',
    };
  }
  if (props.controlAvailable !== true || props.onRequestExit === undefined) {
    return {
      allowed: false,
      reason_code: 'CONTROL_PATH_UNAVAILABLE',
      message: '控制写路径不可用；本页不直接调用交易接口。',
    };
  }
  return {
    allowed: true,
    reason_code: 'ALLOWED_REDUCE_ONLY',
    message: '仅对自有持仓发起减险退出命令；回执不代表平台动作完成。',
  };
}

export function PositionView(props: PositionViewProps): ViewElement {
  const position = props.position;
  const managed = position.position;
  const rows = buildPositionStateRows(props);
  const decision = evaluateExitControl(props);
  const freshness = props.freshness;
  const riskLocks = managed.risk_locks;

  const stateRows = rows.map(row =>
    el(
      'li',
      {
        'data-testid': `position-state-${row.key}`,
        'data-state-key': row.key,
        'data-raw-code': row.raw_code,
        'data-present': row.present ? 'true' : 'false',
      },
      [
        el('h4', { text: row.label }),
        el('span', { 'data-field': 'raw_state', text: row.raw_code }),
        row.details.length === 0
          ? el('span', { 'data-code': 'STATE_NOT_REPORTED', text: '尚未报告该项，不能据此推断。' })
          : el(
              'ul',
              {},
              row.details.map(detail => el('li', { text: detail })),
            ),
        row.missing.length === 0
          ? el('span', { text: '' })
          : el('span', { 'data-field': 'missing', text: `缺失证据：${row.missing.join(',')}` }),
      ],
    ),
  );

  const lockList =
    riskLocks.length === 0
      ? el('p', { 'data-code': 'NO_RISK_LOCK', text: '无风险锁字段。' })
      : el(
          'ul',
          { 'data-testid': 'position-risk-locks' },
          riskLocks.map(lock =>
            el('li', {}, [
              el('span', { text: `${lock.lock_id} · ${lock.state} · ${lock.permission} · ${lock.cause}` }),
            ]),
          ),
        );

  return el(
    'article',
    {
      'data-testid': `position-${managed.position_id}`,
      'data-position-id': managed.position_id,
      'data-environment': managed.scope.environment,
    },
    [
      el('header', { 'data-testid': 'position-header' }, [
        el('h3', { text: `持仓 ${managed.position_id}` }),
        el('span', {
          'data-field': 'instrument',
          text: `${managed.scope.product}/${managed.scope.instrument_uid}/${managed.scope.venue_leg}`,
        }),
        el('span', {
          'data-field': 'manager',
          text: managed.owner_strategy_id === null ? '未归属策略（外部/人工需保留）' : managed.owner_strategy_id,
        }),
        el('span', {
          'data-field': 'ownership_epoch',
          text: `管理权 epoch ${String(managed.ownership_epoch)}`,
        }),
        el('span', {
          'data-field': 'actual_quantity',
          text: `实际仓位 ${managed.actual_quantity.value} ${managed.actual_quantity.unit}`,
        }),
        el('span', {
          'data-field': 'planned_exit',
          text: managed.active_exit_plan_id === null ? '无计划退出原因' : `计划退出 ${managed.active_exit_plan_id}`,
        }),
        el('span', {
          'data-field': 'pnl',
          text: `已实现 ${managed.realized_pnl?.amount ?? '未提供'} ${managed.realized_pnl?.currency ?? ''} · 未实现 ${managed.unrealized_valuation?.amount ?? '未提供'} ${managed.unrealized_valuation?.currency ?? ''}`,
        }),
        el('span', {
          'data-field': 'freshness',
          text: `数据时点 ${freshness.as_of} · 年龄 ${String(freshness.age_ms)} ms · stale ${freshness.stale ? 'true' : 'false'}`,
        }),
      ]),
      el('section', { 'data-testid': 'position-states' }, [
        el('h3', { text: '四态（各自独立显示）' }),
        el('ul', {}, stateRows),
      ]),
      el('section', { 'data-testid': 'position-exit-control' }, [
        el('h3', { text: '退出控制' }),
        el('button', {
          type: 'button',
          'data-action': 'exit-owned',
          'data-denial-code': decision.allowed ? 'NONE' : decision.reason_code,
          disabled: !decision.allowed,
          onClick: decision.allowed ? props.onRequestExit : null,
        }, [el('span', { text: '退出自有持仓' })]),
        el('p', {
          'data-testid': 'exit-control-note',
          'data-code': decision.reason_code,
          text: decision.message,
        }),
      ]),
      lockList,
    ],
  );
}
