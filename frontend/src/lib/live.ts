"use client";

import { useEffect, useState } from "react";
import { api } from "./api";
import { useAuth } from "./auth";
import type { LiveQuotes } from "./types";

const REFRESH_MS = 30_000;

/**
 * Live prices for "MARKET:SYMBOL" items (display only — the analysis stays end-of-day).
 * Refreshes every 30 s while the tab is visible; returns null until the first answer or when nothing is asked.
 */
export function useLiveQuotes(items: string[]): LiveQuotes | null {
  const { status } = useAuth();
  const [data, setData] = useState<LiveQuotes | null>(null);
  const key = [...new Set(items)].sort().join(",");
  useEffect(() => {
    if (status !== "authed" || !key) return;
    let stop = false;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      api.signals.liveQuotes(key.split(",")).then((d) => !stop && setData(d)).catch(() => undefined);
    };
    load();
    const t = setInterval(load, REFRESH_MS);
    document.addEventListener("visibilitychange", load);
    return () => {
      stop = true;
      clearInterval(t);
      document.removeEventListener("visibilitychange", load);
    };
  }, [status, key]);
  return key ? data : null;
}

/** Item key for a quote: an option idea follows NIFTY (its underlying index). */
export function quoteKey(market: string, symbol: string): string {
  return `${market === "NFO" ? "NSE" : market}:${symbol}`;
}
