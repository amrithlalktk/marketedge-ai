"use client";

import { useState } from "react";
import { CURRENCY_SYMBOL, humanize, inr, integer, moveClass, num, pct, period, px, signedPct } from "@/lib/format";
import type { BacktestResult, CompareItem, PortfolioResult } from "@/lib/types";
import { ReturnsHeatmap } from "./BacktestResults";
import { TimeChart } from "./TimeChart";
import { Card, DirectionBadge, EmptyState, Pill, Stat, TableWrap, cx } from "./ui";

const SKIP_TEXT: Record<string, string> = {
  max_positions: "all position slots were already in use",
  insufficient_capital_or_sector_cap: "not enough free capital, or the sector cap was reached",
  already_in_symbol: "a position in that symbol was already open",
};

const MARKET_CCY: Record<string, string | null> = { NSE: "INR", CRYPTO: "USD", US: "USD", EUROPE: null, ASIA: null, FX: null };

export function PortfolioSection({ p, market = "NSE" }: { p: PortfolioResult; market?: string }) {
  // the backend converts multi-currency portfolios into one account currency at historical FX closes
  const ccy = p.currency && p.currency !== "MIXED" ? p.currency : (p.currency === "MIXED" ? null : MARKET_CCY[market] ?? null);
  const money = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v)) return "—";
    if (ccy === "INR") return inr(v, 0);
    if (ccy == null) return integer(v);
    return `${v < 0 ? "-" : ""}${CURRENCY_SYMBOL[ccy] ?? ""}${new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Math.abs(v))}`;
  };
  const [page, setPage] = useState(1);
  const size = 25;
  const skippedTotal = Object.values(p.skipped).reduce((a, b) => a + b, 0);
  const log = [...p.trade_log].reverse();
  const pages = Math.max(1, Math.ceil(log.length / size));
  const c = p.config;
  return (
    <section aria-labelledby="pf-h" className="space-y-4 rounded-lg border-2 border-blue-900/70 p-3 sm:p-4">
      <header>
        <h2 id="pf-h" className="text-base font-semibold">Portfolio simulation <span className="text-sm font-normal text-muted">— {ccy ?? "local currency units"}, capital-constrained</span></h2>
        {ccy == null && <p role="note" className="mt-1 rounded border border-amber-800 bg-amber-950/30 px-2 py-1 text-xs text-amber-200">Some instrument currencies could not be converted ({p.fx_conversion?.missing?.join(", ") || "no FX data"}), so amounts are shown as plain units. Compare percentages (CAGR, drawdown, return), not absolute amounts.</p>}
        {p.fx_conversion?.applied && <p className="mt-1 text-xs text-muted">{p.fx_conversion.note} Pairs used: {Object.entries(p.fx_conversion.pairs).map(([c, path]) => `${c}→${p.fx_conversion?.account_currency} via ${path.join(" × ")}`).join("; ")}.</p>}
        <p className="mt-1 text-xs text-muted">
          One account starting at <span className="num text-ink">{money(c.initial_capital)}</span>, max {c.max_positions} positions, ≤ {c.max_position_pct}% of equity per position
          {c.max_sector_pct ? `, ≤ ${c.max_sector_pct}% per sector` : ", no sector cap"}, risking {c.risk_per_trade_pct}% per trade, {c.commission_pct}% commission + {c.slippage_pct}% slippage. {period(p.period)}.
        </p>
      </header>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
        <Stat label="Final equity" value={money(p.final_equity)} valueClass={moveClass(p.final_equity - c.initial_capital)} />
        <Stat label="Total return" value={signedPct(p.total_return_pct)} valueClass={moveClass(p.total_return_pct)} sub={p.benchmark?.total_return_pct != null ? `benchmark ${signedPct(p.benchmark.total_return_pct)}` : undefined} />
        <Stat label="CAGR" value={signedPct(p.cagr_pct)} valueClass={moveClass(p.cagr_pct)} />
        <Stat label="Max drawdown" value={pct(p.max_drawdown_pct, 2)} valueClass="text-down" />
        <Stat label="Sharpe / Sortino" value={`${num(p.sharpe)} / ${num(p.sortino)}`} />
        <Stat label="Trades taken" value={`${integer(p.trades_taken)} / ${integer(p.trades_available)}`} sub={`${integer(skippedTotal)} skipped`} />
        <Stat label="Win rate" value={pct(p.win_rate)} />
        <Stat label="Profit factor" value={num(p.profit_factor)} />
        <Stat label="Expectancy / trade" value={money(p.expectancy_per_trade)} valueClass={moveClass(p.expectancy_per_trade)} />
        <Stat label="Avg win / loss" value={<><span className="text-up">{money(p.avg_win)}</span> / <span className="text-down">{money(p.avg_loss)}</span></>} />
        <Stat label="Total costs" value={money(p.total_costs)} sub={`turnover ${money(p.turnover)}`} />
        <Stat label="Exposure" value={pct(p.avg_exposure_pct)} sub={`max ${p.max_concurrent_positions} positions · ${p.open_at_end} open at end`} />
      </div>

      <Card title="Equity (mark-to-market) vs benchmark">
        <TimeChart label="Portfolio equity versus benchmark, in rupees" series={[
          { name: "Portfolio equity", color: "#22c55e", type: "area", data: p.equity_curve },
          ...(p.benchmark?.curve?.length ? [{ name: "Benchmark (buy & hold, same capital)", color: "#60a5fa", dashed: true, data: p.benchmark.curve }] : []),
        ]} />
      </Card>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Drawdown">
          <TimeChart percent height={200} label="Portfolio drawdown in percent" series={[{ name: "Drawdown %", color: "#f05252", type: "area", data: p.drawdown_curve }]} />
        </Card>
        <Card title="Exposure & open positions">
          <TimeChart percent height={140} label="Capital exposure as percent of equity" series={[
            { name: "Exposure % of equity", color: "#f5a524", type: "area", data: p.exposure_curve.map((x) => [x[0], x[1]]) },
          ]} />
          <div className="mt-2">
            <TimeChart height={100} label="Number of open positions" series={[
              { name: `Open positions (max ${c.max_positions})`, color: "#a855f7", type: "histogram", data: p.exposure_curve.map((x) => [x[0], x[2]]) },
            ]} />
          </div>
        </Card>
      </div>

      <Card title="Why portfolio and strategy results differ">
        {skippedTotal === 0 ? <p className="text-sm text-muted">No signals were skipped.</p> : (
          <ul className="space-y-1 text-sm">
            {Object.entries(p.skipped).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
              <li key={k} className="flex flex-wrap justify-between gap-2">
                <span>{humanize(k)} <span className="text-xs text-muted">— {SKIP_TEXT[k] ?? "signal could not be taken"}</span></span>
                <span className="num">{integer(v)} ({pct((100 * v) / Math.max(1, p.trades_available), 1)})</span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-muted">
          The strategy-level statistics count <strong className="text-ink">every</strong> historical signal as an independent 1R bet. A real account cannot take them all: signals cluster, slots and
          capital run out, sizing compounds from current equity and every fill pays costs. So portfolio CAGR/drawdown reflect only the {integer(p.trades_taken)} trades that fit, in the order they arrived.
        </p>
        <p className="mt-1 text-[11px] text-muted">Method: {p.method}</p>
      </Card>

      <Card title="Portfolio monthly & yearly returns">
        <ReturnsHeatmap monthly={p.monthly_returns} yearly={p.yearly_returns} />
      </Card>

      <Card title={`Portfolio trade log (${p.trade_log_truncated ? `${integer(log.length)} most recent of ${integer(p.trade_log_total ?? p.trades_taken)}` : integer(log.length)})`}>
        {log.length === 0 ? <EmptyState title="No trades taken" /> : (
          <>
            <TableWrap label="Portfolio trade log">
              <table className="tbl min-w-[760px] text-xs">
                <thead><tr><th scope="col">Symbol</th><th scope="col">Dir</th><th scope="col">Entry</th><th scope="col">Exit</th><th scope="col" className="text-right">Entry px</th><th scope="col" className="text-right">Exit px</th><th scope="col" className="text-right">Qty</th><th scope="col">Reason</th><th scope="col" className="text-right">P&amp;L</th><th scope="col" className="text-right">Costs</th><th scope="col" className="text-right">Return</th></tr></thead>
                <tbody>
                  {log.slice((page - 1) * size, page * size).map((t, i) => (
                    <tr key={`${t.symbol}-${t.entry_date}-${i}`}>
                      <td className="font-mono">{t.symbol}</td><td><DirectionBadge d={t.direction} /></td><td className="num whitespace-nowrap">{t.entry_date}</td><td className="num whitespace-nowrap">{t.exit_date}</td>
                      <td className="num text-right">{px(t.entry_px, { fx: market === "FX" })}</td><td className="num text-right">{px(t.exit_px, { fx: market === "FX" })}</td><td className="num text-right">{integer(t.qty)}</td>
                      <td><Pill tone={t.exit_reason === "stop" ? "red" : t.pnl > 0 ? "green" : "slate"}>{t.exit_reason}</Pill></td>
                      <td className={cx("num text-right", moveClass(t.pnl))}>{money(t.pnl)}</td><td className="num text-right text-muted">{money(t.costs)}</td>
                      <td className={cx("num text-right", moveClass(t.return_pct))}>{signedPct(t.return_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <nav aria-label="Trade log pagination" className="mt-2 flex items-center justify-center gap-2 text-sm">
              <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage((x) => x - 1)}>← Prev</button>
              <span className="num text-muted">Page {page} / {pages}</span>
              <button className="btn-ghost" disabled={page >= pages} onClick={() => setPage((x) => x + 1)}>Next →</button>
            </nav>
            {p.trade_log_truncated && <p className="mt-1 text-[11px] text-muted">Truncated: showing the most recent {integer(log.length)} of {integer(p.trade_log_total ?? p.trades_taken)} portfolio trades. The CSV export contains the full signal-level trade list.</p>}
          </>
        )}
      </Card>
    </section>
  );
}

export function YearlyStability({ y }: { y: NonNullable<BacktestResult["yearly_stability"]> }) {
  const share = y.years_evaluated ? y.positive_years / y.years_evaluated : 0;
  return (
    <Card title="Year-by-year stability (per-trade R)" right={<Pill tone={share >= 0.7 ? "green" : share >= 0.5 ? "amber" : "red"}>{y.positive_years}/{y.years_evaluated} positive years</Pill>}>
      <TableWrap label="Yearly stability">
        <table className="tbl min-w-[560px] text-xs">
          <thead><tr><th scope="col">Year</th><th scope="col" className="text-right">Trades</th><th scope="col" className="text-right">T1 rate</th><th scope="col" className="text-right">T1 95% CI</th><th scope="col" className="text-right">Stop rate</th><th scope="col" className="text-right">Expectancy (R)</th><th scope="col" className="text-right">Avg ret</th></tr></thead>
          <tbody>
            {y.years.map((r) => (
              <tr key={r.year}>
                <th scope="row" className="num text-left">{r.year}</th><td className="num text-right">{r.sample_size}</td><td className="num text-right">{pct(r.t1_hit_rate)}</td>
                <td className="num text-right text-muted">{r.t1_ci95 ? `${pct(r.t1_ci95[0])}–${pct(r.t1_ci95[1])}` : "—"}</td><td className="num text-right">{pct(r.stop_rate)}</td>
                <td className={cx("num text-right font-semibold", moveClass(r.expectancy_r))}>{num(r.expectancy_r, 3)}</td><td className={cx("num text-right", moveClass(r.avg_return_pct))}>{signedPct(r.avg_return_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      <p className="mt-2 text-[11px] text-muted">An edge concentrated in one or two years is fragile; look for consistency rather than a high total.</p>
    </Card>
  );
}

type Col = { k: keyof CompareItem; label: string; fmt: (v: number | null, it: CompareItem) => string; hl?: "oos" | "pf"; better?: "high" | "low" };
const n = (v: unknown) => (typeof v === "number" ? v : null);
const COLS: Col[] = [
  { k: "trades", label: "Trades", fmt: (v) => integer(v) },
  { k: "win_rate", label: "Win rate", fmt: (v) => pct(v), better: "high" },
  { k: "t1_hit_rate", label: "Hist. T1 rate", fmt: (v) => pct(v), better: "high" },
  { k: "stop_rate", label: "Stop rate", fmt: (v) => pct(v), better: "low" },
  { k: "profit_factor", label: "Profit factor", fmt: (v) => num(v), better: "high" },
  { k: "expectancy_r", label: "Expectancy (R, all)", fmt: (v) => num(v, 3), better: "high" },
  { k: "oos_expectancy_r", label: "OOS expectancy (R)", fmt: (v, it) => `${num(v, 3)}${it.oos_trades != null ? ` (n=${it.oos_trades})` : ""}`, hl: "oos", better: "high" },
  { k: "wf_oos_expectancy_r", label: "Walk-fwd OOS (R)", fmt: (v) => num(v, 3), hl: "oos", better: "high" },
  { k: "positive_years", label: "Positive years", fmt: (v, it) => (v == null ? "—" : `${v}/${it.years_evaluated}`), hl: "oos" },
  { k: "portfolio_cagr_pct", label: "Portfolio CAGR", fmt: (v) => signedPct(v), hl: "pf", better: "high" },
  { k: "portfolio_max_dd_pct", label: "Portfolio max DD", fmt: (v) => pct(v, 2), hl: "pf", better: "high" },
  { k: "portfolio_sharpe", label: "Portfolio Sharpe", fmt: (v) => num(v), hl: "pf", better: "high" },
  { k: "trades_skipped", label: "Signals skipped", fmt: (v) => integer(v), hl: "pf" },
];

export function CompareTable({ items, note }: { items: CompareItem[]; note?: string }) {
  const best = (c: Col) => {
    if (!c.better) return null;
    const vals = items.map((it) => n(it[c.k])).filter((v): v is number => v != null);
    if (vals.length < 2) return null;
    return c.better === "high" ? Math.max(...vals) : Math.min(...vals);
  };
  return (
    <div className="space-y-3">
      <TableWrap label="Backtest comparison">
        <table className="tbl min-w-[640px] text-xs">
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {items.map((it) => (
                <th key={it.id} scope="col" className="text-right normal-case">
                  <span className="block text-ink">#{it.id} {it.strategy ?? it.strategy_key}</span>
                  <span className="block font-normal text-muted">{it.strategy_key}{it.strategy_version != null ? ` · v${it.strategy_version}` : ""}</span>
                  <span className="block font-normal text-muted">{period(it.period)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {COLS.map((c) => {
              const b = best(c);
              return (
                <tr key={c.k} className={cx(c.hl === "oos" && "bg-blue-950/30", c.hl === "pf" && "bg-fuchsia-950/20")}>
                  <th scope="row" className="text-left font-medium">{c.label}{c.hl && <span className="ml-1 text-[10px] text-muted">{c.hl === "oos" ? "OOS" : "portfolio"}</span>}</th>
                  {items.map((it) => {
                    const v = n(it[c.k]);
                    return <td key={it.id} className={cx("num text-right", v != null && b != null && v === b && "font-bold text-up")}>{c.fmt(v, it)}</td>;
                  })}
                </tr>
              );
            })}
            <tr>
              <th scope="row" className="text-left font-medium">Costs</th>
              {items.map((it) => <td key={it.id} className="num text-right text-muted">{it.costs ? `${it.costs.commission_pct}% + ${it.costs.slippage_pct}%` : "—"}</td>)}
            </tr>
          </tbody>
        </table>
      </TableWrap>
      <p className="text-[11px] text-muted"><span className="rounded bg-blue-950/60 px-1">blue rows</span> = out-of-sample / stability · <span className="rounded bg-fuchsia-950/40 px-1">purple rows</span> = portfolio simulation · bold green = best in row. {note}</p>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {items.map((it) => it.warnings.length > 0 && (
          <div key={it.id} className="rounded border border-amber-800 bg-amber-950/30 p-2 text-xs">
            <p className="font-semibold text-amber-200">⚠ #{it.id} warnings</p>
            <ul className="mt-1 space-y-0.5 text-amber-100">{it.warnings.map((w) => <li key={w}>• {w}</li>)}</ul>
          </div>
        ))}
      </div>
    </div>
  );
}
