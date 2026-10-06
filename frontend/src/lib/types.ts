// TypeScript mirrors of the FastAPI payloads (backend/app/api/routes/*.py, backend/engine/*).
// Fields the backend may omit or null are typed optional/nullable on purpose.

export type Direction = "LONG" | "SHORT";
export type SetupStatus = "VALID" | "NO_TRADE" | "NO_SIGNAL";
export type Pair<T = number> = [T, T];

export interface DataMeta {
  source?: string;
  is_sample?: boolean;
  last_bar?: string | null;
  fetched_at?: string | null;
  lag_days?: number | null;
  delayed?: boolean;
  error?: string | null;
}

// ---------------------------------------------------------------- auth
export interface TokenOut {
  access_token: string;
  token_type: string;
  expires_in: number;
}
export interface User {
  id: number;
  email: string;
  full_name: string;
  role: string;
  permissions: string[];
  totp_enabled: boolean;
}
export interface TotpSetup {
  secret: string;
  otpauth_uri: string;
}

// ---------------------------------------------------------------- markets
export interface Regime {
  as_of: string;
  regime: string;
  family?: string;
  volatility: string;
  long_favorability?: number;
  short_favorability?: number;
  reasons: string[];
}
export interface IndexCard {
  symbol: string;
  name: string;
  price: number;
  change_1d_pct: number | null;
  change_1w_pct: number | null;
  trend: string;
  realized_vol_20d_pct: number | null;
  volume?: number;
  as_of: string;
  sparkline: number[];
  data?: DataMeta;
  currency?: string;
  asset_class?: string;
}
export interface Breadth {
  as_of: string;
  pct_above_20dma: number | null;
  pct_above_50dma: number | null;
  pct_above_200dma: number | null;
  advances: number;
  declines: number;
  ad_ratio: number | null;
  new_highs: number;
  new_lows: number;
  up_volume_pct: number | null;
  pct_above_50dma_change_5d?: number | null;
  members: number;
  history: [string, number][];
  is_sample?: boolean;
  snapshot_created_at?: string;
}
export interface ScanSummary {
  market_message: string | null;
  valid: number;
  no_trade: number;
  candidates_evaluated: number;
  instruments_scanned: number;
  as_of: string;
  is_sample?: boolean;
  stale_instruments?: string[];
  snapshot_created_at?: string;
}
export interface Overview {
  indices: IndexCard[];
  regime: Regime | null;
  is_sample?: boolean;
  snapshot_created_at?: string;
  breadth: Breadth | null;
  scan: ScanSummary | null;
  coverage?: Record<string, string>;
  profile?: unknown;
  btc_dominance_pct?: number | null;
  btc_dominance_basis?: string;
  stablecoin_flows?: { available: boolean; note: string };
  disclaimer: string;
}
export interface MarketInfo {
  id: string;
  name: string;
  group: string;
  asset_class: string;
  calendar: "weekdays" | "24x7" | string;
  benchmark: string;
  provider: string;
  last_scan: { as_of: string; finished_at: string | null; valid: number | null; is_sample: boolean | null } | null;
  /** true when the market's configured provider is the synthetic SAMPLE provider (not a data flag: does not raise the banner) */
  sample_provider?: boolean;
}
export interface InrBlock {
  available?: boolean;
  note: string;
  rate?: number;
  from?: string;
  via?: string[];
  rate_as_of?: string;
  is_sample?: boolean;
  price_inr?: number;
  risk_per_unit_inr?: number;
  reward_t1_per_unit_inr?: number;
  reward_t2_per_unit_inr?: number;
  example?: { risk_budget_inr: number; units: number; position_value_inr: number; reward_t2_inr: number };
}
export interface DerivativesBlock {
  available: boolean;
  as_of?: string;
  funding?: { latest_pct_8h: number | null; avg_7d_pct_8h: number | null; annualised_pct: number | null; state: string } | null;
  open_interest?: { latest: number | null; change_1d_pct: number | null; change_7d_pct: number | null; price_change_7d_pct: number | null; interpretation: string } | null;
  long_short_ratio?: { latest: number | null; state: string; note: string } | null;
  liquidations?: { available: boolean; note: string };
  note?: string;
  checks?: Check[];
}
export interface MarketExtras {
  inr?: InrBlock;
  derivatives?: DerivativesBlock;
  market_cap_usd?: number | null;
  spread_bps?: number | null;
  exchange?: string;
}
export interface SectorRow {
  sector: string;
  members: number;
  ret20_pct: number | null;
  ret63_pct: number | null;
  relative_strength_63d: number | null;
  pct_above_50dma: number | null;
  volume_trend: number | null;
  pct_uptrend: number | null;
  score: number | null;
  rank: number;
}
export interface Sectors {
  sectors: SectorRow[];
  as_of: string;
  is_sample?: boolean;
  note: string;
}

