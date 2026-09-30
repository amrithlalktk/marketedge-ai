"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { StrategyBuilder } from "@/components/StrategyBuilder";
import { StrategyPerf } from "@/components/StrategyPerf";
import { Card, Confirm, DirectionBadge, Disclaimer, EmptyState, ErrorState, Field, InlineError, Loading, PageHeader, Pill, TableWrap, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fromDefinition, type BuilderState } from "@/lib/dsl";
import { dateTime } from "@/lib/format";
import type { BuiltinStrategy, CustomStrategy, StrategiesResponse, StrategyDefinitionV2, StrategyVersionDetail } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function JsonDiff({ left, right, leftLabel, rightLabel }: { left: unknown; right: unknown; leftLabel: string; rightLabel: string }) {
  const a = JSON.stringify(left, null, 2).split("\n");
  const b = JSON.stringify(right, null, 2).split("\n");
  const sa = new Set(a.map((l) => l.trim()));
  const sb = new Set(b.map((l) => l.trim()));
  const pane = (lines: string[], other: Set<string>, tone: "red" | "green", label: string) => (
    <div className="min-w-0">
      <p className="mb-1 text-xs font-semibold text-muted">{label}</p>
      <pre className="max-h-96 overflow-auto rounded border border-edge bg-bg p-2 text-[11px] leading-4" tabIndex={0} aria-label={label}>
        {lines.map((l, i) => (
          <div key={i} className={cx(!other.has(l.trim()) && (tone === "red" ? "bg-red-950/70 text-red-200" : "bg-green-950/70 text-green-200"))}>{l || " "}</div>
        ))}
      </pre>
    </div>
  );
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {pane(a, sb, "red", leftLabel)}
      {pane(b, sa, "green", rightLabel)}
    </div>
  );
}

