// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RuntimeApp } from '../src/runtime/App';
import { ApiError, RuntimeApi } from '../src/runtime/api';
import { ActionDialog, OrderDialog, RiskForm, StrategyDialog } from '../src/runtime/forms';
import type { Dashboard, Session } from '../src/runtime/types';

const session: Session = { username: 'owner', csrf_token: 'session-secret', environment: 'PAPER', expires_at: '2030-10-26T12:00:00Z' };
function dashboard(): Dashboard {
  const now = new Date().toISOString();
  return {
    as_of: now, environment: 'PAPER',
    account: { id: 'paper', name: 'Paper', currency: 'USDT', initial_cash: '100000', cash: '100000', equity: '100000', day_pnl: '0', day_pnl_pct: '0', exposure_pct: '0', risk_used: '0', risk_limit: '600', revision: 1, paused: false },
    history: [{ at: now, equity: '100000' }], positions: [], orders: [], decisions: [], strategies: [], events: [],
    risk: { max_position_pct: '20', max_daily_loss: '600', revision: 1 },
    services: [{ id: 'model', label: 'AI 模型', status: 'NOT_CONFIGURED', detail: '尚未配置', updated_at: now }],
    market: { source: 'TEST-ONLY', status: 'CONNECTED', last_updated: now, error: null, quotes: [{ symbol: 'BTCUSDT', price: '10000', change_pct: '0', as_of: now, source: 'TEST-ONLY', stale: false }] },
    research: { total_return_pct: '0', max_drawdown_pct: '0', win_rate: null, trade_count: 0, fees: '0' },
  };
}
beforeEach(() => {
  window.location.hash = '';
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('runtime session and empty states', () => {
  it('shows first-run setup and requests no account data before authentication', async () => {
    const api = new RuntimeApi();
    vi.spyOn(api, 'bootstrap').mockResolvedValue({ needs_setup: true, environment: 'PAPER', version: '1' });
    const account = vi.spyOn(api, 'dashboard');
    const user = userEvent.setup();
    const setup = vi.spyOn(api, 'setup').mockRejectedValue(new ApiError(409, 'SETUP_EXISTS', '账户已创建，请重新登录。'));
    render(<RuntimeApp api={api} />);
    await screen.findByRole('heading', { name: '创建你的工作空间' });
    expect(account).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText('用户名'), 'owner');
    await user.type(screen.getByLabelText(/密码/), 'correct-password');
    await user.click(screen.getByRole('button', { name: '创建并进入控制台' }));
    await screen.findByRole('alert');
    expect(setup).toHaveBeenCalledWith('owner', 'correct-password', '100000');
    expect((screen.getByLabelText('用户名') as HTMLInputElement).value).toBe('owner');
  });
  it('shows truthful empty account and all six navigation destinations', async () => {
    const api = new RuntimeApi();
    vi.spyOn(api, 'bootstrap').mockResolvedValue({ needs_setup: false, environment: 'PAPER', version: '1' });
    vi.spyOn(api, 'session').mockResolvedValue(session);
    vi.spyOn(api, 'dashboard').mockResolvedValue(dashboard());
    const user = userEvent.setup();
    render(<RuntimeApp api={api} />);
    await screen.findByRole('heading', { name: '运行总览' });
    expect(screen.getByText('等待权益记录积累')).toBeDefined();
    expect(screen.getByText('等待第一条决策')).toBeDefined();
    expect(screen.getByText('当前没有持仓')).toBeDefined();
    const navigation = screen.getByRole('navigation', { name: '主导航' });
    expect(within(navigation).getAllByRole('button')).toHaveLength(6);
    await user.click(within(navigation).getByRole('button', { name: '策略中心' }));
    await screen.findByRole('heading', { name: '尚未创建策略' });
    await user.click(within(navigation).getByRole('button', { name: '研究复盘' }));
    await screen.findByRole('heading', { name: '研究与复盘' });
    expect(screen.queryByText('+18.64%')).toBeNull();
  });
  it('recovers an expired session into login instead of showing invented data', async () => {
    const api = new RuntimeApi();
    vi.spyOn(api, 'bootstrap').mockResolvedValue({ needs_setup: false, environment: 'PAPER', version: '1' });
    vi.spyOn(api, 'session').mockRejectedValue(new ApiError(401, 'AUTH_REQUIRED', '请登录'));
    const account = vi.spyOn(api, 'dashboard');
    render(<RuntimeApp api={api} />);
    await screen.findByRole('heading', { name: '登录工作空间' });
    expect(account).not.toHaveBeenCalled();
  });
});
describe('reviewable mutation workflows', () => {
  it('retains a strategy draft on failure and saves only supported configuration', async () => {
    const api = new RuntimeApi();
    const save = vi.spyOn(api, 'saveStrategy').mockRejectedValue(new ApiError(409, 'REVISION_CONFLICT', '配置已变更，请重新读取。'));
    const user = userEvent.setup();
    render(<StrategyDialog api={api} mode="AI" onClose={vi.fn()} onSaved={vi.fn()} />);
    await user.type(screen.getByLabelText('策略名称'), '趋势观察');
    await user.click(screen.getByRole('button', { name: '保存策略' }));
    await screen.findByRole('alert');
    expect((screen.getByLabelText('策略名称') as HTMLInputElement).value).toBe('趋势观察');
    expect(save).toHaveBeenCalledWith({ name: '趋势观察', symbol: 'BTCUSDT', mode: 'AI', budget: '1000', target_pct: '10' }, undefined, 'DRAFT');
  });
  it('requires a separate order review and preserves the idempotency key on transport retry', async () => {
    const api = new RuntimeApi();
    const submit = vi.spyOn(api, 'order').mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', '连接中断，请重试。')).mockResolvedValueOnce({ order: { id: 'paper-order' }, account_revision: 2 });
    const saved = vi.fn(), user = userEvent.setup();
    render(<OrderDialog api={api} data={dashboard()} onClose={vi.fn()} onSaved={saved} />);
    await user.type(screen.getByLabelText(/交易数量/), '0.1');
    await user.click(screen.getByRole('button', { name: '核对订单' }));
    expect(submit).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认模拟成交' }));
    await screen.findByText('连接中断，请重试。');
    await user.click(screen.getByRole('button', { name: '确认模拟成交' }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(submit.mock.calls[0][1]).toBe(submit.mock.calls[1][1]);
    expect(submit.mock.calls[0][0]).toEqual({ symbol: 'BTCUSDT', side: 'BUY', quantity: '0.1', strategy_id: null });
  });
  it('blocks an order when quotes are stale', () => {
    const data = dashboard(); data.market.quotes[0].stale = true;
    const api = new RuntimeApi(), submit = vi.spyOn(api, 'order');
    render(<OrderDialog api={api} data={data} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect((screen.getByRole('button', { name: '核对订单' }) as HTMLButtonElement).disabled).toBe(true);
    expect(submit).not.toHaveBeenCalled();
  });
  it('requires the exact exit phrase and sends the reviewed revision', async () => {
    const api = new RuntimeApi(), saved = vi.fn();
    const action = vi.spyOn(api, 'action').mockResolvedValue({ id: 'receipt', action: 'exit_owned', status: 'APPLIED', affected_count: 0, detail: '无持仓可退出', created_at: '' });
    const user = userEvent.setup();
    render(<ActionDialog action="exit_owned" api={api} data={dashboard()} onClose={vi.fn()} onSaved={saved} />);
    const confirm = screen.getByRole('button', { name: '确认退出全部持仓' }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    await user.type(screen.getByLabelText(/确认操作/), 'EXIT PAPER');
    await user.click(confirm);
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(action.mock.calls[0]).toEqual(['exit_owned', 1, expect.any(String), 'EXIT PAPER']);
  });
  it('does not overwrite a risk policy changed while editing', async () => {
    const api = new RuntimeApi(), save = vi.spyOn(api, 'saveRisk'), user = userEvent.setup();
    const initial = dashboard().risk;
    const view = render(<RiskForm risk={initial} api={api} onSaved={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: '编辑风险阈值' }));
    fireEvent.change(screen.getByLabelText('单标的持仓上限'), { target: { value: '30' } });
    view.rerender(<RiskForm risk={{ ...initial, revision: 2, max_position_pct: '25' }} api={api} onSaved={vi.fn()} />);
    expect((screen.getByRole('button', { name: '保存风险阈值' }) as HTMLButtonElement).disabled).toBe(true);
    expect(save).not.toHaveBeenCalled();
    await screen.findByRole('alert');
  });
});