// ---------------------------------------------------------------- setups
export interface Components {
  trend: number | null;
  momentum: number | null;
  volume: number | null;
  price_action: number | null;
  structure: number | null;
  fundamental: number | null;
  volatility: number | null;
  regime: number | null;
  risk_reward: number | null;
  [k: string]: number | null;
}
export interface ProbabilitySummary {
  t1_hit_rate: number | null;
  t2_hit_rate: number | null;
  stop_rate: number | null;
  sample_size: number | null;
  backtest_period: Pair<string> | null;
  conditioning: string | null;
  t1_ci95: Pair | null;
}
export interface Probability extends Partial<ProbabilitySummary> {
  available?: boolean;
  sufficient?: boolean;
  reason?: string;
  min_sample?: number;
  regime_family?: string | null;
  score_bucket?: string;
  market?: string;
  timeframe?: string;
  symbols_covered?: number;
  definitions?: Record<string, string>;
  t1_hits?: number;
  t2_hits?: number;
  stop_hits?: number;
  neither?: number;
  t2_ci95?: Pair | null;
  stop_ci95?: Pair | null;
  neither_rate?: number | null;
  avg_return_pct?: number | null;
  avg_win_pct?: number | null;
  avg_loss_pct?: number | null;
  expectancy_r?: number | null;
  avg_holding_bars?: number | null;
}
export interface StrategyPublic {
  id: string;
  name: string;
  direction: Direction;
  setup_type: string;
  description: string;
  conditions: string[];
  entry_rule: string;
  stop_rule: string;
  target_rule: string;
  max_hold_bars: number;
  timeframe: string;
  notes?: string;
}
export interface Check {
  name: string;
  passed: boolean;
  severity: "block" | "warn";
  detail: string;
}
export interface Explanation {
  trigger?: string;
  conditions_met?: string[];
  patterns: string[];
  agreeing: string[];
  disagreeing: string[];
  risk_factors: string[];
  invalidation: string[];
  support: number[];
  resistance: number[];
  historical_basis: string;
}
export interface MTF {
  timeframes: Record<string, string>;
  alignment: string;
  priority?: string[];
  note?: string;
}
export interface HistoricalExample {
  symbol: string;
  signal_date: string;
  entry: number;
  stop: number;
  t1: number;
  t2: number;
  exit_date: string;
  exit_reason: string;
  t1_hit: boolean;
  t2_hit: boolean;
  stop_hit: boolean;
  net_return_pct: number;
  bars_held: number;
  regime: string | null;
}
export interface SetupSummary extends MarketExtras {
  id: number;
  symbol: string;
  name: string | null;
  sector: string | null;
  market: string;
  currency: string | null;
  direction: Direction;
  status: SetupStatus;
  as_of: string;
  setup_type: string;
  strategy_id: string;
  strategy_name: string;
  current_price: number;
  entry_zone: Pair;
  stop: number;
  targets: number[];
  risk_pct: number;
  reward_pct_t2: number;
  rr_t1: number;
  rr_t2: number;
  score: number;
  score_label: string;
  components: Components;
  probability: ProbabilitySummary;
  expected_holding_days: number | null;
  reasons: string[];
  risk_factors: string[];
  blocking_checks: string[];
  data: DataMeta;
}
export interface SetupDetail extends Omit<MarketExtras, "exchange"> {
  id?: number;
  signal_id?: number | null;
  symbol: string;
  name: string;
  market: string;
  exchange: string;
  currency: string;
  sector: string | null;
  timeframe: string;
  as_of: string;
  generated_at: string;
  engine_version: string;
  status: SetupStatus;
  strategy: StrategyPublic;
  setup_type: string;
  direction: Direction;
  current_price: number;
  entry_zone: Pair;
  entry_method: string;
  stop: number;
  stop_method: string;
  targets: number[];
  target_methods: string[];
  risk_pct: number;
  reward_pct_t1: number;
  reward_pct_t2: number;
  rr_t1: number;
  rr_t2: number;
  atr: number;
  score: number;
  score_label: string;
  components: Components;
  score_notes: string[];
  analysis_mode: string;
  trend: string;
  momentum: string;
  volume: string;
  market_regime: Regime | null;
  mtf: MTF;
  probability: Probability;
  expected_holding_days: number | null;
  historical_examples: HistoricalExample[];
  checks: Check[];
  explanation: Explanation;
  data: DataMeta;
  indicators: Record<string, number | null>;
  disclaimer: string;
  outcome?: {
    status: string;
    t1_hit: boolean | null;
    t2_hit: boolean | null;
    stop_hit: boolean | null;
    exit_reason: string | null;
    net_return_pct: number | null;
  } | null;
}
export type SortKey = "score" | "rr" | "hit_rate" | "volume" | "momentum";
export interface TopSetups {
  as_of: string;
  scan_run_id: number;
  market: string;
  sort: SortKey;
  items: SetupSummary[];
  total_valid: number;
  market_message: string | null;
  sort_note: string;
  disclaimer: string;
  markets?: Record<string, { as_of: string; scan_run_id: number; market_message: string | null }>;
}
export interface SignalList {
  as_of: string;
  status: "VALID" | "NO_TRADE";
  items: SetupSummary[];
  disclaimer: string;
}

