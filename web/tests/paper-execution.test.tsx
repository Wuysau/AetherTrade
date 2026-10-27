// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PaperExecutionView } from '../src/runtime/paperexecution';
import { OrderDialog, StrategyDialog } from '../src/runtime/forms';
import { RuntimeApi } from '../src/runtime/api';
import type { Dashboard, Strategy } from '../src/runtime/types';

function snapshot(): Dashboard {
  return { as_of: new Date().toISOString(),
    account: { cash: '100000', paused: false }, positions: [], strategies: [],
    market: { status: 'CONNECTED', quotes: [{symbol:'BTCUSDT', price:'50000', stale:false, as_of:new Date().toISOString()}] },
    execution: {mode:'UNIFIED_PAPER', simulation_notice:'PAPER 合成事件，无法验证真实滑点和流动性',
      targets:[{symbol:'BTCUSDT', strategy_id:null, target_quantity:'1', in_flight_quantity:'0.6', actual_quantity:'0.4', protection_state:'GAP', protected_quantity:'0.4'}],
      commands:[{command_id:'original-client-command', symbol:'BTCUSDT', state:'UNKNOWN', order_status:'CANCEL_UNKNOWN', quantity:'1', filled_quantity:'0.4', leaves_quantity:'0.6'}],
      risk_locks:[{lock_id:'daily-loss', cause:'DAILY_LOSS', state:'ACTIVE', permission:'NO_NEW_RISK'}]},
  } as Dashboard;
}
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('keeps target, remaining order, actual fill, unverified protection and lock distinct', () => {
  render(<PaperExecutionView data={snapshot()} />);
  for (const label of ['目标数量','在途数量','实际成交持仓','保护已验证数量','风险锁']) expect(screen.getByText(label)).toBeDefined();
  expect(screen.getByText('未验证')).toBeDefined();
  expect(screen.getByText(/DAILY_LOSS · NO_NEW_RISK/)).toBeDefined();
  expect(screen.getByText('撤单结果未知')).toBeDefined();
  expect(screen.queryByText('已成交')).toBeNull();
});

it.each(['PENDING','SUBMITTED','UNKNOWN', undefined])('does not announce an accepted %s receipt as filled', async status => {
  const api = new RuntimeApi();
  vi.spyOn(api, 'order').mockResolvedValue({order: {id:'original-client-command', status}, account_revision:2});
  const saved = vi.fn();
  render(<OrderDialog data={snapshot()} api={api} onClose={vi.fn()} onSaved={saved} />);
  fireEvent.change(screen.getByLabelText(/交易数量/), {target:{value:'0.1'}});
  fireEvent.submit(screen.getByRole('button', {name:'核对订单'}).closest('form')!);
  fireEvent.submit(screen.getByRole('button', {name:/确认模拟/}).closest('form')!);
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(saved.mock.calls[0][0]).not.toContain('已成交');
});

it('can explicitly activate a bounded RULE without acknowledging a model call', async () => {
  const api = new RuntimeApi();
  const save = vi.spyOn(api, 'saveStrategy').mockResolvedValue({} as Strategy);
  render(<StrategyDialog mode="RULE" api={api} onClose={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('策略名称'), {target:{value:'规则观察'}});
  fireEvent.change(screen.getByLabelText('规则触发价格'), {target:{value:'49000'}});
  fireEvent.change(screen.getByLabelText('配置状态'), {target:{value:'ACTIVE'}});
  fireEvent.click(screen.getByRole('button', {name:'保存策略'}));
  await waitFor(() => expect(save).toHaveBeenCalled());
  expect(save).toHaveBeenCalledWith(expect.objectContaining({mode:'RULE',
    acknowledge_model_cost:false, acknowledge_paper_automation:true,
    rule:{kind:'PRICE_THRESHOLD',operator:'LTE',price:'49000',stop_loss_pct:'5'}}), undefined, 'ACTIVE');
});

it('requires a configured rule before activating HYBRID', async () => {
  const api = new RuntimeApi(), save = vi.spyOn(api, 'saveStrategy');
  render(<StrategyDialog mode="HYBRID" api={api} onClose={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('策略名称'), {target:{value:'混合观察'}});
  fireEvent.change(screen.getByLabelText('配置状态'), {target:{value:'ACTIVE'}});
  fireEvent.click(screen.getByRole('button', {name:'保存策略'}));
  await screen.findByRole('alert');
  expect(save).not.toHaveBeenCalled();
});
