// Control API client for the read-only mobile views.
//
// The wire types come from the frozen application contract in
// `web/src/contracts/generated.ts` (IP-01). Decimal amounts stay strings.
//
// Safety rules implemented here are deliberately narrow:
// - control commands are explicit POSTs with an idempotency key; they are
//   never cached and never replayed after a reconnect;
// - control writes are refused while the client is offline or after the page
//   was closed, instead of being queued;
// - switching environment drops cached projections, aborts in-flight control
//   writes and marks that a fresh snapshot is required;
// - an example/mock transport can never be bound to LIVE;
// - HTTP 200/202 is reported as an API receipt only, never as a completed
//   venue action.

import type {
  ApiError,
  ControlAction,
  ControlApiClient,
  ControlCommandReceipt,
  ControlCommandRequest,
  ControlStatus,
  EventPage,
  ProjectionEvent,
  ProjectionPage,
  ProjectionQueryRequest,
  ScopeKey,
  SessionRef,
  StreamCursor,
} from '../contracts/generated';

export interface HttpRequestInit {
  readonly method: 'GET' | 'POST';
  readonly url: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string | null;
  readonly signal: AbortSignal;
}

export interface HttpResponseLike {
  readonly status: number;
  readonly body: string;
}

export interface Transport {
  send(request: HttpRequestInit): Promise<HttpResponseLike>;
}

export interface ClientOptions {
  /** Injected for tests/offline fixtures; production uses `fetch`. */
  readonly transport?: Transport;
  /** True only for the contract mock server, which must stay visibly PAPER. */
  readonly exampleTransport?: boolean;
  /** Injectable clock so fixtures do not depend on the host time. */
  readonly now?: () => number;
}

export type ClientLifecycleKind = 'PAGE_HIDDEN' | 'ONLINE' | 'OFFLINE' | 'ENVIRONMENT_SWITCH' | 'CACHE_CLEARED';

export interface LifecycleEntry {
  readonly kind: ClientLifecycleKind;
  readonly at: string;
  readonly detail: string;
  readonly controlWrites: number;
  readonly networkRequests: number;
}

export type ControlWriteStatus =
  | 'PENDING'
  | 'RECEIPT_RECEIVED'
  | 'REJECTED'
  | 'ABORTED'
  | 'BLOCKED_OFFLINE'
  | 'BLOCKED_PAGE_CLOSED'
  | 'BLOCKED_ENVIRONMENT'
  | 'BLOCKED_REQUEST';

export interface ControlWriteAttempt {
  readonly action: ControlAction;
  readonly idempotency_key: string;
  readonly environment: string;
  readonly started_at: string;
  readonly status: ControlWriteStatus;
  readonly reason_code: string | null;
  /** True only when a request was actually handed to the transport. */
  readonly sent: boolean;
  readonly receipt_status: ControlStatus | null;
}

export class ControlClientError extends Error {
  readonly reason_code: string;

  constructor(reasonCode: string, message: string) {
    super(message);
    this.name = 'ControlClientError';
    this.reason_code = reasonCode;
  }

  toApiError(): ApiError {
    return {
      reason_code: this.reason_code,
      message: this.message,
      correctable_fields: [],
      allowed_actions: [],
    };
  }
}

export class OfflineWriteBlockedError extends ControlClientError {
  constructor(action: ControlAction) {
    super(
      'OFFLINE_CONTROL_WRITE_BLOCKED',
      `Offline: control action ${action} was not sent and was not queued for replay.`,
    );
    this.name = 'OfflineWriteBlockedError';
  }
}

export class PageClosedWriteBlockedError extends ControlClientError {
  constructor(action: ControlAction) {
    super(
      'PAGE_CLOSED_CONTROL_WRITE_BLOCKED',
      `Page is closed: control action ${action} was not sent; closing the page never stops a position.`,
    );
    this.name = 'PageClosedWriteBlockedError';
  }
}

