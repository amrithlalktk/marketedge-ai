"use client";

import { useEffect, useId, useState } from "react";
import { api } from "@/lib/api";
import { MARKETS, marketLabel } from "@/lib/market";
import type { Instrument } from "@/lib/types";
import { cx } from "./ui";

/** Cross-market instrument picker: market filter + debounced search, returns the chosen symbol. */
export function InstrumentSearch({ onPick, label = "Add instrument" }: { onPick: (i: Instrument) => void | Promise<void>; label?: string }) {
  const id = useId();
  const [market, setMarket] = useState("");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Instrument[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!q.trim() && !market) {
      setItems([]);
      return;
    }
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        const r = await api.stocks.list({ q: q.trim() || undefined, market: market || undefined, page_size: 12, include_indices: true });
        setItems(r.items);
        setOpen(true);
      } catch {
        setItems([]);
      } finally {
        setBusy(false);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q, market]);

  return (
    <div className="relative">
      <div className="flex flex-wrap gap-2">
        <label className="sr-only" htmlFor={`${id}-m`}>Market filter</label>
        <select id={`${id}-m`} className="input w-auto" value={market} onChange={(e) => setMarket(e.target.value)}>
          <option value="">All markets</option>
          {MARKETS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
        <label className="sr-only" htmlFor={`${id}-q`}>{label}</label>
        <input id={`${id}-q`} className="input min-w-0 flex-1" placeholder="Search symbol or name (e.g. BTC, EURUSD)" value={q} autoComplete="off"
          role="combobox" aria-expanded={open && items.length > 0} aria-controls={`${id}-list`}
          onChange={(e) => setQ(e.target.value)} onFocus={() => items.length && setOpen(true)} onKeyDown={(e) => e.key === "Escape" && setOpen(false)} />
      </div>
      {open && (items.length > 0 || busy) && (
        <ul id={`${id}-list`} role="listbox" className="absolute left-0 right-0 z-30 mt-1 max-h-72 overflow-y-auto rounded-md border border-edge bg-panel shadow-xl">
          {busy && items.length === 0 && <li className="px-3 py-2 text-xs text-muted">Searching…</li>}
          {items.map((i) => (
            <li key={i.symbol} role="option" aria-selected={false}>
              <button type="button" className={cx("flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-panel2 focus:bg-panel2 focus:outline-none")}
                onClick={async () => { setOpen(false); setQ(""); await onPick(i); }}>
                <span className="min-w-0"><span className="block font-mono font-semibold">{i.symbol}</span><span className="block truncate text-xs text-muted">{i.name}</span></span>
                <span className="shrink-0 text-right text-[11px] text-muted">{marketLabel(i.market)}<span className="block">{i.exchange} · {i.currency}</span></span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
