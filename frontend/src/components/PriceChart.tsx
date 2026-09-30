"use client";

import { useEffect, useRef } from "react";
import type { IChartApi, ISeriesApi, LineData, HistogramData, Time, WhitespaceData, DeepPartial, ChartOptions } from "lightweight-charts";
import { pxDigits } from "@/lib/format";
import type { Candles } from "@/lib/types";

export type IndicatorKey = "ema20" | "ema50" | "ema200" | "sma50" | "sma200" | "bb" | "vwap20" | "supertrend" | "levels" | "patterns" | "volume" | "rsi" | "macd";

export const INDICATORS: { key: IndicatorKey; label: string; color: string }[] = [
  { key: "ema20", label: "EMA 20", color: "#f5a524" },
  { key: "ema50", label: "EMA 50", color: "#3b82f6" },
  { key: "ema200", label: "EMA 200", color: "#a855f7" },
  { key: "sma50", label: "SMA 50", color: "#14b8a6" },
  { key: "sma200", label: "SMA 200", color: "#ec4899" },
  { key: "bb", label: "Bollinger", color: "#64748b" },
  { key: "vwap20", label: "VWAP 20", color: "#eab308" },
  { key: "supertrend", label: "Supertrend", color: "#22c55e" },
  { key: "levels", label: "S/R levels", color: "#94a3b8" },
  { key: "patterns", label: "Patterns", color: "#f5a524" },
  { key: "volume", label: "Volume", color: "#475569" },
  { key: "rsi", label: "RSI pane", color: "#60a5fa" },
  { key: "macd", label: "MACD pane", color: "#f97316" },
];

export interface TradeLines {
  entry?: [number, number];
  stop?: number;
  targets?: number[];
}

type Num = number | null;

function col(c: Candles, k: string): Num[] {
  const v = c.bars[k];
  return Array.isArray(v) ? (v as Num[]) : [];
}

function line(t: string[], vals: Num[], color?: (i: number) => string | undefined): (LineData<Time> | WhitespaceData<Time>)[] {
  return t.map((time, i) => {
    const v = vals[i];
    if (v == null || !Number.isFinite(v)) return { time };
    const c = color?.(i);
    return c ? { time, value: v, color: c } : { time, value: v };
  });
}

const UP = "#22c55e";
const DOWN = "#f05252";