// ---------------------------------------------------------------- stocks
export interface Instrument {
  symbol: string;
  market?: string;
  name: string;
  exchange: string;
  currency: string;
  asset_class: string;
  sector: string | null;
  is_index: boolean;
  is_sample: boolean;
  lot_size: number;
  listed_on: string | null;
  delisted_on: string | null;
}
export interface StockList {
  total: number;
  page: number;
  items: Instrument[];
}
export interface Quote {
  price: number;
  change_1d_pct: number;
  change_1w_pct: number;
  volume: number;
  high_52w: number;
  low_52w: number;
  as_of: string;
}
export interface StockDetail extends Instrument {
  quote: Quote | null;
  fundamentals: Record<string, unknown> | null;
  fundamentals_available: boolean;
  data: DataMeta;
}
export interface Level {
  price: number;
  kind: "support" | "resistance" | string;
  touches: number;
  last_touch: string;
}
export type BarSeries = { t: string[] } & Record<string, (number | null)[] | string[]>;
export interface Candles {
  symbol: string;
  interval: "1d" | "1w";
  bars: BarSeries;
  levels: Level[];
  data: DataMeta;
}
export interface Analysis {
  symbol: string;
  as_of: string;
  mode: "technical" | "fundamental" | "hybrid";
  data: DataMeta;
  active_setups: SetupDetail[];
  status: "VALID" | "NO_TRADE" | "NO_SETUP";
  inactive_strategies: { id: string; name: string; direction: Direction }[];
  state: {
    patterns: string[];
    mtf_long: MTF;
    structure: Record<string, number | boolean | null>;
    indicators: Record<string, number | null>;
    levels: Level[];
  };
  market_regime: Regime | null;
  signal_history: {
    as_of: string;
    strategy: string;
    direction: Direction;
    score: number;
    outcome: string | null;
    t1_hit: boolean | null;
    stop_hit: boolean | null;
    net_return_pct: number | null;
  }[];
  disclaimer: string;
}

