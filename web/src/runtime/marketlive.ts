import { useEffect, useState } from 'react';
import { ApiError, type RuntimeApi } from './api';
import type { BrowserMarketStatus, Market } from './types';

/** A single ordering boundary shared by dashboard, refresh and stream responses. */
export class MarketOrder {
  current: Market | null = null;
  private retired = new Set<string>();
  accept(next: Market, authoritative = false): Market {
    const previous = this.current;
    if (!previous) return this.current = next;
    const oldId = previous.instance_id, newId = next.instance_id;
    if (oldId && !newId) return previous;
    if (newId && this.retired.has(newId)) return previous;
    if (oldId && newId && oldId !== newId) {
      // Only the dedicated live channel can establish a new server instance.
      if (!authoritative) return previous;
      this.retired.add(oldId);
    } else {
      if ((next.source_revision ?? -1) < (previous.source_revision ?? -1)) return previous;
      if (next.source_revision === previous.source_revision && (next.sequence ?? -1) < (previous.sequence ?? -1)) return previous;
    }
    return this.current = next;
  }
}

export function useMarketStream(api: RuntimeApi, enabled: boolean, receive: (market: Market) => void): BrowserMarketStatus {
  const [status, setStatus] = useState<BrowserMarketStatus>('connecting');
  useEffect(() => {
    if (!enabled || !api.marketSnapshot) return;
    let disposed = false, close: (() => void) | undefined, timer: number | undefined, reconnect: number | undefined;
    let polling = false, pending = false, authenticated = true, generation = 0;
    let pollController: AbortController | undefined;
    const isVisible = () => document.visibilityState !== 'hidden';
    function stop() {
      generation++; pollController?.abort(); pending = false;
      close?.(); close = undefined;
      window.clearTimeout(timer); window.clearTimeout(reconnect); timer = reconnect = undefined;
    }
    async function poll() {
      if (disposed || !authenticated || document.visibilityState === 'hidden' || pending) return;
      pending = true; const activeGeneration = generation;
      pollController = new AbortController();
      try { const market = await api.marketSnapshot?.(pollController.signal); if (!disposed && authenticated && activeGeneration === generation && isVisible() && market) { receive(market); setStatus('polling'); } }
      catch (error) {
        if (disposed || activeGeneration !== generation) return;
        if (error instanceof ApiError && error.status === 401) { authenticated = false; stop(); return; }
        if (!disposed) setStatus('unavailable');
      } finally {
        if (activeGeneration === generation) pending = false;
        if (!disposed && authenticated && activeGeneration === generation && polling && isVisible()) timer = window.setTimeout(() => void poll(), 5000);
      }
    }
    function fallback() {
      if (polling) return;
      stop();
      polling = true; setStatus('polling'); void poll();
      reconnect = window.setTimeout(() => { if (!disposed && authenticated) connect(); }, 30_000);
    }
    function connect() {
      stop(); polling = false;
      if (disposed || !authenticated || document.visibilityState === 'hidden') { setStatus('paused'); return; }
      setStatus('connecting');
      if (!api.openMarketStream || typeof EventSource === 'undefined') { fallback(); return; }
      try {
        const activeGeneration = generation;
        timer = window.setTimeout(() => { if (activeGeneration === generation) fallback(); }, 12_000);
        close = api.openMarketStream({
          market: market => { if (!disposed && authenticated && activeGeneration === generation && isVisible()) { window.clearTimeout(timer); receive(market); setStatus('streaming'); } },
          error: () => { if (!disposed && authenticated && activeGeneration === generation) fallback(); },
          unauthorized: () => { if (activeGeneration === generation) { authenticated = false; stop(); } },
        });
      } catch { fallback(); }
    }
    function visibility() { if (document.visibilityState === 'hidden') { stop(); polling = false; setStatus('paused'); } else connect(); }
    connect(); document.addEventListener('visibilitychange', visibility);
    return () => { disposed = true; stop(); document.removeEventListener('visibilitychange', visibility); };
  }, [api, enabled, receive]);
  return status;
}
