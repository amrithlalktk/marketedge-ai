"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { hoursUntil, istDateTime, localDateTime, num, relTime } from "@/lib/format";
import type { Check, EconomicEvent, NewsItem } from "@/lib/types";
import { Card, EmptyState, Pill, cx } from "./ui";

export const NEWS_NOTE = "Sentiment is a lexicon-based classification of the headline and summary. It is context only and never generates or scores a setup.";

export function SentimentBadge({ label }: { label: string }) {
  const cls = label === "Positive" ? "bg-green-950 text-green-300 ring-green-800" : label === "Negative" ? "bg-red-950 text-red-300 ring-red-800" : "bg-slate-800 text-slate-300 ring-slate-700";
  return <span className={cx("badge ring-1", cls)}>{label}</span>;
}

export function ImpactBadge({ impact }: { impact: string }) {
  const tone = impact === "High" ? "red" : impact === "Medium" ? "amber" : "slate";
  return <Pill tone={tone}>{impact}</Pill>;
}

function Terms({ n }: { n: NewsItem }) {
  const t = n.sentiment_terms;
  const pos = t?.positive ?? [], neg = t?.negative ?? [];
  return (
    <div className="mt-1 rounded border border-edge bg-panel2/50 p-2 text-[11px]">
      <p className="text-muted">Method: <span className="font-mono text-ink">{n.sentiment_method ?? "—"}</span>{n.sentiment_score != null && <> · score <span className="num text-ink">{num(n.sentiment_score, 2)}</span></>}</p>
      <p className="mt-0.5"><span className="text-up">Positive terms:</span> {pos.length ? pos.map((x) => <span key={x} className={cx("mr-1 inline-block rounded bg-green-950/60 px-1", x.endsWith("(negated)") && "line-through decoration-1 opacity-70")}>{x}</span>) : <span className="text-muted">none</span>}</p>
      <p className="mt-0.5"><span className="text-down">Negative terms:</span> {neg.length ? neg.map((x) => <span key={x} className={cx("mr-1 inline-block rounded bg-red-950/60 px-1", x.endsWith("(negated)") && "line-through decoration-1 opacity-70")}>{x}</span>) : <span className="text-muted">none</span>}</p>
      {[...pos, ...neg].some((x) => x.endsWith("(negated)")) && <p className="mt-0.5 text-muted">Struck-through terms were negated in the text (e.g. “no”, “not”) and counted the other way or ignored.</p>}
    </div>
  );
}

/** Article card: source, local time + relative, sentiment with "Why?", category, symbols, link. */
export function NewsCard({ n, compact = false }: { n: NewsItem; compact?: boolean }) {
  const [why, setWhy] = useState(false);
  const id = useId();
  const hasTerms = !!n.sentiment_terms;
  return (
    <article className={cx("min-w-0", compact ? "py-2" : "card")} aria-labelledby={`${id}-t`}>
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
        <SentimentBadge label={n.sentiment_label} />
        {n.category && <Pill>{n.category}</Pill>}
        {n.is_sample && <Pill tone="amber">SAMPLE</Pill>}
        <span>{n.source}</span>
        <span aria-hidden>·</span>
        <time dateTime={n.published_at} title={n.published_at}>{localDateTime(n.published_at)} ({relTime(n.published_at)})</time>
      </div>
      <h3 id={`${id}-t`} className={cx("mt-1 break-words font-medium", compact ? "text-sm" : "text-sm sm:text-base")}>
        {n.url ? <a href={n.url} target="_blank" rel="noopener noreferrer" className="link">{n.title} <span aria-hidden>↗</span><span className="sr-only">(opens in a new tab)</span></a> : n.title}
      </h3>
      {!compact && n.summary && <p className="mt-0.5 text-xs text-muted">{n.summary}</p>}
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
        {(n.symbols ?? []).map((s) => <Link key={s} href={`/stocks/${encodeURIComponent(s)}`} className="rounded border border-edge px-1.5 py-0.5 font-mono text-[11px] hover:bg-panel2">{s}</Link>)}
        {hasTerms && (
          <button type="button" className="text-[11px] text-accent underline-offset-2 hover:underline" aria-expanded={why} aria-controls={`${id}-why`} onClick={() => setWhy((w) => !w)}>
            {why ? "Hide why" : "Why this sentiment?"}
          </button>
        )}
      </div>
      {why && <div id={`${id}-why`}><Terms n={n} /></div>}
    </article>
  );
}

