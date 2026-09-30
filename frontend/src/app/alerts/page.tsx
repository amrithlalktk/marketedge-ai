"use client";

import Link from "next/link";
import { useState } from "react";
import { CHANNEL_LABELS, ChannelPicker } from "@/components/ChannelPicker";
import { InstrumentSearch } from "@/components/InstrumentSearch";
import { Card, Confirm, Disclaimer, EmptyState, ErrorState, Field, InlineError, Loading, PageHeader, Pill, UpgradeNote } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, humanize } from "@/lib/format";
import { MARKETS } from "@/lib/market";
import type { AlertItem, AlertKind } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/** Optional parameters the backend accepts but does not list in PARAMS. */
const OPTIONAL: Record<string, string[]> = { new_setup: ["market", "strategy", "direction", "min_score"], unusual_options: ["multiple"] };

function ParamInput({ name, value, onChange, kind }: { name: string; value: string; onChange: (v: string) => void; kind: string }) {
  const id = `ap-${kind}-${name}`;
  const label = name === "min_score" ? "Minimum score" : name === "multiple" ? "Multiple (×)" : humanize(name);
  if (name === "direction") {
    return <Field label={label} htmlFor={id}><select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>{kind === "new_setup" && <option value="">Any</option>}<option>LONG</option><option>SHORT</option></select></Field>;
  }
  if (name === "fast" || name === "slow") {
    return <Field label={`${label} EMA`} htmlFor={id}><select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}>{[20, 50, 100, 200].map((n) => <option key={n}>{n}</option>)}</select></Field>;
  }
  if (name === "market") {
    return <Field label="Market" htmlFor={id}><select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)}><option value="">Any</option>{MARKETS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</select></Field>;
  }
  if (name === "strategy") {
    return <Field label="Strategy id (optional)" htmlFor={id}><input id={id} className="input font-mono" placeholder="e.g. breakout_volume" value={value} onChange={(e) => onChange(e.target.value)} /></Field>;
  }
  return <Field label={label} htmlFor={id}><input id={id} className="input num" type="number" step="any" min={0} value={value} onChange={(e) => onChange(e.target.value)} /></Field>;
}

function defaults(k: AlertKind | undefined): Record<string, string> {
  const d: Record<string, string> = {};
  for (const p of [...(k?.params ?? []), ...(OPTIONAL[k?.kind ?? ""] ?? [])]) {
    d[p] = p === "direction" ? (k?.kind === "new_setup" ? "" : "LONG") : p === "fast" ? "20" : p === "slow" ? "50" : p === "multiple" ? (k?.kind === "volume_spike" ? "2" : "3") : p === "level" && k?.kind.startsWith("rsi") ? "70" : "";
  }
  return d;
}

