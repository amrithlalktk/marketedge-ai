"use client";

import { useState } from "react";
import { compact, integer, moveClass, num, pct } from "@/lib/format";
import type { OptionsOverview } from "@/lib/types";
import { Card, DataStamp, EmptyState, Pill, Segmented, Stat, cx } from "../ui";
import { OiProfileChart, TermStructureChart } from "./charts";

function tone(label: string): "green" | "red" | "amber" | "blue" | "slate" {
  if (/bull|breakout/i.test(label)) return "green";
  if (/bear|breakdown/i.test(label)) return "red";
  if (/high vol/i.test(label)) return "amber";
  if (/low vol|range/i.test(label)) return "blue";
  return "slate";
}

function tfTone(v: string) {
  return v === "Bullish" ? "text-up" : v === "Bearish" ? "text-down" : "text-muted";
}

export function MarketMessage({ ov }: { ov: OptionsOverview }) {
  if (!ov.market_message) {
    return (
      <p className="rounded-lg border border-green-900 bg-green-950/30 p-3 text-sm font-semibold text-green-200">
        {ov.counts.valid} option setup{ov.counts.valid === 1 ? "" : "s"} passed every underlying and contract check.
      </p>
    );
  }
  return (
    <div role="status" className="rounded-lg border border-amber-800 bg-amber-950/40 p-3">
      <p className="text-sm font-bold text-amber-200 sm:text-base">{ov.market_message}</p>
      <p className="mt-1 text-xs text-muted">
        {ov.counts.option_setups} candidate{ov.counts.option_setups === 1 ? "" : "s"} evaluated · {ov.counts.valid} valid · {ov.counts.strategies_proposed} strategies proposed. Standing aside is a valid outcome.
      </p>
    </div>
  );
}

interface BuildupRow { strike: number; option_type: string; price_change: number; oi_change: number; interpretation: string }

const BUILDUP_TONE: Record<string, "green" | "red" | "amber" | "blue"> = { "Long buildup": "green", "Short covering": "blue", "Short buildup": "red", "Long unwinding": "amber" };

