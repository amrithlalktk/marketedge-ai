"use client";

import { Fragment, useState } from "react";
import { api } from "@/lib/api";
import { compact, num } from "@/lib/format";
import type { ChainQuote, ChainRow, OptionChain } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Card, DataStamp, ErrorState, Field, Skeleton, cx } from "../ui";

type Col = { key: keyof ChainQuote; label: string; group: "price" | "oi" | "iv" | "greeks"; digits?: number; fmt?: "compact" };
const COLS: Col[] = [
  { key: "oi", label: "OI", group: "oi", fmt: "compact" },
  { key: "oi_change", label: "ΔOI", group: "oi", fmt: "compact" },
  { key: "volume", label: "Vol", group: "oi", fmt: "compact" },
  { key: "iv", label: "IV", group: "iv", digits: 1 },
  { key: "delta", label: "Δ", group: "greeks", digits: 2 },
  { key: "gamma", label: "Γ", group: "greeks", digits: 4 },
  { key: "theta", label: "Θ", group: "greeks", digits: 1 },
  { key: "vega", label: "V", group: "greeks", digits: 1 },
  { key: "bid", label: "Bid", group: "price", digits: 2 },
  { key: "ask", label: "Ask", group: "price", digits: 2 },
  { key: "ltp", label: "LTP", group: "price", digits: 2 },
];

function cell(q: ChainQuote | undefined, c: Col) {
  const v = q?.[c.key];
  if (typeof v !== "number") return "—";
  return c.fmt === "compact" ? compact(v) : num(v, c.digits ?? 2);
}

function Quote({ q, side }: { q?: ChainQuote; side: "CE" | "PE" }) {
  if (!q) return <p className="text-xs text-muted">No {side} quote</p>;
  const items: [string, string][] = [
    ["Bid / Ask", `${num(q.bid)} / ${num(q.ask)}`], ["LTP", num(q.ltp)], ["Spread", `${num(q.spread_pct, 2)}%`], ["IV", `${num(q.iv, 2)}%`],
    ["Δ", num(q.delta, 3)], ["Γ", num(q.gamma, 5)], ["Θ", num(q.theta, 2)], ["Vega", num(q.vega, 2)],
    ["OI", compact(q.oi)], ["ΔOI", compact(q.oi_change)], ["Volume", compact(q.volume)], ["Liquidity", q.liquid ? "passes filters" : "ILLIQUID"],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[11px]">
      {items.map(([k, v]) => (
        <Fragment key={k}><dt className="text-muted">{k}</dt><dd className={cx("num text-right", k === "Liquidity" && !q.liquid && "text-amber-300")}>{v}</dd></Fragment>
      ))}
    </dl>
  );
}

function MobileRow({ r, spot, atm }: { r: ChainRow; spot: number; atm: boolean }) {
  const [open, setOpen] = useState(false);
  const ceItm = r.strike < spot, peItm = r.strike > spot;
  return (
    <li className={cx("border-b border-edge", atm && "bg-blue-950/40")}>
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="grid w-full grid-cols-[1fr_4.5rem_1fr] items-center gap-1 px-1 py-1.5 text-left text-xs focus-visible:ring-2 focus-visible:ring-accent">
        <span className={cx("num rounded px-1", ceItm && "bg-amber-900/20", r.CE && !r.CE.liquid && "opacity-50")}>
          <span className="font-semibold">{num(r.CE?.ltp)}</span> <span className="text-muted">Δ{num(r.CE?.delta, 2)}</span>
          <span className="block text-[10px] text-muted">OI {compact(r.CE?.oi)}</span>
        </span>
        <span className={cx("num text-center font-semibold", atm && "text-blue-200")}>{num(r.strike, 0)}{atm && <span className="block text-[9px] text-blue-300">ATM</span>}</span>
        <span className={cx("num rounded px-1 text-right", peItm && "bg-amber-900/20", r.PE && !r.PE.liquid && "opacity-50")}>
          <span className="text-muted">Δ{num(r.PE?.delta, 2)}</span> <span className="font-semibold">{num(r.PE?.ltp)}</span>
          <span className="block text-[10px] text-muted">OI {compact(r.PE?.oi)}</span>
        </span>
      </button>
      {open && (
        <div className="grid grid-cols-2 gap-3 border-t border-edge/60 bg-panel2/40 px-2 py-2">
          <div><p className="mb-1 text-[11px] font-semibold text-red-300">{num(r.strike, 0)} CE</p><Quote q={r.CE} side="CE" /></div>
          <div><p className="mb-1 text-[11px] font-semibold text-green-300">{num(r.strike, 0)} PE</p><Quote q={r.PE} side="PE" /></div>
        </div>
      )}
    </li>
  );
}