function CreateAlert({ kinds, channels, evaluation, onCreated }: { kinds: AlertKind[]; channels: Record<string, { configured: boolean }>; evaluation: string; onCreated: (m: string) => void }) {
  const [kind, setKind] = useState(kinds[0]?.kind ?? "price_above");
  const k = kinds.find((x) => x.kind === kind);
  const [params, setParams] = useState<Record<string, string>>(() => defaults(k));
  const [symbol, setSymbol] = useState<string | null>(null);
  const [ch, setCh] = useState<string[]>(["web"]);
  const [repeat, setRepeat] = useState(false);
  const [cooldown, setCooldown] = useState("1440");
  const [note, setNote] = useState("");
  const [expires, setExpires] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [limit, setLimit] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null); setLimit(false);
    if (k?.needs_symbol && !symbol) return setErr("This alert needs a symbol.");
    const out: Record<string, unknown> = {};
    for (const p of k?.params ?? []) {
      const v = params[p];
      if (v === "" || v == null) return setErr(`${humanize(p)} is required.`);
      out[p] = p === "direction" ? v : Number(v);
    }
    for (const p of OPTIONAL[kind] ?? []) {
      const v = params[p];
      if (v !== "" && v != null) out[p] = ["market", "strategy", "direction"].includes(p) ? v : Number(v);
    }
    setBusy(true);
    try {
      const a = await api.alerts.create({ kind, ...(k?.needs_symbol ? { symbol: symbol! } : {}), params: out, channels: ch, repeat, cooldown_minutes: Math.max(5, Number(cooldown) || 1440), note, ...(expires ? { expires_at: new Date(expires).toISOString() } : {}) });
      onCreated(`Alert #${a.id} created: ${a.description}${a.symbol ? ` · ${a.symbol}` : ""}.`);
      setNote("");
    } catch (x) { if (x instanceof ApiError && x.status === 403) setLimit(true); setErr(x instanceof Error ? x.message : "Could not create"); } finally { setBusy(false); }
  };

  return (
    <Card title="New alert">
      <form onSubmit={submit} noValidate className="space-y-3">
        <Field label="Alert type" htmlFor="al-kind">
          <select id="al-kind" className="input" value={kind} onChange={(e) => { const nk = e.target.value; setKind(nk); setParams(defaults(kinds.find((x) => x.kind === nk))); }}>
            {kinds.map((x) => <option key={x.kind} value={x.kind}>{humanize(x.kind)} — {x.description}</option>)}
          </select>
        </Field>
        {k?.needs_symbol && (
          <div>
            <span className="label">Symbol</span>
            <InstrumentSearch label="Alert symbol" onPick={(i) => setSymbol(i.symbol)} />
            {symbol && <p className="mt-1 text-xs">Selected: <span className="font-mono font-semibold">{symbol}</span> <button type="button" className="text-accent hover:underline" onClick={() => setSymbol(null)}>clear</button></p>}
          </div>
        )}
        {[...(k?.params ?? []), ...(OPTIONAL[kind] ?? [])].length > 0 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[...(k?.params ?? []), ...(OPTIONAL[kind] ?? [])].map((p) => <ParamInput key={p} name={p} kind={kind} value={params[p] ?? ""} onChange={(v) => setParams((o) => ({ ...o, [p]: v }))} />)}
          </div>
        )}
        <ChannelPicker value={ch} onChange={setCh} channels={channels} idp="al-ch" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <label className="inline-flex items-center gap-2 self-end pb-2 text-sm"><input type="checkbox" checked={repeat} onChange={(e) => setRepeat(e.target.checked)} /> Repeat</label>
          <Field label="Cooldown (minutes)" htmlFor="al-cd" hint="Minimum gap between repeats"><input id="al-cd" className="input num" type="number" min={5} step="5" value={cooldown} onChange={(e) => setCooldown(e.target.value)} /></Field>
          <Field label="Expires (optional)" htmlFor="al-ex"><input id="al-ex" className="input" type="datetime-local" value={expires} onChange={(e) => setExpires(e.target.value)} /></Field>
          <Field label="Note" htmlFor="al-note"><input id="al-note" className="input" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        </div>
        <p className="text-[11px] text-muted">ⓘ {evaluation}</p>
        {limit && <UpgradeNote feature="More than 10 active alerts" />}
        <InlineError error={err} />
        <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create alert"}</button>
      </form>
    </Card>
  );
}

