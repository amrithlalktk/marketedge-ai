"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { AddToWatchlist } from "@/components/AddToWatchlist";
import { AnalystPanel } from "@/components/AnalystPanel";
import { StockEventsSection } from "@/components/StockEvents";
import { MlLine, MlPanel } from "@/components/MlBlocks";
import { SetupActions } from "@/components/SetupActions";
import { EventRiskBlock, NewsFlowBlock } from "@/components/NewsEvents";
import { INDICATORS, PriceChart, type IndicatorKey } from "@/components/PriceChart";
import { HitRate, ScoreBadge } from "@/components/SetupCard";
import { ChecksPanel, LevelsPanel, MtfPanel, ProbabilityPanel, ScoreBreakdown, WhyPanel } from "@/components/SetupSections";
import { Card, Collapsible, DataStamp, DirectionBadge, Disclaimer, EmptyState, ErrorState, Loading, Pill, Segmented, Skeleton, Stat, StatusBadge, TableWrap, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { compact, humanize, isFx, moveClass, num, price, px, rr, signedPct } from "@/lib/format";
import { marketLabel } from "@/lib/market";
import { CardExtras, DerivativesPanel, InrPanel, PipsPanel } from "@/components/MarketBlocks";
import type { Analysis, Candles, SetupDetail, StockDetail } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Mode = "hybrid" | "technical" | "fundamental";
const DEFAULT_ON: IndicatorKey[] = ["ema20", "ema50", "ema200", "volume", "levels", "patterns", "rsi"];

function ActiveSetup({ s }: { s: SetupDetail }) {
  const o = { fx: isFx(s.market), ref: s.current_price };
  return (
    <article className="rounded-lg border border-edge bg-panel2/40 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold">{s.strategy.name}</span>
            <DirectionBadge d={s.direction} />
            <StatusBadge s={s.status} />
          </div>
          <p className="mt-1 text-xs text-muted">
            Entry {px(s.entry_zone[0], o)}–{px(s.entry_zone[1], o)} · Stop <span className="text-down">{px(s.stop, o)}</span> · T1 <span className="text-up">{px(s.targets[0], o)}</span> · T2{" "}
            <span className="text-up">{px(s.targets[1], o)}</span> · R:R {rr(s.rr_t2)} · {s.currency}
          </p>
          <div className="mt-1"><HitRate p={s.probability} compact /></div>
          <div className="mt-1"><CardExtras s={s} /><MlLine ml={s.ml} /></div>
          <div className="mt-1"><DataStamp meta={s.data} asOf={s.as_of} /></div>
        </div>
        <ScoreBadge score={s.score} label={s.score_label} />
      </div>
      {s.status !== "VALID" && (
        <ul className="mt-2 text-xs text-amber-200">
          {s.checks.filter((c) => !c.passed && c.severity === "block").map((c) => <li key={c.name}>✗ {c.name}: {c.detail}</li>)}
        </ul>
      )}
      {s.signal_id && <div className="mt-3"><SetupActions s={s} signalId={s.signal_id} /></div>}
      <div className="mt-3">
        <Collapsible title="Full setup analysis">
          <div className="space-y-3">
            {s.events && <EventRiskBlock ev={s.events} checks={s.checks} />}
            {s.news && <NewsFlowBlock n={s.news} />}
            {s.inr && <InrPanel i={s.inr} currency={s.currency} />}
            {s.pips && <PipsPanel p={s.pips} />}
            {s.derivatives && <DerivativesPanel d={s.derivatives} marketCap={s.market_cap_usd} spreadBps={s.spread_bps} />}
            <LevelsPanel s={s} />
            <ScoreBreakdown components={s.components} score={s.score} label={s.score_label} notes={s.score_notes} />
            <WhyPanel e={s.explanation} />
            <ProbabilityPanel p={s.probability} />
            <MlPanel ml={s.ml} />
            <ChecksPanel checks={s.checks} />
            <MtfPanel m={s.mtf} />
          </div>
        </Collapsible>
      </div>
    </article>
  );
}

function AnalysisSection({ symbol, onSetup, market }: { symbol: string; onSetup: (s: SetupDetail | null) => void; market?: string }) {
  const fxm = isFx(market);
  const [mode, setMode] = useState<Mode>("hybrid");
  const a = useApi<Analysis>(async () => {
    const r = await api.stocks.analysis(symbol, mode);
    onSetup(r.active_setups[0] ?? null);
    return r;
  }, [symbol, mode]);
  const d = a.data;
  return (
    <Card
      title="Analysis"
      id="analysis"
      right={market && market !== "NSE" ? <span className="text-xs text-muted">technical/hybrid analysis</span> : <Segmented<Mode> label="Analysis mode" value={mode} onChange={setMode} options={[{ value: "technical", label: "Technical" }, { value: "fundamental", label: "Fundamental" }, { value: "hybrid", label: "Hybrid" }]} />}
    >
      {a.error && <ErrorState error={a.error} onRetry={a.reload} what="analysis" />}
      {a.loading && <Loading label="Evaluating strategies…" />}
      {d && !a.loading && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge s={d.status === "NO_SETUP" ? "NO SETUP" : d.status} />
            <DataStamp meta={d.data} asOf={d.as_of} />
            {mode === "fundamental" && <span className="text-xs text-amber-300">Fundamental mode re-weights scores; without a licensed fundamentals feed its component is unavailable.</span>}
          </div>
          {d.active_setups.length === 0 ? (
            <EmptyState title="No active setup">None of the {d.inactive_strategies.length} strategies triggered on the latest bar.</EmptyState>
          ) : (
            <div className="space-y-3">{d.active_setups.map((s) => <ActiveSetup key={s.strategy.id} s={s} />)}</div>
          )}
          {d.inactive_strategies.length > 0 && (
            <p className="text-xs text-muted">Not triggered: {d.inactive_strategies.map((s) => `${s.name} (${s.direction})`).join(" · ")}</p>
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Patterns</h3>
              {d.state.patterns.length ? <div className="flex flex-wrap gap-1">{d.state.patterns.map((p) => <Pill key={p} tone="blue">{p}</Pill>)}</div> : <p className="text-xs text-muted">No pattern detected.</p>}
              <h3 className="mb-1 mt-3 text-xs font-semibold uppercase tracking-wide text-muted">Structure</h3>
              <dl className="grid grid-cols-2 gap-1 text-xs sm:grid-cols-3">
                {Object.entries(d.state.structure).map(([k, v]) => (
                  <div key={k} className="rounded border border-edge px-2 py-1">
                    <dt className="text-muted">{humanize(k)}</dt>
                    <dd className={cx("num", v === true && "text-up", v === false && "text-muted")}>{v == null ? "—" : typeof v === "boolean" ? (v ? "Yes" : "No") : px(v, { fx: fxm })}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Indicator state</h3>
              <dl className="grid grid-cols-3 gap-1 text-xs sm:grid-cols-4">
                {Object.entries(d.state.indicators).map(([k, v]) => (
                  <div key={k} className="rounded border border-edge px-2 py-1">
                    <dt className="text-muted">{k}</dt>
                    <dd className="num">{v == null ? "—" : Math.abs(v) >= 1e5 ? compact(v) : Math.abs(v) < 1 && v !== 0 ? px(v) : num(v, 2)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <MtfPanel m={d.state.mtf_long} title="Multi-timeframe (long bias)" />
            <Card title="Support / resistance levels">
              {d.state.levels.length === 0 ? <EmptyState title="No confirmed levels" /> : (
                <ul className="divide-y divide-edge text-sm">
                  {[...d.state.levels].sort((x, y) => y.price - x.price).map((l, li) => (
                    <li key={`${l.kind}-${l.price}-${li}`} className="grid grid-cols-[6rem_1fr] items-center gap-x-3 py-1 sm:grid-cols-[6rem_6rem_1fr]">
                      <span className={l.kind === "support" ? "text-up" : "text-down"}>{l.kind}</span>
                      <span className="num">{px(l.price, { fx: fxm })}</span>
                      <span className="col-span-2 text-xs text-muted sm:col-span-1 sm:text-right">{l.touches} touches · last {l.last_touch}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Signal history (forward-tracked, published VALID setups)</h3>
            {d.signal_history.length === 0 ? (
              <EmptyState title="No published setups for this symbol yet" />
            ) : (
              <TableWrap label="Signal history">
                <table className="tbl min-w-[560px]">
                  <thead><tr><th scope="col">Date</th><th scope="col">Strategy</th><th scope="col">Dir</th><th scope="col" className="text-right">Score</th><th scope="col">Outcome</th><th scope="col">T1</th><th scope="col">Stop</th><th scope="col" className="text-right">Net</th></tr></thead>
                  <tbody>
                    {d.signal_history.map((h, hi) => (
                      <tr key={`${h.as_of}-${h.strategy}-${hi}`}>
                        <td className="num">{h.as_of}</td><td>{h.strategy}</td><td><DirectionBadge d={h.direction} /></td><td className="num text-right">{num(h.score, 0)}</td>
                        <td>{h.outcome ?? <span className="text-muted">open</span>}</td><td>{h.t1_hit == null ? "—" : h.t1_hit ? "✓" : "✗"}</td><td>{h.stop_hit == null ? "—" : h.stop_hit ? "hit" : "no"}</td>
                        <td className={cx("num text-right", moveClass(h.net_return_pct))}>{signedPct(h.net_return_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

export default function StockPage() {
  const { symbol: raw } = useParams<{ symbol: string }>();
  const symbol = decodeURIComponent(raw).toUpperCase();
  const [interval, setIv] = useState<"1d" | "1w">("1d");
  const [limit, setLimit] = useState(250);
  const [on, setOn] = useState<Set<IndicatorKey>>(new Set(DEFAULT_ON));
  const [setup, setSetup] = useState<SetupDetail | null>(null);
  const [showTrade, setShowTrade] = useState(true);
  const info = useApi<StockDetail>(() => api.stocks.get(symbol), [symbol]);
  const candles = useApi<Candles>(() => api.stocks.candles(symbol, interval, limit), [symbol, interval, limit]);
  const fxm = isFx(info.data?.market ?? info.data?.asset_class);
  useEffect(() => {
    if (fxm) setOn((prev) => { const n = new Set(prev); n.delete("volume"); return n; });
  }, [fxm]);

  if (info.error) return <><ErrorState error={info.error} onRetry={info.reload} what={symbol} /><p className="mt-3"><Link href="/stocks" className="link text-sm">← Back to stocks</Link></p></>;
  const d = info.data;
  const qt = d?.quote;
  const toggle = (k: IndicatorKey) => setOn((prev) => { const n = new Set(prev); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  return (
    <>
      <nav aria-label="Breadcrumb" className="mb-2 text-xs text-muted"><Link href="/stocks" className="link">Stocks</Link> / <span>{symbol}</span></nav>
      <header className="card mb-4">
        {!d ? <Skeleton className="h-20" /> : (
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="font-mono text-xl font-bold">{d.symbol}</h1>
                {d.is_index && <Pill>INDEX</Pill>}
                {d.is_sample && <Pill tone="amber">SAMPLE</Pill>}
                {d.delisted_on && <Pill tone="red">Delisted {d.delisted_on}</Pill>}
              </div>
              <p className="truncate text-sm text-muted">{d.name} · {d.exchange}{d.sector ? ` · ${d.sector}` : ""}{d.market === "NSE" ? ` · lot ${d.lot_size}` : ""}</p>
              <p className="text-xs text-muted">{marketLabel(d.market)} · exchange {d.exchange} · {fxm ? `quoted in ${d.currency}` : `prices in ${d.currency}`} · {d.asset_class}</p>
              {qt && (
                <p className="mt-1 flex flex-wrap items-baseline gap-x-3">
                  <span className="num text-2xl font-semibold">{price(qt.price, d.currency, { fx: fxm })}</span>
                  <span className={cx("num text-sm font-semibold", moveClass(qt.change_1d_pct))}>{signedPct(qt.change_1d_pct)} 1D</span>
                  <span className={cx("num text-sm", moveClass(qt.change_1w_pct))}>{signedPct(qt.change_1w_pct)} 1W</span>
                </p>
              )}
              <DataStamp meta={d.data} asOf={qt?.as_of} className="mt-1" />
            </div>
            <AddToWatchlist symbol={d.symbol} />
          </div>
        )}
        {qt && (
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="52w high" value={price(qt.high_52w, d?.currency, { fx: fxm })} />
            <Stat label="52w low" value={price(qt.low_52w, d?.currency, { fx: fxm })} />
            <Stat label="Volume" value={fxm ? "n/a (FX)" : compact(qt.volume)} />
            <Stat label="From 52w high" value={signedPct(100 * (qt.price / qt.high_52w - 1))} valueClass={moveClass(qt.price / qt.high_52w - 1)} />
          </div>
        )}
      </header>

      <Card title="Chart" right={<DataStamp meta={candles.data?.data} asOf={candles.data?.bars.t.at(-1)} />}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Segmented<"1d" | "1w"> label="Interval" value={interval} onChange={setIv} options={[{ value: "1d", label: "Daily" }, { value: "1w", label: "Weekly" }]} />
          <label className="sr-only" htmlFor="limit">Bars</label>
          <select id="limit" className="input w-auto py-1 text-xs" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {[120, 250, 500, 1000].map((n) => <option key={n} value={n}>{n} bars</option>)}
          </select>
          {setup && (
            <label className="inline-flex items-center gap-1 text-xs">
              <input type="checkbox" checked={showTrade} onChange={(e) => setShowTrade(e.target.checked)} /> Show setup levels
            </label>
          )}
        </div>
        <fieldset className="mb-3">
          <legend className="sr-only">Indicators</legend>
          <div className="flex flex-wrap gap-1">
            {INDICATORS.map((ind) => (
              <label key={ind.key} className={cx("inline-flex cursor-pointer items-center gap-1 rounded border px-2 py-0.5 text-xs", on.has(ind.key) ? "border-accent bg-blue-950/40 text-ink" : "border-edge text-muted")}>
                <input type="checkbox" className="sr-only" checked={on.has(ind.key)} onChange={() => toggle(ind.key)} />
                <span aria-hidden className="inline-block h-2 w-2 rounded-full" style={{ background: ind.color }} />
                {ind.label}
              </label>
            ))}
          </div>
        </fieldset>
        {candles.error ? <ErrorState error={candles.error} onRetry={candles.reload} what="candles" /> : candles.data ? (
          <PriceChart fx={fxm} candles={candles.data} enabled={on} trade={setup && showTrade ? { entry: setup.entry_zone, stop: setup.stop, targets: setup.targets } : undefined} height={360} />
        ) : <Skeleton className="h-[360px]" />}
        {on.has("patterns") && candles.data && !["geo_upper", "geo_lower", "cup_rim"].some((k) => (candles.data!.bars[k] as (number | null)[] | undefined)?.some((x) => x != null)) && <p className="mt-2 text-[11px] text-muted">No geometric pattern (triangle / rectangle / cup rim) in the displayed bars.</p>}
        {on.has("patterns") && <p className="mt-1 text-[11px] text-muted">Patterns: amber dashed = triangle/rectangle bounds, purple dashed = cup-and-handle rim.</p>}
        <p className="mt-2 text-[11px] text-muted">Charts by TradingView Lightweight Charts™. Weekly bars are resampled from daily data (weeks ending Friday).</p>
      </Card>

      <div className="mt-4"><AnalysisSection symbol={symbol} onSetup={setSetup} market={d?.market} /></div>

      <div className="mt-4"><StockEventsSection symbol={symbol} /></div>
      <div className="mt-4"><AnalystPanel symbol={symbol} title={`Ask the analyst about ${symbol}`} /></div>
      <div className="mt-4">
        <Card title="Fundamentals">
          {!d ? <Skeleton className="h-10" /> : d.market && d.market !== "NSE" ? (
            <EmptyState title="No fundamentals feed for this market">{fxm ? "Currency pairs have no company fundamentals; rate-differential and macro-calendar feeds are not configured." : "Scores for this market use technical components only."}</EmptyState>
          ) : d.fundamentals_available && d.fundamentals ? (
            <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              {Object.entries(d.fundamentals).map(([k, v]) => (
                <div key={k} className="rounded border border-edge px-2 py-1"><dt className="text-xs text-muted">{humanize(k)}</dt><dd className="num">{typeof v === "number" ? num(v) : String(v ?? "—")}</dd></div>
              ))}
            </dl>
          ) : (
            <EmptyState title="No licensed fundamentals feed configured">Fundamental metrics and the fundamental score component are unavailable; scores redistribute that weight across technical components.</EmptyState>
          )}
        </Card>
      </div>
      <Disclaimer />
    </>
  );
}
