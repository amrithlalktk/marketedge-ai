"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { dateTime, integer, money, moveClass, num, price } from "@/lib/format";
import type { Direction, PaperPortfolio, PaperTrade, StockDetail } from "@/lib/types";
import { InstrumentSearch } from "./InstrumentSearch";
import { Card, Confirm, DirectionBadge, Field, InlineError, Pill, Segmented, cx } from "./ui";

export const tp = (v: number | null | undefined, t: { currency?: string | null; market?: string }) => price(v, t.currency ?? "INR");

/** Error text for a refused trade; 422 = the portfolio refused it (e.g. a USD crypto trade in an INR portfolio). */
export const refusal = (x: unknown, fallback: string) =>
  x instanceof ApiError && x.status === 422 ? `Refused: ${x.message}` : x instanceof Error ? x.message : fallback;

const STATUS_TONE: Record<string, "green" | "amber" | "slate" | "red" | "blue"> = { open: "green", pending: "amber", closed: "slate", cancelled: "red" };

function LevelEdit({ label, value, onSave, t, cls }: { label: string; value: number | null; onSave: (v: number) => Promise<boolean>; t: PaperTrade; cls?: string }) {
  const [edit, setEdit] = useState(false);
  const [v, setV] = useState(value != null ? String(value) : "");
  const [busy, setBusy] = useState(false);
  if (!edit) {
    return (
      <div className="min-w-0">
        <dt className="text-[10px] uppercase tracking-wide text-muted">{label}</dt>
        <dd className={cx("num text-sm", cls)}>
          {tp(value, t)}{" "}
          <button type="button" className="text-[10px] text-accent hover:underline" aria-label={`Edit ${label} for ${t.symbol}`} onClick={() => setEdit(true)}>edit</button>
        </dd>
      </div>
    );
  }
  return (
    <form className="min-w-0" onSubmit={async (e) => { e.preventDefault(); if (!(Number(v) > 0)) return; setBusy(true); try { if (await onSave(Number(v))) setEdit(false); } finally { setBusy(false); } }}>
      <label className="text-[10px] uppercase tracking-wide text-muted" htmlFor={`le-${t.id}-${label}`}>{label}</label>
      <div className="flex gap-1">
        <input id={`le-${t.id}-${label}`} className="input num px-1 py-0.5 text-xs" type="number" step="any" value={v} onChange={(e) => setV(e.target.value)} autoFocus />
        <button className="btn-primary px-1.5 py-0.5 text-[11px]" disabled={busy}>✓</button>
        <button type="button" className="btn-ghost px-1.5 py-0.5 text-[11px]" onClick={() => setEdit(false)}>✕</button>
      </div>
    </form>
  );
}

