"use client";

import Link from "next/link";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { inr, integer } from "@/lib/format";
import type { PaperPortfolio, SetupDetail } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { ChannelPicker } from "./ChannelPicker";
import { Card, Field, InlineError, UpgradeNote } from "./ui";

function PaperTrade({ s, signalId }: { s: SetupDetail; signalId: number }) {
  const pf = useApi<{ items: PaperPortfolio[] }>(() => api.portfolios.list(), []);
  const paper = (pf.data?.items ?? []).filter((p) => p.kind === "paper");
  const [pid, setPid] = useState<string>("");
  const chosen = paper.find((p) => String(p.id) === pid) ?? paper[0];
  // Default size: risk 1% of portfolio equity (₹) using the INR risk per unit when the instrument is not in INR.
  const riskUnitInr = s.inr && s.inr.available !== false && s.inr.risk_per_unit_inr ? s.inr.risk_per_unit_inr : s.currency === "INR" ? Math.abs(s.current_price - s.stop) : null;
  const suggested = chosen && riskUnitInr ? Math.max(0, Math.floor((0.01 * chosen.analytics.equity) / riskUnitInr)) : null;
  const [qty, setQty] = useState<string>("");
  const q = qty || (suggested ? String(suggested) : "");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (pf.error?.status === 403) return <UpgradeNote feature="Paper trading" />;
  return (
    <div>
      {!pf.data ? <p className="text-xs text-muted">Loading portfolios…</p> : paper.length === 0 ? (
        <p className="text-sm text-muted">No paper portfolio yet. <Link className="link" href="/portfolio">Create one →</Link></p>
      ) : (
        <form className="space-y-2" noValidate onSubmit={async (e) => {
          e.preventDefault(); setErr(null); setMsg(null);
          if (!(Number(q) > 0)) return setErr("Enter a quantity.");
          setBusy(true);
          try { const r = await api.portfolios.fromSetup(chosen!.id, signalId, Number(q)); setMsg(`Order #${r.trade_id} ${r.status} in “${chosen!.name}”. ${r.note}`); }
          catch (x) { setErr(x instanceof Error ? x.message : "Order rejected"); } finally { setBusy(false); }
        }}>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Portfolio" htmlFor={`pt-p-${signalId}`}>
              <select id={`pt-p-${signalId}`} className="input" value={chosen ? String(chosen.id) : ""} onChange={(e) => { setPid(e.target.value); setQty(""); }}>
                {paper.map((p) => <option key={p.id} value={p.id}>{p.name} ({inr(p.analytics.equity, 0)})</option>)}
              </select>
            </Field>
            <Field label="Quantity" htmlFor={`pt-q-${signalId}`} hint={suggested != null ? `1% risk of equity ≈ ${integer(suggested)} units` : "Enter a size"}>
              <input id={`pt-q-${signalId}`} className="input num" type="number" min={0} step="any" value={q} onChange={(e) => setQty(e.target.value)} />
            </Field>
          </div>
          <p className="text-[11px] text-muted">Pre-fills the setup&apos;s stop, T1/T2, 50% partial at T1 and max hold. Fills at the next bar&apos;s open. <Link className="link" href="/risk">Position sizer →</Link></p>
          <button className="btn-primary" disabled={busy}>{busy ? "Placing…" : "Paper trade this setup"}</button>
          {msg && <p role="status" className="text-sm text-green-300">{msg} <Link className="link" href={`/portfolio?id=${chosen!.id}`}>Open portfolio →</Link></p>}
          <InlineError error={err} />
        </form>
      )}
    </div>
  );
}

function AlertMe({ signalId }: { signalId: number }) {
  const kinds = useApi(() => api.alerts.kinds(), []);
  const [sel, setSel] = useState<string[]>(["entry", "target1", "stop"]);
  const [ch, setCh] = useState<string[]>(["web"]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [limit, setLimit] = useState(false);
  const [busy, setBusy] = useState(false);
  const opts = [["entry", "Price enters the entry zone"], ["target1", "Target 1 reached"], ["target2", "Target 2 reached"], ["stop", "Stop reached"]] as const;
  return (
    <form className="space-y-2" noValidate onSubmit={async (e) => {
      e.preventDefault(); setErr(null); setMsg(null); setLimit(false);
      if (!sel.length) return setErr("Pick at least one event.");
      setBusy(true);
      try { const r = await api.alerts.fromSetup(signalId, sel, ch); setMsg(`${r.items.length} alert(s) created.`); }
      catch (x) { if (x instanceof ApiError && x.status === 403) setLimit(true); setErr(x instanceof Error ? x.message : "Could not create alerts"); } finally { setBusy(false); }
    }}>
      <fieldset>
        <legend className="label">Notify me when</legend>
        <div className="flex flex-wrap gap-1.5">
          {opts.map(([k, l]) => (
            <label key={k} className="inline-flex items-center gap-1 rounded border border-edge px-2 py-1 text-xs">
              <input type="checkbox" checked={sel.includes(k)} onChange={(e) => setSel((p) => (e.target.checked ? [...p, k] : p.filter((x) => x !== k)))} /> {l}
            </label>
          ))}
        </div>
      </fieldset>
      {kinds.data && <ChannelPicker value={ch} onChange={setCh} channels={kinds.data.channels} idp={`am-${signalId}`} />}
      <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Alert me"}</button>
      {msg && <p role="status" className="text-sm text-green-300">{msg} <Link className="link" href="/alerts">Manage alerts →</Link></p>}
      {limit && <UpgradeNote feature="More than 10 active alerts" />}
      <InlineError error={err} />
      {kinds.data && <p className="text-[11px] text-muted">{kinds.data.evaluation}</p>}
    </form>
  );
}

export function SetupActions({ s, signalId }: { s: SetupDetail; signalId?: number | null }) {
  const { can } = useAuth();
  if (!signalId) return null;
  return (
    <Card title="Act on this setup">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Paper trade</h3>
          {can("portfolio:write") ? <PaperTrade s={s} signalId={signalId} /> : <UpgradeNote feature="Paper trading" />}
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Alert me</h3>
          {can("alerts:write") ? <AlertMe signalId={signalId} /> : <UpgradeNote feature="Alerts" />}
        </div>
      </div>
      <p className="mt-2 text-[11px] text-muted">Paper trades are simulated — no orders reach a broker. {s.status !== "VALID" && "This candidate is NO TRADE: it failed validation; paper-trading it is for study only."}</p>
    </Card>
  );
}
