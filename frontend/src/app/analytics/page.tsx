"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { MarketSwitcher } from "@/components/MarketSwitcher";
import { marketLabel, sectorWord, useMarket } from "@/lib/market";
import { Card, DataStamp, Disclaimer, EmptyState, ErrorState, Field, Loading, PageHeader, Stat, TableWrap, UpgradeNote, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { humanize, integer, moveClass, num, pct, period, signedPct } from "@/lib/format";
import type { HistBin, HitRateExplore, HitRates, Sectors, StrategiesResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const REGIMES = ["Strong Bull", "Weak Bull", "Range", "Recovery", "Bear", "Panic/Selloff"];
const GROUPINGS = ["regime", "score_bucket", "year", "sector", "symbol", "direction", "strategy_id", "exit_reason"];

interface Filters {
  strategy: string;
  regime: string;
  score_bucket: string;
  sector: string;
  symbol: string;
  start: string;
  end: string;
  group_by: string;
  min_group: string;
}
const EMPTY: Filters = { strategy: "", regime: "", score_bucket: "", sector: "", symbol: "", start: "", end: "", group_by: "regime", min_group: "5" };

function useBoxWidth(initial = 640) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function ci(r: HitRates) {
  return r.t1_ci95 ? `${pct(r.t1_ci95[0])}–${pct(r.t1_ci95[1])}` : "—";
}

/** Horizontal bars of T1 hit rate per group with 95% CI whiskers and n labels. */
function RateBars({ groups, overall }: { groups: (HitRates & { key: string })[]; overall?: number }) {
  const [box, W] = useBoxWidth();
  const rowH = 22, labelW = Math.min(130, W * 0.32), nW = 56, H = groups.length * rowH + 24;
  const plotW = W - labelW - nW - 8;
  const sx = (v: number) => labelW + (Math.max(0, Math.min(100, v)) / 100) * plotW;
  return (
    <div ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" role="img" aria-label={`Historical T1 hit rate by group: ${groups.map((g) => `${g.key} ${g.t1_hit_rate}% (n=${g.sample_size})`).join("; ")}`}>
        {[0, 25, 50, 75, 100].map((t) => (
          <g key={t}>
            <line x1={sx(t)} x2={sx(t)} y1={4} y2={H - 18} stroke="#1c2733" />
            <text x={sx(t)} y={H - 5} textAnchor="middle" fontSize="10" fill="#8b9bb0">{t}%</text>
          </g>
        ))}
        {overall != null && <line x1={sx(overall)} x2={sx(overall)} y1={4} y2={H - 18} stroke="#f5a524" strokeDasharray="3 3"><title>{`Overall ${overall}%`}</title></line>}
        {groups.map((g, i) => {
          const y = 6 + i * rowH;
          const v = g.t1_hit_rate ?? 0;
          return (
            <g key={g.key}>
              <text x={labelW - 6} y={y + 12} textAnchor="end" fontSize="11" fill="#cbd5e1">{g.key.length > 18 ? `${g.key.slice(0, 17)}…` : g.key}</text>
              <rect x={labelW} y={y + 3} width={Math.max(0, sx(v) - labelW)} height={rowH - 9} fill="#3b82f6" rx="2" />
              {g.t1_ci95 && (
                <g stroke="#e6edf5" strokeWidth="1.5">
                  <line x1={sx(g.t1_ci95[0])} x2={sx(g.t1_ci95[1])} y1={y + 7.5} y2={y + 7.5} />
                  <line x1={sx(g.t1_ci95[0])} x2={sx(g.t1_ci95[0])} y1={y + 3} y2={y + 12} />
                  <line x1={sx(g.t1_ci95[1])} x2={sx(g.t1_ci95[1])} y1={y + 3} y2={y + 12} />
                </g>
              )}
              <text x={W - 4} y={y + 12} textAnchor="end" fontSize="10" fill="#8b9bb0">{`${v.toFixed(1)}% n=${g.sample_size}`}</text>
            </g>
          );
        })}
      </svg>
      <p className="text-[11px] text-muted">Bars = historical T1 hit rate; whiskers = 95% Wilson interval; amber dashed line = overall rate. Wide whiskers mean the sample is too small to distinguish groups.</p>
    </div>
  );
}

function Histogram({ title, bins, unit, zeroSplit }: { title: string; bins?: HistBin[]; unit: string; zeroSplit?: boolean }) {
  const [box, W] = useBoxWidth(300);
  if (!bins || bins.length === 0) return <Card title={title}><EmptyState title="No data" /></Card>;
  const H = 140, pad = 18;
  const max = Math.max(1, ...bins.map((b) => b.count));
  const total = bins.reduce((a, b) => a + b.count, 0);
  const bw = (W - 8) / bins.length;
  return (
    <Card title={title}>
      <div ref={box}>
        <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" role="img" aria-label={`${title} histogram, ${total} trades: ${bins.map((b) => `${b.from} to ${b.to}${unit}: ${b.count}`).join(", ")}`}>
          {bins.map((b, i) => {
            const h = ((H - pad - 6) * b.count) / max;
            const color = zeroSplit ? (b.to <= 0 ? "#f05252" : b.from >= 0 ? "#22c55e" : "#64748b") : "#60a5fa";
            return (
              <g key={i}>
                <rect x={4 + i * bw + 1} y={H - pad - h} width={Math.max(1, bw - 2)} height={h} fill={color} opacity="0.85"><title>{`${b.from}–${b.to}${unit}: ${b.count} (${pct((100 * b.count) / Math.max(1, total), 1)})`}</title></rect>
                {(i % Math.ceil(bins.length / 6) === 0 || i === bins.length - 1) && <text x={4 + i * bw} y={H - 4} fontSize="9" fill="#8b9bb0">{b.from}</text>}
              </g>
            );
          })}
        </svg>
      </div>
      <p className="text-[11px] text-muted">n = {integer(total)} trades · bins in {unit.trim() || "units"}</p>
    </Card>
  );
}

type SortK = "key" | "sample_size" | "t1_hit_rate" | "t2_hit_rate" | "stop_rate" | "expectancy_r" | "avg_return_pct";

function AnalyticsInner() {
  const { can } = useAuth();
  const [market] = useMarket();
  const allowed = can("signals:read_all");
  const strategies = useApi<StrategiesResponse>(() => api.strategies.list(), [], allowed);
  const sectors = useApi<Sectors>(() => api.markets.sectors(market), [market], allowed);
  const stratKeys = useApi<HitRateExplore>(() => api.analytics.hitRates({ market, group_by: "strategy_id", min_group: 1 }), [market], allowed);
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [sort, setSort] = useState<{ k: SortK; dir: 1 | -1 }>({ k: "sample_size", dir: -1 });
  useEffect(() => {
    setDraft((p) => ({ ...p, strategy: "", sector: "", symbol: "" }));
    setApplied((p) => ({ ...p, strategy: "", sector: "", symbol: "" }));
  }, [market]);
  const q = useApi<HitRateExplore>(() => {
    const f = applied;
    return api.analytics.hitRates({
      market,
      strategy: f.strategy || undefined, regime: f.regime || undefined, score_bucket: f.score_bucket || undefined, sector: f.sector || undefined,
      symbol: f.symbol.trim() || undefined, start: f.start || undefined, end: f.end || undefined, group_by: f.group_by || undefined, min_group: Number(f.min_group) || 5,
    });
  }, [JSON.stringify(applied), market], allowed);

  if (!allowed) return <><PageHeader title="Hit-rate explorer" /><UpgradeNote feature="The hit-rate explorer" /><Disclaimer /></>;
  const word = sectorWord(market);
  const d = q.data;
  const set = (k: keyof Filters, v: string) => setDraft((p) => ({ ...p, [k]: v }));
  const names = new Map<string, string>([...(strategies.data?.builtin ?? []).map((s) => [s.id, s.name] as [string, string]), ...(strategies.data?.custom ?? []).map((s) => [s.id, `${s.name} (custom)`] as [string, string])]);
  // Strategies that actually have historical events in this market (FX uses price-only px_* rules).
  const stratOpts = (stratKeys.data?.groups ?? []).map((g) => ({ id: g.key, name: `${names.get(g.key) ?? g.key} (n=${g.sample_size})` }));
  const groups = d ? [...d.groups].sort((a, b) => {
    const va = a[sort.k as keyof typeof a], vb = b[sort.k as keyof typeof b];
    if (typeof va === "string" || typeof vb === "string") return String(va).localeCompare(String(vb)) * sort.dir;
    return ((Number(va ?? -Infinity)) - (Number(vb ?? -Infinity))) * sort.dir;
  }) : [];
  const th = (k: SortK, label: string, right = true) => (
    <th scope="col" className={right ? "text-right" : undefined} aria-sort={sort.k === k ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="inline-flex items-center gap-0.5 uppercase hover:text-ink" onClick={() => setSort((s) => ({ k, dir: s.k === k ? (s.dir === 1 ? -1 : 1) : k === "key" ? 1 : -1 }))}>
        {label}{sort.k === k ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
      </button>
    </th>
  );
  const o = d?.overall;

  return (
    <>
      <PageHeader title={`Hit-rate explorer — ${marketLabel(market)}`} subtitle="Slice the historical events behind every published probability. Every rate is shown with its sample size." right={<div className="flex flex-col items-end gap-1"><MarketSwitcher />{d && <DataStamp asOf={d.as_of} />}</div>} />
      <form className="card mb-4" onSubmit={(e) => { e.preventDefault(); setApplied(draft); }} aria-label="Filters">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <Field label="Strategy" htmlFor="f-st">
            <select id="f-st" className="input" value={draft.strategy} onChange={(e) => set("strategy", e.target.value)}>
              <option value="">All strategies</option>
              {stratOpts.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Regime at signal" htmlFor="f-rg">
            <select id="f-rg" className="input" value={draft.regime} onChange={(e) => set("regime", e.target.value)}>
              <option value="">Any</option>{REGIMES.map((r) => <option key={r}>{r}</option>)}
            </select>
          </Field>
          <Field label="Score bucket" htmlFor="f-sb">
            <select id="f-sb" className="input" value={draft.score_bucket} onChange={(e) => set("score_bucket", e.target.value)}>
              <option value="">Any</option>{["75+", "60-75", "<60"].map((r) => <option key={r}>{r}</option>)}
            </select>
          </Field>
          <Field label={word.singular} htmlFor="f-sc">
            <select id="f-sc" className="input" value={draft.sector} onChange={(e) => set("sector", e.target.value)}>
              <option value="">Any</option>{(sectors.data?.sectors ?? []).map((s) => <option key={s.sector}>{s.sector}</option>)}
            </select>
          </Field>
          <Field label="Symbol" htmlFor="f-sy"><input id="f-sy" className="input font-mono" maxLength={64} placeholder={market === "FX" ? "e.g. DEMO_EURUSD" : market === "CRYPTO" ? "e.g. DEMO_BTC" : "e.g. DEMO_001"} value={draft.symbol} onChange={(e) => set("symbol", e.target.value.toUpperCase())} /></Field>
          <Field label="From (signal date)" htmlFor="f-s"><input id="f-s" type="date" className="input" value={draft.start} onChange={(e) => set("start", e.target.value)} /></Field>
          <Field label="To (exit date)" htmlFor="f-e"><input id="f-e" type="date" className="input" value={draft.end} onChange={(e) => set("end", e.target.value)} /></Field>
          <Field label="Group by" htmlFor="f-g">
            <select id="f-g" className="input" value={draft.group_by} onChange={(e) => set("group_by", e.target.value)}>
              <option value="">No grouping</option>{(d?.groupings ?? GROUPINGS).map((g) => <option key={g} value={g}>{humanize(g)}</option>)}
            </select>
          </Field>
          <Field label="Min trades per group" htmlFor="f-m"><input id="f-m" className="input num" type="number" min={1} max={1000} step={1} value={draft.min_group} onChange={(e) => set("min_group", e.target.value)} /></Field>
          <div className="flex items-end gap-2">
            <button className="btn-primary">Apply</button>
            <button type="button" className="btn-ghost" onClick={() => { setDraft(EMPTY); setApplied(EMPTY); }}>Reset</button>
          </div>
        </div>
      </form>

      {q.error && <ErrorState error={q.error} onRetry={q.reload} what="hit rates" />}
      {q.loading && <Loading label="Computing…" />}
      {d && !q.loading && (
        !o || !o.sample_size ? <EmptyState title="No historical trades match these filters">Widen the filters or check the symbol.</EmptyState> : (
          <div className="space-y-4">
            <Card title="Overall" right={<span className="text-xs text-muted">{period(d.period)} · scan #{d.scan_run_id}</span>}>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                <Stat label="Trades (n)" value={integer(o.sample_size)} />
                <Stat label="Historical T1 hit rate" value={pct(o.t1_hit_rate)} sub={`95% CI ${ci(o)} · n=${o.sample_size}`} />
                <Stat label="Historical T2 hit rate" value={pct(o.t2_hit_rate)} sub={o.t2_ci95 ? `95% CI ${pct(o.t2_ci95[0])}–${pct(o.t2_ci95[1])}` : undefined} />
                <Stat label="Stop-first rate" value={pct(o.stop_rate)} sub={o.stop_ci95 ? `95% CI ${pct(o.stop_ci95[0])}–${pct(o.stop_ci95[1])}` : undefined} />
                <Stat label="Expectancy" value={`${num(o.expectancy_r, 3)} R`} valueClass={moveClass(o.expectancy_r)} sub={`avg return ${signedPct(o.avg_return_pct)}`} />
                <Stat label="Avg hold" value={`${num(o.avg_holding_bars, 1)} bars`} sub={`neither ${pct(o.neither_rate)}`} />
              </div>
              {Object.keys(d.filters).length > 0 && <p className="mt-2 text-xs text-muted">Filters: {Object.entries(d.filters).map(([k, v]) => `${humanize(k)} = ${v}`).join(" · ")}</p>}
            </Card>

            {d.group_by && (
              <Card title={`By ${humanize(d.group_by)} (${groups.length} groups with ≥ ${applied.min_group} trades)`}>
                {groups.length === 0 ? <EmptyState title="No group meets the minimum size" /> : (
                  <div className="space-y-4">
                    <RateBars groups={d.group_by === "year" ? [...d.groups] : [...groups].sort((a, b) => b.sample_size - a.sample_size)} overall={o.t1_hit_rate} />
                    <TableWrap label="Grouped hit rates">
                      <table className="tbl min-w-[640px] text-xs">
                        <thead><tr>{th("key", humanize(d.group_by), false)}{th("sample_size", "n")}{th("t1_hit_rate", "T1 rate")}<th scope="col" className="text-right">T1 95% CI</th>{th("t2_hit_rate", "T2 rate")}{th("stop_rate", "Stop rate")}{th("expectancy_r", "Exp. (R)")}{th("avg_return_pct", "Avg ret")}</tr></thead>
                        <tbody>
                          {groups.map((g) => (
                            <tr key={g.key}>
                              <th scope="row" className="text-left font-medium">{g.key}</th><td className="num text-right">{integer(g.sample_size)}</td>
                              <td className="num text-right">{pct(g.t1_hit_rate)}</td><td className="num text-right text-muted">{ci(g)}</td>
                              <td className="num text-right">{pct(g.t2_hit_rate)}</td><td className="num text-right">{pct(g.stop_rate)}</td>
                              <td className={cx("num text-right", moveClass(g.expectancy_r))}>{num(g.expectancy_r, 3)}</td><td className={cx("num text-right", moveClass(g.avg_return_pct))}>{signedPct(g.avg_return_pct)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </TableWrap>
                  </div>
                )}
              </Card>
            )}

            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <Histogram title="R-multiple (clipped −3…6)" bins={d.distributions.r_multiple} unit="R" zeroSplit />
              <Histogram title="MFE — max favourable excursion" bins={d.distributions.mfe_r} unit="R" />
              <Histogram title="MAE — max adverse excursion" bins={d.distributions.mae_r} unit="R" />
              <Histogram title="Bars held" bins={d.distributions.bars_held} unit=" bars" />
            </div>
            <div className="grid grid-cols-2 gap-2 sm:max-w-md">
              <Stat label="Reached ≥ 1R in favour" value={pct(d.distributions.pct_reaching_1r_mfe)} sub={`n=${integer(o.sample_size)}`} />
              <Stat label="Heat ≥ 0.5R against" value={pct(d.distributions.pct_heat_over_0_5r)} sub={`n=${integer(o.sample_size)}`} />
            </div>
            <Card title="Notes & definitions">
              <p className="text-sm">{d.note}</p>
              <dl className="mt-2 space-y-0.5 text-xs">{Object.entries(d.definitions).map(([k, v]) => <div key={k}><dt className="inline font-mono text-muted">{k}: </dt><dd className="inline">{v}</dd></div>)}</dl>
              <p className="mt-2 text-[11px] text-muted">Events come from the latest scan&apos;s historical replay of the stated rules (same-bar rule: stop assumed first). Groups smaller than the minimum are hidden, not merged.</p>
            </Card>
          </div>
        )
      )}
      <Disclaimer />
    </>
  );
}

export default function AnalyticsPage() {
  return (
    <Suspense>
      <AnalyticsInner />
    </Suspense>
  );
}
