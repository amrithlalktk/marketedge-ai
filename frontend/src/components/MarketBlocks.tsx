import { hoursUntil, inr, integer, moveClass, num, pct, signedPct } from "@/lib/format";
import { EventRow } from "./NewsEvents";
import type { DerivativesBlock, InrBlock, MarketExtras, PipsBlock } from "@/lib/types";
import { Card, Pill, Stat, cx } from "./ui";

export function usdCompact(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  return `$${integer(v)}`;
}

/** One-line INR / pips / crypto context for setup cards. */
export function CardExtras({ s }: { s: MarketExtras & { currency?: string | null } }) {
  const i = s.inr;
  const now = Date.now();
  const hot = (s.events?.upcoming ?? []).filter((e) => e.impact === "High" && hoursUntil(e.event_time, now) >= 0 && hoursUntil(e.event_time, now) <= 72);
  return (
    <div className="space-y-1 text-xs">
      {hot.length > 0 && (
        <p className="text-amber-300">⚠ Event risk: {hot.slice(0, 2).map((e) => `${e.name.replace(/\s*\(sample\)/i, "")} (${e.country}) in ${Math.round(hoursUntil(e.event_time, now))}h`).join("; ")}</p>
      )}
      {s.news && s.news.articles > 0 && (
        <p className="text-muted">News {s.news.window_days}d: <span className="text-up">{s.news.counts.Positive ?? 0}+</span> / {s.news.counts.Neutral ?? 0}= / <span className="text-down">{s.news.counts.Negative ?? 0}−</span> · net {num(s.news.net_score, 2)} <span className="text-[10px]">(context only)</span></p>
      )}
      {i && i.available !== false && i.risk_per_unit_inr != null && (
        <p className="text-muted">
          ₹ per unit: risk <span className="num text-down">{inr(i.risk_per_unit_inr)}</span> · T2 <span className="num text-up">{inr(i.reward_t2_per_unit_inr)}</span>
          {i.example && <> · ₹10,000 risk ≈ <span className="num text-ink">{integer(i.example.units)}</span> units</>}
          <span className="block text-[10px]">{i.from}→INR {num(i.rate, 4)} via {(i.via ?? []).join(" → ")} · {i.rate_as_of}</span>
        </p>
      )}
      {i && i.available === false && <p className="text-[11px] text-amber-300">INR equivalent unavailable: {i.note}</p>}
      {s.pips && (
        <p className="text-muted">
          Stop <span className="num text-down">{num(s.pips.stop_pips, 1)}</span> pips · T1 <span className="num text-up">{num(s.pips.t1_pips, 1)}</span> · T2 <span className="num text-up">{num(s.pips.t2_pips, 1)}</span> pips
          {s.pips.example_lots != null && <> · ≈ {num(s.pips.example_lots, 2)} std lots per ₹10k risk</>}
        </p>
      )}
      {(s.market_cap_usd != null || s.spread_bps != null) && (
        <p className="text-muted">
          Mkt cap <span className="num text-ink">{usdCompact(s.market_cap_usd)}</span> · spread <span className={cx("num", (s.spread_bps ?? 0) > 20 ? "text-down" : "text-ink")}>{num(s.spread_bps, 1)} bps</span>
        </p>
      )}
      {s.derivatives?.available && s.derivatives.funding && (
        <p className="text-muted">
          Funding <span className="num text-ink">{pct(s.derivatives.funding.latest_pct_8h, 4)}</span>/8h ({s.derivatives.funding.state}) · OI 7d <span className={cx("num", moveClass(s.derivatives.open_interest?.change_7d_pct))}>{signedPct(s.derivatives.open_interest?.change_7d_pct)}</span> <span className="text-[10px]">(context only)</span>
        </p>
      )}
    </div>
  );
}

export function InrPanel({ i, currency }: { i: InrBlock; currency?: string }) {
  if (i.available === false) {
    return (
      <Card title="INR equivalent">
        <p className="text-sm text-amber-200">Unavailable — {i.note}</p>
      </Card>
    );
  }
  const ex = i.example;
  return (
    <Card title={`INR equivalent (${i.from ?? currency} → INR)`} right={i.is_sample ? <Pill tone="amber">SAMPLE rate</Pill> : null}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Price in ₹" value={inr(i.price_inr)} />
        <Stat label="Risk / unit" value={inr(i.risk_per_unit_inr)} valueClass="text-down" />
        <Stat label="Reward T1 / unit" value={inr(i.reward_t1_per_unit_inr)} valueClass="text-up" />
        <Stat label="Reward T2 / unit" value={inr(i.reward_t2_per_unit_inr)} valueClass="text-up" />
      </div>
      {ex && (
        <div className="mt-3 rounded border border-edge bg-panel2/40 p-2 text-sm">
          <p className="font-semibold">Example: risking {inr(ex.risk_budget_inr, 0)}</p>
          <p className="mt-0.5 text-xs text-muted">
            ≈ <span className="num text-ink">{integer(ex.units)}</span> units · position value <span className="num text-ink">{inr(ex.position_value_inr, 0)}</span> · reward at T2 <span className="num text-up">{inr(ex.reward_t2_inr, 0)}</span> (before costs; stop fills at its price)
          </p>
        </div>
      )}
      <p className="mt-2 text-xs text-muted">
        Rate <span className="num text-ink">{num(i.rate, 4)}</span> {i.from}/INR · conversion path {(i.via ?? []).join(" → ") || "direct"} · rate as of <span className="num">{i.rate_as_of}</span>
      </p>
      <p className="mt-1 text-xs text-amber-200/90">⚠ {i.note}</p>
    </Card>
  );
}

