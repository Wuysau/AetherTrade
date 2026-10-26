// Fixtures for the IP-29 status-view tests.
//
// Everything here drives the real client and view functions: the transport is
// recorded/controllable, but no assertion is derived from a hard-coded PASS.

import {
  createApiClient,
  type AetherControlClient,
  type HttpRequestInit,
  type HttpResponseLike,
  type Transport,
} from '../../src/api/client';
import type {
  ControlCommandProjection,
  ControlCommandReceipt,
  ControlCommandRequest,
  Coverage,
  EvidenceRef,
  ExitReceipt,
  FreshnessView,
  IntentProjection,
  ManagedPosition,
  OrderProjection,
  PositionProjection,
  ProjectionPage,
  ProtectionPlan,
  ScopeKey,
  SessionRef,
  SystemHealth,
  TradeIntent,
} from '../../src/contracts/generated';
import { App, findOne, findAll, textContent, type SectionId, type ViewElement } from '../../src/app/App';
import { PositionView } from '../../src/features/positions/PositionView';

export const FIXED_NOW_ISO = '2026-10-22T09:00:00.000Z';
export const FIXED_NOW_MS = Date.parse(FIXED_NOW_ISO);

export function makeScope(environment: ScopeKey['environment']): ScopeKey {
  return {
    environment,
    venue_account_id: 'acct-demo-1',
    product: 'USD_M_PERP',
    instrument_uid: 'BTCUSDT-PERP',
    venue_leg: 'BOTH',
  };
}

export function makeSession(
  environment: SessionRef['environment'],
  overrides: Partial<SessionRef> = {},
): SessionRef {
  return {
    session_id: `session-${environment.toLowerCase()}`,
    identity_id: 'identity-local-1',
    environment,
    expires_at: '2026-10-22T10:00:00.000Z',
    authorization_revision: 4,
    ...overrides,
  };
}

function evidence(id: string, environment: ScopeKey['environment']): EvidenceRef {
  return {
    id,
    hash: `hash-${id}`,
    environment,
    source: 'fixture',
    visible_at: FIXED_NOW_ISO,
  };
}

function coverage(scope: ScopeKey, missing: ReadonlyArray<string> = []): Coverage {
  return {
    scope,
    query_succeeded: missing.length === 0,
    pagination_complete: true,
    supported: true,
    missing,
  };
}

export function makeFreshness(overrides: Partial<FreshnessView> = {}): FreshnessView {
  return {
    as_of: '2026-10-22T08:59:55.000Z',
    age_ms: 5000,
    stale: false,
    missing: [],
    ...overrides,
  };
}

export function makeOrder(scope: ScopeKey, overrides: Partial<OrderProjection> = {}): OrderProjection {
  return {
    order: {
      identity: {
        scope,
        logical_client_order_id: 'lcid-1',
        native_client_order_id: 'ncid-1',
        venue_client_order_id: 'vcid-1',
        venue_order_id: 'vo-1',
      },
      owner_strategy_id: 'strategy-1',
      ownership_epoch: 7,
    },
    lifecycle_state: 'PARTIALLY_FILLED',
    pending_operation: 'SUBMIT',
    command_certainty: 'CONFIRMED',
    outcome_provenance: 'VENUE_EVENT',
    filled_quantity: { value: '0.4', unit: 'BASE' },
    leaves_quantity: { value: '0.6', unit: 'BASE' },
    lifecycle_revision: 3,
    sequence: 11,
    ...overrides,
  };
}

export function makeProtection(scope: ScopeKey, overrides: Partial<ProtectionPlan> = {}): ProtectionPlan {
  return {
    plan_id: 'protection-1',
    mode: 'NATIVE',
    scope,
    coverage_quantity: { value: '0.4', unit: 'BASE' },
    stop_price: { value: '61000.5', currency: 'USDT' },
    stop_trigger: 'LAST',
    native_order_ids: [
      {
        identity: {
          scope,
          logical_client_order_id: 'lcid-stop',
          native_client_order_id: 'ncid-stop',
          venue_client_order_id: 'vcid-stop',
          venue_order_id: 'vo-stop',
        },
        owner_strategy_id: 'strategy-1',
        ownership_epoch: 7,
      },
    ],
    monitor_id: null,
    state: 'VERIFIED_NATIVE',
    last_verified_at: '2026-10-22T08:59:30.000Z',
    coverage_gap_since: null,
    replacement_revision: 2,
    on_gap_policy: 'REPAIR_OR_EXIT',
    ...overrides,
  };
}

