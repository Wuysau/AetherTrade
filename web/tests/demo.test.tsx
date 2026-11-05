// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RuntimeApi } from '../src/runtime/api';
import { Demo } from '../src/runtime/demo';
import { LocaleProvider } from '../src/runtime/i18n';

const disabled = { environment: 'BINANCE_DEMO', product: 'SPOT', state: 'DISABLED', execution_authorized: false, credential_storage: 'MEMORY_ONLY', balances: [], capabilities: [], blockers: ['DEMO_ACCOUNT_QUALIFICATION_REQUIRED'], endpoint: 'https://demo-api.binance.com', budget_cap_usdt: '10000', connection_epoch: 'fixture-epoch-1234567890' };
const connected = { ...disabled, state: 'READ_ONLY', fingerprint: '1234abcdef', balances: [{ asset: 'USDT', free: '45', locked: '5' }], checked_at: 1 };
beforeEach(() => window.localStorage.clear());
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

it('requires acknowledgement, masks user fields and clears credentials after submission', async () => {
  const requests: Array<{path: string, options?: RequestInit}> = [];
  let current = disabled;
  const api = new RuntimeApi(async (path, options) => {
    requests.push({ path: String(path), options });
    if (String(path).endsWith('/connect')) current = connected;
    return new Response(JSON.stringify(current));
  });
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText('DISABLED');
  const key = screen.getByLabelText('Demo API Key') as HTMLInputElement;
  const secret = screen.getByLabelText('Demo API Secret') as HTMLInputElement;
  expect(key.type).toBe('password'); expect(secret.type).toBe('password');
  fireEvent.change(key, { target: { value: 'fixture-key-123456789' } });
  fireEvent.change(secret, { target: { value: 'fixture-secret-123456' } });
  const button = screen.getByRole('button', { name: '验证只读连接' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(button); fireEvent.click(button);
  await screen.findByText('READ_ONLY');
  expect(key.value).toBe(''); expect(secret.value).toBe('');
  expect(requests.filter(r => r.path.endsWith('/connect'))).toHaveLength(1);
  expect(JSON.parse(String(requests.find(r => r.path.endsWith('/connect'))?.options?.body)).connection_epoch).toBe(disabled.connection_epoch);
  expect(screen.getByText('45')).toBeDefined();
  expect(JSON.stringify(window.localStorage)).not.toContain('fixture');
  expect(screen.queryByRole('button', { name: /下单|交易启用/ })).toBeNull();
});

it('disconnects without making a misleading cancellation claim', async () => {
  let current = connected, sent: string | null = null;
  const api = new RuntimeApi(async (path, options) => { if (String(path).endsWith('/disconnect')) { sent = String(options?.body); current = disabled; } return new Response(JSON.stringify(current)); });
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText('READ_ONLY');
  fireEvent.click(screen.getByRole('button', { name: '移除本次凭据' }));
  await screen.findByText('DISABLED');
  expect(JSON.parse(sent!).connection_epoch).toBe(connected.connection_epoch);
  expect(screen.getByText(/不会取消交易所挂单/)).toBeDefined();
});

it('clears secrets on safe error and does not show upstream error bodies', async () => {
  const api = new RuntimeApi(async path => new Response(JSON.stringify(String(path).endsWith('/connect') ? { error: { code: 'DEMO_CREDENTIAL_REJECTED', message: 'safe failure' } } : disabled), { status: String(path).endsWith('/connect') ? 422 : 200 }));
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText('DISABLED');
  const secret = screen.getByLabelText('Demo API Secret') as HTMLInputElement;
  fireEvent.change(screen.getByLabelText('Demo API Key'), { target: { value: 'fixture-key-123456789' } });
  fireEvent.change(secret, { target: { value: 'fixture-secret-123456' } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '验证只读连接' }));
  await screen.findByText('safe failure');
  expect(secret.value).toBe('');
});

it('ignores obsolete status errors after a newer connection succeeds', async () => {
  let rejectInitial!: (reason: unknown) => void;
  const initial = new Promise<Response>((_, reject) => { rejectInitial = reject; });
  let statusRequests = 0;
  const api = new RuntimeApi(async path => String(path).endsWith('/status') ? (++statusRequests === 1 ? new Response(JSON.stringify(disabled)) : statusRequests === 2 ? initial : new Response(JSON.stringify(connected))) : new Response(JSON.stringify(connected)));
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText('DISABLED');
  fireEvent.focus(window);
  await waitFor(() => expect(statusRequests).toBe(2));
  fireEvent.change(screen.getByLabelText('Demo API Key'), { target: { value: 'fixture-key-123456789' } });
  fireEvent.change(screen.getByLabelText('Demo API Secret'), { target: { value: 'fixture-secret-123456' } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '验证只读连接' }));
  await screen.findByText('READ_ONLY');
  rejectInitial(new Error('old request failed'));
  await new Promise(resolve => setTimeout(resolve, 10));
  expect(screen.queryByRole('alert')).toBeNull();
});