export class CrossEnvironmentError extends ControlClientError {
  constructor(detail: string) {
    super('CROSS_ENVIRONMENT_SCOPE', `Request scope does not match the bound session: ${detail}`);
    this.name = 'CrossEnvironmentError';
  }
}

export class CrossScopeProjectionError extends ControlClientError {
  constructor(detail: string) {
    super('CROSS_SCOPE_PROJECTION', `Projection belongs to another scope: ${detail}`);
    this.name = 'CrossScopeProjectionError';
  }
}

export class EnvironmentSwitchAbortError extends ControlClientError {
  constructor(detail: string) {
    super('ENVIRONMENT_SWITCH_ABORTED', `Pending request was dropped because the session changed: ${detail}`);
    this.name = 'EnvironmentSwitchAbortError';
  }
}

export class OfflineReadError extends ControlClientError {
  constructor() {
    super('OFFLINE_NO_FRESH_READ', 'Offline: no fresh projection read is possible. Cached data is stale by definition.');
    this.name = 'OfflineReadError';
  }
}

export class GapRequiresSnapshotError extends ControlClientError {
  constructor() {
    super('STREAM_GAP_REQUIRES_SNAPSHOT', 'Event stream reported a gap; fetch a new projection snapshot before continuing.');
    this.name = 'GapRequiresSnapshotError';
  }
}

export class MockTransportLiveError extends ControlClientError {
  constructor() {
    super(
      'MOCK_TRANSPORT_NOT_ALLOWED_FOR_LIVE',
      'The contract mock/example transport cannot be bound to LIVE; no production fallback is configured.',
    );
    this.name = 'MockTransportLiveError';
  }
}

export class ApiResponseError extends ControlClientError {
  readonly status: number;

  constructor(status: number, reasonCode: string, message: string) {
    super(reasonCode, `Control API responded ${status}: ${message}`);
    this.name = 'ApiResponseError';
    this.status = status;
  }
}

export class InvalidProjectionError extends ControlClientError {
  constructor(detail: string) {
    super('INVALID_PROJECTION_PAYLOAD', `Control API returned an unusable projection: ${detail}`);
    this.name = 'InvalidProjectionError';
  }
}

export class InvalidReceiptError extends ControlClientError {
  constructor(status: string) {
    super('INVALID_CONTROL_RECEIPT', `Control API returned an unknown receipt status: ${status}`);
    this.name = 'InvalidReceiptError';
  }
}

/** Stable cache key; the environment is part of the key, not a filter. */
export function scopeCacheKey(scope: ScopeKey): string {
  return [
    scope.environment,
    scope.venue_account_id,
    scope.product,
    scope.instrument_uid,
    scope.venue_leg,
  ].join('|');
}

export function queryCacheKey(request: ProjectionQueryRequest): string {
  return [
    scopeCacheKey(request.scope),
    request.resource,
    request.owner_strategy_id ?? '*',
  ].join('#');
}

const RECEIPT_STATUSES: ReadonlyArray<ControlStatus> = ['QUEUED', 'APPLIED', 'REJECTED', 'IN_PROGRESS'];

/** HTTP 200/202 with an APPLIED receipt is still not venue confirmation. */
export function receiptClaimsCompletion(status: ControlStatus): boolean {
  return false;
}

export function buildQueryUrl(baseUrl: string, request: ProjectionQueryRequest): string {
  const params = new URLSearchParams();
  params.set('environment', request.scope.environment);
  params.set('venue_account_id', request.scope.venue_account_id);
  params.set('product', request.scope.product);
  params.set('instrument_uid', request.scope.instrument_uid);
  params.set('venue_leg', request.scope.venue_leg);
  params.set('resource', request.resource);
  params.set('limit', String(request.limit));
  if (request.owner_strategy_id !== null) {
    params.set('owner_strategy_id', request.owner_strategy_id);
  }
  if (request.maximum_age_ms !== null) {
    params.set('maximum_age_ms', String(request.maximum_age_ms));
  }
  if (request.cursor !== null) {
    params.set('cursor_sequence', String(request.cursor.sequence));
    params.set('cursor_stream_id', request.cursor.stream_id);
  }
  return `${baseUrl}/projections?${params.toString()}`;
}

