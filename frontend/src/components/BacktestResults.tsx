"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { humanize, moveClass, num, pct, period, px, signedPct } from "@/lib/format";
import type { BacktestResult, BacktestTrade, HitRates } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { PortfolioSection, YearlyStability } from "./BacktestExtras";
import { EquityChart } from "./EquityChart";
import { Card, DirectionBadge, EmptyState, ErrorState, Pill, Skeleton, Stat, TableWrap, cx } from "./ui";

export function Warnings({ items }: { items: string[] | null | undefined }) {
  if (!items || items.length === 0) return null;
  return (
    <div role="alert" className="rounded-lg border-2 border-amber-600 bg-amber-950/50 p-3">
      <p className="text-sm font-bold text-amber-200">⚠ Overfitting & validity warnings</p>
      <ul className="mt-1 space-y-1 text-sm text-amber-100">
        {items.map((w) => {
          const tooGood = /too good/i.test(w);
          return (
            <li key={w} className={tooGood ? "rounded border border-red-700 bg-red-950/60 px-2 py-1 font-semibold text-red-100" : undefined}>
              {tooGood ? "⛔ " : "• "}{w}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function HitRatesTable({ rows, label, firstCol = "Segment" }: { rows: [string, HitRates][]; label: string; firstCol?: string }) {
  if (rows.length === 0) return <EmptyState title="No data" />;
  return (
    <TableWrap label={label}>
      <table className="tbl min-w-[680px]">
        <thead>
          <tr>
            <th scope="col">{firstCol}</th><th scope="col">Period</th><th scope="col" className="text-right">Trades</th>
            <th scope="col" className="text-right">T1 rate</th><th scope="col" className="text-right">T1 95% CI</th><th scope="col" className="text-right">T2 rate</th>
            <th scope="col" className="text-right">Stop rate</th><th scope="col" className="text-right">Neither</th><th scope="col" className="text-right">Exp. (R)</th><th scope="col" className="text-right">Avg ret</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, r]) => (
            <tr key={k}>
              <th scope="row" className="text-left font-medium">{humanize(k)}</th>
              <td className="num whitespace-nowrap text-xs text-muted">{period(r.period ?? null)}</td>
              <td className="num text-right">{r.sample_size}</td>
              <td className="num text-right">{pct(r.t1_hit_rate)}</td>
              <td className="num text-right text-xs text-muted">{r.t1_ci95 ? `${pct(r.t1_ci95[0])}–${pct(r.t1_ci95[1])}` : "—"}</td>
              <td className="num text-right">{pct(r.t2_hit_rate)}</td>
              <td className="num text-right">{pct(r.stop_rate)}</td>
              <td className="num text-right">{pct(r.neither_rate)}</td>
              <td className={cx("num text-right", moveClass(r.expectancy_r))}>{num(r.expectancy_r, 3)}</td>
              <td className={cx("num text-right", moveClass(r.avg_return_pct))}>{signedPct(r.avg_return_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function heat(v: number | undefined): string {
  if (v == null) return "transparent";
  const a = Math.min(1, Math.abs(v) / 8) * 0.75 + 0.08;
  return v > 0 ? `rgba(34,197,94,${a})` : v < 0 ? `rgba(240,82,82,${a})` : "rgba(100,116,139,0.15)";
}

export function ReturnsHeatmap({ monthly, yearly }: { monthly: Record<string, number>; yearly: Record<string, number> }) {
  const years = Array.from(new Set([...Object.keys(monthly).map((k) => k.slice(0, 4)), ...Object.keys(yearly)])).sort();
  if (years.length === 0) return <EmptyState title="No monthly returns" />;
  return (
    <TableWrap label="Monthly and yearly returns">
      <table className="tbl min-w-[720px] text-xs">
        <thead>
          <tr><th scope="col">Year</th>{MONTHS.map((m) => <th key={m} scope="col" className="text-right">{m}</th>)}<th scope="col" className="text-right">Year</th></tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y}>
              <th scope="row" className="num text-left">{y}</th>
              {MONTHS.map((_, i) => {
                const v = monthly[`${y}-${String(i + 1).padStart(2, "0")}`];
                return <td key={i} className="num text-right" style={{ background: heat(v) }}>{v == null ? "" : num(v, 1)}</td>;
              })}
              <td className={cx("num text-right font-semibold", moveClass(yearly[y]))}>{yearly[y] == null ? "—" : signedPct(yearly[y], 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function TradesTable({ id }: { id: number }) {
  const [page, setPage] = useState(1);
  const size = 50;
  const q = useApi(() => api.backtests.trades(id, page, size), [id, page]);
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="trades" />;
  if (!q.data) return <Skeleton className="h-40" />;
  const pages = Math.max(1, Math.ceil(q.data.total / size));
  return (
    <>
      <TableWrap label="Backtest trades">
        <table className="tbl min-w-[900px] text-xs">
          <thead>
            <tr>
              <th scope="col">Signal</th><th scope="col">Symbol</th><th scope="col">Dir</th><th scope="col">Entry date</th><th scope="col" className="text-right">Entry</th>
              <th scope="col" className="text-right">Stop</th><th scope="col" className="text-right">T1</th><th scope="col" className="text-right">T2</th><th scope="col">Exit date</th>
              <th scope="col" className="text-right">Exit</th><th scope="col">Reason</th><th scope="col" className="text-right">Bars</th><th scope="col" className="text-right">Net %</th><th scope="col" className="text-right">R</th><th scope="col">Regime</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((t: BacktestTrade) => (
              <tr key={t.id}>
                <td className="num whitespace-nowrap">{t.signal_date}</td><td className="font-mono">{t.symbol}</td><td><DirectionBadge d={t.direction} /></td>
                <td className="num whitespace-nowrap">{t.entry_date}</td><td className="num text-right">{px(t.entry, { ref: t.entry })}</td><td className="num text-right">{px(t.stop, { ref: t.entry })}</td>
                <td className={cx("num text-right", t.t1_hit && "text-up")}>{px(t.t1, { ref: t.entry })}</td><td className={cx("num text-right", t.t2_hit && "text-up")}>{px(t.t2, { ref: t.entry })}</td>
                <td className="num whitespace-nowrap">{t.exit_date}</td><td className="num text-right">{px(t.exit_price, { ref: t.entry })}</td>
                <td><Pill tone={t.exit_reason === "stop" ? "red" : t.t1_hit ? "green" : "slate"}>{t.exit_reason}</Pill></td>
                <td className="num text-right">{t.bars_held}</td>
                <td className={cx("num text-right", moveClass(t.net_return_pct))}>{signedPct(t.net_return_pct)}</td>
                <td className={cx("num text-right", moveClass(t.r_multiple))}>{num(t.r_multiple, 2)}</td>
                <td className="text-muted">{t.regime ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      <nav aria-label="Trades pagination" className="mt-2 flex items-center justify-center gap-2 text-sm">
        <button className="btn-ghost" disabled={page <= 1 || q.loading} onClick={() => setPage((p) => p - 1)}>← Prev</button>
        <span className="num text-muted">Page {page} / {pages} · {q.data.total} trades</span>
        <button className="btn-ghost" disabled={page >= pages || q.loading} onClick={() => setPage((p) => p + 1)}>Next →</button>
      </nav>
    </>
  );
}

function CsvButton({ id }: { id: number }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <span className="inline-flex flex-col items-end">
      <button type="button" className="btn-ghost" disabled={busy} onClick={async () => {
        setBusy(true); setErr(null);
        try { await api.backtests.downloadCsv(id); } catch (e) { setErr(e instanceof Error ? e.message : "Export failed"); } finally { setBusy(false); }
      }}>{busy ? "Exporting…" : "⬇ Export trades CSV"}</button>
      {err && <span role="alert" className="mt-1 text-xs text-down">{err}</span>}
    </span>
  );
}

export function BacktestResults({ id, r, market }: { id: number; r: BacktestResult; market?: string }) {
  const s = r.summary;
  const mc = r.monte_carlo;
  const segs = Object.entries(r.segments ?? {});
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-xs text-muted">Strategy <strong className="text-ink">{r.strategy.name}</strong> ({r.strategy.id}) · {r.strategy.direction} · universe {r.universe_size} instruments</p>
        <CsvButton id={id} />
      </div>
      <Warnings items={r.warnings} />
      <h2 className="text-base font-semibold">Strategy-level statistics <span className="text-sm font-normal text-muted">— per-trade R, every signal counted independently</span></h2>
      <Card title={`Summary — ${r.strategy.name}`} right={<span className="text-xs text-muted">{period(r.period)} · universe {r.universe_size} · costs {r.costs.commission_pct}% + {r.costs.slippage_pct}% slippage per side</span>}>
        {!s.sample_size ? <EmptyState title="No trades were generated for this configuration" /> : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
            <Stat label="Trades" value={s.total_trades ?? s.sample_size} sub={`${s.winning_trades ?? "—"} W / ${s.losing_trades ?? "—"} L`} />
            <Stat label="Win rate" value={pct(s.win_rate)} />
            <Stat label="Historical T1 hit rate" value={pct(s.t1_hit_rate)} sub={s.t1_ci95 ? `CI ${pct(s.t1_ci95[0])}–${pct(s.t1_ci95[1])}` : undefined} />
            <Stat label="Historical T2 hit rate" value={pct(s.t2_hit_rate)} />
            <Stat label="Stop-first rate" value={pct(s.stop_rate)} />
            <Stat label="Profit factor" value={num(s.profit_factor)} />
            <Stat label="CAGR" value={signedPct(s.cagr_pct)} valueClass={moveClass(s.cagr_pct)} sub={`Total ${signedPct(s.total_return_pct, 1)}`} />
            <Stat label="Max drawdown" value={pct(s.max_drawdown_pct, 2)} valueClass="text-down" />
            <Stat label="Sharpe" value={num(s.sharpe)} />
            <Stat label="Sortino" value={num(s.sortino)} />
            <Stat label="Expectancy" value={`${num(s.expectancy_r, 3)} R`} valueClass={moveClass(s.expectancy_r)} sub={`${signedPct(s.expectancy_pct, 2)} / trade`} />
            <Stat label="Avg hold" value={`${num(s.avg_holding_bars, 1)} bars`} />
            <Stat label="Best trade" value={signedPct(s.best_trade?.net_return_pct)} valueClass="text-up" sub={s.best_trade ? `${s.best_trade.symbol} ${s.best_trade.signal_date}` : undefined} />
            <Stat label="Worst trade" value={signedPct(s.worst_trade?.net_return_pct)} valueClass="text-down" sub={s.worst_trade ? `${s.worst_trade.symbol} ${s.worst_trade.signal_date}` : undefined} />
            <Stat label="Avg win / loss" value={<><span className="text-up">{signedPct(s.avg_win_pct)}</span> / <span className="text-down">{signedPct(s.avg_loss_pct)}</span></>} />
            <Stat label="Risk per trade" value={pct(r.risk_per_trade_pct, 2)} sub={`Partial at T1 ${pct(100 * r.partial_at_t1, 0)}`} />
          </div>
        )}
      </Card>

      <Card title="Equity curve (fixed-fractional, indexed to 100)">
        <EquityChart points={s.equity_curve ?? []} />
        <p className="mt-2 text-[11px] text-muted">Each trade changes equity by R-multiple × risk %; overlapping trades are not capital-constrained (approximation).</p>
      </Card>

      <Card title="Monthly & yearly returns (per-trade R model)">
        <ReturnsHeatmap monthly={s.monthly_returns ?? {}} yearly={s.yearly_returns ?? {}} />
      </Card>

      {r.yearly_stability && r.yearly_stability.years.length > 0 && <YearlyStability y={r.yearly_stability} />}

      <Card title="Train / validation / out-of-sample">
        <HitRatesTable rows={segs} label="Segments" />
        <p className="mt-2 text-[11px] text-muted">Out-of-sample results are the most relevant; a large drop from training suggests overfitting.</p>
      </Card>

      {r.walk_forward && (
        <Card title="Walk-forward folds">
          <TableWrap label="Walk-forward folds">
            <table className="tbl min-w-[720px] text-xs">
              <thead>
                <tr><th scope="col">Train</th><th scope="col">Test</th><th scope="col">Chosen params</th><th scope="col" className="text-right">IS trades</th><th scope="col" className="text-right">IS exp (R)</th><th scope="col" className="text-right">OOS trades</th><th scope="col" className="text-right">OOS T1</th><th scope="col" className="text-right">OOS exp (R)</th></tr>
              </thead>
              <tbody>
                {r.walk_forward.folds.map((f) => (
                  <tr key={f.test[0]}>
                    <td className="num whitespace-nowrap">{period(f.train)}</td><td className="num whitespace-nowrap">{period(f.test)}</td>
                    <td className="font-mono">{Object.entries(f.params).map(([k, v]) => `${k}=${v}`).join(", ")}</td>
                    <td className="num text-right">{f.in_sample.sample_size}</td><td className={cx("num text-right", moveClass(f.in_sample.expectancy_r))}>{num(f.in_sample.expectancy_r, 3)}</td>
                    <td className="num text-right">{f.out_of_sample.sample_size}</td><td className="num text-right">{pct(f.out_of_sample.t1_hit_rate)}</td>
                    <td className={cx("num text-right", moveClass(f.out_of_sample.expectancy_r))}>{num(f.out_of_sample.expectancy_r, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <h3 className="mb-1 mt-3 text-xs font-semibold uppercase text-muted">Combined out-of-sample</h3>
          <HitRatesTable rows={[["out_of_sample_combined", r.walk_forward.out_of_sample_combined]]} label="Combined OOS" />
          <p className="mt-1 text-[11px] text-muted">Grid: {Object.entries(r.walk_forward.grid).map(([k, v]) => `${k} ∈ {${v.join(", ")}}`).join("; ")}</p>
        </Card>
      )}

      {r.sensitivity && (
        <Card title="Parameter sensitivity" right={<span className="text-xs text-muted">Expectancy σ {num(r.sensitivity.expectancy_std, 3)} · positive in {pct(r.sensitivity.positive_share, 0)} of combos</span>}>
          <TableWrap label="Parameter sensitivity">
            <table className="tbl min-w-[480px] text-xs">
              <thead><tr><th scope="col">Parameters</th><th scope="col" className="text-right">Trades</th><th scope="col" className="text-right">T1 rate</th><th scope="col" className="text-right">Expectancy (R)</th></tr></thead>
              <tbody>
                {r.sensitivity.results.map((x) => (
                  <tr key={JSON.stringify(x.params)}>
                    <td className="font-mono">{Object.entries(x.params).map(([k, v]) => `${k}=${v}`).join(", ")}</td>
                    <td className="num text-right">{x.trades}</td><td className="num text-right">{pct(x.t1_hit_rate)}</td>
                    <td className={cx("num text-right", moveClass(x.expectancy_r))}>{num(x.expectancy_r, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <p className="mt-2 text-[11px] text-muted">Robust strategies stay positive across nearby parameters; a single good combination is a red flag.</p>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Monte Carlo (trade-order bootstrap)">
          {!mc.runs ? <EmptyState title={mc.note ?? "Not available"} /> : (
            <>
              <TableWrap label="Monte Carlo percentiles">
                <table className="tbl text-sm">
                  <thead><tr><th scope="col">Metric</th><th scope="col" className="text-right">P5</th><th scope="col" className="text-right">P50</th><th scope="col" className="text-right">P95</th></tr></thead>
                  <tbody>
                    <tr><th scope="row" className="text-left font-medium">Total return</th>{(["p5", "p50", "p95"] as const).map((p) => <td key={p} className={cx("num text-right", moveClass(mc.total_return_pct?.[p]))}>{signedPct(mc.total_return_pct?.[p], 1)}</td>)}</tr>
                    <tr><th scope="row" className="text-left font-medium">Max drawdown</th>{(["p5", "p50", "p95"] as const).map((p) => <td key={p} className="num text-right text-down">{pct(mc.max_drawdown_pct?.[p], 1)}</td>)}</tr>
                  </tbody>
                </table>
              </TableWrap>
              <p className="mt-2 text-xs text-muted">{mc.runs} resamples · share of paths ending in a loss: <span className="num text-ink">{pct(mc.prob_loss_pct)}</span></p>
            </>
          )}
        </Card>
        <Card title="Methodology">
          <dl className="space-y-1 text-xs">
            {Object.entries(r.methodology).map(([k, v]) => (
              <div key={k}><dt className="inline font-semibold text-ink">{humanize(k)}: </dt><dd className="inline text-muted">{v}</dd></div>
            ))}
          </dl>
        </Card>
      </div>

      <Card title="By market regime">
        <HitRatesTable rows={Object.entries(r.by_regime ?? {})} label="By regime" firstCol="Regime" />
      </Card>

      {r.portfolio ? <PortfolioSection p={r.portfolio} market={market} /> : <EmptyState title="No portfolio simulation in this backtest">Older backtests ran without portfolio settings. Re-run it to see capital-constrained results.</EmptyState>}

      <Card title="Signal-level trades (every historical signal)" right={<CsvButton id={id} />}>
        <TradesTable id={id} />
      </Card>
    </div>
  );
}
