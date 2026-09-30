import { DISCLAIMER } from "@/lib/constants";
import { inr, integer, moveClass, num, pct, period } from "@/lib/format";
import type { OptionSetup, PopHistBucket, ProposedStrategy, StructureMetrics } from "@/lib/types";
import { ChecksPanel } from "../SetupSections";
import { Bar, Card, Collapsible, DataStamp, Pill, Stat, TableWrap, cx } from "../ui";
import { PayoffChart } from "./charts";

export const OPTIONS_DISCLAIMER_FALLBACK = `${DISCLAIMER} Options can lose their entire premium quickly; short options carry unlimited risk. Option levels are model estimates (Black-76, constant IV).`;

export function OptionsDisclaimer({ text }: { text?: string | null }) {
  return (
    <aside aria-label="Options disclaimer" className="mt-6 rounded-md border border-edge bg-panel/60 p-3 text-xs leading-relaxed text-muted">
      <strong className="text-ink">Disclaimer: </strong>
      {text || OPTIONS_DISCLAIMER_FALLBACK}
    </aside>
  );
}

export const premium = (v: number | null | undefined) => (v == null ? "—" : `₹${num(v)}`);

function Money({ v, unlimited, kind }: { v: number | null; unlimited: boolean; kind: "profit" | "loss" }) {
  if (unlimited) return <span className={kind === "loss" ? "font-bold text-down" : "font-bold text-up"}>Unlimited</span>;
  return <span className={kind === "loss" ? "text-down" : "text-up"}>{inr(v == null ? null : Math.abs(v), 0)}</span>;
}

function PopBucket({ label, b, horizon }: { label: string; b?: PopHistBucket; horizon: number }) {
  if (!b) return null;
  return (
    <div className="rounded border border-edge bg-panel2/40 p-2 text-xs">
      <div className="flex justify-between gap-2">
        <span className="text-muted">{label}</span>
        <span className="num font-semibold">{b.pop == null ? "n/a" : pct(b.pop)}</span>
      </div>
      <div className="mt-0.5 text-[11px] text-muted">
        n=<span className="num">{integer(b.sample_size)}</span> · {horizon}-session windows{b.period && <> · {period(b.period)}</>}
        {b.pop == null && " · sample too small"}
      </div>
      {b.avg_pnl != null && (
        <div className="mt-0.5 text-[11px]">
          Avg P&amp;L <span className={cx("num", moveClass(b.avg_pnl))}>{inr(b.avg_pnl, 0)}</span> · 5th pct <span className={cx("num", moveClass(b.p5_pnl))}>{inr(b.p5_pnl, 0)}</span>
        </div>
      )}
    </div>
  );
}