export function makeManagedPosition(
  scope: ScopeKey,
  overrides: Partial<ManagedPosition> = {},
): ManagedPosition {
  return {
    position_id: 'pos-1',
    scope,
    actual_quantity: { value: '0.4', unit: 'BASE' },
    owner_strategy_id: 'strategy-1',
    owner_book_id: 'book-1',
    ownership_epoch: 7,
    cost_evidence: coverage(scope),
    realized_pnl: { amount: '0', currency: 'USDT' },
    unrealized_valuation: { amount: '12.50', currency: 'USDT' },
    active_exit_plan_id: null,
    risk_locks: [],
    ...overrides,
  };
}

export function makePositionProjection(
  scope: ScopeKey,
  overrides: Partial<PositionProjection> = {},
): PositionProjection {
  const freshness = makeFreshness();
  return {
    position: makeManagedPosition(scope),
    protection: makeProtection(scope),
    freshness,
    sequence: 12,
    ...overrides,
  };
}

export function makeExitReceipt(overrides: Partial<ExitReceipt> = {}): ExitReceipt {
  return {
    position_id: 'pos-1',
    state: 'FLAT_VERIFIED',
    command_ids: ['command-exit-1'],
    incomplete_reasons: [],
    ...overrides,
  };
}

export function makeControlProjection(
  scope: ScopeKey,
  overrides: Partial<ControlCommandProjection> = {},
): ControlCommandProjection {
  const request: ControlCommandRequest = {
    action: 'pause_new',
    scope,
    expected_revision: 4,
    ownership_epoch: 7,
    transfer: null,
  };
  return {
    receipt: {
      command_id: 'control-1',
      status: 'QUEUED',
      reason: null,
      existing_result_ref: null,
    },
    request,
    payload_hash: 'payload-hash-1',
    idempotency_key: 'idem-control-1',
    sequence: 21,
    incomplete_reasons: ['awaiting_execution_owner'],
    ...overrides,
  };
}

export function makeIntent(scope: ScopeKey, overrides: Partial<TradeIntent> = {}): TradeIntent {
  return {
    contract_version: '2026-10-20',
    intent_id: 'intent-1',
    environment: scope.environment,
    owner_strategy_id: 'strategy-1',
    strategy_revision: 2,
    decision_run_id: 'run-1',
    authorization_id: 'auth-1',
    venue_account_id: scope.venue_account_id,
    collateral_group_id: 'collateral-1',
    venue: 'BINANCE',
    product: scope.product,
    instrument_uid: scope.instrument_uid,
    venue_leg: 'BOTH',
    ownership_epoch: 7,
    risk_policy_revision: 4,
    basis_fingerprint: 'basis-1',
    snapshot_id: 'snapshot-1',
    as_of: '2026-10-22T08:59:55.000Z',
    created_at: FIXED_NOW_ISO,
    valid_until: '2026-10-22T09:10:00.000Z',
    evidence_refs: ['evidence-1', 'evidence-2'],
    action: 'SET_TARGET',
    target_side: 'LONG',
    target_quantity: '1.0',
    quantity_unit: 'BASE',
    execution: {
      order_preference: 'LIMIT',
      limit_price: '60000.5',
      maximum_entry_slippage_bps: '10',
      entry_deadline: '2026-10-22T09:05:00.000Z',
      allow_partial: false,
    },
    max_planned_loss: { amount: '50.00', currency: 'USDT' },
    requested_leverage: 1,
    exit_plan: {
      protection_policy: 'NATIVE_REQUIRED',
      stop_trigger: 'LAST',
      stop_price: '58000.5',
      review_due_at: '2026-10-22T12:00:00.000Z',
      maximum_holding_seconds: 3600,
    },
    proposal_hash: 'proposal-hash-1',
    rationale_summary: '趋势延续，等待回踩限价入场。',
    supersedes_intent_id: null,
    ...overrides,
  };
}

export function makeIntentProjection(
  scope: ScopeKey,
  overrides: Partial<IntentProjection> = {},
): IntentProjection {
  return {
    intent: makeIntent(scope),
    state: 'RECEIVED',
    risk: null,
    commands: [],
    fills: [],
    protection: [],
    freshness: makeFreshness(),
    ...overrides,
  };
}

export interface PageOptions {
  readonly positions?: ReadonlyArray<PositionProjection>;
  readonly orders?: ReadonlyArray<OrderProjection>;
  readonly intents?: ReadonlyArray<TradeIntent>;
  readonly controls?: ReadonlyArray<ControlCommandProjection>;
  readonly freshness?: FreshnessView;
}

export function makePage(scope: ScopeKey, options: PageOptions = {}): ProjectionPage {
  return {
    scope,
    positions: options.positions ?? [makePositionProjection(scope)],
    orders: options.orders ?? [makeOrder(scope)],
    risk_locks: [],
    controls: options.controls ?? [makeControlProjection(scope)],
    intents: options.intents ?? [makeIntent(scope)],
    next_cursor: null,
    freshness: options.freshness ?? makeFreshness(),
  };
}

