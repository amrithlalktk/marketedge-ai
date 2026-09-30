"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime } from "@/lib/format";
import type { AnalystAnswer, AnalystHistoryItem } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Card, Collapsible, EmptyState, ErrorState, InlineError, Loading, Pill, UpgradeNote, cx } from "./ui";

const PRESETS = ["Why is this setup appearing?", "What could invalidate it?", "What are the risks?", "How strong is the historical evidence?"];
const MAX = 1000;

/** Inline **bold** only; everything is rendered as text nodes (no HTML injection). */
function inline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") && p.length > 4 ? <strong key={i}>{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>));
}

/** Minimal markdown: "## " / "### " headings, "- " / "* " / "1. " bullets, other lines as paragraphs. */
export function SimpleMarkdown({ text }: { text: string }) {
  const blocks: React.ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={`u${blocks.length}`} className="ml-4 list-disc space-y-0.5">{list.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>);
      list = [];
    }
  };
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trimEnd();
    if (/^\s*([-*•]|\d+\.)\s+/.test(line)) {
      list.push(line.replace(/^\s*([-*•]|\d+\.)\s+/, ""));
      continue;
    }
    flush();
    if (!line.trim()) continue;
    if (line.startsWith("### ")) blocks.push(<h4 key={blocks.length} className="mt-2 text-sm font-semibold">{inline(line.slice(4))}</h4>);
    else if (line.startsWith("## ")) blocks.push(<h3 key={blocks.length} className="mt-3 text-sm font-semibold text-ink first:mt-0 sm:text-base">{inline(line.slice(3))}</h3>);
    else if (line.startsWith("# ")) blocks.push(<h3 key={blocks.length} className="mt-3 text-base font-semibold">{inline(line.slice(2))}</h3>);
    else {
      const m = /^([A-Z][A-Za-z /()-]{1,30}):\s(.*)$/.exec(line);
      blocks.push(<p key={blocks.length} className="break-words">{m ? <><strong className="text-ink">{m[1]}:</strong> {inline(m[2])}</> : inline(line)}</p>);
    }
  }
  flush();
  return <div className="space-y-1 text-sm leading-relaxed text-slate-200">{blocks}</div>;
}

function Answer({ a }: { a: AnalystAnswer }) {
  const tokens = a.usage && (a.usage.input_tokens != null || a.usage.output_tokens != null);
  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone={a.mode === "llm" ? "blue" : "slate"}>{a.mode === "llm" ? `AI${a.model ? ` · ${a.model}` : ""}` : "Rule-based"}</Pill>
        {a.grounded ? <Pill tone="green">✓ All numbers verified against platform data</Pill> : <Pill tone="amber">⚠ Unverified numbers</Pill>}
      </div>
      {a.note && <p className="text-xs text-muted">{a.note}</p>}
      {!a.grounded && a.unverified_numbers.length > 0 && (
        <p role="alert" className="rounded border border-amber-800 bg-amber-950/40 px-2 py-1 text-xs text-amber-200">
          These figures in the answer could not be matched to platform data — treat them with caution: <span className="num">{a.unverified_numbers.join(", ")}</span>
        </p>
      )}
      <div className="rounded-md border border-edge bg-panel2/40 p-3"><SimpleMarkdown text={a.answer} /></div>
      <p className="text-[11px] text-muted">{a.grounding_note}{a.context_used?.keys?.length ? ` Context: ${a.context_used.keys.join(", ")}.` : ""}</p>
      {tokens && <p className="text-[10px] text-muted">Tokens: {String(a.usage.input_tokens ?? "—")} in / {String(a.usage.output_tokens ?? "—")} out</p>}
      <p className="text-[11px] text-muted"><strong className="text-ink">Disclaimer:</strong> {a.disclaimer}</p>
    </div>
  );
}

