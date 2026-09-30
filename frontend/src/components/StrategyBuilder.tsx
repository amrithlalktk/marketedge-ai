"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { api } from "@/lib/api";
import {
  EXIT_LIMITS,
  MAX_CONDITIONS,
  MAX_GROUPS,
  feat,
  groupFeatures,
  isBooleanFeature,
  localProblems,
  rid,
  toDefinition,
  totalConditions,
  type BuilderState,
  type OperandState,
  type RowState,
} from "@/lib/dsl";
import type { StrategyDefinitionV2, ValidateOut } from "@/lib/types";
import { Field, Segmented, cx } from "./ui";

const OP_LABEL: Record<string, string> = { ">": ">", ">=": "≥", "<": "<", "<=": "≤", crosses_above: "crosses above", crosses_below: "crosses below" };

function FeatureSelect({ id, value, onChange, groups }: { id: string; value: string; onChange: (v: string) => void; groups: { label: string; items: string[] }[] }) {
  return (
    <select id={id} className="input num min-w-0" value={value} onChange={(e) => onChange(e.target.value)}>
      {groups.map((g) => (
        <optgroup key={g.label} label={g.label}>
          {g.items.map((f) => <option key={f} value={f}>{f}{isBooleanFeature(f) ? " (1/0)" : ""}</option>)}
        </optgroup>
      ))}
    </select>
  );
}

function OperandPicker({ label, value, onChange, groups }: { label: string; value: OperandState; onChange: (o: OperandState) => void; groups: { label: string; items: string[] }[] }) {
  const id = useId();
  const kind = value.kind === "num" ? "num" : value.mult !== "1" || value.shift !== "0" ? "adv" : "feat";
  const first = groups[0]?.items[0] ?? "close";
  return (
    <fieldset className="min-w-0 rounded border border-edge/70 p-2">
      <legend className="px-1 text-[11px] text-muted">{label}</legend>
      <div className="flex flex-wrap items-end gap-2">
        <div className="w-[7.5rem]">
          <label className="sr-only" htmlFor={`${id}-k`}>{label} type</label>
          <select id={`${id}-k`} className="input" value={kind} onChange={(e) => {
            const k = e.target.value;
            if (k === "num") onChange({ kind: "num", num: value.kind === "num" ? value.num : "0" });
            else if (k === "feat") onChange(feat(value.kind === "feat" ? value.feature : first));
            else onChange({ kind: "feat", feature: value.kind === "feat" ? value.feature : first, mult: value.kind === "feat" && value.mult !== "1" ? value.mult : "1", shift: value.kind === "feat" && value.shift !== "0" ? value.shift : "1" });
          }}>
            <option value="num">Number</option>
            <option value="feat">Feature</option>
            <option value="adv">Feature × / ago</option>
          </select>
        </div>
        {value.kind === "num" ? (
          <div className="min-w-[6rem] flex-1">
            <label className="sr-only" htmlFor={`${id}-n`}>{label} value</label>
            <input id={`${id}-n`} className="input num" type="number" step="any" value={value.num} onChange={(e) => onChange({ ...value, num: e.target.value })} />
          </div>
        ) : (
          <>
            <div className="min-w-[9rem] flex-1">
              <label className="sr-only" htmlFor={`${id}-f`}>{label} feature</label>
              <FeatureSelect id={`${id}-f`} value={value.feature} onChange={(f) => onChange({ ...value, feature: f })} groups={groups} />
            </div>
            {kind === "adv" && (
              <>
                <div className="w-[5.5rem]">
                  <label className="text-[10px] text-muted" htmlFor={`${id}-m`}>× mult</label>
                  <input id={`${id}-m`} className="input num" type="number" step="0.01" min={0.01} max={100} value={value.mult} onChange={(e) => onChange({ ...value, mult: e.target.value })} />
                </div>
                <div className="w-[5.5rem]">
                  <label className="text-[10px] text-muted" htmlFor={`${id}-s`}>bars ago</label>
                  <input id={`${id}-s`} className="input num" type="number" step="1" min={0} max={20} value={value.shift} onChange={(e) => onChange({ ...value, shift: e.target.value })} />
                </div>
              </>
            )}
          </>
        )}
      </div>
    </fieldset>
  );
}

