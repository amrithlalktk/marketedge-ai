"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { JournalForm, OrderForm, TradeCard } from "@/components/PortfolioParts";
import { TimeChart } from "@/components/TimeChart";
import { Bar, Card, Confirm, EmptyState, ErrorState, Field, InlineError, Loading, PageHeader, Pill, Segmented, Stat, UpgradeNote, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { money, moveClass, num, pct, signedPct } from "@/lib/format";
import type { PaperPortfolio } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const PF_DISCLAIMER = "Paper trading is simulated: no orders reach any broker and no money moves. Simulated results do not guarantee real results.";

function CreatePortfolio({ onCreated }: { onCreated: (id: number) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"paper" | "journal">("paper");
  const [ccy, setCcy] = useState<"INR" | "USD">("INR");
  const [cap, setCap] = useState("1000000");
  const [err, setErr] = useState<string | null>(null);
  const [limit, setLimit] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Card title="New portfolio">
      <form className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto_12rem_10rem_auto] sm:items-end" noValidate onSubmit={async (e) => {
        e.preventDefault(); setErr(null); setLimit(false);
        if (!name.trim()) return setErr("Give it a name.");
        setBusy(true);
        try { const p = await api.portfolios.create(name.trim(), kind, Number(cap), ccy); setName(""); onCreated(p.id); }
        catch (x) { if (x instanceof ApiError && x.status === 403) setLimit(true); setErr(x instanceof Error ? x.message : "Failed"); } finally { setBusy(false); }
      }}>
        <Field label="Name" htmlFor="pf-name"><input id="pf-name" className="input" maxLength={64} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Swing paper account" /></Field>
        <div>
          <span className="label">Type</span>
          <Segmented<"paper" | "journal"> label="Portfolio type" value={kind} onChange={setKind} options={[{ value: "paper", label: "Paper trading" }, { value: "journal", label: "Trade journal" }]} />
        </div>
        <Field label="Currency" htmlFor="pf-ccy">
          <select id="pf-ccy" className="input" value={ccy} onChange={(e) => { const c = e.target.value as "INR" | "USD"; setCcy(c); setCap(c === "USD" ? "10000" : "1000000"); }}>
            <option value="INR">INR — NSE stocks / NIFTY options</option>
            <option value="USD">USD — Crypto</option>
          </select>
        </Field>
        <Field label={`Starting capital (${ccy === "USD" ? "$" : "₹"})`} htmlFor="pf-cap" hint={Number(cap) > 0 ? money(Number(cap), ccy, 0) : undefined}><input id="pf-cap" className="input num" type="number" min={ccy === "USD" ? 100 : 1000} step={ccy === "USD" ? "1000" : "10000"} value={cap} onChange={(e) => setCap(e.target.value)} /></Field>
        <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create"}</button>
      </form>
      <p className="mt-2 text-[11px] text-muted">Paper: simulated orders filled by the engine&apos;s rules. Journal: record your own real trades to analyse them. Standard accounts: one of each. A portfolio holds instruments in its own currency only: INR for Indian stocks and NIFTY options, USD for crypto.</p>
      {limit && <div className="mt-2"><UpgradeNote feature="More than one portfolio of each type" /></div>}
      <InlineError error={err} />
    </Card>
  );
}

