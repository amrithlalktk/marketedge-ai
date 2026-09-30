"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ImportanceChart, ReliabilityChart } from "@/components/MlBlocks";
import { MarketSwitcher } from "@/components/MarketSwitcher";
import { Card, Disclaimer, EmptyState, ErrorState, InlineError, Loading, PageHeader, Pill, Segmented, Stat, TableWrap, UpgradeNote, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, humanize, num, pct, period } from "@/lib/format";
import { marketLabel, useMarket } from "@/lib/market";
import type { MlMetrics, MlModelDetail, MlModelSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const STATUS_TONE: Record<string, "green" | "blue" | "slate" | "red" | "amber"> = { active: "green", candidate: "blue", retired: "slate", suspended: "red" };

function beats(m?: MlMetrics | null, b?: MlMetrics | null): boolean | null {
  if (!m || !b) return null;
  return m.brier < b.brier;
}

type Sig = { mean_brier_diff: number; ci95: [number, number]; p_model_better: number; resamples: number } | null | undefined;

function MetricsCompare({ model, baseline, gate, sig }: { model: MlMetrics | null; baseline: MlMetrics | null; gate: MlModelSummary["gate"]; sig?: Sig }) {
  if (!model || !baseline) return <EmptyState title="No out-of-sample metrics" />;
  const rows: { k: string; label: string; m: number | null; b: number | null; digits: number; better: "low" | "high" | null; fmt?: "pct" }[] = [
    { k: "brier", label: "Brier score", m: model.brier, b: baseline.brier, digits: 4, better: "low" },
    { k: "log_loss", label: "Log loss", m: model.log_loss, b: baseline.log_loss, digits: 4, better: "low" },
    { k: "auc", label: "AUC", m: model.auc, b: baseline.auc, digits: 3, better: "high" },
    { k: "base_rate", label: "Observed T1 rate (base rate)", m: model.base_rate, b: baseline.base_rate, digits: 1, better: null, fmt: "pct" },
    { k: "mean_predicted", label: "Mean predicted", m: model.mean_predicted, b: baseline.mean_predicted, digits: 1, better: null, fmt: "pct" },
    { k: "n", label: "OOS trades (n)", m: model.n, b: baseline.n, digits: 0, better: null },
  ];
  const f = (v: number | null, r: (typeof rows)[number]) => (v == null ? "—" : r.fmt === "pct" ? pct(100 * v, r.digits) : num(v, r.digits));
  const verdict = beats(model, baseline);
  return (
    <div className="space-y-3">
      <div className={cx("rounded-md border p-3", gate?.eligible ? "border-green-800 bg-green-950/30" : "border-amber-800 bg-amber-950/30")}>
        <p className={cx("text-sm font-bold", gate?.eligible ? "text-green-200" : "text-amber-200")}>
          Beats the empirical baseline out of sample? {verdict ? (gate?.eligible ? "Yes" : "Marginally — lower Brier, but not by the required margin / other gate checks failed") : "No"} · Activation gate: {gate?.eligible ? "PASSED" : "NOT PASSED"}
        </p>
        {gate && gate.reasons.length > 0 && <ul className="mt-1 space-y-0.5 text-xs text-amber-100">{gate.reasons.map((r) => <li key={r}>✗ {r}</li>)}</ul>}
        {sig && (
          <p className="mt-2 text-xs text-ink">
            Statistical reliability (paired bootstrap, {sig.resamples.toLocaleString()} resamples): the model beat the baseline in{" "}
            <strong className={cx("num", sig.p_model_better >= 0.95 ? "text-up" : "text-amber-300")}>{pct(100 * sig.p_model_better, 1)}</strong> of resamples (need ≥ 95%).
            Mean Brier difference (model − baseline) <span className="num">{num(sig.mean_brier_diff, 5)}</span>, 95% CI <span className="num">[{num(sig.ci95[0], 5)}, {num(sig.ci95[1], 5)}]</span>
            {sig.ci95[0] < 0 && sig.ci95[1] > 0 ? " — the interval spans 0, so the improvement is not reliable." : sig.ci95[1] < 0 ? " — entirely below 0 (model better)." : " — at or above 0 (model not better)."}
          </p>
        )}
      </div>
      <TableWrap label="Model vs baseline metrics">
        <table className="tbl min-w-[420px] text-sm">
          <thead><tr><th scope="col">Metric (out of sample)</th><th scope="col" className="text-right">Model</th><th scope="col" className="text-right">Empirical baseline</th><th scope="col" className="text-right">Better</th></tr></thead>
          <tbody>
            {rows.map((r) => {
              const win = r.better && r.m != null && r.b != null ? (r.better === "low" ? r.m < r.b : r.m > r.b) : null;
              return (
                <tr key={r.k}>
                  <th scope="row" className="text-left font-medium">{r.label}</th>
                  <td className={cx("num text-right", win === true && "font-semibold text-up")}>{f(r.m, r)}</td>
                  <td className={cx("num text-right", win === false && "font-semibold text-up")}>{f(r.b, r)}</td>
                  <td className="text-right text-xs">{win == null ? "—" : win ? "model" : "baseline"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableWrap>
      <p className="text-[11px] text-muted">Brier and log loss: lower is better. AUC: 0.5 = no discrimination. The baseline is the platform&apos;s empirical hit rate for the same conditioning.</p>
    </div>
  );
}

function ModelDetail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const { can } = useAuth();
  const q = useApi<MlModelDetail>(() => api.ml.model(id), [id]);
  const [algo, setAlgo] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="model" />;
  if (!q.data) return <Loading />;
  const m = q.data;
  const algos = Object.keys(m.results ?? {});
  const cur = algo && algos.includes(algo) ? algo : m.algo;
  const res = m.results?.[cur];
  const setStatus = async (status: "active" | "retired") => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await api.ml.setStatus(m.id, status, status === "retired" ? "Retired from the ML page" : undefined);
      setMsg(`Model #${r.id} is now ${r.status}. ${r.note}`);
      q.reload(); onChanged();
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 409 ? `Activation refused by the gate: ${e.message}` : e instanceof Error ? e.message : "Failed");
    } finally { setBusy(false); }
  };
  const gateTip = m.gate?.eligible ? "Passed the out-of-sample gate" : `Not eligible: ${(m.gate?.reasons ?? []).join("; ")}`;

  return (
    <div className="space-y-4">
      <Card title={`Model #${m.id} · ${m.market} · ${m.algo} v${m.version}`} right={<span className="flex flex-wrap gap-1"><Pill tone={STATUS_TONE[m.status] ?? "slate"}>{m.status}</Pill>{m.is_sample_data && <Pill tone="amber">SAMPLE DATA</Pill>}{m.calibrated && <Pill tone="blue">calibrated</Pill>}</span>}>
        <p className="text-xs text-muted">Target: {m.target ?? "t1_before_stop"} · trained on {m.n_events ?? "—"} historical events · {period(m.period)} · created {dateTime(m.created_at)}{m.activated_at ? ` · activated ${dateTime(m.activated_at)}` : ""} · {m.features.length} features</p>
        {m.is_sample_data && <p className="mt-2 rounded border border-amber-800 bg-amber-950/30 px-2 py-1 text-xs text-amber-200">Trained on SAMPLE/synthetic data — its metrics say nothing about real markets.</p>}
        {can("admin:settings") && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span title={gateTip}>
              <button type="button" className="btn-primary" disabled={busy || m.status === "active" || !m.eligible} aria-describedby={`gate-${m.id}`} onClick={() => setStatus("active")}>Activate</button>
            </span>
            {!m.eligible && m.status !== "active" && (
              <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={busy} onClick={() => setStatus("active")} title="The server re-checks the gate and will refuse a model that did not pass it">
                Request activation anyway (server re-checks gate)
              </button>
            )}
            <button type="button" className="btn-ghost" disabled={busy || m.status === "retired"} onClick={() => setStatus("retired")}>Retire</button>
            <span id={`gate-${m.id}`} className={cx("text-xs", m.gate?.eligible ? "text-green-300" : "text-amber-300")}>{m.gate?.eligible ? "Eligible for activation" : `Not eligible — ${(m.gate?.reasons ?? []).join("; ")}`}</span>
          </div>
        )}
        {msg && <p role="status" className="mt-2 text-sm text-green-300">{msg}</p>}
        <InlineError error={err} />
      </Card>

      <Card title={`Out-of-sample results — ${cur}`} right={algos.length > 1 ? <Segmented<string> label="Algorithm" value={cur} onChange={setAlgo} options={algos.map((a) => ({ value: a, label: `${a}${a === m.algo ? " (selected)" : ""}` }))} /> : null}>
        {!res?.oos ? <EmptyState title="No results for this algorithm" /> : (
          <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
            <MetricsCompare sig={res.oos.significance} model={res.oos.model} baseline={res.oos.baseline} gate={cur === m.algo ? m.gate : { eligible: false, reasons: ["Not the selected algorithm for this model"] }} />
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Reliability diagram</h3>
              <ReliabilityChart model={res.oos.reliability_model} baseline={res.oos.reliability_baseline} />
            </div>
          </div>
        )}
      </Card>

      {res && res.folds.length > 0 && (
        <Card title={`Purged walk-forward folds (${res.folds.length})`}>
          <TableWrap label="Walk-forward folds">
            <table className="tbl min-w-[760px] text-xs">
              <thead><tr><th scope="col">#</th><th scope="col">Train end</th><th scope="col">Calibration window</th><th scope="col">Test window</th><th scope="col" className="text-right">n train / calib / test</th><th scope="col" className="text-right">Model Brier</th><th scope="col" className="text-right">Baseline Brier</th><th scope="col" className="text-right">Model AUC</th><th scope="col">Beats?</th></tr></thead>
              <tbody>
                {res.folds.map((f, i) => {
                  const w = f.model.brier < f.baseline.brier;
                  return (
                    <tr key={i}>
                      <td className="num">{i + 1}</td><td className="num whitespace-nowrap">{f.train_end}</td>
                      <td className="num whitespace-nowrap">{period(f.calib)}</td><td className="num whitespace-nowrap">{period(f.test)}</td>
                      <td className="num text-right">{f.n_train} / {f.n_calib} / {f.model.n}</td>
                      <td className={cx("num text-right", w && "text-up")}>{num(f.model.brier, 4)}</td><td className={cx("num text-right", !w && "text-up")}>{num(f.baseline.brier, 4)}</td>
                      <td className="num text-right">{num(f.model.auc, 3)}</td><td>{w ? <Pill tone="green">yes</Pill> : <Pill tone="amber">no</Pill>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
          <p className="mt-2 text-[11px] text-muted">Each fold trains on data up to “train end”, calibrates on a later window and is tested on a still-later window, with an embargo between them so overlapping trades cannot leak.</p>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Permutation importance (Brier increase when shuffled)">
          {m.importance && m.importance.length ? <ImportanceChart items={m.importance} /> : <EmptyState title="Not computed" />}
          <p className="mt-2 text-[11px] text-muted">Larger = the model relies more on this feature out of sample. Negative values mean shuffling helped (the feature adds noise).</p>
        </Card>
        <Card title="Validation configuration">
          {m.config ? (
            <dl className="grid grid-cols-2 gap-2 text-xs">
              {Object.entries(m.config).map(([k, v]) => <Stat key={k} label={humanize(k)} value={k.includes("fraction") ? pct(100 * v, 0) : k === "brier_margin" ? `beat by ≥ ${num(100 * (1 - v), 0)}%` : String(v)} />)}
            </dl>
          ) : <EmptyState title="No configuration recorded" />}
          <details className="mt-3 text-xs">
            <summary className="cursor-pointer text-muted">Features ({m.features.length})</summary>
            <p className="mt-1 break-words font-mono text-[11px] text-muted">{m.features.join(", ")}</p>
          </details>
        </Card>
      </div>
    </div>
  );
}

function MlInner() {
  const { can } = useAuth();
  const [market] = useMarket();
  const params = useSearchParams();
  const router = useRouter();
  const allowed = can("signals:read_all");
  const list = useApi<{ items: MlModelSummary[]; note: string }>(() => api.ml.models(market), [market], allowed);
  const [trainMsg, setTrainMsg] = useState<string | null>(null);
  const [trainErr, setTrainErr] = useState<string | null>(null);
  const [training, setTraining] = useState(false);
  const selRaw = Number(params.get("model"));
  if (!allowed) return <><PageHeader title="ML models" /><UpgradeNote feature="The ML model lab" /><Disclaimer /></>;
  const items = list.data?.items ?? [];
  const selected = items.find((i) => i.id === selRaw)?.id ?? items[0]?.id ?? null;
  const select = (id: number) => {
    const p = new URLSearchParams(params.toString());
    p.set("model", String(id));
    router.replace(`/ml?${p.toString()}`, { scroll: false });
  };
  const train = async () => {
    setTraining(true); setTrainErr(null); setTrainMsg(null);
    try {
      const r = await api.admin.mlTrain(market);
      setTrainMsg(`Training job #${r.job_id} submitted for ${marketLabel(market)}. New candidates appear here when it finishes.`);
      list.reload();
    } catch (e) { setTrainErr(e instanceof Error ? e.message : "Failed"); } finally { setTraining(false); }
  };

  return (
    <>
      <PageHeader title={`ML models — ${marketLabel(market)}`} subtitle="Calibrated probability models for “T1 before stop”, validated against the empirical hit rate." right={<MarketSwitcher />} />
      <div role="note" className="mb-4 rounded-md border border-blue-800 bg-blue-950/40 p-3 text-sm text-blue-100">
        <p className="font-semibold">How ML is used</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
          <li>ML probabilities are shown <strong>next to</strong> the empirical historical hit rate — never instead of it.</li>
          <li>A model can only be activated if it beats the empirical baseline out of sample (purged walk-forward, calibrated).</li>
          <li>An active model is automatically suspended if its live accuracy drifts below the baseline.</li>
        </ul>
        {list.data?.note && <p className="mt-1 text-[11px] text-blue-200/80">{list.data.note}</p>}
      </div>
      {can("admin:jobs") && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <button className="btn-primary" disabled={training} onClick={train}>{training ? "Training… (can take a while)" : `Train new candidate — ${marketLabel(market)}`}</button>
          {trainMsg && <span role="status" className="text-sm text-green-300">{trainMsg}</span>}
          <InlineError error={trainErr} />
        </div>
      )}
      {list.error ? <ErrorState error={list.error} onRetry={list.reload} what="models" /> : !list.data ? <Loading /> : items.length === 0 ? (
        <EmptyState title={`No models trained for ${marketLabel(market)} yet`}>{can("admin:jobs") ? "Train a candidate above." : "An administrator can train one."} Until a model passes the gate, setups use the empirical hit rate only.</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <Card title={`Models (${items.length})`}>
            <ul className="divide-y divide-edge text-sm">
              {items.map((m) => {
                const b = beats(m.oos_model, m.oos_baseline);
                return (
                  <li key={m.id}>
                    <button type="button" aria-current={m.id === selected ? "true" : undefined} onClick={() => select(m.id)} className={cx("w-full px-1 py-2 text-left hover:bg-panel2", m.id === selected && "bg-panel2")}>
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="font-medium">#{m.id} {m.algo} v{m.version}</span>
                        <Pill tone={STATUS_TONE[m.status] ?? "slate"}>{m.status}</Pill>
                        <Pill tone={m.eligible ? "green" : "amber"}>{m.eligible ? "gate passed" : "gate failed"}</Pill>
                        {m.is_sample_data && <Pill tone="amber">SAMPLE</Pill>}
                      </span>
                      <span className="mt-0.5 block text-[11px] text-muted">
                        OOS Brier <span className={cx("num", b ? "text-up" : "text-amber-300")}>{num(m.oos_model?.brier, 4)}</span> vs baseline <span className="num">{num(m.oos_baseline?.brier, 4)}</span> · AUC <span className="num">{num(m.oos_model?.auc, 3)}</span> · n={m.oos_model?.n ?? "—"} · {dateTime(m.created_at)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </Card>
          <div className="min-w-0 xl:col-span-2">{selected != null && <ModelDetail id={selected} onChanged={list.reload} />}</div>
        </div>
      )}
      <Disclaimer />
    </>
  );
}

export default function MlPage() {
  return (
    <Suspense>
      <MlInner />
    </Suspense>
  );
}
