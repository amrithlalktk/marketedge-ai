// Helpers for the no-code strategy DSL v2 (backend/engine/strategies.py "No-code strategy builder").
import type { ConditionV2, Direction, Exits, Operand, StrategyDefinitionV2 } from "./types";

export const MAX_CONDITIONS = 12;
export const MAX_GROUPS = 4;
export const EXIT_LIMITS = { atr_stop_mult: [0.5, 6], t1_r: [0.5, 5], t2_r: [1, 10], max_hold_bars: [1, 120] } as const;

export type OperandState =
  | { kind: "num"; num: string }
  | { kind: "feat"; feature: string; mult: string; shift: string };
export interface RowState {
  id: string;
  left: OperandState;
  op: string;
  right: OperandState;
}
export interface ExitsState {
  enabled: boolean;
  stop_method: "structure" | "atr";
  atr_stop_mult: string;
  t1_r: string;
  t2_r: string;
  max_hold_bars: string;
}
export interface BuilderState {
  name: string;
  direction: Direction;
  conditions: RowState[];
  groups: RowState[][];
  exits: ExitsState;
}

let seq = 0;
export const rid = () => `r${Date.now().toString(36)}${(seq++).toString(36)}`;

export const DEFAULT_EXITS: ExitsState = { enabled: false, stop_method: "structure", atr_stop_mult: "2", t1_r: "1.5", t2_r: "3", max_hold_bars: "20" };

export function defaultState(): BuilderState {
  return {
    name: "My rule set",
    direction: "LONG",
    conditions: [
      { id: rid(), left: feat("close"), op: ">", right: feat("ema50") },
      { id: rid(), left: feat("volume"), op: ">", right: { kind: "feat", feature: "vol_sma20", mult: "1.5", shift: "0" } },
    ],
    groups: [],
    exits: { ...DEFAULT_EXITS },
  };
}

export function feat(f: string): OperandState {
  return { kind: "feat", feature: f, mult: "1", shift: "0" };
}

export function toOperand(o: OperandState): Operand {
  if (o.kind === "num") return Number(o.num);
  const mult = Number(o.mult);
  const shift = Math.round(Number(o.shift));
  const hasMult = Number.isFinite(mult) && mult !== 1 && o.mult.trim() !== "";
  const hasShift = Number.isFinite(shift) && shift !== 0;
  if (!hasMult && !hasShift) return o.feature;
  return { feature: o.feature, ...(hasMult ? { mult } : {}), ...(hasShift ? { shift } : {}) };
}

export function fromOperand(v: Operand): OperandState {
  if (typeof v === "number") return { kind: "num", num: String(v) };
  if (typeof v === "string") return feat(v);
  return { kind: "feat", feature: v.feature, mult: v.mult != null ? String(v.mult) : "1", shift: v.shift != null ? String(v.shift) : "0" };
}

const cond = (r: RowState): ConditionV2 => ({ left: toOperand(r.left), op: r.op, right: toOperand(r.right) });

export function toDefinition(s: BuilderState): StrategyDefinitionV2 {
  const d: StrategyDefinitionV2 = { name: s.name.trim() || "Custom strategy", direction: s.direction, conditions: s.conditions.map(cond) };
  const groups = s.groups.filter((g) => g.length > 0);
  if (groups.length) d.any_of = groups.map((g) => g.map(cond));
  const hold = Math.round(Number(s.exits.max_hold_bars)) || 20;
  d.max_hold_bars = hold;
  if (s.exits.enabled) {
    const ex: Exits = { stop_method: s.exits.stop_method, max_hold_bars: hold };
    const n = (x: string) => (x.trim() === "" ? undefined : Number(x));
    if (n(s.exits.atr_stop_mult) != null) ex.atr_stop_mult = n(s.exits.atr_stop_mult);
    if (n(s.exits.t1_r) != null) ex.t1_r = n(s.exits.t1_r);
    if (n(s.exits.t2_r) != null) ex.t2_r = n(s.exits.t2_r);
    d.exits = ex;
  }
  return d;
}

