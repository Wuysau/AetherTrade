import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { EN_COMMON } from './locales/en.common';
import { EN_CONSOLE } from './locales/en.console';
import { EN_FEATURES } from './locales/en.features';
import { EN_BACKEND } from './locales/backend';

export type Locale = 'zh' | 'en';

const STORAGE_KEY = 'aethertrade.locale';
const EN: Record<string, string> = { ...EN_COMMON, ...EN_CONSOLE, ...EN_FEATURES };

function stored(): Locale {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    if (value === 'en' || value === 'zh') return value;
  } catch { /* private mode */ }
  return 'zh';
}

let current: Locale = typeof window === 'undefined' ? 'zh' : stored();

export function locale(): Locale { return current; }
export function localeTag(): string { return current === 'en' ? 'en-US' : 'zh-CN'; }

/** Translate a Chinese UI string. Unknown strings fall back to the source text. */
export function t(text: string): string {
  if (current === 'zh') return text;
  return EN[text] ?? text;
}

const PHRASES: Array<[string, string]> = [
  [' · AI 分析', ' · AI analysis'],
  [' · 已启用，按策略选择使用', ' · enabled, used per strategy'],
  [' · 已停用', ' · disabled'],
  ['尚未配置模型', 'No model configured'],
  ['所选模型方向一致 · ', 'Models aligned · '],
  ['所选模型存在方向分歧 · 建议观察', 'Models split · hold'],
  ['建议买入', 'Buy signal'],
  ['建议卖出', 'Sell signal'],
  ['建议观察', 'Hold signal'],
  ['分析结果', 'Analysis result'],
];

/** Translate server-provided text, including composed notification sentences. */
export function tt(text: string): string;
export function tt(text: string | null | undefined): string | null | undefined;
export function tt(text: string | null | undefined) {
  if (current === 'zh' || !text) return text;
  const exact = EN[text] ?? EN_BACKEND[text];
  if (exact) return exact;
  let out = text;
  for (const [zh, en] of PHRASES) out = out.split(zh).join(en);
  if (out === text) return text;
  return out.replace(/：/g, ': ').replace(/。/g, '. ').replace(/；/g, '; ')
    .replace(/，/g, ', ').replace(/\s+$/, '');
}

/** Wrap a module-scope label map so its strings follow the active locale. */
export function live<T extends object>(build: () => T): T {
  return new Proxy({} as T, {
    get: (_target, key) => Reflect.get(build(), key),
    has: (_target, key) => key in build(),
    ownKeys: () => Reflect.ownKeys(build()),
    getOwnPropertyDescriptor: (_target, key) => {
      const descriptor = Reflect.getOwnPropertyDescriptor(build(), key);
      return descriptor ? { ...descriptor, configurable: true } : undefined;
    },
  });
}

type LocaleContextValue = { locale: Locale; setLocale: (next: Locale) => void };
const LocaleContext = createContext<LocaleContextValue>({ locale: current, setLocale: () => {} });

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [value, setValue] = useState<Locale>(current);
  const setLocale = useCallback((next: Locale) => {
    current = next;
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch { /* private mode */ }
    document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN';
    setValue(next);
  }, []);
  useEffect(() => { document.documentElement.lang = value === 'en' ? 'en' : 'zh-CN'; }, [value]);
  const context = useMemo(() => ({ locale: value, setLocale }), [value, setLocale]);
  return <LocaleContext.Provider value={context}>{children}</LocaleContext.Provider>;
}

export function useLocale() { return useContext(LocaleContext); }

export function LanguageSwitch({ compact = false }: { compact?: boolean }) {
  const { locale: active, setLocale } = useLocale();
  return <div className={`lang-switch ${compact ? 'compact' : ''}`} role="group" aria-label={t('界面语言')}>
    <button type="button" className={active === 'zh' ? 'active' : ''} aria-pressed={active === 'zh'} onClick={() => setLocale('zh')}>中文</button>
    <button type="button" className={active === 'en' ? 'active' : ''} aria-pressed={active === 'en'} onClick={() => setLocale('en')}>EN</button>
  </div>;
}