export function buildEventsUrl(baseUrl: string, cursor: StreamCursor | null): string {
  if (cursor === null) {
    return `${baseUrl}/events`;
  }
  const params = new URLSearchParams();
  params.set('cursor_sequence', String(cursor.sequence));
  params.set('cursor_stream_id', cursor.stream_id);
  return `${baseUrl}/events?${params.toString()}`;
}

function parseApiError(body: string): ApiError | null {
  try {
    const parsed = JSON.parse(body) as Partial<ApiError>;
    if (typeof parsed.reason_code === 'string' && typeof parsed.message === 'string') {
      return {
        reason_code: parsed.reason_code,
        message: parsed.message,
        correctable_fields: parsed.correctable_fields ?? [],
        allowed_actions: parsed.allowed_actions ?? [],
      };
    }
  } catch {
    return null;
  }
  return null;
}

function assertProjection(page: ProjectionPage, session: SessionRef): void {
  if (page === null || typeof page !== 'object') {
    throw new InvalidProjectionError('empty body');
  }
  if (page.scope === undefined || page.scope.environment !== session.environment) {
    throw new CrossEnvironmentError('projection scope is missing or belongs to another environment');
  }
  if (page.freshness === undefined) {
    throw new InvalidProjectionError('freshness is required');
  }
  for (const field of ['positions', 'orders', 'risk_locks', 'controls', 'intents'] as const) {
    if (!Array.isArray(page[field])) {
      throw new InvalidProjectionError(`field ${field} is not an array`);
    }
  }
}

/**
 * Live transport. `cache: 'no-store'` keeps projections and receipts out of the
 * browser HTTP cache; control POSTs are also never written to our own cache.
 */
export const fetchTransport: Transport = {
  async send(request: HttpRequestInit): Promise<HttpResponseLike> {
    const response = await fetch(request.url, {
      method: request.method,
      headers: request.headers,
      body: request.body,
      signal: request.signal,
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
    });
    return { status: response.status, body: await response.text() };
  },
};

export interface AetherControlClient extends ControlApiClient {
  readonly baseUrl: string;
  readonly session: SessionRef;
  switchSession(next: SessionRef): void;
  closePage(): void;
  isPageClosed(): boolean;
  setOnline(online: boolean): void;
  isOffline(): boolean;
  isExampleTransport(): boolean;
  cachedScopes(): ReadonlyArray<string>;
  readCached(request: ProjectionQueryRequest): ProjectionPage | null;
  clearCache(reason: string): void;
  requiresSnapshot(): boolean;
  acknowledgeSnapshot(page: ProjectionPage): void;
  controlWrites(): ReadonlyArray<ControlWriteAttempt>;
  pendingControlWrites(): number;
  queuedForReconnect(): number;
  lifecycleLog(): ReadonlyArray<LifecycleEntry>;
}

interface CacheEntry {
  readonly key: string;
  readonly environment: string;
  readonly page: ProjectionPage;
  readonly stored_at: string;
}

class ControlClient implements AetherControlClient {
  readonly baseUrl: string;
  readonly #transport: Transport;
  readonly #exampleTransport: boolean;
  readonly #now: () => number;

  #session: SessionRef;
  #online = true;
  #pageClosed = false;
  #epoch = 0;
  #snapshotRequired = true;
  #cache = new Map<string, CacheEntry>();
  #pending = new Map<number, AbortController>();
  #pendingSeq = 0;
  #attempts: ControlWriteAttempt[] = [];
  #lifecycle: LifecycleEntry[] = [];