function Row({ r, n, ops, groups, onChange, onRemove }: { r: RowState; n: string; ops: string[]; groups: { label: string; items: string[] }[]; onChange: (r: RowState) => void; onRemove: () => void }) {
  const id = useId();
  return (
    <li className="grid grid-cols-1 gap-2 rounded border border-edge bg-panel2/40 p-2 sm:grid-cols-[1fr_auto] 2xl:grid-cols-[1fr_8rem_1fr_auto] 2xl:items-center">
      <OperandPicker label={`${n} left`} value={r.left} onChange={(left) => onChange({ ...r, left })} groups={groups} />
      <div>
        <label className="sr-only" htmlFor={`${id}-op`}>{n} operator</label>
        <select id={`${id}-op`} className="input" value={r.op} onChange={(e) => onChange({ ...r, op: e.target.value })}>
          {ops.map((o) => <option key={o} value={o}>{OP_LABEL[o] ?? o}</option>)}
        </select>
      </div>
      <OperandPicker label={`${n} right`} value={r.right} onChange={(right) => onChange({ ...r, right })} groups={groups} />
      <button type="button" className="btn-ghost" onClick={onRemove} aria-label={`Remove ${n}`}>✕</button>
    </li>
  );
}

export interface BuilderAction {
  label: string;
  onClick: (d: StrategyDefinitionV2) => void | Promise<void>;
  primary?: boolean;
  disabled?: boolean;
}

