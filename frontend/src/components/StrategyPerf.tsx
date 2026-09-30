"use client";

import Link from "next/link";
import { HitRatesTable, Warnings } from "./BacktestResults";
import { Collapsible, DirectionBadge, EmptyState, Pill, Stat } from "./ui";
import { moveClass, num, pct, period } from "@/lib/format";
import type { StrategyPerformance, StrategyPublic } from "@/lib/types";

export function StrategyPerf({ s, custom }: { s: StrategyPublic & { performance: StrategyPerformance | null }; custom?: { key: string; version: number; inScan: boolean } }) {
  const p = s.performance;
  return (
    <Collapsible
      title={<span className="flex flex-wrap items-center gap-2">{s.name} <DirectionBadge d={s.direction} />{custom && <Pill tone="blue">custom v{custom.version}</Pill>}{custom?.inScan && <Pill tone="green">in scan</Pill>}</span>}
      right={p ? <span className="hidden text-xs text-muted sm:inline">n={p.trades} · T1 {pct(p.t1_hit_rate)} · exp {num(p.expectancy_r, 3)}R</span> : <span className="text-xs text-muted">{custom ? "not yet in a scan" : "no history"}</span>}
    >
      <p className="text-sm">{s.description}</p>
      <ul className="mt-1 text-xs text-muted">{s.conditions.map((c) => <li key={c}>• {c}</li>)}</ul>
      {s.notes && <p className="mt-2 text-xs text-amber-200">ⓘ {s.notes}</p>}
      {custom && <p className="mt-2 text-xs"><Link className="link" href={`/strategies?key=${encodeURIComponent(custom.key)}`}>Manage versions & scan inclusion →</Link> · <Link className="link" href={`/backtest?strategy=${encodeURIComponent(custom.key)}&version=${custom.version}`}>Backtest v{custom.version} →</Link></p>}
      {!p ? (
        <div className="mt-3"><EmptyState title={custom ? "Not yet included in a scan" : "Performance not computed yet — an admin must run a scan"}>{custom ? "Scan performance appears after the strategy is included in the daily scan and a scan has run. Use a backtest meanwhile." : null}</EmptyState></div>
      ) : (
        <div className="mt-3 space-y-3">
          <Warnings items={p.warnings} />
          <p className="text-xs text-muted">Backtest period {period(p.backtest_period)} · costs {p.costs ? `${p.costs.commission_pct_per_side}% commission + ${p.costs.slippage_pct_per_side}% slippage per side` : "—"}</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
            <Stat label="Trades" value={p.trades ?? "—"} />
            <Stat label="Win rate" value={pct(p.win_rate)} />
            <Stat label="Hist. T1 rate" value={pct(p.t1_hit_rate)} sub={`n=${p.trades ?? 0}`} />
            <Stat label="Hist. T2 rate" value={pct(p.t2_hit_rate)} />
            <Stat label="Stop rate" value={pct(p.stop_rate)} />
            <Stat label="Profit factor" value={num(p.profit_factor)} />
            <Stat label="Max DD" value={pct(p.max_drawdown_pct, 2)} valueClass="text-down" />
            <Stat label="Expectancy" value={`${num(p.expectancy_r, 3)} R`} valueClass={moveClass(p.expectancy_r)} />
          </div>
          {p.segments && <HitRatesTable rows={Object.entries(p.segments)} label={`${s.name} segments`} />}
          {p.by_regime && <HitRatesTable rows={Object.entries(p.by_regime)} label={`${s.name} by regime`} firstCol="Regime" />}
        </div>
      )}
    </Collapsible>
  );
}