function Breakdown({ title, data, currency }: { title: string; data: Record<string, number>; currency: string }) {
  const e = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const total = e.reduce((a, [, v]) => a + Math.abs(v), 0) || 1;
  return (
    <Card title={title}>
      {e.length === 0 ? <p className="text-xs text-muted">No open exposure.</p> : (
        <ul className="space-y-1.5">
          {e.map(([k, v]) => (
            <li key={k} className="grid grid-cols-[7rem_1fr_6.5rem] items-center gap-2 text-xs">
              <span className="truncate text-muted">{k}</span><Bar value={(100 * Math.abs(v)) / total} tone="blue" /><span className="num text-right">{money(v, currency, 0)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Detail({ id, onDeleted }: { id: number; onDeleted: () => void }) {
  const q = useApi<PaperPortfolio>(() => api.portfolios.get(id), [id]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} what="portfolio" />;
  if (!q.data) return <Loading />;
  const p = q.data;
  const a = p.analytics;
  const trades = p.trades ?? [];
  const open = trades.filter((t) => t.status === "open");
  const pending = trades.filter((t) => t.status === "pending");
  const closed = trades.filter((t) => t.status === "closed" || t.status === "cancelled");
  const journal = p.kind === "journal";
  const changed = (m?: string) => { if (m) setMsg(m); q.reload(); };
  const cur = p.base_currency;
  const monthly = Object.entries(a.monthly_pnl).sort();
  const maxM = Math.max(1, ...monthly.map(([, v]) => Math.abs(v)));

  return (
    <div className="space-y-4">
      <Card title={<span className="normal-case tracking-normal text-ink">{p.name}</span>} right={<span className="flex items-center gap-1"><Pill tone={journal ? "blue" : "green"}>{journal ? "journal" : "paper"}</Pill><Confirm label="Delete portfolio" className="btn-ghost px-2 py-1 text-xs" onConfirm={async () => { try { await api.portfolios.remove(p.id); onDeleted(); } catch (x) { setErr(x instanceof Error ? x.message : "Delete failed"); } }}>Delete</Confirm></span>}>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
          <Stat label="Equity" value={money(a.equity, cur, 0)} sub={`start ${money(p.starting_capital, cur, 0)}`} />
          <Stat label="Return" value={signedPct(a.return_pct)} valueClass={moveClass(a.return_pct)} />
          <Stat label="Realised P&L" value={money(a.realized_pnl, cur, 0)} valueClass={moveClass(a.realized_pnl)} />
          <Stat label="Unrealised P&L" value={money(a.unrealized_pnl, cur, 0)} valueClass={moveClass(a.unrealized_pnl)} />
          <Stat label="Open risk" value={money(a.open_risk, cur, 0)} sub={`${pct(a.open_risk_pct, 2)} of equity`} valueClass={a.open_risk ? "text-down" : undefined} />
          <Stat label="Gross exposure" value={money(a.gross_exposure, cur, 0)} sub={`${a.open_positions} open`} />
          <Stat label="Win rate" value={pct(a.win_rate)} sub={`${a.winning_trades}W / ${a.losing_trades}L of ${a.total_trades}`} />
          <Stat label="Profit factor" value={num(a.profit_factor)} />
          <Stat label="Expectancy / trade" value={money(a.expectancy, cur, 0)} valueClass={moveClass(a.expectancy)} />
          <Stat label="Avg winner / loser" value={<><span className="text-up">{money(a.avg_winner, cur, 0)}</span> / <span className="text-down">{money(a.avg_loser, cur, 0)}</span></>} />
          <Stat label="Max drawdown" value={pct(a.max_drawdown_pct, 2)} valueClass="text-down" />
          <Stat label="Sharpe · avg hold" value={`${num(a.sharpe)} · ${a.avg_holding_days != null ? `${num(a.avg_holding_days, 1)}d` : "—"}`} />
        </div>
        <p className="mt-2 text-[11px] text-muted">{a.note} All figures are in the portfolio&apos;s base currency ({p.base_currency}).</p>
        {p.execution_note && <p className="mt-1 rounded border border-amber-800 bg-amber-950/30 px-2 py-1.5 text-xs text-amber-200">ⓘ {p.execution_note}</p>}
        {msg && <p role="status" className="mt-2 text-sm text-green-300">{msg}</p>}
        <InlineError error={err} />
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Equity (realised)" className="lg:col-span-2">
          {a.equity_curve.length < 2 ? <p className="text-sm text-muted">The equity curve appears after the first closed trade.</p> : <TimeChart height={200} label="Portfolio equity" series={[{ name: `Equity (${cur})`, color: "#22c55e", type: "area", data: a.equity_curve }]} />}
        </Card>
        <Card title="Monthly P&L">
          {monthly.length === 0 ? <p className="text-sm text-muted">No closed trades yet.</p> : (
            <ul className="space-y-1">
              {monthly.map(([m, v]) => (
                <li key={m} className="grid grid-cols-[4.5rem_1fr_6rem] items-center gap-2 text-xs">
                  <span className="num text-muted">{m}</span>
                  <span className="h-2 overflow-hidden rounded bg-edge"><span className={cx("block h-full rounded", v >= 0 ? "bg-up" : "bg-down")} style={{ width: `${(100 * Math.abs(v)) / maxM}%` }} /></span>
                  <span className={cx("num text-right", moveClass(v))}>{money(v, cur, 0)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Breakdown title="Exposure by market" data={a.exposure_by_market} currency={p.base_currency} />
        <Breakdown title="Exposure by sector" data={a.exposure_by_sector} currency={p.base_currency} />
      </div>

      <Card title={`Open positions (${open.length})`}>
        {open.length === 0 ? <EmptyState title="No open positions" /> : <div className="space-y-2">{open.map((t) => <TradeCard key={t.id} t={t} pid={p.id} base={cur} journal={journal} onChanged={changed} />)}</div>}
      </Card>
      {!journal && (
        <Card title={`Pending orders (${pending.length})`}>
          {pending.length === 0 ? <EmptyState title="No pending orders" /> : <div className="space-y-2">{pending.map((t) => <TradeCard key={t.id} t={t} pid={p.id} base={cur} journal={journal} onChanged={changed} />)}</div>}
          <p className="mt-2 text-[11px] text-muted">Pending orders fill at the next bar&apos;s open after they were placed; with end-of-day data that is the next session.</p>
        </Card>
      )}
      {journal ? <JournalForm p={p} onAdded={changed} /> : <OrderForm p={p} onPlaced={changed} />}
      <Card title={`Closed & cancelled (${closed.length})`}>
        {closed.length === 0 ? <EmptyState title="No closed trades yet" /> : <div className="space-y-2">{closed.map((t) => <TradeCard key={t.id} t={t} pid={p.id} base={cur} journal={journal} onChanged={changed} />)}</div>}
      </Card>
    </div>
  );
}

function PortfolioInner() {
  const { can } = useAuth();
  const params = useSearchParams();
  const router = useRouter();
  const allowed = can("portfolio:write");
  const list = useApi<{ items: PaperPortfolio[]; disclaimer: string }>(() => api.portfolios.list(), [], allowed);
  if (!allowed) return <UpgradeNote feature="Paper trading" />;
  const items = list.data?.items ?? [];
  const sel = items.find((p) => p.id === Number(params.get("id")))?.id ?? items[0]?.id ?? null;
  const select = (id: number | null) => router.replace(id ? `/portfolio?id=${id}` : "/portfolio", { scroll: false });

  return (
    <>
      <PageHeader title="Portfolio & journal" subtitle="Paper-trade setups with the backtest's execution rules, or journal your real trades." />
      <p role="note" className="mb-4 rounded-md border border-blue-800 bg-blue-950/40 px-3 py-2 text-xs text-blue-100">ⓘ {list.data?.disclaimer ?? PF_DISCLAIMER}</p>
      {list.error ? <ErrorState error={list.error} onRetry={list.reload} what="portfolios" /> : !list.data ? <Loading /> : (
        <div className="space-y-4">
          {items.length > 0 && (
            <nav aria-label="Portfolios" className="flex flex-wrap gap-1.5">
              {items.map((p) => (
                <button key={p.id} type="button" aria-current={p.id === sel ? "true" : undefined} onClick={() => select(p.id)}
                  className={cx("rounded border px-2.5 py-1.5 text-left text-xs", p.id === sel ? "border-accent bg-blue-950/40 text-ink" : "border-edge text-muted hover:bg-panel2")}>
                  <span className="block font-semibold">{p.name} <span className="font-normal">· {p.kind} · {p.base_currency}</span></span>
                  <span className="num block"><span className={moveClass(p.analytics.return_pct)}>{signedPct(p.analytics.return_pct)}</span> · {money(p.analytics.equity, p.base_currency, 0)}</span>
                </button>
              ))}
            </nav>
          )}
          {items.length === 0 && <EmptyState title="No portfolios yet">Create a paper portfolio to practise setups, or a journal to record your own trades.</EmptyState>}
          {sel != null && <Detail key={sel} id={sel} onDeleted={() => { list.reload(); select(null); }} />}
          <CreatePortfolio onCreated={(id) => { list.reload(); select(id); }} />
        </div>
      )}
      <aside aria-label="Disclaimer" className="mt-6 rounded-md border border-edge bg-panel/60 p-3 text-xs text-muted">
        <strong className="text-ink">Disclaimer: </strong>{PF_DISCLAIMER} Historical/backtested performance and probability estimates do not guarantee future results. Market conditions can change rapidly. This platform provides analytical information and does not guarantee profits.
      </aside>
    </>
  );
}

export default function PortfolioPage() {
  return (
    <Suspense>
      <PortfolioInner />
    </Suspense>
  );
}