export function AnalystHistory({ refresh = 0, filter }: { refresh?: number; filter?: { signal_id?: number; symbol?: string } }) {
  const q = useApi<{ items: AnalystHistoryItem[] }>(() => api.analyst.history(30), [refresh]);
  if (q.error) return q.error.status === 403 ? null : <ErrorState error={q.error} onRetry={q.reload} what="analyst history" />;
  if (!q.data) return <Loading />;
  const items = q.data.items.filter((i) => !filter || (filter.signal_id ? i.signal_id === filter.signal_id : filter.symbol ? i.symbol === filter.symbol : true));
  if (items.length === 0) return <EmptyState title="No questions yet" />;
  return (
    <ul className="space-y-2">
      {items.map((h) => (
        <li key={h.id}>
          <details className="rounded border border-edge bg-panel2/30">
            <summary className="cursor-pointer px-2 py-1.5 text-xs">
              <span className="font-medium text-ink">{h.question}</span>
              <span className="block text-[10px] text-muted">{dateTime(h.created_at)} · {h.mode === "llm" ? "AI" : "Rule-based"} · {h.symbol ?? ""}{h.signal_id ? ` · setup #${h.signal_id}` : ""}{h.grounded ? "" : " · ⚠ unverified numbers"}</span>
            </summary>
            <div className="border-t border-edge p-2"><SimpleMarkdown text={h.answer} /></div>
          </details>
        </li>
      ))}
    </ul>
  );
}

export function AnalystPanel({ signalId, symbol, title = "Ask the analyst" }: { signalId?: number; symbol?: string; title?: string }) {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [ans, setAns] = useState<AnalystAnswer | null>(null);
  const [err, setErr] = useState<{ kind: "limit" | "perm" | "other"; msg: string } | null>(null);
  const [tick, setTick] = useState(0);

  if (!can("analyst:ask")) return <Card title={title}><UpgradeNote feature="The AI market analyst" /></Card>;

  const ask = async (question: string) => {
    const text = question.trim();
    if (text.length < 3) return setErr({ kind: "other", msg: "Ask a question of at least 3 characters." });
    setBusy(true); setErr(null);
    try {
      setAns(await api.analyst.ask({ question: text.slice(0, MAX), ...(signalId ? { signal_id: signalId } : { symbol }) }));
      setTick((t) => t + 1);
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) setErr({ kind: "limit", msg: e.message });
      else if (e instanceof ApiError && e.status === 403) setErr({ kind: "perm", msg: e.message });
      else setErr({ kind: "other", msg: e instanceof Error ? e.message : "The analyst could not answer" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={title} right={<span className="text-[11px] text-muted">explains platform data · not advice</span>}>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Preset questions">
        {PRESETS.map((p) => <button key={p} type="button" className="btn-ghost px-2 py-1 text-xs" disabled={busy} onClick={() => { setQ(p); ask(p); }}>{p}</button>)}
      </div>
      <form className="mt-3" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <label className="label" htmlFor={`aq-${signalId ?? symbol}`}>Your question</label>
        <textarea id={`aq-${signalId ?? symbol}`} className="input min-h-[70px]" maxLength={MAX} value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. How does the market regime affect this setup?" />
        <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
          <span className={cx("num text-[11px]", q.length > MAX * 0.9 ? "text-amber-300" : "text-muted")}>{q.length}/{MAX}</span>
          <button className="btn-primary" disabled={busy || q.trim().length < 3}>{busy ? "Thinking…" : "Ask"}</button>
        </div>
      </form>
      {err?.kind === "limit" && <p role="alert" className="mt-2 rounded border border-amber-800 bg-amber-950/40 px-2 py-1.5 text-sm text-amber-200">Hourly question limit reached. {err.msg} Try again later.</p>}
      {err?.kind === "perm" && <div className="mt-2"><UpgradeNote feature="The AI market analyst" /></div>}
      {err?.kind === "other" && <InlineError error={err.msg} />}
      {busy && <Loading label="Preparing an explanation from platform data…" />}
      {ans && !busy && <div className="mt-3"><Answer a={ans} /></div>}
      <div className="mt-3">
        <Collapsible title="Analyst history"><AnalystHistory refresh={tick} filter={signalId ? { signal_id: signalId } : symbol ? { symbol } : undefined} /></Collapsible>
      </div>
    </Card>
  );
}