function Detail({ s, features, operators, onChanged }: { s: CustomStrategy; features: string[]; operators: string[]; onChanged: () => void }) {
  const { can } = useAuth();
  const manage = can("strategies:manage");
  const [view, setView] = useState<StrategyVersionDetail | null>(null);
  const [cur, setCur] = useState<StrategyVersionDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [builder, setBuilder] = useState<BuilderState>(() => fromDefinition(s.definition));
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [inScan, setInScan] = useState(s.include_in_scan);
  useEffect(() => setInScan(s.include_in_scan), [s.include_in_scan]);

  useEffect(() => {
    setBuilder(fromDefinition(s.definition));
    setView(null);
    setEditing(false);
    api.strategies.version(s.id, s.current_version).then(setCur).catch(() => setCur(null));
  }, [s.id, s.current_version, s.definition]);

  const act = async (fn: () => Promise<string | void>) => {
    setBusy(true); setError(null); setMsg(null);
    try { const m = await fn(); if (m) setMsg(m); onChanged(); } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); } finally { setBusy(false); }
  };

  const saveVersion = (d: StrategyDefinitionV2) => act(async () => {
    const r = await api.strategies.newVersion(s.id, d, note);
    setEditing(false);
    setNote("");
    return `Saved v${r.current_version}. Earlier versions are unchanged and can still be backtested.`;
  });

  return (
    <div className="space-y-4">
      <Card title={<span className="normal-case tracking-normal text-ink">{s.name}</span>} right={<span className="flex flex-wrap gap-1"><DirectionBadge d={s.direction} /><Pill tone="blue">v{s.current_version}</Pill>{!s.is_active && <Pill tone="red">inactive</Pill>}</span>}>
        <p className="font-mono text-xs text-muted">{s.id}</p>
        <ul className="mt-2 space-y-0.5 font-mono text-xs">{s.conditions.map((c) => <li key={c}>• {c}</li>)}</ul>
        <dl className="mt-2 space-y-0.5 text-xs">
          <div><dt className="inline text-muted">Entry: </dt><dd className="inline">{s.entry_rule}</dd></div>
          <div><dt className="inline text-muted">Stop: </dt><dd className="inline">{s.stop_rule}</dd></div>
          <div><dt className="inline text-muted">Targets: </dt><dd className="inline">{s.target_rule}</dd></div>
          <div><dt className="inline text-muted">Max hold: </dt><dd className="inline">{s.max_hold_bars} bars</dd></div>
        </dl>
        <div className="mt-3 flex flex-wrap gap-2">
          <Link className="btn-primary" href={`/backtest?strategy=${encodeURIComponent(s.id)}&version=${s.current_version}`}>Backtest v{s.current_version}</Link>
          <Link className="btn-ghost" href={`/backtest?load=${encodeURIComponent(s.id)}`}>Open in backtest builder</Link>
          {manage && s.is_active && <button type="button" className="btn-ghost" aria-expanded={editing} onClick={() => setEditing((e) => !e)}>{editing ? "Cancel edit" : "Edit → new version"}</button>}
        </div>

        {manage && (
          <div className="mt-4 rounded border border-edge p-3">
            <label className="inline-flex items-center gap-2 text-sm">
              <input type="checkbox" disabled={busy || !s.is_active} checked={inScan}
                onChange={(e) => {
                  const next = e.target.checked;
                  setInScan(next);
                  act(async () => {
                    try {
                      const r = await api.strategies.patch(s.id, { include_in_scan: next });
                      setInScan(r.include_in_scan);
                      return `${r.include_in_scan ? "Included in" : "Removed from"} the daily scan. ${r.note}`;
                    } catch (err) {
                      setInScan(!next);
                      throw err;
                    }
                  });
                }} />
              Include in daily scan
            </label>
            <p className="mt-1 text-[11px] text-muted">Applies from the next scan. Its setups then carry the same historical evidence, sample sizes and NO TRADE validation checks as built-in strategies.</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {s.is_active ? (
                <Confirm label="Deactivate" className="btn-danger px-2 py-1 text-xs" onConfirm={() => act(async () => { await api.strategies.deactivate(s.id); return "Strategy deactivated and removed from the scan. History is kept."; })}>Deactivate strategy</Confirm>
              ) : (
                <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={busy} onClick={() => act(async () => { await api.strategies.patch(s.id, { is_active: true }); return "Strategy reactivated (not in scan)."; })}>Reactivate</button>
              )}
            </div>
          </div>
        )}
        {msg && <p role="status" className="mt-2 text-sm text-green-300">{msg}</p>}
        <InlineError error={error} />
      </Card>

      {editing && (
        <Card title={`New version (v${s.current_version + 1})`}>
          <StrategyBuilder state={builder} setState={setBuilder} features={features} operators={operators} busy={busy}
            actions={[{ label: busy ? "Saving…" : `Save as v${s.current_version + 1}`, primary: true, onClick: saveVersion }]} />
          <div className="mt-3 max-w-xl"><Field label="Version note" htmlFor="vn"><input id="vn" className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changed and why" /></Field></div>
          <p className="mt-2 text-[11px] text-muted">Versions are immutable: saving creates v{s.current_version + 1} and makes it current; v1…v{s.current_version} stay available for backtests and audit.</p>
        </Card>
      )}

      <Card title={`Version history (${s.versions.length})`}>
        <TableWrap label="Version history">
          <table className="tbl min-w-[520px] text-sm">
            <thead><tr><th scope="col">Version</th><th scope="col">Created</th><th scope="col">Note</th><th scope="col">Actions</th></tr></thead>
            <tbody>
              {[...s.versions].reverse().map((v) => (
                <tr key={v.version} className={cx(view?.version === v.version && "bg-panel2")}>
                  <td className="num">v{v.version}{v.version === s.current_version && <span className="ml-1"><Pill tone="green">current</Pill></span>}</td>
                  <td className="num text-xs">{dateTime(v.created_at)}</td>
                  <td className="text-xs">{v.note || <span className="text-muted">—</span>}</td>
                  <td className="whitespace-nowrap">
                    <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => api.strategies.version(s.id, v.version).then(setView).catch((e) => setError(e.message))}>View / diff</button>{" "}
                    <Link className="btn-ghost px-2 py-1 text-xs" href={`/backtest?strategy=${encodeURIComponent(s.id)}&version=${v.version}`}>Backtest</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
        {view && (
          <div className="mt-3">
            {cur && view.version !== cur.version ? (
              <JsonDiff left={view.definition} right={cur.definition} leftLabel={`v${view.version} (${dateTime(view.created_at)})`} rightLabel={`v${cur.version} — current`} />
            ) : (
              <>
                <p className="mb-1 text-xs font-semibold text-muted">v{view.version} definition{view.version === s.current_version ? " (current)" : ""}</p>
                <pre className="max-h-96 overflow-auto rounded border border-edge bg-bg p-2 text-[11px]" tabIndex={0}>{JSON.stringify(view.definition, null, 2)}</pre>
              </>
            )}
            <p className="mt-1 text-[11px] text-muted">Highlighted lines differ between the two versions.</p>
          </div>
        )}
      </Card>

      <StrategyPerf s={s} custom={{ key: s.id, version: s.current_version, inScan: s.include_in_scan }} />
    </div>
  );
}

function StrategiesInner() {
  const { can } = useAuth();
  const params = useSearchParams();
  const router = useRouter();
  const q = useApi<StrategiesResponse>(() => api.strategies.list(), []);
  const sel = params.get("key");
  const custom = q.data?.custom ?? [];
  const selected = custom.find((c) => c.id === sel) ?? null;

  return (
    <>
      <PageHeader title="Strategies" subtitle="Saved, versioned custom strategies and the built-in library." right={<Link href="/backtest?mode=builder" className="btn-primary">+ New strategy in builder</Link>} />
      {!can("strategies:manage") && <p className="mb-3 rounded border border-edge bg-panel2/60 px-3 py-2 text-xs text-muted">Read-only: creating versions and changing scan inclusion requires the Analyst role.</p>}
      {q.error && <ErrorState error={q.error} onRetry={q.reload} what="strategies" />}
      {q.loading && !q.data && <Loading />}
      {q.data && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Card title={`Custom strategies (${custom.length})`}>
            {custom.length === 0 ? <EmptyState title="No custom strategies yet"><Link className="link" href="/backtest?mode=builder">Build one →</Link></EmptyState> : (
              <ul className="divide-y divide-edge text-sm">
                {custom.map((c) => (
                  <li key={c.id}>
                    <button type="button" aria-current={c.id === sel ? "true" : undefined} className={cx("flex w-full items-center justify-between gap-2 px-1 py-2 text-left hover:bg-panel2", c.id === sel && "bg-panel2")} onClick={() => router.replace(`/strategies?key=${encodeURIComponent(c.id)}`, { scroll: false })}>
                      <span className="min-w-0"><span className="block truncate font-medium">{c.name}</span><span className="block font-mono text-[11px] text-muted">{c.id} · v{c.current_version}</span></span>
                      <span className="flex shrink-0 flex-wrap justify-end gap-1"><DirectionBadge d={c.direction} />{c.include_in_scan && <Pill tone="green">scan</Pill>}{!c.is_active && <Pill tone="red">off</Pill>}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <div className="min-w-0 lg:col-span-2">
            {selected ? (
              <Detail s={selected} features={q.data.allowed_features} operators={q.data.operators} onChanged={q.reload} />
            ) : (
              <Card title="Built-in strategies">
                <p className="mb-2 text-xs text-muted">Select a custom strategy to view versions, diff definitions, backtest a specific version or change scan inclusion.</p>
                <ul className="space-y-2 text-sm">
                  {q.data.builtin.map((b) => (
                    <li key={b.id} className="rounded border border-edge p-2">
                      <span className="flex flex-wrap items-center gap-2 font-medium">{b.name} <DirectionBadge d={b.direction} /></span>
                      <span className="block text-xs text-muted">{b.description}</span>
                      {Object.entries(b.disabled_markets ?? {}).map(([m, d]) => (
                        <span key={m} className="mt-1 block text-xs">
                          <Pill tone="red">off in {m}</Pill> <span className="text-muted">{d.reason || "admin decision"} · still backtested every scan</span>
                        </span>
                      ))}
                      <span className="mt-1 flex flex-wrap items-center gap-3">
                        <Link className="link text-xs" href={`/backtest?strategy=${b.id}`}>Backtest →</Link>
                        {can("admin:settings") && <MarketSwitch strategy={b} onChanged={q.reload} />}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        </div>
      )}
      <Disclaimer />
    </>
  );
}

const MARKETS = ["NSE", "CRYPTO", "US", "EUROPE", "ASIA", "FX"];

function MarketSwitch({ strategy, onChanged }: { strategy: BuiltinStrategy; onChanged: () => void }) {
  const [market, setMarket] = useState("CRYPTO");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const off = Boolean(strategy.disabled_markets?.[market]);
  async function toggle() {
    setBusy(true);
    setErr(null);
    try {
      await api.strategies.setMarketEnabled(market, strategy.id, off, reason);
      setReason("");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="flex flex-wrap items-center gap-1 text-xs">
      <select className="input py-0.5 text-xs" aria-label={`Market for ${strategy.name}`} value={market} onChange={(e) => setMarket(e.target.value)}>
        {MARKETS.map((m) => <option key={m} value={m}>{m}</option>)}
      </select>
      {!off && (
        <input className="input w-44 py-0.5 text-xs" placeholder="Reason (shown on setups)" aria-label="Reason" maxLength={500}
          value={reason} onChange={(e) => setReason(e.target.value)} />
      )}
      <button className="btn-ghost px-2 py-0.5 text-xs" disabled={busy} onClick={toggle}>{off ? `Enable in ${market}` : `Disable in ${market}`}</button>
      <InlineError error={err} />
    </span>
  );
}

export default function StrategiesPage() {
  return (
    <Suspense>
      <StrategiesInner />
    </Suspense>
  );
}
