// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ApiError, RuntimeApi } from '../src/runtime/api';
import { ModelSettings, ManualAnalysis } from '../src/runtime/modelsettings';
import { ModelOutcomes, ModelSelection, profilesFromDashboard, routingError } from '../src/runtime/modelprofiles';
import { StrategyDialog } from '../src/runtime/forms';
import type { Dashboard, Decision, ModelProfile, Strategy } from '../src/runtime/types';

const profile = (id: string, name = id): ModelProfile => ({ id, name, protocol: 'openai_responses', base_url: 'https://api.openai.com/v1', model: `model-${id}`, enabled: true, local_mode: false, max_output_tokens: 1024, timeout_seconds: 120, anthropic_version: '2023-06-01', revision: 1, has_api_key: true, key_available: true, connection_status: 'CONFIGURED', last_test_at: null, last_error: null });
const profiles = [profile('default', '默认模型'), profile('b', '复核模型'), profile('c', '第三模型'), profile('d', '第四模型')];
const dashboard = (): Dashboard => ({ model_profiles: profiles, model_config: profiles[0], strategies: [], services: [], market: { status: 'CONNECTED', quotes: [{ symbol: 'BTCUSDT', price: '100', as_of: new Date().toISOString(), stale: false }] } } as Dashboard);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('uses profile CRUD and analysis contracts with CSRF, CAS and stable request keys', async () => {
  const transport = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}')));
  const api = new RuntimeApi(transport); api.setSession({ username: 'test', csrf_token: 'csrf', expires_at: '', environment: 'PAPER' });
  await api.modelConfigs(); await api.modelProfile('b/c'); await api.createModelProfile({ ...profiles[1], name: 'new' });
  await api.saveModelProfile('b/c', { ...profiles[1], name: 'renamed', expected_revision: 1 });
  await api.deleteModelProfile('b/c', 2); await api.testModelProfile('b/c', 3);
  const values = { symbol: 'BTCUSDT' as const, analysis_mode: 'consensus' as const, model_profile_ids: ['default', 'b'], expected_revisions: { default: 1, b: 1 }, acknowledge_cost: true as const };
  await api.analyzeDecision(values, 'stable-key');
  expect(transport.mock.calls.map(c => c[0])).toEqual(['/api/model-configs', '/api/model-configs/b%2Fc', '/api/model-configs', '/api/model-configs/b%2Fc', '/api/model-configs/b%2Fc', '/api/model-configs/b%2Fc/test', '/api/decisions/analyze']);
  expect(JSON.parse(transport.mock.calls[4][1].body)).toEqual({ expected_revision: 2 });
  expect(JSON.parse(transport.mock.calls[6][1].body)).toEqual(values);
  expect(transport.mock.calls[6][1].headers.get('Idempotency-Key')).toBe('stable-key');
  expect(transport.mock.calls[6][1].headers.get('X-CSRF-Token')).toBe('csrf');
});

it('migrates only legacy snapshots to default and never replaces explicit missing choices', () => {
  expect(profilesFromDashboard({ model_config: profiles[1] }).map(p => p.id)).toEqual(['default']);
  expect(profilesFromDashboard({ model_profiles: [], model_config: profiles[0] })).toEqual([]);
  expect(routingError('single', ['deleted'], profiles)).toContain('不可用');
  expect(routingError('consensus', ['b', 'b'], profiles)).toContain('不同');
  expect(routingError('consensus', ['default'], profiles)).toContain('2');
});

it('discloses call count and restricts cross-analysis to three distinct profiles', () => {
  const change = vi.fn(); render(<ModelSelection profiles={profiles} mode="consensus" ids={['default', 'b', 'c']} onChange={change} />);
  expect(screen.getByText(/每次分析调用 3 个模型/)).toBeTruthy();
  expect((screen.getByRole('checkbox', { name: /第四模型/ }) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: /复核模型/ }));
  expect(change).toHaveBeenCalledWith('consensus', ['default', 'c']);
});

