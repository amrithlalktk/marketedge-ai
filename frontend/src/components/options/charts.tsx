"use client";

import { useEffect, useRef, useState } from "react";
import { compact, num } from "@/lib/format";
import type { OiProfileRow } from "@/lib/types";
import { Segmented } from "../ui";

/** Measures the container so SVG user units equal CSS pixels (text stays legible on phones). */
function useWidth(initial = 640) {
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

/** Payoff at expiry: profit region green, loss region red, zero line, spot and breakeven markers. */
export function PayoffChart(props: { curve: [number, number][]; spot: number; breakevens?: number[]; height?: number; label?: string }) {
  const [zoom, setZoom] = useState<"15" | "7" | "4">("7");
  const z = Number(zoom) / 100;
  const pts = props.curve.filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y) && Math.abs(x / props.spot - 1) <= z + 1e-9);
  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-1 flex justify-end">
        <Segmented<"15" | "7" | "4"> label="Payoff chart range" value={zoom} onChange={setZoom} options={[{ value: "4", label: "±4%" }, { value: "7", label: "±7%" }, { value: "15", label: "±15%" }]} />
      </div>
      {pts.length < 2 ? <p className="text-sm text-muted">No payoff curve available.</p> : <PayoffSvg {...props} curve={pts} />}
    </div>
  );
}

function PayoffSvg({ curve: pts, spot, breakevens = [], height = 260, label = "Payoff at expiry" }: { curve: [number, number][]; spot: number; breakevens?: number[]; height?: number; label?: string }) {
  const [box, W] = useWidth();
  const H = height;
  const pad = { l: 56, r: 10, t: 12, b: 26 };
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys, 0), y1 = Math.max(...ys, 0);
  if (y1 - y0 < 1) { y0 -= 1; y1 += 1; }
  const yPad = (y1 - y0) * 0.08;
  y0 -= yPad; y1 += yPad;
  const sx = (x: number) => pad.l + ((x - x0) / (x1 - x0 || 1)) * (W - pad.l - pad.r);
  const sy = (y: number) => pad.t + (1 - (y - y0) / (y1 - y0)) * (H - pad.t - pad.b);
  const zero = sy(0);
  const line = pts.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ");
  const area = `M${sx(pts[0][0]).toFixed(1)},${zero.toFixed(1)} L${line.replace(/ /g, " L")} L${sx(pts[pts.length - 1][0]).toFixed(1)},${zero.toFixed(1)} Z`;
  const id = `pf${Math.round(x0)}${Math.round(y1)}${pts.length}`;
  const ticksX = [x0, (x0 + spot) / 2, spot, (spot + x1) / 2, x1];
  const ticksY = [y1 - yPad, 0, y0 + yPad];
  return (
    <div ref={box}>
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" role="img" aria-label={`${label}. Spot ${num(spot)}. Breakevens ${breakevens.map((b) => num(b, 0)).join(", ") || "none"}.`}>
      <defs>
        <clipPath id={`${id}-up`}><rect x={0} y={0} width={W} height={Math.max(zero, 0)} /></clipPath>
        <clipPath id={`${id}-dn`}><rect x={0} y={zero} width={W} height={Math.max(H - zero, 0)} /></clipPath>
      </defs>
      <rect x={pad.l} y={pad.t} width={W - pad.l - pad.r} height={H - pad.t - pad.b} fill="#0e141b" />
      {ticksY.map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={W - pad.r} y1={sy(t)} y2={sy(t)} stroke={t === 0 ? "#64748b" : "#1c2733"} strokeWidth={t === 0 ? 1.2 : 1} />
          <text x={pad.l - 4} y={sy(t) + 3} textAnchor="end" fontSize="10" fill="#8b9bb0">{t === 0 ? "0" : compact(t)}</text>
        </g>
      ))}
      <path d={area} fill="rgba(34,197,94,0.22)" clipPath={`url(#${id}-up)`} />
      <path d={area} fill="rgba(240,82,82,0.22)" clipPath={`url(#${id}-dn)`} />
      <polyline points={line} fill="none" stroke="#22c55e" strokeWidth="2" clipPath={`url(#${id}-up)`} />
      <polyline points={line} fill="none" stroke="#f05252" strokeWidth="2" clipPath={`url(#${id}-dn)`} />
      {breakevens.filter((b) => b >= x0 && b <= x1).map((b) => (
        <g key={b}>
          <line x1={sx(b)} x2={sx(b)} y1={pad.t} y2={H - pad.b} stroke="#f5a524" strokeDasharray="3 3" />
          <text x={sx(b)} y={pad.t + 10 + (breakevens.indexOf(b) % 2) * 12} textAnchor={breakevens.indexOf(b) % 2 ? "start" : "end"} dx={breakevens.indexOf(b) % 2 ? 3 : -3} fontSize="10" fill="#f5a524">BE {num(b, 0)}</text>
        </g>
      ))}
      {spot >= x0 && spot <= x1 && (
        <g>
          <line x1={sx(spot)} x2={sx(spot)} y1={pad.t} y2={H - pad.b} stroke="#60a5fa" strokeWidth="1.5" />
          <text x={sx(spot)} y={H - pad.b - 4} textAnchor="middle" fontSize="10" fill="#60a5fa">Spot {num(spot, 0)}</text>
        </g>
      )}
      {ticksX.map((t, i) => (
        <text key={i} x={sx(t)} y={H - 8} textAnchor={i === 0 ? "start" : i === ticksX.length - 1 ? "end" : "middle"} fontSize="10" fill="#8b9bb0">{W < 420 && (i === 1 || i === 3) ? "" : num(t, 0)}</text>
      ))}
    </svg>
    </div>
  );
}

