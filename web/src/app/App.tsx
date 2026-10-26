// Application shell and the small, dependency-free view primitives used by the
// read-only mobile pages.
//
// The repository intentionally has no React/ReactDOM dependency yet (it is not
// in `web/package.json`), so these components are pure render functions: typed
// props in, a React-shaped element descriptor out (`type` + `props` + `key`).
// A host adapter can render the descriptor with `React.createElement`; the
// adapter is deliberately not invented here because the dependency change must
// go through the integration owner.

import type {
  ControlCommandProjection,
  ExitReceipt,
  ProjectionPage,
  SessionRef,
  SystemHealth,
  TradeIntent,
} from '../contracts/generated';
import type { AetherControlClient } from '../api/client';
import { AIActivity } from '../features/activity/AIActivity';
import { Overview } from '../features/overview/Overview';
import { PositionView } from '../features/positions/PositionView';

export interface ViewElement {
  readonly type: string;
  readonly key: string | null;
  readonly props: Readonly<Record<string, unknown>>;
}

export type ViewChild = ViewElement | string;

/** Minimal element factory; the shape matches a React element descriptor. */
export function h(
  type: string,
  props: Record<string, unknown> = {},
  children: ReadonlyArray<ViewChild> = [],
): ViewElement {
  const key = typeof props.key === 'string' ? props.key : null;
  const { key: _ignored, ...rest } = props;
  return {
    type,
    key,
    props: children.length === 0 ? rest : { ...rest, children },
  };
}

export function isViewElement(value: unknown): value is ViewElement {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as ViewElement).type === 'string' &&
    typeof (value as ViewElement).props === 'object'
  );
}

export function walk(root: ViewChild, visit: (element: ViewElement) => void): void {
  if (typeof root === 'string') {
    return;
  }
  visit(root);
  const children = root.props.children;
  if (Array.isArray(children)) {
    for (const child of children as ReadonlyArray<ViewChild>) {
      walk(child, visit);
    }
  }
}

export function findAll(root: ViewChild, predicate: (element: ViewElement) => boolean): ViewElement[] {
  const matches: ViewElement[] = [];
  walk(root, element => {
    if (predicate(element)) {
      matches.push(element);
    }
  });
  return matches;
}

export function findOne(root: ViewChild, predicate: (element: ViewElement) => boolean): ViewElement | null {
  return findAll(root, predicate)[0] ?? null;
}

/** Text content in document order, used by assertions and screen readers. */
export function textContent(root: ViewChild): string {
  if (typeof root === 'string') {
    return root;
  }
  const own = typeof root.props.text === 'string' ? root.props.text : '';
  const children = root.props.children;
  const childText = Array.isArray(children)
    ? (children as ReadonlyArray<ViewChild>).map(child => textContent(child)).join(' ')
    : '';
  return [own, childText].filter(part => part.length > 0).join(' ');
}

export function toReactElement(
  element: ViewElement,
  createElement: (type: string, props: Record<string, unknown>) => unknown,
): unknown {
  const props: Record<string, unknown> = { ...element.props };
  const children = props.children;
  delete props.children;
  const mapped = Array.isArray(children)
    ? (children as ReadonlyArray<ViewChild>).map(child =>
        typeof child === 'string' ? child : toReactElement(child, createElement),
      )
    : [];
  return createElement(element.type, mapped.length === 0 ? props : { ...props, children: mapped });
}

export type SectionId =
  | 'overview'
  | 'ai-activity'
  | 'positions-orders'
  | 'strategy-center'
  | 'research-review'
  | 'permissions';

export interface SectionDefinition {
  readonly id: SectionId;
  readonly label: string;
  readonly implemented_in_ip_29: boolean;
}

/** SPEC section 11: the six functional areas of the phone UI. */
export const SIX_FUNCTION_AREAS: ReadonlyArray<SectionDefinition> = [
  { id: 'overview', label: '总览', implemented_in_ip_29: true },
  { id: 'ai-activity', label: 'AI 动态', implemented_in_ip_29: true },
  { id: 'positions-orders', label: '持仓订单', implemented_in_ip_29: true },
  { id: 'strategy-center', label: '策略中心', implemented_in_ip_29: false },
  { id: 'research-review', label: '研究复盘', implemented_in_ip_29: false },
  { id: 'permissions', label: '权限设置', implemented_in_ip_29: false },
];