const activeEditor = () => within(screen.getByRole('textbox', { name: '配置名称' }).closest('form')!);

it('retains independent dirty drafts and credentials while saving another profile and polling', async () => {
  const api = new RuntimeApi(); const data = { ...dashboard(), model_profiles: profiles.slice(0, 2) };
  const save = vi.spyOn(api, 'saveModelProfile').mockResolvedValue({ ...profiles[1], model: 'b-edited', revision: 2 });
  const props = { api, onSaved: vi.fn() }; const view = render(<ModelSettings {...props} data={data} />);
  fireEvent.click(screen.getByRole('button', { name: '模型配置' }));
  fireEvent.change(activeEditor().getByLabelText('模型名称'), { target: { value: 'a-draft' } });
  fireEvent.change(activeEditor().getByLabelText('API 密钥'), { target: { value: 'a-secret-draft' } });
  fireEvent.click(screen.getByRole('button', { name: '编辑 复核模型' }));
  expect((activeEditor().getByLabelText('API 密钥') as HTMLInputElement).value).toBe('');
  fireEvent.change(activeEditor().getByLabelText('模型名称'), { target: { value: 'b-edited' } });
  fireEvent.click(activeEditor().getByRole('button', { name: '保存配置' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith('b', expect.objectContaining({ model: 'b-edited', expected_revision: 1 })));
  expect(save.mock.calls[0][1]).not.toHaveProperty('api_key');
  view.rerender(<ModelSettings {...props} data={{ ...data, model_profiles: [{ ...profiles[0], revision: 2, model: 'a-remote' }, { ...profiles[1], revision: 2, model: 'b-edited' }] }} />);
  fireEvent.click(screen.getByRole('button', { name: '编辑 默认模型' }));
  expect((activeEditor().getByLabelText('模型名称') as HTMLInputElement).value).toBe('a-draft');
  expect((activeEditor().getByLabelText('API 密钥') as HTMLInputElement).value).toBe('a-secret-draft');
  expect((activeEditor().getByRole('button', { name: '保存配置' }) as HTMLButtonElement).disabled).toBe(true);
  expect(activeEditor().getByText(/你的输入已保留/)).toBeTruthy();
});

it('uses profile-specific CAS reload and test without exposing saved secrets', async () => {
  const api = new RuntimeApi(); const data = { ...dashboard(), model_profiles: profiles.slice(0, 2) };
  const save = vi.spyOn(api, 'saveModelProfile').mockRejectedValue(new ApiError(409, 'REVISION_CONFLICT', '该模型配置已更新'));
  const reload = vi.spyOn(api, 'modelProfile').mockResolvedValue({ ...profiles[1], revision: 3, model: 'new-b' });
  const test = vi.spyOn(api, 'testModelProfile').mockResolvedValue({ config: { ...profiles[1], revision: 3, model: 'new-b', connection_status: 'CONNECTED' }, models: ['new-b'], message: '复核模型目录已验证' });
  render(<ModelSettings data={data} api={api} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '模型配置' })); fireEvent.click(screen.getByRole('button', { name: '编辑 复核模型' }));
  fireEvent.change(activeEditor().getByLabelText('API 密钥'), { target: { value: 'new-b-key' } });
  fireEvent.click(activeEditor().getByRole('button', { name: '保存配置' })); await screen.findByText('该模型配置已更新');
  expect(save).toHaveBeenCalledWith('b', expect.objectContaining({ api_key: 'new-b-key', expected_revision: 1 }));
  fireEvent.click(activeEditor().getByRole('button', { name: '丢弃编辑并重新载入' }));
  await waitFor(() => expect((activeEditor().getByLabelText('API 密钥') as HTMLInputElement).value).toBe(''));
  expect(reload).toHaveBeenCalledWith('b'); expect((activeEditor().getByLabelText('模型名称') as HTMLInputElement).value).toBe('new-b');
  fireEvent.click(activeEditor().getByRole('button', { name: '测试连接' }));
  await screen.findByText('复核模型目录已验证'); expect(test).toHaveBeenCalledWith('b', 3);
});