export function TradeCard({ t, pid, base = "INR", journal, onChanged }: { t: PaperTrade; pid: number; base?: string; journal: boolean; onChanged: (msg?: string) => void }) {
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const active = t.status === "open" || t.status === "pending";
  const patch = async (k: "stop" | "target1" | "target2", v: number) => {
    setErr(null);
    const nx = { stop: t.stop ?? undefined, target1: t.target1 ?? undefined, target2: t.target2 ?? undefined, [k]: v };
    const probs = levelProblems(t.direction, t.filled_price ?? t.limit_price ?? t.last_price, nx.stop, nx.target1, nx.target2);
    if (probs.length) { setErr(probs.join(" ")); return false; }
    try { await api.portfolios.patchTrade(pid, t.id, { [k]: v }); onChanged(`${t.symbol}: ${k === "stop" ? "stop" : k === "target1" ? "T1" : "T2"} updated.`); return true; } catch (e) { setErr(e instanceof Error ? e.message : "Update failed"); return false; }
  };
  const close = async () => {
    setErr(null);
    try { const r = await api.portfolios.closeTrade(pid, t.id); onChanged(`${t.symbol}: ${r.note}`); } catch (e) { setErr(e instanceof Error ? e.message : "Close failed"); }
  };
  const entry = t.filled_price ?? t.limit_price;
  const pnlBase = t.status === "closed" ? t.realized_base : t.unrealized_base;
  const pnlCcy = t.status === "closed" ? t.realized : t.unrealized;
  return (
    <article className="rounded-lg border border-edge bg-panel2/30 p-3" aria-labelledby={`tr-${t.id}`}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <Link id={`tr-${t.id}`} href={`/stocks/${encodeURIComponent(t.symbol)}`} className="link font-mono font-semibold">{t.symbol}</Link>
            <DirectionBadge d={t.direction} />
            <Pill tone={STATUS_TONE[t.status] ?? "slate"}>{t.status}</Pill>
            {t.close_requested && active && <Pill tone="amber">close requested</Pill>}
            {t.signal_id && <Link href={`/setups/${t.signal_id}`} className="text-[11px] text-accent hover:underline">setup #{t.signal_id}</Link>}
          </div>
          <p className="text-[11px] text-muted">{t.market} · {t.currency} · {t.order_type}{t.order_type !== "market" && t.order_type !== "journal" && t.limit_price != null ? ` @ ${tp(t.limit_price, t)}` : ""} · qty {num(t.quantity, t.quantity % 1 ? 4 : 0)}{t.status === "open" && t.open_quantity !== t.quantity ? ` (open ${num(t.open_quantity, t.open_quantity % 1 ? 4 : 0)})` : ""}</p>
        </div>
        <div className="text-right">
          {t.status !== "pending" && t.status !== "cancelled" && (
            <>
              <div className={cx("num text-sm font-semibold", moveClass(pnlBase))}>{money(pnlBase, base, 0)}</div>
              <div className="text-[10px] text-muted">{t.status === "closed" ? "realised" : "unrealised"}{t.currency !== base && <> · <span className={moveClass(pnlCcy)}>{tp(pnlCcy, t)}</span> in {t.currency}</>}</div>
            </>
          )}
        </div>
      </header>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 min-[420px]:grid-cols-3 lg:grid-cols-6">
        <div><dt className="text-[10px] uppercase tracking-wide text-muted">Entry</dt><dd className="num text-sm">{entry != null ? tp(entry, t) : t.status === "pending" ? "next open" : "—"}</dd>{t.filled_at && <dd className="text-[10px] text-muted">{t.filled_at.slice(0, 16)}</dd>}</div>
        <div><dt className="text-[10px] uppercase tracking-wide text-muted">{t.status === "closed" ? "Exit" : "Last price"}</dt><dd className="num text-sm">{t.status === "closed" ? tp(t.exit_price, t) : tp(t.last_price, t)}</dd><dd className="text-[10px] text-muted">{t.status === "closed" ? `${t.closed_at?.slice(0, 16) ?? ""} · ${t.exit_reason ?? ""}` : t.last_price_at ? `as of ${t.last_price_at}` : ""}</dd></div>
        {active && !journal ? (
          <>
            <LevelEdit label="Stop" value={t.stop} t={t} cls="text-down" onSave={(v) => patch("stop", v)} />
            <LevelEdit label="T1" value={t.target1} t={t} cls="text-up" onSave={(v) => patch("target1", v)} />
            <LevelEdit label="T2" value={t.target2} t={t} cls="text-up" onSave={(v) => patch("target2", v)} />
          </>
        ) : (
          <>
            <div><dt className="text-[10px] uppercase tracking-wide text-muted">Stop</dt><dd className="num text-sm text-down">{tp(t.stop, t)}</dd></div>
            <div><dt className="text-[10px] uppercase tracking-wide text-muted">T1 / T2</dt><dd className="num text-sm text-up">{tp(t.target1, t)} / {tp(t.target2, t)}</dd></div>
          </>
        )}
        <div><dt className="text-[10px] uppercase tracking-wide text-muted">Costs</dt><dd className="num text-sm">{tp(t.costs + t.brokerage + t.taxes, t)}</dd><dd className="text-[10px] text-muted">{t.holding_days != null ? `${t.holding_days} days held` : ""}</dd></div>
      </dl>
      {t.notes && <p className="mt-1 text-[11px] text-muted">“{t.notes}”</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {active && !journal && (
          <Confirm label={t.status === "pending" ? "Cancel order" : "Close at next open"} className="btn-ghost px-2 py-1 text-xs" onConfirm={close}>
            {t.status === "pending" ? "Cancel" : "Close position"}
          </Confirm>
        )}
        {t.fills.length > 0 && <button type="button" className="text-xs text-accent hover:underline" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Show"} fills ({t.fills.length})</button>}
      </div>
      {open && (
        <ol className="mt-2 space-y-1 border-l border-edge pl-3 text-xs">
          {t.fills.map((f, i) => (
            <li key={i}><span className="font-semibold">{f.kind}</span> · {dateTime(f.at)} · <span className="num">{num(f.quantity, f.quantity % 1 ? 4 : 0)}</span> @ <span className="num">{tp(f.price, t)}</span>{f.fee ? <> · fee <span className="num">{tp(f.fee, t)}</span></> : null}</li>
          ))}
        </ol>
      )}
      <InlineError error={err} />
    </article>
  );
}

type OT = "market" | "limit" | "stop";

/** Side-of-entry checks mirroring the backend validator. */
export function levelProblems(dir: Direction, ref: number | null, stop?: number, t1?: number, t2?: number): string[] {
  const out: string[] = [];
  if (ref == null) return out;
  const s = dir === "LONG" ? 1 : -1;
  if (stop != null && s * (ref - stop) <= 0) out.push(`Stop must be ${dir === "LONG" ? "below" : "above"} the entry (${num(ref, 4)}).`);
  for (const [k, t] of [["T1", t1], ["T2", t2]] as const) if (t != null && s * (t - ref) <= 0) out.push(`${k} must be ${dir === "LONG" ? "above" : "below"} the entry.`);
  if (t1 != null && t2 != null && s * (t2 - t1) <= 0) out.push("T2 must be beyond T1.");
  return out;
}

export function OrderForm({ p, onPlaced }: { p: PaperPortfolio; onPlaced: (msg: string) => void }) {
  const [inst, setInst] = useState<StockDetail | null>(null);
  const [dir, setDir] = useState<Direction>("LONG");
  const [qty, setQty] = useState("");
  const [ot, setOt] = useState<OT>("market");
  const [lim, setLim] = useState("");
  const [stop, setStop] = useState("");
  const [t1, setT1] = useState("");
  const [t2, setT2] = useState("");
  const [partial, setPartial] = useState("0");
  const [hold, setHold] = useState("");
  const [notes, setNotes] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const n = (x: string) => (x.trim() === "" ? undefined : Number(x));
  const last = inst?.quote?.price ?? null;
  const ref = ot === "market" ? last : n(lim) ?? null;
  const problems = levelProblems(dir, ref, n(stop), n(t1), n(t2));
  const risk = ref != null && n(stop) != null && n(qty) ? Math.abs(ref - (n(stop) as number)) * (n(qty) as number) : null;
  const ccy = inst?.currency ?? "INR";
  const riskPctEquity = risk != null && ccy === p.base_currency ? (100 * risk) / p.analytics.equity : null;
  const sized = ccy === p.base_currency && ref != null && n(stop) != null && Math.abs(ref - (n(stop) as number)) > 0 ? Math.floor((0.01 * p.analytics.equity) / Math.abs(ref - (n(stop) as number))) : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!inst) return setErr("Pick an instrument.");
    if (!(Number(qty) > 0)) return setErr("Enter a quantity.");
    if (ot !== "market" && !(Number(lim) > 0)) return setErr("Limit/stop orders need a price.");
    if (problems.length) return setErr(problems.join(" "));
    setBusy(true);
    try {
      const r = await api.portfolios.order(p.id, {
        symbol: inst.symbol, direction: dir, quantity: Number(qty), order_type: ot, ...(ot !== "market" ? { limit_price: Number(lim) } : {}),
        ...(n(stop) ? { stop: n(stop) } : {}), ...(n(t1) ? { target1: n(t1) } : {}), ...(n(t2) ? { target2: n(t2) } : {}),
        partial_at_t1: Math.min(0.99, Math.max(0, Number(partial) / 100)), ...(n(hold) ? { max_hold_bars: Math.round(n(hold) as number) } : {}), notes,
      });
      onPlaced(`Order #${r.trade_id} ${r.status}. ${r.note}`);
      setQty(""); setNotes("");
    } catch (x) { setErr(refusal(x, "Order rejected")); } finally { setBusy(false); }
  };

  return (
    <Card title="New paper order">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <InstrumentSearch label="Instrument" onPick={async (i) => { try { setInst(await api.stocks.get(i.symbol)); } catch (x) { setErr(x instanceof Error ? x.message : "Lookup failed"); } }} />
        {inst && <p className="text-xs text-muted"><span className="font-mono font-semibold text-ink">{inst.symbol}</span> · {inst.name} · {inst.market} · {inst.currency} · last <span className="num text-ink">{tp(last, inst)}</span> {inst.quote?.as_of && `(as of ${inst.quote.as_of})`}</p>}
        <div className="flex flex-wrap gap-2">
          <Segmented<Direction> label="Direction" value={dir} onChange={setDir} options={[{ value: "LONG", label: "Long" }, { value: "SHORT", label: "Short" }]} />
          <Segmented<OT> label="Order type" value={ot} onChange={setOt} options={[{ value: "market", label: "Market" }, { value: "limit", label: "Limit" }, { value: "stop", label: "Stop" }]} />
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Quantity" htmlFor="o-q" hint={sized != null ? <button type="button" className="text-accent hover:underline" onClick={() => setQty(String(sized))}>Size at 1% risk → {integer(sized)}</button> : undefined}>
            <input id="o-q" className="input num" type="number" step="any" min={0} value={qty} onChange={(e) => setQty(e.target.value)} />
          </Field>
          {ot !== "market" && <Field label={ot === "limit" ? "Limit price" : "Stop-entry price"} htmlFor="o-l"><input id="o-l" className="input num" type="number" step="any" value={lim} onChange={(e) => setLim(e.target.value)} /></Field>}
          <Field label="Stop" htmlFor="o-s"><input id="o-s" className="input num" type="number" step="any" value={stop} onChange={(e) => setStop(e.target.value)} /></Field>
          <Field label="Target 1" htmlFor="o-t1"><input id="o-t1" className="input num" type="number" step="any" value={t1} onChange={(e) => setT1(e.target.value)} /></Field>
          <Field label="Target 2" htmlFor="o-t2"><input id="o-t2" className="input num" type="number" step="any" value={t2} onChange={(e) => setT2(e.target.value)} /></Field>
          <Field label="Partial exit at T1 (%)" htmlFor="o-p"><input id="o-p" className="input num" type="number" min={0} max={99} step="5" value={partial} onChange={(e) => setPartial(e.target.value)} /></Field>
          <Field label="Max hold (bars)" htmlFor="o-h"><input id="o-h" className="input num" type="number" min={1} max={250} step="1" value={hold} onChange={(e) => setHold(e.target.value)} /></Field>
        </div>
        <Field label="Notes" htmlFor="o-n"><input id="o-n" className="input" maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        {problems.length > 0 && <ul className="text-xs text-amber-300">{problems.map((x) => <li key={x}>⚠ {x}</li>)}</ul>}
        <div className="rounded border border-edge bg-panel2/40 p-2 text-xs" aria-live="polite">
          {risk != null ? (
            <>Risk preview: {num(Number(qty), Number(qty) % 1 ? 4 : 0)} × |{tp(ref, { currency: ccy, market: inst?.market })} − {tp(n(stop), { currency: ccy, market: inst?.market })}| = <span className="num font-semibold text-down">{tp(risk, { currency: ccy, market: inst?.market })}</span>{riskPctEquity != null && <> ({num(riskPctEquity, 2)}% of equity)</>}{ot === "market" && " · uses the last price; the real fill is the next bar's open"}</>
          ) : <span className="text-muted">Enter quantity and stop to see the risk. </span>}{" "}
          <Link className="link" href="/risk">Open the position sizer →</Link>
        </div>
        <p className="rounded border border-amber-800 bg-amber-950/30 px-2 py-1.5 text-xs text-amber-200">ⓘ Orders fill at the <strong>next bar&apos;s open</strong> (end-of-day data), the same rule as the backtests. A new order normally shows <strong>pending</strong> until the next session is ingested.</p>
        <InlineError error={err} />
        <button className="btn-primary" disabled={busy || !inst}>{busy ? "Placing…" : "Place paper order"}</button>
      </form>
    </Card>
  );
}

export function JournalForm({ p, onAdded }: { p: PaperPortfolio; onAdded: (msg: string) => void }) {
  const [inst, setInst] = useState<StockDetail | null>(null);
  const [dir, setDir] = useState<Direction>("LONG");
  const [f, setF] = useState({ qty: "", entry: "", entryAt: "", exit: "", exitAt: "", stop: "", t1: "", t2: "", brokerage: "0", taxes: "0", notes: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF((p0) => ({ ...p0, [k]: e.target.value }));
  const n = (x: string) => (x.trim() === "" ? undefined : Number(x));
  useEffect(() => setErr(null), [f]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inst) return setErr("Pick an instrument.");
    if (!(Number(f.qty) > 0) || !(Number(f.entry) > 0) || !f.entryAt) return setErr("Quantity, entry price and entry time are required.");
    if ((f.exit === "") !== (f.exitAt === "")) return setErr("Exit price and exit time go together.");
    if (f.exitAt && f.exitAt < f.entryAt) return setErr("Exit must be after entry.");
    setBusy(true);
    try {
      const r = await api.portfolios.journal(p.id, {
        symbol: inst.symbol, direction: dir, quantity: Number(f.qty), entry_price: Number(f.entry), entry_at: f.entryAt,
        ...(f.exit ? { exit_price: Number(f.exit), exit_at: f.exitAt } : {}), ...(n(f.stop) ? { stop: n(f.stop) } : {}), ...(n(f.t1) ? { target1: n(f.t1) } : {}), ...(n(f.t2) ? { target2: n(f.t2) } : {}),
        brokerage: Number(f.brokerage) || 0, taxes: Number(f.taxes) || 0, notes: f.notes,
      });
      onAdded(`Journal trade #${r.trade_id} recorded (${r.status}).`);
      setF((p0) => ({ ...p0, qty: "", entry: "", exit: "", exitAt: "", notes: "" }));
    } catch (x) { setErr(refusal(x, "Could not record")); } finally { setBusy(false); }
  };
  return (
    <Card title="Record a trade (journal)">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <InstrumentSearch label="Instrument" onPick={async (i) => { try { setInst(await api.stocks.get(i.symbol)); } catch (x) { setErr(x instanceof Error ? x.message : "Lookup failed"); } }} />
        {inst && <p className="text-xs text-muted"><span className="font-mono font-semibold text-ink">{inst.symbol}</span> · {inst.currency} · last {tp(inst.quote?.price, inst)}</p>}
        <Segmented<Direction> label="Direction" value={dir} onChange={setDir} options={[{ value: "LONG", label: "Long" }, { value: "SHORT", label: "Short" }]} />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Quantity" htmlFor="j-q"><input id="j-q" className="input num" type="number" step="any" value={f.qty} onChange={set("qty")} /></Field>
          <Field label="Entry price" htmlFor="j-e"><input id="j-e" className="input num" type="number" step="any" value={f.entry} onChange={set("entry")} /></Field>
          <Field label="Entry time" htmlFor="j-ea"><input id="j-ea" className="input" type="datetime-local" value={f.entryAt} onChange={set("entryAt")} /></Field>
          <Field label="Exit price (optional)" htmlFor="j-x"><input id="j-x" className="input num" type="number" step="any" value={f.exit} onChange={set("exit")} /></Field>
          <Field label="Exit time" htmlFor="j-xa"><input id="j-xa" className="input" type="datetime-local" value={f.exitAt} onChange={set("exitAt")} /></Field>
          <Field label="Stop" htmlFor="j-s"><input id="j-s" className="input num" type="number" step="any" value={f.stop} onChange={set("stop")} /></Field>
          <Field label="T1" htmlFor="j-t1"><input id="j-t1" className="input num" type="number" step="any" value={f.t1} onChange={set("t1")} /></Field>
          <Field label="T2" htmlFor="j-t2"><input id="j-t2" className="input num" type="number" step="any" value={f.t2} onChange={set("t2")} /></Field>
          <Field label="Brokerage" htmlFor="j-b"><input id="j-b" className="input num" type="number" min={0} step="any" value={f.brokerage} onChange={set("brokerage")} /></Field>
          <Field label="Taxes" htmlFor="j-tx"><input id="j-tx" className="input num" type="number" min={0} step="any" value={f.taxes} onChange={set("taxes")} /></Field>
        </div>
        <Field label="Notes" htmlFor="j-n"><input id="j-n" className="input" maxLength={1000} value={f.notes} onChange={set("notes")} /></Field>
        <InlineError error={err} />
        <button className="btn-primary" disabled={busy || !inst}>{busy ? "Saving…" : "Record trade"}</button>
      </form>
    </Card>
  );
}
