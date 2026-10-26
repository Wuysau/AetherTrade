// IP-29 status-view tests. They are offline/unit checks only: they do not
// represent native, platform, or trading qualification evidence.

import { describe, expect, it } from 'vitest';

import {
  createApiClient,
  OfflineWriteBlockedError,
} from '../src/api/client';
import {
  SIX_FUNCTION_AREAS,
  findOne,
  findAll,
  textContent,
  type ViewElement,
} from '../src/app/App';
import { PositionView } from '../src/features/positions/PositionView';
import type {
  ProjectionQueryRequest,
  ScopeKey,
  SessionRef,
} from '../src/contracts/generated';
import {
  DeferredControlTransport,
  FIXED_NOW_MS,
  RecordingTransport,
  collectStateRows,
  makeExitReceipt,
  makeOrder,
  makePage,
  makePaperHarness,
  makePositionProjection,
  makeScope,
  makeSession,
  renderOfflinePositionFixture,
  renderOnlinePositionFixture,
} from './support/ip-29';

function queryFor(scope: ScopeKey): ProjectionQueryRequest {
  return {
    scope,
    resource: 'positions',
    owner_strategy_id: null,
    cursor: null,
    limit: 50,
    maximum_age_ms: 5000,
  };
}

function envFromUrl(url: string): ScopeKey['environment'] {
  const environment = new URL(url).searchParams.get('environment');
  if (environment !== 'RESEARCH' && environment !== 'PAPER' && environment !== 'TESTNET' && environment !== 'LIVE') {
    throw new Error(`unexpected environment in fixture URL: ${String(environment)}`);
  }
  return environment;
}