export interface SwitchEnvironmentResult {
  readonly status: 'SWITCHED' | 'BLOCKED';
  readonly reason_code: string | null;
  readonly cache_cleared: boolean;
  readonly pending_aborted: boolean;
  readonly requires_snapshot: boolean;
}

/**
 * The only environment transition used by the UI. The client owns cache
 * invalidation and aborting pending control writes.
 */
export function switchEnvironment(
  client: AetherControlClient,
  next: SessionRef,
): SwitchEnvironmentResult {
  const before = client.session.environment;
  const pendingBefore = client.pendingControlWrites();
  try {
    client.switchSession(next);
  } catch (error) {
    const reason = error instanceof Error ? (error as { reason_code?: string }).reason_code ?? 'SESSION_SWITCH_REJECTED' : 'SESSION_SWITCH_REJECTED';
    return {
      status: 'BLOCKED',
      reason_code: reason,
      cache_cleared: false,
      pending_aborted: false,
      requires_snapshot: client.requiresSnapshot(),
    };
  }
  return {
    status: 'SWITCHED',
    reason_code: null,
    cache_cleared: client.cachedScopes().length === 0,
    pending_aborted: before !== next.environment && pendingBefore > 0 && client.pendingControlWrites() === 0,
    requires_snapshot: client.requiresSnapshot(),
  };
}

export interface AppProps {
  readonly client: AetherControlClient;
  readonly session: SessionRef;
  readonly page: ProjectionPage | null;
  readonly offline: boolean;
  readonly intents?: ReadonlyArray<TradeIntent>;
  readonly controls?: ReadonlyArray<ControlCommandProjection>;
  readonly health?: SystemHealth | null;
  readonly exits?: ReadonlyArray<ExitReceipt>;
  readonly activeSection?: SectionId;
  readonly onRequestExit?: () => void;
  readonly onSessionChange?: (session: SessionRef) => void;
  readonly onEnvironmentChangeRequest?: (environment: SessionRef['environment']) => void;
  readonly onSectionChange?: (section: SectionId) => void;
  readonly onRefresh?: () => void;
}

function environmentBanner(props: AppProps): ViewElement {
  const example = props.client.isExampleTransport() || props.session.environment !== 'LIVE';
  const text = example
    ? `示例环境（${props.session.environment}）· 合约 mock server · 仅供开发，非生产后端，不构成交易证据`
    : `实盘环境 ${props.session.environment} · 控制请求仅代表已接收，不代表平台动作完成`;
  return h(
    'div',
    {
      'data-testid': example ? 'example-banner' : 'live-banner',
      'data-example': example ? 'true' : 'false',
      'data-environment': props.session.environment,
      role: 'status',
    },
    [h('span', { text })],
  );
}

function sectionPlaceholder(section: SectionDefinition): ViewElement {
  return h('section', { 'data-testid': `section-${section.id}`, 'data-section': section.id }, [
    h('h2', { text: section.label }),
    h('p', {
      'data-code': 'NOT_IMPLEMENTED_IN_IP_29',
      text: '该功能区尚未在本任务实现；不显示推测数据。',
    }),
  ]);
}

function environmentSwitch(props: AppProps): ViewElement {
  const current = props.session.environment;
  const targets: ReadonlyArray<SessionRef['environment']> = ['RESEARCH', 'PAPER', 'TESTNET', 'LIVE'];
  return h('section', { 'data-testid': 'environment-switch' }, [
    h('h2', { text: '环境与会话' }),
    h('p', {
      'data-code': 'SESSION_ISSUED_BY_SERVER',
      text: '切换环境需要服务端签发新的会话；本页不会在本地伪造 LIVE 会话或复用旧快照。',
    }),
    h(
      'div',
      {},
      targets.map(target =>
        h(
          'button',
          {
            type: 'button',
            'data-environment-target': target,
            disabled: target === current,
            onClick: () => props.onEnvironmentChangeRequest?.(target),
          },
          [h('span', { text: target === current ? `${target}（当前）` : `请求切换到 ${target}` })],
        ),
      ),
    ),
  ]);
}

