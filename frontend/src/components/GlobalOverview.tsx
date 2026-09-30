"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { isFx, moveClass, pct, price, px, signedPct } from "@/lib/format";
import { MARKETS, withMarket, type MarketId } from "@/lib/market";
import type { GlobalMarket, GlobalOverview as GO, IndexCard } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Sparkline } from "./Sparkline";
import { Card, DataStamp, ErrorState, Pill, Skeleton, cx } from "./ui";

export function cardPrice(c: IndexCard): string {
  if (c.asset_class === "INDEX") return px(c.price, { currency: c.currency });
  if (isFx(c.asset_class)) return px(c.price, { fx: true });
  return price(c.price, c.currency ?? "INR");
}

function regimeTone(family?: string, regime?: string) {
  if (family === "bull") return "green" as const;
  if (family === "bear" || /panic/i.test(regime ?? "")) return "red" as const;
  return "amber" as const;
}

function Row({ c }: { c: IndexCard }) {
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-0.5 py-1.5 sm:grid-cols-[minmax(0,1fr)_6rem_auto]">
      <Link href={`/stocks/${encodeURIComponent(c.symbol)}`} className="min-w-0 hover:underline">
        <span className="block truncate text-xs font-medium" title={c.name}>{c.name.replace(/\s*\(synthetic\)/i, "")}</span>
        <span className="block font-mono text-[10px] text-muted">{c.symbol}{c.trend ? ` · ${c.trend}` : ""}</span>
      </Link>
      <span className="hidden sm:block"><Sparkline values={c.sparkline} height={22} width={96} label={`${c.name} trend`} /></span>
      <span className="text-right">
        <span className="num block text-xs font-semibold">{cardPrice(c)}</span>
        <span className="num block text-[10px]"><span className={moveClass(c.change_1d_pct)}>{signedPct(c.change_1d_pct)}</span> · <span className={moveClass(c.change_1w_pct)}>1w {signedPct(c.change_1w_pct)}</span></span>
      </span>
    </li>
  );
}

function MarketCard({ id, m }: { id: MarketId; m: GlobalMarket }) {
  const meta = MARKETS.find((x) => x.id === id)!;
  const max = id === "FX" ? 8 : id === "CRYPTO" ? 6 : 5;
  const scan = m.scan;
  return (
    <Card title={meta.label} right={<span className="flex flex-wrap items-center gap-1">{m.regime && <Pill tone={regimeTone(m.regime.family, m.regime.regime)}>{m.regime.regime}</Pill>}{m.is_sample && <Pill tone="amber">SAMPLE</Pill>}</span>}>
      <ul className="divide-y divide-edge/60">{m.cards.slice(0, max).map((c) => <Row key={c.symbol} c={c} />)}</ul>
      {m.cards.length > max && <p className="mt-1 text-[11px] text-muted">+{m.cards.length - max} more</p>}
      {id === "CRYPTO" && (
        <div className="mt-2 rounded border border-edge bg-panel2/40 p-2 text-[11px]">
          <p>BTC dominance <span className="num font-semibold text-ink">{pct(m.btc_dominance_pct)}</span> <span className="text-muted">— {m.btc_dominance_basis ?? "basis not stated"}</span></p>
          <p className="mt-0.5 text-muted">Stablecoin flows: {m.stablecoin_flows?.available ? "available" : <span className="text-amber-200">unavailable{m.stablecoin_flows?.note ? ` — ${m.stablecoin_flows.note}` : ""}</span>}</p>
        </div>
      )}
      <div className="mt-2 border-t border-edge pt-2 text-[11px]">
        {scan ? (
          <>
            <p className={cx("font-semibold", scan.market_message ? "text-amber-300" : "text-green-300")}>{scan.market_message ?? `${scan.valid} valid setup${scan.valid === 1 ? "" : "s"}`}</p>
            <p className="text-muted">{scan.instruments_scanned} scanned · {scan.candidates_evaluated} candidates · {scan.valid} valid · {scan.no_trade} NO TRADE</p>
          </>
        ) : <p className="text-muted">No scan yet</p>}
        <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
          <DataStamp asOf={scan?.as_of ?? m.cards[0]?.as_of} meta={m.cards[0]?.data} />
          <span className="flex gap-2"><Link className="link" href={withMarket("/", id)}>Overview</Link><Link className="link" href={`${withMarket("/setups", id)}#sec-${["US", "EUROPE", "ASIA"].includes(id) ? "GLOBAL" : id}`}>Setups</Link></span>
        </div>
      </div>
    </Card>
  );
}

export function GlobalOverviewSection() {
  const q = useApi<GO>(() => api.markets.global(), []);
  return (
    <section aria-labelledby="global-h" className="mt-6">
      <h2 id="global-h" className="mb-2 text-base font-semibold">Global markets</h2>
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="global overview" /> : !q.data ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-64" />)}</div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {MARKETS.filter((m) => q.data!.markets[m.id]).map((m) => <MarketCard key={m.id} id={m.id} m={q.data!.markets[m.id]} />)}
        </div>
      )}
      <p className="mt-2 text-[11px] text-muted">Each market has its own calendar, benchmark, cost model and scan; prices are in each instrument&apos;s own currency. Regimes are rule-based descriptions, not forecasts.</p>
    </section>
  );
}
