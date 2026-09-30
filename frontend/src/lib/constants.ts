export const DISCLAIMER =
  "Historical/backtested performance and probability estimates do not guarantee future results. Market conditions can change rapidly. This platform provides analytical information and does not guarantee profits.";

export const SAMPLE_BANNER = "SAMPLE DATA ON THIS PAGE — synthetic prices for development. Items marked SAMPLE / DEMO_ are not real market data.";

export const WATCHLIST_TAGS = ["High conviction", "Breakouts", "Monitor", "Long-term"];

export const COMPONENT_LABELS: Record<string, string> = {
  trend: "Technical (trend)",
  momentum: "Momentum",
  volume: "Volume",
  price_action: "Price action",
  structure: "Structure",
  fundamental: "Fundamental",
  volatility: "Volatility",
  regime: "Regime",
  risk_reward: "Risk-reward",
  historical: "Historical evidence",
  ml: "ML estimate",
};

/** Components that are displayed for context and only affect the score if an admin gives them weight. */
export const CONTEXT_COMPONENTS = ["historical", "ml"];

export const SORT_OPTIONS = [
  { value: "score", label: "Score" },
  { value: "rr", label: "Risk:reward (T2)" },
  { value: "hit_rate", label: "Historical T1 hit rate" },
  { value: "volume", label: "Volume component" },
  { value: "momentum", label: "Momentum component" },
] as const;

/** Password policy mirrored from the backend: ≥10 chars and 3 of lower/upper/digit/symbol. */
export function passwordProblem(pw: string): string | null {
  if (pw.length < 10) return "Use at least 10 characters.";
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  if (classes < 3) return "Use at least 3 of: lowercase, uppercase, digit, symbol.";
  return null;
}
