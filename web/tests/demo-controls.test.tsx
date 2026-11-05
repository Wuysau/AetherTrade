// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RuntimeApi } from '../src/runtime/api';
import { DemoRuntimeControls } from '../src/runtime/demo_controls';
import { LocaleProvider } from '../src/runtime/i18n';

beforeEach(() => window.localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('requires separate acknowledgements before saving and starting a real Demo AI window', async () => {
  const calls: { path: string; body?: unknown }[] = [];
  let saved = false;
  const api = new RuntimeApi(async (path, options) => {
    const name = String(path); calls.push({ path: name, body: options?.body ? JSON.parse(String(options.body)) : undefined });
    if (name.endsWith('/model-configs')) return new Response(JSON.stringify({ profiles: [{ id:'model-a', name:'Model A', revision:4, enabled:true, key_available:true, local_mode:false }], default_profile_id:'model-a' }));
    if (name.endsWith('/runtime/status')) return new Response(JSON.stringify({ configured:true, execution_authorized:false, account_state:'READ_ONLY', window:{state:'NOT_STARTED',error:null}, saved_strategy:saved ? { revision:1, grant_hash:'f'.repeat(64), mode:'AI', model_profile_id:'model-a', model_revision:4, budget:'100', max_planned_loss:'2', max_daily_loss:'5', fee_rate_cap:'0.001', holding_period_seconds:300, authorization_seconds:300, authorized_until:2000000000, authorized:true, allow_execution:true, allow_model_calls:true } : null }));
    if (name.endsWith('/strategy')) saved = true;
    return new Response(JSON.stringify(name.endsWith('/strategy') ? {revision:1, execution_authorized:false} : {state:'START_REQUESTED'}));
  });
  render(<LocaleProvider><DemoRuntimeControls api={api} /></LocaleProvider>);
  await screen.findByText('Model A · v4');
  expect(calls.some(call => call.path.endsWith('/start'))).toBe(false);
  expect((screen.getByRole('button',{name:'保存 AI 策略授权'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('配置确认模型调用可能产生费用'));
  fireEvent.click(screen.getByLabelText('配置确认允许真实 Demo 模拟订单'));
  fireEvent.click(screen.getByRole('button',{name:'保存 AI 策略授权'}));
  await waitFor(() => expect(calls.some(call => call.path.endsWith('/strategy'))).toBe(true));
  const config = calls.find(call => call.path.endsWith('/strategy'))?.body as Record<string, unknown>;
  expect(config.model_profile_id).toBe('model-a'); expect(config.model_revision).toBe(4);
  expect(config.acknowledge_model_cost).toBe(true); expect(config.acknowledge_demo_execution).toBe(true);
  await screen.findByText(/已保存策略/);
  expect(screen.getByText(/已保存策略/).textContent).toContain('Model A · v4');
  expect((screen.getByRole('button',{name:'启动 AI 模拟交易'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByLabelText('启动确认模型调用可能产生费用'));
  fireEvent.click(screen.getByLabelText('启动确认允许真实 Demo 模拟订单'));
  fireEvent.change(screen.getByLabelText('策略预算上限（USDT）'), {target:{value:'101'}});
  expect((screen.getByRole('button',{name:'启动 AI 模拟交易'}) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('策略预算上限（USDT）'), {target:{value:'100'}});
  fireEvent.click(screen.getByRole('button',{name:'启动 AI 模拟交易'}));
  await waitFor(() => expect(calls.some(call => call.path.endsWith('/start'))).toBe(true));
  expect(calls.find(call => call.path.endsWith('/start'))?.body).toMatchObject({acknowledge_model_cost:true, acknowledge_demo_orders:true, expected_strategy_revision:1, expected_grant_hash:'f'.repeat(64)});
  expect(screen.getByText(/停止不会取消交易所挂单/)).toBeDefined();
});

it('shows the runtime gate rather than presenting an unconfigured runtime as ready', async () => {
  const api = new RuntimeApi(async path => String(path).endsWith('/runtime/status')
    ? new Response(JSON.stringify({error:{code:'DEMO_RUNTIME_NOT_CONFIGURED',message:'Demo 运行时未配置。'}}), {status:503})
    : new Response(JSON.stringify({profiles:[],default_profile_id:'default'})));
  render(<LocaleProvider><DemoRuntimeControls api={api} /></LocaleProvider>);
  await screen.findByText('Demo 运行时未配置。');
  expect((screen.getByRole('button',{name:'启动 AI 模拟交易'}) as HTMLButtonElement).disabled).toBe(true);
});

it('shows durable records without treating unknown orders as confirmed or inventing P&L', async () => {
  const api = new RuntimeApi(async path => {
    const name = String(path);
    if (name.endsWith('/model-configs')) return new Response(JSON.stringify({profiles:[]}));
    if (name.endsWith('/runtime/status')) return new Response(JSON.stringify({configured:true,execution_authorized:false,window:{state:'STOPPED_REVIEW_REQUIRED',error:null}}));
    if (name.endsWith('/runtime/observations')) return new Response(JSON.stringify({decisions:[{run_id:'r1',state:'ASSESSED',action:'HOLD',summary:'No action'}],orders:[{command_id:'c1',state:'UNKNOWN',confirmation:'OUTCOME_UNKNOWN',order_id:null,order_status:null}],reports:[],fills:[],pnl:{state:'UNAVAILABLE',reason:'DEMO_PNL_EVIDENCE_INCOMPLETE'},complete_history:false}));
    throw new Error('unexpected request');
  });
  render(<LocaleProvider><DemoRuntimeControls api={api} /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button',{name:'读取记录'}));
  await screen.findByText(/OUTCOME_UNKNOWN/);
  expect(screen.getByText(/UNAVAILABLE.*DEMO_PNL_EVIDENCE_INCOMPLETE/)).toBeDefined();
  expect(screen.getByText(/可能不完整或过时/)).toBeDefined();
});

it('shows unresolved account history as requiring review, not a strategy fill', async () => {
  const api = new RuntimeApi(async path => {
    const name = String(path);
    if (name.endsWith('/model-configs')) return new Response(JSON.stringify({profiles:[]}));
    if (name.endsWith('/runtime/status')) return new Response(JSON.stringify({configured:true,execution_authorized:false}));
    if (name.endsWith('/runtime/observations')) return new Response(JSON.stringify({decisions:[],orders:[],reports:[],fills:[],
      unresolved_trade_history:[{symbol:'BTCUSDT',trade_id:'12',order_id:'345',trade_hash:'abc',recorded_at:100}],
      pnl:{state:'UNAVAILABLE',reason:'DEMO_PNL_EVIDENCE_INCOMPLETE'},complete_history:false}));
    throw new Error('unexpected request');
  });
  render(<LocaleProvider><DemoRuntimeControls api={api} /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button',{name:'读取记录'}));
  await screen.findByText(/未归属成交记录需要核对/);
  expect(screen.getByText(/BTCUSDT.*345.*12/)).toBeDefined();
});

it('shows account balance frames still blocking recovery', async () => {
  const api = new RuntimeApi(async path => {
    const name = String(path);
    if (name.endsWith('/model-configs')) return new Response(JSON.stringify({profiles:[]}));
    if (name.endsWith('/runtime/status')) return new Response(JSON.stringify({configured:true,execution_authorized:false}));
    if (name.endsWith('/runtime/observations')) return new Response(JSON.stringify({decisions:[],orders:[],reports:[],fills:[],
      account_position_review:{state:'PENDING',count:2,execution_authorized:false,blocker:'DEMO_ACCOUNT_STREAM_SNAPSHOT_ORDERING_UNQUALIFIED'},
      pnl:{state:'UNAVAILABLE',reason:'DEMO_PNL_EVIDENCE_INCOMPLETE'},complete_history:false}));
    throw new Error('unexpected request');
  });
  render(<LocaleProvider><DemoRuntimeControls api={api} /></LocaleProvider>);
  fireEvent.click(screen.getByRole('button',{name:'读取记录'}));
  await screen.findByText(/2 条余额事件仍待核对/);
  expect(screen.getByText(/DEMO_ACCOUNT_STREAM_SNAPSHOT_ORDERING_UNQUALIFIED/)).toBeDefined();
});