  constructor(baseUrl: string, session: SessionRef, options: ClientOptions) {
    const trimmed = baseUrl.trim();
    if (trimmed.length === 0) {
      throw new ControlClientError(
        'CONTROL_API_BASE_URL_REQUIRED',
        'createApiClient requires an explicit control API base URL; there is no implicit or mock fallback.',
      );
    }
    if (!/^https?:\/\//u.test(trimmed)) {
      throw new ControlClientError(
        'CONTROL_API_BASE_URL_INVALID',
        'Control API base URL must be an absolute http(s) URL.',
      );
    }
    if (session.session_id.trim().length === 0) {
      throw new ControlClientError('SESSION_REF_REQUIRED', 'A session reference is required to bind the control client.');
    }
    this.#exampleTransport = options.exampleTransport === true;
    if (this.#exampleTransport && session.environment === 'LIVE') {
      throw new MockTransportLiveError();
    }
    this.baseUrl = trimmed.replace(/\/+$/u, '');
    this.#session = session;
    this.#transport = options.transport ?? fetchTransport;
    this.#now = options.now ?? Date.now;
  }

  get session(): SessionRef {
    return this.#session;
  }

  isExampleTransport(): boolean {
    return this.#exampleTransport;
  }

  isOffline(): boolean {
    return !this.#online;
  }

  isPageClosed(): boolean {
    return this.#pageClosed;
  }

  requiresSnapshot(): boolean {
    return this.#snapshotRequired;
  }

  acknowledgeSnapshot(page: ProjectionPage): void {
    assertProjection(page, this.#session);
    this.#snapshotRequired = false;
  }

  cachedScopes(): ReadonlyArray<string> {
    return Array.from(new Set(Array.from(this.#cache.values(), entry => entry.environment)));
  }

  readCached(request: ProjectionQueryRequest): ProjectionPage | null {
    const entry = this.#cache.get(queryCacheKey(request));
    if (entry === undefined || entry.environment !== this.#session.environment) {
      return null;
    }
    return entry.page;
  }

  clearCache(reason: string): void {
    this.#cache = new Map<string, CacheEntry>();
    this.#snapshotRequired = true;
    this.#lifecycle.push(this.#entry('CACHE_CLEARED', reason));
  }

  controlWrites(): ReadonlyArray<ControlWriteAttempt> {
    return [...this.#attempts];
  }

  pendingControlWrites(): number {
    return this.#pending.size;
  }

  /** Offline control writes are refused, so there is never a replay queue. */
  queuedForReconnect(): number {
    return 0;
  }

  lifecycleLog(): ReadonlyArray<LifecycleEntry> {
    return [...this.#lifecycle];
  }

  setOnline(online: boolean): void {
    if (this.#online === online) {
      return;
    }
    this.#online = online;
    this.#lifecycle.push(this.#entry(online ? 'ONLINE' : 'OFFLINE', 'transport availability changed'));
  }

  /** The only supported session path: it owns invalidation and abort semantics. */
  switchSession(next: SessionRef): void {
    if (this.#exampleTransport && next.environment === 'LIVE') {
      throw new MockTransportLiveError();
    }
    const previous = this.#session;
    const environmentChanged = previous.environment !== next.environment;
    const sessionChanged = previous.session_id !== next.session_id;

    this.#session = next;
    this.#epoch += 1;
    this.#snapshotRequired = true;

    if (environmentChanged || sessionChanged) {
      this.#cache = new Map<string, CacheEntry>();
      this.#abortPending(
        new EnvironmentSwitchAbortError(`${previous.environment} -> ${next.environment}`),
        'ABORTED',
      );
      this.#lifecycle.push(
        this.#entry(
          'ENVIRONMENT_SWITCH',
          `${previous.environment}/${previous.session_id} -> ${next.environment}/${next.session_id}`,
        ),
      );
    }
  }

  closePage(): void {
    if (this.#pageClosed) {
      return;
    }
    this.#pageClosed = true;
    // Deliberately no network call: closing the page must not stop a position,
    // cancel protection, or flush a queued command.
    this.#lifecycle.push(this.#entry('PAGE_HIDDEN', 'page closed; no control write emitted'));
  }

  async query(request: ProjectionQueryRequest): Promise<ProjectionPage> {
    this.#assertScope(request.scope, 'query');
    this.#assertReadable();
    this.#assertLimit(request.limit);

    const epoch = this.#epoch;
    const controller = new AbortController();
    const key = queryCacheKey(request);
    const response = await this.#guardAbort(
      this.#transport.send({
        method: 'GET',
        url: buildQueryUrl(this.baseUrl, request),
        headers: { accept: 'application/json' },
        body: null,
        signal: controller.signal,
      }),
      controller,
      epoch,
      'query',
    );

    if (epoch !== this.#epoch) {
      throw new EnvironmentSwitchAbortError('projection response arrived after the session changed');
    }
    if (response.status >= 400) {
      const apiError = parseApiError(response.body);
      throw new ApiResponseError(
        response.status,
        apiError?.reason_code ?? 'CONTROL_API_ERROR',
        apiError?.message ?? 'projection read failed',
      );
    }

    let page: ProjectionPage;
    try {
      page = JSON.parse(response.body) as ProjectionPage;
    } catch {
      throw new InvalidProjectionError('response body is not JSON');
    }
    assertProjection(page, this.#session);
    if (scopeCacheKey(page.scope) !== scopeCacheKey(request.scope)) {
      throw new CrossScopeProjectionError(
        `requested ${scopeCacheKey(request.scope)} received ${scopeCacheKey(page.scope)}`,
      );
    }

    this.#cache.set(key, {
      key,
      environment: this.#session.environment,
      page,
      stored_at: new Date(this.#now()).toISOString(),
    });
    this.#snapshotRequired = false;
    return page;
  }

