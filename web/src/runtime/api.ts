import type { ActionReceipt, Bootstrap, Dashboard, Market, PaperAction, PaperOrderInput, Range, Risk, Session, Strategy, StrategyInput } from './types';

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) { super(message); this.name = 'ApiError'; }
}
export class RuntimeApi {
  private csrf = '';
  onUnauthorized: (() => void) | null = null;
  constructor(private readonly transport: typeof fetch = (...args) => fetch(...args)) {}
  setSession(session: Session | null) { this.csrf = session?.csrf_token ?? ''; }
  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    headers.set('Accept', 'application/json');
    if (options.body) headers.set('Content-Type', 'application/json');
    if (options.method && options.method !== 'GET' && this.csrf) headers.set('X-CSRF-Token', this.csrf);
    const signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(20_000)]) : AbortSignal.timeout(20_000);
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
      body: JSON.stringify({ ...values, ...(existing ? { expected_revision: existing.revision, ...(status ? { status } : {}) } : {}) }),
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