export function makeHealth(): SystemHealth {
  return {
    processes: [
      { process_id: 'control-api', state: 'READY', observed_at: FIXED_NOW_ISO, faults: [] },
      { process_id: 'execution-owner', state: 'READY', observed_at: FIXED_NOW_ISO, faults: [] },
      { process_id: 'market-data', state: 'DEGRADED', observed_at: FIXED_NOW_ISO, faults: ['quote_lag'] },
    ],
    risk_permission: 'NO_NEW_RISK',
    missing: [],
  };
}

export interface RecordedRequest {
  readonly method: HttpRequestInit['method'];
  readonly url: string;
  readonly body: string | null;
  readonly headers: Readonly<Record<string, string>>;
  readonly signal: AbortSignal;
}

export type RouteHandler = (request: HttpRequestInit) => Promise<HttpResponseLike> | HttpResponseLike;

/** Records every request and serves fixture responses. */
export class RecordingTransport implements Transport {
  readonly requests: RecordedRequest[] = [];
  readonly #route: RouteHandler;

  constructor(route: RouteHandler) {
    this.#route = route;
  }

  get posts(): RecordedRequest[] {
    return this.requests.filter(request => request.method === 'POST');
  }

  get gets(): RecordedRequest[] {
    return this.requests.filter(request => request.method === 'GET');
  }

  async send(request: HttpRequestInit): Promise<HttpResponseLike> {
    this.requests.push({
      method: request.method,
      url: request.url,
      body: request.body,
      headers: request.headers,
      signal: request.signal,
    });
    return this.#route(request);
  }
}

/** Builds the page body for the environment named in the request URL. */
export class DeferredControlTransport implements Transport {
  readonly requests: RecordedRequest[] = [];
  readonly pendingSignals: AbortSignal[] = [];
  readonly #pageBodyFor: (request: HttpRequestInit) => string;
  #settle: ((response: HttpResponseLike) => void) | null = null;

  constructor(pageBodyFor: (request: HttpRequestInit) => string) {
    this.#pageBodyFor = pageBodyFor;
  }

  get posts(): RecordedRequest[] {
    return this.requests.filter(request => request.method === 'POST');
  }

  get gets(): RecordedRequest[] {
    return this.requests.filter(request => request.method === 'GET');
  }

  get pending(): boolean {
    return this.#settle !== null;
  }

  async send(request: HttpRequestInit): Promise<HttpResponseLike> {
    this.requests.push({
      method: request.method,
      url: request.url,
      body: request.body,
      headers: request.headers,
      signal: request.signal,
    });
    if (request.method === 'GET') {
      return { status: 200, body: this.#pageBodyFor(request) };
    }
    return new Promise<HttpResponseLike>((resolve, reject) => {
      this.#settle = resolve;
      this.pendingSignals.push(request.signal);
      request.signal.addEventListener(
        'abort',
        () => reject(new Error('fixture transport aborted')),
        { once: true },
      );
    });
  }

  settleWith(receipt: ControlCommandReceipt): void {
    const settle = this.#settle;
    if (settle === null) {
      throw new Error('no pending control write to settle');
    }
    this.#settle = null;
    settle({ status: 202, body: JSON.stringify(receipt) });
  }
}

export function pageTransport(scope: ScopeKey, options: PageOptions = {}): RecordingTransport {
  const body = JSON.stringify(makePage(scope, options));
  return new RecordingTransport(request => {
    if (request.method === 'GET') {
      return { status: 200, body };
    }
    const receipt: ControlCommandReceipt = {
      command_id: 'command-exit-1',
      status: 'QUEUED',
      reason: null,
      existing_result_ref: null,
    };
    return { status: 202, body: JSON.stringify(receipt) };
  });
}

export class ViewHarness {
  readonly transport: Transport;
  readonly client: AetherControlClient;
  readonly session: SessionRef;
  readonly scope: ScopeKey;
  offline = false;
  page: ProjectionPage | null = null;
  activeSection: SectionId = 'positions-orders';
  health: SystemHealth | null = makeHealth();
  exits: ReadonlyArray<ExitReceipt> = [];
  readonly denials: unknown[] = [];
  closed = false;

  constructor(transport: Transport, session: SessionRef) {
    this.transport = transport;
    this.session = session;
    this.scope = makeScope(session.environment);
    this.client = createApiClient('http://127.0.0.1:8099', session, {
      transport,
      now: () => FIXED_NOW_MS,
    });
  }