it('marks the previous snapshot unverified after poll failure and clears only the poll error after recovery', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T12:00:00Z'));
  let poll = 0;
  const api = new RuntimeApi(async path => {
    if (String(path).endsWith('/disconnect')) return new Response(JSON.stringify({ error: { code: 'FAILED', message: '移除凭据请求失败' } }), { status: 503 });
    poll += 1;
    if (poll === 2) throw new Error('poll failed');
    return new Response(JSON.stringify({ ...connected, checked_at: Math.floor(Date.now() / 1000), expires_at: Math.floor(Date.now() / 1000) + 900 }));
  });
  await act(async () => { render(<LocaleProvider><Demo api={api} /></LocaleProvider>); });
  expect(screen.getByText('READ_ONLY')).toBeDefined();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
  expect(screen.getByText(/无法连接本地服务/)).toBeDefined();
  expect(screen.getByText('45')).toBeDefined();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '移除本次凭据' })); });
  expect(screen.getByText('移除凭据请求失败')).toBeDefined();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(screen.queryByText(/无法连接本地服务/)).toBeNull();
  expect(screen.getByText('移除凭据请求失败')).toBeDefined();
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
  expect(screen.getByText(/不会取消交易所挂单/)).toBeDefined();
});

it('marks an aged snapshot unverified on resume even when a poll is still pending', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T12:00:00Z'));
  let poll = 0;
  const api = new RuntimeApi(async () => {
    if (++poll > 1) return new Promise<Response>(() => {});
    return new Response(JSON.stringify({ ...connected, checked_at: Math.floor(Date.now() / 1000), expires_at: Math.floor(Date.now() / 1000) + 900 }));
  });
  await act(async () => { render(<LocaleProvider><Demo api={api} /></LocaleProvider>); });
  expect(screen.getByText('READ_ONLY')).toBeDefined();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  vi.setSystemTime(new Date('2026-11-04T12:01:00Z'));
  await act(async () => { window.dispatchEvent(new Event('focus')); });
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
  expect(screen.getByText('45')).toBeDefined();
});

it('does not present a locally expired credential as a verified current connection', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T12:00:00Z'));
  const now = Math.floor(Date.now() / 1000);
  const api = new RuntimeApi(async () => new Response(JSON.stringify({ ...connected, checked_at: now, expires_at: now + 5 })));
  await act(async () => { render(<LocaleProvider><Demo api={api} /></LocaleProvider>); });
  expect(screen.queryByText(/上次快照（当前状态未验证）/)).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
});

it('treats a malformed snapshot timestamp as unverified', async () => {
  const api = new RuntimeApi(async () => new Response(JSON.stringify({ ...connected, checked_at: 'invalid' })));
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText(/上次快照（当前状态未验证）/);
});

it('marks a snapshot stale as the local clock advances while the next poll is pending', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T12:00:00Z'));
  const checked = Math.floor(Date.now() / 1000);
  let poll = 0;
  const api = new RuntimeApi(async () => ++poll === 1
    ? new Response(JSON.stringify({ ...connected, checked_at: checked, expires_at: checked + 900 }))
    : new Promise<Response>(() => {}));
  await act(async () => { render(<LocaleProvider><Demo api={api} /></LocaleProvider>); });
  expect(screen.queryByText(/上次快照（当前状态未验证）/)).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
});

