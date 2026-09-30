"use client";

import { useEffect, useRef } from "react";
import type { IChartApi } from "lightweight-charts";

/** Equity curve (index, starts at 100) rendered with lightweight-charts. */
export function EquityChart({ points, height = 260 }: { points: [string, number][]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || points.length < 2) return;
    let chart: IChartApi | null = null;
    let disposed = false;
    (async () => {
      const lc = await import("lightweight-charts");
      if (disposed) return;
      chart = lc.createChart(el, {
        autoSize: true,
        layout: { background: { color: "#111821" }, textColor: "#8b9bb0", fontSize: 11 },
        grid: { vertLines: { color: "#18222e" }, horzLines: { color: "#18222e" } },
        rightPriceScale: { borderColor: "#223040" },
        timeScale: { borderColor: "#223040" },
        localization: { locale: "en-IN" },
      });
      const up = points[points.length - 1][1] >= points[0][1];
      const s = chart.addSeries(lc.AreaSeries, {
        lineColor: up ? "#22c55e" : "#f05252",
        topColor: up ? "rgba(34,197,94,0.25)" : "rgba(240,82,82,0.25)",
        bottomColor: "rgba(0,0,0,0)",
        lineWidth: 2,
        priceLineVisible: false,
      });
      // de-duplicate / sort dates defensively (library requires strictly ascending time)
      const seen = new Map<string, number>();
      for (const [d, v] of points) seen.set(d, v);
      s.setData([...seen.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([time, value]) => ({ time, value })));
      s.createPriceLine({ price: 100, color: "#475569", lineWidth: 1, lineStyle: lc.LineStyle.Dashed, axisLabelVisible: false, title: "start" });
      chart.timeScale().fitContent();
    })();
    return () => {
      disposed = true;
      chart?.remove();
    };
  }, [points]);
  if (points.length < 2) return <p className="text-sm text-muted">Not enough trades to draw an equity curve.</p>;
  return <div ref={ref} style={{ height }} className="w-full overflow-hidden rounded-md border border-edge" role="img" aria-label="Backtest equity curve, indexed to 100" />;
}
