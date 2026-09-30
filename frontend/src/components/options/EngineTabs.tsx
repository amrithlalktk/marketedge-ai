"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { num, pct, rr } from "@/lib/format";
import type { OptionChain, OptionSignals, OptionStrategies, OptionType, PayoffLegIn, PayoffOut } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Card, Collapsible, DataStamp, DirectionBadge, EmptyState, ErrorState, Field, InlineError, Pill, Skeleton, StatusBadge, UpgradeNote } from "../ui";
import { OptionSetupCard, StrategyCard, StructureView } from "./parts";

export function SetupsTab() {
  const { can } = useAuth();
  const ok = can("options:signals");
  const q = useApi<OptionSignals>(() => api.options.signals(), [], ok);
  if (!ok || q.error?.status === 403) return <UpgradeNote feature="Option setups" />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="option setups" />;
  if (!q.data) return <Skeleton className="h-80" />;
  const d = q.data;
  return (
    <div className="space-y-4">
      {d.market_message && <p role="status" className="rounded-lg border border-amber-800 bg-amber-950/40 p-3 text-sm font-bold text-amber-200">{d.market_message}</p>}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span>{d.items.length} option candidate{d.items.length === 1 ? "" : "s"} · market state {d.market_state.direction}{!can("signals:read_all") && " · your plan shows VALID setups only"}</span>
        <DataStamp meta={d.data} asOf={d.as_of} />
      </div>
      {d.items.length === 0 ? (
        <EmptyState title="No option setups right now">No underlying NIFTY setup triggered, or none passed the contract checks.</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">{d.items.map((s, i) => <OptionSetupCard key={i} s={s} i={i} />)}</div>
      )}
      {d.underlying_setups.length > 0 && (
        <Card title="Underlying NIFTY setups (daily rules)">
          <ul className="divide-y divide-edge text-sm">
            {d.underlying_setups.map((u) => (
              <li key={u.strategy.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="flex flex-wrap items-center gap-1.5">{u.strategy.name} <DirectionBadge d={u.direction} /> <StatusBadge s={u.status} /></span>
                <span className="num text-xs text-muted">
                  score {num(u.score, 0)} · stop {num(u.stop, 0)} · T1 {num(u.targets[0], 0)} · R:R {rr(u.rr_t2)} · Hist. T1 {u.probability.sample_size ? `${pct(u.probability.t1_hit_rate)} (n=${u.probability.sample_size})` : "insufficient history"}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

export function StrategiesTab({ spot }: { spot: number }) {
  const { can } = useAuth();
  const ok = can("options:signals");
  const q = useApi<OptionStrategies>(() => api.options.strategies(), [], ok);
  if (!ok || q.error?.status === 403) return <UpgradeNote feature="The options strategy engine" />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="strategies" />;
  if (!q.data) return <Skeleton className="h-80" />;
  const d = q.data;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1.5">
          {d.market_state.labels.map((l) => <Pill key={l}>{l}</Pill>)}
          <Pill tone="blue">IV pct {d.iv.iv_percentile == null ? "n/a" : num(d.iv.iv_percentile, 0)}</Pill>
        </div>
        <DataStamp meta={d.data} asOf={d.as_of} />
      </div>
      {d.proposed.length === 0 ? (
        <div className="rounded-lg border border-amber-800 bg-amber-950/30 p-5 text-center">
          <p className="text-base font-bold text-amber-200">No strategy suits current conditions</p>
          <p className="mt-1 text-sm text-muted">Every template has explicit eligibility conditions (direction, IV regime, market regime, liquidity). None are all met right now — see why below. You can still model any structure in the Payoff builder.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">{d.proposed.map((s) => <StrategyCard key={s.key} s={s} spot={spot} />)}</div>
      )}
      <Collapsible title={`Not suitable now (${d.not_suitable.length})`} defaultOpen={d.proposed.length === 0}>
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {d.not_suitable.map((s) => (
            <li key={s.key} className="rounded border border-edge bg-panel2/40 p-2">
              <p className="text-sm font-semibold">{s.name} <span className="text-xs font-normal text-muted">· {s.category} · {s.outlook}</span></p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {s.failed.map((f) => <li key={f} className="text-down">✗ {f}</li>)}
                {s.conditions.filter((c) => c.passed).map((c) => <li key={c.condition} className="text-muted">✓ {c.condition}</li>)}
              </ul>
              {s.risk_note && <p className="mt-1 text-[11px] text-red-300">⚠ {s.risk_note}</p>}
            </li>
          ))}
        </ul>
      </Collapsible>
    </div>
  );
}

interface LegRow { strike: number; option_type: OptionType; side: "BUY" | "SELL"; lots: number }

const PRESETS: { key: string; label: string; legs: (atm: number, st: number) => LegRow[] }[] = [
  { key: "lc", label: "Long call", legs: (a) => [{ strike: a, option_type: "CE", side: "BUY", lots: 1 }] },
  { key: "lp", label: "Long put", legs: (a) => [{ strike: a, option_type: "PE", side: "BUY", lots: 1 }] },
  { key: "bcs", label: "Bull call spread", legs: (a, s) => [{ strike: a, option_type: "CE", side: "BUY", lots: 1 }, { strike: a + 4 * s, option_type: "CE", side: "SELL", lots: 1 }] },
  { key: "bps", label: "Bear put spread", legs: (a, s) => [{ strike: a, option_type: "PE", side: "BUY", lots: 1 }, { strike: a - 4 * s, option_type: "PE", side: "SELL", lots: 1 }] },
  { key: "bpcs", label: "Bull put (credit)", legs: (a, s) => [{ strike: a - 2 * s, option_type: "PE", side: "SELL", lots: 1 }, { strike: a - 4 * s, option_type: "PE", side: "BUY", lots: 1 }] },
  { key: "lstr", label: "Long straddle", legs: (a) => [{ strike: a, option_type: "CE", side: "BUY", lots: 1 }, { strike: a, option_type: "PE", side: "BUY", lots: 1 }] },
  { key: "sstr", label: "Short straddle", legs: (a) => [{ strike: a, option_type: "CE", side: "SELL", lots: 1 }, { strike: a, option_type: "PE", side: "SELL", lots: 1 }] },
  { key: "sstg", label: "Short strangle", legs: (a, s) => [{ strike: a + 6 * s, option_type: "CE", side: "SELL", lots: 1 }, { strike: a - 6 * s, option_type: "PE", side: "SELL", lots: 1 }] },
  { key: "ic", label: "Iron condor", legs: (a, s) => [
    { strike: a + 6 * s, option_type: "CE", side: "SELL", lots: 1 }, { strike: a + 10 * s, option_type: "CE", side: "BUY", lots: 1 },
    { strike: a - 6 * s, option_type: "PE", side: "SELL", lots: 1 }, { strike: a - 10 * s, option_type: "PE", side: "BUY", lots: 1 }] },
];

export function BuilderTab({ expiries, defaultExpiry }: { expiries: string[]; defaultExpiry: string }) {
  const [expiry, setExpiry] = useState(defaultExpiry);
  const chain = useApi<OptionChain>(() => api.options.chain(expiry, 40), [expiry]);
  const strikes = useMemo(() => (chain.data?.rows ?? []).map((r) => r.strike), [chain.data]);
  const step = strikes.length > 1 ? Math.min(...strikes.slice(1).map((s, i) => s - strikes[i])) : 50;
  const atm = chain.data?.atm_strike ?? strikes[Math.floor(strikes.length / 2)] ?? 0;
  const [legs, setLegs] = useState<LegRow[]>([]);
  const [out, setOut] = useState<PayoffOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (atm && legs.length === 0) setLegs(PRESETS[2].legs(atm, step));
  }, [atm, step, legs.length]);

  const snap = (k: number) => (strikes.length ? strikes.reduce((b, s) => (Math.abs(s - k) < Math.abs(b - k) ? s : b), strikes[0]) : k);
  const applyPreset = (p: (typeof PRESETS)[number]) => { setLegs(p.legs(atm, step).map((l) => ({ ...l, strike: snap(l.strike) }))); setOut(null); };
  const upd = (i: number, patch: Partial<LegRow>) => setLegs((p) => p.map((l, j) => (j === i ? { ...l, ...patch } : l)));

  async function calc(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body: PayoffLegIn[] = legs.map((l) => ({ ...l, expiry }));
      setOut(await api.options.payoff(body));
    } catch (err) {
      setOut(null);
      setError(err instanceof Error ? err.message : "Payoff calculation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card title="Payoff builder">
        <form onSubmit={calc} className="space-y-3" noValidate>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Expiry (all legs)" htmlFor="pb-exp">
              <select id="pb-exp" className="input w-auto" value={expiry} onChange={(e) => { setExpiry(e.target.value); setOut(null); }}>
                {expiries.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </Field>
            {chain.data && <span className="pb-2 text-xs text-muted">Spot {num(chain.data.spot)} · ATM {num(atm, 0)} · step {step} · lot {chain.data.lot_size}</span>}
          </div>
          <div>
            <span className="label">Presets (ATM-relative)</span>
            <div className="flex flex-wrap gap-1">
              {PRESETS.map((p) => <button key={p.key} type="button" className="btn-ghost px-2 py-1 text-xs" disabled={!strikes.length} onClick={() => applyPreset(p)}>{p.label}</button>)}
            </div>
          </div>
          {chain.error && <ErrorState error={chain.error} onRetry={chain.reload} what="strikes" />}
          <ul className="space-y-2">
            {legs.map((l, i) => (
              <li key={i} className="grid grid-cols-2 gap-2 rounded border border-edge bg-panel2/40 p-2 sm:grid-cols-[1fr_6rem_6rem_5rem_auto] sm:items-end">
                <Field label={`Leg ${i + 1} strike`} htmlFor={`pb-k-${i}`}>
                  <select id={`pb-k-${i}`} className="input num" value={l.strike} onChange={(e) => upd(i, { strike: Number(e.target.value) })}>
                    {!strikes.includes(l.strike) && <option value={l.strike}>{l.strike} (n/a)</option>}
                    {strikes.map((s) => <option key={s} value={s}>{s}{s === atm ? " (ATM)" : ""}</option>)}
                  </select>
                </Field>
                <Field label="Type" htmlFor={`pb-t-${i}`}>
                  <select id={`pb-t-${i}`} className="input" value={l.option_type} onChange={(e) => upd(i, { option_type: e.target.value as OptionType })}><option>CE</option><option>PE</option></select>
                </Field>
                <Field label="Side" htmlFor={`pb-s-${i}`}>
                  <select id={`pb-s-${i}`} className="input" value={l.side} onChange={(e) => upd(i, { side: e.target.value as LegRow["side"] })}><option>BUY</option><option>SELL</option></select>
                </Field>
                <Field label="Lots" htmlFor={`pb-l-${i}`}>
                  <input id={`pb-l-${i}`} className="input num" type="number" min={1} max={100} step={1} value={l.lots} onChange={(e) => upd(i, { lots: Math.max(1, Math.min(100, Math.round(Number(e.target.value) || 1))) })} />
                </Field>
                <button type="button" className="btn-ghost" aria-label={`Remove leg ${i + 1}`} onClick={() => setLegs((p) => p.filter((_, j) => j !== i))}>✕</button>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-ghost" disabled={legs.length >= 6 || !strikes.length} onClick={() => setLegs((p) => [...p, { strike: atm, option_type: "CE", side: "BUY", lots: 1 }])}>+ Add leg ({legs.length}/6)</button>
            <button className="btn-primary" disabled={busy || legs.length === 0}>{busy ? "Calculating…" : "Calculate payoff"}</button>
          </div>
          <InlineError error={error} />
          {!chain.data && !chain.error && <Skeleton className="h-10" />}
        </form>
      </Card>
      {out && (
        <section aria-label="Payoff result" aria-live="polite" className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">Result · expiry {out.expiry}</h2>
            <DataStamp asOf={out.as_of.slice(0, 10)} />
          </div>
          {out.max_loss_unlimited && <p role="alert" className="rounded border border-red-800 bg-red-950/50 px-2 py-1.5 text-sm font-semibold text-red-200">⚠ UNLIMITED RISK: this structure has at least one uncovered short option.</p>}
          {out.risk_note && <p role="alert" className="rounded border border-red-800 bg-red-950/50 px-2 py-1.5 text-sm font-semibold text-red-200">⚠ {out.risk_note}</p>}
          <StructureView m={out} spot={out.spot} lotSize={out.lot_size} extra={{ capital: out.required_capital, capitalNote: out.capital_note, rewardToRisk: out.reward_to_risk }} />
        </section>
      )}
    </div>
  );
}