function controlPanel(props: AppProps): ViewElement {
  const controls = props.controls ?? props.page?.controls ?? [];
  const rows = controls.map(control =>
    h('li', { 'data-testid': `control-${control.receipt.command_id}` }, [
      h('span', { 'data-field': 'action', text: control.request.action }),
      h('span', { 'data-field': 'receipt_status', text: control.receipt.status }),
      h('span', {
        'data-field': 'reason',
        text: control.receipt.reason ?? '—',
      }),
      h('span', {
        'data-field': 'completion',
        text: '回执不等于平台动作完成；需继续读取进度。',
      }),
    ]),
  );
  return h('section', { 'data-testid': 'controls-panel' }, [
    h('h2', { text: '控制回执' }),
    controls.length === 0
      ? h('p', { 'data-code': 'NO_CONTROL_RECEIPT', text: '暂无控制回执。' })
      : h('ul', {}, rows),
  ]);
}

export function App(props: AppProps): ViewElement {
  const active = props.activeSection ?? 'overview';
  const page = props.page;
  const freshness = page?.freshness ?? null;

  const nav = h(
    'nav',
    { 'data-testid': 'section-nav' },
    SIX_FUNCTION_AREAS.map(section =>
      h(
        'button',
        {
          'data-section': section.id,
          'data-active': section.id === active ? 'true' : 'false',
          'data-implemented': section.implemented_in_ip_29 ? 'true' : 'false',
          type: 'button',
          onClick: () => props.onSectionChange?.(section.id),
        },
        [h('span', { text: section.label })],
      ),
    ),
  );

  let pane: ViewElement;
  if (active === 'overview') {
    pane = Overview({
      page,
      freshness,
      offline: props.offline,
      health: props.health ?? null,
    });
  } else if (active === 'positions-orders') {
    const positions = page?.positions ?? [];
    pane = h('section', { 'data-testid': 'positions-section' }, [
      h('h2', { text: '持仓订单' }),
      positions.length === 0
        ? h('p', { 'data-code': 'NO_POSITION_PROJECTION', text: '暂无持仓投影，不推测仓位。' })
        : h(
            'div',
            { 'data-testid': 'position-list' },
            positions.map(position =>
              PositionView({
                position,
                freshness: position.freshness ?? freshness ?? {
                  as_of: new Date(0).toISOString(),
                  age_ms: 0,
                  stale: true,
                  missing: ['freshness'],
                },
                orders: page?.orders ?? [],
                exits: props.exits ?? [],
                offline: props.offline,
                controlAvailable: props.onRequestExit !== undefined,
                onRequestExit: props.onRequestExit,
              }),
            ),
          ),
    ]);
  } else if (active === 'ai-activity') {
    pane = AIActivity({
      intents: props.intents ?? page?.intents ?? [],
      offline: props.offline,
      freshness,
      requiresSnapshot: props.client.requiresSnapshot(),
    });
  } else {
    const section = SIX_FUNCTION_AREAS.find(candidate => candidate.id === active);
    pane = section === undefined ? sectionPlaceholder(SIX_FUNCTION_AREAS[3]) : sectionPlaceholder(section);
  }

  const closeHook = h('div', {
    'data-testid': 'page-lifecycle-hook',
    'aria-hidden': 'true',
    onPageHide: () => props.client.closePage(),
    metadata: { note: 'page hide/close must not stop positions or cancel protection' },
  });

  return h(
    'div',
    {
      'data-testid': 'app-shell',
      'data-environment': props.session.environment,
      'data-offline': props.offline ? 'true' : 'false',
      'data-snapshot-required': props.client.requiresSnapshot() ? 'true' : 'false',
      lang: 'zh-CN',
    },
    [
      environmentBanner(props),
      h('header', { 'data-testid': 'app-header' }, [
        h('h1', { text: 'AetherTrade 控制台' }),
        h('p', {
          'data-field': 'session',
          text: `会话 ${props.session.session_id} · 环境 ${props.session.environment} · 授权修订 ${props.session.authorization_revision}`,
        }),
      ]),
      nav,
      environmentSwitch(props),
      props.offline
        ? h('div', {
            'data-testid': 'offline-banner',
            'data-code': 'OFFLINE_NO_REPLAY',
            role: 'alert',
            text: '离线：仅显示最后一次已知状态；控制命令不会排队，也不会在重连后自动重放。',
          })
        : h('div', { 'data-testid': 'online-status', text: '在线' }),
      pane,
      controlPanel(props),
      closeHook,
      h('footer', { 'data-testid': 'app-footer' }, [
        h('p', {
          text: '只读视图：本页不发起停止交易、撤保护或平仓动作；关闭页面不会改变持仓。',
        }),
      ]),
    ],
  );
}