describe('IP-29 status views', () => {
  it('test_four_states_not_collapsed', () => {
    const scope = makeScope('PAPER');
    const position = makePositionProjection(scope);
    const view = PositionView({
      position,
      freshness: position.freshness,
      orders: [makeOrder(scope)],
      exits: [makeExitReceipt()],
      controlAvailable: true,
      onRequestExit: () => undefined,
    });

    const rows = collectStateRows(view);

    // Four independently addressed rows, in a stable order.
    expect(rows.map(row => row.key)).toEqual(['submitted', 'filled', 'protected', 'exited']);
    expect(rows.map(row => row.label)).toEqual(['已提交', '已成交', '已保护', '已退出']);

    // Each row carries its own domain state, not a single merged badge.
    expect(rows[0].raw_code).toBe('PARTIALLY_FILLED/SUBMIT/CONFIRMED');
    expect(rows[1].raw_code).toBe('0.4+0.6BASE');
    expect(rows[2].raw_code).toBe('NATIVE/VERIFIED_NATIVE');
    expect(rows[3].raw_code).toBe('FLAT_VERIFIED');
    expect(new Set(rows.map(row => row.raw_code)).size).toBe(4);
    expect(rows.every(row => row.present)).toBe(true);

    // The submitted row must not absorb fill/protection/exit state.
    expect(rows[0].raw_code).not.toContain('VERIFIED_NATIVE');
    expect(rows[0].raw_code).not.toContain('FLAT_VERIFIED');
    expect(rows[3].raw_code).not.toContain('PARTIALLY_FILLED');

    const stateList = findOne(view, element => element.props['data-testid'] === 'position-states');
    expect(stateList).not.toBeNull();
    const stateItems = findAll(view, element => typeof element.props['data-state-key'] === 'string');
    expect(stateItems).toHaveLength(4);
    expect(textContent(view)).toContain('已提交');
    expect(textContent(view)).toContain('已退出');
  });

  it('test_environment_switch_clears_cache', async () => {
    const liveSession = makeSession('LIVE', { session_id: 'session-live-1' });
    const liveScope = makeScope('LIVE');
    const transport = new DeferredControlTransport(request => {
      const environment = envFromUrl(request.url);
      return JSON.stringify(makePage(makeScope(environment)));
    });
    const client = createApiClient('https://control.example.test', liveSession, {
      transport,
      now: () => FIXED_NOW_MS,
    });

    const livePage = await client.query(queryFor(liveScope));
    expect(livePage.scope.environment).toBe('LIVE');
    expect(client.cachedScopes()).toEqual(['LIVE']);
    const getCountBeforeSwitch = transport.gets.length;

    const pendingExit = client.command(
      {
        action: 'exit_owned',
        scope: liveScope,
        expected_revision: 1,
        ownership_epoch: 7,
        transfer: null,
      },
      'idem-live-exit',
    );
    expect(transport.pending).toBe(true);
    expect(client.pendingControlWrites()).toBe(1);

    const paperSession = makeSession('PAPER', { session_id: 'session-paper-1' });
    client.switchSession(paperSession);

    // Cache, pending request and any late receipt are dropped.
    expect(client.cachedScopes()).toHaveLength(0);
    expect(client.readCached(queryFor(liveScope))).toBeNull();
    expect(client.pendingControlWrites()).toBe(0);
    expect(client.requiresSnapshot()).toBe(true);
    expect(transport.pendingSignals[0].aborted).toBe(true);

    await expect(pendingExit).rejects.toMatchObject({ reason_code: 'ENVIRONMENT_SWITCH_ABORTED' });
    const abortedAttempt = client
      .controlWrites()
      .find(attempt => attempt.reason_code === 'ENVIRONMENT_SWITCH_ABORTED');
    expect(abortedAttempt).toBeDefined();
    expect(abortedAttempt?.status).toBe('ABORTED');

    // A late LIVE receipt is not accepted after the switch.
    transport.settleWith({
      command_id: 'command-late',
      status: 'APPLIED',
      reason: null,
      existing_result_ref: null,
    });
    expect(client.controlWrites().some(attempt => attempt.receipt_status === 'APPLIED')).toBe(false);

    // PAPER reads go to the server instead of reusing the LIVE cache.
    const paperScope = makeScope('PAPER');
    const paperPage = await client.query(queryFor(paperScope));
    expect(paperPage.scope.environment).toBe('PAPER');
    expect(transport.gets.length).toBe(getCountBeforeSwitch + 1);
    expect(client.cachedScopes()).toEqual(['PAPER']);

    // Writing a LIVE-scoped control command under the PAPER session is refused.
    await expect(
      client.command(
        {
          action: 'pause_new',
          scope: liveScope,
          expected_revision: 1,
          ownership_epoch: 7,
          transfer: null,
        },
        'idem-cross-env',
      ),
    ).rejects.toMatchObject({ reason_code: 'CROSS_ENVIRONMENT_SCOPE' });
    expect(transport.posts).toHaveLength(1);
  });

  it('test_offline_is_not_order_queue', async () => {
    const view = await renderOfflinePositionFixture();

    expect(view.offlineNotice()).toBe(true);
    await view.clickExit();

    expect(view.controlWrites()).toHaveLength(0);
    expect(view.queuedForReconnect()).toBe(false);
    expect(view.denialCodes()).toContain('OFFLINE_NO_CONTROL_WRITE');

    // Defense in depth: the client itself refuses even if a view bypasses the UI.
    await expect(
      view.client.command(
        {
          action: 'exit_owned',
          scope: view.scope,
          expected_revision: 1,
          ownership_epoch: 7,
          transfer: null,
        },
        'idem-offline-exit',
      ),
    ).rejects.toBeInstanceOf(OfflineWriteBlockedError);
    expect(view.controlWrites()).toHaveLength(0);

    // Offline reads do not silently serve the cached page as fresh data.
    await expect(view.client.query(queryFor(view.scope))).rejects.toMatchObject({
      reason_code: 'OFFLINE_NO_FRESH_READ',
    });

    // Reconnecting does not flush a hidden queue (there is none).
    view.goOnline();
    expect(view.controlWrites()).toHaveLength(0);
    expect(view.queuedForReconnect()).toBe(false);
  });

  it('test_mobile_closing_not_stop', async () => {
    const view = await renderOnlinePositionFixture();
    const requestsBeforeClose = (view.transport as RecordingTransport).requests.length;
    expect(requestsBeforeClose).toBe(1);

    view.closePage();

    expect(view.closed).toBe(true);
    expect(view.client.isPageClosed()).toBe(true);
    // Closing the page performs no request at all: no stop, no cancel-protection.
    expect((view.transport as RecordingTransport).requests.length).toBe(requestsBeforeClose);
    expect(view.controlWrites()).toHaveLength(0);
    expect(view.client.controlWrites().map(attempt => attempt.action)).not.toContain('exit_owned');
    const hidden = view.client.lifecycleLog().filter(entry => entry.kind === 'PAGE_HIDDEN');
    expect(hidden).toHaveLength(1);
    expect(hidden[0].controlWrites).toBe(0);

    // Reconnecting after a page close still sends nothing.
    view.goOffline();
    view.goOnline();
    expect(view.controlWrites()).toHaveLength(0);
    expect(view.queuedForReconnect()).toBe(false);
  });

  it('test_example_environment_is_marked_and_cannot_bind_live', async () => {
    const harness = makePaperHarness();
    await harness.refresh();
    const shell = harness.render();

    const banner = findOne(shell, element => element.props['data-testid'] === 'example-banner');
    expect(banner).not.toBeNull();
    expect(textContent(shell)).toContain('非生产后端');

    const sections = SIX_FUNCTION_AREAS.map(section => section.id);
    const rendered = findAll(
      shell,
      element => typeof element.props['data-section'] === 'string' && element.type === 'button',
    ).map(element => String(element.props['data-section']));
    expect(rendered).toEqual(sections);
    expect(textContent(shell)).toContain('策略中心');
    expect(textContent(shell)).toContain('权限设置');

    // The example transport is refused for LIVE and there is no implicit URL.
    expect(() =>
      createApiClient('https://control.example.test', makeSession('LIVE'), {
        transport: harness.transport,
        exampleTransport: true,
      }),
    ).toThrow(/MOCK_TRANSPORT_NOT_ALLOWED_FOR_LIVE|example transport/i);
    expect(() =>
      createApiClient('   ', makeSession('PAPER'), { transport: harness.transport }),
    ).toThrow(/base URL/i);
  });

  it('test_stream_gap_requires_snapshot', async () => {
    const scope = makeScope('PAPER');
    const session = makeSession('PAPER');
    const transport = new RecordingTransport(request => {
      if (request.method === 'GET' && request.url.includes('/events')) {
        return {
          status: 200,
          body: JSON.stringify({
            events: [],
            next_cursor: { scope, sequence: 5, stream_id: 'stream-1' },
            gap: true,
            requires_snapshot: true,
          }),
        };
      }
      return { status: 200, body: JSON.stringify(makePage(scope)) };
    });
    const client = createApiClient('http://127.0.0.1:8099', session, {
      transport,
      now: () => FIXED_NOW_MS,
    });
    await client.query(queryFor(scope));
    expect(client.requiresSnapshot()).toBe(false);

    const iterate = async (): Promise<void> => {
      const stream = client.follow({ scope, sequence: 4, stream_id: 'stream-1' });
      for await (const _event of stream) {
        throw new Error('a gap must not yield events');
      }
    };

    await expect(iterate()).rejects.toMatchObject({
      reason_code: 'STREAM_GAP_REQUIRES_SNAPSHOT',
    });
    expect(client.requiresSnapshot()).toBe(true);
  });

  it('test_projection_page_scope_mismatch_is_rejected', async () => {
    const paperScope = makeScope('PAPER');
    const transport = new RecordingTransport(() => ({
      status: 200,
      body: JSON.stringify(makePage(makeScope('LIVE'))),
    }));
    const client = createApiClient('https://control.example.test', makeSession('PAPER'), {
      transport,
      now: () => FIXED_NOW_MS,
    });

    await expect(client.query(queryFor(paperScope))).rejects.toMatchObject({
      reason_code: 'CROSS_ENVIRONMENT_SCOPE',
    });
    expect(client.cachedScopes()).toHaveLength(0);
  });

  it('test_app_shell_renders_three_core_sections', async () => {
    const view = await renderOnlinePositionFixture();
    const shell: ViewElement = view.render();
    expect(textContent(shell)).toContain('总览');
    expect(textContent(shell)).toContain('AI 动态');

    view.activeSection = 'overview';
    expect(findOne(view.render(), element => element.props['data-testid'] === 'overview-section')).not.toBeNull();

    view.activeSection = 'ai-activity';
    expect(findOne(view.render(), element => element.props['data-testid'] === 'ai-activity-section')).not.toBeNull();

    view.activeSection = 'positions-orders';
    expect(findOne(view.render(), element => element.props['data-testid'] === 'positions-section')).not.toBeNull();

    view.activeSection = 'permissions';
    const placeholder = findOne(view.render(), element => element.props['data-section'] === 'permissions');
    expect(placeholder).not.toBeNull();
    expect(
      findOne(view.render(), element => element.props['data-code'] === 'NOT_IMPLEMENTED_IN_IP_29'),
    ).not.toBeNull();
  });

  it('test_session_switch_to_live_after_cache_does_not_reuse_old_page', async () => {
    const paper = makeSession('PAPER');
    const paperScope = makeScope('PAPER');
    const transport = new RecordingTransport(() => ({
      status: 200,
      body: JSON.stringify(makePage(makeScope('PAPER'))),
    }));
    const client = createApiClient('https://control.example.test', paper, {
      transport,
      now: () => FIXED_NOW_MS,
    });
    await client.query(queryFor(paperScope));

    const next: SessionRef = makeSession('PAPER', { session_id: 'session-paper-2' });
    client.switchSession(next);
    expect(client.readCached(queryFor(paperScope))).toBeNull();
    expect(client.requiresSnapshot()).toBe(true);
    expect(client.pendingControlWrites()).toBe(0);
  });
});
