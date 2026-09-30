"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { WATCHLIST_TAGS } from "@/lib/constants";
import type { Watchlist } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { InlineError } from "./ui";

export function AddToWatchlist({ symbol }: { symbol: string }) {
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const lists = useApi<{ items: Watchlist[] }>(() => api.watchlists.list(), [open], open && can("watchlists:write"));
  const [wid, setWid] = useState<string>("");
  const [tags, setTags] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  if (!can("watchlists:write")) return null;
  const items = lists.data?.items ?? [];
  const chosen = wid || (items[0] ? String(items[0].id) : "");
  const already = items.find((w) => String(w.id) === chosen)?.items.some((i) => i.symbol === symbol);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMsg(null);
    try {
      let target = Number(chosen);
      if (!target) {
        const wl = await api.watchlists.create("My watchlist");
        target = wl.id;
      }
      await api.watchlists.addItem(target, symbol, tags, note);
      setMsg(`${symbol} added.`);
      lists.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add");
    }
  }

  return (
    <div className="relative">
      <button type="button" className="btn-ghost" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        ☆ Add to watchlist
      </button>
      {open && (
        <form id={id} onSubmit={add} className="absolute right-0 z-30 mt-2 w-[min(20rem,calc(100vw-2rem))] space-y-2 rounded-lg border border-edge bg-panel p-3 shadow-xl">
          {lists.loading ? (
            <p className="text-xs text-muted">Loading watchlists…</p>
          ) : items.length === 0 ? (
            <p className="text-xs text-muted">No watchlists yet — a list called “My watchlist” will be created.</p>
          ) : (
            <div>
              <label className="label" htmlFor={`${id}-wl`}>Watchlist</label>
              <select id={`${id}-wl`} className="input" value={chosen} onChange={(e) => setWid(e.target.value)}>
                {items.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.items.length})</option>)}
              </select>
            </div>
          )}
          <fieldset>
            <legend className="label">Tags</legend>
            <div className="flex flex-wrap gap-1">
              {WATCHLIST_TAGS.map((t) => (
                <label key={t} className="inline-flex items-center gap-1 rounded border border-edge px-1.5 py-0.5 text-xs">
                  <input type="checkbox" checked={tags.includes(t)} onChange={(e) => setTags((p) => (e.target.checked ? [...p, t] : p.filter((x) => x !== t)))} />
                  {t}
                </label>
              ))}
            </div>
          </fieldset>
          <div>
            <label className="label" htmlFor={`${id}-note`}>Note</label>
            <input id={`${id}-note`} className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <button className="btn-primary w-full" disabled={already}>{already ? "Already in this list" : "Add"}</button>
          {msg && <p className="text-xs text-green-300" role="status">{msg} <Link href="/watchlists" className="link">Open watchlists</Link></p>}
          <InlineError error={error} />
        </form>
      )}
    </div>
  );
}
