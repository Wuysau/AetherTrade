// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RuntimeApi } from '../src/runtime/api';
import { StrategyAutomation, executionLabel } from '../src/runtime/automation';
import { StrategyDialog } from '../src/runtime/forms';
import type { Strategy } from '../src/runtime/types';
const draft: Strategy = { id: 's1', name: '观察', symbol: 'BTCUSDT', mode: 'AI', budget: '1000', target_pct: '10', status: 'DRAFT', revision: 4, created_at: '', updated_at: '' };
beforeEach(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); }; });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it('leaves legacy draft inactive and explicitly activates with recurring cost acknowledgement', async () => {
  const api = new RuntimeApi(); const save = vi.spyOn(api, 'saveStrategy').mockResolvedValue(draft);
  render(<StrategyAutomation strategy={draft} api={api} onSaved={vi.fn()} />); expect(save).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '启用自动分析' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ acknowledge_model_cost: true, auto_execute: false, analysis_interval_seconds: 300 }), draft, 'ACTIVE'));
});
it('checkbox activates inactive AI without another modal and uncheck preserves active analysis', async () => {
  const api = new RuntimeApi(); const save = vi.spyOn(api, 'saveStrategy').mockResolvedValue(draft);
  const props = { api, onSaved: vi.fn() }; const view = render(<StrategyAutomation {...props} strategy={draft} />);
  fireEvent.click(screen.getByRole('checkbox'));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ auto_execute: true, acknowledge_model_cost: true }), draft, 'ACTIVE'));
  await waitFor(() => expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(false));
  const active: Strategy = { ...draft, status: 'ACTIVE', auto_execute: true, revision: 5 }; view.rerender(<StrategyAutomation {...props} strategy={active} />);
  fireEvent.click(screen.getByRole('checkbox')); await waitFor(() => expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ auto_execute: false }), active, 'ACTIVE'));
  expect(save.mock.calls[1][0]).not.toHaveProperty('acknowledge_model_cost');
});
it('switching away from AI clears automatic permissions and active state', async () => {
  const api = new RuntimeApi(); const save = vi.spyOn(api, 'saveStrategy').mockResolvedValue(draft);
  render(<StrategyDialog api={api} existing={{ ...draft, status: 'ACTIVE', auto_execute: true }} mode="AI" onClose={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('决策模式'), { target: { value: 'RULE' } }); fireEvent.click(screen.getByRole('button', { name: '保存策略' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ mode: 'RULE', auto_execute: false, acknowledge_model_cost: false }), expect.anything(), 'DRAFT'));
});
it('serializes strict CAS and creation activation plus authenticated read notifications', async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}'))); const api = new RuntimeApi(fetcher);
  api.setSession({ username: 'owner', csrf_token: 'csrf', expires_at: '', environment: 'PAPER' });
  await api.saveStrategy({ ...draft, auto_execute: true, acknowledge_model_cost: true }, draft, 'ACTIVE');
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ expected_revision: 4, status: 'ACTIVE', auto_execute: true, acknowledge_model_cost: true });
  await api.saveStrategy({ name: 'new', mode: 'AI', symbol: 'BTCUSDT', budget: '100', target_pct: '1', acknowledge_model_cost: true }, undefined, 'ACTIVE');
  expect(JSON.parse(fetcher.mock.calls[1][1].body).status).toBe('ACTIVE');
  await api.markNotificationsRead({ all: true }); expect(fetcher.mock.calls[2][0]).toBe('/api/notifications/read'); expect(fetcher.mock.calls[2][1].headers.get('X-CSRF-Token')).toBe('csrf'); expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ all: true });
});
it('distinguishes actual execution outcomes from model advice', () => {
  expect(executionLabel('FILLED')).toBe('PAPER 已成交'); expect(executionLabel('BLOCKED')).toBe('PAPER 风控拦截'); expect(executionLabel('NOT_ENABLED')).toBe('自动下单未启用'); expect(executionLabel('NO_ACTION')).toBe('观望 · 无需下单'); expect(executionLabel('PAUSED')).toBe('已暂停');
});