export function fromDefinition(d: StrategyDefinitionV2): BuilderState {
  const row = (c: ConditionV2): RowState => ({ id: rid(), left: fromOperand(c.left), op: c.op, right: fromOperand(c.right) });
  const ex = d.exits;
  return {
    name: d.name,
    direction: d.direction,
    conditions: (d.conditions ?? []).map(row),
    groups: (d.any_of ?? []).map((g) => g.map(row)),
    exits: ex
      ? {
          enabled: true,
          stop_method: ex.stop_method ?? "structure",
          atr_stop_mult: ex.atr_stop_mult != null ? String(ex.atr_stop_mult) : "",
          t1_r: ex.t1_r != null ? String(ex.t1_r) : "",
          t2_r: ex.t2_r != null ? String(ex.t2_r) : "",
          max_hold_bars: String(ex.max_hold_bars ?? d.max_hold_bars ?? 20),
        }
      : { ...DEFAULT_EXITS, max_hold_bars: String(d.max_hold_bars ?? 20) },
  };
}

export function totalConditions(s: BuilderState): number {
  return s.conditions.length + s.groups.reduce((a, g) => a + g.length, 0);
}

/** Client-side checks mirroring the engine (the server remains authoritative via /strategies/validate). */
export function localProblems(s: BuilderState): string[] {
  const out: string[] = [];
  const total = totalConditions(s);
  if (total < 1) out.push("Add at least one condition.");
  if (total > MAX_CONDITIONS) out.push(`At most ${MAX_CONDITIONS} conditions in total (currently ${total}).`);
  if (s.groups.length > MAX_GROUPS) out.push(`At most ${MAX_GROUPS} "Any of" groups.`);
  if (s.groups.some((g) => g.length === 0)) out.push('Each "Any of" group needs at least one condition.');
  const rows = [...s.conditions, ...s.groups.flat()];
  for (const r of rows) {
    for (const o of [r.left, r.right]) {
      if (o.kind === "num" && (o.num.trim() === "" || !Number.isFinite(Number(o.num)))) out.push("Every number operand needs a value.");
      if (o.kind === "feat") {
        const m = Number(o.mult);
        if (o.mult.trim() !== "" && (!Number.isFinite(m) || m < 0.01 || m > 100)) out.push("Multiplier must be between 0.01 and 100.");
        const sh = Number(o.shift);
        if (!Number.isInteger(sh) || sh < 0 || sh > 20) out.push('"Bars ago" must be a whole number 0–20.');
      }
    }
    if (r.left.kind === "num" && r.right.kind === "num") out.push("A condition must reference at least one feature.");
  }
  return Array.from(new Set(out));
}

export const FEATURE_GROUPS: { label: string; test: (f: string) => boolean }[] = [
  { label: "Price", test: (f) => ["open", "high", "low", "close", "high_52w", "low_52w", "hh20_prior", "ll20_prior", "close_loc"].includes(f) },
  { label: "Trend", test: (f) => /^(ema|sma)\d|ema200_slope|supertrend|adx|plus_di|minus_di|weekly_trend|vwap20/.test(f) },
  { label: "Momentum", test: (f) => /^(rsi|macd|stoch|cci|roc|mfi|ret\d)/.test(f) },
  { label: "Volume", test: (f) => /^(volume|vol_|obv)/.test(f) },
  { label: "Volatility", test: (f) => /^(atr|bb_)/.test(f) },
  { label: "Structure", test: (f) => /swing_|higher_|lower_/.test(f) },
  { label: "Patterns", test: (f) => f.startsWith("pat_") },
];

export function groupFeatures(features: string[]): { label: string; items: string[] }[] {
  const used = new Set<string>();
  const out = FEATURE_GROUPS.map((g) => {
    const items = features.filter((f) => !used.has(f) && g.test(f));
    items.forEach((f) => used.add(f));
    return { label: g.label, items };
  }).filter((g) => g.items.length);
  const rest = features.filter((f) => !used.has(f));
  if (rest.length) out.push({ label: "Other", items: rest });
  return out;
}

export const isBooleanFeature = (f: string) => f.startsWith("pat_") || /^(higher|lower)_(high|low)$/.test(f);
