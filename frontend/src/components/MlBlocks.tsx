"use client";

import { useEffect, useRef, useState } from "react";
import { num, pct } from "@/lib/format";
import type { MlRegime, ReliabilityBin, SetupMl } from "@/lib/types";
import { Bar, Card, Pill, TableWrap, cx } from "./ui";

function useWidth(initial = 480) {
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

/** One line for setup cards. */
export function MlLine({ ml }: { ml?: SetupMl | null }) {
  if (!ml) return null;
  if (!ml.available) return <p className="text-[11px] text-muted">No validated ML model active — empirical rate only.</p>;
  return (
    <p className="text-xs text-muted">
      ML estimate (T1 before stop) <span className="num font-semibold text-blue-200">{pct(ml.probability_t1_pct)}</span> vs empirical <span className="num text-ink">{pct(ml.empirical_t1_pct)}</span>
      <span className="text-[10px]"> · v{ml.version} {ml.algo} · OOS Brier {num(ml.oos_brier, 3)} vs {num(ml.baseline_brier, 3)} (n={ml.oos_n ?? "—"})</span>
    </p>
  );
}

export function MlPanel({ ml }: { ml?: SetupMl | null }) {
  if (!ml) return null;
  if (!ml.available) {
    return (
      <Card title="ML estimate">
        <p className="text-sm text-muted">No validated ML model active — the empirical hit rate is the only probability estimate.</p>
        <p className="mt-1 text-[11px] text-muted">{ml.note} Models are only activated after beating the empirical baseline out of sample.</p>
      </Card>
    );
  }
  const better = ml.oos_brier != null && ml.baseline_brier != null && ml.oos_brier < ml.baseline_brier;
  return (
    <Card title="ML estimate" right={<Pill tone="blue">model v{ml.version} · {ml.algo}{ml.calibrated ? " · calibrated" : ""}</Pill>}>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="mb-1 flex justify-between text-xs"><span className="text-muted">ML probability (T1 before stop)</span><span className="num font-semibold">{pct(ml.probability_t1_pct)}</span></div>
          <Bar value={ml.probability_t1_pct} tone="blue" />
        </div>
        <div>
          <div className="mb-1 flex justify-between text-xs"><span className="text-muted">Empirical T1 hit rate</span><span className="num font-semibold">{pct(ml.empirical_t1_pct)}</span></div>
          <Bar value={ml.empirical_t1_pct ?? 0} tone="green" />
        </div>
      </div>
      <p className="mt-2 text-xs text-muted">
        Out-of-sample Brier <span className={cx("num", better ? "text-up" : "text-amber-300")}>{num(ml.oos_brier, 4)}</span> vs baseline <span className="num">{num(ml.baseline_brier, 4)}</span> (lower is better) · AUC <span className="num">{num(ml.oos_auc, 3)}</span> · n = <span className="num">{ml.oos_n ?? "—"}</span>
      </p>
      <p className="mt-1 text-[11px] text-muted">{ml.note}</p>
    </Card>
  );
}

/** Reliability diagram: mean predicted vs observed per bin, model and baseline, with the diagonal and bin counts. */
export function ReliabilityChart({ model, baseline }: { model: ReliabilityBin[]; baseline: ReliabilityBin[] }) {
  const [box, W] = useWidth();
  const size = Math.min(W, 460);
  const pad = 34;
  const inner = size - pad - 10;
  const all = [...model, ...baseline];
  const maxV = Math.min(1, Math.max(0.5, ...all.map((b) => Math.max(b.mean_predicted, b.observed_rate))) * 1.1);
  const sx = (v: number) => pad + (v / maxV) * inner;
  const sy = (v: number) => 10 + inner - (v / maxV) * inner;
  const maxCount = Math.max(1, ...all.map((b) => b.count));
  const ticks = [0, maxV / 4, maxV / 2, (3 * maxV) / 4, maxV];
  const series = [
    { name: "Model", bins: model, color: "#60a5fa" },
    { name: "Empirical baseline", bins: baseline, color: "#f5a524" },
  ];
  return (
    <div ref={box}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} className="block max-w-full" role="img"
        aria-label={`Reliability diagram. Model bins: ${model.map((b) => `predicted ${(100 * b.mean_predicted).toFixed(0)}% observed ${(100 * b.observed_rate).toFixed(0)}% n=${b.count}`).join("; ")}`}>
        <rect x={pad} y={10} width={inner} height={inner} fill="#0e141b" />
        {ticks.map((t) => (
          <g key={t}>
            <line x1={sx(t)} x2={sx(t)} y1={10} y2={10 + inner} stroke="#1c2733" />
            <line x1={pad} x2={pad + inner} y1={sy(t)} y2={sy(t)} stroke="#1c2733" />
            <text x={sx(t)} y={size - 8} textAnchor="middle" fontSize="10" fill="#8b9bb0">{Math.round(100 * t)}%</text>
            <text x={pad - 4} y={sy(t) + 3} textAnchor="end" fontSize="10" fill="#8b9bb0">{Math.round(100 * t)}%</text>
          </g>
        ))}
        <line x1={sx(0)} y1={sy(0)} x2={sx(maxV)} y2={sy(maxV)} stroke="#64748b" strokeDasharray="4 4" />
        {series.map((s) => (
          <g key={s.name}>
            <polyline fill="none" stroke={s.color} strokeWidth="1.5" points={s.bins.map((b) => `${sx(b.mean_predicted)},${sy(b.observed_rate)}`).join(" ")} />
            {s.bins.map((b, i) => (
              <circle key={i} cx={sx(b.mean_predicted)} cy={sy(b.observed_rate)} r={3 + 5 * Math.sqrt(b.count / maxCount)} fill={s.color} fillOpacity="0.55" stroke={s.color}>
                <title>{`${s.name}: bin ${(100 * b.bin[0]).toFixed(0)}–${(100 * b.bin[1]).toFixed(0)}% · predicted ${(100 * b.mean_predicted).toFixed(1)}% · observed ${(100 * b.observed_rate).toFixed(1)}% · n=${b.count}`}</title>
              </circle>
            ))}
          </g>
        ))}
      </svg>
      <p className="mt-1 flex flex-wrap gap-3 text-[11px] text-muted">
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-[#60a5fa]" />Model</span>
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-[#f5a524]" />Empirical baseline</span>
        <span>dashed = perfect calibration · x = predicted, y = observed · dot size ∝ trades in bin</span>
      </p>
      <TableWrap label="Reliability bins">
        <table className="tbl mt-2 min-w-[420px] text-[11px]">
          <thead><tr><th scope="col">Bin</th><th scope="col" className="text-right">Model pred / obs (n)</th><th scope="col" className="text-right">Baseline pred / obs (n)</th></tr></thead>
          <tbody>
            {Array.from(new Set(all.map((b) => b.bin[0]))).sort((a, b) => a - b).map((lo) => {
              const m = model.find((b) => b.bin[0] === lo), bl = baseline.find((b) => b.bin[0] === lo);
              const cell = (b?: ReliabilityBin) => (b ? `${pct(100 * b.mean_predicted, 1)} / ${pct(100 * b.observed_rate, 1)} (${b.count})` : "—");
              return <tr key={lo}><td className="num">{Math.round(100 * lo)}–{Math.round(100 * (m ?? bl)!.bin[1])}%</td><td className="num text-right">{cell(m)}</td><td className="num text-right">{cell(bl)}</td></tr>;
            })}
          </tbody>
        </table>
      </TableWrap>
    </div>
  );
}

export function ImportanceChart({ items }: { items: { feature: string; brier_increase: number }[] }) {
  const max = Math.max(1e-9, ...items.map((i) => Math.abs(i.brier_increase)));
  return (
    <ul className="space-y-1">
      {items.map((i) => (
        <li key={i.feature} className="grid grid-cols-[7.5rem_1fr_4rem] items-center gap-2 text-xs sm:grid-cols-[10rem_1fr_4.5rem]">
          <span className="truncate font-mono text-muted" title={i.feature}>{i.feature}</span>
          <span className="h-2 overflow-hidden rounded bg-edge">
            <span className={cx("block h-full rounded", i.brier_increase >= 0 ? "bg-accent" : "bg-down")} style={{ width: `${(100 * Math.abs(i.brier_increase)) / max}%` }} />
          </span>
          <span className={cx("num text-right", i.brier_increase < 0 && "text-down")}>{i.brier_increase >= 0 ? "+" : ""}{num(i.brier_increase, 4)}</span>
        </li>
      ))}
    </ul>
  );
}

export function MlRegimeCard({ r }: { r: MlRegime | null | undefined }) {
  if (!r || !r.available) {
    return <Card title="ML regime"><p className="text-sm text-muted">No ML regime computed for this market yet.</p></Card>;
  }
  const probs = Object.entries(r.state_probabilities ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <Card title="ML regime (context only)" right={<span className="text-[11px] text-muted">as of {r.as_of}</span>}>
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={/selloff|down/i.test(r.state ?? "") ? "red" : /volatile/i.test(r.state ?? "") ? "amber" : "green"}>{r.state}</Pill>
        <span className="text-xs text-muted">confidence <span className="num text-ink">{pct(100 * (r.confidence ?? 0), 1)}</span></span>
        {r.agrees_with_rule_based != null && (
          <Pill tone={r.agrees_with_rule_based ? "green" : "amber"}>{r.agrees_with_rule_based ? "agrees" : "differs"} with rule-based: {r.rule_based_regime ?? "—"}</Pill>
        )}
      </div>
      <ul className="mt-3 space-y-1.5">
        {probs.map(([k, v]) => (
          <li key={k} className="grid grid-cols-[8rem_1fr_3.5rem] items-center gap-2 text-xs">
            <span className="truncate text-muted">{k}</span>
            <Bar value={100 * v} tone="blue" />
            <span className="num text-right">{pct(100 * v, 1)}</span>
          </li>
        ))}
      </ul>
      {r.states && r.states.length > 0 && (
        <TableWrap label="ML regime states">
          <table className="tbl mt-3 min-w-[420px] text-[11px]">
            <thead><tr><th scope="col">State</th><th scope="col" className="text-right">20d return</th><th scope="col" className="text-right">20d vol</th><th scope="col" className="text-right">vs 200 EMA</th><th scope="col" className="text-right">Share of history</th></tr></thead>
            <tbody>
              {r.states.map((s, i) => (
                <tr key={`${s.name}-${i}`} className={cx(s.name === r.state && "bg-panel2")}>
                  <td>{s.name}</td><td className="num text-right">{pct(s.mean_ret20_pct, 2)}</td><td className="num text-right">{pct(s.mean_vol20_pct, 1)}</td>
                  <td className="num text-right">{pct(s.mean_dist200_pct, 2)}</td><td className="num text-right">{pct(s.share_of_history_pct, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {r.recent_transitions && r.recent_transitions.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Recent transitions</p>
          <ol className="mt-1 flex flex-wrap gap-1 text-[11px]">
            {r.recent_transitions.slice(-8).map((t, i) => <li key={`${t.date}-${i}`} className="rounded border border-edge px-1.5 py-0.5"><span className="num text-muted">{t.date}</span> → {t.state}</li>)}
          </ol>
        </div>
      )}
      <p className="mt-2 text-[11px] text-muted">{r.method}</p>
      <p className="mt-1 rounded border border-blue-900 bg-blue-950/30 px-2 py-1 text-[11px] text-blue-100">ⓘ {r.note}</p>
    </Card>
  );
}