function BuildupTable({ rows }: { rows: BuildupRow[] }) {
  const [side, setSide] = useState<"ALL" | "CE" | "PE">("ALL");
  const view = rows.filter((r) => side === "ALL" || r.option_type === side);
  const counts = rows.reduce<Record<string, number>>((a, r) => ({ ...a, [r.interpretation]: (a[r.interpretation] ?? 0) + 1 }), {});
  return (
    <div className="mt-4">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">OI buildup (price vs OI change since the previous snapshot)</h3>
        <Segmented<"ALL" | "CE" | "PE"> label="Buildup side" value={side} onChange={setSide} options={[{ value: "ALL", label: "All" }, { value: "CE", label: "Calls" }, { value: "PE", label: "Puts" }]} />
      </div>
      <p className="mb-1 flex flex-wrap gap-1 text-[11px]">{Object.entries(counts).map(([k, v]) => <Pill key={k} tone={BUILDUP_TONE[k] ?? "slate"}>{k}: {v}</Pill>)}</p>
      <table className="tbl w-full table-fixed text-xs">
        <thead><tr><th scope="col" className="w-[28%]">Strike</th><th scope="col" className="w-[20%] text-right">ΔPrice</th><th scope="col" className="w-[20%] text-right">ΔOI</th><th scope="col">Reading</th></tr></thead>
        <tbody>
          {view.map((r) => (
            <tr key={`${r.strike}-${r.option_type}`}>
              <td className="num">{num(r.strike, 0)} <span className={r.option_type === "CE" ? "text-red-300" : "text-green-300"}>{r.option_type}</span></td>
              <td className={cx("num text-right", moveClass(r.price_change))}>{r.price_change > 0 ? "+" : ""}{num(r.price_change, 2)}</td>
              <td className={cx("num text-right", moveClass(r.oi_change))}>{r.oi_change > 0 ? "+" : ""}{compact(r.oi_change)}</td>
              <td><Pill tone={BUILDUP_TONE[r.interpretation] ?? "slate"}>{r.interpretation}</Pill></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-muted">Price↑ OI↑ long buildup · price↓ OI↑ short buildup · price↑ OI↓ short covering · price↓ OI↓ long unwinding. Interpretations are conventions, not predictions.</p>
    </div>
  );
}

export function OverviewTab({ ov }: { ov: OptionsOverview }) {
  const u = ov.underlying;
  const ms = ov.market_state;
  const iv = ov.iv;
  const em = ov.expected_move;
  const oi = ov.oi;
  const it = ov.intraday;
  return (
    <div className="space-y-4">
      <MarketMessage ov={ov} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title={u.name} right={<DataStamp meta={ov.data} asOf={u.as_of} />}>
          <div className="num text-2xl font-semibold">{num(u.spot)}</div>
          <p className="text-xs text-muted">{u.symbol} · lot size {u.lot_size}</p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <Stat label={`Forward (${u.futures.near_expiry})`} value={num(u.futures.forward)} />
            <Stat label="Basis" value={`${u.futures.basis_pts > 0 ? "+" : ""}${num(u.futures.basis_pts)} pts`} valueClass={moveClass(u.futures.basis_pts)} />
          </div>
          <p className="mt-2 text-[11px] text-muted">{u.futures.note}</p>
        </Card>

        <Card title="Market state" className="lg:col-span-2" right={<DataStamp asOf={ms.as_of} />}>
          <div className="flex flex-wrap gap-1.5">
            {ms.labels.map((l) => <Pill key={l} tone={tone(l)}>{l}</Pill>)}
            {ms.squeeze && <Pill tone="amber">BB squeeze</Pill>}
            {ms.regime && <Pill>Regime: {ms.regime}</Pill>}
          </div>
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted">
            <span>ADX <span className="num text-ink">{num(ms.adx, 1)}</span></span>
            <span>RSI <span className="num text-ink">{num(ms.rsi, 1)}</span></span>
          </div>
          <ul className="mt-2 space-y-0.5 text-xs text-muted">{ms.reasons.map((r) => <li key={r}>• {r}</li>)}</ul>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Intraday context" right={it ? <DataStamp asOf={it.as_of.slice(0, 10)} /> : null}>
          {!it ? <EmptyState title="No intraday bars available" /> : (
            <>
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Session VWAP" value={num(it.session_vwap)} sub={<>price <span className={it.price_vs_vwap === "above" ? "text-up" : "text-down"}>{it.price_vs_vwap}</span> VWAP</>} />
                <Stat label="Session range" value={`${num(it.session_low, 0)}–${num(it.session_high, 0)}`} />
              </div>
              <ul className="mt-2 grid grid-cols-4 gap-1 text-center">
                {["5M", "15M", "30M", "1H"].map((tf) => (
                  <li key={tf} className="rounded border border-edge px-1 py-1">
                    <div className="text-[10px] text-muted">{tf}</div>
                    <div className={cx("text-xs font-semibold", tfTone(it.timeframes[tf] ?? ""))}>{it.timeframes[tf] ?? "n/a"}</div>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs">Alignment: <strong>{it.alignment}</strong></p>
              <p className="mt-1 text-[11px] text-muted">{it.note}</p>
            </>
          )}
        </Card>

        <Card title="Implied volatility">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="ATM IV" value={pct(iv.atm_iv_near_pct, 2)} />
            <Stat label="IV percentile" value={iv.iv_percentile == null ? "n/a" : num(iv.iv_percentile, 0)} />
            <Stat label="25Δ skew" value={iv.skew_25d_pts == null ? "—" : `${num(iv.skew_25d_pts, 2)} pts`} />
          </div>
          <p className="mt-1 text-[11px] text-muted">Percentile basis: {iv.iv_percentile_basis}</p>
          <h3 className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-muted">Term structure (ATM IV by expiry)</h3>
          <TermStructureChart points={iv.term_structure} />
        </Card>

        <Card title={`Expected move · ${em.expiry}`}>
          <div className="grid grid-cols-1 gap-2">
            <Stat label="ATM straddle price" value={em.straddle_price == null ? "—" : `₹${num(em.straddle_price)}`} sub={em.straddle_price ? `≈ ±${pct((100 * em.straddle_price) / u.spot, 2)} of spot` : undefined} />
            <Stat label="IV 1σ to expiry" value={em.iv_1sd_to_expiry == null ? "—" : `±${num(em.iv_1sd_to_expiry, 0)} pts`} sub={em.iv_1sd_to_expiry ? `${num(u.spot - em.iv_1sd_to_expiry, 0)} – ${num(u.spot + em.iv_1sd_to_expiry, 0)}` : undefined} />
            <Stat label="IV 1σ, 1 day" value={em.iv_1sd_1day == null ? "—" : `±${num(em.iv_1sd_1day, 0)} pts`} />
          </div>
          <p className="mt-2 text-[11px] text-muted">A 1σ range contains the index roughly two-thirds of the time under the model; it is not a forecast.</p>
        </Card>
      </div>

      <Card title={`Open interest · ${oi.expiry}`}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Stat label="PCR (OI)" value={num(oi.pcr_oi, 3)} />
          <Stat label="PCR (volume)" value={num(oi.pcr_volume, 3)} />
          <Stat label="PCR (ΔOI)" value={num(oi.pcr_oi_change, 3)} />
          <Stat label="PCR all expiries" value={num(oi.pcr_all_expiries, 3)} />
          <Stat label="Max pain" value={num(oi.max_pain, 0)} />
        </div>
        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="grid grid-cols-2 content-start gap-3 lg:col-span-1 lg:grid-cols-1">
            {([["OI resistance (call writers)", oi.resistance, "text-down"], ["OI support (put writers)", oi.support, "text-up"]] as const).map(([t, list, cls]) => (
              <div key={t}>
                <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">{t}</h3>
                {list.length === 0 ? <p className="text-xs text-muted">None identified</p> : (
                  <ul className="divide-y divide-edge text-xs">
                    {list.map((l) => (
                      <li key={l.strike} className="flex flex-wrap justify-between gap-x-2 py-1">
                        <span className={cx("num font-semibold", cls)}>{num(l.strike, 0)}</span>
                        <span className="num text-muted">{integer(l.oi)} <span className={moveClass(l.oi_change)}>({l.oi_change > 0 ? "+" : ""}{integer(l.oi_change)})</span></span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
          <div className="min-w-0 lg:col-span-2">
            <OiProfileChart rows={oi.profile} spot={u.spot} maxPain={oi.max_pain} />
          </div>
        </div>
        {oi.buildup.length > 0 && <BuildupTable rows={oi.buildup as unknown as BuildupRow[]} />}
        <p className="mt-2 text-[11px] text-muted">{oi.note}</p>
      </Card>

      <p className="text-[11px] text-muted">
        Liquidity filter: {ov.liquidity.liquid_contracts} of {ov.liquidity.total_contracts} contracts pass (spread ≤ {ov.liquidity.filters.max_spread_pct}%, OI ≥ {ov.liquidity.filters.min_oi_lots} lots, volume ≥ {ov.liquidity.filters.min_volume_lots} lots, premium ≥ ₹{ov.liquidity.filters.min_premium}).
      </p>
    </div>
  );
}
