"use client";

import Link from "next/link";
import { useState } from "react";
import { InstrumentSearch } from "@/components/InstrumentSearch";
import { Card, Confirm, DataStamp, DirectionBadge, Disclaimer, EmptyState, ErrorState, InlineError, PageHeader, Pill, Skeleton, StatusBadge, TableWrap, UpgradeNote, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { WATCHLIST_TAGS } from "@/lib/constants";
import { moveClass, num, price, signedPct } from "@/lib/format";
import type { Watchlist, WatchlistItem } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function msg(e: unknown) {
  if (e instanceof ApiError && e.status === 403 && /watchlists/i.test(e.message)) return `${e.message}. Upgrade to Premium for unlimited watchlists.`;
  return e instanceof Error ? e.message : "Request failed";
}

function TagEditor({ item, onSave }: { item: WatchlistItem; onSave: (tags: string[], note: string) => Promise<void> }) {
  const [tags, setTags] = useState<string[]>(item.tags);
  const [custom, setCustom] = useState("");
  const [note, setNote] = useState(item.note);
  const [busy, setBusy] = useState(false);
  const all = Array.from(new Set([...WATCHLIST_TAGS, ...tags]));
  return (
    <form
      className="space-y-2"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try { await onSave(tags, note); } finally { setBusy(false); }
      }}
    >
      <fieldset>
        <legend className="label">Tags for {item.symbol}</legend>
        <div className="flex flex-wrap gap-1">
          {all.map((t) => (
            <label key={t} className="inline-flex items-center gap-1 rounded border border-edge px-1.5 py-0.5 text-xs">
              <input type="checkbox" checked={tags.includes(t)} onChange={(e) => setTags((p) => (e.target.checked ? [...p, t] : p.filter((x) => x !== t)))} /> {t}
            </label>
          ))}
        </div>
        <div className="mt-2 flex gap-2">
          <label className="sr-only" htmlFor={`ct-${item.id}`}>Custom tag</label>
          <input id={`ct-${item.id}`} className="input py-1 text-xs" placeholder="Custom tag" maxLength={32} value={custom} onChange={(e) => setCustom(e.target.value)} />
          <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={!custom.trim() || tags.length >= 10} onClick={() => { setTags((p) => Array.from(new Set([...p, custom.trim()]))); setCustom(""); }}>Add tag</button>
        </div>
      </fieldset>
      <div>
        <label className="label" htmlFor={`nt-${item.id}`}>Note</label>
        <textarea id={`nt-${item.id}`} className="input min-h-[60px]" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <button className="btn-primary px-3 py-1 text-xs" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
    </form>
  );
}

