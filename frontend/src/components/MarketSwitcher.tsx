"use client";

import { enabledMarkets, useMarket, type MarketId } from "@/lib/market";
import { cx } from "./ui";

/** Market selector bound to `?market=`; a select on phones, a segmented control from `sm` up. */
export function MarketSwitcher({ className, only }: { className?: string; only?: MarketId[] }) {
  const [market, setMarket] = useMarket();
  const opts = enabledMarkets().filter((m) => !only || only.includes(m.id));
  if (opts.length < 2) return null;
  return (
    <div className={cx("min-w-0", className)}>
      <label className="sr-only" htmlFor="mkt-sel">Market</label>
      <select id="mkt-sel" className="input w-auto sm:hidden" value={market} onChange={(e) => setMarket(e.target.value as MarketId)}>
        {opts.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
      </select>
      <div role="radiogroup" aria-label="Market" className="hidden max-w-full flex-wrap rounded-md border border-edge bg-panel2 p-0.5 sm:inline-flex">
        {opts.map((m) => (
          <button key={m.id} type="button" role="radio" aria-checked={market === m.id} onClick={() => setMarket(m.id)}
            className={cx("rounded px-2.5 py-1 text-xs font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-accent", market === m.id ? "bg-accent text-white" : "text-muted hover:text-ink")}>
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}