it.each(['DISABLED', 'CONNECTING'])('marks %s unverified when the local status response ages during a hung poll', async state => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T12:00:00Z'));
  let poll = 0;
  const api = new RuntimeApi(async () => ++poll === 1
    ? new Response(JSON.stringify({ ...disabled, state }))
    : new Promise<Response>(() => {}));
  await act(async () => { render(<LocaleProvider><Demo api={api} /></LocaleProvider>); });
  expect(screen.getByText(state)).toBeDefined();
  expect(screen.queryByText(/上次快照（当前状态未验证）/)).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
});

it('keeps an operation result unverified when the required post-operation status reread fails', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T12:00:00Z'));
  let polls = 0;
  const now = Math.floor(Date.now() / 1000);
  const fresh = { ...connected, checked_at: now, expires_at: now + 900 };
  const api = new RuntimeApi(async path => {
    if (String(path).endsWith('/connect')) return new Response(JSON.stringify(fresh));
    if (++polls === 1) return new Response(JSON.stringify(disabled));
    if (polls === 2) throw new Error('status unavailable');
    return new Response(JSON.stringify(fresh));
  });
  await act(async () => { render(<LocaleProvider><Demo api={api} /></LocaleProvider>); });
  fireEvent.change(screen.getByLabelText('Demo API Key'), { target: { value: 'fixture-key-123456789' } });
  fireEvent.change(screen.getByLabelText('Demo API Secret'), { target: { value: 'fixture-secret-123456' } });
  fireEvent.click(screen.getByRole('checkbox'));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '验证只读连接' })); });
  expect(screen.getByText('READ_ONLY')).toBeDefined();
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
  expect(screen.getByText(/无法连接本地服务/)).toBeDefined();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(screen.queryByText(/无法连接本地服务/)).toBeNull();
  expect(screen.queryByText(/上次快照（当前状态未验证）/)).toBeNull();
});

it('requires a new status reread after reactivation and ignores an earlier in-flight response', async () => {
  const now = Math.floor(Date.now() / 1000);
  const fresh = { ...connected, checked_at: now, expires_at: now + 900 };
  let oldResolve!: (value: Response) => void, freshResolve!: (value: Response) => void;
  const oldRequest = new Promise<Response>(resolve => { oldResolve = resolve; });
  const freshRequest = new Promise<Response>(resolve => { freshResolve = resolve; });
  let calls = 0;
  const api = new RuntimeApi(async () => ++calls === 1 ? new Response(JSON.stringify(fresh)) : calls === 2 ? oldRequest : freshRequest);
  const view = render(<LocaleProvider><Demo api={api} active /></LocaleProvider>);
  await screen.findByText('READ_ONLY');
  fireEvent.focus(window);
  await waitFor(() => expect(calls).toBe(2));
  view.rerender(<LocaleProvider><Demo api={api} active={false} /></LocaleProvider>);
  view.rerender(<LocaleProvider><Demo api={api} active /></LocaleProvider>);
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
  await act(async () => { oldResolve(new Response(JSON.stringify(fresh))); });
  await waitFor(() => expect(calls).toBe(3));
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
  await act(async () => { freshResolve(new Response(JSON.stringify(fresh))); });
  await waitFor(() => expect(screen.queryByText(/上次快照（当前状态未验证）/)).toBeNull());
});

it.each(['DISABLED', 'CONNECTING'])('does not treat a delayed %s status response as fresh on receipt', async state => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T12:00:00Z'));
  let finish!: (response: Response) => void;
  const initial = new Promise<Response>(resolve => { finish = resolve; });
  let calls = 0;
  const api = new RuntimeApi(async () => ++calls === 1 ? initial : new Promise<Response>(() => {}));
  await act(async () => { render(<LocaleProvider><Demo api={api} /></LocaleProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(16_000); });
  await act(async () => { finish(new Response(JSON.stringify({ ...disabled, state }))); });
  expect(screen.getByText(state)).toBeDefined();
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
});