function ItemRow({ wl, it, onChange }: { wl: Watchlist; it: WatchlistItem; onChange: (w: Watchlist | null, err?: string) => void }) {
  const [editing, setEditing] = useState(false);
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/stocks/${encodeURIComponent(it.symbol)}`} className="link font-mono font-semibold">{it.symbol}</Link>
          {it.is_sample && <span className="ml-1"><Pill tone="amber">SAMPLE</Pill></span>}
          <p className="truncate text-xs text-muted">{it.name}</p>
          {it.tags.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{it.tags.map((t) => <Pill key={t} tone="blue">{t}</Pill>)}</div>}
          {it.note && <p className="mt-1 text-xs text-muted">“{it.note}”</p>}
        </div>
        <div className="text-right">
          {it.quote ? (
            <>
              <div className="num font-semibold">{price(it.quote.price, it.currency ?? "INR")}</div>
              <div className={cx("num text-xs", moveClass(it.quote.change_1d_pct))}>{signedPct(it.quote.change_1d_pct)}</div>
              <DataStamp asOf={it.quote.as_of} />
            </>
          ) : <span className="text-xs text-muted">No quote</span>}
        </div>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
        {it.setups.length === 0 ? <span className="text-muted">No active setup</span> : it.setups.map((s) => (
          <Link key={s.id} href={`/setups/${s.id}`} className="inline-flex items-center gap-1 rounded border border-edge px-1.5 py-0.5 hover:bg-panel2">
            <StatusBadge s={s.status} /> <DirectionBadge d={s.direction} /> {s.strategy} · <span className="num">{num(s.score, 0)}</span>
          </Link>
        ))}
        <span className="ml-auto flex gap-1">
          <button type="button" className="btn-ghost px-2 py-1 text-xs" aria-expanded={editing} onClick={() => setEditing((e) => !e)}>{editing ? "Close" : "Tags & note"}</button>
          <Confirm label="Remove" className="btn-ghost px-2 py-1 text-xs" onConfirm={async () => {
            try { await api.watchlists.removeItem(wl.id, it.id); onChange({ ...wl, items: wl.items.filter((x) => x.id !== it.id) }); } catch (e) { onChange(null, msg(e)); }
          }}>Remove</Confirm>
        </span>
      </div>
      {editing && (
        <div className="mt-2 rounded border border-edge bg-panel2/40 p-2">
          <TagEditor item={it} onSave={async (tags, note) => {
            try { onChange(await api.watchlists.patchItem(wl.id, it.id, { tags, note })); setEditing(false); } catch (e) { onChange(null, msg(e)); }
          }} />
        </div>
      )}
    </li>
  );
}

function WatchlistCard({ wl, onChange, onDelete }: { wl: Watchlist; onChange: (w: Watchlist) => void; onDelete: () => void }) {
  const [sym, setSym] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");
  const tags = Array.from(new Set(wl.items.flatMap((i) => i.tags)));
  const items = filter ? wl.items.filter((i) => i.tags.includes(filter)) : wl.items;
  return (
    <Card
      title={<span className="normal-case tracking-normal text-ink">{wl.name} <span className="text-muted">({wl.items.length})</span></span>}
      right={<Confirm label="Delete list" className="btn-ghost px-2 py-1 text-xs" onConfirm={onDelete}>Delete</Confirm>}
    >
      <InstrumentSearch label={`Add instrument to ${wl.name}`} onPick={async (i) => {
        setError(null);
        try { onChange(await api.watchlists.addItem(wl.id, i.symbol)); } catch (err) { setError(msg(err)); }
      }} />
      <form className="mt-2 flex gap-2" onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try { onChange(await api.watchlists.addItem(wl.id, sym.trim().toUpperCase())); setSym(""); } catch (err) { setError(msg(err)); }
      }}>
        <label className="sr-only" htmlFor={`add-${wl.id}`}>Add exact symbol to {wl.name}</label>
        <input id={`add-${wl.id}`} className="input" placeholder="…or type an exact symbol" maxLength={64} value={sym} onChange={(e) => setSym(e.target.value)} />
        <button className="btn-ghost shrink-0" disabled={!sym.trim()}>Add</button>
      </form>
      <InlineError error={error} />
      {tags.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1 text-xs">
          <span className="text-muted">Filter:</span>
          <button type="button" className={cx("badge ring-1", !filter ? "bg-accent text-white ring-accent" : "ring-edge")} onClick={() => setFilter("")}>All</button>
          {tags.map((t) => <button type="button" key={t} className={cx("badge ring-1", filter === t ? "bg-accent text-white ring-accent" : "ring-edge")} onClick={() => setFilter(t)}>{t}</button>)}
        </div>
      )}
      {items.length === 0 ? <div className="mt-3"><EmptyState title="No instruments yet" /></div> : (
        <TableWrap label={`${wl.name} items`}>
          <ul className="mt-2 divide-y divide-edge">
            {items.map((it) => <ItemRow key={it.id} wl={wl} it={it} onChange={(w, err) => (w ? onChange(w) : setError(err ?? null))} />)}
          </ul>
        </TableWrap>
      )}
    </Card>
  );
}

export default function WatchlistsPage() {
  const { can } = useAuth();
  const allowed = can("watchlists:write");
  const q = useApi<{ items: Watchlist[] }>(() => api.watchlists.list(), [], allowed);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!allowed) return <UpgradeNote feature="Watchlists" />;
  const lists = q.data?.items ?? [];
  const replace = (w: Watchlist) => q.setData({ items: lists.map((x) => (x.id === w.id ? w : x)) });

  return (
    <>
      <PageHeader
        title="Watchlists"
        subtitle={can("watchlists:unlimited") ? "Unlimited watchlists on your plan." : "Standard plan: up to 3 watchlists."}
      />
      <form className="card mb-4 flex flex-wrap items-end gap-2" onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try { const w = await api.watchlists.create(name.trim()); q.setData({ items: [...lists, w] }); setName(""); } catch (err) { setError(msg(err)); }
      }}>
        <div className="min-w-0 flex-1">
          <label className="label" htmlFor="wl-name">New watchlist name</label>
          <input id="wl-name" className="input" maxLength={64} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Breakout candidates" />
        </div>
        <button className="btn-primary" disabled={!name.trim()}>Create</button>
        <div className="w-full"><InlineError error={error} /></div>
      </form>
      {q.error && <ErrorState error={q.error} onRetry={q.reload} what="watchlists" />}
      {q.loading && !q.data && <Skeleton className="h-40" />}
      {q.data && lists.length === 0 && <EmptyState title="No watchlists yet">Create one above, or use “Add to watchlist” on any stock page.</EmptyState>}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {lists.map((w) => (
          <WatchlistCard key={w.id} wl={w} onChange={replace} onDelete={async () => {
            try { await api.watchlists.remove(w.id); q.setData({ items: lists.filter((x) => x.id !== w.id) }); } catch (err) { setError(msg(err)); }
          }} />
        ))}
      </div>
      <Disclaimer />
    </>
  );
}
