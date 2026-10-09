"use client";

import { Suspense } from "react";
import { MarketSwitcher } from "@/components/MarketSwitcher";
import { Card, Disclaimer, ErrorState, PageHeader, Pill, Skeleton, Stat, TableWrap, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { DASH, humanize } from "@/lib/format";
import { marketLabel, useMarket } from "@/lib/market";
import type { Calibration, Diagnostics, GroupStats, StrategyValidation, TradeStats } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const r = (v: number | null | undefined, d = 3) => (v == null ? DASH : `${v > 0 ? "+" : ""}${v.toFixed(d)} R`);
const p = (v: number | null | undefined) => (v == null ? DASH : `${v}%`);
const tone = (v: number | null | undefined) => (v == null ? undefined : v > 0 ? "text-up" : "text-down");

/** One row of results: what happened to a group of historical trades. */
function StatsTable({ rows, keyLabel }: { rows: GroupStats[]; keyLabel: string }) {
  if (!rows?.length) return <p className="text-xs text-muted">Not enough trades in any group.</p>;
  return (
    <TableWrap label={keyLabel}>
      <table className="w-full text-sm">
        <thead className="text-left text-[11px] uppercase text-muted">
          <tr><th className="py-1 pr-3">{keyLabel}</th><th className="pr-3 text-right">Trades</th><th className="pr-3 text-right">Target 1</th>
            <th className="pr-3 text-right">Stop first</th><th className="pr-3 text-right">Time exit</th><th className="pr-3 text-right">Avg result</th>
            <th className="pr-3 text-right">Profit factor</th><th className="text-right">Max drawdown</th></tr>
        </thead>
        <tbody className="num">
          {rows.map((x) => (
            <tr key={x.key} className="border-t border-edge/60">
              <td className="py-1 pr-3 font-medium">{humanize(x.key)}</td>
              <td className="pr-3 text-right">{x.trades}</td>
              <td className="pr-3 text-right">{p(x.t1_rate)}</td>
              <td className="pr-3 text-right text-down">{p(x.stop_rate)}</td>
              <td className="pr-3 text-right">{p(x.time_exit_rate)}</td>
              <td className={cx("pr-3 text-right font-semibold", tone(x.expectancy_r))}>
                {r(x.expectancy_r)}{x.expectancy_se != null && <span className="ml-1 text-[11px] font-normal text-muted">± {x.expectancy_se}</span>}
              </td>
              <td className="pr-3 text-right">{x.profit_factor ?? DASH}</td>
              <td className="text-right">{x.max_drawdown_r != null ? `${x.max_drawdown_r} R` : DASH}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function Overall({ s, label }: { s: TradeStats; label: string }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
      <Stat label={label} value={s.trades} />
      <Stat label="Reached Target 1" value={p(s.t1_rate)} valueClass="text-up" />
      <Stat label="Hit stop first" value={p(s.stop_rate)} valueClass="text-down" />
      <Stat label="Time exit" value={p(s.time_exit_rate)} />
      <Stat label="Avg result" value={r(s.expectancy_r)} valueClass={tone(s.expectancy_r)} sub={s.expectancy_se != null ? `± ${s.expectancy_se} (1 SE)` : undefined} />
      <Stat label="Profit factor" value={s.profit_factor ?? DASH} />
      <Stat label="Before costs" value={s.avg_gross_return_pct != null ? `${s.avg_gross_return_pct}%` : DASH} sub="avg per trade" />
      <Stat label="After costs" value={s.avg_net_return_pct != null ? `${s.avg_net_return_pct}%` : DASH} valueClass={tone(s.avg_net_return_pct)} sub="avg per trade" />
    </div>
  );
}

const LOSS_LABELS: Record<string, string> = {
  stopped_trades: "Stopped trades",
  fast_failures_pct: "Failed within 2 days (false start / poor entry)",
  gap_through_stop_pct: "Gapped through the stop overnight",
  were_in_profit_0_5r_pct: "Were +0.5 R in profit before stopping",
  were_in_profit_1r_pct: "Were +1 R in profit before stopping",
  avg_r_lost: "Average loss (R)",
  median_bars_to_stop: "Median days to stop",
  median_stop_atr_stopped: "Median stop distance, losers (ATR)",
  median_stop_atr_winners: "Median stop distance, winners (ATR)",
  stop_rate_tight_lt_1atr: "Stop rate when stop < 1 ATR",
  stop_rate_normal_ge_1atr: "Stop rate when stop ≥ 1 ATR",
  ambiguous_same_bar_pct: "Stop and target on the same day (order unknown)",
};

function LossProfile({ lp }: { lp: Record<string, number | null> }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
      {Object.entries(lp).map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-b border-edge/40 py-1">
          <dt className="text-muted">{LOSS_LABELS[k] ?? humanize(k)}</dt>
          <dd className="num font-semibold">{v == null ? DASH : k.endsWith("_pct") || k.startsWith("stop_rate") ? `${v}%` : v}</dd>
        </div>
      ))}
    </dl>
  );
}

function ValidationTable({ v }: { v: Record<string, StrategyValidation> }) {
  const rows = Object.entries(v).sort((a, b) => (b[1].out_of_sample?.expectancy_r ?? -9) - (a[1].out_of_sample?.expectancy_r ?? -9));
  return (
    <TableWrap label="Strategy validation">
      <table className="w-full text-sm">
        <thead className="text-left text-[11px] uppercase text-muted">
          <tr><th className="py-1 pr-3">Strategy</th><th className="pr-3">Status</th><th className="pr-3 text-right">Design avg</th>
            <th className="pr-3 text-right">Out-of-sample trades</th><th className="pr-3 text-right">Out-of-sample avg</th>
            <th className="pr-3 text-right">Profit factor</th><th className="pr-3 text-right">Stop first</th><th>Why</th></tr>
        </thead>
        <tbody>
          {rows.map(([id, x]) => (
            <tr key={id} className="border-t border-edge/60 align-top">
              <td className="py-1 pr-3 font-medium">{humanize(id)}</td>
              <td className="pr-3"><Pill tone={x.status === "VALIDATED" ? "green" : "amber"}>{x.status === "VALIDATED" ? "Validated" : "Unvalidated · paper only"}</Pill></td>
              <td className={cx("num pr-3 text-right", tone(x.design?.expectancy_r))}>{r(x.design?.expectancy_r)}</td>
              <td className="num pr-3 text-right">{x.out_of_sample?.trades ?? 0}</td>
              <td className={cx("num pr-3 text-right font-semibold", tone(x.out_of_sample?.expectancy_r))}>
                {r(x.out_of_sample?.expectancy_r)}{x.out_of_sample?.expectancy_se != null && <span className="ml-1 text-[11px] font-normal text-muted">± {x.out_of_sample.expectancy_se}</span>}
              </td>
              <td className="num pr-3 text-right">{x.out_of_sample?.profit_factor ?? DASH}</td>
              <td className="num pr-3 text-right">{p(x.out_of_sample?.stop_rate)}</td>
              <td className="text-xs text-muted">{x.reasons.length ? x.reasons.join("; ") : "passes every acceptance rule"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function CalibrationNote({ c }: { c: Calibration }) {
  if (!c || c.avg_predicted_t1 == null) return <p className="text-sm text-muted">Not enough out-of-sample trades to check calibration.</p>;
  const over = (c.overstatement_pts ?? 0) > 0;
  return (
    <div className="space-y-2 text-sm">
      <p>
        Over {c.period?.join(" → ")}, the app would have shown an average <strong>{c.avg_predicted_t1}%</strong> chance of reaching Target 1;{" "}
        <strong className={over ? "text-down" : "text-up"}>{c.actual_t1}%</strong> actually did.{" "}
        <Pill tone={c.status === "calibrated" ? "green" : "amber"}>{c.status}</Pill>
      </p>
      {c.bands && c.bands.length > 0 && (
        <TableWrap label="Calibration bands">
          <table className="text-sm">
            <thead className="text-left text-[11px] uppercase text-muted"><tr><th className="pr-4">Shown chance</th><th className="pr-4 text-right">Trades</th><th className="pr-4 text-right">Avg shown</th><th className="text-right">Actual</th></tr></thead>
            <tbody className="num">{c.bands.map((b) => (
              <tr key={b.predicted} className="border-t border-edge/60"><td className="pr-4">{b.predicted}%</td><td className="pr-4 text-right">{b.trades}</td><td className="pr-4 text-right">{b.avg_predicted}%</td><td className="text-right">{b.actual}%</td></tr>
            ))}</tbody>
          </table>
        </TableWrap>
      )}
    </div>
  );
}

function Inner() {
  const [market] = useMarket();
  const q = useApi<Diagnostics>(() => api.markets.diagnostics(market), [market]);
  const d = q.data;
  return (
    <>
      <PageHeader title="Diagnostics" subtitle="Why trades lose: every historical trade of the current rules, how the stopped ones failed, and which strategies are validated out of sample."
        right={<MarketSwitcher />} />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="diagnostics" /> : !d ? (
        <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-64" /></div>
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-muted">
            {marketLabel(market as never)} · measured on stored daily history {d.period.join(" → ")} · updated after each daily scan
            {d.is_sample && <strong className="ml-1 text-fuchsia-300">SAMPLE data — not real results</strong>}
          </p>

          {d.validation?.strategies && (
            <Card title="Strategy validation (out of sample)" right={<span className="text-xs text-muted">{d.validation.validated.length} of {Object.keys(d.validation.strategies).length} validated</span>}>
              <p className="mb-2 text-xs text-muted">
                A strategy publishes trade ideas only if its most recent 40% of history (never used to choose it) is still profitable after costs
                with a margin for noise, with enough trades, a profit factor ≥ 1.1 and limited drawdown. Otherwise its setups are tracked as paper trades.
              </p>
              <ValidationTable v={d.validation.strategies} />
            </Card>
          )}

          <Card title="Is the shown chance accurate?">
            <CalibrationNote c={d.validation?.calibration ?? (d.gate_comparison.policies[0]?.out_of_sample_calibration as Calibration)} />
          </Card>

          <Card title="All historical trades of the current rules">
            <Overall s={d.all_trades.overall} label="Trades" />
            <h3 className="mb-1 mt-4 text-sm font-semibold">How the stopped trades failed</h3>
            <LossProfile lp={d.all_trades.loss_profile} />
          </Card>

          <Card title="Results by strategy"><StatsTable rows={d.all_trades.by_strategy} keyLabel="Strategy" /></Card>
          <Card title="Results by market regime"><StatsTable rows={d.all_trades.by_regime} keyLabel="Regime" /></Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Results by setup score"><StatsTable rows={d.all_trades.by_score_bucket} keyLabel="Score" /></Card>
            <Card title="Results by year"><StatsTable rows={d.all_trades.by_year} keyLabel="Year" /></Card>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Results by exit"><StatsTable rows={d.all_trades.by_exit_reason} keyLabel="Exit" /></Card>
            <Card title="Results by sector">
              <StatsTable rows={d.all_trades.by_sector} keyLabel="Sector" />
              {d.all_trades.by_sector.length === 1 && d.all_trades.by_sector[0].key === "Unknown" && (
                <p className="mt-1 text-[11px] text-muted">The data source provides no stock-to-sector mapping, so sector results are not available.</p>
              )}
            </Card>
          </div>

          <Card title="What the old publish gate would have published">
            <Overall s={d.published_by_current_gate.overall} label="Published trades" />
            <p className="mt-2 text-xs text-muted">
              Rejected by: {Object.entries(d.published_by_current_gate.rejected_by).map(([k, v]) => `${k} ${v}`).join(" · ")}
            </p>
          </Card>

          <Card title={`Publish rules compared — chosen before ${d.gate_comparison.split}, tested after`}>
            <TableWrap label="Gate comparison">
              <table className="w-full text-sm">
                <thead className="text-left text-[11px] uppercase text-muted">
                  <tr><th className="py-1 pr-3">Rule</th><th className="pr-3 text-right">Design trades</th><th className="pr-3 text-right">Design avg</th>
                    <th className="pr-3 text-right">Out-of-sample trades</th><th className="pr-3 text-right">Out-of-sample avg</th><th className="text-right">Stop first</th></tr>
                </thead>
                <tbody className="num">{d.gate_comparison.policies.map((x) => (
                  <tr key={x.policy.name} className="border-t border-edge/60">
                    <td className="py-1 pr-3 font-sans">{x.policy.name}</td>
                    <td className="pr-3 text-right">{x.design.trades}</td>
                    <td className={cx("pr-3 text-right", tone(x.design.expectancy_r))}>{r(x.design.expectancy_r)}</td>
                    <td className="pr-3 text-right">{x.out_of_sample.trades}</td>
                    <td className={cx("pr-3 text-right font-semibold", tone(x.out_of_sample.expectancy_r))}>{r(x.out_of_sample.expectancy_r)}</td>
                    <td className="text-right">{p(x.out_of_sample.stop_rate)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </TableWrap>
          </Card>

          {d.entry_too_close_to_stop?.bands && (
            <Card title="Results by stop distance (after the next-day fill)">
              <StatsTable rows={d.entry_too_close_to_stop.bands.map((b) => ({ ...b, key: `${b.risk_atr} ATR` }))} keyLabel="Stop distance" />
            </Card>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Data checks">
              <ul className="space-y-1 text-sm">
                <li>{d.data_audit.instruments} instruments, {d.data_audit.bars.toLocaleString()} daily bars</li>
                <li>Suspected unadjusted splits/bonuses/demergers: <strong>{d.data_audit.suspected_unadjusted_corporate_actions}</strong>
                  {d.data_audit.examples.length > 0 && <span className="text-muted"> ({d.data_audit.examples.map((x) => `${x.symbol} ${x.date}`).join(", ")})</span>}</li>
                <li>Trades near one: {d.data_audit.trades_near_suspected_action_pct}%</li>
                <li>Stop and target on the same day (order unknown, stop assumed): {p(d.all_trades.overall.ambiguous_rate)}</li>
              </ul>
            </Card>
            <Card title="Live track record">
              <ul className="space-y-1 text-sm">
                <li>{d.track_record.published} tracked ideas · {d.track_record.open} still open · {d.track_record.resolved} finished</li>
                <li>{d.track_record.t1_hit} reached Target 1 · {d.track_record.stop_hit} hit the stop</li>
                {d.track_record.replay_agreement && (
                  <li>Replay agrees with the tracker on {d.track_record.replay_agreement.same} of {d.track_record.replay_agreement.same + d.track_record.replay_agreement.different} finished ideas</li>
                )}
              </ul>
            </Card>
          </div>

          <ul className="list-disc space-y-1 pl-5 text-[11px] text-muted">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      )}
      <Disclaimer />
    </>
  );
}

export default function DiagnosticsPage() {
  return (
    <Suspense>
      <Inner />
    </Suspense>
  );
}