/** Butterfly OI profile: puts to the left, calls to the right of each strike. */
export function OiProfileChart({ rows, spot, maxPain }: { rows: OiProfileRow[]; spot: number; maxPain?: number | null }) {
  const [mode, setMode] = useState<"oi" | "chg">("oi");
  const [span, setSpan] = useState<"10" | "20" | "all">("10");
  const [box, W] = useWidth();
  if (rows.length === 0) return <p className="text-sm text-muted">No OI profile.</p>;
  const sorted = [...rows].sort((a, b) => b.strike - a.strike);
  const atm = sorted.reduce((best, r, i) => (Math.abs(r.strike - spot) < Math.abs(sorted[best].strike - spot) ? i : best), 0);
  const view = span === "all" ? sorted : sorted.slice(Math.max(0, atm - Number(span)), atm + Number(span) + 1);
  const val = (r: OiProfileRow, side: "call" | "put") => (mode === "oi" ? (side === "call" ? r.call_oi : r.put_oi) : side === "call" ? r.call_oi_change : r.put_oi_change);
  const max = Math.max(1, ...view.flatMap((r) => [Math.abs(val(r, "call")), Math.abs(val(r, "put"))]));
  const rowH = 15, mid = 52, half = (W - mid) / 2, H = view.length * rowH + 20;
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Segmented<"oi" | "chg"> label="OI measure" value={mode} onChange={setMode} options={[{ value: "oi", label: "Open interest" }, { value: "chg", label: "Change in OI" }]} />
        <Segmented<"10" | "20" | "all"> label="Strike range" value={span} onChange={setSpan} options={[{ value: "10", label: "±10" }, { value: "20", label: "±20" }, { value: "all", label: "All" }]} />
      </div>
      <div ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="block max-w-full" role="img" aria-label={`Open interest profile by strike (${mode === "oi" ? "total OI" : "change in OI"}): puts left, calls right`}>
        <text x={half - 4} y={11} textAnchor="end" fontSize="10" fill="#22c55e">◀ PUT {mode === "oi" ? "OI" : "ΔOI"}</text>
        <text x={half + mid + 4} y={11} fontSize="10" fill="#f05252">CALL {mode === "oi" ? "OI" : "ΔOI"} ▶</text>
        {view.map((r, i) => {
          const y = 16 + i * rowH;
          const p = val(r, "put"), c = val(r, "call");
          const pw = (Math.abs(p) / max) * (half - 4), cw = (Math.abs(c) / max) * (half - 4);
          const isAtm = r.strike === sorted[atm].strike;
          return (
            <g key={r.strike}>
              {isAtm && <rect x={0} y={y - 1} width={W} height={rowH} fill="rgba(59,130,246,0.12)" />}
              <rect x={half - pw} y={y + 1} width={pw} height={rowH - 4} fill={p < 0 ? "rgba(34,197,94,0.35)" : "#22c55e"} rx="1">
                <title>{`${r.strike} PE ${mode === "oi" ? "OI" : "ΔOI"} ${compact(p)}`}</title>
              </rect>
              <rect x={half + mid} y={y + 1} width={cw} height={rowH - 4} fill={c < 0 ? "rgba(240,82,82,0.35)" : "#f05252"} rx="1">
                <title>{`${r.strike} CE ${mode === "oi" ? "OI" : "ΔOI"} ${compact(c)}`}</title>
              </rect>
              <text x={half + mid / 2} y={y + rowH - 4} textAnchor="middle" fontSize="10" fill={isAtm ? "#93c5fd" : r.strike === maxPain ? "#f5a524" : "#cbd5e1"} fontFamily="ui-monospace, monospace">
                {r.strike}
              </text>
            </g>
          );
        })}
      </svg>
      </div>
      <p className="mt-1 text-[11px] text-muted">
        Blue row = ATM (spot {num(spot)}){maxPain ? <> · amber strike = max pain {num(maxPain, 0)}</> : null}
        {mode === "chg" && " · faded bars = OI reduced"}
      </p>
    </div>
  );
}