function AlertRow({ a, onChanged }: { a: AlertItem; onChanged: (m?: string) => void }) {
  const [err, setErr] = useState<string | null>(null);
  const toggle = async () => {
    setErr(null);
    try { const r = await api.alerts.patch(a.id, { status: a.status === "active" ? "paused" : "active" }); onChanged(`Alert #${a.id} ${r.status}.`); } catch (x) { setErr(x instanceof Error ? x.message : "Failed"); }
  };
  const params = Object.entries(a.params ?? {});
  return (
    <li className="rounded-lg border border-edge bg-panel2/30 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-1.5 text-sm">
            <span className="font-semibold">{humanize(a.kind)}</span>
            {a.symbol && <Link className="link font-mono" href={`/stocks/${encodeURIComponent(a.symbol)}`}>{a.symbol}</Link>}
            <Pill tone={a.status === "active" ? "green" : a.status === "paused" ? "amber" : "slate"}>{a.status}</Pill>
            {a.signal_id && <Link href={`/setups/${a.signal_id}`} className="text-[11px] text-accent hover:underline">setup #{a.signal_id}</Link>}
          </p>
          <p className="text-xs text-muted">{a.description}{params.length > 0 && <> · {params.map(([k, v]) => `${k} ${String(v)}`).join(", ")}</>}</p>
          <p className="mt-1 flex flex-wrap gap-1 text-[11px]">{a.channels.map((c) => <Pill key={c}>{CHANNEL_LABELS[c] ?? c}</Pill>)}</p>
          <p className="mt-1 text-[11px] text-muted">
            Triggered {a.trigger_count}× {a.last_triggered_at ? `· last ${dateTime(a.last_triggered_at)}` : ""} · {a.repeat ? `repeats (cooldown ${a.cooldown_minutes} min)` : "one-shot"}{a.expires_at ? ` · expires ${dateTime(a.expires_at)}` : ""} · created {dateTime(a.created_at)}
          </p>
          {a.note && <p className="text-[11px] text-muted">“{a.note}”</p>}
        </div>
        <div className="flex gap-1">
          {(a.status === "active" || a.status === "paused") && <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={toggle}>{a.status === "active" ? "Pause" : "Resume"}</button>}
          <Confirm label="Delete" className="btn-ghost px-2 py-1 text-xs" onConfirm={async () => { try { await api.alerts.remove(a.id); onChanged(`Alert #${a.id} deleted.`); } catch (x) { setErr(x instanceof Error ? x.message : "Failed"); } }}>Delete</Confirm>
        </div>
      </div>
      <InlineError error={err} />
    </li>
  );
}

export default function AlertsPage() {
  const { can } = useAuth();
  const allowed = can("alerts:write");
  const kinds = useApi(() => api.alerts.kinds(), [], allowed);
  const list = useApi<{ items: AlertItem[] }>(() => api.alerts.list(), [], allowed);
  const [msg, setMsg] = useState<string | null>(null);
  if (!allowed) return <UpgradeNote feature="Alerts" />;
  const items = list.data?.items ?? [];
  const activeCount = items.filter((a) => a.status === "active" || a.status === "paused").length;
  const changed = (m?: string) => { if (m) setMsg(m); list.reload(); };
  return (
    <>
      <PageHeader title="Alerts" subtitle="Price, indicator, pattern and new-setup alerts delivered in-app and to any configured channel." />
      {kinds.error ? <ErrorState error={kinds.error} onRetry={kinds.reload} what="alert types" /> : !kinds.data ? <Loading /> : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
          <div className="min-w-0 xl:col-span-2">
            <CreateAlert kinds={kinds.data.kinds} channels={kinds.data.channels} evaluation={kinds.data.evaluation} onCreated={changed} />
          </div>
          <div className="min-w-0 xl:col-span-3">
            <Card title={`Your alerts (${items.length})`} right={!can("alerts:unlimited") ? <span className="text-[11px] text-muted">{activeCount}/10 active or paused (Standard plan)</span> : null}>
              {msg && <p role="status" className="mb-2 text-sm text-green-300">{msg}</p>}
              {list.error ? <ErrorState error={list.error} onRetry={list.reload} what="alerts" /> : !list.data ? <Loading /> : items.length === 0 ? <EmptyState title="No alerts yet" /> : <ul className="space-y-2">{items.map((a) => <AlertRow key={a.id} a={a} onChanged={changed} />)}</ul>}
            </Card>
          </div>
        </div>
      )}
      <Disclaimer />
    </>
  );
}