// ---------------------------------------------------------------- built-in strategy performance
export interface HitRates {
  sample_size: number;
  t1_hits?: number;
  t2_hits?: number;
  stop_hits?: number;
  neither?: number;
  t1_hit_rate?: number;
  t1_ci95?: Pair;
  t2_hit_rate?: number;
  t2_ci95?: Pair;
  stop_rate?: number;
  stop_ci95?: Pair;
  neither_rate?: number;
  avg_return_pct?: number;
  avg_win_pct?: number | null;
  avg_loss_pct?: number | null;
  expectancy_r?: number;
  avg_holding_bars?: number;
  period?: Pair<string>;
}
export interface StrategyPerformance {
  backtest_period: Pair<string> | null;
  trades: number | null;
  win_rate: number | null;
  t1_hit_rate: number | null;
  t2_hit_rate: number | null;
  stop_rate: number | null;
  profit_factor: number | null;
  max_drawdown_pct: number | null;
  avg_holding_bars: number | null;
  expectancy_r: number | null;
  segments: Record<string, HitRates> | null;
  by_regime: Record<string, HitRates> | null;
  warnings: string[] | null;
  costs: Record<string, number> | null;
}
export interface StrategyDisabledInfo {
  reason: string;
  by: number | null;
  at: string;
}
/** GET /admin/strategies — built-in strategies with their stored backtest performance and per-market switch. */
export interface AdminStrategy extends StrategyPublic {
  performance: Pick<StrategyPerformance, "backtest_period" | "trades" | "t1_hit_rate" | "stop_rate" | "profit_factor" | "expectancy_r" | "segments" | "warnings"> | null;
  /** set when the strategy is switched off for live setups in this market (it is still backtested) */
  disabled: StrategyDisabledInfo | null;
}
export interface AdminStrategies {
  market: string;
  items: AdminStrategy[];
}

// ---------------------------------------------------------------- watchlists
export interface WatchlistItem {
  id: number;
  symbol: string;
  name: string;
  tags: string[];
  note: string;
  added_at: string;
  quote: { price: number; change_1d_pct: number; as_of: string } | null;
  market?: string;
  currency?: string;
  exchange?: string;
  is_sample: boolean;
  setups: { id: number; status: string; strategy: string; score: number; direction: Direction }[];
}
export interface Watchlist {
  id: number;
  name: string;
  market: string;
  created_at: string;
  items: WatchlistItem[];
}

// ---------------------------------------------------------------- risk
export interface PositionSizeIn {
  capital: number;
  risk_pct: number;
  entry: number;
  stop?: number;
  atr?: number;
  atr_mult?: number;
  direction?: Direction;
  lot_size?: number;
  max_position_pct?: number;
}
export interface PositionSizeOut {
  max_risk: number;
  risk_per_unit: number;
  quantity: number;
  position_value: number;
  max_loss: number;
  capital_used_pct: number;
  capped_by: string | null;
  direction: Direction;
  stop?: number;
  method: string;
}

// ---------------------------------------------------------------- admin
export interface DataSourceStatus {
  source: string;
  is_sample: boolean;
  instruments: number;
  last_bar: string | null;
  last_fetch: string | null;
  errors: number;
}
export interface AdminHealth {
  database: boolean;
  cache_backend: string;
  redis: boolean;
  provider: Record<string, unknown>;
  data_sources: DataSourceStatus[];
  last_scan: {
    id: number;
    status: string;
    as_of: string | null;
    started_at: string;
    error: string | null;
    stats: Record<string, unknown> | null;
  } | null;
  failed_jobs: number;
  readiness?: { ready: boolean; checks: Record<string, string> };
}
/** Admin job start: the local inline runner returns a job id; on Vercel a GitHub Actions workflow is dispatched instead. */
export type JobStart = { job_id: number; status?: string; dispatched?: false } | { dispatched: true; actions_url?: string | null };
export interface Job {
  id: number;
  kind: string;
  status: string;
  params?: Record<string, unknown>;
  result: unknown;
  error: string | null;
  created_at?: string;
  finished_at?: string | null;
}
export interface EngineSettings {
  overrides: Record<string, unknown>;
  effective: {
    weights: Record<string, number>;
    labels: [number, string][];
    levels: Record<string, number>;
    validation: Record<string, number>;
    backtest: Record<string, unknown>;
    analysis_mode: string;
  };
  note?: string;
}
export interface AdminUser {
  id: number;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  totp_enabled: boolean;
  created_at: string;
  last_login_at: string | null;
}
export interface AuditLog {
  id: number;
  user_id: number | null;
  action: string;
  target: string | null;
  detail: unknown;
  ip: string | null;
  created_at: string;
}
export interface Providers {
  market_data: { active: string; available: string[] };
  options: { active: string; available: string[] };
  crypto: { active: string; available: string[] };
  api_keys: { id: number; provider: string; name: string; enabled: boolean; created_at: string }[];
  status: DataSourceStatus[];
}

// ---------------------------------------------------------------- NIFTY options (backend/engine/options/*)
export type OptionType = "CE" | "PE";

