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

it('renders saved RULE decisions without model-only counter evidence', async () => {
  const data = dashboard();
  data.decisions = [{id:'rule:saved', symbol:'BTCUSDT', action:'BUY', summary:'价格规则计算',
    created_at:data.as_of, risk_status:'PAPER_REJECTED', execution_status:'BLOCKED',
    execution_reason:'超过仓位限制'}] as Dashboard['decisions'];
  window.location.hash = '#decisions';
  const api = new RuntimeApi();
  vi.spyOn(api, 'bootstrap').mockResolvedValue({needs_setup:false, environment:'PAPER', version:'fixture'});
  vi.spyOn(api, 'session').mockResolvedValue(session);
  vi.spyOn(api, 'dashboard').mockResolvedValue(data);
  render(<RuntimeApp api={api} />);
  await screen.findByRole('heading', {name:'AI 决策中心'});
  expect(screen.getByText('该记录未提供反对证据。')).toBeDefined();
  expect(screen.getByRole('heading', {name:'规则建议'})).toBeDefined();
  expect(screen.getByText('超过仓位限制')).toBeDefined();
});

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
    expect(save).toHaveBeenCalledWith({ name: '趋势观察', symbol: 'BTCUSDT', mode: 'AI', budget: '1000', target_pct: '10', analysis_interval_seconds: 300, auto_execute: false, acknowledge_model_cost: false }, undefined, 'DRAFT');
  });
  it('requires a separate order review and preserves the idempotency key on transport retry', async () => {
    const api = new RuntimeApi();
    const submit = vi.spyOn(api, 'order').mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', '连接中断，请重试。')).mockResolvedValueOnce({ order: { id: 'paper-order' }, account_revision: 2 });
    const saved = vi.fn(), user = userEvent.setup();
    render(<OrderDialog api={api} data={dashboard()} onClose={vi.fn()} onSaved={saved} />);
    await user.type(screen.getByLabelText(/交易数量/), '0.1');
    await user.click(screen.getByRole('button', { name: '核对订单' }));
    expect(submit).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '确认模拟委托' }));
    await screen.findByText('连接中断，请重试。');
    await user.click(screen.getByRole('button', { name: '确认模拟委托' }));
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
  it('clears a selected strategy when changing symbols and submits the reviewed manual order', async () => {
    const data = dashboard();
    data.strategies = [{ id: 'btc-strategy', name: 'BTC 观察', symbol: 'BTCUSDT', mode: 'AI', budget: '10000', target_pct: '10', status: 'DRAFT', revision: 1, created_at: data.as_of, updated_at: data.as_of }];
    data.market.quotes.push({ ...data.market.quotes[0], symbol: 'ETHUSDT', price: '2000' });
    const api = new RuntimeApi(), saved = vi.fn(), user = userEvent.setup();
    const submit = vi.spyOn(api, 'order').mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', '连接中断，请重试。')).mockResolvedValueOnce({ order: { id: 'eth-order' }, account_revision: 2 });
    render(<OrderDialog api={api} data={data} onClose={vi.fn()} onSaved={saved} />);
    await user.selectOptions(screen.getByLabelText(/归属策略/), 'btc-strategy');
    await user.selectOptions(screen.getByLabelText('交易标的'), 'ETHUSDT');
    expect((screen.getByLabelText(/归属策略/) as HTMLSelectElement).value).toBe('');
    await user.type(screen.getByLabelText(/交易数量/), '1');
    await user.click(screen.getByRole('button', { name: '核对订单' }));
    expect(submit).not.toHaveBeenCalled();
    await screen.findByRole('heading', { name: '确认模拟订单' });
    await user.click(screen.getByRole('button', { name: '确认模拟委托' }));
    await screen.findByText('连接中断，请重试。');
    await user.click(screen.getByRole('button', { name: '确认模拟委托' }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(submit.mock.calls[0][0]).toEqual({ symbol: 'ETHUSDT', side: 'BUY', quantity: '1', strategy_id: null });
    expect(submit.mock.calls[1]).toEqual(submit.mock.calls[0]);
  });
  it.each(['incompatible', 'missing'] as const)('requires an explicit strategy choice for an %s initial binding', async kind => {
    const data = dashboard();
    if (kind === 'incompatible') data.strategies = [{ id: 'unavailable-strategy', name: 'ETH 观察', symbol: 'ETHUSDT', mode: 'AI', budget: '10000', target_pct: '10', status: 'DRAFT', revision: 1, created_at: data.as_of, updated_at: data.as_of }];
    const api = new RuntimeApi(), saved = vi.fn(), user = userEvent.setup();
    const submit = vi.spyOn(api, 'order').mockResolvedValue({ order: { id: 'manual-order' }, account_revision: 2 });
    render(<OrderDialog api={api} data={data} initial={{ strategy_id: 'unavailable-strategy', quantity: '0.1' }} onClose={vi.fn()} onSaved={saved} />);
    expect(within(screen.getByLabelText(/归属策略/)).getByRole('option', { selected: true }).textContent).toBe('原策略不可用，请重新选择');
    await user.click(screen.getByRole('button', { name: '核对订单' }));
    await screen.findByText('原归属策略已不可用或与交易标的不符，请重新选择归属策略。');
    expect(submit).not.toHaveBeenCalled();
    await user.selectOptions(screen.getByLabelText(/归属策略/), '');
    await user.click(screen.getByRole('button', { name: '核对订单' }));
    await user.click(screen.getByRole('button', { name: '确认模拟委托' }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(submit.mock.calls[0][0].strategy_id).toBeNull();
  });
  it('blocks confirmation if the reviewed strategy becomes unavailable without changing the retry payload', async () => {
    const data = dashboard();
    data.strategies = [{ id: 'btc-strategy', name: 'BTC 观察', symbol: 'BTCUSDT', mode: 'AI', budget: '10000', target_pct: '10', status: 'DRAFT', revision: 1, created_at: data.as_of, updated_at: data.as_of }];
    const api = new RuntimeApi(), saved = vi.fn(), user = userEvent.setup();
    const submit = vi.spyOn(api, 'order').mockRejectedValueOnce(new ApiError(0, 'NETWORK_ERROR', '连接中断，请重试。')).mockResolvedValueOnce({ order: { id: 'strategy-order' }, account_revision: 2 });
    const view = render(<OrderDialog api={api} data={data} initial={{ strategy_id: 'btc-strategy', quantity: '0.1' }} onClose={vi.fn()} onSaved={saved} />);
    await user.click(screen.getByRole('button', { name: '核对订单' }));
    await user.click(screen.getByRole('button', { name: '确认模拟委托' }));
    await screen.findByText('连接中断，请重试。');
    view.rerender(<OrderDialog api={api} data={{ ...data, strategies: [] }} onClose={vi.fn()} onSaved={saved} />);
    await user.click(screen.getByRole('button', { name: '确认模拟委托' }));
    await screen.findByText('原归属策略已不可用或与交易标的不符，请重新选择归属策略。');
    expect(submit).toHaveBeenCalledTimes(1);
    view.rerender(<OrderDialog api={api} data={data} onClose={vi.fn()} onSaved={saved} />);
    await user.click(screen.getByRole('button', { name: '确认模拟委托' }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(submit.mock.calls[1]).toEqual(submit.mock.calls[0]);
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

it('keeps bell unread state until read and opens the associated decision', async () => {
  const api = new RuntimeApi(); const data = dashboard();
  data.decisions = [{ id: 'decision-notified', symbol: 'BTCUSDT', action: 'HOLD', source: 'test', summary: '真实建议记录', counter_evidence: [], invalidation: '', risk_status: 'NOT_EVALUATED', created_at: data.as_of, next_review_at: null, execution_status: 'NO_ACTION' }];
  data.notifications = { unread_count: 1, items: [{ id: 'n1', kind: 'AI_RESULT', title: '分析完成', message: '观望，无需下单', severity: 'info', created_at: data.as_of, read_at: null, strategy_id: 's1', decision_id: 'decision-notified', order_id: null }] };
  vi.spyOn(api, 'bootstrap').mockResolvedValue({ needs_setup: false, environment: 'PAPER', version: '1' }); vi.spyOn(api, 'session').mockResolvedValue(session); vi.spyOn(api, 'dashboard').mockResolvedValue(data); vi.spyOn(api, 'modelConfig').mockRejectedValue(new Error('未配置'));
  const read = vi.spyOn(api, 'markNotificationsRead').mockResolvedValue({ unread_count: 0, items: [{ ...data.notifications.items[0], read_at: data.as_of }] });
  render(<RuntimeApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看通知，1 条未读' }));
  expect(read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '查看关联决策' }));
  await waitFor(() => expect(read).toHaveBeenCalledWith({ ids: ['n1'] }));
  await screen.findByText('decision-notified'); expect(screen.getByText('观望 · 无需下单')).toBeTruthy();
  fireEvent.click(await screen.findByRole('button', { name: '查看通知，0 条未读' }));
  expect((screen.getByRole('button', { name: '全部标为已读' }) as HTMLButtonElement).disabled).toBe(true);
});
it('reopening the same notification reselects its decision while dashboard refresh preserves manual selection', async () => {
  const api = new RuntimeApi(); const data = dashboard();
  const first = { id: 'decision-a', symbol: 'BTCUSDT', action: 'HOLD', source: 'test', summary: '记录 A', counter_evidence: [], invalidation: '', risk_status: 'NOT_EVALUATED', created_at: data.as_of, next_review_at: null };
  data.decisions = [first, { ...first, id: 'decision-b', symbol: 'ETHUSDT', summary: '记录 B' }];
  data.model_config = { protocol: 'openai_responses', base_url: 'https://api.openai.com/v1', model: 'saved-model', enabled: false, local_mode: false, max_output_tokens: 1024, timeout_seconds: 120, anthropic_version: '2023-06-01', revision: 1, has_api_key: false, key_available: false, connection_status: 'CONFIGURED', last_test_at: null, last_error: null };
  data.notifications = { unread_count: 1, items: [{ id: 'n-a', kind: 'AI_RESULT', title: '分析完成', message: '记录 A 已生成', severity: 'info', created_at: data.as_of, read_at: null, strategy_id: 's1', decision_id: 'decision-a', order_id: null }] };
  vi.spyOn(api, 'bootstrap').mockResolvedValue({ needs_setup: false, environment: 'PAPER', version: '1' }); vi.spyOn(api, 'session').mockResolvedValue(session);
  const sync = vi.spyOn(api, 'dashboard').mockResolvedValue(data);
  const readNotifications = { unread_count: 0, items: [{ ...data.notifications.items[0], read_at: data.as_of }] };
  vi.spyOn(api, 'markNotificationsRead').mockResolvedValue(readNotifications);
  render(<RuntimeApp api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: '查看通知，1 条未读' })); fireEvent.click(screen.getByRole('button', { name: '查看关联决策' }));
  await screen.findByText('decision-a');
  fireEvent.click(screen.getByRole('button', { name: /ETHUSDT.*记录 B/ })); await screen.findByText('decision-b');
  fireEvent.click(screen.getByRole('button', { name: '模型配置' })); fireEvent.change(screen.getByLabelText('模型名称'), { target: { value: 'unsaved-model' } });
  sync.mockResolvedValue({ ...data, as_of: new Date(Date.now() + 1000).toISOString(), notifications: readNotifications });
  fireEvent.focus(window); await waitFor(() => expect(sync).toHaveBeenCalledTimes(2));
  expect(screen.getByText('decision-b')).toBeTruthy(); expect((screen.getByLabelText('模型名称') as HTMLInputElement).value).toBe('unsaved-model');
  fireEvent.click(await screen.findByRole('button', { name: '查看通知，0 条未读' })); fireEvent.click(screen.getByRole('button', { name: '查看关联决策' }));
  await screen.findByText('decision-a'); expect(screen.queryByText('decision-b')).toBeNull();
  expect((screen.getByLabelText('模型名称') as HTMLInputElement).value).toBe('unsaved-model');
});
