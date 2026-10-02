"use client";

import Link from "next/link";
import { Suspense } from "react";
import { GlobalOverviewSection, cardPrice } from "@/components/GlobalOverview";
import { MarketSwitcher } from "@/components/MarketSwitcher";
import { MlRegimeCard } from "@/components/MlBlocks";
import { SetupCard } from "@/components/SetupCard";
import { Sparkline } from "@/components/Sparkline";
import { Bar, Card, DataStamp, Disclaimer, EmptyState, ErrorState, PageHeader, Pill, Skeleton, Stat, TableWrap, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { integer, moveClass, num, pct, signedPct } from "@/lib/format";
import { featureOn, marketLabel, sectorWord, useMarket, withMarket, type MarketId } from "@/lib/market";
import type { Breadth, IndexCard, Overview, Regime, ScanSummary, Sectors } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function IndexTile({ ix }: { ix: IndexCard }) {
  return (
    <article className="card flex min-w-0 flex-col gap-2" aria-label={ix.name}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold" title={ix.name}>
            {ix.name}
          </h3>
          <p className="font-mono text-[11px] text-muted">{ix.symbol}</p>
        </div>
        <Pill tone={/bull/i.test(ix.trend) ? "green" : /bear/i.test(ix.trend) ? "red" : "slate"}>{ix.trend}</Pill>
      </div>
      <div className="num text-xl font-semibold">{cardPrice(ix)}</div>
      <div className="flex flex-wrap gap-x-3 text-xs">
        <span>
          1D <span className={cx("num font-semibold", moveClass(ix.change_1d_pct))}>{signedPct(ix.change_1d_pct)}</span>
        </span>
        <span>
          1W <span className={cx("num font-semibold", moveClass(ix.change_1w_pct))}>{signedPct(ix.change_1w_pct)}</span>
        </span>
        <span className="text-muted">
          Vol 20d <span className="num text-ink">{pct(ix.realized_vol_20d_pct)}</span>
        </span>
      </div>
      <Sparkline values={ix.sparkline} label={`${ix.name} 60-session trend`} />
      <DataStamp meta={ix.data} asOf={ix.as_of} />
    </article>
  );
}

function RegimePanel({ r }: { r: Regime | null }) {
  if (!r) return <Card title="Market regime"><EmptyState title="Regime not computed yet" /></Card>;
  const tone = r.family === "bull" ? "green" : r.family === "bear" || /panic/i.test(r.regime) ? "red" : "amber";
  return (
    <Card title="Market regime" right={<DataStamp asOf={r.as_of} />}>
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={tone}>{r.regime}</Pill>
        <Pill tone={r.volatility === "High" || r.volatility === "Extreme" ? "amber" : "slate"}>Volatility: {r.volatility}</Pill>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <div className="mb-1 flex justify-between text-xs"><span className="text-muted">Long favourability</span><span className="num">{r.long_favorability ?? "—"}/100</span></div>
          <Bar value={r.long_favorability} tone="green" />
        </div>
        <div>
          <div className="mb-1 flex justify-between text-xs"><span className="text-muted">Short favourability</span><span className="num">{r.short_favorability ?? "—"}/100</span></div>
          <Bar value={r.short_favorability} tone="red" />
        </div>
      </div>
      <ul className="mt-3 space-y-1 text-xs text-muted">
        {r.reasons.map((x) => (
          <li key={x}>• {x}</li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-muted">Favourability is a rule-based description of current conditions, not a forecast.</p>
    </Card>
  );
}

function BreadthPanel({ b }: { b: Breadth | null }) {
  if (!b) return <Card title="Market breadth"><EmptyState title="Breadth not computed yet" /></Card>;
  const rows: [string, number | null][] = [
    ["% above 20 DMA", b.pct_above_20dma],
    ["% above 50 DMA", b.pct_above_50dma],
    ["% above 200 DMA", b.pct_above_200dma],
  ];
  return (
    <Card title="Market breadth" right={<DataStamp asOf={b.as_of} meta={{ is_sample: b.is_sample }} />}>
      <div className="space-y-2">
        {rows.map(([k, v]) => (
          <div key={k}>
            <div className="mb-1 flex justify-between text-xs"><span className="text-muted">{k}</span><span className="num">{pct(v)}</span></div>
            <Bar value={v} />
          </div>
        ))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Adv / Dec" value={<><span className="text-up">{b.advances}</span> / <span className="text-down">{b.declines}</span></>} sub={`A/D ${num(b.ad_ratio)}`} />
        <Stat label="New highs" value={integer(b.new_highs)} valueClass="text-up" />
        <Stat label="New lows" value={integer(b.new_lows)} valueClass="text-down" />
        {b.up_volume_pct != null ? <Stat label="Up volume" value={pct(b.up_volume_pct)} /> : <Stat label="Up volume" value="n/a" sub="no volume in this market" />}
      </div>
      <div className="mt-3">
        <div className="mb-1 flex justify-between text-[11px] text-muted">
          <span>% above 50 DMA — last {b.history.length} sessions</span>
          {b.pct_above_50dma_change_5d != null && <span className={cx("num", moveClass(b.pct_above_50dma_change_5d))}>5d {signedPct(b.pct_above_50dma_change_5d, 1)} pts</span>}
        </div>
        <Sparkline values={b.history.map((h) => h[1])} height={40} tone="neutral" domain={[0, 100]} label="Percent of members above 50-day moving average history" />
      </div>
      <p className="mt-2 text-[11px] text-muted">Universe: {b.members} instruments.</p>
    </Card>
  );
}

function SectorTable({ s, market }: { s: Sectors | null; market: MarketId }) {
  const word = sectorWord(market);
  const hasVol = !!s?.sectors.some((r) => r.volume_trend != null);
  return (
    <Card title={`${word.singular} rotation`} right={s ? <DataStamp asOf={s.as_of} meta={{ is_sample: s.is_sample }} /> : null}>
      {!s || s.sectors.length === 0 ? (
        <EmptyState title={`No ${word.singular.toLowerCase()} data yet`} />
      ) : (
        <>
          <TableWrap label={`${word.singular} rotation table`}>
            <table className="tbl min-w-[560px]">
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">{word.singular}</th>
                  <th scope="col" className="text-right">20d</th>
                  <th scope="col" className="text-right">63d</th>
                  <th scope="col" className="text-right">RS 63d</th>
                  <th scope="col" className="text-right">&gt;50 DMA</th>
                  <th scope="col" className="text-right">Uptrend</th>
                  {hasVol && <th scope="col" className="text-right">Vol trend</th>}
                </tr>
              </thead>
              <tbody>
                {s.sectors.map((r) => (
                  <tr key={r.sector}>
                    <td className="num text-muted">{r.rank}</td>
                    <td className="font-medium">{r.sector} <span className="text-[11px] text-muted">({r.members})</span></td>
                    <td className={cx("num text-right", moveClass(r.ret20_pct))}>{signedPct(r.ret20_pct)}</td>
                    <td className={cx("num text-right", moveClass(r.ret63_pct))}>{signedPct(r.ret63_pct)}</td>
                    <td className={cx("num text-right", moveClass(r.relative_strength_63d))}>{signedPct(r.relative_strength_63d)}</td>
                    <td className="num text-right">{pct(r.pct_above_50dma, 0)}</td>
                    <td className="num text-right">{pct(r.pct_uptrend, 0)}</td>
                    {hasVol && <td className="num text-right">{r.volume_trend == null ? "—" : `${num(r.volume_trend)}×`}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <p className="mt-2 text-[11px] text-muted">{s.note || "Rankings describe current relative strength and are not forecasts."}</p>
        </>
      )}
    </Card>
  );
}

function ScanBanner({ scan }: { scan: ScanSummary | null }) {
  if (!scan) return null;
  const blocked = !!scan.market_message;
  return (
    <section
      aria-label="Scan summary"
      className={cx("rounded-lg border p-3 sm:p-4", blocked ? "border-amber-800 bg-amber-950/40" : "border-green-900 bg-green-950/30")}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className={cx("text-sm font-bold sm:text-base", blocked ? "text-amber-200" : "text-green-200")}>
          {scan.market_message ?? `${scan.valid} setup${scan.valid === 1 ? "" : "s"} passed every validation check`}
        </p>
        <DataStamp asOf={scan.as_of} meta={{ is_sample: scan.is_sample }} />
      </div>
      <p className="mt-1 text-xs text-muted">
        Scanned <span className="num text-ink">{scan.instruments_scanned}</span> instruments · <span className="num text-ink">{scan.candidates_evaluated}</span> candidates evaluated ·{" "}
        <span className="num text-up">{scan.valid}</span> valid · <span className="num text-amber-300">{scan.no_trade}</span> rejected (NO TRADE)
        {scan.stale_instruments && scan.stale_instruments.length > 0 && <> · {scan.stale_instruments.length} with stale data</>}
      </p>
      {blocked && <p className="mt-1 text-xs text-muted">Standing aside is a valid outcome: the engine only publishes setups that pass every blocking check.</p>}
    </section>
  );
}

function DashboardInner() {
  const { can } = useAuth();
  const [market] = useMarket();
  const ov = useApi<Overview>(() => api.markets.overview(market), [market]);
  const sectors = useApi<Sectors>(() => api.markets.sectors(market), [market]);
  const top = useApi(() => api.signals.top({ limit: 3, market }), [market], can("signals:read"));
  const label = marketLabel(market);

  return (
    <>
      <PageHeader title={`Market dashboard — ${label}`} subtitle={ov.data?.profile ? String(ov.data.profile) : "End-of-day analysis"} right={<MarketSwitcher />} />
      {ov.error && <ErrorState error={ov.error} onRetry={ov.reload} what={`${label} overview`} />}
      {ov.loading && !ov.data && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-40" />)}
        </div>
      )}
      {ov.data && (
        <div className="space-y-4">
          <ScanBanner scan={ov.data.scan} />
          <section aria-label={`${label} overview`} className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {ov.data.indices.map((ix) => <IndexTile key={ix.symbol} ix={ix} />)}
          </section>
          {market === "CRYPTO" && ov.data.btc_dominance_pct != null && (
            <p className="rounded border border-edge bg-panel p-2 text-xs">BTC dominance <span className="num font-semibold">{pct(ov.data.btc_dominance_pct)}</span> <span className="text-muted">— {ov.data.btc_dominance_basis}</span>{ov.data.stablecoin_flows && !ov.data.stablecoin_flows.available && <span className="block text-amber-200">Stablecoin flows unavailable — {ov.data.stablecoin_flows.note}</span>}</p>
          )}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <RegimePanel r={ov.data.regime} />
            {featureOn("ml") && <MlRegimeCard r={ov.data.regime_ml} />}
          </div>
          {ov.data.breadth ? <BreadthPanel b={ov.data.breadth} /> : <Card title="Market breadth"><EmptyState title="Breadth not available for this market" /></Card>}
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-5">
        <div className="min-w-0 xl:col-span-3">
          {sectors.error ? <ErrorState error={sectors.error} onRetry={sectors.reload} what={`${sectorWord(market).singular.toLowerCase()} rotation`} /> : <SectorTable s={sectors.data} market={market} />}
        </div>
        <div className="min-w-0 xl:col-span-2">
          <Card title={`Top setups — ${label}`} right={<Link href={withMarket("/setups", market)} className="link text-xs">View all →</Link>}>
            {!can("signals:read") ? (
              <EmptyState title="Setups are not included in your plan" />
            ) : top.error ? (
              <ErrorState error={top.error} onRetry={top.reload} what="top setups" />
            ) : top.loading ? (
              <Skeleton className="h-32" />
            ) : top.data && top.data.items.length > 0 ? (
              <div className="space-y-3">
                {top.data.items.map((s) => <SetupCard key={s.id} s={s} />)}
              </div>
            ) : (
              <div className="rounded-md border border-amber-800 bg-amber-950/30 p-4 text-center">
                <p className="text-sm font-bold text-amber-200">NO VALID SETUP</p>
                <p className="mt-1 text-xs text-muted">No candidate passed every validation check in the latest {label} scan.</p>
                {can("signals:read_all") && (
                  <Link href={withMarket("/setups?tab=no_trade", market)} className="link mt-2 inline-block text-xs">
                    See rejected candidates and why →
                  </Link>
                )}
              </div>
            )}
          </Card>
        </div>
      </div>

      <GlobalOverviewSection />
      <Disclaimer />
    </>
  );
}

export default function Dashboard() {
  return (
    <Suspense>
      <DashboardInner />
    </Suspense>
  );
}