  async command(request: ControlCommandRequest, key: string): Promise<ControlCommandReceipt> {
    const action = request.action;
    if (key.trim().length === 0) {
      this.#recordAttempt(action, '', 'BLOCKED_REQUEST', 'IDEMPOTENCY_KEY_REQUIRED', false, null);
      throw new ControlClientError('IDEMPOTENCY_KEY_REQUIRED', 'Control commands require a non-empty idempotency key.');
    }
    if (this.#pageClosed) {
      this.#recordAttempt(action, key, 'BLOCKED_PAGE_CLOSED', 'PAGE_CLOSED_CONTROL_WRITE_BLOCKED', false, null);
      throw new PageClosedWriteBlockedError(action);
    }
    if (!this.#online) {
      // Refused before touching the transport and before any queue: an offline
      // PWA is not an order queue.
      this.#recordAttempt(action, key, 'BLOCKED_OFFLINE', 'OFFLINE_CONTROL_WRITE_BLOCKED', false, null);
      throw new OfflineWriteBlockedError(action);
    }
    if (request.scope.environment !== this.#session.environment) {
      this.#recordAttempt(action, key, 'BLOCKED_ENVIRONMENT', 'CROSS_ENVIRONMENT_SCOPE', false, null);
      throw new CrossEnvironmentError(`control action ${action} targets ${request.scope.environment}`);
    }

    const epoch = this.#epoch;
    const controller = new AbortController();
    const seq = (this.#pendingSeq += 1);
    this.#pending.set(seq, controller);
    const attemptIndex = this.#recordAttempt(action, key, 'PENDING', null, true, null);

    try {
      const response = await this.#guardAbort(
        this.#transport.send({
          method: 'POST',
          url: `${this.baseUrl}/control-commands`,
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
            'idempotency-key': key,
          },
          body: JSON.stringify(request),
          signal: controller.signal,
        }),
        controller,
        epoch,
        'command',
      );

      if (epoch !== this.#epoch) {
        this.#patchAttempt(attemptIndex, { status: 'ABORTED', reason_code: 'ENVIRONMENT_SWITCH_ABORTED' });
        throw new EnvironmentSwitchAbortError('control receipt arrived after the session changed');
      }
      if (response.status >= 400) {
        const apiError = parseApiError(response.body);
        const reason = apiError?.reason_code ?? `HTTP_${response.status}`;
        this.#patchAttempt(attemptIndex, { status: 'REJECTED', reason_code: reason });
        throw new ApiResponseError(response.status, reason, apiError?.message ?? 'control command rejected');
      }

      let receipt: ControlCommandReceipt;
      try {
        receipt = JSON.parse(response.body) as ControlCommandReceipt;
      } catch {
        this.#patchAttempt(attemptIndex, { status: 'REJECTED', reason_code: 'INVALID_CONTROL_RECEIPT' });
        throw new InvalidReceiptError('non-JSON');
      }
      if (!RECEIPT_STATUSES.includes(receipt.status)) {
        this.#patchAttempt(attemptIndex, { status: 'REJECTED', reason_code: 'INVALID_CONTROL_RECEIPT' });
        throw new InvalidReceiptError(String(receipt.status));
      }
      this.#patchAttempt(attemptIndex, {
        status: 'RECEIPT_RECEIVED',
        reason_code: receipt.reason,
        receipt_status: receipt.status,
      });
      // Never cached and never treated as a completed venue action.
      return receipt;
    } catch (error) {
      if (error instanceof EnvironmentSwitchAbortError || error instanceof ControlClientError) {
        throw error;
      }
      const aborted = controller.signal.aborted || epoch !== this.#epoch;
      this.#patchAttempt(attemptIndex, {
        status: aborted ? 'ABORTED' : 'REJECTED',
        reason_code: aborted ? 'ENVIRONMENT_SWITCH_ABORTED' : 'CONTROL_TRANSPORT_ERROR',
      });
      if (aborted) {
        throw new EnvironmentSwitchAbortError('pending control write aborted');
      }
      throw new ControlClientError(
        'CONTROL_TRANSPORT_ERROR',
        `Control transport failed for ${action}; the outcome is unknown and must be reconciled.`,
      );
    } finally {
      this.#pending.delete(seq);
    }
  }

  follow(cursor: StreamCursor | null): AsyncIterable<ProjectionEvent> {
    this.#assertReadable();
    if (cursor !== null) {
      if (cursor.scope.environment !== this.#session.environment) {
        throw new CrossEnvironmentError('stream cursor belongs to another environment');
      }
      if (cursor.sequence < 0) {
        throw new ControlClientError('INVALID_STREAM_CURSOR', 'Stream cursor sequence must not be negative.');
      }
    }

    const client = this;
    return {
      async *[Symbol.asyncIterator](): AsyncGenerator<ProjectionEvent, void, undefined> {
        let nextCursor: StreamCursor | null = cursor;
        while (!client.isPageClosed()) {
          if (client.isOffline()) {
            throw new OfflineReadError();
          }
          const epoch = client.#epoch;
          const controller = new AbortController();
          const response = await client.#guardAbort(
            client.#transport.send({
              method: 'GET',
              url: buildEventsUrl(client.baseUrl, nextCursor),
              headers: { accept: 'application/x-ndjson' },
              body: null,
              signal: controller.signal,
            }),
            controller,
            epoch,
            'stream',
          );
          if (epoch !== client.#epoch) {
            throw new EnvironmentSwitchAbortError('stream response arrived after the session changed');
          }
          if (response.status >= 400) {
            const apiError = parseApiError(response.body);
            throw new ApiResponseError(
              response.status,
              apiError?.reason_code ?? 'CONTROL_API_ERROR',
              apiError?.message ?? 'event read failed',
            );
          }
          let page: EventPage;
          try {
            page = JSON.parse(response.body) as EventPage;
          } catch {
            throw new InvalidProjectionError('event page is not JSON');
          }
          if (!Array.isArray(page.events)) {
            throw new InvalidProjectionError('event page has no events array');
          }
          if (page.gap || page.requires_snapshot) {
            client.#snapshotRequired = true;
            throw new GapRequiresSnapshotError();
          }
          for (const item of page.events) {
            yield item.event;
          }
          nextCursor = page.next_cursor;
        }
      },
    };
  }

  #assertReadable(): void {
    if (this.#pageClosed) {
      throw new ControlClientError('PAGE_CLOSED', 'The page is closed; reopen it before reading projections.');
    }
    if (!this.#online) {
      throw new OfflineReadError();
    }
    if (Date.parse(this.#session.expires_at) <= this.#now()) {
      throw new ControlClientError('SESSION_EXPIRED', 'The control session expired; re-authenticate.');
    }
  }

  #assertScope(scope: ScopeKey, operation: string): void {
    if (scope.environment !== this.#session.environment) {
      throw new CrossEnvironmentError(`${operation} scope ${scope.environment} != session ${this.#session.environment}`);
    }
  }

  #assertLimit(limit: number): void {
    if (!Number.isInteger(limit) || limit <= 0 || limit > 200) {
      throw new ControlClientError('INVALID_PAGE_LIMIT', 'Projection page limit must be an integer in 1..200.');
    }
  }

  async #guardAbort(
    pending: Promise<HttpResponseLike>,
    controller: AbortController,
    epoch: number,
    operation: string,
  ): Promise<HttpResponseLike> {
    const hook: { handler: (() => void) | null } = { handler: null };
    const aborted = new Promise<never>((_resolve, reject) => {
      const handler = (): void => {
        reject(new EnvironmentSwitchAbortError(`${operation} aborted by environment switch`));
      };
      hook.handler = handler;
      if (controller.signal.aborted) {
        handler();
        return;
      }
      controller.signal.addEventListener('abort', handler, { once: true });
    });
    // Keep the losing promise from becoming an unhandled rejection.
    pending.catch(() => undefined);
    try {
      const response = await Promise.race([pending, aborted]);
      if (epoch !== this.#epoch) {
        throw new EnvironmentSwitchAbortError(`${operation} completed after the session changed`);
      }
      return response;
    } finally {
      const handler = hook.handler;
      if (handler !== null) {
        controller.signal.removeEventListener('abort', handler);
      }
    }
  }

  #abortPending(error: ControlClientError, status: ControlWriteStatus): void {
    for (const controller of this.#pending.values()) {
      controller.abort();
    }
    this.#pending.clear();
    this.#attempts = this.#attempts.map(attempt =>
      attempt.status === 'PENDING'
        ? { ...attempt, status, reason_code: error.reason_code }
        : attempt,
    );
  }

  #recordAttempt(
    action: ControlAction,
    key: string,
    status: ControlWriteStatus,
    reasonCode: string | null,
    sent: boolean,
    receiptStatus: ControlStatus | null,
  ): number {
    this.#attempts.push({
      action,
      idempotency_key: key,
      environment: this.#session.environment,
      started_at: new Date(this.#now()).toISOString(),
      status,
      reason_code: reasonCode,
      sent,
      receipt_status: receiptStatus,
    });
    return this.#attempts.length - 1;
  }

  #patchAttempt(index: number, patch: Partial<ControlWriteAttempt>): void {
    const current = this.#attempts[index];
    if (current === undefined) {
      return;
    }
    this.#attempts[index] = { ...current, ...patch };
  }

  #entry(kind: ClientLifecycleKind, detail: string): LifecycleEntry {
    return {
      kind,
      at: new Date(this.#now()).toISOString(),
      detail,
      controlWrites: 0,
      networkRequests: 0,
    };
  }
}

/**
 * Bind the generated `ControlApiClient` contract to a base URL and session.
 * No mock or production URL is assumed: `baseUrl` is mandatory.
 */
export function createApiClient(
  baseUrl: string,
  session: SessionRef,
  options: ClientOptions = {},
): AetherControlClient {
  return new ControlClient(baseUrl, session, options);
}