/** ATM IV by expiry. */
export function TermStructureChart({ points }: { points: { expiry: string; dte: number; atm_iv_pct: number | null }[] }) {
  const p = points.filter((x) => x.atm_iv_pct != null) as { expiry: string; dte: number; atm_iv_pct: number }[];
  if (p.length < 2) return <p className="text-xs text-muted">Term structure unavailable.</p>;
  const w = 300, h = 90, pl = 30, pr = 8, pt = 10, pb = 20;
  const lo = Math.min(...p.map((x) => x.atm_iv_pct)) - 0.5, hi = Math.max(...p.map((x) => x.atm_iv_pct)) + 0.5;
  const dmax = Math.max(...p.map((x) => x.dte)), dmin = Math.min(...p.map((x) => x.dte));
  const sx = (d: number) => pl + ((d - dmin) / (dmax - dmin || 1)) * (w - pl - pr);
  const sy = (v: number) => pt + (1 - (v - lo) / (hi - lo)) * (h - pt - pb);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="block h-auto w-full max-w-md" role="img" aria-label={`ATM IV term structure: ${p.map((x) => `${x.dte}d ${x.atm_iv_pct}%`).join(", ")}`}>
      <polyline fill="none" stroke="#60a5fa" strokeWidth="1.5" points={p.map((x) => `${sx(x.dte)},${sy(x.atm_iv_pct)}`).join(" ")} />
      {p.map((x) => (
        <g key={x.expiry}>
          <circle cx={sx(x.dte)} cy={sy(x.atm_iv_pct)} r="2.5" fill="#60a5fa" />
          <text x={sx(x.dte)} y={h - 6} textAnchor="middle" fontSize="9" fill="#8b9bb0">{x.dte}d</text>
          <text x={sx(x.dte)} y={sy(x.atm_iv_pct) - 5} textAnchor="middle" fontSize="9" fill="#cbd5e1">{x.atm_iv_pct.toFixed(1)}</text>
        </g>
      ))}
      <text x={2} y={pt + 4} fontSize="9" fill="#8b9bb0">IV%</text>
    </svg>
  );
}