it('invalidates an older status request on window focus until a post-focus reread succeeds', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-04T12:00:00Z'));
  const now = Math.floor(Date.now() / 1000), fresh = { ...connected, checked_at: now, expires_at: now + 900 };
  let oldResolve!: (value: Response) => void, freshResolve!: (value: Response) => void;
  const oldRequest = new Promise<Response>(resolve => { oldResolve = resolve; });
  const freshRequest = new Promise<Response>(resolve => { freshResolve = resolve; });
  let calls = 0;
  const api = new RuntimeApi(async () => ++calls === 1 ? new Response(JSON.stringify(fresh)) : calls === 2 ? oldRequest : freshRequest);
  await act(async () => { render(<LocaleProvider><Demo api={api} /></LocaleProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(calls).toBe(2);
  await act(async () => { window.dispatchEvent(new Event('focus')); });
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
  await act(async () => { oldResolve(new Response(JSON.stringify(fresh))); });
  expect(calls).toBe(3);
  expect(screen.getByText(/上次快照（当前状态未验证）/)).toBeDefined();
  await act(async () => { freshResolve(new Response(JSON.stringify(fresh))); });
  expect(screen.queryByText(/上次快照（当前状态未验证）/)).toBeNull();
});

it.each([undefined, '误'.repeat(16)])('does not submit credentials without a valid server-issued preflight epoch', async connection_epoch => {
  const withoutEpoch = { ...disabled, connection_epoch };
  const requests: string[] = [];
  const api = new RuntimeApi(async path => { requests.push(String(path)); return new Response(JSON.stringify(withoutEpoch)); });
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText('DISABLED');
  fireEvent.change(screen.getByLabelText('Demo API Key'), { target: { value: 'fixture-key-123456789' } });
  fireEvent.change(screen.getByLabelText('Demo API Secret'), { target: { value: 'fixture-secret-123456' } });
  fireEvent.click(screen.getByRole('checkbox'));
  expect((screen.getByRole('button', { name: '验证只读连接' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '移除本次凭据' }) as HTMLButtonElement).disabled).toBe(true);
  expect(requests.filter(path => path.endsWith('/connect'))).toHaveLength(0);
});


it('uses the rotated preflight epoch after credential removal', async () => {
  let current = { ...disabled }, submitted: Record<string, unknown> | null = null;
  const rotated = 'fixture-epoch-rotated-12345';
  const api = new RuntimeApi(async (path, options) => {
    if (String(path).endsWith('/disconnect')) current = { ...disabled, connection_epoch: rotated };
    if (String(path).endsWith('/connect')) { submitted = JSON.parse(String(options?.body)); current = { ...connected, connection_epoch: rotated }; }
    return new Response(JSON.stringify(current));
  });
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText('DISABLED');
  fireEvent.click(screen.getByRole('button', { name: '移除本次凭据' }));
  await waitFor(() => expect((screen.getByRole('button', { name: '移除本次凭据' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('Demo API Key'), { target: { value: 'fixture-key-123456789' } });
  fireEvent.change(screen.getByLabelText('Demo API Secret'), { target: { value: 'fixture-secret-123456' } });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: '验证只读连接' }));
  await waitFor(() => expect(submitted).not.toBeNull());
  expect(submitted?.connection_epoch).toBe(rotated);
});

it('opens the separate AI runtime controls without starting a model or window', async () => {
  const calls: string[] = [];
  const api = new RuntimeApi(async path => {
    const name = String(path); calls.push(name);
    if (name.endsWith('/model-configs')) return new Response(JSON.stringify({profiles:[], default_profile_id:'default'}));
    if (name.endsWith('/runtime/status')) return new Response(JSON.stringify({configured:false, execution_authorized:false, blocker:'DEMO_RUNTIME_NOT_BOUND'}));
    return new Response(JSON.stringify(disabled));
  });
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText('DISABLED');
  expect(calls.some(path => path.endsWith('/runtime/status'))).toBe(false);
  fireEvent.click(screen.getByRole('button',{name:'AI 模拟交易控制'}));
  await screen.findByRole('region',{name:'AI 模拟交易控制'});
  await screen.findByText(/DEMO_RUNTIME_NOT_BOUND/);
  expect(calls.some(path => path.endsWith('/runtime/start'))).toBe(false);
});