export function EventRow({ e, now = Date.now() }: { e: EconomicEvent; now?: number }) {
  const h = hoursUntil(e.event_time, now);
  const soon = h >= 0 && h <= 24;
  return (
    <li className={cx("flex flex-wrap items-start justify-between gap-x-3 gap-y-1 py-1.5 text-sm", soon && "rounded bg-amber-950/30 px-1")}>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-1.5">
          <ImpactBadge impact={e.impact} />
          <span className="rounded border border-edge px-1 font-mono text-[10px]">{e.country}</span>
          <span className="break-words font-medium">{e.name}</span>
        </span>
        <span className="block text-[11px] text-muted">
          <time dateTime={e.event_time} title={e.event_time}>{istDateTime(e.event_time)}</time>
          {e.forecast != null && <> · forecast <span className="num">{num(e.forecast, 2)}</span></>}
          {e.previous != null && <> · previous <span className="num">{num(e.previous, 2)}</span></>}
        </span>
      </span>
      <span className={cx("num shrink-0 text-xs", soon ? "font-semibold text-amber-300" : "text-muted")}>{h < 0 ? relTime(e.event_time, now) : `in ${h < 48 ? `${h.toFixed(h < 10 ? 1 : 0)}h` : `${Math.round(h / 24)}d`}`}</span>
    </li>
  );
}

export function EventRiskBlock({ ev, checks }: { ev: { upcoming: EconomicEvent[]; note: string }; checks?: Check[] }) {
  const now = Date.now();
  const upcoming = ev.upcoming.filter((e) => hoursUntil(e.event_time, now) > -1);
  const relevant = (checks ?? []).filter((c) => /event|macro|earn|calendar/i.test(c.name));
  return (
    <Card title="Event risk">
      {upcoming.length === 0 ? <EmptyState title="No relevant economic events in the window" /> : <ul className="divide-y divide-edge">{upcoming.map((e) => <EventRow key={`${e.name}-${e.event_time}`} e={e} now={now} />)}</ul>}
      {relevant.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs">
          {relevant.map((c) => <li key={c.name} className={c.passed ? "text-muted" : c.severity === "block" ? "text-down" : "text-amber-300"}>{c.passed ? "✓" : c.severity === "block" ? "✗" : "!"} {c.name}: {c.detail}</li>)}
        </ul>
      )}
      <p className="mt-2 text-[11px] text-muted">{ev.note} Times in IST; highlighted = within 24h. Hours are computed live from the event time.</p>
    </Card>
  );
}

export function NewsFlowBlock({ n }: { n: { window_days: number; articles: number; counts: Record<string, number>; net_score: number | null; latest: NewsItem[]; note: string; checks?: Check[] } }) {
  return (
    <Card title={`News flow (last ${n.window_days} days)`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="num text-ink">{n.articles}</span> article{n.articles === 1 ? "" : "s"}:
        <span className="text-up">{n.counts.Positive ?? 0} positive</span> · <span className="text-muted">{n.counts.Neutral ?? 0} neutral</span> · <span className="text-down">{n.counts.Negative ?? 0} negative</span>
        <span>· net score <span className={cx("num", (n.net_score ?? 0) > 0 ? "text-up" : (n.net_score ?? 0) < 0 ? "text-down" : "text-ink")}>{n.net_score == null ? "—" : num(n.net_score, 2)}</span></span>
      </div>
      {n.latest.length > 0 ? <div className="mt-1 divide-y divide-edge">{n.latest.slice(0, 3).map((a) => <NewsCard key={a.id} n={a} compact />)}</div> : <p className="mt-2 text-xs text-muted">No recent articles for this instrument.</p>}
      {n.checks && n.checks.length > 0 && <ul className="mt-2 space-y-0.5 text-xs text-amber-200">{n.checks.map((c) => <li key={c.name}>⚠ {c.name}: {c.detail}</li>)}</ul>}
      <p className="mt-2 rounded border border-blue-900 bg-blue-950/30 px-2 py-1 text-[11px] text-blue-100">ⓘ {n.note}</p>
    </Card>
  );
}
