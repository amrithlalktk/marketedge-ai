"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { marketOn } from "@/lib/market";
import { DASH, inr, price, rr } from "@/lib/format";
import type { OptionSetup, ProbabilitySummary, SetupSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Card, DataStamp, DirectionBadge, Pill, Skeleton, cx } from "./ui";

/** "52%" with the sample behind it, or a dash. Never shown without the number of past cases. */
function Chance({ p }: { p?: Partial<ProbabilitySummary> | null }) {
  if (!p || p.t1_hit_rate == null || !p.sample_size) return <span className="text-muted">{DASH}</span>;
  const tone = p.t1_hit_rate >= 55 ? "text-up" : p.t1_hit_rate >= 45 ? "text-ink" : "text-amber-300";
  return (
    <span>
      <span className={cx("text-lg font-bold", tone)}>{Math.round(p.t1_hit_rate)}%</span>
      <span className="ml-1 text-xs text-muted">
        reach Target 1 · {p.t2_hit_rate != null && <>{Math.round(p.t2_hit_rate)}% Target 2 · </>}
        {p.stop_rate != null && <>{Math.round(p.stop_rate)}% stop first · </>}
        {p.sample_size} past cases
      </span>
    </span>
  );
}

function Levels({ entry, stop, t1, t2 }: { entry: string; stop: string; t1: string; t2: string }) {
  return (
    <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-sm sm:grid-cols-4">
      <div><dt className="text-[11px] uppercase text-muted">Entry</dt><dd className="num font-semibold">{entry}</dd></div>
      <div><dt className="text-[11px] uppercase text-muted">Stop loss</dt><dd className="num font-semibold text-down">{stop}</dd></div>
      <div><dt className="text-[11px] uppercase text-muted">Exit 1 (Target 1)</dt><dd className="num font-semibold text-up">{t1}</dd></div>
      <div><dt className="text-[11px] uppercase text-muted">Exit 2 (Target 2)</dt><dd className="num font-semibold text-up">{t2}</dd></div>
    </dl>
  );
}

function OptionIdea({ o }: { o: OptionSetup }) {
  const c = o.contract!;
  return (
    <li className="rounded-md border border-edge bg-panel2/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-bold">{c.label}</span>
        <span className="text-xs text-muted">lot {c.lot_size} · {c.dte} days to expiry</span>
        <Pill tone="blue">NIFTY option</Pill>
        <DirectionBadge d={o.direction} />
      </div>
      <Levels entry={`₹${o.entry}`} stop={`₹${o.stop}`} t1={`₹${o.targets?.[0]}`} t2={`₹${o.targets?.[1]}`} />
      <div className="mt-2"><Chance p={o.probability} /></div>
      <p className="mt-1 text-xs text-muted">
        Premium per lot {inr(o.premium_per_lot, 0)} · max loss at stop ≈ {inr(o.risk_per_lot, 0)} per lot · reward:risk {rr(o.rr)}
        {o.underlying && <> · triggered by NIFTY {o.underlying.strategy.name} (index stop {o.underlying.stop}, targets {o.underlying.targets.join(" / ")})</>}
      </p>
      <p className="mt-1 text-[11px] text-muted">Chance is how often the NIFTY setup reached its target before its stop historically; option prices are model estimates.</p>
      <Link className="link mt-1 inline-block text-xs" href="/options?tab=setups">Details →</Link>
    </li>
  );
}

function StockIdea({ s }: { s: SetupSummary }) {
  const p = (v: number) => price(v, s.currency);
  return (
    <li className="rounded-md border border-edge bg-panel2/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Link href={`/stocks/${encodeURIComponent(s.symbol)}`} className="text-base font-bold hover:underline">{s.symbol}</Link>
        <span className="text-xs text-muted">{s.strategy_name}</span>
        <Pill tone="slate">{s.market === "CRYPTO" ? "Crypto" : "NSE stock"}</Pill>
        <DirectionBadge d={s.direction} />
        <span className="text-xs text-muted">score {Math.round(s.score)}</span>
      </div>
      <Levels entry={s.entry_zone[0] === s.entry_zone[1] ? p(s.entry_zone[0]) : `${p(s.entry_zone[0])}–${p(s.entry_zone[1])}`}
        stop={p(s.stop)} t1={p(s.targets[0])} t2={p(s.targets[1])} />
      <div className="mt-2"><Chance p={s.probability} /></div>
      <p className="mt-1 text-xs text-muted">reward:risk {rr(s.rr_t2)} · risk {s.risk_pct.toFixed(1)}% to stop{s.risk_factors[0] ? ` · ⚠ ${s.risk_factors[0]}` : ""}</p>
      <Link className="link mt-1 inline-block text-xs" href={s.id ? `/setups/${s.id}` : `/stocks/${encodeURIComponent(s.symbol)}`}>Details →</Link>
    </li>
  );
}

/**
 * Today's trade ideas across NIFTY options, NSE stocks and crypto: contract / stock, entry, stop loss, exits and the
 * historical chance. Only VALID setups (every safety check passed) are shown as ideas; otherwise "no trade today".
 */
export function TradeIdeas() {
  const { can } = useAuth();
  const optOn = marketOn("NSE") && can("options:signals");
  const opts = useApi(() => api.options.signals(), [], optOn);
  const nse = useApi(() => api.signals.top({ limit: 5, market: "NSE" }), [], marketOn("NSE") && can("signals:read"));
  const cry = useApi(() => api.signals.top({ limit: 3, market: "CRYPTO" }), [], marketOn("CRYPTO") && can("signals:read"));

  const optionIdeas = (opts.data?.items ?? []).filter((o) => o.status === "VALID" && o.contract);
  const stockIdeas = [...(nse.data?.items ?? []), ...(cry.data?.items ?? [])].filter((s) => s.status === "VALID");
  const loading = (optOn && opts.loading) || nse.loading || cry.loading;
  const none = !loading && optionIdeas.length === 0 && stockIdeas.length === 0;

  return (
    <Card title="Today's trade ideas" right={<span className="text-xs text-muted">updated after each daily scan (NSE 18:30 IST)</span>}>
      {loading && optionIdeas.length + stockIdeas.length === 0 ? (
        <div className="space-y-2"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
      ) : none ? (
        <div className="space-y-2 text-sm">
          <p className="text-base font-semibold">No trade today</p>
          <p className="text-muted">
            No NIFTY option or stock setup passed every safety check in the latest scan. Not trading is the right call on days like this.
          </p>
          <ul className="list-disc pl-5 text-xs text-muted">
            {opts.data?.market_message && <li>NIFTY options: {opts.data.market_message}</li>}
            {nse.data?.market_message && <li>NSE stocks: {nse.data.market_message}</li>}
            {cry.data?.market_message && <li>Crypto: {cry.data.market_message}</li>}
          </ul>
          <Link className="link text-xs" href="/setups?tab=no_trade">See the rejected candidates and why →</Link>
        </div>
      ) : (
        <ul className="space-y-2">
          {optionIdeas.map((o, i) => <OptionIdea key={`o${i}`} o={o} />)}
          {stockIdeas.map((s) => <StockIdea key={`${s.market}${s.id}`} s={s} />)}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted">
        <span>
          Chance = how often this exact setup reached the target before the stop in past data, with the number of cases. It is not a guarantee;
          decide your position size with the Risk calculator and always place the stop loss.
        </span>
        {nse.data && <DataStamp asOf={nse.data.as_of} />}
      </div>
    </Card>
  );
}
