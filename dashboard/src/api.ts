// Typed client for the Trading-Bot REST API.

export type Series = [number, number][];

export interface Position {
  symbol: string;
  qty: number;
  type?: string;
  entry_price: number;
  last_price?: number;
  value?: number;
  unrealized_pnl?: number;
  pnl_percentage?: number;
  bot_id?: string;
  bot?: string;
}

export interface Trade {
  id: string;
  bot_id?: string;
  symbol: string;
  side: string;
  qty: number;
  entry_price: number;
  exit_price: number;
  opened_at: number;
  closed_at: number;
  pnl: number;
  fees: number;
  return_pct: number;
  exit_reason: string;
  strategy?: string;
}

export interface OrderRow {
  id: string;
  symbol: string;
  side: string;
  type: string;
  role: string;
  qty: number;
  price: number | null;
  status: string;
  filled_qty: number;
  avg_fill_price: number;
  fee: number;
  tag: string;
  reject_reason: string;
  created_at: number;
}

export interface EventRow {
  bot_id: string;
  ts: number;
  level: string;
  kind: string;
  message: string;
}

export interface Decision {
  bot_id: string;
  ts: number;
  symbol: string;
  model: string;
  authority: string;
  consensus: number;
  llm_exposure: number | null;
  final_exposure: number;
  confidence: number | null;
  action: string;
  rationale: string;
  context: Record<string, unknown>;
  raw_response: string;
  latency_ms: number;
  error: string;
}

export interface BotStatus {
  id: string;
  name: string;
  status: string;
  error?: string;
  mode: string;
  strategy: string;
  symbols: string[];
  timeframe: string;
  capital?: number;
  equity?: number;
  balance?: number;
  pnl?: number;
  pnl_pct?: number;
  exposure?: number;
  positions?: Position[];
  open_orders?: OrderRow[];
  risk?: { state: string; reason: string };
  bars_processed?: number;
  last_bar_ts?: number | null;
  replay_progress?: number;
  fees_paid?: number;
  config?: BotConfig;
}

export interface BotConfig {
  id: string;
  name: string;
  strategy: string;
  symbols: string[];
  timeframe: string;
  mode: "paper" | "live" | "replay";
  capital: number;
  hp: Record<string, unknown>;
  exchange: { id: string; market_type: "spot" | "futures"; api_key_env?: string | null; secret_env?: string | null; password_env?: string | null; sandbox: boolean };
  costs: { fee_maker: number; fee_taker: number; slippage_bps: number; leverage: number; funding_rate_8h: number };
  risk: {
    max_position_pct: number;
    max_gross_exposure: number;
    max_open_positions: number;
    min_order_notional: number;
    max_price_deviation_pct: number;
    daily_loss_limit_pct: number;
    max_drawdown_pct: number;
    max_orders_per_minute: number;
    flatten_on_halt: boolean;
    protections: Record<string, unknown>[];
  };
  jev: JevSettings;
  replay: { exchange: string; start: string | null; speed: number; warmup_bars: number };
  warmup_bars: number;
  candle_delay_s: number;
  autostart: boolean;
}

export interface JevSettings {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key_env: string | null;
  authority: "advisory" | "veto" | "full";
  temperature: number;
  max_tokens: number;
  timeout_s: number;
  reasoning_effort: "low" | "medium" | "high";
  min_confidence: number;
  max_exposure: number;
  decide_every_bars: number;
  anonymize: boolean;
  analysts: string[];
}

export interface HyperParam {
  name: string;
  type: string;
  min?: number;
  max?: number;
  default: unknown;
  options?: unknown[];
}

export interface StrategyInfo {
  name: string;
  kind: "route" | "portfolio";
  timeframe: string;
  status: string;
  summary: string;
  description: string;
  hyperparameters: HyperParam[];
  supports_short: boolean;
  research: { key: string; verdict: string; sharpe: number; wf_oos_sharpe: number | null }[];
}

export interface Overview {
  bots_total: number;
  bots_running: number;
  equity: number;
  capital: number;
  pnl: number;
  pnl_pct: number;
  positions: Position[];
  risk_states: Record<string, string>;
  recent_trades: Trade[];
  recent_decisions: Decision[];
  recent_events: EventRow[];
  notifications: string[];
  bots: BotStatus[];
}

export interface LeaderRow {
  key: string;
  title: string;
  strategy: string;
  timeframe: string;
  verdict: string;
  cagr_pct: number;
  sharpe: number;
  sortino: number;
  max_drawdown_pct: number;
  calmar: number;
  total_trades: number;
  exposure_pct: number;
  wf_oos_sharpe: number | null;
  wf_oos_cagr_pct: number | null;
  wf_oos_max_drawdown_pct: number | null;
  dsr: number;
  pbo: number | null;
  sharpe_2x_costs: number;
  grid_share_sharpe_gt_0_5: number | null;
  benchmark_sharpe: number;
  benchmark_cagr_pct: number;
  benchmark_max_drawdown_pct: number;
  start: string;
  end: string;
}

export interface ResearchSummary {
  generated_at: string;
  studies: LeaderRow[];
  strategy_portfolio: { members: string[]; weights: string; metrics: Record<string, number>; equity: Series; correlation: Record<string, Record<string, number>> } | null;
  methodology: string;
}

export type Metrics = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export interface BacktestPayload {
  strategy: string;
  symbols: string[];
  timeframe: string;
  hp: Record<string, unknown>;
  metrics: Metrics;
  equity: Series;
  drawdown: Series;
  benchmark: Series | null;
  trades: Trade[];
  candles?: [number, number, number, number, number][];
  candles_timeframe?: string;
  markers?: { time: number; price: number; kind: "entry" | "exit"; side: string; pnl?: number }[];
  runtime_sec: number;
}

export interface BacktestRow {
  id: string;
  created_at: number;
  status: string;
  progress: number;
  request: Record<string, unknown>;
  summary: Record<string, number>;
  error: string;
  result?: BacktestPayload | null;
}

const TOKEN_KEY = "tradebot.token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(t: string) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: token lives for this session only */
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body = init?.body;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, headers: { ...headers, ...(init?.headers as Record<string, string>) }, body });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, json?: unknown) => api<T>(path, { method: "POST", json: json ?? {} });
export const put = <T,>(path: string, json: unknown) => api<T>(path, { method: "PUT", json });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });
