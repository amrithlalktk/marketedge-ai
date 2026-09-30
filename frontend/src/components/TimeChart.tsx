"use client";

import { useEffect, useRef } from "react";
import type { IChartApi } from "lightweight-charts";

export interface TimeSeries {
  name: string;
  color: string;
  type?: "line" | "area" | "histogram";
  data: [string, number][];
  scale?: "left" | "right";
  dashed?: boolean;
}

function clean(data: [string, number][]) {
  const m = new Map<string, number>();
  for (const [d, v] of data) if (d && Number.isFinite(v)) m.set(d.slice(0, 10), v);
  return [...m.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([time, value]) => ({ time, value }));
}

/** Multi-series time chart (lightweight-charts) with a small legend. */
export function TimeChart({ series, height = 260, label, percent = false }: { series: TimeSeries[]; height?: number; label: string; percent?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const key = series.map((s) => `${s.name}:${s.data.length}:${s.data[0]?.[1]}:${s.data.at(-1)?.[1]}`).join("|");
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let chart: IChartApi | null = null;
    let disposed = false;
    (async () => {
      const lc = await import("lightweight-charts");
      if (disposed) return;
      const left = series.some((s) => s.scale === "left");
      chart = lc.createChart(el, {
        autoSize: true,
        layout: { background: { color: "#111821" }, textColor: "#8b9bb0", fontSize: 11 },
        grid: { vertLines: { color: "#18222e" }, horzLines: { color: "#18222e" } },
        rightPriceScale: { borderColor: "#223040" },
        leftPriceScale: { visible: left, borderColor: "#223040" },
        timeScale: { borderColor: "#223040" },
        localization: { locale: "en-IN", priceFormatter: percent ? (v: number) => `${v.toFixed(1)}%` : (v: number) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(v) },
      });
      for (const s of series) {
        const common = { priceScaleId: s.scale ?? "right", lastValueVisible: false, priceLineVisible: false };
        const data = clean(s.data);
        if (s.type === "area") {
          chart.addSeries(lc.AreaSeries, { ...common, lineColor: s.color, topColor: `${s.color}55`, bottomColor: `${s.color}08`, lineWidth: 2 }).setData(data);
        } else if (s.type === "histogram") {
          chart.addSeries(lc.HistogramSeries, { ...common, color: `${s.color}88` }).setData(data);
        } else {
          chart.addSeries(lc.LineSeries, { ...common, color: s.color, lineWidth: 2, lineStyle: s.dashed ? lc.LineStyle.Dashed : lc.LineStyle.Solid }).setData(data);
        }
      }
      chart.timeScale().fitContent();
    })();
    return () => {
      disposed = true;
      chart?.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, percent]);
  return (
    <div>
      <div className="mb-1 flex flex-wrap gap-3 text-[11px] text-muted">
        {series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1">
            <span aria-hidden className="inline-block h-0.5 w-4" style={{ background: s.color }} /> {s.name}{s.scale === "left" ? " (left axis)" : ""}
          </span>
        ))}
      </div>
      <div ref={ref} style={{ height }} className="w-full overflow-hidden rounded-md border border-edge" role="img" aria-label={label} />
    </div>
  );
}