export interface OptMarketState {
  direction: "Bullish" | "Bearish" | "Range-bound" | string;
  events: string[];
  volatility: string;
  squeeze: boolean;
  labels: string[];
  adx: number | null;
  rsi: number | null;
  regime: string | null;
  reasons: string[];
  as_of: string;
}
export interface OptIntraday {
  as_of: string;
  session_vwap: number;
  price: number;
  price_vs_vwap: "above" | "below";
  session_high: number;
  session_low: number;
  timeframes: Record<string, string>;
  alignment: string;
  note: string;
}
export interface OptIV {
  atm_iv_near_pct: number | null;
  iv_percentile: number | null;
  iv_percentile_basis: string;
  skew_25d_pts: number | null;
  term_structure: { expiry: string; dte: number; atm_iv_pct: number | null }[];
}
export interface OiLevel {
  strike: number;
  oi: number;
  oi_change: number;
}
export interface OiProfileRow {
  strike: number;
  call_oi: number;
  put_oi: number;
  call_oi_change: number;
  put_oi_change: number;
}
export interface OptOI {
  expiry: string;
  pcr_oi: number | null;
  pcr_volume: number | null;
  pcr_oi_change: number | null;
  pcr_all_expiries: number | null;
  max_pain: number | null;
  support: OiLevel[];
  resistance: OiLevel[];
  buildup: Record<string, unknown>[];
  profile: OiProfileRow[];
  note: string;
}
export interface OptionsOverview {
  underlying: {
    symbol: string;
    name: string;
    spot: number;
    as_of: string;
    lot_size: number;
    futures: { near_expiry: string; forward: number; basis_pts: number; note: string };
  };
  market_state: OptMarketState;
  intraday: OptIntraday | null;
  regime: Regime | null;
  iv: OptIV;
  expected_move: { expiry: string; straddle_price: number | null; iv_1sd_to_expiry: number | null; iv_1sd_1day: number | null };
  oi: OptOI;
  liquidity: { liquid_contracts: number; total_contracts: number; filters: Record<string, number> };
  expiries: string[];
  status: "VALID" | "NO_TRADE";
  market_message: string | null;
  counts: { option_setups: number; valid: number; strategies_proposed: number };
  data: DataMeta & { snapshot_ts?: string };
  disclaimer: string;
  snapshot_created_at?: string;
}
export interface ChainQuote {
  bid: number | null;
  ask: number | null;
  ltp: number | null;
  mid: number | null;
  spread_pct: number | null;
  volume: number | null;
  oi: number | null;
  oi_change: number | null;
  iv: number | null; // percent
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  liquid: boolean;
}
export interface ChainRow {
  strike: number;
  CE?: ChainQuote;
  PE?: ChainQuote;
}
export interface OptionChain {
  expiry: string;
  spot: number;
  atm_strike: number | null;
  rows: ChainRow[];
  lot_size: number;
  as_of: string;
  data: DataMeta;
}
export interface OptionContract {
  label: string;
  underlying: string;
  strike: number;
  option_type: OptionType;
  expiry: string;
  dte: number;
  lot_size: number;
  bid: number;
  ask: number;
  ltp: number;
  mid: number;
  spread_pct: number;
  oi: number;
  oi_change: number;
  volume: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}
