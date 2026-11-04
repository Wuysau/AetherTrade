import { useEffect, useRef, useState, type FormEvent } from 'react';
import { RuntimeApi, errorMessage } from './api';
import { useLocale } from './i18n';
import { Alert, Loading } from './ui';

interface DemoStatus {
  environment: 'BINANCE_DEMO'; product: string; state: string; execution_authorized: false;
  credential_storage: string; endpoint: string; budget_cap_usdt: string;
  balances: { asset: string; free: string; locked: string }[];
  fingerprint?: string; checked_at?: number; snapshot_stale?: boolean; expires_at?: number;
  open_order_count?: number; blockers: string[];
  capabilities: { product: string; order_type: string; state: string; enabled: boolean }[];
}

/** Dedicated venue panel. It never relabels PAPER balances, orders or PnL. */
export function Demo({ api }: { api: RuntimeApi }) {
  const { locale } = useLocale();
  const en = locale === 'en';
  const text = (zh: string, english: string) => en ? english : zh;
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [key, setKey] = useState(''), [secret, setSecret] = useState('');
  const [ack, setAck] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const busyRef = useRef(false), revision = useRef(0), mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    ++revision.current;
    let polling = false;
    async function refreshStatus() {
      if (busyRef.current || polling) return;
      polling = true;
      const version = revision.current;
      try {
        const value = await api.request<DemoStatus>('/demo/status', { signal: controller.signal });
        if (mounted.current && revision.current === version) setStatus(value);
      } catch (err) {
        if (!controller.signal.aborted && mounted.current && revision.current === version) setError(errorMessage(err));
      } finally { polling = false; }
    }
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 10_000);
    return () => { mounted.current = false; ++revision.current; controller.abort(); window.clearInterval(timer); };
  }, [api]);
  async function connect(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current || !ack || !key || !secret) return;
    busyRef.current = true; setBusy(true); setError('');
    const version = ++revision.current;
    const body = JSON.stringify({ api_key: key, api_secret: secret, acknowledge_demo_only: true });
    // Clear the controls before awaiting a request. Never use browser storage.
    setKey(''); setSecret(''); setAck(false);
    try {
      const value = await api.request<DemoStatus>('/demo/connect', { method: 'POST', body }, 60_000);
      if (mounted.current && version === revision.current) setStatus(value);
    } catch (err) {
      if (mounted.current && version === revision.current) { setError(errorMessage(err)); setStatus(null); }
    } finally {
      busyRef.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function disconnect() {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(''); setKey(''); setSecret(''); setAck(false);
    const version = ++revision.current;
    try {
      const value = await api.request<DemoStatus>('/demo/disconnect', { method: 'POST' });
      if (mounted.current && version === revision.current) setStatus(value);
    } catch (err) { if (mounted.current) setError(errorMessage(err)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  return <section className="demo-page">
    <header className="page-heading"><div><p className="eyebrow">BINANCE DEMO · SPOT</p><h1 id="page-heading" tabIndex={-1}>{text('币安 Demo 连接', 'Binance Demo connection')}</h1><p>{text('独立官方模拟账户。不会使用本地 PAPER 余额，也不会连接实盘。', 'An independent official simulated account. PAPER balances are separate; production trading is not connected.')}</p></div></header>
    <Alert>{text('当前仅开放只读验证。订单能力尚未通过 Demo 验收，交易与自动策略均未启用。', 'Read-only verification only. Order capabilities have not passed Demo qualification. Trading and automatic strategies are disabled.')}</Alert>
    <div className="panel" style={{ padding: '24px', marginTop: '20px' }}>
      <h2>{text('用户填入专用 API Key', 'Enter your dedicated Demo API key')}</h2>
      <p>{text('仅支持 Demo HMAC Key。凭据只保留在服务进程内存中，最多 15 分钟，退出登录或移除后失效。不写入数据库、浏览器存储或模型上下文。', 'Demo HMAC keys only. Credentials remain in server memory for at most 15 minutes, until logout or removal. They are never saved to the database, browser storage or model context.')}</p>
      <form className="editor-form" onSubmit={connect} autoComplete="off">
        <label className="field"><span>Demo API Key</span><input type="password" autoComplete="off" spellCheck={false} required minLength={16} maxLength={256} value={key} onChange={e => setKey(e.target.value)} disabled={busy} /></label>
        <label className="field"><span>Demo API Secret</span><input type="password" autoComplete="new-password" spellCheck={false} required minLength={16} maxLength={256} value={secret} onChange={e => setSecret(e.target.value)} disabled={busy} /></label>
        <label><input type="checkbox" checked={ack} disabled={busy} onChange={e => setAck(e.target.checked)} /> {text('确认这是 Demo 专用凭据，并同意向官方 Demo 端点发送只读查询。不要填入实盘密钥。', 'I confirm these are Demo-only credentials and authorize read-only queries to the official Demo endpoint. Do not enter production keys.')}</label>
        <div className="form-actions"><button className="button primary" type="submit" disabled={busy || !ack || !key || !secret}>{busy ? text('正在处理…', 'Working…') : text('验证只读连接', 'Verify read-only connection')}</button><button className="button" type="button" disabled={busy} onClick={() => void disconnect()}>{text('移除本次凭据', 'Remove session credentials')}</button></div>
      </form>
      <p className="field-help">{text('移除凭据不会取消交易所挂单。此页面不提供下单、撤单或交易启用入口。', 'Removing credentials does not cancel exchange orders. This page cannot place or cancel orders, or enable trading.')}</p>
      {error && <Alert error>{error}</Alert>}
    </div>
    <div className="panel" style={{ padding: '24px', marginTop: '20px' }} aria-live="polite">
      <h2>{text('只读连接状态', 'Read-only connection status')}</h2>
      {!status ? busy ? <Loading /> : <p>{text('尚无已验证快照。', 'No verified snapshot.')}</p> : <>
        <p><strong>{status.state}</strong> · {status.endpoint}</p>
        {status.fingerprint && <p>{text('密钥指纹', 'Key fingerprint')}: {status.fingerprint}</p>}
        {status.checked_at && <p>{text('快照时间', 'Snapshot time')}: {new Date(status.checked_at * 1000).toISOString()} · {text('仅供观察，不构成交易准入', 'For observation only, not execution admission')}</p>}
        {status.snapshot_stale && <Alert>{text('快照已过期。重新填入凭据验证后才会更新。', 'The snapshot is stale. Re-enter credentials and verify to refresh.')}</Alert>}
        <p>{text('项目预算上限', 'Project budget ceiling')}: {status.budget_cap_usdt} USDT · {text('不是官方 Demo 余额', 'Not the official Demo balance')}</p>
        {status.balances.length > 0 ? <table className="data-table"><thead><tr><th>{text('资产', 'Asset')}</th><th>{text('官方可用余额', 'Official free balance')}</th><th>{text('官方冻结余额', 'Official locked balance')}</th></tr></thead><tbody>{status.balances.map(balance => <tr key={balance.asset}><td>{balance.asset}</td><td>{balance.free}</td><td>{balance.locked}</td></tr>)}</tbody></table> : <p>{text('没有可显示的官方余额。', 'No official balances to display.')}</p>}
        <p>{text('能力状态', 'Capability status')}: {status.capabilities.map(item => `${item.product} ${item.order_type}: ${item.state}`).join(' · ') || 'DISABLED'}</p>
        <p>{text('U 本位合约、组合单、杠杆和复杂算法保持禁用。', 'USD-M futures, order lists, leverage and advanced algorithms remain disabled.')}</p>
      </>}
    </div>
  </section>;
}
