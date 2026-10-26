import { MarketOrder, useMarketStream } from './marketlive';
import { profilesFromDashboard } from './modelprofiles';
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Bell, BrainCircuit, ChartNoAxesCombined, Check, ChevronRight, CircleHelp, Clock3, Info, LayoutGrid, LoaderCircle, LogOut, RefreshCw, ShieldCheck, SlidersHorizontal, UserRound, Wallet, X } from 'lucide-react';
import { ApiError, errorMessage, runtimeApi, type RuntimeApi } from './api';
import { positiveDecimal, time } from './format';
import { ActionDialog, OrderDialog, StrategyDialog } from './forms';
import { Decisions, Overview, Positions, Research, RiskPage, Strategies } from './pages';
import { Alert, Badge, Brand, Dialog, Empty, Loading } from './ui';
import type { Bootstrap, Dashboard, Mode, Page, PaperAction, PaperOrderInput, Range, Session, Strategy, Market } from './types';

const NAV = [
  { id: 'overview', label: '总览', title: '运行总览', Icon: LayoutGrid },
  { id: 'decisions', label: 'AI 决策', title: 'AI 决策', Icon: BrainCircuit },
  { id: 'positions', label: '持仓订单', title: '持仓订单', Icon: Wallet },
  { id: 'strategies', label: '策略中心', title: '策略中心', Icon: SlidersHorizontal },
  { id: 'research', label: '研究复盘', title: '研究复盘', Icon: ChartNoAxesCombined },
  { id: 'risk', label: '风控权限', title: '风控与权限', Icon: ShieldCheck },
] as const;
function pageFromHash(): Page { const value = window.location.hash.slice(1); return NAV.some(n => n.id === value) ? value as Page : 'overview'; }

function AuthScreen({ setup, api, onSession, notice }: { setup: boolean; api: RuntimeApi; onSession: (session: Session) => void; notice: string }) {
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [cash, setCash] = useState('100000');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (setup && !positiveDecimal(cash)) { setError('初始虚拟资金必须是大于 0 的十进制数。'); return; }
    setBusy(true);
    try { const session = setup ? await api.setup(username.trim(), password, cash) : await api.login(username.trim(), password); onSession(session); }
    catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }
  return <main className="auth-shell"><div className="auth-brand"><Brand /></div><div className="auth-layout"><div className="auth-context"><h1>把每一次决策，<br />放在可控的边界内。</h1><p>查看账户与行情，管理策略预算，追踪每一笔模拟成交。你的交易研究工作台，从这里开始。</p><div className="auth-features"><span><ChartNoAxesCombined className="icon" />真实公开行情</span><span><ShieldCheck className="icon" />明确风险边界</span><span><Wallet className="icon" />独立模拟账户</span></div><div className="auth-boundary"><span className="small-dot" />本地运行 · PAPER 环境<br /><span>资金为虚拟资金，不连接真实交易账户。</span></div></div><section className="auth-card panel"><div className="auth-card-heading"><h2>{setup ? '创建你的工作空间' : '登录工作空间'}</h2><p>{setup ? '首次使用，创建本地管理员与模拟账户。' : '使用本地管理员账户继续。'}</p></div><form onSubmit={submit} className="editor-form">{notice && <Alert>{notice}</Alert>}<label className="field"><span>用户名</span><input autoFocus required minLength={2} maxLength={64} autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} disabled={busy} placeholder="输入用户名" /></label><label className="field"><span>密码 {setup && <small>至少 10 个字符</small>}</span><input type="password" required minLength={setup ? 10 : undefined} maxLength={256} autoComplete={setup ? 'new-password' : 'current-password'} value={password} onChange={e => setPassword(e.target.value)} disabled={busy} placeholder={setup ? '设置本地登录密码' : '输入密码'} /></label>{setup && <label className="field"><span>初始虚拟资金 <small>USDT</small></span><input required inputMode="decimal" value={cash} onChange={e => setCash(e.target.value)} disabled={busy} /><span className="field-help">仅用于本地模拟交易，创建后作为账户初始资金记录。</span></label>}{error && <Alert error>{error}</Alert>}<button className="button primary auth-submit" disabled={busy}>{busy ? <LoaderCircle className="icon spin" /> : <ShieldCheck className="icon" />}{busy ? '正在连接…' : setup ? '创建并进入控制台' : '登录控制台'}</button><div className="auth-fineprint"><Info className="icon sm" />登录状态受到保护，退出后立即失效。</div></form></section></div><footer className="auth-footer">AetherTrade · 本地交易研究与模拟执行工作台</footer></main>;
}
export function RuntimeApp({ api = runtimeApi }: { api?: RuntimeApi }) {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null), [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const initialize = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const boot = await api.bootstrap(); setBootstrap(boot);
      if (!boot.needs_setup) {
        try { const current = await api.session(); api.setSession(current); setSession(current); }
        catch (err) { if (!(err instanceof ApiError && err.status === 401)) throw err; api.setSession(null); setSession(null); }
      }
    } catch (err) { setError(errorMessage(err)); } finally { setLoading(false); }
  }, [api]);
  useEffect(() => { void initialize(); }, [initialize]);
  useEffect(() => {
    if (!session) { api.onUnauthorized = null; return; }
    api.onUnauthorized = () => { api.setSession(null); setSession(null); setNotice('登录已过期，请重新登录。未提交的操作不会执行。'); };
    return () => { api.onUnauthorized = null; };
  }, [session, api]);
  function authenticated(current: Session) { api.setSession(current); setSession(current); setBootstrap(previous => previous ? { ...previous, needs_setup: false } : previous); setNotice(''); }
  if (loading) return <div className="startup"><Brand /><Loading text="正在连接本地工作空间…" /></div>;
  if (error || !bootstrap) return <div className="startup"><Brand /><div className="startup-error"><Alert error onRetry={() => void initialize()}>{error || '无法读取服务配置。'}</Alert><p>请确认 AetherTrade 服务已启动，并通过启动脚本提供的地址访问。</p></div></div>;
  if (!session) return <AuthScreen setup={bootstrap.needs_setup} api={api} onSession={authenticated} notice={notice} />;
  return <Console key={session.csrf_token} api={api} session={session} onLogout={() => { api.setSession(null); setSession(null); setNotice('你已安全退出。'); }} />;
}

