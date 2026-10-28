export type Range = '24H' | '7D' | '30D' | '90D';
export type Page = 'overview' | 'decisions' | 'positions' | 'strategies' | 'research' | 'risk';
export type Mode = 'AI' | 'RULE' | 'HYBRID';
export type SymbolName = 'BTCUSDT' | 'ETHUSDT' | 'SOLUSDT' | 'TSLABUSDT' | 'NVDABUSDT' | 'AAPLBUSDT';
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
  protection: string; updated_at: string;
}
export interface Order {
  id: string; symbol: SymbolName; side: 'BUY' | 'SELL'; quantity: string;
  price: string; notional: string; fee: string; status: 'FILLED' | 'PENDING' | 'SUBMITTED' | 'PARTIALLY_FILLED' | 'CANCEL_UNKNOWN' | 'EXPIRED' | 'REJECTED' | 'CANCELED' | 'UNKNOWN';
  filled_quantity?: string; leaves_quantity?: string; provenance?: string;
  strategy_id: string | null; created_at: string;
}
export interface Decision {
  id: string; symbol: string; action: string; source: string; summary: string;
  counter_evidence: string[]; invalidation: string; risk_status: string;
  created_at: string; next_review_at: string | null;
  strategy_id?: string | null; strategy_name?: string | null; execution_status?: string; execution_reason?: string | null; order_id?: string | null;
  analysis_mode?: AnalysisMode; consensus_status?: 'AGREED' | 'DISAGREED' | 'INCOMPLETE';
  model_profile_ids?: string[]; profile_revisions?: Record<string, number>; model_results?: ModelResult[];
  data_as_of?: string; snapshot_hash?: string; model_profile_id?: string; profile_name?: string; model?: string; config_revision?: number;
  model_profiles?: { profile_id: string; profile_name: string; model: string; config_revision: number }[];
}
export type AnalysisMode = 'single' | 'consensus';
export interface TokenUsage { input_tokens: number | null; output_tokens: number | null }
export interface ModelResult { profile_id: string; profile_name: string; model: string; config_revision: number; status: 'SUCCEEDED' | 'FAILED'; decision?: Partial<Decision>; error?: string; usage?: TokenUsage | null }
export interface AnalysisInput { symbol: SymbolName; analysis_mode: AnalysisMode; model_profile_ids: string[]; expected_revisions: Record<string, number>; acknowledge_cost: true }
export type StrategyStatus = 'DRAFT' | 'ACTIVE' | 'PAUSED';
export interface PriceRule { kind: 'PRICE_THRESHOLD'; operator: 'LTE' | 'GTE'; price: string; stop_loss_pct: string }
export interface StrategyInput { status?: StrategyStatus; rule?: PriceRule; acknowledge_paper_automation?: boolean; analysis_mode?: AnalysisMode; model_profile_ids?: string[]; analysis_interval_seconds?: number; auto_execute?: boolean; acknowledge_model_cost?: boolean; name: string; symbol: SymbolName; mode: Mode; budget: string; target_pct: string }
export interface Strategy extends StrategyInput {
  id: string; status: StrategyStatus; runtime_status?: string; next_analysis_at?: string | null; last_analysis_at?: string | null; last_analysis_error?: string | null; revision: number; created_at: string; updated_at: string;
}
export interface Notification { id: string; kind: string; title: string; message: string; severity: 'info' | 'success' | 'warning' | 'error'; created_at: string; read_at: string | null; strategy_id: string; decision_id: string | null; order_id: string | null }
export interface Notifications { unread_count: number; items: Notification[] }
export interface RuntimeEvent { id: string; kind: string; title: string; detail: string; created_at: string }
export interface Risk { max_position_pct: string; max_daily_loss: string; revision: number }
export interface Service { id: string; label: string; status: 'CONNECTED' | 'CONFIGURED' | 'UNAVAILABLE' | 'NOT_CONFIGURED'; detail: string; updated_at: string | null }
export interface Quote { symbol: SymbolName; price: string; change_pct: string; as_of: string; source: string; stale: boolean; open?: string; high?: string; low?: string; volume?: string; quote_volume?: string }
export type CandleInterval = '1m' | '5m' | '15m' | '1h' | '4h' | '1d';
export interface Candle { time: number; open: string; high: string; low: string; close: string; volume: string }
export interface MarketBook { bids: [string, string][]; asks: [string, string][]; as_of: string; stale: boolean }
export interface MarketTrade { id: string; price: string; quantity: string; side: 'BUY' | 'SELL'; as_of: string }
export interface MarketFields { provider: 'binance' | 'binance_compatible' | 'standard_json'; name: string; rest_url: string; ws_url: string; auth_scheme: 'none' | 'bearer' | 'x_api_key'; local_mode: boolean; enabled: boolean; poll_seconds: number; timeout_seconds: number }
export interface MarketConfig extends MarketFields { revision: number; has_api_key: boolean; key_available: boolean }
export interface MarketInput extends MarketFields { expected_revision: number; api_key?: string; clear_api_key?: boolean }
export interface CandleHistory { symbol: SymbolName; interval: CandleInterval; source_revision: number; candles: Candle[]; source: string }
export type BrowserMarketStatus = 'connecting' | 'streaming' | 'polling' | 'paused' | 'unavailable';
export interface Market {
  instance_id?: string; source_revision?: number; sequence?: number;
  transport?: { mode: 'websocket' | 'polling' | 'disabled'; status: 'CONNECTING' | 'LIVE' | 'RECONNECTING' | 'POLLING' | 'DISABLED'; last_event_at: string | null; last_error: string | null; reconnect_attempt: number };
  books?: Partial<Record<SymbolName, MarketBook>>; trades?: Partial<Record<SymbolName, MarketTrade[]>>;
  candles?: Partial<Record<SymbolName, Partial<Record<CandleInterval, Candle>>>>;
  source: string; status: 'CONNECTED' | 'PARTIAL' | 'UNAVAILABLE' | 'STALE';
  last_updated: string | null; error: string | null; quotes: Quote[];
  groups?: { id: 'crypto' | 'bstocks'; label: string; status: string; last_updated: string | null; error: string | null }[];
}
export interface Dashboard {
  as_of: string; environment: 'PAPER'; account: Account;
  history: { at: string; equity: string }[]; positions: Position[]; orders: Order[];
  decisions: Decision[]; strategies: Strategy[]; events: RuntimeEvent[];
  notifications?: Notifications; automation?: { enabled: boolean; active_strategies: number; auto_order_strategies: number; running_jobs: number };
  instruments?: Instrument[]; model_config?: ModelConfig; model_profiles?: ModelProfile[];
  risk: Risk; services: Service[]; market: Market;
  execution?: PaperExecution;
  risk_locks?: PaperRiskLock[];
  research: { total_return_pct: string; max_drawdown_pct: string; win_rate: string | null; trade_count: number; fees: string };
}

