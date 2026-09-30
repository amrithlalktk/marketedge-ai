"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { CURRENCY_SYMBOL, inr, integer, num, pct, price } from "@/lib/format";
import type { Direction, PositionSizeOut } from "@/lib/types";
import { InlineError, NumberInput, Segmented, Stat } from "./ui";

type Mode = "stop" | "atr";

export function PositionSizer({ entry = 1000, stop = 950, atr, direction = "LONG", lotSize = 1, compact = false, currency = "INR" }: { entry?: number; stop?: number; atr?: number; direction?: Direction; lotSize?: number; compact?: boolean; currency?: string }) {
  const sym = (CURRENCY_SYMBOL[currency] ?? `${currency} `).trim();
  const money = (v: number | null | undefined) => (currency === "INR" ? inr(v) : price(v, currency));
  const [mode, setMode] = useState<Mode>("stop");
  const [capital, setCapital] = useState("500000");
  const [riskPct, setRiskPct] = useState("1");
  const [e, setE] = useState(String(entry));
  const [s, setS] = useState(String(stop));
  const [a, setA] = useState(atr != null ? String(atr) : "20");
  const [mult, setMult] = useState("2");
  const [dir, setDir] = useState<Direction>(direction);
  const [lot, setLot] = useState(String(lotSize));
  const [maxPos, setMaxPos] = useState("");
  const [out, setOut] = useState<PositionSizeOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function calc(ev: React.FormEvent) {
    ev.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body = {
        capital: Number(capital),
        risk_pct: Number(riskPct),
        entry: Number(e),
        direction: dir,
        lot_size: Math.max(1, Math.round(Number(lot) || 1)),
        ...(maxPos ? { max_position_pct: Number(maxPos) } : {}),
        ...(mode === "stop" ? { stop: Number(s) } : { atr: Number(a), atr_mult: Number(mult) }),
      };
      setOut(await api.risk.positionSize(body));
    } catch (err) {
      setOut(null);
      setError(err instanceof Error ? err.message : "Calculation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <form onSubmit={calc} className="space-y-3" aria-label="Position size calculator" noValidate>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented<Mode> label="Sizing method" value={mode} onChange={setMode} options={[{ value: "stop", label: "Fixed % with stop" }, { value: "atr", label: "ATR-based" }]} />
          {mode === "atr" && (
            <Segmented<Direction> label="Direction" value={dir} onChange={setDir} options={[{ value: "LONG", label: "Long" }, { value: "SHORT", label: "Short" }]} />
          )}
        </div>
        {mode === "stop" && <p className="text-[11px] text-muted">Direction is inferred from the stop: below entry = long, above entry = short.</p>}
        <div className={compact ? "grid grid-cols-2 gap-3" : "grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"}>
          <NumberInput label="Capital" suffix={sym} value={capital} onChange={setCapital} min={1} />
          <NumberInput label="Risk per trade" suffix="%" value={riskPct} onChange={setRiskPct} min={0.01} max={100} step="0.05" />
          <NumberInput label="Entry" suffix={sym} value={e} onChange={setE} min={0.01} />
          {mode === "stop" ? (
            <NumberInput label="Stop" suffix={sym} value={s} onChange={setS} min={0.01} />
          ) : (
            <>
              <NumberInput label="ATR" suffix={sym} value={a} onChange={setA} min={0.01} />
              <NumberInput label="ATR multiple" suffix="×" value={mult} onChange={setMult} min={0.1} max={10} step="0.1" />
            </>
          )}
          <NumberInput label="Lot size" value={lot} onChange={setLot} min={1} step="1" />
          <NumberInput label="Max position" suffix="% of capital" value={maxPos} onChange={setMaxPos} min={0} max={100} hint="Optional cap" />
        </div>
        {currency !== "INR" && <p className="text-[11px] text-muted">All amounts are in {currency} (the instrument&apos;s currency). Capital should be entered in {currency} too.</p>}
        <button className="btn-primary" disabled={busy}>
          {busy ? "Calculating…" : "Calculate position size"}
        </button>
      </form>
      <InlineError error={error} />
      {out && (
        <div className="mt-4" aria-live="polite">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <Stat label="Quantity" value={`${integer(out.quantity)} ${out.quantity === 1 ? "share" : "shares"}`} valueClass="text-accent" />
            <Stat label="Max risk (budget)" value={money(out.max_risk)} />
            <Stat label="Risk / unit" value={money(out.risk_per_unit)} />
            <Stat label="Max loss at stop" value={money(out.max_loss)} valueClass="text-down" />
            <Stat label="Capital used" value={money(out.position_value)} sub={`${pct(out.capital_used_pct, 1)} of capital`} />
            {out.stop != null ? <Stat label="Stop used" value={money(out.stop)} /> : <Stat label="Method" value={<span className="text-xs">{out.method}</span>} />}
          </div>
          <p className="mt-2 text-[11px] text-muted">
            Method: {out.method}
            {out.capped_by && <> · Capped by {out.capped_by.replace(/_/g, " ")}</>} · Direction {out.direction}. Max loss assumes the stop fills at its price; gaps can make real losses larger. Risk/unit {num(out.risk_per_unit)}.
          </p>
        </div>
      )}
    </div>
  );
}
