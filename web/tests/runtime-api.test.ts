import { describe, it, expect, vi } from 'vitest';
import { RuntimeApi } from '../src/runtime/api';
import { chartObservations, chartTimeLabel, money, percent, positiveDecimal, quoteIsFresh, time } from '../src/runtime/format';

describe('runtime request boundary', () => {
  it('sends cookie, session CSRF and stable idempotency key without a client execution price', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ order: { id: 'o1' }, account_revision: 2 })));
    const api = new RuntimeApi(fetcher);
    api.setSession({ username: 'owner', csrf_token: 'session-bound', environment: 'PAPER', expires_at: '' });
    await api.order({ symbol: 'BTCUSDT', side: 'BUY', quantity: '0.01', strategy_id: null }, 'attempt-1');
    const [path, init] = fetcher.mock.calls[0];
    expect(path).toBe('/api/paper/orders');
    expect(init.credentials).toBe('same-origin');
    expect(init.headers.get('X-CSRF-Token')).toBe('session-bound');
    expect(init.headers.get('Idempotency-Key')).toBe('attempt-1');
    expect(JSON.parse(init.body)).not.toHaveProperty('price');
  });
  it('clears CSRF with session and retains server conflict information', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'REVISION_CONFLICT', message: '配置已变更，请刷新。' } }), { status: 409 }));
    const api = new RuntimeApi(fetcher);
    api.setSession(null);
    await expect(api.saveRisk({ max_position_pct: '20', max_daily_loss: '500' }, 1)).rejects.toMatchObject({ status: 409, code: 'REVISION_CONFLICT', message: '配置已变更，请刷新。' });
    expect(fetcher.mock.calls[0][1].headers.has('X-CSRF-Token')).toBe(false);
  });
  it('shows an actionable outage and rejects non-JSON success', async () => {
    await expect(new RuntimeApi(vi.fn().mockRejectedValue(new TypeError('fetch failed'))).bootstrap()).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
    await expect(new RuntimeApi(vi.fn().mockResolvedValue(new Response('<html>'))).bootstrap()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  });
});
describe('truthful data formatting', () => {
  it('uses local clock labels for short actual histories and dates from a day onward', () => {
    expect(chartTimeLabel('2026-10-26T06:00:00Z', 600_000)).toBe('14:00');
    expect(chartTimeLabel('2026-10-26T06:10:00Z', 600_000)).toBe('14:10');
    expect(chartTimeLabel('2026-10-26T06:00:00Z', 86_400_000)).toBe('10/26');
    expect(chartTimeLabel('2026-10-27T06:00:00Z', 172_800_000)).toBe('10/27');
  });
  it('bounds a long chart while retaining observed endpoints and extrema', () => {
    const points = Array.from({ length: 5000 }, (_, index) => ({ at: new Date(index * 1000).toISOString(), equity: index === 1111 ? '150' : index === 3333 ? '50' : '100' }));
    const chart = chartObservations(points);
    expect(chart.length).toBeLessThanOrEqual(400);
    expect(chart[0]).toBe(points[0]); expect(chart.at(-1)).toBe(points.at(-1));
    expect(chart).toContain(points[1111]); expect(chart).toContain(points[3333]);
    expect(chart.every(point => points.includes(point))).toBe(true);
  });
  it('never turns missing or invalid money into a zero', () => {
    expect(money(null)).toBe('—'); expect(money('NaN')).toBe('—');
    expect(money('100000')).toBe('$100,000.00'); expect(money('-12.34', true)).toBe('−$12.34');
    expect(percent('0')).toBe('0.00%'); expect(time(null)).toBe('尚未同步');
  });
  it('rejects invalid decimal input and expired quote execution', () => {
    expect(positiveDecimal('Infinity')).toBe(false); expect(positiveDecimal('1e8')).toBe(false); expect(positiveDecimal('-1')).toBe(false); expect(positiveDecimal('0.001')).toBe(true);
    const now = Date.parse('2026-10-26T10:00:00Z');
    expect(quoteIsFresh({ stale: false, as_of: '2026-10-26T09:58:00Z' }, now)).toBe(false);
    expect(quoteIsFresh({ stale: true, as_of: '2026-10-26T09:59:59Z' }, now)).toBe(false);
    expect(quoteIsFresh({ stale: false, as_of: '2026-10-26T09:59:59Z' }, now)).toBe(true);
  });
});
