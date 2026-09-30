"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { NotificationItem, NotificationSettings as NS, TelegramLink } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { CHANNEL_LABELS } from "./ChannelPicker";
import { DeliveryChips, emitNotificationsChanged } from "./Notifications";
import { Card, ErrorState, Field, InlineError, Loading, Pill, cx } from "./ui";

function urlB64ToUint8Array(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

function ChannelCard({ id, title, configured, children, state, preview }: { id: string; title: string; configured: boolean; children: React.ReactNode; state?: React.ReactNode; preview?: boolean }) {
  return (
    <section aria-labelledby={`ch-${id}`} className={cx("rounded-lg border p-3", configured ? "border-edge bg-panel2/30" : "border-dashed border-edge bg-panel/40")}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 id={`ch-${id}`} className="text-sm font-semibold">{title}</h3>
        {configured ? state : <Pill>not configured by administrator</Pill>}
      </div>
      {!configured && <p className="text-xs text-muted">This channel needs server credentials that the administrator has not set up. It stays unavailable until then.</p>}
      {(configured || preview) && <div className={configured ? "" : "mt-2 opacity-90"}>{children}</div>}
    </section>
  );
}

export function NotificationSettingsPanel() {
  const q = useApi<NS>(() => api.notifications.settings(), []);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tg, setTg] = useState<TelegramLink | null>(null);
  const [wa, setWa] = useState<string | null>(null);
  const [waOpt, setWaOpt] = useState(false);
  const [test, setTest] = useState<NotificationItem | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="notification settings" />;
  if (!q.data) return <Loading />;
  const s = q.data;
  const ch = s.channels;
  const run = async (key: string, fn: () => Promise<string | void>) => {
    setBusy(key); setErr(null); setMsg(null);
    try { const m = await fn(); if (m) setMsg(m); } catch (e) { setErr(e instanceof Error ? e.message : "Request failed"); } finally { setBusy(null); }
  };
  const save = (body: Parameters<typeof api.notifications.saveSettings>[0], m: string) => run("save", async () => { q.setData(await api.notifications.saveSettings(body)); return m; });
  const toggleDefault = (c: string, on: boolean) => save({ default_channels: on ? [...s.default_channels, c] : s.default_channels.filter((x) => x !== c) }, "Default channels saved.");
  const pushSupported = typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window;
  const subscribePush = () => run("push", async () => {
    if (!s.vapid_public_key) throw new Error("No VAPID public key configured on the server.");
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("Browser notification permission was not granted.");
    const reg = await navigator.serviceWorker.register("/sw.js");
    await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(s.vapid_public_key) as BufferSource });
    const j = sub.toJSON();
    await api.notifications.pushSubscribe({ endpoint: j.endpoint!, keys: (j.keys ?? {}) as Record<string, string> });
    q.reload();
    return "This browser is subscribed to push notifications.";
  });
  const unsubscribePush = () => run("push", async () => {
    const reg = await navigator.serviceWorker.getRegistration("/sw.js");
    const sub = await reg?.pushManager.getSubscription();
    if (sub) { await api.notifications.pushUnsubscribe(sub.endpoint); await sub.unsubscribe(); }
    q.reload();
    return "Push unsubscribed for this browser.";
  });
  const hours = Array.from({ length: 24 }, (_, i) => i);
  const waValue = wa ?? s.whatsapp_number ?? "";

  return (
    <Card title="Notification settings" id="notifications">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ChannelCard id="web" title="In-app" configured={ch.web?.configured ?? true} state={<Pill tone="green">always on</Pill>}>
          <p className="text-xs text-muted">Shown in the bell menu and on the Notifications page.</p>
        </ChannelCard>

        <ChannelCard id="email" title="Email" configured={!!ch.email?.configured} state={<Pill tone={s.email_enabled ? "green" : "slate"}>{s.email_enabled ? "enabled" : "off"}</Pill>}>
          <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={s.email_enabled} disabled={busy === "save"} onChange={(e) => save({ email_enabled: e.target.checked }, `Email notifications ${e.target.checked ? "enabled" : "disabled"}.`)} /> Send to {s.email_address}</label>
        </ChannelCard>

        <ChannelCard id="telegram" title="Telegram" preview configured={!!ch.telegram?.configured} state={<Pill tone={s.telegram_linked ? "green" : "slate"}>{s.telegram_linked ? "linked" : "not linked"}</Pill>}>
          {s.telegram_linked ? (
            <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={busy === "tg"} onClick={() => run("tg", async () => { await api.notifications.telegramUnlink(); setTg(null); q.reload(); return "Telegram unlinked."; })}>Unlink Telegram</button>
          ) : (
            <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={busy === "tg"} onClick={() => run("tg", async () => { setTg(await api.notifications.telegramLink()); })}>{ch.telegram?.configured ? "Get link code" : "Generate link code (bot not configured yet)"}</button>
          )}
        </ChannelCard>

        <ChannelCard id="push" title="Web push (this browser)" configured={!!ch.push?.configured && !!s.vapid_public_key} state={<Pill tone={s.push_subscriptions ? "green" : "slate"}>{s.push_subscriptions} subscription(s)</Pill>}>
          {!pushSupported ? <p className="text-xs text-amber-300">This browser does not support Web Push.</p> : (
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={busy === "push"} onClick={subscribePush}>Subscribe this browser</button>
              {s.push_subscriptions > 0 && <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={busy === "push"} onClick={unsubscribePush}>Unsubscribe this browser</button>}
            </div>
          )}
        </ChannelCard>

        <ChannelCard id="whatsapp" title="WhatsApp" configured={!!ch.whatsapp?.configured} state={<Pill tone={s.whatsapp_number ? "green" : "slate"}>{s.whatsapp_number ? "number saved" : "no number"}</Pill>}>
          <form className="space-y-2" noValidate onSubmit={(e) => { e.preventDefault(); if (waValue && !/^\+[1-9]\d{7,14}$/.test(waValue)) return setErr("Use international format, e.g. +919876543210."); if (waValue && !waOpt) return setErr("Tick the opt-in box to receive WhatsApp messages."); save({ whatsapp_number: waValue, whatsapp_opt_in: waOpt }, waValue ? "WhatsApp number saved." : "WhatsApp number removed."); }}>
            <Field label="WhatsApp number (E.164)" htmlFor="wa-num"><input id="wa-num" className="input num" inputMode="tel" placeholder="+919876543210" maxLength={20} value={waValue} onChange={(e) => setWa(e.target.value.trim())} /></Field>
            <label className="flex items-start gap-2 text-xs"><input type="checkbox" className="mt-0.5" checked={waOpt} onChange={(e) => setWaOpt(e.target.checked)} /> I explicitly opt in to receive alert messages from MarketEdge AI on WhatsApp at this number.</label>
            <button className="btn-ghost px-2 py-1 text-xs" disabled={busy === "save"}>Save</button>
            <p className="text-[11px] text-muted">WhatsApp business messages require an approved message template; delivery can be refused until one is approved.</p>
          </form>
        </ChannelCard>
      </div>

      {tg && (
        <div role="status" className="mt-3 rounded-md border border-blue-800 bg-blue-950/30 p-3 text-sm">
          <p className="font-semibold">Telegram link code: <span className="num select-all rounded bg-bg px-1.5 py-0.5 font-mono text-base tracking-widest">{tg.code}</span></p>
          <p className="mt-1 text-xs text-muted">{tg.instructions} Expires {dateTime(tg.expires_at)}.</p>
          {tg.deep_link ? <a className="link mt-1 inline-block text-xs" href={tg.deep_link} target="_blank" rel="noopener noreferrer">Open Telegram ({tg.bot || "bot"}) ↗</a> : <p className="mt-1 text-xs text-amber-300">No bot deep link available{tg.configured ? "" : " — the Telegram bot is not configured on the server"}.</p>}
          <button type="button" className="btn-ghost mt-2 px-2 py-1 text-xs" onClick={() => { setTg(null); q.reload(); }}>I&apos;ve sent it — refresh status</button>
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
        <fieldset>
          <legend className="label">Default channels for new alerts</legend>
          <div className="flex flex-wrap gap-1.5">
            {Object.keys(CHANNEL_LABELS).filter((c) => c in ch).map((c) => (
              <label key={c} className={cx("inline-flex items-center gap-1 rounded border px-2 py-1 text-xs", ch[c]?.configured ? "border-edge" : "border-edge/50 text-muted")}>
                <input type="checkbox" disabled={c === "web" || !ch[c]?.configured || busy === "save"} checked={c === "web" || s.default_channels.includes(c)} onChange={(e) => toggleDefault(c, e.target.checked)} />
                {CHANNEL_LABELS[c]}{!ch[c]?.configured && <span className="text-[10px]">(not configured)</span>}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend className="label">Quiet hours (IST) — non-web channels are held</legend>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="From" htmlFor="qh-s"><select id="qh-s" className="input w-auto" value={s.quiet_start_hour ?? ""} onChange={(e) => e.target.value !== "" && save({ quiet_start_hour: Number(e.target.value), quiet_end_hour: s.quiet_end_hour ?? 7 }, "Quiet hours saved.")}><option value="">Off</option>{hours.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</select></Field>
            <Field label="To" htmlFor="qh-e"><select id="qh-e" className="input w-auto" value={s.quiet_end_hour ?? ""} onChange={(e) => e.target.value !== "" && save({ quiet_end_hour: Number(e.target.value), quiet_start_hour: s.quiet_start_hour ?? 22 }, "Quiet hours saved.")}><option value="">Off</option>{hours.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}</select></Field>
          </div>
        </fieldset>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary" disabled={busy === "test"} onClick={() => run("test", async () => { setTest(await api.notifications.test()); emitNotificationsChanged(); return "Test notification sent."; })}>{busy === "test" ? "Sending…" : "Send test notification"}</button>
        {test && <span className="text-xs">Delivery: <DeliveryChips d={test.deliveries} /></span>}
      </div>
      {msg && <p role="status" className="mt-2 text-sm text-green-300">{msg}</p>}
      <InlineError error={err} />
    </Card>
  );
}
