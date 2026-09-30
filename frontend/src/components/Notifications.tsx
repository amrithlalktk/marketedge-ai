"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { localDateTime, relTime } from "@/lib/format";
import type { NotificationItem } from "@/lib/types";
import { CHANNEL_LABELS } from "./ChannelPicker";
import { cx } from "./ui";

export const NOTIF_EVENT = "marketedge:notifications-changed";
export const emitNotificationsChanged = () => window.dispatchEvent(new Event(NOTIF_EVENT));

export function DeliveryChips({ d }: { d: NotificationItem["deliveries"] }) {
  const e = Object.entries(d ?? {});
  if (!e.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {e.map(([c, v]) => (
        <span key={c} title={v.error ? `${v.status}: ${v.error}` : `${v.status}${v.at ? ` at ${v.at}` : ""}`}
          className={cx("badge ring-1", v.status === "sent" || v.status === "delivered" ? "bg-green-950 text-green-300 ring-green-800" : v.status === "failed" ? "bg-red-950 text-red-300 ring-red-800" : "bg-slate-800 text-slate-300 ring-slate-700")}>
          {CHANNEL_LABELS[c] ?? c}: {v.status}
        </span>
      ))}
    </span>
  );
}

export function NotificationRow({ n, onOpen }: { n: NotificationItem; onOpen?: () => void }) {
  const inner = (
    <>
      <span className="flex items-start justify-between gap-2">
        <span className={cx("min-w-0 text-sm", !n.read && "font-semibold text-ink")}>{!n.read && <span className="mr-1 inline-block h-2 w-2 rounded-full bg-accent" aria-label="unread" />}{n.title}</span>
        <time className="shrink-0 text-[10px] text-muted" dateTime={n.created_at} title={localDateTime(n.created_at)}>{relTime(n.created_at)}</time>
      </span>
      {n.body && <span className="mt-0.5 block break-words text-xs text-muted">{n.body}</span>}
      <span className="mt-1 block"><DeliveryChips d={n.deliveries} /></span>
    </>
  );
  // Only follow internal links (never navigate to an arbitrary URL from a notification payload).
  const internal = n.link && n.link.startsWith("/") && !n.link.startsWith("//") ? n.link : null;
  return internal ? <Link href={internal} onClick={onOpen} className="block rounded px-2 py-2 hover:bg-panel2">{inner}</Link> : <div className="px-2 py-2">{inner}</div>;
}

/** Bell with unread badge: polls every 60 s while the tab is visible; optional browser notifications for new items. */
export function NotificationBell() {
  const { status } = useAuth();
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [open, setOpen] = useState(false);
  const lastSeen = useRef<number | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const poll = useCallback(async () => {
    if (document.visibilityState === "hidden") return;
    try {
      const r = await api.notifications.list({ limit: 8 });
      setUnread(r.unread);
      setItems(r.items);
      const maxId = r.items.reduce((m, n) => Math.max(m, n.id), 0);
      if (lastSeen.current != null && typeof Notification !== "undefined" && Notification.permission === "granted") {
        for (const n of r.items.filter((x) => x.id > (lastSeen.current as number) && !x.read).slice(0, 3)) {
          try { new Notification(n.title, { body: n.body, tag: `me-${n.id}` }); } catch { /* ignore */ }
        }
      }
      lastSeen.current = Math.max(lastSeen.current ?? 0, maxId);
    } catch { /* keep the last value */ }
  }, []);

  useEffect(() => {
    if (status !== "authed") return;
    poll();
    const t = setInterval(poll, 60_000);
    const vis = () => document.visibilityState === "visible" && poll();
    document.addEventListener("visibilitychange", vis);
    window.addEventListener(NOTIF_EVENT, poll);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); window.removeEventListener(NOTIF_EVENT, poll); };
  }, [status, poll]);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const k = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [open]);

  if (status !== "authed") return null;
  const canBrowser = typeof Notification !== "undefined" && Notification.permission === "default";
  return (
    <div ref={box} className="relative">
      <button type="button" className="btn-ghost relative px-2 py-1" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} aria-expanded={open} aria-controls="notif-panel" onClick={() => { setOpen((o) => !o); if (!open) poll(); }}>
        <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.7 21a2 2 0 0 1-3.4 0" /></svg>
        {unread > 0 && <span className="absolute -right-1 -top-1 min-w-[1.1rem] rounded-full bg-down px-1 text-center text-[10px] font-bold leading-4 text-white">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div id="notif-panel" role="dialog" aria-label="Notifications" className="absolute right-0 z-50 mt-2 w-[min(22rem,calc(100vw-1.5rem))] rounded-lg border border-edge bg-panel shadow-2xl">
          <div className="flex items-center justify-between border-b border-edge px-3 py-2">
            <span className="text-sm font-semibold">Notifications {unread > 0 && <span className="text-xs text-muted">({unread} unread)</span>}</span>
            <button type="button" className="text-xs text-accent hover:underline disabled:opacity-50" disabled={!unread} onClick={async () => { await api.notifications.read(); poll(); emitNotificationsChanged(); }}>Mark all read</button>
          </div>
          <div className="max-h-[60vh] divide-y divide-edge overflow-y-auto">
            {items.length === 0 ? <p className="px-3 py-4 text-center text-xs text-muted">No notifications yet.</p> : items.map((n) => <NotificationRow key={n.id} n={n} onOpen={() => { setOpen(false); if (!n.read) api.notifications.read([n.id]).then(poll); }} />)}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-edge px-3 py-2 text-xs">
            <Link href="/notifications" className="link" onClick={() => setOpen(false)}>See all</Link>
            {canBrowser && <button type="button" className="text-accent hover:underline" onClick={() => Notification.requestPermission()}>Enable browser pop-ups</button>}
            <Link href="/account#notifications" className="link" onClick={() => setOpen(false)}>Settings</Link>
          </div>
        </div>
      )}
    </div>
  );
}