export function PriceChart({ candles, enabled, trade, height = 420, fx = false }: { candles: Candles; enabled: Set<IndicatorKey>; trade?: TradeLines; height?: number; fx?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const on = [...enabled].sort().join(",");

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let chart: IChartApi | null = null;
    let disposed = false;

    (async () => {
      const lc = await import("lightweight-charts");
      if (disposed || !ref.current) return;
      const opts: DeepPartial<ChartOptions> = {
        autoSize: true,
        layout: { background: { color: "#111821" }, textColor: "#8b9bb0", fontSize: 11, panes: { separatorColor: "#223040", enableResize: true } },
        grid: { vertLines: { color: "#18222e" }, horzLines: { color: "#18222e" } },
        rightPriceScale: { borderColor: "#223040" },
        timeScale: { borderColor: "#223040", rightOffset: 4 },
        crosshair: { mode: lc.CrosshairMode.Normal },
        localization: { locale: "en-IN" },
      };
      chart = lc.createChart(el, opts);
      const t = candles.bars.t;
      const o = col(candles, "open"), h = col(candles, "high"), l = col(candles, "low"), c = col(candles, "close"), v = col(candles, "volume");

      const lastC = [...c].reverse().find((x) => x != null && x !== 0) ?? 1;
      const dec = (candles.bars as unknown as { price_decimals?: number }).price_decimals;
      const digits = typeof dec === "number" && dec >= 0 && dec <= 12 ? dec : pxDigits(lastC, fx);
      const pf = { type: "price" as const, precision: digits, minMove: Math.pow(10, -digits) };
      const price: ISeriesApi<"Candlestick"> = chart.addSeries(lc.CandlestickSeries, {
        priceFormat: pf,
        upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN, wickUpColor: UP, wickDownColor: DOWN,
      });
      price.setData(
        t.map((time, i) =>
          o[i] == null || h[i] == null || l[i] == null || c[i] == null ? { time } : { time, open: o[i] as number, high: h[i] as number, low: l[i] as number, close: c[i] as number },
        ),
      );

      const addLine = (key: string, color: string, width: 1 | 2 = 1, pane = 0, style = lc.LineStyle.Solid, perPoint?: (i: number) => string | undefined) => {
        const s = chart!.addSeries(lc.LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...(pane === 0 ? { priceFormat: pf } : {}) }, pane);
        s.setData(line(t, col(candles, key), perPoint));
        return s;
      };

      if (enabled.has("volume")) {
        const vol = chart.addSeries(lc.HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
        vol.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        // lightweight-charts rejects |values| > ~9e13 (huge token-denominated volumes); rescale by a power of ten.
        const vmax = Math.max(0, ...v.map((x) => (x == null ? 0 : Math.abs(x))));
        const vdiv = vmax > 9e12 ? Math.pow(10, Math.ceil(Math.log10(vmax / 9e12))) : 1;
        vol.setData(
          t.map((time, i): HistogramData<Time> | WhitespaceData<Time> =>
            v[i] == null ? { time } : { time, value: (v[i] as number) / vdiv, color: (c[i] ?? 0) >= (o[i] ?? 0) ? "rgba(34,197,94,0.35)" : "rgba(240,82,82,0.35)" },
          ),
        );
      }
      for (const k of ["ema20", "ema50", "ema200", "sma50", "sma200", "vwap20"] as const) {
        if (enabled.has(k)) addLine(k, INDICATORS.find((x) => x.key === k)!.color, k === "ema200" || k === "sma200" ? 2 : 1, 0, k === "vwap20" ? lc.LineStyle.Dotted : lc.LineStyle.Solid);
      }
      if (enabled.has("bb")) {
        addLine("bb_upper", "#64748b", 1, 0, lc.LineStyle.Dashed);
        addLine("bb_mid", "#475569", 1, 0, lc.LineStyle.Dotted);
        addLine("bb_lower", "#64748b", 1, 0, lc.LineStyle.Dashed);
      }
      if (enabled.has("supertrend")) {
        const dir = col(candles, "supertrend_dir");
        addLine("supertrend", UP, 2, 0, lc.LineStyle.Solid, (i) => ((dir[i] ?? 0) >= 0 ? UP : DOWN));
      }
      if (enabled.has("patterns")) {
        // Geometric pattern bounds (triangle/rectangle) and cup-and-handle rim; null outside a detected pattern.
        if (col(candles, "geo_upper").some((x) => x != null)) addLine("geo_upper", "#f5a524", 2, 0, lc.LineStyle.Dashed);
        if (col(candles, "geo_lower").some((x) => x != null)) addLine("geo_lower", "#f5a524", 2, 0, lc.LineStyle.Dashed);
        if (col(candles, "cup_rim").some((x) => x != null)) addLine("cup_rim", "#c084fc", 2, 0, lc.LineStyle.Dashed);
      }
      if (enabled.has("levels")) {
        for (const lv of candles.levels) {
          price.createPriceLine({
            price: lv.price, color: lv.kind === "support" ? "rgba(34,197,94,0.7)" : "rgba(240,82,82,0.7)", lineWidth: 1, lineStyle: lc.LineStyle.Dashed,
            axisLabelVisible: true, title: `${lv.kind === "support" ? "S" : "R"} ×${lv.touches}`,
          });
        }
      }
      if (trade) {
        if (trade.entry) {
          price.createPriceLine({ price: trade.entry[0], color: "#3b82f6", lineWidth: 1, lineStyle: lc.LineStyle.Dotted, axisLabelVisible: false, title: "Entry lo" });
          price.createPriceLine({ price: trade.entry[1], color: "#3b82f6", lineWidth: 1, lineStyle: lc.LineStyle.Dotted, axisLabelVisible: true, title: "Entry" });
        }
        if (trade.stop != null) price.createPriceLine({ price: trade.stop, color: DOWN, lineWidth: 2, lineStyle: lc.LineStyle.Solid, axisLabelVisible: true, title: "Stop" });
        trade.targets?.slice(0, 2).forEach((tg, i) => price.createPriceLine({ price: tg, color: UP, lineWidth: 2, lineStyle: lc.LineStyle.Solid, axisLabelVisible: true, title: `T${i + 1}` }));
      }

      let pane = 1;
      if (enabled.has("rsi")) {
        const rsi = addLine("rsi", "#60a5fa", 1, pane);
        rsi.createPriceLine({ price: 70, color: "rgba(240,82,82,0.5)", lineWidth: 1, lineStyle: lc.LineStyle.Dashed, axisLabelVisible: false, title: "70" });
        rsi.createPriceLine({ price: 30, color: "rgba(34,197,94,0.5)", lineWidth: 1, lineStyle: lc.LineStyle.Dashed, axisLabelVisible: false, title: "30" });
        pane++;
      }
      if (enabled.has("macd")) {
        const hist = col(candles, "macd_hist");
        const hs = chart.addSeries(lc.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane);
        hs.setData(t.map((time, i) => (hist[i] == null ? { time } : { time, value: hist[i] as number, color: (hist[i] as number) >= 0 ? "rgba(34,197,94,0.6)" : "rgba(240,82,82,0.6)" })));
        addLine("macd", "#f97316", 1, pane);
        addLine("macd_signal", "#60a5fa", 1, pane);
        pane++;
      }
      const panes = chart.panes();
      for (let i = 1; i < panes.length; i++) panes[i].setHeight(110);
      chart.timeScale().fitContent();
    })();

    return () => {
      disposed = true;
      chart?.remove();
      chart = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, on, fx, trade?.stop, trade?.entry?.[0], trade?.entry?.[1], trade?.targets?.[0], trade?.targets?.[1]]);

  const extra = (enabled.has("rsi") ? 110 : 0) + (enabled.has("macd") ? 110 : 0);
  return <div ref={ref} style={{ height: height + extra }} className="w-full overflow-hidden rounded-md border border-edge" role="img" aria-label={`Candlestick chart for ${candles.symbol} (${candles.interval})`} />;
}