export interface OptionProbability extends Partial<ProbabilitySummary> {
  neither_rate?: number | null;
  t2_ci95?: Pair | null;
  expectancy_r?: number | null;
  avg_holding_bars?: number | null;
  definitions?: Record<string, string>;
  basis: string;
  basis_note: string;
}
export interface OptionSetup {
  status: "VALID" | "NO_TRADE";
  direction: "BULLISH" | "BEARISH" | Direction;
  reason?: string;
  contract?: OptionContract;
  entry?: number;
  entry_method?: string;
  targets?: [number, number];
  stop?: number;
  level_method?: string;
  rr?: number;
  risk_per_lot?: number;
  reward_t2_per_lot?: number;
  premium_per_lot?: number;
  theta_burn_pct?: number;
  expected_move_hold?: number;
  score?: number;
  contract_score?: number;
  underlying_score?: number;
  probability?: OptionProbability;
  expected_holding?: string;
  underlying?: {
    symbol: string;
    strategy: StrategyPublic;
    price: number;
    entry_zone: Pair;
    stop: number;
    targets: number[];
    rr_t2: number;
    status: string;
    score: number;
    stop_method: string;
    target_methods: string[];
  };
  underlying_setup?: Record<string, unknown>;
  checks?: Check[];
  reasons?: string[];
  risk_factors?: string[];
  alternatives?: { label: string; entry: number; delta: number; rr: number; theta_burn_pct: number; contract_score: number; passes: boolean }[];
  rejected_contracts?: number;
  data?: DataMeta;
  as_of?: string;
}
export interface OptionSignals {
  as_of: string;
  status: string;
  market_message: string | null;
  market_state: OptMarketState;
  items: OptionSetup[];
  underlying_setups: {
    strategy: StrategyPublic;
    direction: Direction;
    status: string;
    score: number;
    current_price: number;
    stop: number;
    targets: number[];
    rr_t2: number;
    probability: Probability;
    checks: Check[];
  }[];
  data: DataMeta;
  disclaimer: string;
}
export interface OptionLeg {
  option_type: OptionType;
  strike: number;
  expiry: string;
  side: 1 | -1;
  action: "BUY" | "SELL";
  lots: number;
  premium: number;
  iv: number; // percent
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}
export interface PopHistBucket {
  sample_size: number;
  pop: number | null;
  avg_pnl?: number;
  p5_pnl?: number;
  period?: Pair<string>;
}
export interface StructureMetrics {
  legs: OptionLeg[];
  max_profit: number | null;
  max_profit_unlimited: boolean;
  max_loss: number | null;
  max_loss_unlimited: boolean;
  breakevens: number[];
  net_premium: number;
  premium_type: "credit" | "debit";
  pop_model: number | null;
  ev_model: number | null;
  pop_model_note: string;
  pop_historical: { horizon_sessions: number; note: string; all?: PopHistBucket; same_regime?: PopHistBucket } | null;
  net_greeks: { delta: number; gamma: number; theta: number; vega: number };
  payoff_curve: [number, number][];
}
export interface StrategyCondition {
  condition: string;
  passed: boolean;
}
export interface StrategyBase {
  key: string;
  name: string;
  category: string;
  outlook: string;
  iv_condition: string;
  recommended_expiry: string;
  management: string;
  risk_note: string;
  conditions: StrategyCondition[];
}
export interface ProposedStrategy extends StrategyBase, StructureMetrics {
  expiry: string;
  dte: number;
  lot_size: number;
  required_capital: number | null;
  capital_note: string;
  reward_to_risk: number | null;
  iv_at_entry: { atm_iv_pct: number | null; iv_percentile: number | null };
}
export interface NotSuitableStrategy extends StrategyBase {
  failed: string[];
}
export interface OptionStrategies {
  as_of: string;
  market_state: OptMarketState;
  iv: OptIV;
  proposed: ProposedStrategy[];
  not_suitable: NotSuitableStrategy[];
  data: DataMeta;
  disclaimer: string;
}
export interface PayoffLegIn {
  strike: number;
  option_type: OptionType;
  expiry: string;
  side: "BUY" | "SELL";
  lots: number;
}
export interface PayoffOut extends StructureMetrics {
  required_capital: number | null;
  capital_note: string;
  reward_to_risk: number | null;
  risk_note?: string;
  expiry: string;
  spot: number;
  lot_size: number;
  as_of: string;
  disclaimer: string;
}