export function StrategyBuilder({ state, setState, features, operators, actions, busy }: { state: BuilderState; setState: (s: BuilderState) => void; features: string[]; operators: string[]; actions: BuilderAction[]; busy?: boolean }) {
  const groups = useMemo(() => groupFeatures(features.length ? features : ["close", "ema50"]), [features]);
  const [check, setCheck] = useState<ValidateOut | null>(null);
  const [checking, setChecking] = useState(false);
  const problems = localProblems(state);
  const def = useMemo(() => toDefinition(state), [state]);
  const defKey = JSON.stringify(def);
  const total = totalConditions(state);

  // Live server-side validation (debounced). The engine is the source of truth.
  useEffect(() => {
    if (problems.length) {
      setCheck(null);
      return;
    }
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      setChecking(true);
      try {
        setCheck(await api.strategies.validate(JSON.parse(defKey) as StrategyDefinitionV2, ctl.signal));
      } catch (e) {
        if (!ctl.signal.aborted) setCheck({ valid: false, error: e instanceof Error ? e.message : "Validation failed" });
      } finally {
        if (!ctl.signal.aborted) setChecking(false);
      }
    }, 450);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [defKey, problems.length]);

  const set = (patch: Partial<BuilderState>) => setState({ ...state, ...patch });
  const newRow = (): RowState => ({ id: rid(), left: feat("rsi"), op: ">", right: { kind: "num", num: "50" } });
  const ex = state.exits;
  const setEx = (p: Partial<typeof ex>) => set({ exits: { ...ex, ...p } });
  const ok = !problems.length && check?.valid === true;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto]">
        <Field label="Strategy name" htmlFor="sb-name"><input id="sb-name" className="input" maxLength={128} value={state.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <div>
          <span className="label">Direction</span>
          <Segmented label="Direction" value={state.direction} onChange={(d) => set({ direction: d })} options={[{ value: "LONG", label: "Long" }, { value: "SHORT", label: "Short" }]} />
        </div>
      </div>

      <section aria-label="All of these conditions">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">ALL of these must be true <span className="text-xs font-normal text-muted">(AND)</span></h3>
          <span className={cx("num text-xs", total > MAX_CONDITIONS ? "text-down" : "text-muted")}>{total}/{MAX_CONDITIONS} conditions</span>
        </div>
        <ul className="space-y-2">
          {state.conditions.map((r, i) => (
            <Row key={r.id} r={r} n={`Condition ${i + 1}`} ops={operators} groups={groups}
              onChange={(nr) => set({ conditions: state.conditions.map((x) => (x.id === r.id ? nr : x)) })}
              onRemove={() => set({ conditions: state.conditions.filter((x) => x.id !== r.id) })} />
          ))}
        </ul>
        <button type="button" className="btn-ghost mt-2" disabled={total >= MAX_CONDITIONS} onClick={() => set({ conditions: [...state.conditions, newRow()] })}>+ Add condition</button>
      </section>

      <section aria-label="Any of groups" className="space-y-3">
        {state.groups.map((g, gi) => (
          <div key={gi} className="rounded-lg border border-blue-900 bg-blue-950/20 p-2">
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-blue-200">{gi === 0 ? "AND ANY OF these groups" : "OR"} — group {gi + 1} <span className="text-xs font-normal text-muted">(all conditions inside a group must hold)</span></h3>
              <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => set({ groups: state.groups.filter((_, j) => j !== gi) })}>Remove group</button>
            </div>
            <ul className="space-y-2">
              {g.map((r, i) => (
                <Row key={r.id} r={r} n={`Group ${gi + 1} condition ${i + 1}`} ops={operators} groups={groups}
                  onChange={(nr) => set({ groups: state.groups.map((gg, j) => (j === gi ? gg.map((x) => (x.id === r.id ? nr : x)) : gg)) })}
                  onRemove={() => set({ groups: state.groups.map((gg, j) => (j === gi ? gg.filter((x) => x.id !== r.id) : gg)) })} />
              ))}
            </ul>
            <button type="button" className="btn-ghost mt-2 px-2 py-1 text-xs" disabled={total >= MAX_CONDITIONS} onClick={() => set({ groups: state.groups.map((gg, j) => (j === gi ? [...gg, newRow()] : gg)) })}>+ Add condition to group</button>
          </div>
        ))}
        <button type="button" className="btn-ghost" disabled={state.groups.length >= MAX_GROUPS || total >= MAX_CONDITIONS} onClick={() => set({ groups: [...state.groups, [newRow()]] })}>
          + Add “Any of” group ({state.groups.length}/{MAX_GROUPS})
        </button>
        <p className="text-[11px] text-muted">Operands: a number, a feature, or feature × multiplier with an optional “bars ago” shift (0–20; the future is never read). Pattern and structure flags compare as 1/0, e.g. <code className="font-mono">pat_breakout ≥ 1</code>.</p>
      </section>

      <fieldset className="rounded border border-edge p-3">
        <legend className="px-1 text-xs text-muted">Exits</legend>
        <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={ex.enabled} onChange={(e) => setEx({ enabled: e.target.checked })} /> Custom exit rules (otherwise engine defaults: structure stop, T1 1.5R, T2 3R)</label>
        <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-5">
          <Field label="Stop method" htmlFor="ex-sm">
            <select id="ex-sm" className="input" disabled={!ex.enabled} value={ex.stop_method} onChange={(e) => setEx({ stop_method: e.target.value as "structure" | "atr" })}>
              <option value="structure">Structure (ATR fallback)</option>
              <option value="atr">ATR only</option>
            </select>
          </Field>
          <Field label={`ATR mult (${EXIT_LIMITS.atr_stop_mult.join("–")})`} htmlFor="ex-atr"><input id="ex-atr" className="input num" type="number" step="0.1" disabled={!ex.enabled} value={ex.atr_stop_mult} onChange={(e) => setEx({ atr_stop_mult: e.target.value })} /></Field>
          <Field label={`T1 R (${EXIT_LIMITS.t1_r.join("–")})`} htmlFor="ex-t1"><input id="ex-t1" className="input num" type="number" step="0.1" disabled={!ex.enabled} value={ex.t1_r} onChange={(e) => setEx({ t1_r: e.target.value })} /></Field>
          <Field label={`T2 R (${EXIT_LIMITS.t2_r.join("–")}, > T1)`} htmlFor="ex-t2"><input id="ex-t2" className="input num" type="number" step="0.1" disabled={!ex.enabled} value={ex.t2_r} onChange={(e) => setEx({ t2_r: e.target.value })} /></Field>
          <Field label="Max hold (bars)" htmlFor="ex-hold"><input id="ex-hold" className="input num" type="number" step="1" min={1} max={120} value={ex.max_hold_bars} onChange={(e) => setEx({ max_hold_bars: e.target.value })} /></Field>
        </div>
      </fieldset>

      <section aria-live="polite" aria-label="Validation" className={cx("rounded-md border p-3 text-sm", ok ? "border-green-900 bg-green-950/30" : problems.length || check?.valid === false ? "border-red-900 bg-red-950/30" : "border-edge bg-panel2/40")}>
        {problems.length > 0 ? (
          <ul className="space-y-0.5 text-red-200">{problems.map((p) => <li key={p}>✗ {p}</li>)}</ul>
        ) : checking && !check ? (
          <p className="text-muted">Validating…</p>
        ) : check?.valid ? (
          <>
            <p className="font-semibold text-green-200">✓ Valid rule set{checking && <span className="ml-2 text-xs font-normal text-muted">re-checking…</span>}</p>
            <ul className="mt-1 space-y-0.5 font-mono text-xs">{check.rules?.map((r) => <li key={r}>• {r}</li>)}</ul>
            <dl className="mt-2 space-y-0.5 text-xs">
              <div><dt className="inline text-muted">Entry: </dt><dd className="inline">{check.entry_rule}</dd></div>
              <div><dt className="inline text-muted">Stop: </dt><dd className="inline">{check.stop_rule}</dd></div>
              <div><dt className="inline text-muted">Targets: </dt><dd className="inline">{check.target_rule}</dd></div>
              <div><dt className="inline text-muted">Max hold: </dt><dd className="inline">{check.max_hold_bars} bars</dd></div>
            </dl>
          </>
        ) : check ? (
          <p className="text-red-200">✗ {check.error}</p>
        ) : <p className="text-muted">Edit the rules to validate.</p>}
      </section>

      {actions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {actions.map((a) => (
            <button key={a.label} type="button" className={a.primary ? "btn-primary" : "btn-ghost"} disabled={!ok || busy || a.disabled} onClick={() => a.onClick(def)}>{a.label}</button>
          ))}
        </div>
      )}
    </div>
  );
}