export interface PaperRiskLock { lock_id: string; cause: string; state: string; permission: string }
export interface PaperTarget {
  symbol: string; strategy_id: string | null; target_quantity: string;
  actual_quantity: string; in_flight_quantity: string;
  protection_state: string; protected_quantity: string; command_id?: string; reason?: string;
}
export interface PaperCommand {
  command_id: string; symbol: string; state: string; order_status: string;
  quantity: string; filled_quantity: string; leaves_quantity: string; reason?: string;
}
export interface PaperExecution {
  mode: string; simulation_notice: string; targets: PaperTarget[];
  commands: PaperCommand[]; risk_locks: PaperRiskLock[];
  protection_adjustments?: {cancel_command_id: string; stop_command_id: string; reduction_command_id: string | null;
    quantity: string; symbol: string; phase: string; protection_responsibility: string;
    invalidation_reason?: string; invalidated_actual_quantity?: string}[];
}
export interface PaperOrderInput { symbol: SymbolName; side: 'BUY' | 'SELL'; quantity: string; strategy_id: string | null }
export interface ActionReceipt { id: string; action: PaperAction; status: 'APPLIED'; affected_count: number; detail: string; created_at: string }

export interface Instrument { symbol: SymbolName; name: string; asset_class: 'CRYPTO' | 'BSTOCK'; base_asset: string; quote_asset: string; underlying_symbol: string | null }
export type ModelProtocol = 'openai_responses' | 'openai_chat' | 'anthropic';
export interface ModelFields { protocol: ModelProtocol; base_url: string; model: string; enabled: boolean; local_mode: boolean; max_output_tokens: number; timeout_seconds: number; anthropic_version: string }
export interface ModelConfig extends ModelFields { revision: number; has_api_key: boolean; key_available: boolean; connection_status: 'NOT_CONFIGURED' | 'CONFIGURED' | 'CONNECTED' | 'UNAVAILABLE'; last_test_at: string | null; last_error: string | null }
export interface ModelInput extends ModelFields { expected_revision: number; api_key?: string; clear_api_key?: boolean }
export interface ModelProfile extends ModelConfig { id: string; name: string }
export interface ModelProfileInput extends ModelFields { name: string; api_key?: string; clear_api_key?: boolean }