// ---------------------------------------------------------------- paper trading, journal, alerts, notifications (Phase 7)
export interface PaperFill {
  kind: string;
  at: string;
  price: number;
  quantity: number;
  fee: number;
}
export interface PaperTrade {
  id: number;
  symbol: string;
  market: string;
  currency: string;
  direction: Direction;
  status: "pending" | "open" | "closed" | "cancelled";
  order_type: string;
  quantity: number;
  open_quantity: number;
  limit_price: number | null;
  stop: number | null;
  target1: number | null;
  target2: number | null;
  filled_price: number | null;
  filled_at: string | null;
  exit_price: number | null;
  closed_at: string | null;
  exit_reason: string | null;
  last_price: number | null;
  last_price_at: string | null;
  unrealized: number;
  realized: number;
  costs: number;
  brokerage: number;
  taxes: number;
  fx_to_base: number | null;
  unrealized_base: number;
  realized_base: number;
  holding_days: number | null;
  fills: PaperFill[];
  signal_id: number | null;
  notes: string;
  close_requested: boolean;
}
export interface PortfolioAnalytics {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number | null;
  avg_winner: number | null;
  avg_loser: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  realized_pnl: number;
  unrealized_pnl: number;
  avg_holding_days: number | null;
  open_positions: number;
  open_risk: number;
  open_risk_pct: number;
  gross_exposure: number;
  equity: number;
  return_pct: number;
  exposure_by_market: Record<string, number>;
  exposure_by_sector: Record<string, number>;
  max_drawdown_pct: number | null;
  sharpe: number | null;
  equity_curve: [string, number][];
  monthly_pnl: Record<string, number>;
  note: string;
}
export interface PaperPortfolio {
  id: number;
  name: string;
  kind: "paper" | "journal";
  base_currency: string;
  starting_capital: number;
  trades: PaperTrade[] | null;
  analytics: PortfolioAnalytics;
  execution_note?: string;
  disclaimer?: string;
}
export interface OrderIn {
  symbol: string;
  direction: Direction;
  quantity: number;
  order_type: "market" | "limit" | "stop";
  limit_price?: number;
  stop?: number;
  target1?: number;
  target2?: number;
  partial_at_t1?: number;
  max_hold_bars?: number;
  expires_at?: string;
  notes?: string;
}
export interface JournalIn {
  symbol: string;
  direction: Direction;
  quantity: number;
  entry_price: number;
  entry_at: string;
  exit_price?: number;
  exit_at?: string;
  stop?: number;
  target1?: number;
  target2?: number;
  brokerage: number;
  taxes: number;
  notes: string;
}
export type ChannelId = "web" | "email" | "telegram" | "push" | "whatsapp";
export interface AlertKind {
  kind: string;
  description: string;
  params: string[];
  needs_symbol: boolean;
}
export interface AlertItem {
  id: number;
  kind: string;
  description: string;
  symbol: string | null;
  params: Record<string, unknown>;
  channels: string[];
  status: "active" | "paused" | "triggered" | "expired" | string;
  repeat: boolean;
  cooldown_minutes: number;
  trigger_count: number;
  last_triggered_at: string | null;
  note: string;
  signal_id: number | null;
  expires_at: string | null;
  created_at: string;
}
export interface AlertIn {
  kind: string;
  symbol?: string;
  params: Record<string, unknown>;
  channels: string[];
  repeat: boolean;
  cooldown_minutes: number;
  note: string;
  expires_at?: string;
}
export interface NotificationItem {
  id: number;
  title: string;
  body: string;
  link: string | null;
  payload: Record<string, unknown>;
  deliveries: Record<string, { status: string; error?: string; at?: string }>;
  read: boolean;
  created_at: string;
}
export interface NotificationSettings {
  email_enabled: boolean;
  email_address: string;
  telegram_linked: boolean;
  whatsapp_number: string | null;
  push_subscriptions: number;
  default_channels: string[];
  quiet_start_hour: number | null;
  quiet_end_hour: number | null;
  channels: Record<string, { configured: boolean }>;
  vapid_public_key: string | null;
}
export interface TelegramLink {
  code: string;
  expires_at: string;
  bot: string;
  deep_link: string | null;
  instructions: string;
  configured: boolean;
}

export type IdeaResult = "open" | "target1" | "target2" | "stop" | "time" | "not_filled";

/** One published idea and what happened next (GET /signals/history). */
export interface PastIdea {
  id: number;
  market: "NSE" | "CRYPTO" | "NFO";
  as_of: string;
  label: string;
  direction: Direction;
  strategy: string;
  currency: string | null;
  entry_zone: [number, number] | null;
  stop: number | null;
  targets: [number, number] | null;
  chance_t1: number | null;
  sample_size: number;
  result: IdeaResult;
  exit_reason: string | null;
  net_return_pct: number | null;
  resolved_at: string | null;
  judged_on?: { symbol: string; entry_zone: [number, number]; stop: number; targets: [number, number] };
  /** Price when the idea was published vs the latest close (NIFTY for an option idea). */
  move: { of: string; price_then: number | null; price_now: number | null; as_of: string | null; change_pct: number | null };
}

export interface IdeaHistory {
  items: PastIdea[];
  summary: {
    ideas: number; open: number; not_filled: number; closed: number; target1_or_better: number; target2: number; stop: number; time: number;
    target1_rate: number | null; stop_rate: number | null; expected_target1_rate: number | null; avg_net_return_pct: number | null;
  };
  note: string;
}
