// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiError, RuntimeApi } from '../src/runtime/api';
import { ConfigForm, ManualAnalysis } from '../src/runtime/modelsettings';
import type { Dashboard, ModelConfig } from '../src/runtime/types';

const config: ModelConfig = { protocol: 'openai_responses', base_url: 'https://api.openai.com/v1', model: 'test-model', enabled: true, local_mode: false, max_output_tokens: 1024, timeout_seconds: 120, anthropic_version: '2023-06-01', revision: 1, has_api_key: true, key_available: true, connection_status: 'CONFIGURED', last_test_at: null, last_error: null };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('retains saved keys when blank and clears a newly entered key after saving', async () => {
  const api = new RuntimeApi(); const save = vi.spyOn(api, 'saveModelConfig').mockResolvedValue({ ...config, revision: 2 });
  const storage = vi.spyOn(Storage.prototype, 'setItem');
  render(<ConfigForm config={config} api={api} onConfig={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save.mock.calls[0][0]).not.toHaveProperty('api_key');
  await waitFor(() => expect((screen.getByRole('button', { name: '保存配置' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('API 密钥'), { target: { value: 'test-secret-only' } });
  fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
  await waitFor(() => expect((screen.getByLabelText('API 密钥') as HTMLInputElement).value).toBe(''));
  expect(save.mock.calls[1][0].api_key).toBe('test-secret-only'); expect(storage).not.toHaveBeenCalled();
});

it('keeps dirty inputs through polling and CAS rejection until explicit reload', async () => {
  const api = new RuntimeApi(); vi.spyOn(api, 'saveModelConfig').mockRejectedValue(new ApiError(409, 'REVISION_CONFLICT', '配置版本冲突'));
  const reload = vi.spyOn(api, 'modelConfig').mockResolvedValue({ ...config, model: 'server-model', revision: 3 });
  const props = { api, onConfig: vi.fn(), onSaved: vi.fn() };
  const view = render(<ConfigForm {...props} config={config} />);
  fireEvent.change(screen.getByLabelText('模型名称'), { target: { value: 'my-draft' } });
  fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
  await screen.findByText('配置版本冲突');
  view.rerender(<ConfigForm {...props} config={{ ...config, revision: 2, model: 'polled-model' }} />);
  expect((screen.getByLabelText('模型名称') as HTMLInputElement).value).toBe('my-draft');
  fireEvent.click(screen.getByRole('button', { name: '丢弃编辑并重新载入' }));
  await waitFor(() => expect((screen.getByLabelText('模型名称') as HTMLInputElement).value).toBe('server-model'));
  expect(reload).toHaveBeenCalledTimes(1);
});

it('requires credential re-entry for protocol changes and separates save from directory test', async () => {
  const api = new RuntimeApi(); const save = vi.spyOn(api, 'saveModelConfig').mockResolvedValue({ ...config, protocol: 'anthropic', revision: 2 });
  const test = vi.spyOn(api, 'testModelConfig').mockResolvedValue({ config: { ...config, connection_status: 'CONNECTED' }, models: ['test-model'], message: '目录验证完成' });
  render(<ConfigForm config={config} api={api} onConfig={vi.fn()} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '测试连接' })); await screen.findByText('目录验证完成'); expect(test).toHaveBeenCalledWith(1); expect(save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('API 协议'), { target: { value: 'anthropic' } });
  fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
  await screen.findByText('更换地址或协议后，请重新输入密钥，或明确清除已保存的密钥。'); expect(save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('API 密钥'), { target: { value: 'test-key' } });
  fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ protocol: 'anthropic', api_key: 'test-key', anthropic_version: '2023-06-01' })));
});

it('requires symbol and cost acknowledgement and preserves the request key on failure', async () => {
  const api = new RuntimeApi(); const generate = vi.spyOn(api, 'generateDecision').mockRejectedValue(new ApiError(503, 'FAILED', '服务不可用'));
  const data = { instruments: undefined, market: { status: 'PARTIAL', quotes: [{ symbol: 'BTCUSDT', price: '100', as_of: new Date().toISOString(), stale: false }] } } as Dashboard;
  render(<ManualAnalysis config={config} data={data} api={api} onSaved={vi.fn()} />);
  expect((screen.getByRole('button', { name: '生成一次分析' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText('分析标的'), { target: { value: 'BTCUSDT' } });
  expect((screen.getByRole('button', { name: '生成一次分析' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '生成一次分析' }));
  await screen.findByText(/服务不可用/); fireEvent.click(screen.getByRole('button', { name: '查询 \/ 重试同一请求' }));
  await waitFor(() => expect(generate).toHaveBeenCalledTimes(2));
  expect(generate.mock.calls[1]).toEqual(generate.mock.calls[0]);
  fireEvent.change(screen.getByLabelText('分析标的'), { target: { value: 'TSLABUSDT' } }); fireEvent.click(screen.getByRole('checkbox'));
  expect((screen.getByRole('button', { name: '生成一次分析' }) as HTMLButtonElement).disabled).toBe(true);
});

it('uses exact model API contracts, CSRF and a 150 second inference deadline', async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}'))); const api = new RuntimeApi(fetcher);
  api.setSession({ username: 'test', csrf_token: 'csrf', expires_at: '', environment: 'PAPER' });
  const timeout = vi.spyOn(AbortSignal, 'timeout');
  await api.generateDecision('NVDABUSDT', 7, 'stable-key');
  const [path, init] = fetcher.mock.calls[0]; expect(path).toBe('/api/decisions/generate');
  expect(JSON.parse(init.body)).toEqual({ symbol: 'NVDABUSDT', expected_revision: 7, acknowledge_cost: true });
  expect(init.headers.get('Idempotency-Key')).toBe('stable-key'); expect(init.headers.get('X-CSRF-Token')).toBe('csrf'); expect(timeout).toHaveBeenCalledWith(150_000);
  await api.testModelConfig(7); expect(fetcher.mock.calls[1][0]).toBe('/api/model-config/test'); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_revision: 7 });
});