type Modal = { kind: 'strategy'; mode: Mode; existing?: Strategy } | { kind: 'order'; initial?: Partial<PaperOrderInput> } | { kind: 'action'; action: PaperAction } | { kind: 'notifications' } | { kind: 'activity' } | { kind: 'account' } | null;
function Console({ api, session, onLogout }: { api: RuntimeApi; session: Session; onLogout: () => void }) {
  const [page, setPage] = useState<Page>(pageFromHash), [range, setRange] = useState<Range>('30D');
  const [data, setData] = useState<Dashboard | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false), [exporting, setExporting] = useState(false), [loggingOut, setLoggingOut] = useState(false);
  const [modal, setModal] = useState<Modal>(null), [toast, setToast] = useState(''), [operationError, setOperationError] = useState('');
  const [selectedDecisionId, setSelectedDecisionId] = useState<string>(), [reading, setReading] = useState(false);
  const [decisionSelectionVersion, setDecisionSelectionVersion] = useState(0);
  const refreshRef = useRef<() => void>(() => {});
  const marketOrder = useRef(new MarketOrder());
  const receiveMarket = useCallback((market: Market) => {
    const accepted = marketOrder.current.accept(market, true);
    setData(previous => previous ? { ...previous, market: accepted } : previous);
  }, []);
  const marketBrowserStatus = useMarketStream(api, Boolean(data?.market.instance_id) && !loggingOut, receiveMarket);
  useEffect(() => {
    const handle = () => setPage(pageFromHash()); window.addEventListener('hashchange', handle); return () => window.removeEventListener('hashchange', handle);
  }, []);
  useEffect(() => { document.title = `${NAV.find(n => n.id === page)?.title} · AetherTrade`; }, [page]);
  useEffect(() => {
    let disposed = false, fetching = false, queued = false;
    const controller = new AbortController();
    async function sync(showLoading = false) {
      if (fetching) { queued = true; return; }
      if (disposed) return;
      fetching = true; if (showLoading) setLoading(true);
      try { const next = await api.dashboard(range, controller.signal); if (!disposed) { setData({ ...next, market: marketOrder.current.accept(next.market) }); setError(''); } }
      catch (err) { if (!disposed) setError(errorMessage(err)); }
      finally { fetching = false; if (!disposed) { setLoading(false); if (queued) { queued = false; void sync(); } } }
    }
    refreshRef.current = () => void sync(true);
    void sync(true);
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void sync(); }, 10_000);
    const onFocus = () => void sync(); window.addEventListener('focus', onFocus);
    return () => { disposed = true; controller.abort(); window.clearInterval(timer); window.removeEventListener('focus', onFocus); };
  }, [api, range]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 7000); return () => window.clearTimeout(timer); }, [toast]);
  function navigate(next: Page) {
    window.location.hash = next; setPage(next); window.scrollTo({ top: 0, behavior: 'instant' });
    window.requestAnimationFrame(() => document.getElementById('page-heading')?.focus({ preventScroll: true }));
  }
  function saved(message: string) { setModal(null); setToast(message); refreshRef.current(); }
  async function readNotifications(ids?: string[]) {
    setReading(true); setOperationError('');
    try { const notifications = await api.markNotificationsRead(ids ? { ids } : { all: true }); setData(previous => previous ? { ...previous, notifications } : previous); }
    catch (err) { setOperationError(errorMessage(err)); } finally { setReading(false); }
  }
  function openDecision(id: string) { setSelectedDecisionId(id); setDecisionSelectionVersion(version => version + 1); setModal(null); navigate('decisions'); }
  async function refreshMarket() {
    setRefreshing(true); setOperationError('');
    try { const market = await api.refreshMarket(); receiveMarket(market); setToast(market.status === 'CONNECTED' ? '行情已更新。' : market.status === 'PARTIAL' ? '部分市场已更新；每笔订单仍校验所选标的报价。' : '行情源暂不可用；缓存状态已更新。'); refreshRef.current(); }
    catch (err) { setOperationError(errorMessage(err)); } finally { setRefreshing(false); }
  }
  async function exportResearch() {
    setExporting(true); setOperationError('');
    try { const blob = await api.exportResearch(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `aethertrade-paper-${new Date().toISOString().slice(0, 10)}.txt`; document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); setToast('复盘摘要已下载，内容来自已保存的模拟账户记录。'); }
    catch (err) { setOperationError(errorMessage(err)); } finally { setExporting(false); }
  }
  async function logout() {
    setLoggingOut(true); setOperationError('');
    try { await api.logout(); onLogout(); } catch (err) { setOperationError(errorMessage(err)); setModal(null); } finally { setLoggingOut(false); }
  }
  const current = NAV.find(n => n.id === page)!;
  const props = data ? { marketBrowserStatus, onMarket: receiveMarket, selectedDecisionId, decisionSelectionVersion, data, range, onRange: setRange, onNavigate: navigate, onOrder: (initial?: Partial<PaperOrderInput>) => setModal({ kind: 'order', initial }), onAction: (action: PaperAction) => setModal({ kind: 'action', action }), onStrategy: (mode: Mode, existing?: Strategy) => setModal({ kind: 'strategy', mode, existing }), onRefreshMarket: () => void refreshMarket(), refreshing, loading, offline: Boolean(error), api, onSaved: saved, onExport: () => void exportResearch(), exporting } : null;
  return <div className="app"><a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>跳到主内容</a><aside className="sidebar"><Brand /><div className="nav-caption">工作空间</div><nav className="nav-items" aria-label="主导航">{NAV.map(({ id, label, Icon }) => <button key={id} className={`nav-btn ${page === id ? 'active' : ''}`} onClick={() => navigate(id)} aria-current={page === id ? 'page' : undefined}><Icon className="icon" /><span className="nav-name">{label}</span></button>)}</nav><div className="sidebar-bottom"><div className="sidebar-division" /><div className="env-side"><div className="en-title">连接环境</div><strong><span className={`small-dot ${error ? 'unavailable' : ''}`} />本地 PAPER 账户</strong><div className="sidebar-env-detail">{error ? '服务连接异常' : '持久化存储 · 公开行情'}</div></div><div className="sidebar-note">虚拟资金 / PAPER ACCOUNT<br />无真实交易所写权限</div></div></aside><div className="main"><header className="topbar"><div className="crumb"><span>工作空间</span><ChevronRight className="icon sm" /><strong>{current.title}</strong></div><div className="top-tools"><span className="demo-pill">PAPER</span><span className="tclock">{data ? time(data.as_of) : '--:--:--'} UTC+8</span><span className="top-divider" /><button className="top-icon activity-button" aria-label={`查看通知，${data?.notifications?.unread_count ?? 0} 条未读`} onClick={() => setModal({ kind: 'notifications' })}><Bell className="icon" />{Boolean(data?.notifications?.unread_count) && <span className="notification-count">{data!.notifications!.unread_count > 99 ? '99+' : data!.notifications!.unread_count}</span>}</button><button className="account-button" aria-label={`账户 ${session.username}`} onClick={() => setModal({ kind: 'account' })}><span className="avatar">{session.username.slice(0, 2).toUpperCase()}</span><UserRound className="icon mobile-user" /></button></div></header><main className="content" id="main-content" tabIndex={-1}>{error && <div className="global-message"><Alert error onRetry={() => refreshRef.current()}>{error}{data ? ` 当前显示 ${time(data.as_of)} 的最后一次快照。` : ''}</Alert></div>}{operationError && <div className="global-message"><Alert error>{operationError}</Alert><button className="message-dismiss top-icon" aria-label="关闭错误提示" onClick={() => setOperationError('')}><X className="icon" /></button></div>}{props ? <div className="runtime-view" key={page}>{page === 'overview' ? <Overview {...props} /> : page === 'decisions' ? <Decisions {...props} /> : page === 'positions' ? <Positions {...props} /> : page === 'strategies' ? <Strategies {...props} /> : page === 'research' ? <Research {...props} /> : <RiskPage {...props} />}</div> : loading ? <div className="dashboard-loading"><Loading /><div className="skeleton-metrics">{[1, 2, 3, 4].map(i => <div key={i} />)}</div><div className="skeleton-panels"><div /><div /></div></div> : <Empty title="账户数据暂不可用" text="请检查本地服务连接后重试。"><button className="button" onClick={() => refreshRef.current()}><RefreshCw className="icon" />重新连接</button></Empty>}<footer className="footer-tip"><Info className="icon sm" /><span>AetherTrade · 本地 PAPER 账户使用虚拟资金；公开行情仅供读取。AI 策略可定期分析并按授权自动 PAPER 下单；bStocks 为币安代币化股票现货。</span></footer></main></div>
    {toast && <div className="toast visible" role="status" aria-live="polite"><Check className="icon" /><span>{toast}</span><button className="top-icon" aria-label="关闭通知" onClick={() => setToast('')}><X className="icon sm" /></button></div>}
    {modal?.kind === 'strategy' && <StrategyDialog api={api} profiles={data ? profilesFromDashboard(data) : []} mode={modal.mode} existing={modal.existing} onClose={() => setModal(null)} onSaved={saved} />}
    {modal?.kind === 'order' && data && <OrderDialog api={api} data={data} initial={modal.initial} onClose={() => setModal(null)} onSaved={saved} />}
    {modal?.kind === 'action' && data && <ActionDialog api={api} data={data} action={modal.action} onClose={() => setModal(null)} onSaved={saved} />}
    {modal?.kind === 'notifications' && <Dialog title="AI 结果通知" onClose={() => setModal(null)} wide><div className="action-strip"><button className="button small" disabled={reading || !data?.notifications?.unread_count} onClick={() => void readNotifications()}>全部标为已读</button><button className="button link" onClick={() => setModal({ kind: 'activity' })}>查看近期活动</button></div>{data?.notifications?.items.length ? <div className="activity-list notification-list">{data.notifications.items.map(item => <div key={item.id}><div className="flex between gap12"><strong>{item.title}</strong><Badge kind={item.severity === 'error' ? 'fail' : item.severity === 'success' ? 'pass' : 'wait'}>{item.read_at ? '已读' : '未读'}</Badge></div><p>{item.message}</p><time>{time(item.created_at, true)} UTC+8</time><div className="action-strip">{item.decision_id && <button className="button small" disabled={reading} onClick={() => { void readNotifications([item.id]); openDecision(item.decision_id!); }}>查看关联决策</button>}{!item.read_at && <button className="button link" disabled={reading} onClick={() => void readNotifications([item.id])}>标为已读</button>}{item.order_id && <span className="quiet">PAPER 回执 {item.order_id}</span>}</div></div>)}</div> : <Empty title="暂无 AI 通知" text="自动分析结果、等待状态与执行回执将在这里保留。" compact />}</Dialog>}
    {modal?.kind === 'activity' && <Dialog title="近期活动" onClose={() => setModal(null)} wide>{data?.events.length ? <div className="activity-list">{data.events.slice(0, 10).map(event => <div key={event.id}><div className="flex between gap12"><strong>{event.title}</strong><time>{time(event.created_at)}</time></div><p>{event.detail}</p></div>)}</div> : <Empty title="暂无活动记录" text="设置变更与模拟执行的回执将在这里显示。" compact />}</Dialog>}
    {modal?.kind === 'account' && <Dialog title="账户与会话" onClose={() => setModal(null)} busy={loggingOut}><div className="account-detail"><div className="account-identity"><UserRound className="icon" /><strong>{session.username}</strong><Badge>PAPER</Badge></div><p>本地管理员账户</p><div className="session-expiry"><Clock3 className="icon sm" />会话到期 {time(session.expires_at, true)} UTC+8</div><button className="button" onClick={() => void logout()} disabled={loggingOut}><LogOut className="icon" />{loggingOut ? '退出中…' : '安全退出登录'}</button></div></Dialog>}
  </div>;
}