it('creates a blank independent profile and keeps default and referenced profiles undeletable', async () => {
  const api = new RuntimeApi(); const create = vi.spyOn(api, 'createModelProfile').mockResolvedValue(profile('new-id', '新增模型'));
  const remove = vi.spyOn(api, 'deleteModelProfile').mockResolvedValue({ deleted: true, id: 'c' });
  const data = { ...dashboard(), model_profiles: profiles.slice(0, 3), strategies: [{ id: 's', name: '暂停策略', mode: 'AI', status: 'PAUSED', model_profile_ids: ['b'] } as Strategy] };
  render(<ModelSettings data={data} api={api} onSaved={vi.fn()} />); fireEvent.click(screen.getByRole('button', { name: '模型配置' }));
  expect((screen.getByRole('button', { name: '删除 默认模型' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '删除 复核模型' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '添加模型配置' }));
  expect((activeEditor().getByLabelText('API 密钥') as HTMLInputElement).value).toBe('');
  expect((activeEditor().getByLabelText('启用模型服务') as HTMLInputElement).checked).toBe(false);
  fireEvent.change(activeEditor().getByLabelText('配置名称'), { target: { value: '新增模型' } });
  fireEvent.change(activeEditor().getByLabelText('模型名称'), { target: { value: 'new-model' } });
  fireEvent.click(activeEditor().getByRole('button', { name: '保存配置' }));
  await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({ name: '新增模型', model: 'new-model', enabled: false })));
  expect(create.mock.calls[0][0]).not.toHaveProperty('api_key');
  expect(create.mock.calls[0][0]).not.toHaveProperty('expected_revision');
  fireEvent.click(screen.getByRole('button', { name: '删除 第三模型' })); expect(remove).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '确认删除配置' }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith('c', 1));
});

it('loads the profile collection when the snapshot has no model data and keeps legacy default editable', async () => {
  const api = new RuntimeApi(); const list = vi.spyOn(api, 'modelConfigs').mockResolvedValue({ profiles: [profiles[0]], default_profile_id: 'default' });
  const data = dashboard(); delete data.model_profiles; delete data.model_config;
  const view = render(<ModelSettings data={data} api={api} onSaved={vi.fn()} />);
  await waitFor(() => expect(list).toHaveBeenCalledOnce()); await screen.findByText('1 / 20 份配置'); view.unmount();
  const save = vi.spyOn(api, 'saveModelProfile').mockResolvedValue({ ...profiles[0], revision: 2 });
  render(<ModelSettings data={{ ...data, model_config: profiles[0] }} api={api} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '模型配置' })); fireEvent.click(activeEditor().getByRole('button', { name: '保存配置' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith('default', expect.objectContaining({ name: '默认模型', expected_revision: 1 })));
});

it('does not undo a local create or delete when an older dashboard poll arrives', async () => {
  const api = new RuntimeApi(); const added = profile('new-id', '新增模型');
  vi.spyOn(api, 'createModelProfile').mockResolvedValue(added); vi.spyOn(api, 'deleteModelProfile').mockResolvedValue({ deleted: true, id: 'b' });
  const data = { ...dashboard(), model_profiles: profiles.slice(0, 2) }; const props = { api, onSaved: vi.fn() };
  const view = render(<ModelSettings {...props} data={data} />); fireEvent.click(screen.getByRole('button', { name: '模型配置' }));
  fireEvent.click(screen.getByRole('button', { name: '添加模型配置' }));
  fireEvent.change(activeEditor().getByLabelText('配置名称'), { target: { value: added.name } }); fireEvent.change(activeEditor().getByLabelText('模型名称'), { target: { value: added.model } });
  fireEvent.click(activeEditor().getByRole('button', { name: '保存配置' })); await screen.findByRole('button', { name: '编辑 新增模型' });
  fireEvent.click(screen.getByRole('button', { name: '删除 复核模型' })); fireEvent.click(screen.getByRole('button', { name: '确认删除配置' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: '编辑 复核模型' })).toBeNull());
  view.rerender(<ModelSettings {...props} data={{ ...data, model_profiles: [...data.model_profiles] }} />);
  expect(screen.getByRole('button', { name: '编辑 新增模型' })).toBeTruthy(); expect(screen.queryByRole('button', { name: '编辑 复核模型' })).toBeNull();
  view.rerender(<ModelSettings {...props} data={{ ...data, model_profiles: [profiles[0], added] }} />);
  expect(screen.getByRole('button', { name: '编辑 新增模型' })).toBeTruthy();
});

