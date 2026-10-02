"use client";

import { COMPONENT_LABELS, CONTEXT_COMPONENTS, RETIRED_COMPONENTS } from "@/lib/constants";
import { useEngineWeights } from "@/lib/weights";
import { dateTime, humanize, num, pct, period, price, px, rr, signedPct, moveClass } from "@/lib/format";
import type { Check, Components, Explanation, HistoricalExample, MTF, Probability, SetupDetail } from "@/lib/types";
import { Bar, Card, EmptyState, Pill, Stat, TableWrap, cx } from "./ui";

export function LevelsPanel({ s }: { s: SetupDetail }) {
  const cur = s.currency;
  const o = { ref: s.current_price };
  const rows: { k: string; v: string; method: string; cls?: string; extra?: string }[] = [
    { k: "Entry zone", v: `${price(s.entry_zone[0], cur, o)} – ${price(s.entry_zone[1], cur, o)}`, method: s.entry_method },
    { k: "Stop", v: price(s.stop, cur, o), method: s.stop_method, cls: "text-down", extra: `Risk ${pct(s.risk_pct, 2)} · ATR ${px(s.atr, o)}` },
    ...s.targets.map((t, i) => ({
      k: `Target ${i + 1}`,
      v: price(t, cur, o),
      method: s.target_methods[i] ?? "",
      cls: "text-up",
      extra: i === 0 ? `R:R ${rr(s.rr_t1)} · ${signedPct(s.reward_pct_t1)}` : i === 1 ? `R:R ${rr(s.rr_t2)} · ${signedPct(s.reward_pct_t2)}` : undefined,
    })),
  ];
  return (
    <Card title="Levels & methodology">
      <ul className="divide-y divide-edge">
        {rows.map((r) => (
          <li key={r.k} className="grid grid-cols-1 gap-1 py-2 sm:grid-cols-[9rem_11rem_1fr] sm:gap-3">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted">{r.k}</span>
            <span className={cx("num text-sm font-semibold", r.cls)}>
              {r.v}
              {r.extra && <span className="block text-[11px] font-normal text-muted">{r.extra}</span>}
            </span>
            <span className="text-xs text-muted">{r.method}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-muted">Current price {price(s.current_price, cur, o)} · Timeframe {s.timeframe} · Max hold {s.strategy.max_hold_bars} bars.</p>
    </Card>
  );
}

export function ScoreBreakdown({ components, score, label, notes }: { components: Components; score: number; label: string; notes?: string[] }) {
  const weights = useEngineWeights();
  const keys = Object.keys(COMPONENT_LABELS).filter((k) => k in components);
  const extra = Object.keys(components).filter((k) => !(k in COMPONENT_LABELS) && !RETIRED_COMPONENTS.includes(k));
  return (
    <Card title="Score breakdown" right={<span className="num text-sm font-semibold">{num(score, 1)} · {label}</span>}>
      <ul className="space-y-2">
        {[...keys, ...extra].map((k) => {
          const v = components[k];
          return (
            <li key={k} className="grid grid-cols-[8.5rem_1fr_3rem] items-center gap-2 text-xs sm:grid-cols-[10rem_1fr_3rem]">
              <span className="min-w-0 text-muted">
                <span className="block truncate">{COMPONENT_LABELS[k] ?? humanize(k)}</span>
                {weights && weights[k] != null && <span className="block text-[10px]">{weights[k] === 0 ? "weight 0 (not in score)" : `weight ${weights[k]}`}</span>}
                {!weights && CONTEXT_COMPONENTS.includes(k) && <span className="block text-[10px]">shown for context</span>}
              </span>
              {v == null ? <span className="text-[11px] italic text-muted">unavailable</span> : <Bar value={v} tone={CONTEXT_COMPONENTS.includes(k) ? "blue" : undefined} />}
              <span className="num text-right">{v == null ? "—" : num(v, 0)}</span>
            </li>
          );
        })}
      </ul>
      {keys.some((k) => CONTEXT_COMPONENTS.includes(k)) && (
        <p className="mt-2 text-[11px] text-muted">“Historical evidence” summarises the empirical hit-rate record. It is shown for context and counts toward the score only if an administrator assigns it weight.</p>
      )}
      {notes && notes.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11px] text-muted">
          {notes.map((n) => <li key={n}>ⓘ {n}</li>)}
        </ul>
      )}
    </Card>
  );
}

export function WhyPanel({ e }: { e: Explanation }) {
  return (
    <Card title="Why this setup?">
      {e.trigger && <p className="mb-2 text-sm">{e.trigger}</p>}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-xs font-semibold text-up">Agreeing evidence</h3>
          {e.agreeing.length ? (
            <ul className="space-y-1 text-sm">
              {e.agreeing.map((x) => (
                <li key={x} className="flex gap-2"><span className="text-up" aria-hidden>✓</span><span className="sr-only">Agrees:</span><span>{x}</span></li>
              ))}
            </ul>
          ) : <p className="text-xs text-muted">None listed.</p>}
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold text-down">Disagreeing indicators</h3>
          {e.disagreeing.length ? (
            <ul className="space-y-1 text-sm">
              {e.disagreeing.map((x) => (
                <li key={x} className="flex gap-2"><span className="text-down" aria-hidden>✗</span><span className="sr-only">Disagrees:</span><span>{x}</span></li>
              ))}
            </ul>
          ) : <p className="text-xs text-muted">No indicator disagreed with the direction.</p>}
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold text-amber-300">Risk factors</h3>
          {e.risk_factors.length ? (
            <ul className="space-y-1 text-sm text-amber-100/90">
              {e.risk_factors.map((x) => (
                <li key={x} className="flex gap-2"><span aria-hidden>⚠</span><span>{x}</span></li>
              ))}
            </ul>
          ) : <p className="text-xs text-muted">None flagged.</p>}
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold text-ink">Invalidation — the setup is wrong if…</h3>
          <ul className="space-y-1 text-sm">
            {e.invalidation.map((x) => <li key={x}>• {x}</li>)}
          </ul>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted">
        {e.patterns.length > 0 && (
          <span className="flex flex-wrap items-center gap-1">
            Patterns: {e.patterns.map((p) => <Pill key={p}>{p}</Pill>)}
          </span>
        )}
        <span>Support: <span className="num text-ink">{e.support.length ? e.support.map((x) => px(x)).join(", ") : "—"}</span></span>
        <span>Resistance: <span className="num text-ink">{e.resistance.length ? e.resistance.map((x) => px(x)).join(", ") : "—"}</span></span>
      </div>
      {e.conditions_met && e.conditions_met.length > 0 && (
        <p className="mt-2 text-[11px] text-muted">Strategy conditions met: {e.conditions_met.join(" · ")}</p>
      )}
    </Card>
  );
}

export function ChecksPanel({ checks }: { checks: Check[] }) {
  const blocking = checks.filter((c) => !c.passed && c.severity === "block").length;
  const warns = checks.filter((c) => !c.passed && c.severity === "warn").length;
  return (
    <Card
      title="Validation checklist"
      right={
        <span className="text-xs">
          <span className={blocking ? "text-down" : "text-up"}>{blocking} blocking</span> · <span className="text-amber-300">{warns} warnings</span> · {checks.length} checks
        </span>
      }
    >
      <ul className="divide-y divide-edge">
        {checks.map((c) => (
          <li key={c.name} className="flex items-start gap-2 py-1.5 text-sm">
            <span aria-hidden className={cx("mt-0.5 w-4 shrink-0 text-center", c.passed ? "text-up" : c.severity === "block" ? "text-down" : "text-amber-300")}>
              {c.passed ? "✓" : c.severity === "block" ? "✗" : "!"}
            </span>
            <span className="min-w-0 flex-1">
              <span className="font-medium">{c.name}</span>{" "}
              <span className="sr-only">{c.passed ? "passed" : "failed"}</span>
              <span className={cx("badge ml-1", c.severity === "block" ? "bg-slate-800 text-slate-300" : "bg-amber-950 text-amber-300")}>{c.severity === "block" ? "BLOCK" : "WARN"}</span>
              <span className="block text-xs text-muted">{c.detail}</span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function RateRow({ label, rate, ci, hits, def }: { label: string; rate?: number | null; ci?: [number, number] | null; hits?: number; def?: string }) {
  return (
    <li className="py-1.5">
      <div className="grid grid-cols-[1fr_auto] items-center gap-2 text-sm">
        <span>{label}</span>
        <span className="num font-semibold">{pct(rate)}</span>
      </div>
      <Bar value={rate ?? 0} tone={/stop/i.test(label) ? "red" : /neither/i.test(label) ? "amber" : "green"} />
      <div className="mt-0.5 text-[11px] text-muted">
        {hits != null && <span className="num">{hits} trades</span>}
        {ci && <span className="num"> · 95% CI {pct(ci[0])}–{pct(ci[1])}</span>}
        {def && <span> · {def}</span>}
      </div>
    </li>
  );
}

export function ProbabilityPanel({ p, strategyRules }: { p: Probability; strategyRules?: { entry: string; exit: string } }) {
  const d = p.definitions ?? {};
  if (!p.available || !p.sample_size) {
    return (
      <Card title="Historical probability">
        <EmptyState title="Insufficient history">{p.reason ?? "No comparable historical trades were found for this strategy and context."}</EmptyState>
      </Card>
    );
  }
  return (
    <Card title="Historical probability (backtest)" right={p.sufficient === false ? <Pill tone="amber">Small sample (min {p.min_sample})</Pill> : <Pill tone="blue">n = {p.sample_size}</Pill>}>
      <p className="mb-2 text-xs text-muted">
        Based on <span className="num text-ink">{p.sample_size}</span> comparable historical trades · {period(p.backtest_period ?? null)} · conditioning: {p.conditioning ?? "—"}
      </p>
      <ul className="divide-y divide-edge">
        <RateRow label="Historical T1 hit rate" rate={p.t1_hit_rate} ci={p.t1_ci95} hits={p.t1_hits} def={d.t1_hit_rate} />
        <RateRow label="Historical T2 hit rate" rate={p.t2_hit_rate} ci={p.t2_ci95} hits={p.t2_hits} def={d.t2_hit_rate} />
        <RateRow label="Historical stop-first rate" rate={p.stop_rate} ci={p.stop_ci95} hits={p.stop_hits} def={d.stop_rate} />
        <RateRow label="Neither (timed out)" rate={p.neither_rate} hits={p.neither} def={d.neither_rate} />
      </ul>
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Expectancy" value={p.expectancy_r != null ? `${num(p.expectancy_r, 2)} R` : "—"} valueClass={moveClass(p.expectancy_r)} />
        <Stat label="Avg return" value={signedPct(p.avg_return_pct)} valueClass={moveClass(p.avg_return_pct)} />
        <Stat label="Avg win / loss" value={<><span className="text-up">{signedPct(p.avg_win_pct)}</span> / <span className="text-down">{signedPct(p.avg_loss_pct)}</span></>} />
        <Stat label="Avg hold" value={p.avg_holding_bars != null ? `${num(p.avg_holding_bars, 1)} bars` : "—"} />
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        <div><dt className="inline text-muted">Market: </dt><dd className="inline">{p.market ?? "—"}</dd></div>
        <div><dt className="inline text-muted">Timeframe: </dt><dd className="inline">{p.timeframe ?? "—"}</dd></div>
        <div><dt className="inline text-muted">Regime family: </dt><dd className="inline">{p.regime_family ?? "any"}</dd></div>
        <div><dt className="inline text-muted">Score bucket: </dt><dd className="inline">{p.score_bucket ?? "—"}</dd></div>
        <div><dt className="inline text-muted">Symbols covered: </dt><dd className="inline num">{p.symbols_covered ?? "—"}</dd></div>
        {d.same_bar_rule && <div className="sm:col-span-2"><dt className="inline text-muted">Same-bar rule: </dt><dd className="inline">{d.same_bar_rule}</dd></div>}
        {strategyRules && (
          <>
            <div className="sm:col-span-2"><dt className="inline text-muted">Entry rule: </dt><dd className="inline">{strategyRules.entry}</dd></div>
            <div className="sm:col-span-2"><dt className="inline text-muted">Exit rules: </dt><dd className="inline">{strategyRules.exit}</dd></div>
          </>
        )}
      </dl>
      <p className="mt-2 text-[11px] text-muted">These are historical frequencies from backtests after costs, not predictions for this trade.</p>
    </Card>
  );
}

export function ExamplesTable({ rows, currency }: { rows: HistoricalExample[]; currency?: string }) {
  return (
    <Card title="Historical similar examples">
      {rows.length === 0 ? (
        <EmptyState title="No comparable past signals on record" />
      ) : (
        <TableWrap label="Historical examples">
          <table className="tbl min-w-[720px]">
            <thead>
              <tr>
                <th scope="col">Signal</th><th scope="col">Symbol</th><th scope="col" className="text-right">Entry</th><th scope="col" className="text-right">Stop</th>
                <th scope="col" className="text-right">T1</th><th scope="col" className="text-right">T2</th><th scope="col">Exit</th><th scope="col">Outcome</th>
                <th scope="col" className="text-right">Net</th><th scope="col" className="text-right">Bars</th><th scope="col">Regime</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.symbol}-${r.signal_date}`}>
                  <td className="num whitespace-nowrap">{r.signal_date}</td>
                  <td className="font-mono">{r.symbol}</td>
                  <td className="num text-right">{price(r.entry, currency, { ref: r.entry })}</td>
                  <td className="num text-right text-down">{px(r.stop, { ref: r.entry })}</td>
                  <td className={cx("num text-right", r.t1_hit && "text-up")}>{px(r.t1, { ref: r.entry })}{r.t1_hit && " ✓"}</td>
                  <td className={cx("num text-right", r.t2_hit && "text-up")}>{px(r.t2, { ref: r.entry })}{r.t2_hit && " ✓"}</td>
                  <td className="num whitespace-nowrap">{r.exit_date}</td>
                  <td><Pill tone={r.exit_reason === "stop" ? "red" : r.t1_hit ? "green" : "slate"}>{r.exit_reason}</Pill></td>
                  <td className={cx("num text-right", moveClass(r.net_return_pct))}>{signedPct(r.net_return_pct)}</td>
                  <td className="num text-right">{r.bars_held}</td>
                  <td className="text-xs text-muted">{r.regime ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

export function MtfPanel({ m, title = "Multi-timeframe alignment" }: { m: MTF; title?: string }) {
  const tone = m.alignment === "STRONG" ? "green" : m.alignment === "CONFLICT" ? "red" : m.alignment === "WEAK" ? "amber" : "blue";
  return (
    <Card title={title} right={<Pill tone={tone}>{m.alignment}</Pill>}>
      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {(m.priority ?? Object.keys(m.timeframes)).map((tf) => {
          const v = m.timeframes[tf];
          return (
            <li key={tf} className="rounded border border-edge bg-panel2/60 px-2 py-1.5">
              <div className="text-[11px] text-muted">{tf}</div>
              <div className={cx("text-sm font-semibold", v === "Bullish" ? "text-up" : v === "Bearish" ? "text-down" : "text-muted")}>{v ?? "n/a"}</div>
            </li>
          );
        })}
      </ul>
      {m.note && <p className="mt-2 text-[11px] text-muted">{m.note}</p>}
    </Card>
  );
}

export function GeneratedStamp({ s }: { s: SetupDetail }) {
  return (
    <span className="text-[11px] text-muted">
      Generated {dateTime(s.generated_at)} · engine v{s.engine_version} · mode {s.analysis_mode}
    </span>
  );
}
