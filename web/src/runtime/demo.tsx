import { useEffect, useRef, useState, type FormEvent } from 'react';
import { RuntimeApi, errorMessage } from './api';
import { useLocale } from './i18n';
import { Alert, Loading } from './ui';
import { DemoRuntimeControls } from './demo_controls';

interface DemoStatus {
  environment: 'BINANCE_DEMO'; product: string; state: string; execution_authorized: false;
  credential_storage: string; endpoint: string; budget_cap_usdt: string;
  balances: { asset: string; free: string; locked: string }[];
  fingerprint?: string; checked_at?: number; snapshot_stale?: boolean; expires_at?: number; connection_epoch?: string;
  open_order_count?: number; blockers: string[];
  capabilities: { product: string; order_type: string; state: string; enabled: boolean }[];
}

/** Dedicated venue panel. It never relabels PAPER balances, orders or PnL. */
export function Demo({ api, active = true }: { api: RuntimeApi; active?: boolean }) {
  const { locale } = useLocale();
  const en = locale === 'en';
  const text = (zh: string, english: string) => en ? english : zh;
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [key, setKey] = useState(''), [secret, setSecret] = useState('');
  const [controlsOpen, setControlsOpen] = useState(false);
  const [ack, setAck] = useState(false), [busy, setBusy] = useState(false);
  const [operationError, setOperationError] = useState(''), [pollError, setPollError] = useState('');
  const [uncertain, setUncertain] = useState(false), [now, setNow] = useState(() => Date.now());
  const [statusRequestedAt, setStatusRequestedAt] = useState<number | null>(null);
  const busyRef = useRef(false), revision = useRef(0), operationRevision = useRef(0), mounted = useRef(true);
  const refreshRef = useRef<() => void>(() => {}), previousActive = useRef(active);
  useEffect(() => {
    mounted.current = true; setBusy(false);
    const controller = new AbortController();
    ++revision.current;
    let polling = false, queued = false;
    async function refreshStatus() {
      if (busyRef.current || polling) { queued = true; return; }
      queued = false; polling = true;
      const version = revision.current, requestedAt = Date.now();
      try {
        const value = await api.request<DemoStatus>('/demo/status', { signal: controller.signal });
        if (mounted.current && revision.current === version) { setStatus(value); setPollError(''); setUncertain(false); setStatusRequestedAt(requestedAt); setNow(Date.now()); }
      } catch (err) {
        if (!controller.signal.aborted && mounted.current && revision.current === version) { setPollError(errorMessage(err)); setUncertain(true); setNow(Date.now()); }
      } finally {
        polling = false;
        if (queued && !busyRef.current && !controller.signal.aborted) { queued = false; void refreshStatus(); }
      }
    }
    refreshRef.current = () => void refreshStatus();
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 10_000);
    const clock = window.setInterval(() => setNow(Date.now()), 1_000);
    const resume = () => {
      setNow(Date.now());
      if (document.visibilityState === 'visible') { ++revision.current; setUncertain(true); void refreshStatus(); }
    };
    window.addEventListener('focus', resume); document.addEventListener('visibilitychange', resume);
    return () => { mounted.current = false; ++revision.current; ++operationRevision.current; busyRef.current = false; controller.abort(); window.clearInterval(timer); window.clearInterval(clock); window.removeEventListener('focus', resume); document.removeEventListener('visibilitychange', resume); };
  }, [api]);
  useEffect(() => {
    if (!active) { setKey(''); setSecret(''); setAck(false); }
    else if (!previousActive.current) { ++revision.current; setUncertain(true); setNow(Date.now()); refreshRef.current(); }
    previousActive.current = active;
  }, [active]);
  const epochValue = status?.connection_epoch;
  const connectionEpoch = typeof epochValue === 'string' && /^[A-Za-z0-9_-]{16,128}$/.test(epochValue) ? epochValue : null;
  async function connect(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current || !ack || !key || !secret || !connectionEpoch) return;
    busyRef.current = true; setBusy(true); setOperationError(''); setUncertain(true);
    const version = ++operationRevision.current; ++revision.current;
    const body = JSON.stringify({ api_key: key, api_secret: secret, acknowledge_demo_only: true, connection_epoch: connectionEpoch });
    // Clear the controls before awaiting a request. Never use browser storage.
    setKey(''); setSecret(''); setAck(false);
    try {
      const value = await api.request<DemoStatus>('/demo/connect', { method: 'POST', body }, 60_000);
      if (mounted.current && version === operationRevision.current) { setStatus(value); setStatusRequestedAt(Date.now()); setNow(Date.now()); }
    } catch (err) {
      if (mounted.current && version === operationRevision.current) { setOperationError(errorMessage(err)); setStatus(null); setStatusRequestedAt(null); }
    } finally {
      if (version === operationRevision.current) {
        busyRef.current = false;
        if (mounted.current) { setBusy(false); refreshRef.current(); }
      }
    }
  }
  async function disconnect() {
    if (busyRef.current || !connectionEpoch) return;
    busyRef.current = true; setBusy(true); setOperationError(''); setUncertain(true); setKey(''); setSecret(''); setAck(false);
    const version = ++operationRevision.current; ++revision.current;
    try {
      const value = await api.request<DemoStatus>('/demo/disconnect', { method: 'POST', body: JSON.stringify({ connection_epoch: connectionEpoch }) });
      if (mounted.current && version === operationRevision.current) { setStatus(value); setStatusRequestedAt(Date.now()); setNow(Date.now()); }
    } catch (err) { if (mounted.current && version === operationRevision.current) setOperationError(errorMessage(err)); }
    finally { if (version === operationRevision.current) { busyRef.current = false; if (mounted.current) { setBusy(false); refreshRef.current(); } } }
  }
  const timestamp = status?.checked_at;
  const checkedAt = typeof timestamp === 'number' && timestamp > 0 && Number.isFinite(new Date(timestamp * 1000).getTime()) ? timestamp : null;
  const expiry = status?.expires_at;
  const expiresAt = typeof expiry === 'number' && expiry > 0 && Number.isFinite(new Date(expiry * 1000).getTime()) ? expiry : null;
  const age = checkedAt === null ? null : now - checkedAt * 1000;
  const statusAge = statusRequestedAt === null ? null : now - statusRequestedAt;
  const stale = (status !== null && (statusAge === null || statusAge > 15_000 || statusAge < -5_000)) || Boolean(status?.snapshot_stale) || (age !== null && (age > 15_000 || age < -5_000)) ||
    (status?.state === 'READ_ONLY' && (age === null || expiresAt === null || now >= expiresAt * 1000));
  const unverified = uncertain || (active && !previousActive.current) || Boolean(pollError) || Boolean(operationError) || stale;
  return <section className="demo-page">
    <header className="page-heading"><div><p className="eyebrow">BINANCE DEMO · SPOT</p><h1 id="demo-page-heading" tabIndex={-1}>{text('币安 Demo 连接', 'Binance Demo connection')}</h1><p>{text('独立官方模拟账户。不会使用本地 PAPER 余额，也不会连接实盘。', 'An independent official simulated account. PAPER balances are separate; production trading is not connected.')}</p></div></header>
    <Alert>{text('连接验证仅读取账户。AI 模拟交易需要独立授权，并且必须通过所有账户、运行时和场所资格门禁。', 'Connection verification only reads the account. AI Demo trading requires separate authorization and all account, runtime, and venue qualification gates.')}</Alert>
    <div className="panel" style={{ padding: '24px', marginTop: '20px' }}>
      <h2>{text('用户填入专用 API Key', 'Enter your dedicated Demo API key')}</h2>
      <p>{text('仅支持 Demo HMAC Key。凭据只保留在服务进程内存中，最多 15 分钟，退出登录或移除后失效。不写入数据库、浏览器存储或模型上下文。', 'Demo HMAC keys only. Credentials remain in server memory for at most 15 minutes, until logout or removal. They are never saved to the database, browser storage or model context.')}</p>
      <form className="editor-form" onSubmit={connect} autoComplete="off">
        <label className="field"><span>Demo API Key</span><input type="password" autoComplete="off" spellCheck={false} required minLength={16} maxLength={256} value={key} onChange={e => setKey(e.target.value)} disabled={busy} /></label>
        <label className="field"><span>Demo API Secret</span><input type="password" autoComplete="new-password" spellCheck={false} required minLength={16} maxLength={256} value={secret} onChange={e => setSecret(e.target.value)} disabled={busy} /></label>
        <label><input type="checkbox" checked={ack} disabled={busy} onChange={e => setAck(e.target.checked)} /> {text('确认这是 Demo 专用凭据，并同意向官方 Demo 端点发送只读查询。不要填入实盘密钥。', 'I confirm these are Demo-only credentials and authorize read-only queries to the official Demo endpoint. Do not enter production keys.')}</label>
        <div className="form-actions"><button className="button primary" type="submit" disabled={busy || !ack || !key || !secret || !connectionEpoch}>{busy ? text('正在处理…', 'Working…') : text('验证只读连接', 'Verify read-only connection')}</button><button className="button" type="button" disabled={busy || !connectionEpoch} onClick={() => void disconnect()}>{text('移除本次凭据', 'Remove session credentials')}</button></div>
      </form>
      {!connectionEpoch && <p className="field-help">{text('请先等待连接状态加载，再验证凭据。', 'Wait for connection status before verifying credentials.')}</p>}
      <p className="field-help">{text('移除凭据不会取消交易所挂单。AI 策略需要单独授权，可能提交真实 Demo 模拟订单。', 'Removing credentials does not cancel exchange orders. AI strategies require separate authorization and may submit orders on the real Demo venue.')}</p>
      {operationError && <Alert error>{operationError}</Alert>}
      {pollError && <Alert error>{pollError}</Alert>}
    </div>
    <div className="panel" style={{ padding: '24px', marginTop: '20px' }} aria-live="polite">
      <h2>{text('只读连接状态', 'Read-only connection status')}</h2>
      {!status ? busy ? <Loading /> : <p>{text('尚无已验证快照。', 'No verified snapshot.')}</p> : <>
        <p>{unverified && <strong>{text('上次快照（当前状态未验证）', 'Last snapshot (current status unverified)')}: </strong>}<strong>{status.state}</strong> · {status.endpoint}</p>
        {status.fingerprint && <p>{text('密钥指纹', 'Key fingerprint')}: {status.fingerprint}</p>}
        {checkedAt !== null && <p>{text('快照时间', 'Snapshot time')}: {new Date(checkedAt * 1000).toISOString()} · {text('仅供观察，不构成交易准入', 'For observation only, not execution admission')}</p>}
        {unverified && <Alert>{text('显示的是上次快照，不能证明当前连接或订单状态。请检查操作错误并重新验证，或等待状态恢复。', 'This is the last snapshot and does not establish the current connection or order status. Review any operation error and verify credentials again, or wait for status recovery.')}</Alert>}
        <p>{text('项目预算上限', 'Project budget ceiling')}: {status.budget_cap_usdt} USDT · {text('不是官方 Demo 余额', 'Not the official Demo balance')}</p>
        {status.balances.length > 0 ? <table className="data-table"><thead><tr><th>{text('资产', 'Asset')}</th><th>{text('官方可用余额', 'Official free balance')}</th><th>{text('官方冻结余额', 'Official locked balance')}</th></tr></thead><tbody>{status.balances.map(balance => <tr key={balance.asset}><td>{balance.asset}</td><td>{balance.free}</td><td>{balance.locked}</td></tr>)}</tbody></table> : <p>{text('没有可显示的官方余额。', 'No official balances to display.')}</p>}
        <p>{text('能力状态', 'Capability status')}: {status.capabilities.map(item => `${item.product} ${item.order_type}: ${item.state}`).join(' · ') || 'DISABLED'}</p>
        <p>{text('U 本位合约、组合单、杠杆和复杂算法保持禁用。', 'USD-M futures, order lists, leverage and advanced algorithms remain disabled.')}</p>
      </>}
    </div>
    <div className="form-actions" style={{marginTop:'20px'}}><button className="button" type="button" aria-expanded={controlsOpen} onClick={() => setControlsOpen(value => !value)}>{text('AI 模拟交易控制', 'AI Demo trading controls')}</button></div>
    {controlsOpen && <DemoRuntimeControls api={api} connectionGeneration={connectionEpoch ?? ""} connectionBusy={busy} />}
  </section>;
}
