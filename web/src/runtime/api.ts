import type { CandleHistory, CandleInterval, MarketConfig, MarketInput, AnalysisInput, ModelProfile, ModelProfileInput, TokenUsage, Notifications, ModelConfig, ModelInput, Decision, Instrument, SymbolName, ActionReceipt, Bootstrap, Dashboard, Market, PaperAction, PaperOrderInput, Range, Risk, Session, Strategy, StrategyInput } from './types';

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) { super(message); this.name = 'ApiError'; }
}
export class RuntimeApi {
  private csrf = '';
  onUnauthorized: (() => void) | null = null;
  constructor(private readonly transport: typeof fetch = (...args) => fetch(...args)) {}
  setSession(session: Session | null) { this.csrf = session?.csrf_token ?? ''; }
  async request<T>(path: string, options: RequestInit = {}, timeoutMs = 20_000): Promise<T> {
    const headers = new Headers(options.headers);
    headers.set('Accept', 'application/json');
    if (options.body) headers.set('Content-Type', 'application/json');
    if (options.method && options.method !== 'GET' && this.csrf) headers.set('X-CSRF-Token', this.csrf);
    const signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(timeoutMs)]) : AbortSignal.timeout(timeoutMs);
    let response: Response;
    try { response = await this.transport(`/api${path}`, { ...options, headers, credentials: 'same-origin', signal }); }
    catch (error) {
      if (options.signal?.aborted) throw error;
      throw new ApiError(0, 'NETWORK_ERROR', '无法连接本地服务，请检查服务是否启动后重试。');
    }
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      if (response.status === 401) this.onUnauthorized?.();
      throw new ApiError(response.status, body?.error?.code ?? 'HTTP_ERROR', body?.error?.message ?? `服务请求失败（${response.status}），请重试。`);
    }
    if (body === null) throw new ApiError(response.status, 'INVALID_RESPONSE', '服务响应格式异常，请刷新重试。');
    return body as T;
  }
  marketConfig() { return this.request<MarketConfig>('/market/config'); }
  saveMarketConfig(values: MarketInput) { return this.request<MarketConfig>('/market/config', { method: 'PUT', body: JSON.stringify(values) }); }
  testMarketConfig(revision: number) { return this.request<{ ok: boolean; revision: number; message: string; quote_count: number }>('/market/config/test', { method: 'POST', body: JSON.stringify({ expected_revision: revision }) }, 30_000); }
  marketSnapshot(signal?: AbortSignal) { return this.request<Market>('/market/snapshot', { signal }); }
  marketCandles(symbol: SymbolName, interval: CandleInterval, signal?: AbortSignal) { return this.request<CandleHistory>(`/market/candles?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=120`, { signal }); }
  openMarketStream(callbacks: { market: (market: Market) => void; error: () => void; unauthorized: () => void }) {
    const stream = new EventSource('/api/market/stream');
    let stopped = false;
    const close = () => { stopped = true; stream.close(); };
    stream.addEventListener('market', event => { if (stopped) return; try { callbacks.market(JSON.parse((event as MessageEvent).data)); } catch { callbacks.error(); } });
    stream.addEventListener('auth-expired', () => { if (stopped) return; close(); callbacks.unauthorized(); this.onUnauthorized?.(); });
    stream.onerror = () => { if (!stopped) callbacks.error(); };
    return close;
  }
  notifications() { return this.request<Notifications>('/notifications'); }
  markNotificationsRead(values: { ids: string[] } | { all: true }) { return this.request<Notifications>('/notifications/read', { method: 'POST', body: JSON.stringify(values) }); }
  instruments() { return this.request<{ instruments: Instrument[] }>('/instruments'); }
  modelConfig() { return this.request<ModelConfig>('/model-config'); }
  modelConfigs() { return this.request<{ profiles: ModelProfile[]; default_profile_id: string }>('/model-configs'); }
  modelProfile(id: string) { return this.request<ModelProfile>(`/model-configs/${encodeURIComponent(id)}`); }
  createModelProfile(values: ModelProfileInput) { return this.request<ModelProfile>('/model-configs', { method: 'POST', body: JSON.stringify(values) }); }
  saveModelProfile(id: string, values: ModelProfileInput & { expected_revision: number }) { return this.request<ModelProfile>(`/model-configs/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(values) }); }
  deleteModelProfile(id: string, revision: number) { return this.request<{ deleted: true; id: string }>(`/model-configs/${encodeURIComponent(id)}`, { method: 'DELETE', body: JSON.stringify({ expected_revision: revision }) }); }
  testModelProfile(id: string, revision: number) { return this.request<{ config: ModelProfile; models: string[]; message: string }>(`/model-configs/${encodeURIComponent(id)}/test`, { method: 'POST', body: JSON.stringify({ expected_revision: revision }) }, 150_000); }
  analyzeDecision(values: AnalysisInput, key: string) { return this.request<{ decision: Decision; usage: TokenUsage | null }>('/decisions/analyze', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(values) }, 150_000); }
  saveModelConfig(values: ModelInput) { return this.request<ModelConfig>('/model-config', { method: 'PUT', body: JSON.stringify(values) }); }
  testModelConfig(revision: number) { return this.request<{ config: ModelConfig; models: string[]; message: string }>('/model-config/test', { method: 'POST', body: JSON.stringify({ expected_revision: revision }) }, 150_000); }
  generateDecision(symbol: SymbolName, revision: number, key: string) { return this.request<{ decision: Decision; usage: { input_tokens: number | null; output_tokens: number | null } }>('/decisions/generate', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ symbol, expected_revision: revision, acknowledge_cost: true }) }, 150_000); }
  bootstrap() { return this.request<Bootstrap>('/bootstrap'); }
  session() { return this.request<Session>('/session'); }
  login(username: string, password: string) { return this.request<Session>('/session/login', { method: 'POST', body: JSON.stringify({ username, password }) }); }
  setup(username: string, password: string, initial_cash: string) { return this.request<Session>('/setup', { method: 'POST', body: JSON.stringify({ username, password, initial_cash }) }); }
  logout() { return this.request<{ ok: boolean }>('/session/logout', { method: 'POST' }); }
  dashboard(range: Range, signal?: AbortSignal) { return this.request<Dashboard>(`/dashboard?range=${range}`, { signal }); }
  refreshMarket() { return this.request<Market>('/market/refresh', { method: 'POST' }); }
  saveStrategy(values: StrategyInput, existing?: Strategy, status?: Strategy['status']) {
    return this.request<Strategy>(existing ? `/strategies/${encodeURIComponent(existing.id)}` : '/strategies', {
      method: existing ? 'PATCH' : 'POST',
      body: JSON.stringify({ ...values, ...(existing ? { expected_revision: existing.revision } : {}), ...(status ? { status } : {}) }),
    });
  }
  saveRisk(values: Omit<Risk, 'revision'>, revision: number) { return this.request<Risk>('/risk-policy', { method: 'PUT', body: JSON.stringify({ ...values, expected_revision: revision }) }); }
  order(values: PaperOrderInput, key: string) {
    return this.request<{ order: { id: string }; account_revision: number }>('/paper/orders', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(values) });
  }
  action(action: PaperAction, revision: number, key: string, confirmation?: string) {
    return this.request<ActionReceipt>('/paper/actions', { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({ action, expected_revision: revision, ...(confirmation ? { confirmation } : {}) }) });
  }
  async exportResearch(): Promise<Blob> {
    let response: Response;
    try { response = await this.transport('/api/research/export', { credentials: 'same-origin', signal: AbortSignal.timeout(20_000) }); }
    catch { throw new ApiError(0, 'NETWORK_ERROR', '摘要下载失败，请检查连接后重试。'); }
    if (!response.ok) {
      if (response.status === 401) this.onUnauthorized?.();
      const body = await response.json().catch(() => null);
      throw new ApiError(response.status, body?.error?.code ?? 'HTTP_ERROR', body?.error?.message ?? '摘要下载失败，请重试。');
    }
    return response.blob();
  }
}
export const runtimeApi = new RuntimeApi();
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请重试。';