it('switches known official URL defaults but preserves a custom gateway', () => {
  render(<ConfigForm config={config} api={new RuntimeApi()} onConfig={vi.fn()} onSaved={vi.fn()} />);
  const url = screen.getByLabelText('API 基础地址') as HTMLInputElement;
  fireEvent.change(screen.getByLabelText('API 协议'), { target: { value: 'anthropic' } });
  expect(url.value).toBe('https://api.anthropic.com/v1');
  fireEvent.change(screen.getByLabelText('API 协议'), { target: { value: 'openai_chat' } });
  expect(url.value).toBe('https://api.openai.com/v1');
  fireEvent.change(url, { target: { value: 'https://gateway.example/v1' } });
  fireEvent.change(screen.getByLabelText('API 协议'), { target: { value: 'anthropic' } });
  expect(url.value).toBe('https://gateway.example/v1');
});

it('creates a new analysis attempt only after explicit reset and renewed cost acknowledgement', async () => {
  const api = new RuntimeApi(); const generate = vi.spyOn(api, 'generateDecision').mockRejectedValue(new ApiError(503, 'FAILED', '请求结果未知'));
  const data = { market: { status: 'CONNECTED', quotes: [{ symbol: 'BTCUSDT', price: '100', as_of: new Date().toISOString(), stale: false }] } } as Dashboard;
  const props = { data, api, onSaved: vi.fn() };
  const view = render(<ManualAnalysis {...props} config={config} />);
  fireEvent.change(screen.getByLabelText('分析标的'), { target: { value: 'BTCUSDT' } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '生成一次分析' }));
  await screen.findByRole('button', { name: '新建分析请求' });
  view.rerender(<ManualAnalysis {...props} config={{ ...config, revision: 2 }} />);
  expect(generate).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '新建分析请求' }));
  expect(generate).toHaveBeenCalledTimes(1);
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('button', { name: '生成一次分析' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '生成一次分析' }));
  await waitFor(() => expect(generate).toHaveBeenCalledTimes(2));
  expect(generate.mock.calls[1][0]).toBe('BTCUSDT'); expect(generate.mock.calls[1][1]).toBe(2);
  expect(generate.mock.calls[1][2]).not.toBe(generate.mock.calls[0][2]);
});

it('manual one-off analysis never activates a strategy or submits a PAPER order', async () => {
  const api = new RuntimeApi(); const generate = vi.spyOn(api, 'generateDecision').mockResolvedValue({ decision: { id: 'manual-only' } as never, usage: { input_tokens: 1, output_tokens: 1 } });
  const order = vi.spyOn(api, 'order'); const strategy = vi.spyOn(api, 'saveStrategy');
  const data = { market: { status: 'CONNECTED', quotes: [{ symbol: 'BTCUSDT', price: '100', as_of: new Date().toISOString(), stale: false }] } } as Dashboard;
  const saved = vi.fn(); render(<ManualAnalysis config={config} data={data} api={api} onSaved={saved} />);
  fireEvent.change(screen.getByLabelText('分析标的'), { target: { value: 'BTCUSDT' } }); fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: '生成一次分析' }));
  await waitFor(() => expect(saved).toHaveBeenCalledWith(expect.stringContaining('未执行订单'))); expect(generate).toHaveBeenCalledTimes(1); expect(order).not.toHaveBeenCalled(); expect(strategy).not.toHaveBeenCalled();
});

it('persists an unset application output limit as null',async()=>{
 const api=new RuntimeApi();const save=vi.spyOn(api,'saveModelConfig').mockResolvedValue({...config,max_output_tokens:null,revision:2});
 render(<ConfigForm config={config} api={api} onConfig={vi.fn()} onSaved={vi.fn()}/>);
 fireEvent.click(screen.getByText('高级设置'));
 fireEvent.change(screen.getByLabelText('最大输出 token'),{target:{value:''}});
 fireEvent.click(screen.getByRole('button',{name:'保存配置'}));
 await waitFor(()=>expect(save).toHaveBeenCalledTimes(1));
 expect(save.mock.calls[0][0].max_output_tokens).toBeNull();
});
