// Read-only overview page. Pure render function: no React runtime is installed
// in the web workspace yet.

import type {
  FreshnessView,
  ProjectionPage,
  RiskLock,
  SystemHealth,
} from '../../contracts/generated';
import type { ViewChild, ViewElement } from '../../app/App';

export interface OverviewProps {
  readonly page: ProjectionPage | null;
  readonly freshness: FreshnessView | null;
  readonly offline: boolean;
  readonly health: SystemHealth | null;
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

export interface FreshnessRow {
  readonly stale: boolean;
  readonly raw_code: string;
  readonly as_of: string;
  readonly age_ms: number;
  readonly missing: ReadonlyArray<string>;
}

/** Freshness is never promoted to "fresh" without a server-provided view. */
export function describeFreshness(props: OverviewProps): FreshnessRow {
  if (props.freshness === null) {
    return {
      stale: true,
      raw_code: props.offline ? 'OFFLINE_FRESHNESS_UNKNOWN' : 'FRESHNESS_UNAVAILABLE',
      as_of: 'unknown',
      age_ms: 0,
      missing: ['freshness'],
    };
  }
  return {
    stale: props.freshness.stale || props.offline,
    raw_code: props.offline ? 'OFFLINE_STALE_BY_DEFINITION' : props.freshness.stale ? 'DATA_STALE' : 'FRESH',
    as_of: props.freshness.as_of,
    age_ms: props.freshness.age_ms,
    missing: props.freshness.missing,
  };
}

/** Each lock keeps its own cause/permission/evidence; no merged risk badge. */
export function lockRows(locks: ReadonlyArray<RiskLock>): ReadonlyArray<ReadonlyArray<string>> {
  return locks.map(lock => [
    lock.lock_id,
    lock.state,
    lock.permission,
    lock.cause,
    lock.required_evidence.join(',') || '—',
  ]);
}

export function Overview(props: OverviewProps): ViewElement {
  const page = props.page;
  const freshness = describeFreshness(props);
  const locks = page?.risk_locks ?? [];
  const positions = page?.positions ?? [];
  const orders = page?.orders ?? [];
  const health = props.health;

  const staleBanner = freshness.stale
    ? el('div', {
        'data-testid': 'freshness-stale',
        'data-code': freshness.raw_code,
        role: 'alert',
        text: props.offline
          ? '离线：显示的最后状态可能已过期；需要重新取快照后才可判断。'
          : '数据新鲜度不足；先重新取快照再判断。',
      })
    : el('div', { 'data-testid': 'freshness-ok', 'data-code': freshness.raw_code, text: '数据新鲜度由服务端提供。' });

  const healthRows =
    health === null
      ? [
          el('li', {
            'data-testid': 'health-unavailable',
            'data-code': 'HEALTH_UNAVAILABLE',
            text: '未读取系统健康度；不显示推测的健康状态。',
          }),
        ]
      : health.processes.map(process =>
          el('li', { 'data-testid': `process-${process.process_id}` }, [
            el('span', { 'data-field': 'process_id', text: process.process_id }),
            el('span', { 'data-field': 'state', text: process.state }),
            el('span', { 'data-field': 'observed_at', text: process.observed_at }),
            el('span', {
              'data-field': 'faults',
              text: process.faults.length === 0 ? '无故障字段' : process.faults.join(','),
            }),
          ]),
        );

  const lockList =
    locks.length === 0
      ? el('p', { 'data-code': 'NO_RISK_LOCK', text: '当前投影未报告风险锁。' })
      : el(
          'ul',
          { 'data-testid': 'risk-lock-list' },
          lockRows(locks).map((row, index) =>
            el('li', { 'data-testid': `risk-lock-${index}` }, [
              el('span', { 'data-field': 'lock_id', text: row[0] }),
              el('span', { 'data-field': 'state', text: row[1] }),
              el('span', { 'data-field': 'permission', text: row[2] }),
              el('span', { 'data-field': 'cause', text: row[3] }),
              el('span', { 'data-field': 'required_evidence', text: row[4] }),
            ]),
          ),
        );

  return el('section', { 'data-testid': 'overview-section' }, [
    el('h2', { text: '总览' }),
    el('div', { 'data-testid': 'freshness-block', 'data-stale': freshness.stale ? 'true' : 'false' }, [
      el('span', { 'data-field': 'as_of', text: `数据时点 ${freshness.as_of}` }),
      el('span', { 'data-field': 'age_ms', text: `年龄 ${String(freshness.age_ms)} ms` }),
      el('span', { 'data-field': 'raw_code', text: freshness.raw_code }),
      el('span', {
        'data-field': 'missing',
        text: freshness.missing.length === 0 ? '缺失项：无' : `缺失项：${freshness.missing.join(',')}`,
      }),
    ]),
    staleBanner,
    el('section', { 'data-testid': 'counts' }, [
      el('h3', { text: '投影计数' }),
      el('span', { 'data-field': 'positions', text: `持仓 ${String(positions.length)}` }),
      el('span', { 'data-field': 'orders', text: `订单 ${String(orders.length)}` }),
      el('span', { 'data-field': 'intents', text: `意图 ${String((page?.intents ?? []).length)}` }),
      page === null
        ? el('p', { 'data-code': 'NO_PROJECTION', text: '尚未取得投影快照。' })
        : el('p', {
            'data-code': 'PROJECTION_SOURCE',
            text: `作用域 ${page.scope.environment}/${page.scope.venue_account_id}/${page.scope.product}/${page.scope.instrument_uid}/${page.scope.venue_leg}`,
          }),
    ]),
    el('section', { 'data-testid': 'health-block' }, [
      el('h3', { text: '系统健康度（分进程）' }),
      health === null
        ? el('p', { 'data-code': 'RISK_PERMISSION_UNKNOWN', text: '风险许可未知，不推断为 NORMAL。' })
        : el('p', {
            'data-field': 'risk_permission',
            text: `风险许可 ${health.risk_permission}`,
          }),
      el('ul', {}, healthRows),
      health === null || health.missing.length === 0
        ? el('span', { text: '' })
        : el('span', { 'data-field': 'health_missing', text: `健康度缺失：${health.missing.join(',')}` }),
    ]),
    el('section', { 'data-testid': 'risk-locks' }, [
      el('h3', { text: '风险锁' }),
      lockList,
    ]),
  ]);
}