  async refresh(): Promise<ProjectionPage> {
    this.page = await this.client.query({
      scope: this.scope,
      resource: 'positions',
      owner_strategy_id: null,
      cursor: null,
      limit: 50,
      maximum_age_ms: 5000,
    });
    return this.page;
  }

  render(): ViewElement {
    return App({
      client: this.client,
      session: this.client.session,
      page: this.page,
      offline: this.offline,
      health: this.health,
      exits: this.exits,
      activeSection: this.activeSection,
      onRequestExit: this.requestExit,
    });
  }

  renderPosition(): ViewElement {
    const position = this.page?.positions[0] ?? makePositionProjection(this.scope);
    return PositionView({
      position,
      freshness: position.freshness,
      orders: this.page?.orders ?? [],
      exits: this.exits,
      offline: this.offline,
      controlAvailable: true,
      onRequestExit: this.requestExit,
    });
  }

  switchEnvironment(next: SessionRef): { status: string; reason_code: string | null } {
    try {
      this.client.switchSession(next);
      this.offline = false;
      return { status: 'SWITCHED', reason_code: null };
    } catch (error) {
      return {
        status: 'BLOCKED',
        reason_code: (error as { reason_code?: string }).reason_code ?? 'SESSION_SWITCH_REJECTED',
      };
    }
  }

  private readonly requestExit = async (): Promise<void> => {
    const request: ControlCommandRequest = {
      action: 'exit_owned',
      scope: this.scope,
      expected_revision: 1,
      ownership_epoch: 7,
      transfer: null,
    };
    try {
      await this.client.command(request, 'idem-exit-1');
    } catch (error) {
      this.denials.push(error);
    }
  };

  async clickExit(): Promise<void> {
    const button = findOne(this.render(), element => element.props['data-action'] === 'exit-owned');
    if (button === null) {
      this.denials.push({ reason_code: 'EXIT_BUTTON_ABSENT' });
      return;
    }
    const onClick = button.props.onClick;
    if (typeof onClick !== 'function') {
      this.denials.push({ reason_code: button.props['data-denial-code'] ?? 'DISABLED' });
      return;
    }
    await (onClick as () => Promise<void>)();
  }

  closePage(): void {
    const hook = findOne(this.render(), element => element.props['data-testid'] === 'page-lifecycle-hook');
    if (hook === null) {
      this.denials.push({ reason_code: 'PAGE_LIFECYCLE_HOOK_ABSENT' });
      return;
    }
    const onPageHide = hook.props.onPageHide;
    if (typeof onPageHide === 'function') {
      (onPageHide as () => void)();
      this.closed = true;
    }
  }

  goOffline(): void {
    this.offline = true;
    this.client.setOnline(false);
  }

  goOnline(): void {
    this.offline = false;
    this.client.setOnline(true);
  }

  /** Network-level control writes (POSTs) actually attempted. */
  controlWrites(): ReadonlyArray<RecordedRequest> {
    if (this.transport instanceof RecordingTransport) {
      return this.transport.posts;
    }
    return [];
  }

  queuedForReconnect(): boolean {
    return this.client.queuedForReconnect() > 0;
  }

  denialCodes(): ReadonlyArray<string> {
    return this.denials.map(denial => {
      if (typeof denial === 'object' && denial !== null && 'reason_code' in denial) {
        return String((denial as { reason_code: unknown }).reason_code);
      }
      return String(denial);
    });
  }

  offlineNotice(): boolean {
    return findOne(this.render(), element => element.props['data-testid'] === 'offline-banner') !== null;
  }

  text(): string {
    return textContent(this.render());
  }
}

export function makePaperHarness(): ViewHarness {
  const scope = makeScope('PAPER');
  return new ViewHarness(pageTransport(scope), makeSession('PAPER'));
}

export async function renderOnlinePositionFixture(): Promise<ViewHarness> {
  const harness = makePaperHarness();
  await harness.refresh();
  return harness;
}

export async function renderOfflinePositionFixture(): Promise<ViewHarness> {
  const harness = await renderOnlinePositionFixture();
  harness.goOffline();
  return harness;
}

export interface StateRowView {
  readonly key: string;
  readonly label: string;
  readonly raw_code: string;
  readonly present: boolean;
}

export function collectStateRows(element: ViewElement): StateRowView[] {
  return findAll(element, node => typeof node.props['data-state-key'] === 'string').map(node => ({
    key: String(node.props['data-state-key']),
    label: textContent(node).split(' ')[0] ?? '',
    raw_code: String(node.props['data-raw-code']),
    present: node.props['data-present'] === 'true',
  }));
}

export function findSection(element: ViewElement, testId: string): ViewElement | null {
  return findOne(element, node => node.props['data-testid'] === testId);
}
