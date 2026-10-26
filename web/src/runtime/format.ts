export function finite(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
export function number(value: string | number | null | undefined, decimals = 2): string {
  const n = finite(value);
  return n === null ? '—' : n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
export function money(value: string | number | null | undefined, signed = false): string {
  const n = finite(value);
  if (n === null) return '—';
  return `${n < 0 ? '−' : signed && n > 0 ? '+' : ''}$${number(Math.abs(n))}`;
}
export function percent(value: string | number | null | undefined, signed = false): string {
  const n = finite(value);
  return n === null ? '—' : `${n < 0 ? '−' : signed && n > 0 ? '+' : ''}${number(Math.abs(n))}%`;
}
export function quantity(value: string | null | undefined): string {
  const n = finite(value);
  return n === null ? '—' : n.toLocaleString('en-US', { maximumFractionDigits: 8 });
}
export function time(value: string | null | undefined, full = false): string {
  if (!value || !Number.isFinite(Date.parse(value))) return '尚未同步';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', ...(full ? { year: 'numeric', month: '2-digit', day: '2-digit' } as const : {}),
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(value));
}
export function tone(value: string | number | null | undefined): string {
  const n = finite(value);
  return n === null || n === 0 ? '' : n > 0 ? 'positive' : 'negative';
}
export function positiveDecimal(value: string): boolean {
  return /^\d+(\.\d+)?$/.test(value.trim()) && (finite(value) ?? 0) > 0;
}
export function quoteIsFresh(quote: { stale: boolean; as_of: string } | undefined, now = Date.now()): boolean {
  if (!quote || quote.stale) return false;
  const age = now - Date.parse(quote.as_of);
  return Number.isFinite(age) && age >= -5000 && age <= 90_000;
}

export function chartTimeLabel(at: string, spanMilliseconds: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    ...(spanMilliseconds < 86_400_000
      ? { hour: '2-digit', minute: '2-digit', hour12: false } as const
      : { month: '2-digit', day: '2-digit' } as const),
  }).format(new Date(at));
}

/** Preserve actual observations and each bucket's extremes without inventing points. */
export function chartObservations(points: { at: string; equity: string }[], maximum = 400) {
  const sorted = points.filter(p => finite(p.equity) !== null && Number.isFinite(Date.parse(p.at))).sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const unique = sorted.filter((point, index) => index === 0 || point.at !== sorted[index - 1].at);
  if (unique.length <= maximum) return unique;
  const result = [unique[0]], buckets = Math.max(1, Math.floor((maximum - 2) / 2)), size = (unique.length - 2) / buckets;
  for (let bucket = 0; bucket < buckets; bucket++) {
    const start = 1 + Math.floor(bucket * size), end = Math.min(unique.length - 1, 1 + Math.floor((bucket + 1) * size));
    let low = start, high = start;
    for (let index = start + 1; index < end; index++) {
      if (Number(unique[index].equity) < Number(unique[low].equity)) low = index;
      if (Number(unique[index].equity) > Number(unique[high].equity)) high = index;
    }
    result.push(unique[Math.min(low, high)]);
    if (low !== high) result.push(unique[Math.max(low, high)]);
  }
  result.push(unique[unique.length - 1]);
  return result;
}

/** Compact display precision; raw decimal values remain available in market titles. */
export function marketPrice(value: string | number | null | undefined): string {
  const parsed = finite(value);
  if (parsed === null) return '—';
  const magnitude = Math.abs(parsed);
  return parsed.toLocaleString('en-US', magnitude > 0 && magnitude < .01
    ? { maximumSignificantDigits: 6 }
    : { maximumFractionDigits: magnitude >= 1000 ? 2 : magnitude >= 1 ? 4 : 6 });
}
export function marketVolume(value: string | null | undefined): string {
  const parsed = finite(value);
  if (parsed === null) return '—';
  return Math.abs(parsed) >= 1_000_000
    ? parsed.toLocaleString('en-US', { notation: 'compact', maximumFractionDigits: 2 })
    : marketPrice(value);
}