export function ChainTab({ expiries, defaultExpiry }: { expiries: string[]; defaultExpiry: string }) {
  const [expiry, setExpiry] = useState(defaultExpiry);
  const [width, setWidth] = useState(15);
  const [groups, setGroups] = useState({ oi: true, iv: true, greeks: false });
  const q = useApi<OptionChain>(() => api.options.chain(expiry, width), [expiry, width]);
  const cols = COLS.filter((c) => c.group === "price" || groups[c.group]);
  const ch = q.data;

  return (
    <Card title="Option chain" right={ch ? <DataStamp meta={ch.data} asOf={ch.as_of} /> : null}>
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Field label="Expiry" htmlFor="oc-exp">
          <select id="oc-exp" className="input w-auto" value={expiry} onChange={(e) => setExpiry(e.target.value)}>
            {expiries.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
        </Field>
        <Field label="Strikes each side" htmlFor="oc-w">
          <select id="oc-w" className="input w-auto" value={width} onChange={(e) => setWidth(Number(e.target.value))}>
            {[5, 10, 15, 20, 40, 80].map((w) => <option key={w} value={w}>±{w}</option>)}
          </select>
        </Field>
        <fieldset className="hidden lg:block">
          <legend className="label">Columns</legend>
          <div className="flex gap-2 text-xs">
            {(["greeks", "oi", "iv"] as const).map((g) => (
              <label key={g} className="inline-flex items-center gap-1 rounded border border-edge px-2 py-1">
                <input type="checkbox" checked={groups[g]} onChange={(e) => setGroups((p) => ({ ...p, [g]: e.target.checked }))} /> {g === "oi" ? "OI / volume" : g === "iv" ? "IV" : "Greeks"}
              </label>
            ))}
          </div>
        </fieldset>
      </div>
      {q.error && <ErrorState error={q.error} onRetry={q.reload} what="option chain" />}
      {!ch && !q.error && <Skeleton className="h-80" />}
      {ch && (
        <>
          <p className="mb-2 text-xs text-muted">
            Spot <span className="num text-ink">{num(ch.spot)}</span> · ATM <span className="num text-blue-200">{num(ch.atm_strike, 0)}</span> · lot {ch.lot_size} · <span className="rounded bg-amber-900/30 px-1">shaded</span> = in the money · <span className="opacity-50">faded</span> = fails liquidity filters
          </p>
          {/* Desktop table */}
          <div className="hidden lg:block">
            <div className="max-h-[70vh] overflow-auto rounded border border-edge" role="region" aria-label="Option chain table" tabIndex={0}>
              <table className="tbl text-xs">
                <thead className="sticky top-0 z-10 bg-panel">
                  <tr>
                    <th colSpan={cols.length} scope="colgroup" className="text-center text-red-300">CALLS (CE)</th>
                    <th scope="col" className="text-center">Strike</th>
                    <th colSpan={cols.length} scope="colgroup" className="text-center text-green-300">PUTS (PE)</th>
                  </tr>
                  <tr>
                    {cols.map((c) => <th key={`c${c.key}`} scope="col" className="text-right">{c.label}</th>)}
                    <th scope="col" className="text-center">₹</th>
                    {[...cols].reverse().map((c) => <th key={`p${c.key}`} scope="col" className="text-right">{c.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {ch.rows.map((r) => {
                    const atm = r.strike === ch.atm_strike;
                    const ceItm = r.strike < ch.spot, peItm = r.strike > ch.spot;
                    return (
                      <tr key={r.strike} className={cx(atm && "outline outline-1 outline-blue-500")}>
                        {cols.map((c) => (
                          <td key={`c${c.key}`} className={cx("num text-right", ceItm && "bg-amber-900/20", r.CE && !r.CE.liquid && "opacity-50", c.key === "ltp" && "font-semibold")} title={r.CE && !r.CE.liquid ? "Fails liquidity filters" : undefined}>{cell(r.CE, c)}</td>
                        ))}
                        <th scope="row" className={cx("num whitespace-nowrap bg-panel2 text-center font-semibold", atm && "bg-blue-950 text-blue-200")}>{num(r.strike, 0)}</th>
                        {[...cols].reverse().map((c) => (
                          <td key={`p${c.key}`} className={cx("num text-right", peItm && "bg-amber-900/20", r.PE && !r.PE.liquid && "opacity-50", c.key === "ltp" && "font-semibold")} title={r.PE && !r.PE.liquid ? "Fails liquidity filters" : undefined}>{cell(r.PE, c)}</td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
          {/* Mobile / tablet compact list */}
          <div className="lg:hidden">
            <div className="grid grid-cols-[1fr_4.5rem_1fr] gap-1 border-b border-edge px-1 pb-1 text-[10px] font-semibold uppercase text-muted">
              <span className="text-red-300">CE ltp · Δ · OI</span><span className="text-center">Strike</span><span className="text-right text-green-300">PE Δ · ltp · OI</span>
            </div>
            <ul>{ch.rows.map((r) => <MobileRow key={r.strike} r={r} spot={ch.spot} atm={r.strike === ch.atm_strike} />)}</ul>
            <p className="mt-1 text-[11px] text-muted">Tap a strike for bid/ask, IV, Greeks and liquidity.</p>
          </div>
        </>
      )}
    </Card>
  );
}