it('preserves a dirty editor if that profile is deleted elsewhere without routing to another profile', () => {
  const data = { ...dashboard(), model_profiles: profiles.slice(0, 2) }; const props = { api: new RuntimeApi(), onSaved: vi.fn() };
  const view = render(<ModelSettings {...props} data={data} />); fireEvent.click(screen.getByRole('button', { name: '模型配置' }));
  fireEvent.click(screen.getByRole('button', { name: '编辑 复核模型' })); fireEvent.change(activeEditor().getByLabelText('模型名称'), { target: { value: 'retained-draft' } });
  view.rerender(<ModelSettings {...props} data={{ ...data, model_profiles: [profiles[0]] }} />);
  expect((activeEditor().getByLabelText('模型名称') as HTMLInputElement).value).toBe('retained-draft');
  expect((activeEditor().getByRole('button', { name: '保存配置' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/此配置已不可用，未保存内容仍保留/)).toBeTruthy();
});

it('locks an unknown manual cross-analysis attempt and replays its original body after profiles change', async () => {
  const api = new RuntimeApi(); const analyze = vi.spyOn(api, 'analyzeDecision').mockRejectedValue(new ApiError(0, 'NETWORK_ERROR', '结果未知'));
  const props = { api, onSaved: vi.fn(), data: dashboard() };
  const view = render(<ManualAnalysis {...props} profiles={profiles} />);
  fireEvent.change(screen.getByLabelText('分析标的'), { target: { value: 'BTCUSDT' } });
  fireEvent.change(screen.getByLabelText('分析方式'), { target: { value: 'consensus' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /复核模型/ }));
  fireEvent.click(screen.getByRole('checkbox', { name: /我确认/ })); fireEvent.click(screen.getByRole('button', { name: '生成一次分析' }));
  await screen.findByRole('button', { name: '查询 / 重试同一请求' });
  expect(analyze).toHaveBeenCalledWith({ symbol: 'BTCUSDT', analysis_mode: 'consensus', model_profile_ids: ['default', 'b'], expected_revisions: { default: 1, b: 1 }, acknowledge_cost: true }, expect.any(String));
  expect((screen.getByLabelText('分析标的') as HTMLSelectElement).disabled).toBe(true);
  expect(screen.getByLabelText('分析方式').closest('fieldset')!.disabled).toBe(true);
  view.rerender(<ManualAnalysis {...props} data={{ ...props.data, market: { ...props.data.market, quotes: [] } }} profiles={[{ ...profiles[0], enabled: false, revision: 4 }]} />);
  fireEvent.click(screen.getByRole('button', { name: '查询 / 重试同一请求' }));
  await waitFor(() => expect(analyze).toHaveBeenCalledTimes(2)); expect(analyze.mock.calls[1]).toEqual(analyze.mock.calls[0]);
  fireEvent.click(screen.getByRole('button', { name: '新建分析请求' }));
  expect((screen.getByRole('checkbox', { name: /我确认/ }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole('button', { name: '生成一次分析' }) as HTMLButtonElement).disabled).toBe(true);
  expect(analyze).toHaveBeenCalledTimes(2);
});

it.each([['AGREED', '方向一致'], ['DISAGREED', '方向分歧 · HOLD'], ['INCOMPLETE', '结果不完整 · HOLD']] as const)('labels %s and displays each participant honestly', (consensus_status, label) => {
  const decision = { analysis_mode: 'consensus', consensus_status, model_profile_ids: ['default', 'b', 'c'], model_results: [{ profile_id: 'default', profile_name: '当时的名称', model: 'model-a', config_revision: 2, status: 'SUCCEEDED', decision: { action: 'BUY', summary: '支持上涨', counter_evidence: ['波动风险'], risk_status: 'NOT_EVALUATED' } }, { profile_id: 'b', profile_name: '复核模型', model: 'model-b', config_revision: 1, status: 'FAILED', error: '服务商返回无效内容' }] } as Decision;
  render(<ModelOutcomes decision={decision} />);
  expect(screen.getByText(label)).toBeTruthy(); expect(screen.getByText('当时的名称')).toBeTruthy(); expect(screen.getByText('支持上涨')).toBeTruthy();
  expect(screen.getByText('服务商返回无效内容')).toBeTruthy(); expect(screen.getByText('结果缺失')).toBeTruthy(); expect(screen.getByText(/不代表建议正确或风控批准/)).toBeTruthy();
});

it('saves explicit strategy routing with active cost acknowledgement and clears RULE bindings', async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  const existing: Strategy = { id: 's1', name: '观察', mode: 'AI', status: 'ACTIVE', model_profile_ids: ['default'], analysis_mode: 'single', symbol: 'BTCUSDT', budget: '100', target_pct: '10', revision: 3, created_at: '', updated_at: '' };
  const api = new RuntimeApi(); const save = vi.spyOn(api, 'saveStrategy').mockResolvedValue(existing);
  render(<StrategyDialog api={api} existing={existing} mode="AI" profiles={profiles} onSaved={vi.fn()} onClose={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('分析方式'), { target: { value: 'consensus' } }); fireEvent.click(screen.getByRole('checkbox', { name: /复核模型/ }));
  expect(screen.getByText(/保存即确认按当前模型选择持续分析/)).toBeTruthy(); fireEvent.click(screen.getByRole('button', { name: '保存策略' }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ analysis_mode: 'consensus', model_profile_ids: ['default', 'b'], acknowledge_model_cost: true }), existing, 'ACTIVE'));
  await waitFor(() => expect((screen.getByRole('button', { name: '保存策略' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('决策模式'), { target: { value: 'RULE' } }); fireEvent.click(screen.getByRole('button', { name: '保存策略' }));
  await waitFor(() => expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ mode: 'RULE', analysis_mode: 'single', model_profile_ids: [], acknowledge_model_cost: false, auto_execute: false }), existing, 'DRAFT'));
});

it('rejects a one-model cross selection and missing bindings without falling back', async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  const existing: Strategy = { id: 's1', name: '观察', mode: 'AI', status: 'ACTIVE', model_profile_ids: ['deleted'], analysis_mode: 'single', symbol: 'BTCUSDT', budget: '100', target_pct: '10', revision: 3, created_at: '', updated_at: '' };
  const api = new RuntimeApi(); const save = vi.spyOn(api, 'saveStrategy');
  render(<StrategyDialog api={api} existing={existing} mode="AI" profiles={profiles} onSaved={vi.fn()} onClose={vi.fn()} />);
  expect((screen.getByLabelText('模型配置') as HTMLSelectElement).value).toBe('deleted'); fireEvent.click(screen.getByRole('button', { name: '保存策略' }));
  await screen.findByText(/所选模型配置不可用，请重新选择/); expect(save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('模型配置'), { target: { value: 'b' } }); fireEvent.change(screen.getByLabelText('分析方式'), { target: { value: 'consensus' } });
  fireEvent.click(screen.getByRole('button', { name: '保存策略' })); await screen.findByText('交叉分析需要选择 2 至 3 个不同模型配置。'); expect(save).not.toHaveBeenCalled();
});