export function PipsPanel({ p }: { p: PipsBlock }) {
  return (
    <Card title="Forex: pips & lot size">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Stop" value={`${num(p.stop_pips, 1)} pips`} valueClass="text-down" />
        <Stat label="T1" value={`${num(p.t1_pips, 1)} pips`} valueClass="text-up" />
        <Stat label="T2" value={`${num(p.t2_pips, 1)} pips`} valueClass="text-up" />
        <Stat label="Pip size" value={String(p.pip_size)} />
      </div>
      <p className="mt-2 text-xs text-muted">
        Standard lot = {new Intl.NumberFormat("en-US").format(p.standard_lot_units)} units of the base currency.
        {p.example_lots != null ? <> Risking ₹10,000 at this stop ≈ <span className="num text-ink">{num(p.example_lots, 2)}</span> standard lots.</> : " Example lot size unavailable (no INR rate)."}
      </p>
      <ul className="mt-2 space-y-0.5 text-xs text-muted">
        <li>Rate differential (carry): {p.rate_differential.available ? "available" : <span className="text-amber-200">unavailable — {p.rate_differential.note}</span>}</li>
        {!p.macro_calendar.available && <li>Macro calendar: <span className="text-amber-200">unavailable — {p.macro_calendar.note}</span></li>}
      </ul>
      {p.macro_calendar.available && (
        <div className="mt-2">
          <p className="text-xs font-semibold text-ink">Macro calendar for this pair</p>
          {(p.macro_calendar.upcoming ?? []).length ? <ul className="divide-y divide-edge">{(p.macro_calendar.upcoming ?? []).map((e) => <EventRow key={`${e.name}-${e.event_time}`} e={e} />)}</ul> : <p className="text-xs text-muted">No upcoming releases for these currencies in the window.</p>}
          <p className="mt-1 text-[11px] text-muted">{p.macro_calendar.note}</p>
        </div>
      )}
      <p className="mt-1 text-[11px] text-muted">Forex has no consolidated volume; FX setups use price-only rules.</p>
    </Card>
  );
}

export function DerivativesPanel({ d, marketCap, spreadBps, exchange }: { d: DerivativesBlock; marketCap?: number | null; spreadBps?: number | null; exchange?: string }) {
  const f = d.funding, oi = d.open_interest, ls = d.long_short_ratio;
  return (
    <Card title="Crypto: derivatives & market context" right={<Pill tone="blue">context only</Pill>}>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Market cap" value={usdCompact(marketCap)} />
        <Stat label="Bid/ask spread" value={spreadBps != null ? `${num(spreadBps, 1)} bps` : "—"} valueClass={(spreadBps ?? 0) > 20 ? "text-down" : undefined} sub="block above 20 bps" />
        <Stat label="Venue" value={exchange ?? "—"} />
        <Stat label="As of" value={d.as_of ?? "—"} />
      </div>
      {!d.available ? (
        <p className="mt-3 text-sm text-amber-200">Derivatives data unavailable for this instrument.</p>
      ) : (
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          <div className="rounded border border-edge bg-panel2/40 p-2 text-xs">
            <p className="font-semibold text-ink">Funding rate</p>
            {f ? (
              <>
                <p className="num mt-1 text-base">{pct(f.latest_pct_8h, 4)} <span className="text-xs text-muted">/ 8h</span></p>
                <p className="text-muted">7-day avg {pct(f.avg_7d_pct_8h, 4)}/8h · annualised <span className="num text-ink">{pct(f.annualised_pct, 1)}</span></p>
                <p className="mt-0.5">State: <Pill tone={/extreme|high|crowded/i.test(f.state) ? "amber" : "slate"}>{f.state}</Pill></p>
              </>
            ) : <p className="text-muted">n/a</p>}
          </div>
          <div className="rounded border border-edge bg-panel2/40 p-2 text-xs">
            <p className="font-semibold text-ink">Open interest</p>
            {oi ? (
              <>
                <p className="num mt-1 text-base">{usdCompact(oi.latest).replace("$", "")}</p>
                <p className="text-muted">1d <span className={cx("num", moveClass(oi.change_1d_pct))}>{signedPct(oi.change_1d_pct)}</span> · 7d <span className={cx("num", moveClass(oi.change_7d_pct))}>{signedPct(oi.change_7d_pct)}</span> · price 7d <span className={cx("num", moveClass(oi.price_change_7d_pct))}>{signedPct(oi.price_change_7d_pct)}</span></p>
                <p className="mt-0.5">{oi.interpretation}</p>
              </>
            ) : <p className="text-muted">n/a</p>}
          </div>
          <div className="rounded border border-edge bg-panel2/40 p-2 text-xs">
            <p className="font-semibold text-ink">Long/short ratio</p>
            {ls ? (
              <>
                <p className="num mt-1 text-base">{num(ls.latest, 3)}</p>
                <p>State: <Pill>{ls.state}</Pill></p>
                <p className="mt-0.5 text-muted">{ls.note}</p>
              </>
            ) : <p className="text-muted">n/a</p>}
          </div>
        </div>
      )}
      <p className="mt-2 text-xs text-muted">Liquidations: {d.liquidations?.available ? "available" : <span className="text-amber-200">unavailable{d.liquidations?.note ? ` — ${d.liquidations.note}` : ""}</span>}</p>
      {d.checks && d.checks.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-amber-200">{d.checks.map((c) => <li key={c.name}>⚠ {c.name}: {c.detail}</li>)}</ul>
      )}
      <p className="mt-2 rounded border border-blue-900 bg-blue-950/30 px-2 py-1.5 text-xs text-blue-100">ⓘ {d.note ?? "Derivatives data is shown as context and warnings only; it is not part of the score or the historical hit rates."}</p>
    </Card>
  );
}