/** Metrics common to proposed strategies and the payoff builder. */
export function StructureView({ m, spot, lotSize, extra }: { m: StructureMetrics; spot: number; lotSize: number; extra?: { capital?: number | null; capitalNote?: string; rewardToRisk?: number | null } }) {
  const rr = extra?.rewardToRisk ?? null;
  const h = m.pop_historical;
  return (
    <div className="space-y-3">
      <TableWrap label="Strategy legs">
        <table className="tbl min-w-[520px] text-xs">
          <thead>
            <tr><th scope="col">Action</th><th scope="col">Contract</th><th scope="col" className="text-right">Lots</th><th scope="col" className="text-right">Premium</th><th scope="col" className="text-right">IV</th><th scope="col" className="text-right">Δ</th><th scope="col" className="text-right">Θ/day</th><th scope="col" className="text-right">Vega</th></tr>
          </thead>
          <tbody>
            {m.legs.map((l, i) => (
              <tr key={i}>
                <td><Pill tone={l.action === "BUY" ? "green" : "red"}>{l.action}</Pill></td>
                <td className="num whitespace-nowrap">{num(l.strike, 0)} {l.option_type} · {l.expiry}</td>
                <td className="num text-right">{l.lots}</td>
                <td className="num text-right">{premium(l.premium)}</td>
                <td className="num text-right">{pct(l.iv, 1)}</td>
                <td className="num text-right">{num(l.delta, 2)}</td>
                <td className="num text-right">{num(l.theta, 2)}</td>
                <td className="num text-right">{num(l.vega, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Max profit" value={<Money v={m.max_profit} unlimited={m.max_profit_unlimited} kind="profit" />} />
        <Stat label="Max loss" value={<Money v={m.max_loss} unlimited={m.max_loss_unlimited} kind="loss" />} />
        <Stat label={m.premium_type === "credit" ? "Net credit" : "Net debit"} value={inr(Math.abs(m.net_premium), 0)} sub={`lot size ${lotSize}`} valueClass={m.premium_type === "credit" ? "text-up" : "text-ink"} />
        <Stat label="Reward : risk" value={rr ? `${num(rr, 2)} : 1` : m.max_loss_unlimited ? "n/a (unlimited risk)" : "—"} />
        <Stat label="Breakevens" value={m.breakevens.length ? m.breakevens.map((b) => num(b, 0)).join(" / ") : "—"} className="col-span-2" />
        {extra && "capital" in extra && (
          <Stat label="Required capital" value={inr(extra.capital ?? null, 0)} sub={extra.capitalNote} className="col-span-2" valueClass={extra.capitalNote?.startsWith("INDICATIVE") ? "text-amber-300" : undefined} />
        )}
      </div>
      <Card title="Payoff at expiry">
        <PayoffChart curve={m.payoff_curve} spot={spot} breakevens={m.breakevens} />
        <p className="mt-1 text-[11px] text-muted">P&amp;L per position at expiry (₹), all legs held to expiry, premiums at the assumed fills (buy at ask, sell at bid). Use the range toggle to zoom around spot.</p>
      </Card>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Probability of profit</h4>
          <div className="rounded border border-edge bg-panel2/40 p-2 text-xs">
            <div className="flex justify-between"><span className="text-muted">Model POP (at expiry)</span><span className="num font-semibold">{m.pop_model == null ? "n/a" : pct(m.pop_model)}</span></div>
            {m.ev_model != null && <div className="mt-0.5 text-[11px]">Model expected value <span className={cx("num", moveClass(m.ev_model))}>{inr(m.ev_model, 0)}</span></div>}
            <p className="mt-0.5 text-[11px] text-muted">{m.pop_model_note}</p>
          </div>
          {h ? (
            <div className="mt-2 space-y-2">
              <PopBucket label="Historical POP — all history" b={h.all} horizon={h.horizon_sessions} />
              <PopBucket label="Historical POP — same regime" b={h.same_regime} horizon={h.horizon_sessions} />
              <p className="text-[11px] text-muted">Payoff applied to past NIFTY moves over {h.horizon_sessions} sessions. {h.note}</p>
            </div>
          ) : <p className="mt-2 text-[11px] text-muted">Historical POP unavailable (not enough index history).</p>}
        </div>
        <div>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Net Greeks (position)</h4>
          <dl className="grid grid-cols-2 gap-2 text-xs">
            {(["delta", "gamma", "theta", "vega"] as const).map((k) => (
              <div key={k} className="rounded border border-edge px-2 py-1">
                <dt className="text-muted">{k === "theta" ? "Theta (₹/day)" : k === "vega" ? "Vega (₹/IV pt)" : k === "delta" ? "Delta (index units)" : "Gamma"}</dt>
                <dd className={cx("num", k === "theta" && moveClass(m.net_greeks[k]))}>{num(m.net_greeks[k], k === "gamma" ? 4 : 2)}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </div>
  );
}

export function StrategyCard({ s, spot }: { s: ProposedStrategy; spot: number }) {
  const shortVol = s.category.includes("short") || s.max_loss_unlimited;
  return (
    <article className="card space-y-3" aria-labelledby={`st-${s.key}`}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id={`st-${s.key}`} className="text-base font-semibold">{s.name}</h3>
          <p className="text-xs text-muted">{s.category} · outlook {s.outlook} · expiry {s.expiry} ({s.dte}d)</p>
        </div>
        <div className="flex flex-wrap gap-1">
          <Pill tone="blue">{s.premium_type}</Pill>
          {s.max_loss_unlimited && <Pill tone="red">UNLIMITED RISK</Pill>}
        </div>
      </header>
      {s.risk_note && <p role="alert" className={cx("rounded border px-2 py-1.5 text-sm font-semibold", shortVol ? "border-red-800 bg-red-950/50 text-red-200" : "border-amber-800 bg-amber-950/40 text-amber-200")}>⚠ {s.risk_note}</p>}
      <StructureView m={s} spot={spot} lotSize={s.lot_size} extra={{ capital: s.required_capital, capitalNote: s.capital_note, rewardToRisk: s.reward_to_risk }} />
      <dl className="grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
        <div><dt className="inline text-muted">IV at entry: </dt><dd className="inline num">{pct(s.iv_at_entry.atm_iv_pct, 2)} ATM · percentile {s.iv_at_entry.iv_percentile == null ? "n/a" : num(s.iv_at_entry.iv_percentile, 0)}</dd></div>
        <div><dt className="inline text-muted">Recommended expiry: </dt><dd className="inline">{s.recommended_expiry}</dd></div>
        <div><dt className="inline text-muted">IV condition: </dt><dd className="inline">{s.iv_condition}</dd></div>
        <div className="sm:col-span-2"><dt className="inline text-muted">Management: </dt><dd className="inline">{s.management}</dd></div>
      </dl>
      <ul className="space-y-0.5 text-xs">
        {s.conditions.map((c) => <li key={c.condition}><span className={c.passed ? "text-up" : "text-down"} aria-hidden>{c.passed ? "✓" : "✗"}</span> <span className="sr-only">{c.passed ? "met" : "not met"}</span>{c.condition}</li>)}
      </ul>
    </article>
  );
}

function Lv({ k, v, cls, sub }: { k: string; v: React.ReactNode; cls?: string; sub?: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-muted">{k}</dt>
      <dd className={cx("num break-words text-sm font-medium", cls)}>{v}</dd>
      {sub && <dd className="text-[10px] text-muted">{sub}</dd>}
    </div>
  );
}

export function OptionSetupCard({ s, i }: { s: OptionSetup; i: number }) {
  const bull = s.direction === "BULLISH" || s.direction === "LONG";
  if (!s.contract) {
    return (
      <article className="card" aria-label={`Option setup ${i + 1}`}>
        <div className="flex flex-wrap items-center gap-2">
          <Pill tone={bull ? "green" : "red"}>{s.direction}</Pill>
          <Pill tone="amber">{s.status.replace("_", " ")}</Pill>
        </div>
        <p className="mt-2 text-sm">{s.reason ?? "No suitable contract."}</p>
        <DataStamp meta={s.data} asOf={s.as_of} className="mt-2" />
      </article>
    );
  }
  const c = s.contract;
  const p = s.probability;
  const blocks = (s.checks ?? []).filter((x) => !x.passed && x.severity === "block");
  return (
    <article className="card space-y-3" aria-labelledby={`os-${i}`}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 id={`os-${i}`} className="font-mono text-base font-semibold">{c.label}</h3>
            <Pill tone={bull ? "green" : "red"}>{s.direction}</Pill>
            <Pill tone={s.status === "VALID" ? "green" : "amber"}>{s.status.replace("_", " ")}</Pill>
          </div>
          <p className="text-xs text-muted">Buy {c.option_type} · {c.dte} days to expiry · lot {c.lot_size} · from underlying “{s.underlying?.strategy.name}”</p>
        </div>
        <div className="text-right">
          <div className="num text-lg font-bold leading-none text-blue-200">{num(s.score, 0)}</div>
          <div className="text-[10px] uppercase tracking-wide text-muted">combined score</div>
          <div className="mt-0.5 text-[10px] text-muted">underlying {num(s.underlying_score, 0)} · contract {num(s.contract_score, 0)}</div>
        </div>
      </header>
      {blocks.length > 0 && (
        <div role="alert" className="rounded border border-amber-800 bg-amber-950/40 p-2 text-xs">
          <p className="font-semibold text-amber-200">NO TRADE — blocked by:</p>
          <ul className="mt-1 text-amber-100/90">{blocks.map((b) => <li key={b.name}>✗ {b.name}: {b.detail}</li>)}</ul>
        </div>
      )}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 min-[420px]:grid-cols-4">
        <Lv k="Entry premium" v={premium(s.entry)} sub={`bid ${num(c.bid)} / ask ${num(c.ask)}`} />
        <Lv k="T1 premium" v={premium(s.targets?.[0])} cls="text-up" />
        <Lv k="T2 premium" v={premium(s.targets?.[1])} cls="text-up" />
        <Lv k="Stop premium" v={premium(s.stop)} cls="text-down" />
        <Lv k="Premium R:R (T2)" v={s.rr != null ? `1:${num(s.rr, 2)}` : "—"} />
        <Lv k="Premium / lot" v={inr(s.premium_per_lot, 0)} />
        <Lv k="Risk / lot" v={inr(s.risk_per_lot, 0)} cls="text-down" />
        <Lv k="Reward (T2) / lot" v={inr(s.reward_t2_per_lot, 0)} cls="text-up" />
        <Lv k="IV" v={pct(c.iv, 2)} sub={`spread ${pct(c.spread_pct, 2)}`} />
        <Lv k="Δ / Γ" v={`${num(c.delta, 3)} / ${num(c.gamma, 5)}`} />
        <Lv k="Θ / Vega" v={`${num(c.theta, 2)} / ${num(c.vega, 2)}`} />
        <Lv k="Theta burn (hold)" v={pct(s.theta_burn_pct, 1)} sub="of premium" cls={(s.theta_burn_pct ?? 0) > 8 ? "text-amber-300" : undefined} />
        <Lv k="Expected move (hold)" v={`±${num(s.expected_move_hold, 0)} pts`} sub="IV-implied 1σ" />
        <Lv k="Holding period" v={s.expected_holding ?? "—"} />
        <Lv k="OI / ΔOI" v={`${integer(c.oi)} / ${c.oi_change > 0 ? "+" : ""}${integer(c.oi_change)}`} />
        <Lv k="Underlying" v={`${num(s.underlying?.price)} → T1 ${num(s.underlying?.targets[0], 0)}`} sub={`stop ${num(s.underlying?.stop, 0)}`} />
      </dl>

      {p && (
        <section aria-label="Historical probability" className="rounded-md border border-blue-900 bg-blue-950/30 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-blue-200">Historical probability (underlying basis)</h4>
            <Pill tone="blue">n = {p.sample_size ?? 0}</Pill>
          </div>
          <p className="mt-1 text-sm font-medium text-blue-100">{p.basis_note}</p>
          <p className="mt-1 text-xs text-muted">
            <span className="num text-ink">{p.sample_size ?? 0}</span> historical underlying trades · backtest period <span className="num text-ink">{period(p.backtest_period ?? null)}</span> · conditioning {p.conditioning ?? "—"}
          </p>
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {([["Historical T1 hit rate", p.t1_hit_rate, p.t1_ci95, "green"], ["Historical T2 hit rate", p.t2_hit_rate, p.t2_ci95, "green"], ["Historical stop-first rate", p.stop_rate, null, "red"]] as const).map(([l, v, ci, tone]) => (
              <div key={l}>
                <div className="flex justify-between text-xs"><span className="text-muted">{l}</span><span className="num font-semibold">{p.sample_size ? pct(v) : "n/a"}</span></div>
                <Bar value={v ?? 0} tone={tone} />
                {ci && <div className="num text-[10px] text-muted">95% CI {pct(ci[0])}–{pct(ci[1])}</div>}
              </div>
            ))}
          </div>
          {p.expectancy_r != null && <p className="mt-1 text-[11px] text-muted">Underlying expectancy <span className={cx("num", moveClass(p.expectancy_r))}>{num(p.expectancy_r, 3)} R</span> · avg hold {num(p.avg_holding_bars, 1)} bars</p>}
        </section>
      )}

      <p className="text-xs text-muted"><strong className="text-ink">Level method:</strong> {s.level_method}</p>
      {s.entry_method && <p className="text-xs text-muted"><strong className="text-ink">Entry:</strong> {s.entry_method}</p>}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {s.reasons && s.reasons.length > 0 && (
          <ul className="space-y-0.5 text-xs">{s.reasons.slice(0, 8).map((r) => <li key={r} className="flex gap-1.5"><span className="text-up" aria-hidden>✓</span><span>{r}</span></li>)}</ul>
        )}
        {s.risk_factors && s.risk_factors.length > 0 && (
          <ul className="space-y-0.5 text-xs text-amber-200/90">{s.risk_factors.map((r) => <li key={r} className="flex gap-1.5"><span aria-hidden>⚠</span><span>{r}</span></li>)}</ul>
        )}
      </div>

      {s.checks && s.checks.length > 0 && (
        <Collapsible title={`Validation checklist (${s.checks.filter((x) => !x.passed && x.severity === "block").length} blocking)`}>
          <ChecksPanel checks={s.checks} />
        </Collapsible>
      )}
      {s.alternatives && s.alternatives.length > 0 && (
        <Collapsible title={`Alternative contracts (${s.alternatives.length}${s.rejected_contracts != null ? ` · ${s.rejected_contracts} rejected` : ""})`}>
          <TableWrap label="Alternative contracts">
            <table className="tbl min-w-[480px] text-xs">
              <thead><tr><th scope="col">Contract</th><th scope="col" className="text-right">Entry</th><th scope="col" className="text-right">Δ</th><th scope="col" className="text-right">R:R</th><th scope="col" className="text-right">Theta burn</th><th scope="col" className="text-right">Score</th><th scope="col">Checks</th></tr></thead>
              <tbody>
                {s.alternatives.map((a) => (
                  <tr key={a.label}>
                    <td className="num">{a.label}</td><td className="num text-right">{premium(a.entry)}</td><td className="num text-right">{num(a.delta, 2)}</td>
                    <td className="num text-right">1:{num(a.rr, 2)}</td><td className="num text-right">{pct(a.theta_burn_pct, 1)}</td><td className="num text-right">{num(a.contract_score, 1)}</td>
                    <td>{a.passes ? <Pill tone="green">contract OK</Pill> : <Pill tone="red">fails</Pill>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Collapsible>
      )}
      <footer className="border-t border-edge pt-2"><DataStamp meta={s.data} asOf={s.as_of} /></footer>
    </article>
  );
}
