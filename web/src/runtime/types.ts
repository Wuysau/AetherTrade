export type Range = '24H' | '7D' | '30D' | '90D';
export type Page = 'overview' | 'decisions' | 'positions' | 'strategies' | 'research' | 'risk';
export type Mode = 'AI' | 'RULE' | 'HYBRID';
export type SymbolName = 'BTCUSDT' | 'ETHUSDT' | 'SOLUSDT';
export type PaperAction = 'pause_new' | 'resume_new' | 'cancel_entries' | 'exit_owned';
export interface Session { username: string; csrf_token: string; environment: 'PAPER'; expires_at: string }
export interface Bootstrap { needs_setup: boolean; environment: 'PAPER'; version: string }
export interface Account {
  id: string; name: string; currency: 'USDT'; initial_cash: string; cash: string;
  equity: string; day_pnl: string; day_pnl_pct: string; exposure_pct: string;
  risk_used: string; risk_limit: string; revision: number; paused: boolean;
}
export interface Position {
  id: string; symbol: SymbolName; product: 'SPOT'; side: 'LONG'; quantity: string;
  average_price: string; mark_price: string | null; market_value: string | null;
  unrealized_pnl: string | null; strategy_id: string | null; owner: string;
  protection: 'UNCONFIGURED'; updated_at: string;
}
export interface Order {
  id: string; symbol: SymbolName; side: 'BUY' | 'SELL'; quantity: string;
  price: string; notional: string; fee: string; status: 'FILLED';
  strategy_id: string | null; created_at: string;
}
export interface Decision {
  id: string; symbol: string; action: string; source: string; summary: string;
  counter_evidence: string[]; invalidation: string; risk_status: string;
  created_at: string; next_review_at: string | null;
}
export interface StrategyInput { name: string; symbol: SymbolName; mode: Mode; budget: string; target_pct: string }
export interface Strategy extends StrategyInput {
  id: string; status: 'DRAFT' | 'PAUSED'; revision: number; created_at: string; updated_at: string;
}
export interface RuntimeEvent { id: string; kind: string; title: string; detail: string; created_at: string }
export interface Risk { max_position_pct: string; max_daily_loss: string; revision: number }
export interface Service { id: string; label: string; status: 'CONNECTED' | 'UNAVAILABLE' | 'NOT_CONFIGURED'; detail: string; updated_at: string | null }
export interface Quote { symbol: SymbolName; price: string; change_pct: string; as_of: string; source: string; stale: boolean }
export interface Market {
  source: string; status: 'CONNECTED' | 'UNAVAILABLE' | 'STALE';
  last_updated: string | null; error: string | null; quotes: Quote[];
}
export interface Dashboard {
  as_of: string; environment: 'PAPER'; account: Account;
  history: { at: string; equity: string }[]; positions: Position[]; orders: Order[];
  decisions: Decision[]; strategies: Strategy[]; events: RuntimeEvent[];
  risk: Risk; services: Service[]; market: Market;
  research: { total_return_pct: string; max_drawdown_pct: string; win_rate: string | null; trade_count: number; fees: string };
}
export interface PaperOrderInput { symbol: SymbolName; side: 'BUY' | 'SELL'; quantity: string; strategy_id: string | null }
export interface ActionReceipt { id: string; action: PaperAction; status: 'APPLIED'; affected_count: number; detail: string; created_at: string }
