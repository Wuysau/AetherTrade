// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RuntimeApi } from '../src/runtime/api';
import { Demo } from '../src/runtime/demo';
import { LocaleProvider } from '../src/runtime/i18n';

const disabled = { environment: 'BINANCE_DEMO', product: 'SPOT', state: 'DISABLED', execution_authorized: false, credential_storage: 'MEMORY_ONLY', balances: [], capabilities: [], blockers: ['DEMO_ACCOUNT_QUALIFICATION_REQUIRED'], endpoint: 'https://demo-api.binance.com', budget_cap_usdt: '10000' };
const connected = { ...disabled, state: 'READ_ONLY', fingerprint: '1234abcdef', balances: [{ asset: 'USDT', free: '45', locked: '5' }], checked_at: 1 };
beforeEach(() => window.localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('requires acknowledgement, masks user fields and clears credentials after submission', async () => {
  const requests: Array<{path: string, options?: RequestInit}> = [];
  const api = new RuntimeApi(async (path, options) => {
    requests.push({ path: String(path), options });
    return new Response(JSON.stringify(String(path).endsWith('/connect') ? connected : disabled));
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
  expect(screen.getByText('45')).toBeDefined();
  expect(JSON.stringify(window.localStorage)).not.toContain('fixture');
  expect(screen.queryByRole('button', { name: /下单|交易启用/ })).toBeNull();
});

it('disconnects without making a misleading cancellation claim', async () => {
  const api = new RuntimeApi(async path => new Response(JSON.stringify(String(path).endsWith('/disconnect') ? disabled : connected)));
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  await screen.findByText('READ_ONLY');
  fireEvent.click(screen.getByRole('button', { name: '移除本次凭据' }));
  await screen.findByText('DISABLED');
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
  const api = new RuntimeApi(async path => String(path).endsWith('/status') ? initial : new Response(JSON.stringify(connected)));
  render(<LocaleProvider><Demo api={api} /></LocaleProvider>);
  fireEvent.change(screen.getByLabelText('Demo API Key'), { target: { value: 'fixture-key-123456789' } });
  fireEvent.change(screen.getByLabelText('Demo API Secret'), { target: { value: 'fixture-secret-123456' } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '验证只读连接' }));
  await screen.findByText('READ_ONLY');
  rejectInitial(new Error('old request failed'));
  await new Promise(resolve => setTimeout(resolve, 10));
  expect(screen.queryByRole('alert')).toBeNull();
});
