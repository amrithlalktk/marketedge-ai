"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

export type MarketId = "NSE" | "CRYPTO";

export const MARKETS: { id: MarketId; label: string; short: string; group: string }[] = [
  { id: "NSE", label: "Indian Stocks", short: "India", group: "INDIA" },
  { id: "CRYPTO", label: "Crypto", short: "Crypto", group: "CRYPTO" },
];

export const MARKET_IDS = MARKETS.map((m) => m.id);
export const isMarket = (v: string | null | undefined): v is MarketId => !!v && (MARKET_IDS as string[]).includes(v);
export const marketLabel = (m: string | null | undefined) => MARKETS.find((x) => x.id === m)?.label ?? m ?? "";

/** Wording for the "sector" dimension per market. */
export function sectorWord(m: MarketId): { plural: string; singular: string } {
  if (m === "CRYPTO") return { plural: "Categories", singular: "Category" };
  return { plural: "Sectors", singular: "Sector" };
}

/**
 * The market used when the URL has no `?market=`. Set once from the backend (`/markets` → `default_market`:
 * the first market on real data, or DEFAULT_MARKET) by <MarketDefaultGate> before any page renders.
 */
let defaultMarket: MarketId = "NSE";
let enabled: MarketId[] | null = null; // null = all markets (until /markets has answered)

/** Markets switched on for this installation (MARKETS_ENABLED), in display order. */
export const enabledMarkets = () => MARKETS.filter((m) => !enabled || enabled.includes(m.id));
export const marketOn = (m: MarketId) => !enabled || enabled.includes(m);
/** Called once with the market ids from GET /markets (MARKETS_ENABLED on the backend). */
export function setEnabledMarkets(ids: string[]) {
  enabled = ids.filter(isMarket);
}
export const getDefaultMarket = () => defaultMarket;
export function setDefaultMarket(m: string | null | undefined) {
  if (isMarket(m)) defaultMarket = m;
}

/**
 * Global market selection kept in the URL (`?market=`), so it survives navigation/reload and is shareable,
 * without browser storage. The default market is omitted from the URL.
 */
export function useMarket(): [MarketId, (m: MarketId) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname() || "/";
  const raw = params.get("market");
  const market: MarketId = isMarket(raw) ? raw : defaultMarket;
  const set = useCallback(
    (m: MarketId) => {
      const p = new URLSearchParams(params.toString());
      if (m === defaultMarket) p.delete("market");
      else p.set("market", m);
      const q = p.toString();
      router.replace(q ? `${path}?${q}` : path, { scroll: false });
    },
    [params, router, path],
  );
  return [market, set];
}

/** Append the current market to an internal link (for nav links that should keep the market). */
export function withMarket(href: string, m: MarketId): string {
  if (m === defaultMarket) return href;
  return `${href}${href.includes("?") ? "&" : "?"}market=${m}`;
}
