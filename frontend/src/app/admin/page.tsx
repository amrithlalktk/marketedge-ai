"use client";

import { useEffect, useState } from "react";
import { Card, Confirm, EmptyState, ErrorState, Field, InlineError, Loading, PageHeader, Pill, Segmented, Stat, TableWrap } from "@/components/ui";
import { api } from "@/lib/api";
import { isAdmin, useAuth } from "@/lib/auth";
import { COMPONENT_LABELS } from "@/lib/constants";
import { invalidateWeights } from "@/lib/weights";
import { dateTime, humanize } from "@/lib/format";
import { MARKETS, marketLabel } from "@/lib/market";
import type { AdminHealth, AdminUser, AuditLog, EngineSettings, Job, MarketInfo, Providers } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Tab = "health" | "jobs" | "scoring" | "users" | "audit" | "providers";

function HealthTab() {
  const h = useApi<AdminHealth>(() => api.admin.health(), []);
  if (h.error) return <ErrorState error={h.error} onRetry={h.reload} what="health" />;
  if (!h.data) return <Loading />;
  const d = h.data;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Stat label="Database" value={d.database ? "OK" : "DOWN"} valueClass={d.database ? "text-up" : "text-down"} />
        <Stat label="Cache" value={d.cache_backend} />
        <Stat label="Redis" value={d.redis ? "OK" : "not connected"} valueClass={d.redis ? "text-up" : "text-muted"} />
        <Stat label="Provider" value={String(d.provider.provider ?? "—")} sub={d.provider.ok ? "healthy" : "unhealthy"} valueClass={d.provider.ok ? "text-up" : "text-down"} />
        <Stat label="Failed jobs" value={d.failed_jobs} valueClass={d.failed_jobs ? "text-down" : "text-up"} />
      </div>
      {d.provider.note != null && <p className="text-xs text-muted">Provider note: {String(d.provider.note)}</p>}
      {(d.readiness || d.queues) && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          {d.readiness && (
            <Stat label="Readiness" value={d.readiness.ready ? "READY" : "NOT READY"} valueClass={d.readiness.ready ? "text-up" : "text-down"}
              sub={`migrations: ${d.readiness.checks.migrations ?? "—"}`} />
          )}
          {Object.entries(d.queues ?? {}).map(([q, n]) => (
            <Stat key={q} label={`Queue · ${q}`} value={n} sub="waiting" valueClass={n > 20 ? "text-down" : undefined} />
          ))}
        </div>
      )}
      <Card title="Data sources" right={<button className="btn-ghost px-2 py-1 text-xs" onClick={h.reload}>Refresh</button>}>
        <TableWrap label="Data sources">
          <table className="tbl min-w-[560px]">
            <thead><tr><th scope="col">Source</th><th scope="col">Instruments</th><th scope="col">Last bar</th><th scope="col">Last fetch</th><th scope="col">Errors</th><th scope="col">Type</th></tr></thead>
            <tbody>
              {d.data_sources.map((s) => (
                <tr key={s.source}>
                  <td className="font-mono">{s.source}</td><td className="num">{s.instruments}</td><td className="num">{s.last_bar?.slice(0, 10) ?? "—"}</td>
                  <td className="num text-xs">{dateTime(s.last_fetch)}</td><td className={s.errors ? "num text-down" : "num"}>{s.errors}</td>
                  <td>{s.is_sample ? <Pill tone="amber">SAMPLE</Pill> : <Pill tone="green">Live</Pill>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>
      <Card title="Last scan">
        {!d.last_scan ? <EmptyState title="No scan has run yet" /> : (
          <div className="space-y-2 text-sm">
            <p>#{d.last_scan.id} · <Pill tone={d.last_scan.status === "done" ? "green" : d.last_scan.status === "failed" ? "red" : "amber"}>{d.last_scan.status}</Pill> · as of {d.last_scan.as_of ?? "—"} · started {dateTime(d.last_scan.started_at)}</p>
            {d.last_scan.error && <p className="text-down">{d.last_scan.error}</p>}
            {d.last_scan.stats && (
              <dl className="grid grid-cols-2 gap-1 text-xs sm:grid-cols-4">
                {Object.entries(d.last_scan.stats).map(([k, v]) => (
                  <div key={k} className="rounded border border-edge px-2 py-1"><dt className="text-muted">{humanize(k)}</dt><dd className="break-words">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>
                ))}
              </dl>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}

function MarketsList({ tick }: { tick: number }) {
  const q = useApi<{ items: MarketInfo[] }>(() => api.markets.list(), [tick]);
  return (
    <Card title="Markets" right={<button className="btn-ghost px-2 py-1 text-xs" onClick={q.reload}>Refresh</button>}>
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="markets" /> : !q.data ? <Loading /> : (
        <TableWrap label="Markets">
          <table className="tbl min-w-[720px] text-xs">
            <thead><tr><th scope="col">Market</th><th scope="col">Group</th><th scope="col">Calendar</th><th scope="col">Benchmark</th><th scope="col">Provider</th><th scope="col">Last scan (as of)</th><th scope="col">Finished</th><th scope="col" className="text-right">Valid</th><th scope="col">Data</th></tr></thead>
            <tbody>
              {q.data.items.map((m) => (
                <tr key={m.id}>
                  <th scope="row" className="text-left"><span className="font-mono">{m.id}</span> <span className="block font-normal text-muted">{m.name}</span></th>
                  <td>{m.group} · {m.asset_class}</td><td>{m.calendar === "24x7" ? "24×7" : "Weekdays"}</td><td className="font-mono">{m.benchmark}</td><td>{m.provider}</td>
                  <td className="num">{m.last_scan?.as_of ?? "never"}</td><td className="num whitespace-nowrap">{dateTime(m.last_scan?.finished_at)}</td>
                  <td className="num text-right">{m.last_scan?.valid ?? "—"}</td>
                  <td>{m.last_scan?.is_sample ? <Pill tone="amber">SAMPLE</Pill> : m.last_scan ? <Pill tone="green">Live</Pill> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

function JobsTab() {
  const jobs = useApi<{ items: Job[] }>(() => api.admin.jobs(50), []);
  const [market, setMarket] = useState("NSE");
  const [tick, setTick] = useState(0);
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const anyRunning = jobs.data?.items.some((j) => j.status === "queued" || j.status === "running");
  const { reload } = jobs;
  useEffect(() => {
    if (!anyRunning) return;
    const t = setTimeout(reload, 3000);
    return () => clearTimeout(t);
  }, [anyRunning, reload, jobs.data]);

  const trigger = async (kind: "ingest" | "scan" | "options" | "news" | "calendar" | "ml") => {
    setBusy(kind); setError(null); setMsg(null);
    try {
      const r = kind === "ingest" ? await api.admin.ingest(full, market) : kind === "scan" ? await api.admin.scan(market) : kind === "news" ? await api.admin.news(market) : kind === "calendar" ? await api.admin.calendar() : kind === "ml" ? await api.admin.mlTrain(market) : await api.admin.options();
      const label = kind === "options" ? "Options ingest + analysis" : kind === "calendar" ? "Calendar refresh" : kind === "news" ? `News fetch (${marketLabel(market)})` : kind === "ml" ? `ML training (${marketLabel(market)})` : `${humanize(kind)} (${marketLabel(market)})`;
      setMsg(`${label} job #${r.job_id} submitted.`);
      reload();
      setTick((t) => t + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "Failed"); } finally { setBusy(null); }
  };

  return (
    <div className="space-y-4">
      <Card title="Trigger jobs">
        <p className="mb-3 text-sm text-muted">Run <strong className="text-ink">ingestion</strong> first to fetch bars, then a <strong className="text-ink">scan</strong> to compute market snapshots, strategy performance and today&apos;s setups. <strong className="text-ink">Options analysis</strong> ingests the NIFTY option chain and rebuilds the options view. <strong className="text-ink">News</strong> fetches and classifies headlines for the selected market; <strong className="text-ink">calendars</strong> refresh economic releases and earnings dates (setups pick them up on the next scan).</p>
        <div className="flex flex-wrap items-center gap-3">
          <Field label="Market" htmlFor="job-mkt">
            <select id="job-mkt" className="input w-auto" value={market} onChange={(e) => setMarket(e.target.value)}>
              {MARKETS.map((m) => <option key={m.id} value={m.id}>{m.label} ({m.id})</option>)}
            </select>
          </Field>
          <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> Full history re-ingest</label>
          <button className="btn-primary" disabled={!!busy} onClick={() => trigger("ingest")}>{busy === "ingest" ? "Ingesting…" : `Run ingestion — ${market}`}</button>
          <button className="btn-primary" disabled={!!busy} onClick={() => trigger("scan")}>{busy === "scan" ? "Scanning…" : `Run scan — ${market}`}</button>
          <button className="btn-primary" disabled={!!busy} onClick={() => trigger("options")}>{busy === "options" ? "Analysing options…" : "Run options analysis"}</button>
          <button className="btn-ghost" disabled={!!busy} onClick={() => trigger("news")}>{busy === "news" ? "Fetching news…" : `Fetch news — ${market}`}</button>
          <button className="btn-ghost" disabled={!!busy} onClick={() => trigger("ml")}>{busy === "ml" ? "Training…" : `Train ML — ${market}`}</button>
          <button className="btn-ghost" disabled={!!busy} onClick={() => trigger("calendar")}>{busy === "calendar" ? "Refreshing…" : "Refresh calendars"}</button>
        </div>
        {busy && <p className="mt-2 text-xs text-muted">With eager (in-process) jobs the request waits until the job finishes.</p>}
        {msg && <p role="status" className="mt-2 text-sm text-green-300">{msg}</p>}
        <InlineError error={error} />
      </Card>
      <MarketsList tick={tick} />
      <Card title="Recent jobs" right={<button className="btn-ghost px-2 py-1 text-xs" onClick={reload}>Refresh</button>}>
        {jobs.error ? <ErrorState error={jobs.error} onRetry={reload} /> : !jobs.data ? <Loading /> : jobs.data.items.length === 0 ? <EmptyState title="No jobs yet" /> : (
          <TableWrap label="Jobs">
            <table className="tbl min-w-[640px] text-xs">
              <thead><tr><th scope="col">#</th><th scope="col">Kind</th><th scope="col">Market</th><th scope="col">Status</th><th scope="col">Created</th><th scope="col">Finished</th><th scope="col">Result / error</th></tr></thead>
              <tbody>
                {jobs.data.items.map((j) => (
                  <tr key={j.id}>
                    <td className="num">{j.id}</td><td>{j.kind}</td><td className="font-mono">{typeof j.params?.market === "string" ? j.params.market : j.kind === "options" ? "NFO" : j.kind === "calendar" ? "all" : "—"}</td>
                    <td><Pill tone={j.status === "done" ? "green" : j.status === "failed" ? "red" : "amber"}>{j.status}</Pill></td>
                    <td className="num whitespace-nowrap">{dateTime(j.created_at)}</td><td className="num whitespace-nowrap">{dateTime(j.finished_at)}</td>
                    <td className="max-w-[28rem] break-words font-mono text-[11px] text-muted">{j.error ? <span className="text-down">{j.error}</span> : j.result ? JSON.stringify(j.result).slice(0, 300) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
    </div>
  );
}

function ScoringTab() {
  const q = useApi<EngineSettings>(() => api.admin.getEngine(), []);
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [labels, setLabels] = useState<{ min: string; label: string }[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!q.data) return;
    setWeights(Object.fromEntries(Object.entries(q.data.effective.weights).map(([k, v]) => [k, String(v)])));
    setLabels(q.data.effective.labels.map(([m, l]) => ({ min: String(m), label: l })));
  }, [q.data]);
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="engine settings" />;
  if (!q.data) return <Loading />;
  const total = Object.values(weights).reduce((a, b) => a + (Number(b) || 0), 0);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null); setMsg(null);
    try {
      const r = await api.admin.putEngine({
        weights: Object.fromEntries(Object.entries(weights).map(([k, v]) => [k, Number(v)])),
        labels: labels.filter((l) => l.label.trim()).map((l) => [Number(l.min), l.label.trim()] as [number, string]).sort((a, b) => b[0] - a[0]),
      });
      q.setData(r);
      invalidateWeights();
      setMsg(r.note ?? "Saved.");
    } catch (err) { setError(err instanceof Error ? err.message : "Save failed"); } finally { setBusy(false); }
  };

  return (
    <form onSubmit={save} className="space-y-4">
      <Card title="Scoring weights" right={<span className="num text-xs text-muted">Total {total} (weights are normalised by the engine)</span>}>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {Object.keys(weights).map((k) => (
            <Field key={k} label={COMPONENT_LABELS[k] ?? humanize(k)} htmlFor={`w-${k}`} hint={k === "historical" ? "Empirical hit-rate evidence. Default 0 = context only." : k === "ml" ? "Active ML model probability (only when a model passed the gate). Default 0 = context only." : undefined}>
              <input id={`w-${k}`} className="input num" type="number" min={0} max={100} step="1" value={weights[k]} onChange={(e) => setWeights((p) => ({ ...p, [k]: e.target.value }))} />
            </Field>
          ))}
        </div>
      </Card>
      <Card title="Score labels (minimum score → label)">
        <ul className="space-y-2">
          {labels.map((l, i) => (
            <li key={i} className="grid grid-cols-[6rem_1fr_auto] items-end gap-2">
              <Field label="Min score" htmlFor={`lm-${i}`}><input id={`lm-${i}`} className="input num" type="number" min={0} max={100} value={l.min} onChange={(e) => setLabels((p) => p.map((x, j) => (j === i ? { ...x, min: e.target.value } : x)))} /></Field>
              <Field label="Label" htmlFor={`ll-${i}`}><input id={`ll-${i}`} className="input" maxLength={64} value={l.label} onChange={(e) => setLabels((p) => p.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} /></Field>
              <button type="button" className="btn-ghost" aria-label={`Remove label ${l.label}`} onClick={() => setLabels((p) => p.filter((_, j) => j !== i))}>✕</button>
            </li>
          ))}
        </ul>
        <button type="button" className="btn-ghost mt-2" onClick={() => setLabels((p) => [...p, { min: "0", label: "" }])}>+ Add label</button>
        <p className="mt-2 text-[11px] text-muted">Avoid promissory wording (e.g. “guaranteed”, “sure-shot”). Labels describe setup quality only.</p>
      </Card>
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save settings"}</button>
        {msg && <span role="status" className="text-sm text-green-300">{msg}</span>}
      </div>
      <InlineError error={error} />
      <Card title="Other effective settings (read-only)">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {(["levels", "validation", "backtest"] as const).map((sec) => (
            <div key={sec}>
              <h3 className="mb-1 text-xs font-semibold uppercase text-muted">{sec}</h3>
              <pre className="overflow-x-auto rounded border border-edge bg-bg p-2 text-[11px]">{JSON.stringify(q.data!.effective[sec], null, 2)}</pre>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-muted">Analysis mode: {q.data.effective.analysis_mode} · Overrides stored: {Object.keys(q.data.overrides).join(", ") || "none"}</p>
      </Card>
    </form>
  );
}

function UsersTab() {
  const { user: me } = useAuth();
  const q = useApi<{ items: AdminUser[] }>(() => api.admin.users(1), []);
  const [error, setError] = useState<string | null>(null);
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="users" />;
  if (!q.data) return <Loading />;
  const update = async (u: AdminUser, role: string, is_active?: boolean) => {
    setError(null);
    try {
      const r = await api.admin.changeUser(u.id, role, is_active);
      q.setData({ items: q.data!.items.map((x) => (x.id === u.id ? { ...x, role: r.role, is_active: r.is_active } : x)) });
    } catch (e) { setError(e instanceof Error ? e.message : "Update failed"); }
  };
  return (
    <Card title="Users">
      <InlineError error={error} />
      <TableWrap label="Users">
        <table className="tbl min-w-[720px]">
          <thead><tr><th scope="col">Email</th><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">2FA</th><th scope="col">Created</th><th scope="col">Last login</th></tr></thead>
          <tbody>
            {q.data.items.map((u) => (
              <tr key={u.id}>
                <td className="max-w-[16rem] truncate">{u.email}</td><td>{u.full_name || "—"}</td>
                <td>
                  <label className="sr-only" htmlFor={`role-${u.id}`}>Role for {u.email}</label>
                  <select id={`role-${u.id}`} className="input w-auto py-1 text-xs" value={u.role} disabled={u.id === me?.id} onChange={(e) => update(u, e.target.value)}>
                    {["standard", "premium", "analyst", "admin"].map((r) => <option key={r}>{r}</option>)}
                  </select>
                </td>
                <td>
                  {u.id === me?.id ? <Pill tone="green">you</Pill> : u.is_active ? (
                    <Confirm label="Deactivate" className="btn-ghost px-2 py-1 text-xs" onConfirm={() => update(u, u.role, false)}>Active</Confirm>
                  ) : (
                    <button className="btn-ghost px-2 py-1 text-xs" onClick={() => update(u, u.role, true)}>Inactive · reactivate</button>
                  )}
                </td>
                <td>{u.totp_enabled ? <Pill tone="green">on</Pill> : <Pill>off</Pill>}</td>
                <td className="num text-xs">{dateTime(u.created_at)}</td><td className="num text-xs">{dateTime(u.last_login_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  );
}

function AuditTab() {
  const [action, setAction] = useState("");
  const [applied, setApplied] = useState("");
  const q = useApi<{ items: AuditLog[] }>(() => api.admin.audit(200, applied || undefined), [applied]);
  return (
    <Card title="Audit logs">
      <form className="mb-3 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); setApplied(action.trim()); }}>
        <Field label="Filter by exact action" htmlFor="act"><input id="act" className="input" placeholder="e.g. auth.login" value={action} onChange={(e) => setAction(e.target.value)} /></Field>
        <button className="btn-ghost">Apply</button>
      </form>
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : !q.data ? <Loading /> : q.data.items.length === 0 ? <EmptyState title="No entries" /> : (
        <TableWrap label="Audit log entries">
          <table className="tbl min-w-[720px] text-xs">
            <thead><tr><th scope="col">When</th><th scope="col">User</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col">IP</th><th scope="col">Detail</th></tr></thead>
            <tbody>
              {q.data.items.map((a) => (
                <tr key={a.id}>
                  <td className="num whitespace-nowrap">{dateTime(a.created_at)}</td><td className="num">{a.user_id ?? "—"}</td><td className="font-mono">{a.action}</td>
                  <td className="max-w-[14rem] truncate">{a.target ?? "—"}</td><td className="num">{a.ip ?? "—"}</td>
                  <td className="max-w-[20rem] break-words font-mono text-[11px] text-muted">{a.detail && JSON.stringify(a.detail) !== "{}" ? JSON.stringify(a.detail).slice(0, 240) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

const UPSTOX_RESULT: Record<string, string> = {
  connected: "Upstox connected — valid until 03:30 IST tomorrow.",
  expired: "The connect link expired or was already used. Click Connect Upstox again.",
  denied: "Upstox login was cancelled.",
  exchange: "Upstox rejected the login code. Check API_KEY / API_SECRET and that the Redirect URL matches exactly.",
};

function UpstoxCard() {
  const q = useApi(() => api.upstox.status(), []);
  const [err, setErr] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [result] = useState(() => (typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("upstox") : null));
  const reason = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("reason") : null;
  async function saveToken(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setSaved(null);
    setBusy(true);
    try {
      const r = await api.upstox.saveAnalyticsToken(token);
      setToken("");
      setSaved(`Token verified with Upstox and stored (valid until about ${dateTime(r.expires_at)}).`);
      q.reload();
    } catch (x) {
      setErr(x instanceof Error ? x.message : "Could not save the token");
    } finally {
      setBusy(false);
    }
  }
  async function connect() {
    setErr(null);
    try {
      window.location.href = (await api.upstox.connect()).url; // Upstox login page; it redirects back here
    } catch (x) {
      setErr(x instanceof Error ? x.message : "Could not start the Upstox login");
    }
  }
  const d = q.data;
  return (
    <Card title="Upstox (NSE + NIFTY options)">
      {result && (
        <p role="status" className={result === "connected" ? "mb-2 text-sm text-green-300" : "mb-2 text-sm text-red-300"}>
          {UPSTOX_RESULT[result === "connected" ? "connected" : reason ?? ""] ?? "Upstox connection failed."}
        </p>
      )}
      {!d ? <Loading /> : (
        <div className="space-y-3 text-sm">
          <p>
            Status:{" "}
            {d.rejected_at ? <Pill tone="red">token rejected by Upstox ({dateTime(d.rejected_at)}): generate a new one and paste it below</Pill>
              : d.mode === "analytics" ? <Pill tone="green">analytics token · valid until ~{dateTime(d.expires_at)}</Pill>
              : d.mode === "daily" ? <Pill tone="green">connected until {dateTime(d.expires_at)}</Pill>
              : <Pill tone="amber">not connected</Pill>}
            {" "}· used for {[d.used_for.market_data && "NSE market data", d.used_for.options && "NIFTY options"].filter(Boolean).join(" and ") || "nothing yet"}
          </p>
          <form className="space-y-2" autoComplete="off" onSubmit={saveToken}>
            <p className="font-medium">Recommended: Analytics token (read-only, valid 1 year, no daily login)</p>
            <ol className="list-decimal space-y-0.5 pl-5 text-xs text-muted">
              <li>Upstox → Apps → My Apps → <b>Analytics</b> tab → <b>Generate Token</b>, then copy the whole token.</li>
              <li>Paste it here. It is checked with Upstox, stored encrypted, and never shown again.</li>
            </ol>
            <Field label="Analytics token" htmlFor="uxtok">
              <input id="uxtok" type="password" className="input font-mono" autoComplete="new-password" minLength={20} maxLength={4096} required
                value={token} onChange={(e) => setToken(e.target.value)} />
            </Field>
            <button className="btn-primary" disabled={busy || token.trim().length < 20}>{busy ? "Checking with Upstox…" : d.mode === "analytics" ? "Replace token" : "Save token"}</button>
            {saved && <span role="status" className="ml-2 text-sm text-green-300">{saved}</span>}
          </form>
          <InlineError error={err} />
          <details className="text-xs text-muted">
            <summary className="cursor-pointer">Alternative: daily login with an app (tokens end at 03:30 IST)</summary>
            <ol className="mt-1 list-decimal space-y-0.5 pl-5">
              <li>Create an app with Redirect URL exactly <span className="font-mono text-ink">{d.redirect_uri}</span>.</li>
              <li>Store <span className="font-mono text-ink">upstox</span> / <span className="font-mono text-ink">API_KEY</span> and <span className="font-mono text-ink">API_SECRET</span> below, then connect once a day.</li>
            </ol>
            <button className="btn-ghost mt-1 px-2 py-1 text-xs" disabled={!d.configured} onClick={connect}>{d.mode === "daily" ? "Reconnect Upstox" : "Connect Upstox"}</button>
          </details>
        </div>
      )}
    </Card>
  );
}

function ProvidersTab() {
  const q = useApi<Providers>(() => api.admin.providers(), []);
  const [provider, setProvider] = useState("");
  const [name, setName] = useState("API_KEY");
  const [value, setValue] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="providers" />;
  if (!q.data) return <Loading />;
  const d = q.data;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Market data provider"><p className="text-sm">Active: <Pill tone="blue">{d.market_data.active}</Pill></p><p className="mt-1 text-xs text-muted">Available: {d.market_data.available.join(", ")}</p></Card>
        <Card title="Fundamentals provider"><p className="text-sm">Active: <Pill tone="blue">{d.fundamentals.active}</Pill></p><p className="mt-1 text-xs text-muted">Available: {d.fundamentals.available.join(", ")}</p></Card>
      </div>
      <UpstoxCard />
      <Card title="Stored API keys">
        {d.api_keys.length === 0 ? <EmptyState title="No keys stored" /> : (
          <ul className="divide-y divide-edge text-sm">
            {d.api_keys.map((k) => <li key={k.id} className="flex justify-between py-1.5"><span className="font-mono">{k.provider}:{k.name}</span><span className="text-xs text-muted">{k.enabled ? "enabled" : "disabled"} · {dateTime(k.created_at)}</span></li>)}
          </ul>
        )}
        <p className="mt-2 text-[11px] text-muted">Key values are encrypted at rest and are never returned by the API.</p>
      </Card>
      <Card title="Add / replace API key (write-only)">
        <div className="mb-3 rounded border border-edge bg-panel2/40 p-2 text-xs text-muted">
          <p className="font-semibold text-ink">Notification channels are configured with these provider / key names:</p>
          <ul className="mt-1 grid grid-cols-1 gap-0.5 sm:grid-cols-2">
            <li><span className="font-mono text-ink">smtp</span> / <span className="font-mono text-ink">PASSWORD</span> — email</li>
            <li><span className="font-mono text-ink">telegram</span> / <span className="font-mono text-ink">BOT_TOKEN</span> — Telegram bot</li>
            <li><span className="font-mono text-ink">webpush</span> / <span className="font-mono text-ink">VAPID_PRIVATE_KEY</span> — Web Push</li>
            <li><span className="font-mono text-ink">whatsapp</span> / <span className="font-mono text-ink">ACCESS_TOKEN</span> — WhatsApp Cloud API</li>
          </ul>
          <p className="mt-2 font-semibold text-ink">Market data:</p>
          <ul className="mt-1 grid grid-cols-1 gap-0.5 sm:grid-cols-2">
            <li><span className="font-mono text-ink">angelone</span> / <span className="font-mono text-ink">API_KEY</span>, <span className="font-mono text-ink">CLIENT_CODE</span>, <span className="font-mono text-ink">MPIN</span>, <span className="font-mono text-ink">TOTP_SECRET</span> — Angel One SmartAPI (NSE + NIFTY options)</li>
            <li><span className="font-mono text-ink">upstox</span> / <span className="font-mono text-ink">API_KEY</span>, <span className="font-mono text-ink">API_SECRET</span> — Upstox (NSE + NIFTY options; then Connect daily)</li>
            <li><span className="font-mono text-ink">twelvedata</span> / <span className="font-mono text-ink">API_KEY</span> — US / global stocks and forex</li>
          </ul>
          <p className="mt-1">Non-secret settings (SMTP host/sender, bot username, VAPID public key, WhatsApp phone-number id and approved template) come from the server environment. A channel shows “not configured” to users until both are present.</p>
        </div>
        <form className="grid grid-cols-1 gap-3 sm:grid-cols-3" autoComplete="off" onSubmit={async (e) => {
          e.preventDefault(); setError(null); setMsg(null);
          try { const r = await api.admin.addKey(provider.trim(), name.trim(), value); setValue(""); setMsg(`Stored ${r.provider}:${r.name}.`); q.reload(); } catch (err) { setError(err instanceof Error ? err.message : "Failed"); }
        }}>
          <Field label="Provider id" htmlFor="pv" hint="lowercase, e.g. kite"><input id="pv" className="input" pattern="[a-z0-9_]{2,64}" required value={provider} onChange={(e) => setProvider(e.target.value)} /></Field>
          <Field label="Key name" htmlFor="kn" hint="e.g. API_KEY"><input id="kn" className="input" pattern="[A-Za-z0-9_]{2,64}" required value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Secret value" htmlFor="kv"><input id="kv" type="password" className="input" autoComplete="new-password" required maxLength={4096} value={value} onChange={(e) => setValue(e.target.value)} /></Field>
          <div className="sm:col-span-3"><button className="btn-primary" disabled={!provider || !name || !value}>Save key</button> {msg && <span role="status" className="ml-2 text-sm text-green-300">{msg}</span>}</div>
        </form>
        <InlineError error={error} />
      </Card>
    </div>
  );
}

export default function AdminPage() {
  const { user, can } = useAuth();
  const tabs: { value: Tab; label: string; perm: string }[] = [
    { value: "health", label: "Health", perm: "admin:jobs" },
    { value: "jobs", label: "Jobs", perm: "admin:jobs" },
    { value: "scoring", label: "Scoring", perm: "admin:settings" },
    { value: "users", label: "Users", perm: "admin:users" },
    { value: "audit", label: "Audit", perm: "audit:read" },
    { value: "providers", label: "Providers", perm: "admin:providers" },
  ];
  const allowed = tabs.filter((t) => can(t.perm));
  // ?tab= lets redirects (e.g. back from Upstox login) open a specific section
  const [tab, setTab] = useState<Tab>(() => {
    const t = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("tab") : null;
    return (allowed.find((x) => x.value === t)?.value ?? allowed[0]?.value ?? "health") as Tab;
  });
  if (!isAdmin(user) || allowed.length === 0) return <div role="alert" className="rounded-md border border-edge bg-panel p-4 text-sm"><p className="font-semibold">Administrator access required</p><p className="mt-1 text-muted">Your account does not have admin permissions.</p></div>;
  const current = allowed.some((t) => t.value === tab) ? tab : allowed[0].value;
  return (
    <>
      <PageHeader title="Administration" subtitle="System health, jobs, scoring configuration, users and providers." />
      <div className="mb-4"><Segmented<Tab> label="Admin section" value={current} onChange={setTab} options={allowed} /></div>
      {current === "health" && <HealthTab />}
      {current === "jobs" && <JobsTab />}
      {current === "scoring" && <ScoringTab />}
      {current === "users" && <UsersTab />}
      {current === "audit" && <AuditTab />}
      {current === "providers" && <ProvidersTab />}
    </>
  );
}
